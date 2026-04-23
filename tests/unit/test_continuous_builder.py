from __future__ import annotations

from datetime import date
from math import isclose

import pandas as pd

from cpdshadow.continuous import (
    CONTINUOUS_DAILY_COLUMNS,
    ContinuousBuildError,
    ContinuousSeriesConfig,
    build_continuous_daily,
    validate_continuous_daily,
)


def _config(**overrides: object) -> ContinuousSeriesConfig:
    base = {
        "series_id": "v1_back_ratio_settle",
        "builder_version": "continuous_builder_v1",
        "strict_roll_ratio": True,
        "allow_close_fallback": True,
        "allowed_settle_statuses": ("final", "preliminary", "close_fallback"),
        "blocked_settle_statuses": ("missing",),
        "max_abs_daily_return_warning": 0.20,
        "max_abs_daily_return_error": 0.50,
    }
    base.update(overrides)
    return ContinuousSeriesConfig(**base)


def _daily(
    *,
    trade_date: date,
    root: str,
    raw_symbol: str,
    settle: float | None,
    settle_status: str = "final",
) -> dict[str, object]:
    return {
        "trade_date": trade_date,
        "root": root,
        "raw_symbol": raw_symbol,
        "dataset": "GLBX.MDP3",
        "instrument_id": 1,
        "open_price": settle,
        "high_price": settle,
        "low_price": settle,
        "close_price": settle,
        "settle_price": settle,
        "settle_status": settle_status,
        "volume": 100.0,
        "open_interest": 1000.0,
        "price_source": "statistics",
        "volume_source": "statistics",
        "available_at_utc": pd.Timestamp(f"{trade_date.isoformat()}T23:00:00Z"),
        "ingested_at_utc": pd.Timestamp(f"{trade_date.isoformat()}T23:30:00Z"),
        "quality_flags": [],
        "override_id": None,
        "snapshot_id": "snapshot_test",
    }


def _lead_row(
    *,
    as_of_date: date,
    root: str,
    lead_raw_symbol: str,
    roll_flag: bool = False,
    roll_event_id: str | None = None,
) -> dict[str, object]:
    return {
        "as_of_date": as_of_date,
        "root": root,
        "roll_policy_version": "volume3_hardroll_v1",
        "lead_raw_symbol": lead_raw_symbol,
        "next_raw_symbol": None,
        "prev_lead_raw_symbol": None,
        "roll_flag": roll_flag,
        "roll_event_id": roll_event_id,
        "days_to_expiry": 30,
        "front_volume_tminus1": 100.0,
        "next_volume_tminus1": 110.0,
        "confirmation_count": 0,
        "hard_roll_deadline": as_of_date,
        "selection_reason": "carry_forward",
        "builder_version": "roll_engine_v1",
        "snapshot_id": "snapshot_test",
    }


def _roll_event(
    *,
    root: str,
    roll_event_id: str,
    from_raw_symbol: str,
    to_raw_symbol: str,
    trigger_date: date,
    effective_date: date,
    ratio_adjustment: float | None,
    from_settle: float | None,
    to_settle: float | None,
) -> dict[str, object]:
    basis = None
    if from_settle is not None and to_settle is not None:
        basis = to_settle - from_settle
    return {
        "roll_event_id": roll_event_id,
        "root": root,
        "from_raw_symbol": from_raw_symbol,
        "to_raw_symbol": to_raw_symbol,
        "trigger_date": trigger_date,
        "effective_date": effective_date,
        "roll_reason": "volume_3day",
        "front_volume_tminus1": 100.0,
        "next_volume_tminus1": 120.0,
        "confirmation_count": 3,
        "from_settle": from_settle,
        "to_settle": to_settle,
        "ratio_adjustment": ratio_adjustment,
        "basis_at_roll": basis,
        "builder_version": "roll_engine_v1",
        "override_id": None,
    }


