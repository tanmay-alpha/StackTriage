# Anomaly detection over time windows: method, evaluation, decision

Everything here is reproduced by `python scripts/evaluate_anomaly.py` (about 75 s on the
machine that produced the numbers; Python 3.11, scikit-learn 1.5.2, numpy 2.4.6). The tables
below are a verbatim paste of one run on seeds 0-9.

**What the detector claims.** A window (or run of windows) is *statistically unusual compared
with an earlier healthy period of the same log*, and these features deviate. It never claims
a root cause, and it does not "understand" individual messages. It looks at activity across
time windows only.

## 1. Three different quantities

| quantity | type | derived from | where |
|---|---|---|---|
| **anomaly score** | continuous, 0..1 | how far the worst feature deviates from the training baseline, divided by the decision threshold fitted on the training period: `raw / (raw + threshold)`. 0.5 = the decision threshold, so `is_anomalous  <=>  score >= 0.5`. Not a probability; comparable only inside one report. | `WindowFinding.score` |
| **anomaly classification** | label | explicit rules over *which* features deviate and for how long | `WindowFinding.classification` |
| **incident severity** | low / medium / high / critical | operational evidence of the episode (error volume, error ratio, duration, still ongoing, new error groups, traffic loss, metric shift). Never reads the score. `None` for windows that are not anomalous. | `WindowFinding.severity`, `Episode.severity` |

This replaces the upstream project's defect (every row `CRITICAL` because severity was a
threshold on the anomaly score). Severity has no access to the score: `severity.grade()` takes
only an `EpisodeEvidence`. Tests prove the three can disagree (a brief extreme blip has a
higher score and a lower severity than a milder, longer, still-running problem; two episodes
with the same peak score get different severity).

### Classification rules (`anomaly/classify.py`)

"Deviates" = at least 3.0 robust sigmas in the unusual direction. First match wins:

1. `new_error_type`: `new_groups` deviates and no other feature does (low-volume novelty).
2. `quiet_period`: `event_rate` is below baseline and `error_count` does not deviate (traffic
   dropped without an error surge: silent failure, upstream outage, logging gap).
3. `deviation`: `error_count`, `error_ratio`, a numeric metric, or `new_groups` together with
   other features deviate. Resolved by duration: a run of 1-2 consecutive anomalous windows is a
   `spike`; 3 or more is a `sustained_shift`.
4. `unusual_pattern`: flagged by the model but no single feature reaches 3 sigma (possible for
   combinations of small deviations). Contributions still name the largest.
5. `normal`: not anomalous.

Episode classification = classification of its peak-score window.
`WindowFinding.contributions` lists the top (max 3) features that deviate by >= 2 sigma:
`{"feature", "observed", "baseline", "deviation"}` where `baseline` is the training median and
`deviation` the signed robust z-score. For Isolation Forest the contributions are computed the
same way (they describe which features moved, not how the forest decided).

### Severity rules (`anomaly/severity.py`)

Points are added per piece of evidence; the total is mapped to a level.

| evidence | points |
|---|---|
| peak error ratio >= 50 % / >= 20 % / >= 10 % | 3 / 2 / 1 |
| errors above the baseline over the episode >= 200 / >= 50 / >= 10 | 3 / 2 / 1 |
| duration >= 30 min / >= 5 min | 2 / 1 |
| episode includes the last window of the data ("still ongoing") | 1 |
| new error groups in the episode >= 3 / >= 1 (log-start warm-up windows excluded) | 2 / 1 |
| event rate lost vs baseline (mean over the episode) >= 80 % / 50 % / 25 % | 3 / 2 / 1 |
| largest numeric-metric shift vs baseline >= 100 % / >= 30 % | 2 / 1 |

Level: 0-1 `low`, 2-3 `medium`, 4-6 `high`, >= 7 `critical`. Every episode carries the human
readable list of the rules that fired (`Episode.evidence`). The thresholds are operational
judgement calls, not fitted to data; they are absolute (minutes, share of error lines), so for
very long windows (hours) every episode earns the duration points.

## 2. Method

### Features (per window, `anomaly/features.py`)

