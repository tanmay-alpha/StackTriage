"""Raw anomaly statistics. Both return one non-negative number per window, larger = more unusual.

`statistical`       maximum deviation (robust z, unusual direction only) over the features.
`isolation_forest`  scikit-learn IsolationForest, *refit from the training windows on every
                    call* (a few hundred milliseconds); nothing is ever loaded from disk.
                    scikit-learn is imported lazily so the statistical path needs no ML
                    dependency.
"""
from __future__ import annotations

import statistics
from collections.abc import Sequence

IF_TREES = 100  # scikit-learn default
IF_MAX_SAMPLES = 0.7  # each tree sees 70 % of the training windows (less in-sample memorisation)
IF_FOLDS = 3  # out-of-fold rescoring of the training windows for threshold calibration


def sklearn_available() -> bool:
    try:
        import numpy  # noqa: F401
        import sklearn  # noqa: F401
    except ImportError:
        return False
    return True


def statistical_raw(excess_rows: Sequence[Sequence[float]]) -> list[float]:
    return [max(row) if row else 0.0 for row in excess_rows]


def isolation_forest_raw(
    train_rows: Sequence[Sequence[float]], all_rows: Sequence[Sequence[float]], seed: int,
) -> tuple[list[float], list[float]]:
    """Return (raw scores of all rows, out-of-fold raw scores of the training rows).

    The forest that scores `all_rows` is fit on every training row. Training rows are
    re-scored out-of-fold (IF_FOLDS interleaved folds, each scored by a forest that never saw
    it) so the decision threshold is calibrated on scores not inflated by memorisation.
    raw = max(0, IF anomaly score - median out-of-fold training score).
    """
    import numpy as np
    from sklearn.ensemble import IsolationForest

    def make() -> IsolationForest:
        return IsolationForest(
            n_estimators=IF_TREES, max_samples=IF_MAX_SAMPLES, contamination="auto",
            random_state=seed, n_jobs=1,
        )

    x_train = np.asarray(train_rows, dtype=float)
    s_all = (-make().fit(x_train).score_samples(np.asarray(all_rows, dtype=float))).tolist()
    oof = np.empty(len(x_train))
    fold = np.arange(len(x_train)) % IF_FOLDS
    for k in range(IF_FOLDS):
        oof[fold == k] = -make().fit(x_train[fold != k]).score_samples(x_train[fold == k])
    base = statistics.median(oof.tolist())
    return [max(0.0, s - base) for s in s_all], [max(0.0, s - base) for s in oof.tolist()]
