# StackTriage

Upload an application log, see which errors occurred, which repeat, when each
started and whether it is still happening.

**Status: in development.** The log-processing engine (parsing, redaction, error
grouping, timelines) is implemented and tested. Anomaly detection, the API and the
web UI are not built yet; this README is extended as each lands.

## What works now

```bash
pip install -e ".[dev]"
python -m stacktriage analyze tests/fixtures/spring_boot.log
```

```
tests/fixtures/spring_boot.log: java, 340 events, 2026-03-05T10:00:26+00:00 -> 2026-03-05T11:59:30+00:00
levels: ERROR=83, INFO=240, WARNING=17
4 error groups:
      67x [ERROR] shop-orders: java.sql.SQLTransientConnectionException: <ID> - Connection is not available, request timed out after <N>ms.
          2026-03-05T11:10:05.232000+00:00 .. 2026-03-05T11:59:30.474000+00:00  periodic, ongoing
      17x [WARNING] shop-orders: Sync lagging by <N> seconds for warehouse <ID>
          2026-03-05T10:05:00.722000+00:00 .. 2026-03-05T11:33:35.951000+00:00  irregular, ended
      11x [ERROR] shop-orders: java.lang.NullPointerException: Cannot invoke "java.util.List.size()" because the return value of "Order.getItems()" is null
          2026-03-05T10:15:19.032000+00:00 .. 2026-03-05T11:38:31.231000+00:00  irregular, ended
       5x [ERROR] shop-orders: java.lang.NullPointerException: Cannot invoke "com.shop.model.Sku.getCode()" because "sku" is null
          2026-03-05T10:34:21.766000+00:00 .. 2026-03-05T11:20:11.328000+00:00  irregular, ended
```

Supported inputs: Python `logging` text, Node.js/Express (winston-style lines, Common
Log Format access lines), Java/Spring Boot and Logback text, JSON Lines (including
pino/bunyan numeric levels), CSV exports with a timestamp/level/message column.
Multiline stack traces are kept with their record. Credentials and personal data are
redacted at parse time, before anything is stored or shown.

See `docs/foundation-audit.md` for the review of the upstream project this started
from, and `docs/PLAN.md` for scope and milestones.

## Tests

```bash
python -m pytest
python scripts/acceptance_report.py   # expected vs actual for each fixture
```

## License

MIT. See `LICENSE` and `NOTICE.md` for attribution.
