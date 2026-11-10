"""Deterministic error fingerprinting.

Two events share a fingerprint when they have the same service, the same root
exception type, the same normalized exception message and the same application
frame (file, function, line) where it originated. Variable parts (ids, numbers, addresses, timestamps)
are masked; words are never fuzzy-matched, so "connection refused" and
"connection timed out" stay separate.

No ML or similarity thresholds are involved: the same input always yields the
same key, which keeps grouping reproducible across re-imports.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from collections.abc import Callable
from functools import lru_cache

from .models import LogEvent

# ------------------------------------------------------------ normalization
def _mask_ip(m: re.Match[str]) -> str:
    """Mask the address; keep well-known/service ports (they tell redis from postgres)
    but mask ephemeral ports (>= 32768), which differ on every connection."""
    port = m.group(1)
    if port is None:
        return "<IP>"
    return f"<IP>:{port}" if int(port) < 32768 else "<IP>:<PORT>"


def _mask_id(m: re.Match[str]) -> str:
    tok = m.group(0)
    has_digit = any(c.isdigit() for c in tok)
    has_alpha = any(c.isalpha() for c in tok)
    return "<ID>" if has_digit and has_alpha else tok


_NORM_STEPS: list[tuple[re.Pattern[str], str | Callable[[re.Match[str]], str]]] = [
    (re.compile(r"(?<=[\w/])\?[^\s\"')\]]+"), "?<Q>"),  # URL query strings
    (re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"), "<UUID>"),
    (re.compile(r"\b0x[0-9a-fA-F]+\b"), "<HEX>"),
    (re.compile(r"\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?(?:Z|[+-]\d{2}:?\d{2})?"), "<TS>"),
    (re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}(?::(\d{1,5}))?\b"), lambda m: _mask_ip(m)),
    (re.compile(r"\b[0-9a-fA-F]{16,}\b"), "<HEX>"),
    # numbers with a unit suffix (3000ms, 45s, 10MB, 87%) must not look like ids
    (re.compile(r"\b\d+(?:\.\d+)?(?=(?:ms|us|ns|s|m|h|d|kb|mb|gb|kib|mib|gib|b)\b)|\b\d+(?:\.\d+)?(?=%)"), "<N>"),
    # alphanumeric ids: has a digit and a letter, 5+ chars (order-8841, req_a1b2c3, 7f3a9c1e)
    (re.compile(r"\b[\w-]{5,}\b"), lambda m: _mask_id(m)),
    (re.compile(r"\b[A-Za-z]+[-_#]\d+\b"), "<ID>"),
    # HTTP status codes identify different errors (500 vs 404) and are kept
    (re.compile(r"(?<![A-Za-z_\d])(?<!HTTP )(?<!<IP>:)(?<!status )(?<!status: )(?<!status=)\d+(?:\.\d+)?"), "<N>"),
    (re.compile(r"\s+"), " "),
]


_QUOTED = re.compile(r"'[^'\n]{0,200}'|\"[^\"\n]{0,200}\"")


def normalize_message(text: str, limit: int = 300, mask_quoted: bool = False) -> str:
    """Mask variable tokens so repeated occurrences of one error compare equal.

    `mask_quoted` also masks quoted literals ('abc', "abc"). Used only when a stack
    frame (file, function, line) already pins the error to one code location, so a
    varying quoted value is data, not a different bug.
    """
    if mask_quoted:
        text = _QUOTED.sub("'<STR>'", text)
    for pattern, repl in _NORM_STEPS:
        text = pattern.sub(repl, text)
    return text.strip()[:limit]


# ------------------------------------------------------------ root extraction
_PY_FRAME = re.compile(r'^File "(?P<file>[^"]+)", line (?P<line>\d+), in (?P<func>.+)$')
_PY_EXC = re.compile(r"^(?P<type>[A-Za-z_][\w.]*)(?::\s?(?P<msg>.*))?$")
_PY_SKIP = re.compile(r"^(Traceback \(|During handling of|The above exception|\+|\s*\|)")
_PY_LIB = re.compile(r"site-packages|dist-packages|[/\\]lib[/\\]python|[/\\]Lib[/\\]|<frozen|<string>")

_JAVA_EXC = re.compile(
    r"^(?:Exception in thread \"[^\"]*\"\s+)?(?:Caused by:\s+)?"
    r"(?P<type>(?:[A-Za-z_$][\w$]*\.)+[A-Za-z_$][\w$]*)(?::\s?(?P<msg>.*))?$"
)
_JAVA_CAUSED = re.compile(r"^\s*Caused by:\s")
_JAVA_FRAME = re.compile(r"^\s+at\s+(?P<sym>[\w.$<>]+)\((?P<src>[^)]*)\)")
_JAVA_INFRA = (
    "java.", "javax.", "jdk.", "sun.", "com.sun.", "org.springframework.", "org.apache.",
    "org.hibernate.", "io.netty.", "reactor.", "org.eclipse.", "org.junit.", "jakarta.",
    "com.zaxxer.", "org.postgresql.", "com.mysql.", "kotlin.",
)

_NODE_EXC = re.compile(r"^(?P<type>[A-Z]\w*(?:Error|Exception)|Error)(?::\s?(?P<msg>.*))?$")
_NODE_INFRA = re.compile(r"node_modules|^node:|^internal/|\(native\)|<anonymous>$")

_MSG_EXC = re.compile(
    r"(?P<type>(?:[A-Za-z_$][\w$]*\.)*[A-Z][\w$]*(?:Error|Exception))\b(?::\s*(?P<msg>.*))?"
)


@dataclass(frozen=True, slots=True)
class RootError:
    exc_type: str
    exc_message: str
    frame: str | None


def _node_frame(ln: str) -> tuple[str | None, str, str] | None:
    """Parse `    at fn (path:line:col)` / `    at path:line:col` -> (fn, path, line). Linear time."""
    if ln[:1] not in (" ", "\t") or len(ln) > 1000:
        return None
    s = ln.strip()
    if not s.startswith("at "):
        return None
    rest = s[3:].strip()
    fn = None
    if rest.endswith(")") and " (" in rest:
        i = rest.rindex(" (")
        fn, rest = rest[:i].strip(), rest[i + 2 : -1]
    parts = rest.rsplit(":", 2)
    if len(parts) == 3 and parts[1].isdigit() and parts[2].isdigit():
        return fn, parts[0], parts[1]
    parts = rest.rsplit(":", 1)
    if len(parts) == 2 and parts[1].isdigit():
        return fn, parts[0], parts[1]
    return None


def _basename(path: str) -> str:
    return re.split(r"[/\\]", path.rstrip("/\\"))[-1]


def _python_root(lines: list[str]) -> RootError | None:
    start = max((i for i, ln in enumerate(lines) if ln.startswith("Traceback (most recent")), default=-1)
    if start < 0:
        return None
    frames: list[tuple[str, str, str]] = []
    for ln in lines[start + 1 :]:
        m = _PY_FRAME.match(ln.strip())
        if m:
            frames.append((m["file"], m["func"], m["line"]))
            continue
        if not ln or ln[0] in " \t" or _PY_SKIP.match(ln):
            continue
        m = _PY_EXC.match(ln)
        if m:
            app = [f for f in frames if not _PY_LIB.search(f[0])]
            fr = (app or frames or [None])[-1]
            return RootError(m["type"], (m["msg"] or "").strip(), f"{_basename(fr[0])}:{fr[1]}:{fr[2]}" if fr else None)
    return None


def _java_root(lines: list[str]) -> RootError | None:
    blocks: list[tuple[str, str, list[tuple[str, str]]]] = []  # (type, msg, [(symbol, source)])
    for ln in lines:
        if ln and ln[0] not in " \t\r":
            m = _JAVA_EXC.match(ln.strip())
            if m and (_JAVA_CAUSED.match(ln) or not blocks):
                blocks.append((m["type"], (m["msg"] or "").strip(), []))
                continue
        elif blocks:
            if _JAVA_CAUSED.match(ln):
                m = _JAVA_EXC.match(ln.strip())
                if m:
                    blocks.append((m["type"], (m["msg"] or "").strip(), []))
                continue
            m = _JAVA_FRAME.match(ln)
            if m:
                blocks[-1][2].append((m["sym"], m["src"]))
    if not blocks:
        return None
    exc_type, msg, syms = blocks[-1]
    app = [s for s in syms if not s[0].startswith(_JAVA_INFRA)]
    top = (app or syms or [None])[0]
    frame = None
    if top is not None:
        line = re.search(r":(\d+)$", top[1])
        frame = f"{top[0]}:{line.group(1)}" if line else top[0]
    return RootError(exc_type, msg, frame)


def _node_root(lines: list[str]) -> RootError | None:
    exc: re.Match[str] | None = None
    frames: list[tuple[str | None, str, str]] = []
    for ln in lines:
        if ln and ln[0] not in " \t":
            m = _NODE_EXC.match(ln.strip())
            if m and exc is None:
                exc = m
        else:
            f = _node_frame(ln)
            if f:
                frames.append(f)
    if exc is None:
        return None
    app = [f for f in frames if not _NODE_INFRA.search(f[1])]
    fr = (app or frames or [None])[0]
    frame = None
    if fr is not None:
        frame = f"{_basename(fr[1])}:{(fr[0] or '<anonymous>').strip()}:{fr[2]}"
    return RootError(exc["type"], (exc["msg"] or "").strip(), frame)


def extract_root(message: str, stack_trace: str | None) -> RootError | None:
    """Best-effort root exception from a stack trace (Python, Java or Node style)."""
    if not stack_trace:
        return None
    lines = [ln[:2000] for ln in stack_trace.splitlines()]  # bound regex input
    if any(ln.startswith("Traceback (most recent") for ln in lines):
        return _python_root(lines)
    if any(_JAVA_FRAME.match(ln) for ln in lines):
        return _java_root(lines)
    if any(_node_frame(ln) for ln in lines):
        r = _node_root(lines)
        if r is not None:
            return r
        # Node traces logged as "error: <message>" put the exception type in the message.
        m = _MSG_EXC.search(message[:500])
        if m:
            return RootError(m["type"], (m["msg"] or "").strip(), None)
    return None


def exception_summary(stack_trace: str) -> str | None:
    """One-line `Type: message` for a headerless trace (used by parsers)."""
    root = extract_root("", stack_trace)
    if root is None:
        return None
    return f"{root.exc_type}: {root.exc_message}".rstrip(": ").strip()


# ------------------------------------------------------------------ public API
@dataclass(frozen=True, slots=True)
class Fingerprint:
    key: str  # 16 hex chars
    title: str  # human-readable group name
    exc_type: str | None
    kind: str  # "trace" (root exception from a stack trace) | "message"
    frame: str | None


def is_groupable(ev: LogEvent) -> bool:
    """Events that represent a problem: WARNING+ or carrying a stack trace."""
    return ev.level >= 30 or ev.stack_trace is not None


@lru_cache(maxsize=1024)
def _fingerprint(service: str, message: str, stack_trace: str | None) -> Fingerprint:
    root = extract_root(message, stack_trace)
    if root is not None:
        norm = normalize_message(root.exc_message, mask_quoted=root.frame is not None)
        basis = ("trace", root.exc_type, norm, root.frame or "")
        shown = normalize_message(root.exc_message)
        title = f"{root.exc_type}: {shown}" if shown else root.exc_type
        exc_type, kind, frame = root.exc_type, "trace", root.frame
    else:
        norm = normalize_message(message)
        basis = ("message", "", norm, "")
        title, exc_type, kind, frame = (norm or "(empty message)"), None, "message", None
    digest = hashlib.sha1("\x1f".join((service, *basis)).encode("utf-8", "replace")).hexdigest()
    return Fingerprint(digest[:16], title[:200], exc_type, kind, frame)


def fingerprint(ev: LogEvent) -> Fingerprint:
    return _fingerprint(ev.service or "unknown", ev.message, ev.stack_trace)