| feature | unusual direction | noise floor of the scale |
|---|---|---|
| `error_count` (ERROR + CRITICAL) | up | `sqrt(max(median, 1))` (Poisson) |
| `event_rate` (events / s) | **down** only | 10 % of the median |
| `error_ratio` (errors / events) | up | 0.01 |
| `new_groups` (error fingerprints first seen in the window) | up | 0.1 (one new group = 10 sigma over an all-zero baseline) |
| `metric.<name>` (per-window mean of a numeric metric, e.g. `metric.latency_ms`) | both | 5 % of `|median|` |

Metrics come from the new `Bucket.metrics` (mean per metric name over the events that carried
it; absent when none did). A metric becomes a feature if at least half of the training windows
have it; a window without it counts as typical for that feature.

`event_rate` deliberately ignores *increases*: a traffic surge of INFO lines with a normal error
count is not an incident (the `busy_healthy` trap). A surge that comes with errors is caught by
the error features. A log-flood incident of non-error lines would be missed. This is a design
decision, not an oversight.

### Baseline and split (no leakage)

1. **Split by time.** Training candidates are the first `train_fraction` (default 0.5) of the
   windows. Nothing later is used to fit anything: medians, MAD scales, the decision
   threshold, the Isolation Forest and its calibration all see training windows only. There is
   no shuffling. `AnomalyReport.train_range` is `(start of first window, end of last candidate
   window)`; `WindowFinding.used_for_training` marks the windows actually used.
2. **Contamination guard.** An incident that begins inside the training period would be learned
   as normal. So: fit median/MAD on all candidates (breakdown point about 50 %), mark every
   candidate whose worst unusual-direction robust z exceeds 6.0 as *not healthy*, and refit the
   baseline on the rest (one pass, no cascade). Excluded windows are still scored (and flagged).
   Tested (`test_early_incident_does_not_poison_the_baseline` plus a control that disables the
   rule and shows the incident is then missed).
3. **Warm-up.** In the first 10 % of the training candidates the `new_groups` value is ignored
   (every error type is "new" at the start of any log).
4. **Minimum.** At least 12 healthy training windows are required. Fewer, an empty timeline or an
   all-zero timeline give a report with one `note`, unscored windows (score 0, not anomalous) and
   no episodes, never an exception.

### Candidates

* **statistical**: score = max over features of the robust z in the unusual direction
  `(x - median) / max(1.4826 * MAD, floor)`. Threshold = `max(4.0, q98 of the training scores)`.
  Deterministic, no dependency.
* **isolation_forest**: scikit-learn `IsolationForest` (100 trees, `max_samples=0.7`) on the same
  unusual-direction deviations, **refit from the training windows on every call**; nothing is
  ever loaded from a file (no pickle/joblib anywhere in the package; a test greps for it).
  Training windows are re-scored out-of-fold (3 folds) so the threshold is not calibrated on
  memorised scores: threshold = `max(0.02, 1.2 * q90 of the out-of-fold scores)` above their median.
  scikit-learn is imported lazily; without it `isolation_forest` falls back to `statistical`
  with a note.
* **error_count_rule** (sanity floor, not a candidate): flag a window when ERROR count >
  mean + 3 sd of the first half.

`AnomalyReport.parameters` is plain JSON-safe data (medians, scales, thresholds); it is
informational and nothing reads it back. No model artifact is persisted or tracked in git.

### Tuning discipline

All constants were chosen on **dev seeds 1000-1005**; the reported numbers use **seeds 0-9**
(`make_ml_scenarios.EVAL_SEEDS` / `DEV_SEEDS`; `python scripts/evaluate_anomaly.py --dev` shows the
dev run). What was searched on the dev seeds: statistical floor in {3.5, 4, 5} x calibration
quantile in {0.95..0.99} x margin {1.0, 1.2} (pooled F1 0.71-0.90), `EXCLUDE_Z` in {5, 6, 8} (0.90, 0.89,
0.85; 6 kept as the conservative value above the 4.0 detection threshold), `new_groups` scale in
{0.2, 0.1}; Isolation Forest: quantile {0.90, 0.95, 0.98} x margin {1.0, 1.2} x input signed z
vs unusual-direction only. The Isolation Forest got a fair chance: the first version calibrated
the threshold in-sample and reached at best F1 0.66 on the dev seeds; the out-of-fold calibration
lifted its best dev F1 to 0.74, unusual-direction input beat signed input (0.74 vs 0.68). Its
numbers did not approach the statistical baseline's (about 0.89 on the dev seeds). The dev-seed figures in this
paragraph were measured with the generator before the `new_error_type` correction; no constant was retuned
afterwards, on any seeds.

