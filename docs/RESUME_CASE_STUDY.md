# StackTriage: resume case study

## Project description

StackTriage is a single-user application-log triage workspace, not a root-cause
engine or a production observability platform. It accepts Python, Node/Express,
Java/Spring Boot, JSON Lines and CSV logs, groups recurring problems, builds a
timeline and persists review status and investigation notes. FastAPI serves the
API; React, TypeScript and Vite provide the browser UI; SQLite stores imports and
triage data. The core parser/grouping engine has no third-party dependencies.

**Do not upload confidential logs, credentials or personal data.** Demonstrate
with the checked-in synthetic fixtures. Redaction is pattern-based and cannot
prove that an upload, database, note, export or backup is safe to share.

This case study describes repository capabilities and recorded synthetic results.
It does not claim real users, production traffic, business impact, deployment
success or real-world detection accuracy.

## Four truthful resume bullets

- Built a FastAPI, SQLite and React/TypeScript log-triage application supporting
  five input families, deterministic error grouping, incident timelines and
  persistent investigation status and notes.
- Implemented bounded ingestion, best-effort credential redaction, shared-key API
  authentication, atomic SQLite imports and content-based duplicate detection;
  added synthetic fixtures and automated regression coverage.
- Built before/after-deployment import comparison: traffic-normalized error rates,
  an exact binomial test with Benjamini-Hochberg correction and a minimum effect size
  to separate real increases from larger captures, with compatibility warnings and
  links into the investigation workflow; verified against a synthetic pair with
  declared ground truth.
- Evaluated a median/MAD statistical detector against Isolation Forest on labelled
  synthetic time-window scenarios; documented the statistical baseline's reported
  F1 of 0.909 versus 0.719 for Isolation Forest, along with false alarms,
  slow-ramp weaknesses and training-contamination limits.

Use these only for work you personally performed and can explain. The metric
bullet must retain the synthetic-data qualification.

## Architecture walkthrough

1. **Upload boundary.** `POST /api/imports?filename=...` accepts a raw
   `application/octet-stream` body, not multipart form data. The API checks auth,
   validates options and streams into a size-capped temporary spool. Defaults are
   25 MiB per API upload and 200,000 parsed events.
2. **Parse and sanitize.** Format sniffing selects text, JSONL or CSV parsing.
   Multiline traces become part of one event; timestamps normalize to UTC.
   Recognized sensitive patterns are redacted before event storage. Diagnostics
   explain malformed records, missing timestamps, redactions and truncation.
3. **Analyze without a database write lock.** The engine computes groups,
   per-second counters and rebinned windows while redacted event rows spool to
   temporary storage. Untimestamped events remain counted but cannot enter the
   timeline. Missing windows mean no timestamped records in the file, not proven
   absence of application traffic.
4. **Persist atomically.** SQLAlchemy writes import metadata, events, groups and
   reports in one SQLite transaction. A unique content key combines the file's
   SHA-256 with format/service options. A repeat returns the existing import;
   this is import-level idempotency, not cross-import event deduplication.
5. **Detect unusual windows.** The detector uses the earlier half of the timeline
   for training by default, filters extreme candidate training windows, fits
   robust feature baselines and calibrates thresholds using training data only.
   Consecutive anomalous windows form episodes. Detection is advisory: a detector
   failure is recorded without discarding an otherwise valid import.
6. **Investigate.** The UI presents Overview, Import, Incidents and Investigation
   views. Status and notes are keyed by fingerprint, so they carry into another
   import with the same fingerprint. Exports support CSV, JSON and Markdown.

Source map: [API](../src/stacktriage/api/app.py),
[parsers](../src/stacktriage/parsers/),
[analysis](../src/stacktriage/analysis.py),
[persistence](../src/stacktriage/db/store.py),
[database design](architecture.md), [frontend](../frontend/src/).

## Why FastAPI, SQLite and React?

These are tradeoffs supported by the implemented architecture, not measured
performance comparisons:

- **FastAPI:** typed request/response schemas, dependency-based shared-key auth,
  API validation and an OpenAPI contract fit the Python analysis engine. Ingestion
  work runs through a thread pool rather than parsing on the async request loop.
  That does not make ingestion a durable background job or remove resource limits.