def _single_roll_inputs() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, list[date]]:
    dates = [
        date(2024, 1, 2),
        date(2024, 1, 3),
        date(2024, 1, 4),
        date(2024, 1, 5),
        date(2024, 1, 8),
    ]
    contracts_daily = pd.DataFrame([
        _daily(trade_date=dates[0], root="ES", raw_symbol="ESH4", settle=100.0),
        _daily(trade_date=dates[1], root="ES", raw_symbol="ESH4", settle=101.0),
        _daily(trade_date=dates[2], root="ES", raw_symbol="ESH4", settle=102.0),
        _daily(trade_date=dates[2], root="ES", raw_symbol="ESM4", settle=204.0),
        _daily(trade_date=dates[3], root="ES", raw_symbol="ESM4", settle=206.0),
        _daily(trade_date=dates[4], root="ES", raw_symbol="ESM4", settle=208.0),
    ])
    lead_map = pd.DataFrame([
        _lead_row(as_of_date=dates[0], root="ES", lead_raw_symbol="ESH4"),
        _lead_row(as_of_date=dates[1], root="ES", lead_raw_symbol="ESH4"),
        _lead_row(as_of_date=dates[2], root="ES", lead_raw_symbol="ESH4"),
        _lead_row(as_of_date=dates[3], root="ES", lead_raw_symbol="ESM4", roll_flag=True, roll_event_id="roll_es_1"),
        _lead_row(as_of_date=dates[4], root="ES", lead_raw_symbol="ESM4"),
    ])
    roll_events = pd.DataFrame([
        _roll_event(
            root="ES",
            roll_event_id="roll_es_1",
            from_raw_symbol="ESH4",
            to_raw_symbol="ESM4",
            trigger_date=dates[2],
            effective_date=dates[3],
            ratio_adjustment=2.0,
            from_settle=102.0,
            to_settle=204.0,
        ),
    ])
    return contracts_daily, lead_map, roll_events, dates


def test_no_roll_series_keeps_factor_one_and_raw_returns() -> None:
    dates = [date(2024, 1, day) for day in (2, 3, 4, 5, 8)]
    contracts_daily = pd.DataFrame([
        _daily(trade_date=dates[0], root="ES", raw_symbol="ESH4", settle=100.0),
        _daily(trade_date=dates[1], root="ES", raw_symbol="ESH4", settle=101.0),
        _daily(trade_date=dates[2], root="ES", raw_symbol="ESH4", settle=103.0),
        _daily(trade_date=dates[3], root="ES", raw_symbol="ESH4", settle=102.0),
        _daily(trade_date=dates[4], root="ES", raw_symbol="ESH4", settle=104.0),
    ])
    lead_map = pd.DataFrame([
        _lead_row(as_of_date=trade_date, root="ES", lead_raw_symbol="ESH4")
        for trade_date in dates
    ])
    result = build_continuous_daily(
        contracts_daily=contracts_daily,
        lead_map=lead_map,
        roll_events=pd.DataFrame(),
        config=_config(),
        snapshot_id="snapshot_test",
    )

    assert result["adj_factor"].tolist() == [1.0] * len(dates)
    assert result["adj_settle_price"].tolist() == result["raw_settle_price"].tolist()
    assert pd.isna(result.iloc[0]["daily_return"])
    assert "warmup_first_row" in result.iloc[0]["quality_flags"]
    assert isclose(float(result.iloc[1]["daily_return"]), 101.0 / 100.0 - 1.0)
    assert isclose(float(result.iloc[2]["daily_return"]), 103.0 / 101.0 - 1.0)


def test_single_roll_back_ratio_adjustment_matches_canonical_example() -> None:
    contracts_daily, lead_map, roll_events, dates = _single_roll_inputs()
    result = build_continuous_daily(
        contracts_daily=contracts_daily,
        lead_map=lead_map,
        roll_events=roll_events,
        config=_config(),
        snapshot_id="snapshot_test",
    )

    factors = result.set_index("as_of_date")["adj_factor"].to_dict()
    assert factors[dates[0]] == 2.0
    assert factors[dates[1]] == 2.0
    assert factors[dates[2]] == 2.0
    assert factors[dates[3]] == 1.0
    assert factors[dates[4]] == 1.0
    assert isclose(
        float(result.loc[result["as_of_date"] == dates[2], "adj_settle_price"].item()),
        204.0,
    )
    assert isclose(
        float(result.loc[result["as_of_date"] == dates[3], "adj_settle_price"].item()),
        206.0,
    )
    assert isclose(
        float(result.loc[result["as_of_date"] == dates[3], "daily_return"].item()),
        206.0 / 204.0 - 1.0,
    )