## 3. Evaluation design

The generator (`scripts/make_ml_scenarios.py`) produces seeded LogEvent streams (60 s windows,
240 windows, about 40 INFO + 1 WARNING + 1.6 ERROR lines per window with a varying load factor;
INFO heartbeats carry cpu_pct / latency_ms / queue_depth; six known error types plus a rare new
one) and runs them through the real `analyze()` pipeline. Labels are per window. Incidents occur only in
the later half; the first half is the training period. Novel error types never *add* events: they rewrite the
message of events the background process already produced, so a novelty scenario has the healthy ERROR-count
distribution (`test_new_error_type_replaces_events_so_error_volume_is_unchanged`). An earlier version added
them on top of the background errors; that leaked volume as a signal and was corrected (see section 6).

| scenario | group | what happens | ERROR-count rule |
|---|---|---|---|
| healthy | main | nothing (false-positive control) | - |
| error_spike | main | ERROR volume x8 for 12-18 windows (the easy case) | sees it |
| traffic_drop | main | traffic and errors fall to 10 % (silent failure; ERROR count goes *down*) | blind |
| latency_rise | main | latency_ms +40 %, queue_depth +50 %, log levels unchanged | blind |
| new_error_type | main | 3 new error types **replace** ~40 % of the window's existing ERROR events (per-window ERROR count unchanged); a type only shows up in its first window if that window has at least one ERROR | blind |
| busy_healthy | main | **trap**: 2.2x traffic for 25 windows, extra lines are all INFO | - |
| natural_bursts | main | **trap**: short x5 error bursts in train *and* test, no incident | - |
| noisy_mixed | main | bursts + busy period + traffic drop + error spike + latency rise | partly |
| slow_ramp | stress | ERROR volume ramps x1 to x5 over 60 windows (labelled from the first ramp window) | partly |
| early_incident | stress | error spike at windows 15-35 (inside the training period) | - |
| short_log | stress | 36 windows only | - |
| tiny_log | stress | 20 windows (baseline would be 10) | - |
| incident_at_start | stress | error spike at windows 0-12 | - |
| incident_fills_baseline | stress | error spike over windows 0-70 = 58 % of the training period | - |

The pooled tables use the `main` scenarios, later half only, one value per seed, then mean +/- sample
std over 10 seeds. Definitions:

* precision / recall / F1 / FPR: per window.
* incident recall: share of labelled incidents with at least one flagged window inside them.
* delay: windows from the incident's first window to the first flagged window (detected incidents only).
* false episode: a run of consecutive flagged windows that touches no labelled window.
  **FA/incident** = false episodes per labelled incident; **episode prec.** = share of episodes that touch an incident.
* ROC-AUC / PR-AUC of the continuous score, pooled per seed (the pooled version mixes scores
  of separate reports, which are only approximately comparable; the per-scenario table below
  is exact). "best F1 (oracle threshold)" is the best F1 any threshold on that score could
  reach using the labels, i.e. the ranking quality independent of calibration.

Sanity gates in the script (non-zero exit on failure): the ERROR-count rule and every single
feature are not perfect separators; no training window lies in the later period and every main
incident starts after the training range; replacing the whole later period with something
different does not change the baseline, the threshold or any training-period score (leakage);
generation and detection are bit-for-bit repeatable; severity uses more than 2 levels and is
not monotone in the score; `AUTO_METHOD` equals the decision measured below.

## 4. Results (seeds 0-9)

### Main scenarios, pooled per seed, later period only (mean ± std over 10 seeds)

