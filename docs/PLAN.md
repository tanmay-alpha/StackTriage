# Implementation milestones

Historical scope and implementation order. See `README.md` and
`docs/architecture.md` for the current behavior; this list is not a live task queue.

## Principles
- Core engine is stdlib-only Python and testable without a server or database.
- Every number shown to a user is computed from recorded events.
- ML is one detection candidate, selected only if it beats a deterministic baseline
  on labelled fixtures; the result is documented either way.
- Anomaly score, anomaly classification and incident severity are three separate
  quantities.
- Single-user, SQLite, one deployable backend.

## Milestones
1. Repository, license, `docs/foundation-audit.md`.
2. Parsers (Python, Node, Java, JSONL, CSV), schemas, redaction, diagnostics, limits.
3. Fingerprint grouping, timeline, group activity. Acceptance: ground-truth fixtures.
4. SQLite persistence (idempotent imports).
5. Anomaly detection over time windows: statistical baseline vs Isolation Forest,
   evaluation script, measured results.
6. FastAPI backend: ingestion, incidents, filters, detail, review status, notes,
   export, API key auth, bounded pagination.
7. React + TypeScript + Vite frontend: Overview, Import, Incidents, Investigation.
8. Playwright end-to-end, security review and repairs.
9. Deployment and documentation.

All nine milestones above are delivered and covered by CI (backend, frontend, docker).

## Next milestones (proposed, not implemented)
Ordered by interview value per unit of risk. Each must ship with tests and measured,
reproducible evidence; none is claimed as delivered.

1. **Import comparison and regression detection.** Groups are stored per import
   (`groups` PK `(import_id, fingerprint)`) while triage is global per fingerprint, so a
   diff of two imports is a set join: *new*, *gone*, *recurring* with rate change, and
   *regressed* (fingerprint marked `resolved` that reappears). API
   `GET /api/imports/{a}/compare/{b}`, an Incidents-view comparison mode, and fixture
   pairs with expected diffs. Low risk: read-only over existing tables.
2. **Trend detector for slow ramps.** The statistical detector's documented weakness is
   slow ramps (window recall 0.34, first flag after 14.7 windows). Add an EWMA/CUSUM
   candidate on the same features and evaluate it in `scripts/evaluate_anomaly.py`
   with the same dev/eval seed split and gates; adopt only if pooled F1/FPR do not regress.
3. **Template mining for free-text normalization.** Unquoted variable words split groups.
   A Drain-style parse tree (fixed depth, similarity threshold) can learn templates per
   import. Evaluate grouping accuracy against ground-truth templates (LogHub 2k
   samples, after licence review) and keep the current fingerprint as the stable key.
4. **Background ingestion jobs.** Imports are request-bound today. A `jobs` table,
   worker thread, progress endpoint and cancellation would remove upload timeouts for
   large files while keeping the single-writer SQLite model.
5. **SQLite FTS5 event search.** Replace `LIKE %term%` over events with an FTS5 index
   (bm25 ranking, prefix queries), with a benchmark on a 200,000-event import.
6. **External dataset evaluation.** Candidate: LogHub HDFS_v1 (block-level anomaly labels,
   <https://github.com/logpai/loghub>). Its README states the datasets are free for research
   or academic work with a repository reference and citation, and that logs are not
   sanitized: download at evaluation time, never commit them. Block labels do not map
   directly to time windows; document that mismatch before publishing any number.
7. **Hosted demo.** `render.yaml` is ready but requires a paid instance and disk; needs
   the owner's approval.

## Package layout
```
src/stacktriage/
  models.py        LogEvent, Diagnostics, Limits
  redaction.py     credential / PII masking (applied at parse time)
  parsers/         common (bounded reader, time, levels, ids), text, structured, detect
  fingerprint.py   deterministic error identity
  grouping.py      streaming ErrorGroup accumulation, recurrence
  timeline.py      per-window aggregation
  analysis.py      one-pass analyze(events) -> Analysis
  cli.py           python -m stacktriage analyze FILE
```
