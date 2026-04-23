from __future__ import annotations

from datetime import date

import pandas as pd

from cpdshadow.config import WalkforwardConfig
from cpdshadow.research.walkforward import plan_walkforward_windows


def _features(start: str = "2018-01-02", periods: int = 1250) -> pd.DataFrame:
    dates = list(pd.bdate_range(start, periods=periods).date)
    return pd.DataFrame(
        [
            {
                "feature_set_id": "features_v1",
                "as_of_date": as_of_date,
                "root": root,
                "series_id": "v1_back_ratio_settle",
                "snapshot_id": "snapshot_test",
            }
            for as_of_date in dates
            for root in ("ES", "NQ")
        ]
    )


def test_quarterly_walkforward_windows_are_stable_and_non_overlapping() -> None:
    windows = plan_walkforward_windows(
        features_daily=_features(),
        run_id="wf_test",
        roots=("ES", "NQ"),
        oos_start=date(2021, 1, 1),
        oos_end=date(2021, 6, 30),
        config=WalkforwardConfig(),
        train_years=2,
        val_years=1,
        min_train_days=200,
        min_val_days=100,
        min_oos_days=20,
    )

    assert list(windows["fold_id"]) == ["wf_wf_test_2021Q1", "wf_wf_test_2021Q2"]
    assert set(windows["status"]) == {"planned"}
    for _, row in windows.iterrows():
        assert row["train_end"] < row["val_start"] <= row["val_end"] < row["oos_start"]
        assert row["oos_start"] <= row["oos_end"]


def test_walkforward_plan_does_not_change_when_future_rows_are_mutated() -> None:
    base = _features()
    before = plan_walkforward_windows(
        features_daily=base,
        run_id="wf_test",
        roots=("ES",),
        oos_start=date(2021, 1, 1),
        oos_end=date(2021, 3, 31),
        config=WalkforwardConfig(),
        train_years=2,
        val_years=1,
        min_train_days=200,
        min_val_days=100,
        min_oos_days=20,
    ).drop(columns=["created_at_utc"])
    mutated = base.copy()
    mutated.loc[mutated["as_of_date"] > date(2021, 3, 31), "root"] = "ZZ"
    after = plan_walkforward_windows(
        features_daily=mutated,
        run_id="wf_test",
        roots=("ES",),
        oos_start=date(2021, 1, 1),
        oos_end=date(2021, 3, 31),
        config=WalkforwardConfig(),
        train_years=2,
        val_years=1,
        min_train_days=200,
        min_val_days=100,
        min_oos_days=20,
    ).drop(columns=["created_at_utc"])

    pd.testing.assert_frame_equal(before, after)