| candidate | precision | recall | F1 | FPR/window | incident recall | delay (win) | FA/incident | episode prec. | ROC-AUC | PR-AUC | best F1 (oracle threshold) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| statistical | 0.890 ± 0.034 | 0.930 ± 0.020 | 0.909 ± 0.021 | 0.015 ± 0.005 | 1.000 ± 0.000 | 0.14 ± 0.22 | 1.614 ± 0.551 | 0.502 ± 0.101 | 0.976 ± 0.007 | 0.894 ± 0.026 | 0.917 ± 0.020 |
| isolation_forest | 0.634 ± 0.062 | 0.832 ± 0.040 | 0.719 ± 0.053 | 0.063 ± 0.015 | 0.900 ± 0.069 | 0.23 ± 0.17 | 6.771 ± 1.400 | 0.221 ± 0.016 | 0.942 ± 0.011 | 0.717 ± 0.063 | 0.732 ± 0.053 |
| error_count_rule | 0.717 ± 0.045 | 0.228 ± 0.019 | 0.346 ± 0.025 | 0.012 ± 0.003 | 0.371 ± 0.074 | 2.02 ± 2.20 | 1.357 ± 0.280 | 0.242 ± 0.084 | 0.491 ± 0.033 | 0.317 ± 0.024 | 0.361 ± 0.029 |

### Single features cannot separate these scenarios (pooled main scenarios, later period)

| feature | ROC-AUC mean ± std | best-possible F1 of a threshold tuned on the labels |
|---|---|---|
| error_count | 0.524 ± 0.023 | 0.361 ± 0.029 |
| event_rate | 0.600 ± 0.022 | 0.474 ± 0.032 |
| error_ratio | 0.538 ± 0.038 | 0.408 ± 0.039 |
| new_groups | 0.516 ± 0.004 | 0.199 ± 0.012 |
| metric.cpu_pct | 0.531 ± 0.018 | 0.240 ± 0.018 |
| metric.latency_ms | 0.678 ± 0.045 | 0.601 ± 0.040 |
| metric.queue_depth | 0.679 ± 0.025 | 0.577 ± 0.048 |

(In this table each feature may use whichever direction separates better, which is why `event_rate` and the metrics read above 0.5; the detector uses the directions listed in section 2.)
The ERROR-count rule reaches F1 0.35 and misses every traffic drop (0/10), every new-error-type incident (0/10)
and 9 of 10 latency rises: the scenarios are not solvable by log level. Contrast with the upstream project, where
single raw features reached ROC-AUC 1.0.

### Per scenario (mean over seeds; later period unless noted)