def test_future_roll_outside_build_end_does_not_change_factors() -> None:
    contracts_daily, lead_map, roll_events, dates = _single_roll_inputs()
    truncated = build_continuous_daily(
        contracts_daily=contracts_daily,
        lead_map=lead_map,
        roll_events=roll_events,
        config=_config(),
        snapshot_id="snapshot_test",
        end_date=dates[1],
    )

    assert truncated["adj_factor"].tolist() == [1.0, 1.0]


def test_multiple_rolls_use_product_of_future_ratios() -> None:
    dates = [date(2024, 1, day) for day in (2, 3, 4, 5, 8, 9)]
    contracts_daily = pd.DataFrame([
        _daily(trade_date=dates[0], root="ES", raw_symbol="ESH4", settle=100.0),
        _daily(trade_date=dates[1], root="ES", raw_symbol="ESH4", settle=101.0),
        _daily(trade_date=dates[2], root="ES", raw_symbol="ESM4", settle=200.0),
        _daily(trade_date=dates[3], root="ES", raw_symbol="ESM4", settle=202.0),
        _daily(trade_date=dates[4], root="ES", raw_symbol="ESU4", settle=101.0),
        _daily(trade_date=dates[5], root="ES", raw_symbol="ESU4", settle=103.0),
    ])
    lead_map = pd.DataFrame([
        _lead_row(as_of_date=dates[0], root="ES", lead_raw_symbol="ESH4"),
        _lead_row(as_of_date=dates[1], root="ES", lead_raw_symbol="ESH4"),
        _lead_row(as_of_date=dates[2], root="ES", lead_raw_symbol="ESM4", roll_flag=True, roll_event_id="roll_1"),
        _lead_row(as_of_date=dates[3], root="ES", lead_raw_symbol="ESM4"),
        _lead_row(as_of_date=dates[4], root="ES", lead_raw_symbol="ESU4", roll_flag=True, roll_event_id="roll_2"),
        _lead_row(as_of_date=dates[5], root="ES", lead_raw_symbol="ESU4"),
    ])
    roll_events = pd.DataFrame([
        _roll_event(
            root="ES",
            roll_event_id="roll_1",
            from_raw_symbol="ESH4",
            to_raw_symbol="ESM4",
            trigger_date=dates[1],
            effective_date=dates[2],
            ratio_adjustment=2.0,
            from_settle=101.0,
            to_settle=202.0,
        ),
        _roll_event(
            root="ES",
            roll_event_id="roll_2",
            from_raw_symbol="ESM4",
            to_raw_symbol="ESU4",
            trigger_date=dates[3],
            effective_date=dates[4],
            ratio_adjustment=0.5,
            from_settle=202.0,
            to_settle=101.0,
        ),
    ])
    result = build_continuous_daily(
        contracts_daily=contracts_daily,
        lead_map=lead_map,
        roll_events=roll_events,
        config=_config(),
        snapshot_id="snapshot_test",
    )

    factors = result.set_index("as_of_date")["adj_factor"].to_dict()
    assert factors[dates[0]] == 1.0
    assert factors[dates[1]] == 1.0
    assert factors[dates[2]] == 0.5
    assert factors[dates[3]] == 0.5
    assert factors[dates[4]] == 1.0
    assert factors[dates[5]] == 1.0


