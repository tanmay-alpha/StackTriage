"""Generate the parser/grouping test fixtures and their ground truth.

Deterministic (seeded). The scenario definitions below ARE the ground truth: the
`expected/*.json` files record what was injected (counts, first/last timestamp,
root error), independently of any StackTriage parsing code. Tests compare the
parser's actual output against them.

    python scripts/make_fixtures.py
"""
from __future__ import annotations

import json
import random
from datetime import UTC, datetime, timedelta
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "tests" / "fixtures"
BASE = datetime(2026, 3, 5, 10, 0, 0, tzinfo=UTC)
HOURS = 2
rng = random.Random(7)


class Fixture:
    """Collects (timestamp, lines) records and the injected-incident ground truth."""

    def __init__(self, name: str, fmt: str, service: str | None):
        self.name, self.fmt, self.service = name, fmt, service
        self.records: list[tuple[datetime, list[str]]] = []
        self.truth: list[dict] = []

    def add(self, ts: datetime, lines: list[str]) -> None:
        self.records.append((ts, lines))

    def incident(self, id: str, offsets_s: list[int], emit, *, level: str, title_contains: str,
                 exc_type: str | None = None, ongoing: bool = False) -> None:
        offsets_s = sorted(offsets_s)
        for i, off in enumerate(offsets_s):
            ts = BASE + timedelta(seconds=off, milliseconds=rng.randint(0, 999))
            self.add(ts, emit(ts, i))
        first = BASE + timedelta(seconds=offsets_s[0])
        last = BASE + timedelta(seconds=offsets_s[-1])
        self.truth.append({
            "id": id, "count": len(offsets_s), "level": level, "title_contains": title_contains,
            "exc_type": exc_type, "first_second": first.isoformat(), "last_second": last.isoformat(),
            "ongoing": ongoing,
        })

    def write(self, path: str, render) -> None:
        recs = sorted(self.records, key=lambda r: r[0])
        text = render(recs)
        (OUT / path).write_text(text, encoding="utf-8", newline="\n")
        n_events = len(recs)
        (OUT / "expected").mkdir(exist_ok=True)
        (OUT / "expected" / f"{self.name}.json").write_text(
            json.dumps({
                "file": path, "format": self.fmt, "service": self.service, "total_events": n_events,
                "window_start": BASE.isoformat(), "groups": self.truth,
            }, indent=2) + "\n", encoding="utf-8", newline="\n")
        print(f"{path}: {n_events} events, {len(self.truth)} injected incidents")


def spread(n: int, start_s: int, end_s: int) -> list[int]:
    """Random distinct seconds; capped at 6200 so these incidents end well before the log does."""
    return sorted(rng.sample(range(start_s, min(end_s, 6200)), n))


def until_end(start_s: int, every: int, jitter: int) -> list[int]:
    """Recurring every `every`s from start_s through the end of the log (an ongoing incident)."""
    return [t + rng.randint(0, jitter) for t in range(start_s, HOURS * 3600 - 10, every)]


def steady(start_s: int = 0, end_s: int = HOURS * 3600, every: int = 25) -> list[int]:
    return [s + rng.randint(0, every - 1) for s in range(start_s, end_s, every)]


def ms(ts: datetime, sep: str = ",") -> str:
    return ts.strftime("%Y-%m-%d %H:%M:%S") + sep + f"{ts.microsecond // 1000:03d}"


