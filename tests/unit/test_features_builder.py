from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

from cpdshadow.config import FeaturesConfig
from cpdshadow.features import (
    CPD_DAILY_COLUMNS,
    FEATURES_DAILY_COLUMNS,
    build_feature_outputs,
    validate_feature_outputs,
)


def _config(**overrides: object) -> FeaturesConfig:
    payload: dict[str, object] = {
        "feature_set_id": "features_v1",
        "builder_version": "features_builder_v1",
        "series_id": "v1_back_ratio_settle",
        "price_column": "adj_settle_price",
        "return_column": "daily_return",
        "annualization_factor": 252,
        "warmup_days": 252,
        "epsilon": 1.0e-12,
        "horizons": {"normalized_returns": [1, 21, 63, 126, 252]},
        "volatility": {
            "estimator": "ewm_std",
            "span_days": 60,
            "min_periods": 20,
            "ratio_pairs": [(20, 60), (60, 252)],
        },
        "macd": {
            "method": "log_price_ema_diff_zscore",
            "pairs": [(8, 24), (16, 48), (32, 96)],
            "zscore_span_days": 252,
            "zscore_min_periods": 63,
        },
        "cpd": {
            "builder_version": "cpd_builder_v1",
            "method": "two_sample_t_v1",
            "windows": [21, 63],
            "min_segment_days": 5,
            "min_segment_fraction": 0.25,
            "input_return": "vol_scaled_daily_return",
            "score_transform": "one_minus_exp_half_t2",
        },
        "clipping": {
            "normalized_return_abs_max": 20.0,
            "macd_abs_max": 20.0,
            "vol_ratio_min": 0.05,
            "vol_ratio_max": 20.0,
            "cpd_score_min": 0.0,
            "cpd_score_max": 1.0,
        },
    }
    payload.update(overrides)
    return FeaturesConfig.model_validate(payload)


def _make_continuous_daily(
    *,
    roots: tuple[str, ...] = ("ES",),
    periods: int = 360,
    start: str = "2023-01-02",
    snapshot_id: str = "snapshot_test",
    blocked: dict[str, set[int]] | None = None,
    bad_daily_return: bool = False,
) -> pd.DataFrame:
    blocked = blocked or {}
    dates = pd.bdate_range(start, periods=periods)
    rows: list[dict[str, object]] = []
    for root_index, root in enumerate(roots):
        price = 100.0 + 10.0 * root_index
        previous_price: float | None = None
        for idx, timestamp in enumerate(dates):
            growth = 0.0008 + 0.0004 * np.sin(idx / 11.0 + root_index)
            price *= 1.0 + growth
            daily_return = None if previous_price is None else price / previous_price - 1.0
            if bad_daily_return and idx == 280:
                daily_return = 0.25
            is_blocked = idx in blocked.get(root, set())
            rows.append({
                "series_id": "v1_back_ratio_settle",
                "as_of_date": timestamp.date(),
                "root": root,
                "lead_raw_symbol": f"{root}H4",
                "raw_settle_price": price,
                "adj_settle_price": price,
                "adj_factor": 1.0,
                "daily_return": daily_return,
                "settle_status": "final",
                "roll_flag": False,
                "roll_event_id": None,
                "is_usable_for_signal": not is_blocked,
                "quality_flags": ["blocked_test"] if is_blocked else [],
                "builder_version": "continuous_builder_v1",
                "snapshot_id": snapshot_id,
            })
            previous_price = price
    return pd.DataFrame(rows)


def test_no_future_leakage_keeps_feature_row_and_cpd_row_stable() -> None:
    config = _config()
    continuous_daily = _make_continuous_daily()
    start_date = date(2024, 1, 2)
    end_date = date(2024, 3, 29)
    cutoff = date(2024, 2, 15)

    base_cpd, base_features = build_feature_outputs(
        continuous_daily=continuous_daily,
        config=config,
        feature_set_id=config.feature_set_id,
        snapshot_id="snapshot_test",
        start_date=start_date,
        end_date=end_date,
    )

    modified = continuous_daily.copy()
    future_mask = pd.to_datetime(modified["as_of_date"]).dt.date > cutoff
    modified.loc[future_mask, "adj_settle_price"] = (
        pd.to_numeric(modified.loc[future_mask, "adj_settle_price"]) * 1.5
    )
    modified.loc[future_mask, "raw_settle_price"] = (
        pd.to_numeric(modified.loc[future_mask, "raw_settle_price"]) * 1.5
    )
    future_cpd, future_features = build_feature_outputs(
        continuous_daily=modified,
        config=config,
        feature_set_id=config.feature_set_id,
        snapshot_id="snapshot_test",
        start_date=start_date,
        end_date=end_date,
    )

    pd.testing.assert_frame_equal(
        base_features.loc[base_features["as_of_date"] == cutoff].reset_index(drop=True),
        future_features.loc[future_features["as_of_date"] == cutoff].reset_index(drop=True),
        check_dtype=False,
    )
    pd.testing.assert_frame_equal(
        base_cpd.loc[base_cpd["as_of_date"] == cutoff].reset_index(drop=True),
        future_cpd.loc[future_cpd["as_of_date"] == cutoff].reset_index(drop=True),
        check_dtype=False,
    )