def test_missing_raw_price_keeps_row_but_marks_unusable() -> None:
    dates = [date(2024, 1, 2), date(2024, 1, 3)]
    contracts_daily = pd.DataFrame([
        _daily(trade_date=dates[0], root="ES", raw_symbol="ESH4", settle=100.0),
    ])
    lead_map = pd.DataFrame([
        _lead_row(as_of_date=dates[0], root="ES", lead_raw_symbol="ESH4"),
        _lead_row(as_of_date=dates[1], root="ES", lead_raw_symbol="ESH4"),
    ])
    result = build_continuous_daily(
        contracts_daily=contracts_daily,
        lead_map=lead_map,
        roll_events=pd.DataFrame(),
        config=_config(),
        snapshot_id="snapshot_test",
    )

    missing_row = result.loc[result["as_of_date"] == dates[1]].iloc[0]
    assert pd.isna(missing_row["raw_settle_price"])
    assert pd.isna(missing_row["adj_settle_price"])
    assert pd.isna(missing_row["daily_return"])
    assert bool(missing_row["is_usable_for_signal"]) is False
    assert "missing_raw_price" in missing_row["quality_flags"]


def test_missing_roll_ratio_is_fatal_in_strict_mode() -> None:
    contracts_daily, lead_map, _, dates = _single_roll_inputs()
    bad_roll_events = pd.DataFrame([
        _roll_event(
            root="ES",
            roll_event_id="roll_es_bad",
            from_raw_symbol="ESH4",
            to_raw_symbol="ESM4",
            trigger_date=dates[2],
            effective_date=dates[3],
            ratio_adjustment=None,
            from_settle=None,
            to_settle=None,
        ),
    ])

    try:
        build_continuous_daily(
            contracts_daily=contracts_daily,
            lead_map=lead_map,
            roll_events=bad_roll_events,
            config=_config(strict_roll_ratio=True),
            snapshot_id="snapshot_test",
        )
    except ContinuousBuildError as exc:
        assert exc.code == "invalid_roll_ratio_in_strict_mode"
    else:
        raise AssertionError("expected ContinuousBuildError")


def test_close_fallback_rows_are_tagged_and_still_usable_when_allowed() -> None:
    dates = [date(2024, 1, 2), date(2024, 1, 3)]
    contracts_daily = pd.DataFrame([
        _daily(trade_date=dates[0], root="ES", raw_symbol="ESH4", settle=100.0),
        _daily(trade_date=dates[1], root="ES", raw_symbol="ESH4", settle=101.0, settle_status="close_fallback"),
    ])
    lead_map = pd.DataFrame([
        _lead_row(as_of_date=dates[0], root="ES", lead_raw_symbol="ESH4"),
        _lead_row(as_of_date=dates[1], root="ES", lead_raw_symbol="ESH4"),
    ])
    result = build_continuous_daily(
        contracts_daily=contracts_daily,
        lead_map=lead_map,
        roll_events=pd.DataFrame(),
        config=_config(allow_close_fallback=True),
        snapshot_id="snapshot_test",
    )

    row = result.loc[result["as_of_date"] == dates[1]].iloc[0]
    assert bool(row["is_usable_for_signal"]) is True
    assert "close_fallback" in row["quality_flags"]


def test_validate_continuous_daily_catches_duplicate_primary_keys() -> None:
    continuous_daily = pd.DataFrame([
        {
            "series_id": "v1_back_ratio_settle",
            "as_of_date": date(2024, 1, 2),
            "root": "ES",
            "lead_raw_symbol": "ESH4",
            "raw_settle_price": 100.0,
            "adj_settle_price": 100.0,
            "adj_factor": 1.0,
            "daily_return": None,
            "settle_status": "final",
            "roll_flag": False,
            "roll_event_id": None,
            "is_usable_for_signal": False,
            "quality_flags": ["warmup_first_row"],
            "builder_version": "continuous_builder_v1",
            "snapshot_id": "snapshot_test",
        },
        {
            "series_id": "v1_back_ratio_settle",
            "as_of_date": date(2024, 1, 2),
            "root": "ES",
            "lead_raw_symbol": "ESH4",
            "raw_settle_price": 100.0,
            "adj_settle_price": 100.0,
            "adj_factor": 1.0,
            "daily_return": None,
            "settle_status": "final",
            "roll_flag": False,
            "roll_event_id": None,
            "is_usable_for_signal": False,
            "quality_flags": ["warmup_first_row"],
            "builder_version": "continuous_builder_v1",
            "snapshot_id": "snapshot_test",
        },
    ], columns=CONTINUOUS_DAILY_COLUMNS)
    lead_map = pd.DataFrame([
        _lead_row(as_of_date=date(2024, 1, 2), root="ES", lead_raw_symbol="ESH4"),
    ])
    report = validate_continuous_daily(
        continuous_daily=continuous_daily,
        lead_map=lead_map,
        roll_events=pd.DataFrame(),
        contracts_daily=pd.DataFrame(),
        config=_config(),
    )

    assert "duplicate_primary_keys" in {issue.code for issue in report.issues}


