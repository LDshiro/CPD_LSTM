from __future__ import annotations

from datetime import UTC, date, datetime

import pandas as pd

from cpdshadow.signals import SIGNALS_DAILY_COLUMNS, SignalBuildRequest, validate_signals_daily


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


def _signal_row(
    *,
    as_of_date: date,
    root: str,
    signal_raw: float | None = 1.0,
    signal_clipped: float | None = 1.0,
    is_valid: bool = True,
    invalid_reason: str | None = None,
    feature_hash: str | None = "hash",
) -> dict[str, object]:
    return {
        "run_id": "infer_tsmom_2024",
        "strategy_id": "tsmom",
        "model_id": "tsmom_v1",
        "as_of_date": as_of_date,
        "root": root,
        "signal_raw": signal_raw,
        "signal_clipped": signal_clipped,
        "is_valid": is_valid,
        "invalid_reason": invalid_reason,
        "feature_hash": feature_hash,
        "created_at_utc": datetime(2026, 4, 20, tzinfo=UTC),
    }


def _feature_row(*, as_of_date: date, root: str, feature_hash: str = "hash") -> dict[str, object]:
    return {
        "feature_set_id": "features_v1",
        "as_of_date": as_of_date,
        "root": root,
        "series_id": "v1_back_ratio_settle",
        "ret_1": 0.1,
        "ret_21": 1.0,
        "ret_63": 1.0,
        "ret_126": 0.1,
        "ret_252": 1.0,
        "macd_8_24": 0.1,
        "macd_16_48": 0.1,
        "macd_32_96": 0.1,
        "cpd21_score": 0.1,
        "cpd21_age": 0.1,
        "cpd63_score": 0.1,
        "cpd63_age": 0.1,
        "vol_20_60": 1.0,
        "vol_60_252": 1.0,
        "annualized_vol_60": 0.2,
        "is_complete": True,
        "warmup_status": "ok",
        "feature_hash": feature_hash,
        "builder_version": "features_builder_v1",
        "snapshot_id": "snapshot_test",
    }


def test_validate_signals_daily_accepts_valid_sorted_output() -> None:
    signals_daily = pd.DataFrame(
        [
            _signal_row(as_of_date=date(2024, 1, 2), root="ES", feature_hash="hash_a"),
            _signal_row(
                as_of_date=date(2024, 1, 3),
                root="ES",
                signal_raw=0.0,
                signal_clipped=0.0,
                feature_hash="hash_b",
            ),
        ],
        columns=SIGNALS_DAILY_COLUMNS,
    )
    features_daily = pd.DataFrame(
        [
            _feature_row(as_of_date=date(2024, 1, 2), root="ES", feature_hash="hash_a"),
            _feature_row(as_of_date=date(2024, 1, 3), root="ES", feature_hash="hash_b"),
        ]
    )

    report = validate_signals_daily(
        signals_daily=signals_daily,
        request=_request(),
        clip_min=-1.0,
        clip_max=1.0,
        sort_keys=("as_of_date", "root"),
        features_daily=features_daily,
    )

    assert report.has_errors is False


def test_duplicate_primary_keys_are_rejected() -> None:
    signals_daily = pd.DataFrame(
        [
            _signal_row(as_of_date=date(2024, 1, 2), root="ES"),
            _signal_row(as_of_date=date(2024, 1, 2), root="ES"),
        ],
        columns=SIGNALS_DAILY_COLUMNS,
    )

    report = validate_signals_daily(
        signals_daily=signals_daily,
        request=_request(),
        clip_min=-1.0,
        clip_max=1.0,
        sort_keys=("as_of_date", "root"),
    )

    assert "duplicate_primary_keys" in {issue.code for issue in report.issues}


def test_invalid_reason_and_clip_invariants_are_enforced() -> None:
    signals_daily = pd.DataFrame(
        [
            _signal_row(as_of_date=date(2024, 1, 2), root="ES", signal_clipped=1.2),
            _signal_row(
                as_of_date=date(2024, 1, 3),
                root="ES",
                is_valid=False,
                invalid_reason=None,
                signal_raw=None,
                signal_clipped=None,
            ),
            _signal_row(
                as_of_date=date(2024, 1, 4), root="ES", is_valid=True, invalid_reason="bad"
            ),
        ],
        columns=SIGNALS_DAILY_COLUMNS,
    )

    report = validate_signals_daily(
        signals_daily=signals_daily,
        request=_request(),
        clip_min=-1.0,
        clip_max=1.0,
        sort_keys=("as_of_date", "root"),
    )
    codes = {issue.code for issue in report.issues}

    assert "signal_clipped_out_of_bounds" in codes
    assert "invalid_rows_missing_reason" in codes
    assert "valid_rows_have_invalid_reason" in codes


def test_unsorted_rows_and_feature_hash_mismatch_are_rejected() -> None:
    signals_daily = pd.DataFrame(
        [
            _signal_row(as_of_date=date(2024, 1, 3), root="ES", feature_hash="hash_b"),
            _signal_row(as_of_date=date(2024, 1, 2), root="ES", feature_hash="hash_wrong"),
        ],
        columns=SIGNALS_DAILY_COLUMNS,
    )
    features_daily = pd.DataFrame(
        [
            _feature_row(as_of_date=date(2024, 1, 2), root="ES", feature_hash="hash_a"),
            _feature_row(as_of_date=date(2024, 1, 3), root="ES", feature_hash="hash_b"),
        ]
    )

    report = validate_signals_daily(
        signals_daily=signals_daily,
        request=_request(),
        clip_min=-1.0,
        clip_max=1.0,
        sort_keys=("as_of_date", "root"),
        features_daily=features_daily,
    )
    codes = {issue.code for issue in report.issues}

    assert "unsorted_output_rows" in codes
    assert "feature_hash_mismatch" in codes
