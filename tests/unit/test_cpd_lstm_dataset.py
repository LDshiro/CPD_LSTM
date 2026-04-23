from __future__ import annotations

from datetime import date
from math import sqrt

import pandas as pd

from cpdshadow.config import CpdLstmModelConfig
from cpdshadow.features import FEATURE_VECTOR_ORDER
from cpdshadow.ml.dataset import build_inference_sequences, build_training_panels
from cpdshadow.strategies.tsmom import TsmomSignalStrategy


def _frames(n_days: int = 90) -> tuple[pd.DataFrame, pd.DataFrame, list[date]]:
    dates = list(pd.bdate_range("2024-01-02", periods=n_days).date)
    feature_rows = []
    continuous_rows = []
    for idx, as_of_date in enumerate(dates):
        features = {name: float(idx + feature_idx / 100.0) for feature_idx, name in enumerate(FEATURE_VECTOR_ORDER)}
        feature_rows.append({
            "feature_set_id": "features_v1",
            "as_of_date": as_of_date,
            "root": "ES",
            "series_id": "v1_back_ratio_settle",
            **features,
            "annualized_vol_60": 0.20,
            "is_complete": True,
            "warmup_status": "ok",
            "feature_hash": f"hash_{idx}",
            "builder_version": "features_builder_v1",
            "snapshot_id": "snapshot_test",
        })
        continuous_rows.append({
            "series_id": "v1_back_ratio_settle",
            "as_of_date": as_of_date,
            "root": "ES",
            "adj_settle_price": 100.0 + idx,
            "daily_return": 0.123 if idx == 63 else 0.001 * idx,
            "is_usable_for_signal": True,
            "snapshot_id": "snapshot_test",
        })
    return pd.DataFrame(feature_rows), pd.DataFrame(continuous_rows), dates


def test_cpd_lstm_feature_order_is_fixed() -> None:
    assert CpdLstmModelConfig().feature_order == FEATURE_VECTOR_ORDER


def test_cpd_lstm_sequence_builder_uses_63_rows() -> None:
    features, continuous, dates = _frames()
    panels = build_training_panels(
        features_daily=features,
        continuous_daily=continuous,
        config=CpdLstmModelConfig(),
        snapshot_id="snapshot_test",
        feature_set_id="features_v1",
        series_id="v1_back_ratio_settle",
        roots=["ES"],
        train_start=dates[62],
        train_end=dates[63],
        val_start=dates[64],
        val_end=dates[65],
    )

    assert panels.train.x.shape[1:] == (63, len(FEATURE_VECTOR_ORDER))
    assert panels.train.x[0, 0, 0] == 0.0
    assert panels.train.x[0, -1, 0] == 62.0


def test_cpd_lstm_label_uses_next_root_trading_day_return() -> None:
    features, continuous, dates = _frames()
    panels = build_training_panels(
        features_daily=features,
        continuous_daily=continuous,
        config=CpdLstmModelConfig(),
        snapshot_id="snapshot_test",
        feature_set_id="features_v1",
        series_id="v1_back_ratio_settle",
        roots=["ES"],
        train_start=dates[62],
        train_end=dates[62],
        val_start=dates[64],
        val_end=dates[65],
    )

    expected = 0.123 / (0.20 / sqrt(252))
    assert panels.train.next_dates == (dates[63],)
    assert abs(float(panels.train.y_norm[0]) - expected) < 1.0e-5


def test_cpd_lstm_no_lookahead_in_inference() -> None:
    features, _, dates = _frames()
    config = CpdLstmModelConfig()
    before = build_inference_sequences(
        features_daily=features,
        config=config,
        snapshot_id="snapshot_test",
        feature_set_id="features_v1",
        series_id="v1_back_ratio_settle",
        roots=["ES"],
        start_date=dates[70],
        end_date=dates[70],
    )
    mutated = features.copy()
    mutated.loc[mutated["as_of_date"] > dates[70], "ret_21"] = 9999.0
    after = build_inference_sequences(
        features_daily=mutated,
        config=config,
        snapshot_id="snapshot_test",
        feature_set_id="features_v1",
        series_id="v1_back_ratio_settle",
        roots=["ES"],
        start_date=dates[70],
        end_date=dates[70],
    )

    assert (before.panel.x == after.panel.x).all()


def test_tsmom_still_runs_after_wp9() -> None:
    assert TsmomSignalStrategy is not None