def test_validate_continuous_daily_emits_large_return_warning_and_error() -> None:
    lead_map = pd.DataFrame([
        _lead_row(as_of_date=date(2024, 1, 2), root="ES", lead_raw_symbol="ESH4"),
        _lead_row(as_of_date=date(2024, 1, 3), root="ES", lead_raw_symbol="ESH4"),
        _lead_row(as_of_date=date(2024, 1, 4), root="ES", lead_raw_symbol="ESH4"),
    ])
    continuous_daily = pd.DataFrame([
        {
            "series_id": "v1_back_ratio_settle",
            "as_of_date": date(2024, 1, 2),
            "root": "ES",
            "lead_raw_symbol": "ESH4",
            "raw_settle_price": 100.0,
            "adj_settle_price": 100.0,
            "adj_factor": 1.0,
            "daily_return": None,
            "settle_status": "final",
            "roll_flag": False,
            "roll_event_id": None,
            "is_usable_for_signal": False,
            "quality_flags": ["warmup_first_row"],
            "builder_version": "continuous_builder_v1",
            "snapshot_id": "snapshot_test",
        },
        {
            "series_id": "v1_back_ratio_settle",
            "as_of_date": date(2024, 1, 3),
            "root": "ES",
            "lead_raw_symbol": "ESH4",
            "raw_settle_price": 130.0,
            "adj_settle_price": 130.0,
            "adj_factor": 1.0,
            "daily_return": 0.30,
            "settle_status": "final",
            "roll_flag": False,
            "roll_event_id": None,
            "is_usable_for_signal": True,
            "quality_flags": [],
            "builder_version": "continuous_builder_v1",
            "snapshot_id": "snapshot_test",
        },
        {
            "series_id": "v1_back_ratio_settle",
            "as_of_date": date(2024, 1, 4),
            "root": "ES",
            "lead_raw_symbol": "ESH4",
            "raw_settle_price": 210.0,
            "adj_settle_price": 210.0,
            "adj_factor": 1.0,
            "daily_return": 0.60,
            "settle_status": "final",
            "roll_flag": False,
            "roll_event_id": None,
            "is_usable_for_signal": True,
            "quality_flags": [],
            "builder_version": "continuous_builder_v1",
            "snapshot_id": "snapshot_test",
        },
    ], columns=CONTINUOUS_DAILY_COLUMNS)

    report = validate_continuous_daily(
        continuous_daily=continuous_daily,
        lead_map=lead_map,
        roll_events=pd.DataFrame(),
        contracts_daily=pd.DataFrame(),
        config=_config(max_abs_daily_return_warning=0.20, max_abs_daily_return_error=0.50),
    )

    codes = {issue.code for issue in report.issues}
    assert "daily_return_over_warning_threshold" in codes
    assert "daily_return_over_error_threshold" in codes


def test_roll_effective_row_keeps_roll_flag_and_event_id() -> None:
    contracts_daily, lead_map, roll_events, dates = _single_roll_inputs()
    result = build_continuous_daily(
        contracts_daily=contracts_daily,
        lead_map=lead_map,
        roll_events=roll_events,
        config=_config(),
        snapshot_id="snapshot_test",
    )

    effective_row = result.loc[result["as_of_date"] == dates[3]].iloc[0]
    assert bool(effective_row["roll_flag"]) is True
    assert effective_row["roll_event_id"] == "roll_es_1"
    non_roll_rows = result.loc[result["as_of_date"] != dates[3]]
    assert not non_roll_rows["roll_flag"].any()