| scenario | candidate | incidents detected | window recall | delay (win) | FP windows/seed | FP episodes/seed | ROC-AUC | PR-AUC |
|---|---|---|---|---|---|---|---|---|
| healthy | statistical | - | - | n/a | 0.60 | 0.60 | n/a | n/a |
| healthy | isolation_forest | - | - | n/a | 7.90 | 7.20 | n/a | n/a |
| healthy | error_count_rule | - | - | n/a | 1.00 | 1.00 | n/a | n/a |
| error_spike | statistical | 10/10 | 0.99 ± 0.02 | 0.0 ± 0.0 | 1.00 | 0.90 | 1.00 ± 0.00 | 0.99 ± 0.03 |
| error_spike | isolation_forest | 10/10 | 1.00 ± 0.00 | 0.0 ± 0.0 | 6.80 | 6.30 | 0.98 ± 0.02 | 0.86 ± 0.17 |
| error_spike | error_count_rule | 10/10 | 0.99 ± 0.03 | 0.1 ± 0.3 | 0.60 | 0.60 | 1.00 ± 0.00 | 1.00 ± 0.00 |
| traffic_drop | statistical | 10/10 | 0.97 ± 0.04 | 0.1 ± 0.3 | 0.80 | 0.80 | 1.00 ± 0.01 | 0.96 ± 0.07 |
| traffic_drop | isolation_forest | 10/10 | 0.80 ± 0.14 | 0.3 ± 0.5 | 6.10 | 5.50 | 0.96 ± 0.03 | 0.85 ± 0.11 |
| traffic_drop | error_count_rule | 0/10 | 0.00 ± 0.00 | n/a | 0.40 | 0.40 | 0.15 ± 0.04 | 0.13 ± 0.02 |
| latency_rise | statistical | 10/10 | 0.93 ± 0.05 | 0.0 ± 0.0 | 1.30 | 1.30 | 0.99 ± 0.01 | 0.93 ± 0.07 |
| latency_rise | isolation_forest | 10/10 | 0.81 ± 0.19 | 0.2 ± 0.4 | 6.80 | 6.00 | 0.96 ± 0.03 | 0.78 ± 0.13 |
| latency_rise | error_count_rule | 1/10 | 0.01 ± 0.02 | 18.0 ± 0.0 | 1.50 | 1.50 | 0.46 ± 0.06 | 0.20 ± 0.04 |
| new_error_type | statistical | 10/10 | 0.37 ± 0.11 | 0.8 ± 1.6 | 1.00 | 0.90 | 0.67 ± 0.12 | 0.41 ± 0.14 |
| new_error_type | isolation_forest | 3/10 | 0.05 ± 0.08 | 2.3 ± 1.5 | 6.20 | 5.80 | 0.47 ± 0.13 | 0.07 ± 0.03 |
| new_error_type | error_count_rule | 0/10 | 0.00 ± 0.00 | n/a | 0.50 | 0.50 | 0.46 ± 0.07 | 0.06 ± 0.02 |
| busy_healthy | statistical | - | - | n/a | 1.30 | 1.30 | n/a | n/a |
| busy_healthy | isolation_forest | - | - | n/a | 5.70 | 5.10 | n/a | n/a |
| busy_healthy | error_count_rule | - | - | n/a | 1.40 | 1.40 | n/a | n/a |
| natural_bursts | statistical | - | - | n/a | 4.00 | 3.50 | n/a | n/a |
| natural_bursts | isolation_forest | - | - | n/a | 8.70 | 7.90 | n/a | n/a |
| natural_bursts | error_count_rule | - | - | n/a | 2.70 | 2.30 | n/a | n/a |
| noisy_mixed | statistical | 30/30 | 0.96 ± 0.03 | 0.0 ± 0.1 | 2.70 | 2.00 | 0.99 ± 0.01 | 0.96 ± 0.03 |
| noisy_mixed | isolation_forest | 30/30 | 0.91 ± 0.05 | 0.1 ± 0.3 | 5.20 | 3.60 | 0.97 ± 0.02 | 0.93 ± 0.04 |
| noisy_mixed | error_count_rule | 15/30 | 0.22 ± 0.04 | 2.1 ± 2.6 | 1.90 | 1.80 | 0.47 ± 0.03 | 0.53 ± 0.03 |
| slow_ramp | statistical | 10/10 | 0.34 ± 0.07 | 14.7 ± 5.9 | 0.60 | 0.60 | 0.79 ± 0.03 | 0.83 ± 0.04 |
| slow_ramp | isolation_forest | 10/10 | 0.54 ± 0.12 | 4.4 ± 4.1 | 4.00 | 3.60 | 0.79 ± 0.02 | 0.82 ± 0.03 |
| slow_ramp | error_count_rule | 10/10 | 0.38 ± 0.10 | 15.9 ± 6.1 | 0.30 | 0.30 | 0.86 ± 0.03 | 0.86 ± 0.03 |
| early_incident | statistical | 10/10 | 0.93 ± 0.10 | 0.0 ± 0.0 | 1.60 | 1.40 | 1.00 ± 0.00 | 0.96 ± 0.03 |
| early_incident | isolation_forest | 10/10 | 0.97 ± 0.05 | 0.0 ± 0.0 | 9.60 | 9.20 | 0.99 ± 0.00 | 0.91 ± 0.08 |
| early_incident | error_count_rule | 10/10 | 0.11 ± 0.05 | 8.6 ± 4.0 | 0.00 | 0.00 | 1.00 ± 0.00 | 0.99 ± 0.01 |
| short_log | statistical | 10/10 | 0.97 ± 0.07 | 0.0 ± 0.0 | 0.60 | 0.30 | 0.99 ± 0.03 | 0.98 ± 0.05 |
| short_log | isolation_forest | 10/10 | 0.73 ± 0.29 | 0.5 ± 0.8 | 1.70 | 1.10 | 0.91 ± 0.10 | 0.81 ± 0.17 |
| short_log | error_count_rule | 10/10 | 0.98 ± 0.05 | 0.0 ± 0.0 | 0.20 | 0.20 | 1.00 ± 0.01 | 0.99 ± 0.02 |
| tiny_log | statistical | 0/10 | 0.00 ± 0.00 | n/a | 0.00 | 0.00 | 0.50 ± 0.00 | 0.40 ± 0.00 |
| tiny_log | isolation_forest | 0/10 | 0.00 ± 0.00 | n/a | 0.00 | 0.00 | 0.50 ± 0.00 | 0.40 ± 0.00 |
| tiny_log | error_count_rule | 10/10 | 0.97 ± 0.08 | 0.1 ± 0.3 | 0.30 | 0.30 | 1.00 ± 0.00 | 1.00 ± 0.00 |
| incident_at_start | statistical | 10/10 | 0.96 ± 0.08 | 0.1 ± 0.3 | 1.40 | 1.40 | 1.00 ± 0.00 | 0.94 ± 0.09 |
| incident_at_start | isolation_forest | 10/10 | 0.97 ± 0.11 | 0.1 ± 0.3 | 10.40 | 10.20 | 0.99 ± 0.01 | 0.88 ± 0.12 |
| incident_at_start | error_count_rule | 10/10 | 0.40 ± 0.08 | 0.9 ± 0.7 | 0.00 | 0.00 | 1.00 ± 0.00 | 0.99 ± 0.01 |
| incident_fills_baseline | statistical | 1/10 | 0.00 ± 0.00 | 45.0 ± 0.0 | 0.80 | 0.80 | 0.44 ± 0.05 | 0.28 ± 0.04 |
| incident_fills_baseline | isolation_forest | 10/10 | 0.07 ± 0.03 | 13.3 ± 16.5 | 4.40 | 4.30 | 0.58 ± 0.05 | 0.38 ± 0.04 |
| incident_fills_baseline | error_count_rule | 1/10 | 0.00 ± 0.00 | 18.0 ± 0.0 | 0.00 | 0.00 | 1.00 ± 0.00 | 1.00 ± 0.00 |

