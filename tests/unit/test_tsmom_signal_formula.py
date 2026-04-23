from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import pandas as pd

from cpdshadow.config import load_yaml
from cpdshadow.signals import SignalBuildRequest
from cpdshadow.strategies.tsmom import TsmomSignalStrategy


def _request() -> SignalBuildRequest:
    return SignalBuildRequest(
        run_id="infer_tsmom_2024",
        strategy_id="tsmom",
        model_id="tsmom_v1",
        feature_set_id="features_v1",
        snapshot_id="snapshot_test",
        start_date=date(2024, 1, 2),
        end_date=date(2024, 1, 10),
        roots=("ES",),
        created_at_utc=datetime(2026, 4, 20, tzinfo=UTC),
    )


def _strategy() -> TsmomSignalStrategy:
    cfg = load_yaml(Path("config/settings.base.yml"))
    return TsmomSignalStrategy(
        signals_config=cfg.signals,
        strategy_config=cfg.strategies.tsmom,
    )


def _feature_row(
    *,
    as_of_date: date,
    root: str,
    ret_21: float | None,
    ret_63: float | None,
    ret_252: float | None,
    is_complete: bool = True,
    warmup_status: str = "ok",
    feature_hash: str | None = "hash",
) -> dict[str, object]:
    return {
        "feature_set_id": "features_v1",
        "as_of_date": as_of_date,
        "root": root,
        "series_id": "v1_back_ratio_settle",
        "ret_1": 0.1,
        "ret_21": ret_21,
        "ret_63": ret_63,
        "ret_126": 0.2,
        "ret_252": ret_252,
        "macd_8_24": 0.3,
        "macd_16_48": 0.4,
        "macd_32_96": 0.5,
        "cpd21_score": 0.1,
        "cpd21_age": 0.2,
        "cpd63_score": 0.3,
        "cpd63_age": 0.4,
        "vol_20_60": 1.0,
        "vol_60_252": 1.0,
        "annualized_vol_60": 0.2,
        "is_complete": is_complete,
        "warmup_status": warmup_status,
        "feature_hash": feature_hash,
        "builder_version": "features_builder_v1",
        "snapshot_id": "snapshot_test",
    }


def test_tsmom_formula_matches_examples_exactly() -> None:
    strategy = _strategy()
    request = _request()
    features = pd.DataFrame(
        [
            _feature_row(
                as_of_date=date(2024, 1, 2),
                root="ES",
                ret_21=1.2,
                ret_63=0.4,
                ret_252=2.0,
                feature_hash="h1",
            ),
            _feature_row(
                as_of_date=date(2024, 1, 3),
                root="ES",
                ret_21=1.2,
                ret_63=-0.4,
                ret_252=2.0,
                feature_hash="h2",
            ),
            _feature_row(
                as_of_date=date(2024, 1, 4),
                root="ES",
                ret_21=-1.2,
                ret_63=-0.4,
                ret_252=2.0,
                feature_hash="h3",
            ),
            _feature_row(
                as_of_date=date(2024, 1, 5),
                root="ES",
                ret_21=-1.2,
                ret_63=-0.4,
                ret_252=-2.0,
                feature_hash="h4",
            ),
            _feature_row(
                as_of_date=date(2024, 1, 8),
                root="ES",
                ret_21=0.0,
                ret_63=1.0,
                ret_252=-1.0,
                feature_hash="h5",
            ),
        ]
    )

    result = strategy.build_signals(features, request)

    assert result.signals["signal_raw"].tolist() == [1.0, 1.0 / 3.0, -1.0 / 3.0, -1.0, 0.0]
    assert result.signals["signal_clipped"].tolist() == [1.0, 1.0 / 3.0, -1.0 / 3.0, -1.0, 0.0]
    assert result.signals["is_valid"].tolist() == [True, True, True, True, True]


def test_missing_required_feature_invalidates_row_without_partial_horizons() -> None:
    strategy = _strategy()
    request = _request()
    features = pd.DataFrame(
        [
            _feature_row(
                as_of_date=date(2024, 1, 2), root="ES", ret_21=1.0, ret_63=1.0, ret_252=None
            ),
        ]
    )

    result = strategy.build_signals(features, request)
    row = result.signals.iloc[0]

    assert bool(row["is_valid"]) is False
    assert row["invalid_reason"] == "missing_required_feature"
    assert pd.isna(row["signal_raw"])
    assert pd.isna(row["signal_clipped"])


def test_warmup_incomplete_and_nonfinite_rows_invalidate() -> None:
    strategy = _strategy()
    request = _request()
    features = pd.DataFrame(
        [
            _feature_row(
                as_of_date=date(2024, 1, 2),
                root="ES",
                ret_21=1.0,
                ret_63=1.0,
                ret_252=1.0,
                warmup_status="warmup",
            ),
            _feature_row(
                as_of_date=date(2024, 1, 3),
                root="ES",
                ret_21=1.0,
                ret_63=1.0,
                ret_252=1.0,
                is_complete=False,
            ),
            _feature_row(
                as_of_date=date(2024, 1, 4), root="ES", ret_21=float("inf"), ret_63=1.0, ret_252=1.0
            ),
        ]
    )

    result = strategy.build_signals(features, request)

    assert result.signals["invalid_reason"].tolist() == [
        "warmup_not_ok",
        "feature_incomplete",
        "nonfinite_required_feature",
    ]


def test_valid_rows_carry_feature_hash_and_input_order_does_not_matter() -> None:
    strategy = _strategy()
    request = _request()
    features = pd.DataFrame(
        [
            _feature_row(
                as_of_date=date(2024, 1, 3),
                root="ES",
                ret_21=1.0,
                ret_63=-1.0,
                ret_252=1.0,
                feature_hash="hash_b",
            ),
            _feature_row(
                as_of_date=date(2024, 1, 2),
                root="ES",
                ret_21=-1.0,
                ret_63=-1.0,
                ret_252=-1.0,
                feature_hash="hash_a",
            ),
        ]
    )

    forward = strategy.build_signals(features, request).signals
    reverse = strategy.build_signals(features.iloc[::-1].reset_index(drop=True), request).signals

    pd.testing.assert_frame_equal(forward, reverse, check_dtype=False)
    assert forward["feature_hash"].tolist() == ["hash_a", "hash_b"]


def test_future_feature_change_does_not_change_earlier_signal() -> None:
    strategy = _strategy()
    request = _request()
    base_features = pd.DataFrame(
        [
            _feature_row(
                as_of_date=date(2024, 1, 2),
                root="ES",
                ret_21=1.0,
                ret_63=1.0,
                ret_252=1.0,
                feature_hash="hash_a",
            ),
            _feature_row(
                as_of_date=date(2024, 1, 3),
                root="ES",
                ret_21=-1.0,
                ret_63=-1.0,
                ret_252=-1.0,
                feature_hash="hash_b",
            ),
        ]
    )
    modified_features = base_features.copy()
    modified_features.loc[1, "ret_21"] = 1.0
    modified_features.loc[1, "ret_63"] = 1.0
    modified_features.loc[1, "ret_252"] = 1.0

    base = strategy.build_signals(base_features, request).signals
    modified = strategy.build_signals(modified_features, request).signals

    pd.testing.assert_frame_equal(
        base.loc[base["as_of_date"] == date(2024, 1, 2)].reset_index(drop=True),
        modified.loc[modified["as_of_date"] == date(2024, 1, 2)].reset_index(drop=True),
        check_dtype=False,
    )
