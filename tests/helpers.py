from __future__ import annotations

import io

from stacktriage import Limits, analyze, parse


def run(text: str, *, limits: Limits | None = None, **kw):
    """Parse text and return (session, events, analysis)."""
    kwargs = dict(kw)
    if limits is not None:
        kwargs["limits"] = limits
    sess = parse(io.BytesIO(text.encode("utf-8")), **kwargs)
    events = list(sess)
    return sess, events


def run_analysis(text: str, **kw):
    sess = parse(io.BytesIO(text.encode("utf-8")), **kw)
    res = analyze(sess)
    return sess, res
