from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

from cpdshadow.config import FeaturesConfig
from cpdshadow.cpd import build_cpd_daily_for_root, compute_two_sample_t_cpd


def _config() -> FeaturesConfig:
    return FeaturesConfig.model_validate({
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
    })


def test_stationary_window_produces_low_cpd_score() -> None:
    window = np.array([0.001 * np.sin(i / 3.0) for i in range(21)], dtype=float)
    result = compute_two_sample_t_cpd(
        window_values=window,
        window_days=21,
        min_segment_days=5,
        min_segment_fraction=0.25,
        epsilon=1.0e-12,
        score_min=0.0,
        score_max=1.0,
    )

    assert result.cpd_is_valid is True
    assert result.cpd_score is not None
    assert result.cpd_score < 0.20


def test_step_change_window_produces_higher_score_near_true_split() -> None:
    window = np.array([0.0] * 10 + [1.5] * 11, dtype=float)
    result = compute_two_sample_t_cpd(
        window_values=window,
        window_days=21,
        min_segment_days=5,
        min_segment_fraction=0.25,
        epsilon=1.0e-12,
        score_min=0.0,
        score_max=1.0,
    )

    assert result.cpd_is_valid is True
    assert result.cpd_score is not None
    assert result.cpd_score > 0.90
    assert result.cpd_location_index is not None
    assert abs(result.cpd_location_index - 10) <= 1


def test_recent_change_produces_lower_cpd_age_than_older_change() -> None:
    older = compute_two_sample_t_cpd(
        window_values=np.array([0.0] * 6 + [1.5] * 15, dtype=float),
        window_days=21,
        min_segment_days=5,
        min_segment_fraction=0.25,
        epsilon=1.0e-12,
        score_min=0.0,
        score_max=1.0,
    )
    recent = compute_two_sample_t_cpd(
        window_values=np.array([0.0] * 15 + [1.5] * 6, dtype=float),
        window_days=21,
        min_segment_days=5,
        min_segment_fraction=0.25,
        epsilon=1.0e-12,
        score_min=0.0,
        score_max=1.0,
    )

    assert older.cpd_is_valid is True
    assert recent.cpd_is_valid is True
    assert older.cpd_age_days is not None
    assert recent.cpd_age_days is not None
    assert recent.cpd_age_days < older.cpd_age_days


def test_insufficient_or_nonfinite_window_is_invalid() -> None:
    short = compute_two_sample_t_cpd(
        window_values=np.array([0.1] * 20, dtype=float),
        window_days=21,
        min_segment_days=5,
        min_segment_fraction=0.25,
        epsilon=1.0e-12,
        score_min=0.0,
        score_max=1.0,
    )
    nonfinite = compute_two_sample_t_cpd(
        window_values=np.array([0.0] * 10 + [np.nan] + [0.0] * 10, dtype=float),
        window_days=21,
        min_segment_days=5,
        min_segment_fraction=0.25,
        epsilon=1.0e-12,
        score_min=0.0,
        score_max=1.0,
    )

    assert short.cpd_is_valid is False
    assert nonfinite.cpd_is_valid is False


def test_tie_break_prefers_latest_split() -> None:
    flat_window = np.zeros(21, dtype=float)
    result = compute_two_sample_t_cpd(
        window_values=flat_window,
        window_days=21,
        min_segment_days=5,
        min_segment_fraction=0.25,
        epsilon=1.0e-12,
        score_min=0.0,
        score_max=1.0,
    )

    assert result.cpd_is_valid is True
    assert result.cpd_location_index == 16
    assert result.cpd_age_days == 5


def test_build_cpd_daily_for_root_emits_both_windows_for_requested_range() -> None:
    config = _config()
    dates = pd.bdate_range("2024-01-02", periods=70)
    z_t = np.array([0.0] * 35 + [1.0] * 35, dtype=float)
    source_df = pd.DataFrame({
        "as_of_date": dates.date,
        "root": ["ES"] * len(dates),
        "z_t": z_t,
    })

    cpd_daily = build_cpd_daily_for_root(
        root="ES",
        source_df=source_df,
        config=config,
        feature_set_id="features_v1",
        snapshot_id="snapshot_test",
        start_date=date(2024, 2, 1),
        end_date=date(2024, 4, 15),
    )

    assert set(cpd_daily["cpd_window_days"]) == {21, 63}
    assert not cpd_daily.duplicated(
        subset=["feature_set_id", "as_of_date", "root", "cpd_window_days"]
    ).any()