The three scenarios whose incident lies in the training period (`early_incident`,
`incident_at_start`, `incident_fills_baseline`) are scored on *all* windows
(`FP windows` there counts every flagged unlabelled window, including natural noise in the training half).
`new_error_type` window recall is capped at 0.5 by construction: only the first window of each
of the three new types shows novelty (3 of 6 labelled windows).
For `tiny_log` the AUC rows are meaningless (the detector refuses to score, so every score is 0).

### Incidents inside the training period (baseline fitted on the first half)

| scenario | candidate | incident-window recall | windows excluded from baseline | later-period false windows |
|---|---|---|---|---|
| early_incident | statistical | 0.93 ± 0.10 | 17.2 ± 2.3 | 0.50 ± 0.53 |
| early_incident | isolation_forest | 0.97 ± 0.05 | 17.2 ± 2.3 | 5.70 ± 2.06 |
| incident_at_start | statistical | 0.96 ± 0.08 | 9.8 ± 1.8 | 0.80 ± 1.32 |
| incident_at_start | isolation_forest | 0.97 ± 0.11 | 9.8 ± 1.8 | 6.50 ± 3.54 |
| incident_fills_baseline | statistical | 0.00 ± 0.00 | 0.1 ± 0.3 | 0.60 ± 1.07 |
| incident_fills_baseline | isolation_forest | 0.07 ± 0.03 | 0.1 ± 0.3 | 3.00 ± 2.58 |

### Severity of episodes (evaluated period), split by whether the episode touches a labelled incident

| candidate | outcome | low | medium | high | critical |
|---|---|---|---|---|---|
| statistical | true incident | 67 | 77 | 68 | 5 |
| statistical | false alarm | 81 | 39 | 1 | 0 |
| isolation_forest | true incident | 82 | 81 | 94 | 5 |
| isolation_forest | false alarm | 411 | 106 | 1 | 0 |