- **SQLite:** one local database makes a single-user installation and online
  backups straightforward. WAL, foreign keys, a busy timeout and versioned schema
  migrations support persistence without another database service. SQLite still
  has a single writer; the deployment requires persistent storage and one backend
  instance, not horizontal scaling across hosts.
- **React + TypeScript + Vite:** separate typed UI views support import progress,
  filtering and investigation interactions; the built SPA can be served by the
  backend in the same container. Frontend types are maintained in the repository;
  they are not evidence of generated, automatically synchronized API clients.

## Fingerprint grouping

[`fingerprint.py`](../src/stacktriage/fingerprint.py) groups warning-or-higher
records and records carrying traces. It extracts the last Python exception chain,
the deepest Java `Caused by:` block or a Node exception, then chooses an application
frame where possible. The key is the first 16 hex characters of SHA-1 over service,
kind, exception type, normalized message and frame, with separators.

Normalization masks variable tokens such as UUIDs, numbers, query strings and
some identifiers. Quoted literals are normalized for trace identity only when a
frame anchors the location. Selected HTTP status contexts and service ports are
preserved. In the Spring Boot fixture, two null-pointer failures stay separate
while repeated connection timeouts group together.

This is deterministic grouping, not semantic similarity or proof of one root
cause. Unquoted changing words can split groups; shifted frame line numbers can
split a group after deployment; normalization and truncated hashes can merge
unrelated records. SHA-1 here is an identity heuristic, not a security primitive.

## Before/after comparison

[`compare.py`](../src/stacktriage/compare.py) answers "what changed after this
deployment?" from two persisted imports without re-reading raw logs. It is a pure
function over aggregate group rows; the API adds one bounded column select per import and
performs no writes (a test asserts no INSERT/UPDATE/DELETE statement runs).

- **Normalization first.** The default rate is occurrences per 1,000 events of the same
  import, so doubling traffic or capture length does not read as a regression. Per-minute
  rates are offered only when both captures have usable timestamp coverage, and rows
  where the two bases disagree are flagged instead of silently choosing one.
- **Evidence before verdicts.** Conditional on the total, the target count is binomial
  under equal rates; an exact two-sided test, Benjamini-Hochberg adjustment across all
  groups and a 1.5x minimum ratio are all required for `increased`/`decreased`. With
  large counts a 1.2x change is significant but not reported; with tiny counts nothing is.
- **Careful wording.** "Disappeared" means absent from the target capture. A group marked
  resolved that appears in the target is flagged for review, not called a regression,
  because review status carries no fix timestamp.
- **Known limit, demonstrated.** Exact fingerprint matching means a deploy that moves a
  frame's line number splits a group into disappeared + new; the fixture pair includes
  that case on purpose, and no fuzzy merging is attempted.

Evidence: `tests/test_compare.py` (statistics against known values and SciPy, BH,
zero/missing timestamps, unequal volumes and durations, low counts, auth, validation,
export injection safety, read-only behaviour) and the fixture pair whose expected
categories are declared in `scripts/make_compare_fixtures.py`. These are synthetic
results; no performance figures are claimed.

## Statistical versus Isolation Forest

Both candidates use window features: error count, error ratio, falling event rate,
new groups and means of sufficiently covered numeric metrics. The statistical
method uses the largest unusual-direction robust deviation from a median/MAD
baseline, with scale floors for sparse data. Isolation Forest uses scikit-learn,
is refitted per analysis and uses out-of-fold training scores for calibration.
Neither reads message semantics or loads a serialized model.

The statistical candidate is the default: it is dependency-free and directly
explains which feature deviated. Isolation Forest can model combinations but adds
an optional dependency and seed sensitivity. Its displayed feature contributions
come from the robust baseline, not causal explanations of its trees. Both need an
adequately healthy initial period and at least 12 retained training windows.

### Metrics and their limits

The following are **recorded results**, copied from
[the evaluation report](ml-evaluation.md), not a new benchmark run. Main synthetic
scenarios, later period only; mean +/- sample standard deviation across seeds 0-9:

| Candidate | Precision | Recall | F1 | False-positive rate/window | Episode precision |
|---|---|---|---|---|---|
| Statistical | 0.890 +/- 0.034 | 0.930 +/- 0.020 | 0.909 +/- 0.021 | 0.015 +/- 0.005 | 0.502 +/- 0.101 |
| Isolation Forest | 0.634 +/- 0.062 | 0.832 +/- 0.040 | 0.719 +/- 0.053 | 0.063 +/- 0.015 | 0.221 +/- 0.016 |

Window precision is the share of flagged windows labelled anomalous; recall is the
share of labelled windows flagged; F1 balances those two. False-positive rate is
the share of healthy windows flagged. Episode precision counts contiguous flagged
runs that overlap a labelled incident: a good window F1 can still yield distracting
false episodes. Anomaly score is not a probability, and severity is graded from
operational evidence separately from score and pattern classification.

The report records constants chosen on development seeds 1000-1005 and evaluation
on seeds 0-9. Isolation Forest detected slow ramps earlier (4.4 versus 14.7 windows
on average), despite worse pooled results. Statistical slow-ramp window recall was
0.34; an incident filling 58% of the training period was detected in only 1/10 runs.
About half the statistical episodes touched no labelled incident. There is no
seasonality model, INFO-only flood detector or established real-world accuracy.

Reproduce with `python scripts/evaluate_anomaly.py` after installing the ML extra.
Parser acceptance checks use `python scripts/acceptance_report.py`. Fixture counts
are expectations in [`tests/fixtures/expected/`](../tests/fixtures/expected/), not
claims about production volume. No current test total, latency, throughput or
memory measurement is claimed here.

## Security design and limits

- API-key comparison uses `hmac.compare_digest`; production requires a key of at
  least 16 characters. Bearer and `X-API-Key` headers are supported. The browser
  keeps the entered key in session storage, falling back to page memory if storage
  writes are blocked; the fallback is lost on reload. It never uses local storage,
  a build-time variable or a URL for the API key.
  Health and static UI routes are public. There are no accounts, roles or tenant
  boundaries: everyone with the shared key has the same access.
- Local development without a key is unauthenticated. The automatic Host allow-list
  restricts that mode to loopback/test names; with a key, configure allowed hosts
  explicitly. CORS is an origin policy, not authentication. Use HTTPS and network
  access controls before remote exposure.
- Upload/event limits and parser limits bound individual inputs, not aggregate
  concurrency, disk usage or total retained history. No rate-limiting or retention
  service is established by those caps. Raw uploads can spill to temporary disk;
  redacted rows also spool during analysis. Protect temporary storage and backups.
- Pattern-based redaction misses unknown secrets and sensitive context. Treat log
  text, filenames, diagnostics, user-entered notes, exports and the SQLite file as
  sensitive. Do not rely on the redaction counter as proof of confidentiality.
- Security headers restrict browser framing and script sources; API responses use
  `Cache-Control: no-store`. CSV formula-like cells are prefixed to reduce
  spreadsheet injection risk. Neither defense makes arbitrary logs trustworthy.
- Models fit in memory; report parameters are JSON-safe values, not executable
  pickle/joblib artifacts. This avoids a model-deserialization surface, not all
  possible code-execution or dependency risks.