# ----------------------------------------------------------------------- Python
def make_python() -> None:
    f = Fixture("python_app", "python", "orders-api")
    L = lambda ts, name, lvl, msg: [f"{ms(ts)} - {name} - {lvl} - {msg}"]  # noqa: E731
    for i, off in enumerate(steady()):
        ts = BASE + timedelta(seconds=off)
        kind = i % 4
        msg = [f"Order {rng.randint(1000, 9999)} created for customer {rng.randint(100, 999)}",
               f"GET /api/orders/{rng.randint(1000, 9999)} 200 {rng.randint(8, 90)}ms",
               "Cache refreshed", f"Payment {rng.randint(10**5, 10**6)} captured"][kind]
        f.add(ts, L(ts, "app.orders", "INFO", msg))
    f.add(BASE, L(BASE, "app.boot", "INFO",
                  "Connecting with DATABASE_URL=postgresql://orders:s3cr3tPass@10.0.3.12:5432/orders"))
    f.incident("auth_reject", [300], lambda ts, i: L(ts, "app.auth", "WARNING",
               "Rejected request, Authorization: Bearer abc123SECRETtokenvalue999 from alice@example.com"),
               level="WARNING", title_contains="Rejected request")
    f.incident("login_retry", [301], lambda ts, i: L(ts, "app.auth", "WARNING",
               "Login retry for user bob password=hunter2 api_key=sk-live-1234567890abcdefghijklmn"),
               level="WARNING", title_contains="Login retry for user bob")

    def e_value(ts, i):
        oid, bad = rng.randint(1000, 9999), rng.choice(["x12", "ten", "1e3", "", "--5"])
        return L(ts, "app.orders", "ERROR", f"Failed to process order {oid} for customer {rng.randint(100, 999)}") + [
            "Traceback (most recent call last):",
            '  File "/srv/orders/app/api.py", line 112, in create_order',
            '    qty = parse_qty(payload["qty"])',
            '  File "/srv/orders/app/orders.py", line 47, in parse_qty',
            "    return int(raw)",
            f"ValueError: invalid literal for int() with base 10: '{bad}'",
        ]
    f.incident("value_error_qty", spread(14, 600, 6600), e_value, level="ERROR",
               title_contains="invalid literal for int()", exc_type="ValueError")

    def e_currency(ts, i):
        return L(ts, "app.pricing", "ERROR", f"Price lookup failed for order {rng.randint(1000, 9999)}") + [
            "Traceback (most recent call last):",
            '  File "/srv/orders/app/pricing.py", line 88, in convert',
            "    rate = RATES[code]",
            "KeyError: 'XQZ'" if i % 2 else "KeyError: 'ABC'",
        ]
    f.incident("keyerror_rates", spread(5, 1200, 5000), e_currency, level="ERROR",
               title_contains="KeyError", exc_type="KeyError")

    def e_value2(ts, i):
        return L(ts, "app.orders", "ERROR", f"Failed to process order {rng.randint(1000, 9999)}") + [
            "Traceback (most recent call last):",
            '  File "/srv/orders/app/orders.py", line 63, in validate_currency',
            '    raise ValueError(f"Unknown currency code {code}")',
            f"ValueError: Unknown currency code '{rng.choice(['XQZ', 'ZZZ'])}'",
        ]
    f.incident("value_error_currency", spread(6, 2000, 6000), e_value2, level="ERROR",
               title_contains="Unknown currency code", exc_type="ValueError")

    def e_db(ts, i):
        return L(ts, "app.db", "ERROR", f"Database unavailable, attempt {i + 1}") + [
            "Traceback (most recent call last):",
            '  File "/srv/venv/lib/python3.11/site-packages/psycopg2/__init__.py", line 122, in connect',
            "    conn = _connect(dsn, connection_factory=connection_factory, **kwasync)",
            'psycopg2.OperationalError: connection to server at "10.0.3.12", port 5432 failed: Connection refused',
            "",
            "The above exception was the direct cause of the following exception:",
            "",
            "Traceback (most recent call last):",
            '  File "/srv/orders/app/db.py", line 33, in get_session',
            "    session.execute(text('SELECT 1'))",
            'sqlalchemy.exc.OperationalError: (psycopg2.OperationalError) connection to server at "10.0.3.12", port 5432 failed: Connection refused',
        ]
    # burst from minute 85 until the end of the log: an ongoing incident
    f.incident("db_down", until_end(5100, 20, 5), e_db, level="ERROR",
               title_contains="Connection refused", exc_type="sqlalchemy.exc.OperationalError", ongoing=True)

    def w_slow(ts, i):
        return L(ts, "app.db", "WARNING", f"Slow query took {rng.randint(1100, 4800)} ms (threshold 1000 ms) for order {rng.randint(1000, 9999)}")
    f.incident("slow_query", spread(23, 300, 7000), w_slow, level="WARNING", title_contains="Slow query took")

    f.write("python_app.log", lambda recs: "".join("\n".join(ls) + "\n" for _, ls in recs))


