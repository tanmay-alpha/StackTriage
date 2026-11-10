# Implementation plan (authoritative)

Single source of truth for scope and order. Changes to scope are recorded here
before code changes.

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
4. SQLite persistence (idempotent imports). [next]
5. Anomaly detection over time windows: statistical baseline vs Isolation Forest,
   evaluation script, measured results.
6. FastAPI backend: ingestion, incidents, filters, detail, review status, notes,
   export, API key auth, bounded pagination.
7. React + TypeScript + Vite frontend: Overview, Import, Incidents, Investigation.
8. Playwright end-to-end, security review and repairs.
9. Deployment and documentation.

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