- The Dockerfile runs as UID 10001 and owns `/data` in the image. Existing mounts
  must separately permit that UID to create SQLite DB/WAL/SHM files. Protect the
  host and database; use online backups and stop the server before restoring.
  The production image is built and exercised by `scripts/docker_verify.py` in CI
  and locally (restart, recreation on the same named volume, backup/restore, auth,
  log-leak checks; see [deployment](deployment.md#repeatable-container-verification)).
  No hosted deployment exists.

See [settings](../src/stacktriage/api/settings.py),
[redaction](../src/stacktriage/redaction.py),
[export handling](../src/stacktriage/api/export.py),
[browser API client](../frontend/src/api/client.ts) and
[deployment instructions](deployment.md).

## Twelve interview questions and answer anchors

1. **What problem does this solve?** Repeated-log grouping and investigation of a
   finite imported file; not automatic diagnosis, tracing or live monitoring.
2. **How do you join multiline exceptions?** Text headers delimit records;
   continuation lines accumulate in a bounded trace, with truncation diagnostics.
3. **Why not group by exception type alone?** The two fixture null-pointer groups
   illustrate why service, normalized message and frame also matter.
4. **What breaks fingerprint stability?** Changing service, unnormalized text or
   frame line numbers; normalization can also collapse distinct messages.
5. **How are duplicate imports handled safely?** File-content/options key plus a
   UNIQUE constraint; concurrent duplicates resolve to the persisted import.
6. **Why parse outside the write transaction?** Avoid holding SQLite's write lock
   throughout parsing; spool redacted rows, then commit all stored data together.
7. **How is temporal leakage limited?** Earlier-period training and training-only
   threshold calibration; a contaminated first half can still invalidate baseline
   assumptions. Isolation Forest calibration uses out-of-fold training scores.
8. **Why retain the simpler detector?** Better reported pooled synthetic F1 and
   false-positive rate; acknowledge Isolation Forest's earlier slow-ramp detection.
9. **How do score and severity differ?** Unusualness is relative to a fitted
   baseline; severity uses error volume, duration, ratios and other episode
   evidence. Neither establishes actual customer impact.
10. **What would block a production/team claim?** Shared-key-only authorization,
    single-instance SQLite, no live collection, synthetic-only evaluation and
    unvalidated operational capacity; document those gaps rather than imply scale.
11. **How do you know an error got worse after a deploy and not just a bigger log?**
    Compare per-1,000-event rates; test the counts with an exact conditional binomial
    test, correct across groups (Benjamini-Hochberg) and require a 1.5x ratio. Show the
    pool-exhaustion fixture: count doubles, rate does not.
12. **Why not call a resolved error that reappears a regression?** Global review status
    has no fix timestamp or release link, and a capture can predate the fix; flag it for
    review instead. Recording fix versions would be the prerequisite for a stronger claim.

## Five-minute synthetic demo

Prepare using the [README setup](../README.md#install-and-run): install extras,
build the frontend and start `stacktriage serve` from the repository root (or use the
README Docker quick start). In a second shell with the virtual environment active, run:

```sh
python scripts/demo.py
python scripts/demo.py --compare
```

The first command sends only `spring_boot.log` and `telemetry.csv` from the checked-in
fixtures, compares format/event/group counts with their expected JSON files and
re-uploads each to check duplicate detection. A rerun reuses imports and preserves
review state. `--compare` imports `deploy_before.log` and `deploy_after.log`, marks the
warehouse `ConnectionError` group resolved if it is still open (simulating an earlier
fix) and checks the comparison against the declared ground truth. The scripts write to
the running server's database; they do not erase data, start a server or generate
fixtures. Set `STACKTRIAGE_API_KEY` in their environment if the server requires
authentication; `--url` selects another backend. Do not send an API key to an
untrusted server or use public plain HTTP.

| Time | Action and truthful narration |
|---|---|
| 0:00-0:40 | Open the app and select the Spring Boot import. Show 340 synthetic events and four groups; this is a fixture, not customer data. |
| 0:40-1:20 | Inspect diagnostics/redactions and Overview. Point out the timeout group beginning near 11:10 UTC; missing timestamps cannot appear in the timeline. |
| 1:20-2:00 | Open Incidents and search `NullPointer`. Show the two separate groups. Explain that ongoing is relative to the end of this file, not a live health check. |
| 2:00-2:40 | Open the connection timeout group (67 expected occurrences), inspect its trace and timeline, set investigating and add a synthetic note. Reload; show persisted state. |
| 2:40-4:10 | Open the URL printed by `demo.py --compare`. Baseline is before, target after the synthetic deployment; traffic doubled. Show the new `AttributeError`, the payment `TimeoutError` increase per 1,000 events, the pool error whose count doubled but whose rate did not (and the per-minute disagreement note), the warehouse error flagged as marked resolved but observed, and the tax `LookupError` split by a moved line number. Open the payment row's investigation page; export Markdown. |
| 4:10-5:00 | Open the evaluation table above. State synthetic-only results, slow-ramp and contaminated-baseline failures, comparison limits (exact matching, even-spread rate assumption) and the shared-key/single-instance security limits. Do not claim a public deployment. |
