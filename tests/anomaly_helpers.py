"""Hand-built timelines for anomaly tests (deterministic; no files, no generated artifacts)."""
from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta

from stacktriage.timeline import Bucket, Timeline

T0 = datetime(2026, 3, 5, 0, 0, 0, tzinfo=UTC)
W = 60


def healthy_row(rng: random.Random) -> dict:
    return {
        "total": 50 + rng.randint(-5, 5),
        "errors": rng.choice((0, 1, 1, 2, 2, 3)),
        "new_groups": 0,
        "metrics": {"latency_ms": 80.0 + rng.uniform(-4, 4), "cpu_pct": 30.0 + rng.uniform(-2, 2)},
    }


def build(rows: list[dict]) -> Timeline:
    buckets = []
    for i, r in enumerate(rows):
        errors = r["errors"]
        buckets.append(Bucket(
            T0 + timedelta(seconds=i * W), W, total=r["total"], info=max(0, r["total"] - errors), error=errors,
            new_groups=r.get("new_groups", 0), metrics=dict(r.get("metrics", {})),
        ))
    return Timeline(W, buckets[0].start if buckets else None, buckets[-1].start if buckets else None, buckets)


def series(n: int, seed: int = 0, **overrides: dict) -> list[dict]:
    """n healthy rows; overrides = {window_index: dict of row fields to replace}."""
    rng = random.Random(seed)
    rows = [healthy_row(rng) for _ in range(n)]
    for i, patch in overrides.items():
        rows[i].update(patch)
    return rows


def patch_range(rows: list[dict], a: int, b: int, **fields) -> None:
    for i in range(a, b):
        rows[i] = {**rows[i], **fields}
