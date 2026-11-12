"""Generate a synthetic before/after-deployment pair for the import comparison feature.

    python scripts/make_compare_fixtures.py

Deterministic (seeded). Writes tests/fixtures/deploy_before.log, tests/fixtures/deploy_after.log and
tests/fixtures/expected/compare.json. The scenario table below IS the ground truth: expected
categories are stated by design, independently of StackTriage's comparison code.

Both captures cover one hour of a Python service. The deployment doubles traffic (INFO volume),
so absolute counts roughly double even for an unchanged error *rate*; that is deliberate.
"""
from __future__ import annotations

import json
import random
from datetime import UTC, datetime, timedelta
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "tests" / "fixtures"
BEFORE = datetime(2026, 3, 5, 10, 0, 0, tzinfo=UTC)   # baseline capture 10:00-11:00 UTC
AFTER = datetime(2026, 3, 5, 12, 0, 0, tzinfo=UTC)    # deployment at 11:30, target capture 12:00-13:00 UTC
HOUR = 3600
rng = random.Random(11)


def ms(ts: datetime) -> str:
    return ts.strftime("%Y-%m-%d %H:%M:%S") + f",{ts.microsecond // 1000:03d}"


def line(ts: datetime, logger: str, level: str, msg: str) -> str:
    return f"{ms(ts)} - {logger} - {level} - {msg}"


def tb(frames: list[tuple[str, int, str, str]], exc: str) -> list[str]:
    out = ["Traceback (most recent call last):"]
    for path, no, fn, code in frames:
        out += [f'  File "{path}", line {no}, in {fn}', f"    {code}"]
    return out + [exc]


def pool(ts, i):
    return [line(ts, "app.db", "ERROR", f"Checkout {rng.randint(1000, 9999)} failed")] + tb(
        [("/srv/shop/app/db.py", 41, "acquire", "conn = self.pool.get(timeout=2)")],
        "queue.Empty: connection pool exhausted (size=20)")


def payment(ts, i):
    return [line(ts, "app.payments", "ERROR", f"Charge for order {rng.randint(1000, 9999)} failed")] + tb(
        [("/srv/shop/app/payments.py", 77, "charge", "resp = client.post(url, json=body, timeout=3)")],
        f"TimeoutError: payment gateway did not respond within {rng.choice([3000, 3001, 2999])} ms")


def cache(ts, i):
    return [line(ts, "app.cache", "WARNING", f"Cache entry for product {rng.randint(100, 999)} could not be decoded; refetching")]


def currency(ts, i):
    return [line(ts, "app.pricing", "ERROR", f"Pricing failed for cart {rng.randint(1000, 9999)}")] + tb(
        [("/srv/shop/app/pricing.py", 58, "total", "return cart.currency.round(amount)")],
        "AttributeError: 'NoneType' object has no attribute 'round'")


def legacy(ts, i):
    return [line(ts, "app.api", "WARNING", "Deprecated endpoint /v1/cart called; scheduled for removal")]


def inventory(ts, i):
    return [line(ts, "app.inventory", "ERROR", f"Stock sync for SKU {rng.randint(10000, 99999)} failed")] + tb(
        [("/srv/shop/app/inventory.py", 120, "sync", "warehouse.reserve(sku, qty)")],
        "ConnectionError: warehouse API refused connection")


def coupon(ts, i):
    return [line(ts, "app.checkout", "ERROR", f"Coupon check failed for order {rng.randint(1000, 9999)}")] + tb(
        [("/srv/shop/app/coupons.py", 33, "apply", "rule = RULES[code]")], "KeyError: 'SPRING26'")


def tax(lineno: int):
    def emit(ts, i):
        return [line(ts, "app.tax", "ERROR", f"Tax lookup failed for region {rng.randint(10, 99)}")] + tb(
            [("/srv/shop/app/tax.py", lineno, "rate_for", "return TABLE[region]")], "LookupError: no tax table loaded")
    return emit


# id, emitter(before), emitter(after), baseline count, target count, expected category, expected change
SCENARIOS = [
    ("pool_exhausted", pool, pool, 30, 60, "recurring", "no_clear_change",
     "Count doubles with traffic; rate per 1,000 events is unchanged."),
    ("payment_timeout", payment, payment, 12, 150, "recurring", "increased", "Regression introduced by the deployment."),
    ("cache_decode", cache, cache, 80, 20, "recurring", "decreased", "Improved after the deployment."),
    ("currency_none", None, currency, 0, 45, "new", None, "New error type after the deployment."),
    ("legacy_endpoint", legacy, None, 25, 0, "disappeared", None, "Absent from the target capture."),
    ("inventory_sync", inventory, inventory, 6, 40, "recurring", "increased",
     "Marked resolved in the demo before comparing: resolved but observed again."),
    ("coupon_missing", coupon, coupon, 2, 3, "recurring", "insufficient_data", "Too few occurrences to judge."),
    # same failure, but the deployment shifted the frame from line 88 to 91: a fingerprint limitation
    ("tax_before_shift", tax(88), None, 15, 0, "disappeared", None, "Fingerprint split by a shifted line number."),
    ("tax_after_shift", None, tax(91), 0, 30, "new", None, "Fingerprint split by a shifted line number."),
]


def capture(start: datetime, info_every: int, which: int) -> tuple[str, int]:
    recs: list[tuple[datetime, list[str]]] = []
    for k, s in enumerate(range(0, HOUR, info_every)):
        ts = start + timedelta(seconds=s + rng.randint(0, info_every - 1))
        recs.append((ts, [line(ts, "app.http", "INFO", f"GET /api/{['cart', 'products', 'orders'][k % 3]} 200 {rng.randint(5, 90)}ms")]))
    for sid, eb, ea, nb, na, *_ in SCENARIOS:
        emit, n = (eb, nb) if which == 0 else (ea, na)
        if not n:
            continue
        for i, s in enumerate(sorted(rng.sample(range(5, HOUR - 5), n))):
            ts = start + timedelta(seconds=s, milliseconds=rng.randint(0, 999))
            recs.append((ts, emit(ts, i)))
    recs.sort(key=lambda r: r[0])
    return "".join("\n".join(ls) + "\n" for _, ls in recs), len(recs)


def main() -> None:
    before, n_before = capture(BEFORE, 6, 0)   # ~600 INFO events
    after, n_after = capture(AFTER, 3, 1)      # ~1200 INFO events: traffic doubled
    (OUT / "deploy_before.log").write_text(before, encoding="utf-8", newline="\n")
    (OUT / "deploy_after.log").write_text(after, encoding="utf-8", newline="\n")
    truth = {
        "baseline": {"file": "deploy_before.log", "total_events": n_before},
        "target": {"file": "deploy_after.log", "total_events": n_after},
        "groups": [{"id": sid, "baseline_count": nb, "target_count": na, "category": cat, "change": chg, "why": why}
                   for sid, _, _, nb, na, cat, chg, why in SCENARIOS],
    }
    (OUT / "expected" / "compare.json").write_text(json.dumps(truth, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(f"deploy_before.log: {n_before} events; deploy_after.log: {n_after} events; {len(SCENARIOS)} scenarios")


if __name__ == "__main__":
    main()
