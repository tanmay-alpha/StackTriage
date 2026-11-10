"""Reproduce the findings recorded in docs/foundation-audit.md.

Run with the *upstream* checkout's interpreter (it needs upstream's pinned deps):

    <upstream>/.venv/Scripts/python.exe scripts/audit_upstream.py <upstream-dir>

Nothing is written inside StackTriage; upstream's data/ and models/ get generated
files, which upstream already gitignores.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

up = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(up))
py = sys.executable

print("== 1. generate data + train (upstream's own pipeline)")
subprocess.run([py, "data/generate_data.py"], cwd=up, check=True)
subprocess.run([py, "-m", "src.train"], cwd=up, check=True)

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from sklearn.metrics import roc_auc_score  # noqa: E402

from src import config  # noqa: E402
from src.model import LogAnomalyDetector  # noqa: E402

test = pd.read_csv(config.TEST_CSV)
y = test[config.TARGET].values

print("\n== 2. is the synthetic task trivially separable?")
print("ROC-AUC of ONE raw feature, no model (1.0 = perfect separation):")
for col in config.NUMERIC_FEATURES:
    print(f"  {col:20s} {roc_auc_score(y, test[col]):.4f}")
lvl = test["log_level"].isin(["ERROR", "CRITICAL", "FATAL"]).astype(int)
print(f"  log_level in ERROR/CRITICAL/FATAL as a rule: AUC={roc_auc_score(y, lvl):.4f}")
for col in ["cpu_usage", "network_latency_ms", "failed_login_count"]:
    print(
        f"  {col}: normal max={test.loc[y == 0, col].max()}  anomaly min={test.loc[y == 1, col].min()}"
        f"  -> ranges overlap: {test.loc[y == 0, col].max() >= test.loc[y == 1, col].min()}"
    )
print("  failed_login_count>=5 alone as a rule: "
      f"precision/recall = "
      f"{((test.failed_login_count >= 5) & (y == 1)).sum() / max((test.failed_login_count >= 5).sum(), 1):.3f}/"
      f"{((test.failed_login_count >= 5) & (y == 1)).sum() / y.sum():.3f}")

print("\n== 3. severity thresholds (api/app.py: >=0.20 CRITICAL, >=0.10 HIGH, >=0.05 MEDIUM)")
det = LogAnomalyDetector.load()
scores = det.anomaly_score(test)
print(f"score range over test set: min={scores.min():.3f} max={scores.max():.3f}")
sev = np.where(scores >= 0.20, "CRITICAL", np.where(scores >= 0.10, "HIGH", np.where(scores >= 0.05, "MEDIUM", "LOW")))
print("severity counts for NORMAL rows:", dict(zip(*np.unique(sev[y == 0], return_counts=True))))
print("severity counts for ANOMALY rows:", dict(zip(*np.unique(sev[y == 1], return_counts=True))))

print("\n== 4. ordinary application log ingestion")
print("upstream accepts only CSV rows with 10 hard-coded telemetry columns; a plain log line:")
line = "2024-03-05 12:00:01,123 ERROR app.db: connection refused"
try:
    from api.app import LogRecord

    LogRecord.model_validate({"message": line})
except Exception as e:  # noqa: BLE001
    print("  rejected:", type(e).__name__, str(e).splitlines()[0])

print("\n== 5. unseen-category behaviour (OneHotEncoder handle_unknown=ignore)")
rec = {c: test[c].iloc[0] for c in config.NUMERIC_FEATURES + config.CATEGORICAL_FEATURES}
rec["event_type"] = "totally_new_event"
rec["device_model"] = "never_seen_host"
print("  score with unseen categories:", det.anomaly_score(pd.DataFrame([rec]))[0])