# ------------------------------------------------------------------------- Node
def make_node() -> None:
    f = Fixture("node_express", "node", "api-gateway")
    iso = lambda ts: ts.strftime("%Y-%m-%dT%H:%M:%S.") + f"{ts.microsecond // 1000:03d}Z"  # noqa: E731
    W = lambda ts, lvl, msg: [f"{iso(ts)} [api-gateway] {lvl}: {msg}"]  # noqa: E731
    for i, off in enumerate(steady(every=20)):
        ts = BASE + timedelta(seconds=off)
        f.add(ts, W(ts, "info", [f"GET /api/users/{rng.randint(100, 9999)} 200 {rng.randint(5, 80)}ms",
                                 f"Session {rng.randrange(16**8):08x} refreshed",
                                 "Listening on port 3000"][i % 3]))
    f.add(BASE + timedelta(seconds=12), W(BASE + timedelta(seconds=12), "info",
          'Outbound call {"headers":{"authorization":"Bearer abcdefghijklmnop123456"},"token":"tok_live_998877"}'))

    def trace_users(prop: str, line: int):
        def emit(ts, i):
            return W(ts, "error", f"Unhandled error in GET /api/users/{rng.randint(100, 9999)} TypeError: Cannot read properties of undefined (reading '{prop}')") + [
                f"TypeError: Cannot read properties of undefined (reading '{prop}')",
                f"    at getUser (/app/routes/users.js:{line}:19)",
                "    at Layer.handle [as handle_request] (/app/node_modules/express/lib/router/layer.js:95:5)",
                "    at next (/app/node_modules/express/lib/router/route.js:144:13)",
            ]
        return emit
    f.incident("typeerror_id", spread(12, 400, 5000), trace_users("id", 42), level="ERROR",
               title_contains="(reading 'id')", exc_type="TypeError")
    f.incident("typeerror_email", spread(7, 900, 6000), trace_users("email", 57), level="ERROR",
               title_contains="(reading 'email')", exc_type="TypeError")

    def redis(ts, i):
        return W(ts, "error", "Redis client error") + [
            "Error: connect ECONNREFUSED 127.0.0.1:6379",
            "    at TCPConnectWrap.afterConnect [as oncomplete] (node:net:1555:16)",
            "    at RedisClient.connect (/app/lib/cache.js:27:11)",
        ]
    f.incident("redis_down", until_end(3000, 30, 4), redis, level="ERROR",
               title_contains="ECONNREFUSED", exc_type="Error", ongoing=True)

    # Express/morgan access log lines (common log format): 5xx -> ERROR, 4xx -> WARNING
    def clf(status: int):
        def emit(ts, i):
            stamp = ts.strftime("%d/%b/%Y:%H:%M:%S +0000")
            return [f'10.0.0.{rng.randint(2, 250)} - - [{stamp}] "POST /api/orders/{rng.randint(1000, 99999)} HTTP/1.1" {status} 132 "-" "axios/1.6.2"']
        return emit
    f.incident("http_500", spread(9, 1500, 6500), clf(500), level="ERROR", title_contains="HTTP 500 POST /api/orders/<N>")
    f.incident("http_404", spread(6, 200, 7000), clf(404), level="WARNING", title_contains="HTTP 404 POST")

    f.write("node_express.log", lambda recs: "".join("\n".join(ls) + "\n" for _, ls in recs))