def test_recomputed_returns_drive_features_and_only_validation_warns() -> None:
    config = _config()
    clean_source = _make_continuous_daily()
    bad_return_source = _make_continuous_daily(bad_daily_return=True)
    start_date = date(2024, 1, 2)
    end_date = date(2024, 3, 29)

    clean_cpd, clean_features = build_feature_outputs(
        continuous_daily=clean_source,
        config=config,
        feature_set_id=config.feature_set_id,
        snapshot_id="snapshot_test",
        start_date=start_date,
        end_date=end_date,
    )
    bad_cpd, bad_features = build_feature_outputs(
        continuous_daily=bad_return_source,
        config=config,
        feature_set_id=config.feature_set_id,
        snapshot_id="snapshot_test",
        start_date=start_date,
        end_date=end_date,
    )

    pd.testing.assert_frame_equal(clean_features, bad_features, check_dtype=False)
    pd.testing.assert_frame_equal(clean_cpd, bad_cpd, check_dtype=False)

    report = validate_feature_outputs(
        features_daily=bad_features,
        cpd_daily=bad_cpd,
        continuous_daily=bad_return_source,
        config=config,
    )
    codes = {issue.code for issue in report.issues}
    assert "return_validation_mismatch" in codes
    assert report.summary["return_validation_warning_count"] > 0


def test_warmup_and_blocked_quality_statuses_are_classified_correctly() -> None:
    config = _config()
    continuous_daily = _make_continuous_daily(blocked={"ES": {280}})
    start_date = date(2023, 1, 2)
    end_date = date(2024, 3, 29)

    _, features_daily = build_feature_outputs(
        continuous_daily=continuous_daily,
        config=config,
        feature_set_id=config.feature_set_id,
        snapshot_id="snapshot_test",
        start_date=start_date,
        end_date=end_date,
    )

    first_row = features_daily.iloc[0]
    assert first_row["warmup_status"] == "warmup"
    assert bool(first_row["is_complete"]) is False

    blocked_date = pd.to_datetime(continuous_daily.iloc[280]["as_of_date"]).date()
    blocked_row = features_daily.loc[features_daily["as_of_date"] == blocked_date].iloc[0]
    assert blocked_row["warmup_status"] == "blocked_quality"
    assert bool(blocked_row["is_complete"]) is False


def test_feature_outputs_are_deterministic_for_identical_inputs() -> None:
    config = _config()
    continuous_daily = _make_continuous_daily(roots=("ES", "NQ"))
    start_date = date(2024, 1, 2)
    end_date = date(2024, 3, 29)

    first_cpd, first_features = build_feature_outputs(
        continuous_daily=continuous_daily,
        config=config,
        feature_set_id=config.feature_set_id,
        snapshot_id="snapshot_test",
        start_date=start_date,
        end_date=end_date,
    )
    second_cpd, second_features = build_feature_outputs(
        continuous_daily=continuous_daily,
        config=config,
        feature_set_id=config.feature_set_id,
        snapshot_id="snapshot_test",
        start_date=start_date,
        end_date=end_date,
    )

    pd.testing.assert_frame_equal(first_cpd, second_cpd, check_dtype=False)
    pd.testing.assert_frame_equal(first_features, second_features, check_dtype=False)


def test_feature_output_schema_and_cpd_age_conventions_match_contract() -> None:
    config = _config()
    continuous_daily = _make_continuous_daily()
    start_date = date(2024, 1, 2)
    end_date = date(2024, 3, 29)

    cpd_daily, features_daily = build_feature_outputs(
        continuous_daily=continuous_daily,
        config=config,
        feature_set_id=config.feature_set_id,
        snapshot_id="snapshot_test",
        start_date=start_date,
        end_date=end_date,
    )

    assert list(cpd_daily.columns) == CPD_DAILY_COLUMNS
    assert list(features_daily.columns) == FEATURES_DAILY_COLUMNS
    assert set(cpd_daily["cpd_window_days"]) == {21, 63}

    valid_feature_ages = features_daily[["cpd21_age", "cpd63_age"]].stack().dropna()
    assert not valid_feature_ages.empty
    assert valid_feature_ages.between(0.0, 1.0).all()

    valid_cpd_ages = pd.to_numeric(cpd_daily.loc[cpd_daily["cpd_is_valid"], "cpd_age_days"], errors="coerce").dropna()
    assert not valid_cpd_ages.empty
    assert (valid_cpd_ages >= 0).all()
    assert (valid_cpd_ages > 1).any()
    assert np.allclose(valid_cpd_ages, np.floor(valid_cpd_ages))


def test_validate_feature_outputs_warns_when_clipping_is_frequent() -> None:
    config = _config(clipping={
        "normalized_return_abs_max": 0.01,
        "macd_abs_max": 0.01,
        "vol_ratio_min": 0.05,
        "vol_ratio_max": 1.05,
        "cpd_score_min": 0.0,
        "cpd_score_max": 0.50,
    })
    continuous_daily = _make_continuous_daily()
    start_date = date(2024, 1, 2)
    end_date = date(2024, 3, 29)

    cpd_daily, features_daily = build_feature_outputs(
        continuous_daily=continuous_daily,
        config=config,
        feature_set_id=config.feature_set_id,
        snapshot_id="snapshot_test",
        start_date=start_date,
        end_date=end_date,
    )
    report = validate_feature_outputs(
        features_daily=features_daily,
        cpd_daily=cpd_daily,
        continuous_daily=continuous_daily,
        config=config,
    )

    assert "frequent_feature_clipping" in {issue.code for issue in report.issues}