### False-alarm windows of the statistical baseline (main scenarios, later period, all 10 seeds)

* top contributing feature: error_ratio 52, error_count 33, new_groups 28, metric.latency_ms 7, metric.queue_depth 5, metric.cpu_pct 2
* classification: spike 95, new_error_type 25, sustained_shift 5, quiet_period 2

### Generated parser/integration fixture (not a validation)

`tests/fixtures/telemetry.csv` is produced by `scripts/make_fixtures.py`; it exists to test parsers and the timeline
(10:00-12:00; heartbeats for an hour, then 300 `timeout calling inventory after <N>ms` errors from 11:00).
With 60 s windows, parsed and analyzed through the real pipeline, the statistical detector flags 60 of the 60
windows from 11:00 on (plus 1 window before), as one `sustained_shift` episode, severity `critical` (peak error
ratio 71 %, 300 errors above baseline, 60 min, ongoing, 1 new group, latency_ms shifted about 35x), top features
`error_ratio`, `metric.latency_ms`, `metric.queue_depth`. Pinned in `tests/test_anomaly_fixture.py` as an
integration check that metric means flow from parsed CSV into the detector. It is one step-change case with
the answer built in; it says nothing about performance on real logs.

## 5. Decision

**Default (`AUTO_METHOD`) = `statistical`.** Rule in the script: Isolation Forest becomes the default
only if its mean pooled F1 beats the baseline's by more than the larger std over seeds. Measured:
F1 0.719 (IF) vs 0.909 (statistical), difference -0.190 against a margin of 0.053. The script exits
non-zero if the constant disagrees with this rule. Note that this gate evaluates whichever seeds are being run
(the reported seeds 0-9 by default), so it checks consistency of the constant with the documented decision; it does
not by itself prove an independent, development-seeds-only model selection. The dev-seed vs evaluation-seed
split applies to constant tuning only.

Why Isolation Forest loses here, as measured: (a) ranking quality alone is worse (ROC-AUC 0.942 vs 0.976,
PR-AUC 0.717 vs 0.894, best-threshold F1 0.732 vs 0.917), so this is not just calibration; (b) its false
positive rate is 4.2x higher (0.063 vs 0.015 per window; 6.8 vs 1.6 false episodes per incident), including on
the traps; (c) it depends on a random seed and is slower (about 0.25 s per run vs about 2 ms), and
it needs a second dependency. It was clearly better in one place: the slow ramp (window recall 0.54 vs 0.34,
first flag after 4.4 vs 14.7 windows), at the cost of the false alarms above; on the step-change scenarios its
window recall is equal or within noise of the statistical one. The statistical rule additionally explains itself
(which feature, how many sigma), which the forest does not.
Likely reasons (not isolated by an experiment): with ~60 training windows and 4-7 features a forest has
little to learn that the per-feature deviations do not already say, and its scores are not tied to units the
way a z-score is. The conclusion is conditional on this data: do not read it as "IF is bad in general".

## 6. Known failure cases (all measured above)

* **Slow ramps.** `slow_ramp`: the statistical window recall is 0.34 and the first flag comes 14.7 windows after
  the ramp starts (the ERROR-count rule is similar, 15.9). Each window deviates a little from a global baseline; only
  the late, steep part crosses the threshold. EWMA / trend detection is not implemented. Isolation Forest does
  better here (4.4 windows).
* **Incident that fills the baseline.** `incident_fills_baseline` (58 % of the training period): the median
  breakdown point is exceeded, nothing is excluded, the incident is learned as normal. Statistical: 1/10 incidents,
  window recall 0.00. Not detectable by this design; `train_fraction` and a healthy first period are a precondition.
* **Mild incident windows stay in the baseline.** The guard drops only windows beyond 6 robust sigma; windows of an
  early incident at 4-6 sigma are flagged but still used for fitting (e.g. 9 of 20 incident windows for
  `early_incident` seed 1). Contamination is reduced, not eliminated.