# ------------------------------------------------------------------------- Java
def make_java() -> None:
    f = Fixture("spring_boot", "java", None)
    pid = 4121

    def S(ts, lvl, thread, logger, msg):
        t = ts.strftime("%Y-%m-%dT%H:%M:%S.") + f"{ts.microsecond // 1000:03d}+00:00"
        return [f"{t} {lvl:>5} {pid} --- [shop-orders] [{thread}] {logger:<40} : {msg}"]
    for i, off in enumerate(steady(every=30)):
        ts = BASE + timedelta(seconds=off)
        f.add(ts, S(ts, "INFO", f"nio-8080-exec-{rng.randint(1, 8)}", "c.s.orders.OrderController",
                    ["Fetched order " + str(rng.randint(1000, 9999)), "Cart updated for session " + f"{rng.randrange(16**10):010x}",
                     "Health check ok"][i % 3]))

    def npe(cls: str, method: str, expr: str):
        def emit(ts, i):
            return S(ts, "ERROR", f"nio-8080-exec-{rng.randint(1, 8)}", "o.a.c.c.C.[.[.[/].[dispatcherServlet]",
                     "Servlet.service() for servlet [dispatcherServlet] in context with path [] threw exception [Request processing failed: java.lang.NullPointerException: " + expr + "] with root cause") + [
                f"java.lang.NullPointerException: {expr}",
                f"\tat com.shop.orders.service.{cls}.{method}({cls}.java:{rng.choice([88, 88, 88])})",
                f"\tat com.shop.orders.web.OrderController.get(OrderController.java:41)",
                "\tat java.base/jdk.internal.reflect.NativeMethodAccessorImpl.invoke0(Native Method)",
                "\tat org.springframework.web.servlet.FrameworkServlet.service(FrameworkServlet.java:883)",
                "\tat org.apache.catalina.core.StandardWrapperValve.invoke(StandardWrapperValve.java:197)",
                "\t... 41 common frames omitted",
            ]
        return emit
    f.incident("npe_order_total", spread(11, 500, 6000), npe("OrderService", "calculateTotal",
               'Cannot invoke "java.util.List.size()" because the return value of "Order.getItems()" is null'),
               level="ERROR", title_contains="Order.getItems()", exc_type="java.lang.NullPointerException")
    f.incident("npe_inventory", spread(5, 1000, 6500), npe("InventoryService", "reserve",
               'Cannot invoke "com.shop.model.Sku.getCode()" because "sku" is null'),
               level="ERROR", title_contains='because "sku" is null', exc_type="java.lang.NullPointerException")

    def db(ts, i):
        return S(ts, "ERROR", f"http-nio-8080-exec-{rng.randint(1, 8)}", "c.s.orders.web.GlobalExceptionHandler",
                 f"Unexpected error on request {rng.randrange(16**8):08x}") + [
            "org.springframework.dao.DataAccessResourceFailureException: Unable to acquire JDBC Connection; nested exception is org.hibernate.exception.JDBCConnectionException: Unable to acquire JDBC Connection",
            "\tat org.springframework.orm.jpa.vendor.HibernateJpaDialect.convertHibernateAccessException(HibernateJpaDialect.java:276)",
            "\tat com.shop.orders.repo.OrderRepositoryImpl.findById(OrderRepositoryImpl.java:57)",
            "Caused by: org.hibernate.exception.JDBCConnectionException: Unable to acquire JDBC Connection",
            "\tat org.hibernate.exception.internal.SQLStateConversionDelegate.convert(SQLStateConversionDelegate.java:100)",
            "Caused by: java.sql.SQLTransientConnectionException: HikariPool-1 - Connection is not available, request timed out after 30000ms.",
            "\tat com.zaxxer.hikari.pool.HikariPool.createTimeoutException(HikariPool.java:696)",
            "\tat com.zaxxer.hikari.pool.HikariPool.getConnection(HikariPool.java:197)",
            "\t... 58 common frames omitted",
        ]
    f.incident("hikari_timeout", until_end(4200, 45, 9), db, level="ERROR",
               title_contains="Connection is not available, request timed out after <N>ms", exc_type="java.sql.SQLTransientConnectionException",
               ongoing=True)

    def warn(ts, i):
        return S(ts, "WARN", f"scheduling-{rng.randint(1, 3)}", "c.s.orders.job.SyncJob",
                 f"Sync lagging by {rng.randint(20, 900)} seconds for warehouse WH-{rng.randint(10, 99)}")
    f.incident("sync_lag", spread(17, 100, 7100), warn, level="WARNING", title_contains="Sync lagging by")
    f.write("spring_boot.log", lambda recs: "".join("\n".join(ls) + "\n" for _, ls in recs))


