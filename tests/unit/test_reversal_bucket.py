from __future__ import annotations

import pandas as pd

from cpdshadow.config import WalkforwardReversalBucketConfig
from cpdshadow.research.reversal import find_reversal_events


def test_reversal_threshold_uses_train_validation_only_and_applies_cooldown() -> None:
    dates = list(pd.bdate_range("2024-01-02", periods=34).date)
    features = []
    for idx, as_of_date in enumerate(dates):
        if idx < 20:
            score = idx / 20.0
        elif idx in {20, 21, 22, 23, 24, 25, 26, 32}:
            score = 100.0
        else:
            score = 0.0
        features.append({
            "feature_set_id": "features_v1",
            "as_of_date": as_of_date,
            "root": "ES",
            "series_id": "v1_back_ratio_settle",
            "cpd21_score": score,
            "cpd63_score": 0.0,
            "snapshot_id": "snapshot_test",
        })
    windows = pd.DataFrame([
        {
            "run_id": "wf_test",
            "fold_id": "fold_1",
            "train_start": dates[0],
            "train_end": dates[9],
            "val_start": dates[10],
            "val_end": dates[19],
            "oos_start": dates[20],
            "oos_end": dates[-1],
            "roots": "ES",
            "status": "completed",
        }
    ])

    events, thresholds = find_reversal_events(
        features_daily=pd.DataFrame(features),
        windows=windows,
        config=WalkforwardReversalBucketConfig(cooldown_days=5),
    )

    threshold = float(thresholds.loc[0, "threshold_21"])
    assert threshold < 1.0
    assert threshold < 100.0
    assert list(events["as_of_date"]) == [dates[20], dates[26], dates[32]]