* **Incident at the very start** is handled (recall 0.96, ~10 windows excluded), but novelty (`new_groups`) is
  ignored for the first 10 % of the training candidates, so a brand-new error type during log start-up is invisible.
* **Short logs.** `short_log` (36 windows, 18 for training) works for the statistical method (0.97 recall) but
  Isolation Forest drops to 0.73 window recall. `tiny_log` (20 windows): both refuse (0/10 detected,
  note "too few windows to train"); a naive rule with a 10-window baseline "detects" 10/10 there, but that
  rule is not trustworthy and has no guard.
* **New error type at low volume, volume-neutral.** After the generator fix (novel types replace existing error
  events instead of adding to them) this is much harder, as expected. Statistical: all 10 incidents are flagged
  (delay 0.8 windows) but window recall is 0.37 and ROC-AUC 0.67; Isolation Forest detects only 3/10 incidents
  (window recall 0.05, ROC-AUC 0.47, i.e. no better than chance); the ERROR-count rule 0/10. Before the fix, with
  extra error volume leaking into the incident, the statistical numbers were window recall 0.40 / ROC-AUC 0.76 and
  Isolation Forest detected 8/10: part of that earlier result came from volume, not novelty. Only the first window
  of each new type has `new_groups > 0`; the following windows of a persisting low-volume error are invisible.
* **False alarms.** Half of the episodes the statistical method raises touch no incident (episode precision 0.50;
  1.6 false episodes per incident; 1.5 % of windows). They are mostly `error_ratio` / `error_count` blips
  (`natural_bursts`: 3.5 false episodes per run) and natural novelty (`new_groups`, 28 windows over 10 seeds).
  A healthy log gives 0.6 false windows per 120. Lowering them costs recall (dev-seed floor 5.0: F1 0.78).
* **Fragmented episodes.** Episodes are runs of consecutive flagged windows; an incident partly below the
  threshold splits into several short episodes, and short episodes earn little severity (67 of 217 true-incident
  episodes are `low`). Merging across small gaps was not added because the contract says "consecutive".
* **Isolation Forest** flags 6.3 % of healthy windows even with out-of-fold calibration, and a few windows of
  the healthy training hour of the generated telemetry fixture.

## 7. Limitations

* **No real-world validation is presented.** Every number above is measured on synthetic data written alongside
  the detector; the only other input is a generated fixture (`tests/fixtures/telemetry.csv`, made by
  `scripts/make_fixtures.py`) used as a parser/integration check. The scenarios encode my assumptions
  (Poisson-like noise, step-change incidents, independent metrics, one background load factor) and the statistical
  baseline is built from the features the generator varies, so these numbers are *optimistic* for real logs and must
  not be quoted as production accuracy.
* No seasonality: a daily cycle, deploy-time spikes or weekday patterns appear as deviations from the first half.
  The baseline is a single global median/MAD, so non-stationary logs will drift out of it.
* All evaluation uses 60 s windows. Other window sizes only change the noise floors; the severity thresholds
  (minutes, error volume) are absolute and were not validated at other sizes.
* Metric features depend on events carrying them; metrics are assumed to have unknown polarity (two-sided).
  Non-finite metric values are ignored everywhere (timeline aggregation, scoring, severity); means are computed
  without summing raw values, so finite values near the float limit cannot overflow.
* CPU (and other metrics) are independent of load in the generator; in real systems a healthy busy period raises
  them, which this detector would flag.
* Log-flood incidents of non-error lines (event rate up, nothing else) are not flagged by design.
* Labels are per window and stop exactly at the incident's end; a flag in the recovery tail counts as a false positive.
* Severity thresholds are not validated against real incident handling; they are explicit and explained, not "right".
* Findings are evidence of unusual windows with contributing features; they say nothing about root cause.

## 8. Reproduce

```
pip install -e ".[ml]"          # scikit-learn + numpy (statistical method needs neither)
python scripts/evaluate_anomaly.py              # seeds 0-9, ~75 s, exit 1 if a gate fails
python scripts/evaluate_anomaly.py --dev        # tuning seeds
python scripts/make_ml_scenarios.py --seed 3    # summary of the generated scenarios
python -m pytest -q tests/test_anomaly_*.py
```
