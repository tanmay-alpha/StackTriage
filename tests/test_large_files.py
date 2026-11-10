"""Bounded-memory behaviour on files much larger than the sample fixtures."""
import io
import time
import tracemalloc

from stacktriage import Limits, analyze, parse


class LineStream(io.RawIOBase):
    """Generates a log on the fly so the test itself does not hold it in memory."""

    def __init__(self, gen):
        self._it, self._buf, self._pos = gen, b"", 0
        self._head = b""

    def readable(self):
        return True

    def seekable(self):
        return True

    def seek(self, pos, whence=0):
        self._it = self._factory()
        self._buf = b""
        return 0

    def readinto(self, b):
        while len(self._buf) < len(b):
            try:
                self._buf += next(self._it)
            except StopIteration:
                break
        n = min(len(b), len(self._buf))
        b[:n] = self._buf[:n]
        self._buf = self._buf[n:]
        return n


def make_stream(n_lines: int):
    def factory():
        for i in range(n_lines):
            s, m = divmod(i, 60)
            yield (f"2026-03-05 {(s // 60) % 24:02d}:{s % 60:02d}:{m:02d},000 - app.mod - "
                   f"{'ERROR' if i % 7 == 0 else 'INFO'} - Request {i} failed for user {i * 31} reason {i % 5}\n").encode()
    st = LineStream(factory())
    st._factory = factory
    return io.BufferedReader(st, buffer_size=1 << 16)


def test_events_beyond_limit_are_cut_with_bounded_memory():
    tracemalloc.start()
    sess = parse(make_stream(120_000), limits=Limits(max_events=60_000))
    res = analyze(sess)
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert res.total_events == 60_000 and sess.diagnostics.truncated
    assert peak < 60 * 1024 * 1024, f"peak {peak / 1e6:.0f} MB"
    assert len(res.groups) <= 10  # variable ids masked, so a handful of groups


def test_single_huge_line_is_truncated_not_buffered():
    big = b"2026-03-05 10:00:01,123 - a - ERROR - " + b"x" * 5_000_000 + b"\n2026-03-05 10:00:02,123 - a - INFO - after\n"
    tracemalloc.start()
    sess = parse(io.BytesIO(big), limits=Limits(max_line_chars=1000))
    events = list(sess)
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert len(events) == 2 and events[1].message == "after" and len(events[0].message) <= 1000
    assert sess.diagnostics.code_counts["LINE_TOO_LONG"] == 1
    assert peak < 20 * 1024 * 1024


def test_throughput_is_reasonable():
    t = time.perf_counter()
    res = analyze(parse(make_stream(100_000)))
    elapsed = time.perf_counter() - t
    assert res.total_events == 100_000
    assert elapsed < 60, f"{elapsed:.1f}s for 100k lines"
