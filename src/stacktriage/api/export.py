"""Report exports. CSV cells that a spreadsheet would treat as formulas are neutralized."""
from __future__ import annotations

import csv
import io
import re
from datetime import datetime

_FORMULA_START = re.compile(r"^[\s]*[=+\-@\t\r]")


def safe_cell(v: object) -> object:
    """Prefix a single quote when text could be interpreted as a formula (OWASP CSV injection)."""
    if isinstance(v, str) and (_FORMULA_START.match(v) or v[:1] in ("\t", "\r", "\n")):
        return "'" + v
    if isinstance(v, datetime):
        return v.isoformat()
    return v


def to_csv(header: list[str], rows: list[list[object]]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow([safe_cell(h) for h in header])
    for r in rows:
        w.writerow([safe_cell(c) for c in r])
    return buf.getvalue()


def md_escape(s: str, limit: int = 300) -> str:
    s = " ".join(str(s).split())[:limit]
    return re.sub(r"([\\`*_{}\[\]<>|#])", r"\\\1", s)


def safe_filename(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", name)[:80] or "report"