# ------------------------------------------------------------------ JSONL (pino)
def make_jsonl() -> None:
    f = Fixture("pino_payments", "jsonl", "payments")

    def J(ts, level, msg, **extra):
        d = {"level": level, "time": int(ts.timestamp() * 1000), "pid": 17, "hostname": "pay-7f9c",
             "name": "payments", "msg": msg}
        d.update(extra)
        return [json.dumps(d, separators=(",", ":"))]
    for i, off in enumerate(steady(every=15)):
        ts = BASE + timedelta(seconds=off)
        f.add(ts, J(ts, 30, "charge ok", orderId=f"ord_{rng.randrange(16**8):08x}", amount=rng.randint(500, 20000),
                    latencyMs=rng.randint(80, 240)))

    def stack(msg, frames):
        return msg + "\n" + "\n".join(f"    at {fr}" for fr in frames)

    def timeout(ts, i):
        return J(ts, 50, "gateway call failed", err={"type": "Error", "message": "connect ETIMEDOUT 52.6.18.4:443",
                 "stack": stack("Error: connect ETIMEDOUT 52.6.18.4:443", [
                     "TCPConnectWrap.afterConnect [as oncomplete] (node:net:1555:16)",
                     "GatewayClient.charge (/srv/payments/src/gateway.js:61:17)"])}, orderId=f"ord_{rng.randrange(16**8):08x}",
                 authorization="Bearer zzzzzzzzzzzzzzzzzzzzzzzz")
    f.incident("gateway_timeout", until_end(2400, 40, 9), timeout, level="ERROR",
               title_contains="ETIMEDOUT", exc_type="Error", ongoing=True)

    def validation(ts, i):
        return J(ts, 50, "invalid charge request", err={"type": "ValidationError", "message": '"amount" must be greater than 0',
                 "stack": stack('ValidationError: "amount" must be greater than 0', [
                     "Object.validate (/srv/payments/src/schema.js:19:11)", "charge (/srv/payments/src/charge.js:33:5)"])},
                 userEmail="carol@example.org")
    f.incident("validation_amount", spread(8, 200, 6900), validation, level="ERROR",
               title_contains='"amount" must be greater than', exc_type="ValidationError")

    def retry(ts, i):
        return J(ts, 40, f"retrying charge, attempt {rng.randint(2, 5)}", orderId=f"ord_{rng.randrange(16**8):08x}")
    f.incident("retry_warn", spread(19, 300, 7000), retry, level="WARNING", title_contains="retrying charge, attempt")
    f.write("pino_payments.jsonl", lambda recs: "".join(ls[0] + "\n" for _, ls in recs))


# -------------------------------------------------------------------------- CSV
def make_csv() -> None:
    f = Fixture("telemetry", "csv", None)
    import csv
    import io

    def row(ts, host, lvl, msg, cpu, lat, q):
        return [ts.strftime("%Y-%m-%dT%H:%M:%SZ"), host, lvl, msg, f"{cpu:.1f}", str(lat), str(q)]
    for off in steady(every=30):
        ts = BASE + timedelta(seconds=off)
        h = rng.choice(["edge-a", "edge-b"])
        f.add(ts, row(ts, h, "INFO", "heartbeat ok", rng.uniform(12, 38), rng.randint(20, 110), rng.randint(0, 6)))

    def upstream(ts, i):
        return row(ts, rng.choice(["edge-a", "edge-b"]), "ERROR",
                   f"timeout calling inventory after {rng.randint(2900, 3100)}ms, retrying", rng.uniform(70, 95),
                   rng.randint(2800, 3600), rng.randint(40, 200))
    f.incident("upstream_timeout", until_end(3600, 12, 4),
               lambda ts, i: upstream(ts, i), level="ERROR", title_contains="timeout calling inventory after <N>ms",
               ongoing=True)

    def disk(ts, i):
        return row(ts, "edge-b", "WARN", 'disk usage high, volume "/data" at ' + f"{rng.randint(86, 97)}%", rng.uniform(20, 40), rng.randint(40, 120), 3)
    f.incident("disk_warn", spread(10, 500, 7000), disk, level="WARNING", title_contains="disk usage high")

    def render(recs):
        buf = io.StringIO()
        w = csv.writer(buf, lineterminator="\n")
        w.writerow(["timestamp", "host", "level", "message", "cpu_pct", "latency_ms", "queue_depth"])
        for _, r in recs:
            w.writerow(r)
        return buf.getvalue()
    f.write("telemetry.csv", render)


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    make_python()
    make_node()
    make_java()
    make_jsonl()
    make_csv()
