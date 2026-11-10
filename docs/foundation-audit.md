# Foundation audit: swagatblog/AIML_LogAnalyser

Reference: <https://github.com/swagatblog/AIML_LogAnalyser>, single commit `fa072e2`, MIT.
Cloned to a separate directory outside this repo. Everything below was run, not
copied from the earlier audit, except where marked *by inspection*.
`scripts/audit_upstream.py` reproduces the numbers (Python 3.11, upstream's pinned
`scikit-learn==1.4.2`, `numpy==1.26.4`).

## What upstream is

~600 lines of Python: a synthetic-telemetry generator (`data/generate_data.py`), a
`ColumnTransformer` + `IsolationForest` wrapper (`src/preprocess.py`, `src/model.py`),
a train script, and a FastAPI app with `/predict`, `/predict_batch`, `/metrics`.
Input is a fixed 11-column record (cpu, memory, latency, failed logins, ...), one
row per request.

## Findings from the earlier audit, verified

| Claim | Result | Evidence |
|---|---|---|
| Isolation Forest is genuine but runs on structured telemetry | **Confirmed** | `src/model.py` wraps `sklearn.ensemble.IsolationForest` on 7 numeric + 3 one-hot columns. |
| Synthetic anomalies are trivially separable | **Confirmed, stronger than stated** | On its own test set, the raw features `cpu_usage`, `memory_usage`, `disk_io_mbps`, `network_latency_ms` and `failed_login_count` each reach ROC-AUC **1.0000 alone, with no model**. `failed_login_count >= 5` is a rule with precision 1.0, recall 1.0. Value ranges do not overlap (normal max CPU 44.9 vs anomaly min 85.3; latency 120 vs 802). `log_level` in ERROR/CRITICAL/FATAL is also AUC 1.0. The generator draws anomalies from disjoint uniform ranges, so the reported ROC-AUC 1.0 says nothing about real detection. |
| Hard-coded severity thresholds are unreliable | **Confirmed, worse than stated** | `api/app.py` maps score >= 0.20 CRITICAL, >= 0.10 HIGH, >= 0.05 MEDIUM. `-score_samples` on the test set lies in [0.402, 0.655], so **all 900 normal rows and all 100 anomalies are labelled CRITICAL**. Severity is also derived directly from the anomaly score, conflating "statistically unusual" with "operationally severe". |
| Doesn't ingest ordinary application logs | **Confirmed** | Validating `{"message": "2024-03-05 12:00:01,123 ERROR app.db: connection refused"}` against `LogRecord` fails with 11 validation errors. No text/JSON-lines parser, no stack-trace handling, no timestamps in the model. |
| ML tests may skip without generated data | **Confirmed** | `pytest` on a fresh clone: `1 passed, 5 skipped` (`tests/test_pipeline.py` skips when `data/train_logs.csv` is absent). Only the `/health` test runs. |
| No persistent incident-management app | **Confirmed** | No database, no incidents, no review state or notes; `/predict` is stateless. |
| Docker depends on generated model artifacts | **Confirmed by inspection** | `docker/Dockerfile` has `COPY models/ ./models/`, but `models/*.joblib` is gitignored and `git ls-files models` is empty, so a fresh clone has no `models/` directory and the build step cannot succeed until `python -m src.train` is run. I could not run `docker build` (Docker Desktop daemon was not running); this is from reading the Dockerfile and `git ls-files`. |

## Additional findings

- **Training-set contamination:** `contamination=0.05` is hard-coded while the
  training file is 3% anomalies; on the test set 18 of 900 normal rows are flagged
  (precision 0.847 despite perfect separability). The comment "tuned via
  cross-validation in train.py" is false: no cross-validation exists.
- **No temporal handling:** rows are shuffled before the train/test files are
  written, and the model has no time dimension, so there is no concept of "earlier
  healthy period vs later incident".
- **Unseen categories score as normal:** a record with an unseen `device_model` and
  `event_type` scores 0.373, lower than every test-set score (0.402-0.655), i.e. maximally
  "normal", because one-hot encoding silently drops unknown categories.
- **Untrusted pickle surface:** `LogAnomalyDetector.load()` calls `joblib.load` on
  whatever file is at the path. Loading a tampered artifact can execute code.
- **Dead dependencies/exposure:** `prometheus-client` and `loguru` are pulled in for
  a metrics endpoint with no auth; the API has no authentication and binds `0.0.0.0`.

## Decisions

| Component | Decision | Notes |
|---|---|---|
| Idea: preprocessing + model in one fit/score/persist wrapper | **Retained (idea only)** | Re-implemented for time-window features in the ML milestone; no upstream code copied. |
| Idea: Isolation Forest as an unsupervised candidate | **Retained, demoted** | One candidate next to a deterministic baseline, chosen by measured results, trained on a healthy period only. |
| Idea: FastAPI service with `/health` | **Retained (idea only)** | Rewritten around incidents, ingestion runs and auth. |
| `src/preprocess.py`, `src/model.py` | **Rewritten, not reused** | Input type is different (window features, not telemetry rows); persistence avoids unsafe pickle loading. |
| `src/config.py`, `src/train.py`, `src/predict.py` | **Rejected** | Hard-coded columns/paths and an in-sample evaluation. |
| `data/generate_data.py` | **Rejected** | Disjoint-range anomalies make evaluation meaningless. Replaced by fixtures where the anomalous period overlaps normal ranges on any single metric and log level alone cannot separate it. |
| Severity thresholds in `api/app.py` | **Rejected** | Replaced by separate score / classification / severity (see ML docs). |
| `/predict`, `/predict_batch`, `/metrics` | **Rejected** | Different product (incident triage, not per-record scoring). |
| `docker/` | **Rejected** | Depends on untracked artifacts; to be rewritten once artifacts are reproducible. |
| `sample_logs/` | **Rejected** | Same telemetry schema; not ordinary application logs. |
| `tests/test_pipeline.py` | **Rejected** | Skips silently; asserts on trivially separable data. |
| `LICENSE` (MIT) | **Retained as attribution** | Reproduced in `NOTICE.md`. |

**Files reused verbatim from upstream: none.** Everything under `src/stacktriage/`
is new.
