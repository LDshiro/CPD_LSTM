from __future__ import annotations

from datetime import date

import pandas as pd

from cpdshadow.rolls import (
    RollBuildError,
    build_lead_map_for_root,
    filter_outright_contracts,
    validate_lead_map,
)


def _contract(
    *,
    root: str,
    raw_symbol: str,
    instrument_id: int,
    last_trade_date: date,
    first_trade_date: date,
    instrument_class: str = "F",
) -> dict[str, object]:
    return {
        "dataset": "GLBX.MDP3",
        "instrument_id": instrument_id,
        "raw_symbol": raw_symbol,
        "root": root,
        "exchange": "CME",
        "currency": "USD",
        "expiration_date": last_trade_date,
        "last_trade_date": last_trade_date,
        "first_trade_date": first_trade_date,
        "multiplier": 50.0,
        "tick_size": 0.25,
        "instrument_class": instrument_class,
        "valid_from_utc": pd.Timestamp("2024-01-01T00:00:00Z"),
        "valid_to_utc": None,
        "definition_hash": f"hash-{raw_symbol}",
        "ingested_at_utc": pd.Timestamp("2024-01-01T00:00:00Z"),
    }


def _daily(
    *,
    trade_date: date,
    root: str,
    raw_symbol: str,
    instrument_id: int,
    volume: float | None,
    settle: float | None,
    close: float | None = None,
    settle_status: str = "final",
) -> dict[str, object]:
    return {
        "trade_date": trade_date,
        "root": root,
        "raw_symbol": raw_symbol,
        "dataset": "GLBX.MDP3",
        "instrument_id": instrument_id,
        "open_price": settle,
        "high_price": settle,
        "low_price": settle,
        "close_price": close if close is not None else settle,
        "settle_price": settle,
        "settle_status": settle_status,
        "volume": volume,
        "open_interest": 1000.0,
        "price_source": "statistics",
        "volume_source": "statistics",
        "available_at_utc": pd.Timestamp(f"{trade_date.isoformat()}T23:00:00Z"),
        "ingested_at_utc": pd.Timestamp(f"{trade_date.isoformat()}T23:30:00Z"),
        "quality_flags": [],
        "override_id": None,
        "snapshot_id": "snapshot_test",
    }


def _base_roll_config(start: date, end: date) -> dict[str, object]:
    return {
        "policy_version": "volume3_hardroll_v1",
        "builder_version": "roll_engine_v1",
        "volume_confirmation_days": 3,
        "effective_lag_trading_days": 1,
        "volume_column": "volume",
        "volume_trigger_operator": "next_strictly_greater_than_front",
        "missing_volume_resets_confirmation": True,
        "hard_roll_anchor_preference": ["last_trade_date", "expiration_date"],
        "calendar_source": "root_contracts_daily_dates",
        "no_rollback": True,
        "start_date": start,
        "end_date": end,
    }


def _instrument_config(*, hard_roll_days: int, anchor: str = "last_trade_date") -> dict[str, object]:
    return {
        "root": "ES",
        "hard_roll_anchor": anchor,
        "hard_roll_business_days_before": hard_roll_days,
    }


def test_volume_roll_takes_effect_on_next_root_date_without_lookahead() -> None:
    dates = [
        date(2024, 1, 2),
        date(2024, 1, 3),
        date(2024, 1, 4),
        date(2024, 1, 5),
        date(2024, 1, 8),
    ]
    contract_master = pd.DataFrame([
        _contract(root="ES", raw_symbol="ESH4", instrument_id=1, last_trade_date=date(2024, 2, 15), first_trade_date=dates[0]),
        _contract(root="ES", raw_symbol="ESM4", instrument_id=2, last_trade_date=date(2024, 5, 15), first_trade_date=dates[0]),
        _contract(root="ES", raw_symbol="ESU4", instrument_id=3, last_trade_date=date(2024, 8, 15), first_trade_date=dates[0]),
    ])
    contracts_daily = pd.DataFrame([
        _daily(trade_date=dates[0], root="ES", raw_symbol="ESH4", instrument_id=1, volume=100, settle=100),
        _daily(trade_date=dates[0], root="ES", raw_symbol="ESM4", instrument_id=2, volume=110, settle=111),
        _daily(trade_date=dates[1], root="ES", raw_symbol="ESH4", instrument_id=1, volume=100, settle=101),
        _daily(trade_date=dates[1], root="ES", raw_symbol="ESM4", instrument_id=2, volume=120, settle=112),
        _daily(trade_date=dates[2], root="ES", raw_symbol="ESH4", instrument_id=1, volume=100, settle=102),
        _daily(trade_date=dates[2], root="ES", raw_symbol="ESM4", instrument_id=2, volume=130, settle=113),
        _daily(trade_date=dates[2], root="ES", raw_symbol="ESU4", instrument_id=3, volume=80, settle=120),
        _daily(trade_date=dates[3], root="ES", raw_symbol="ESH4", instrument_id=1, volume=90, settle=103),
        _daily(trade_date=dates[3], root="ES", raw_symbol="ESM4", instrument_id=2, volume=140, settle=114),
        _daily(trade_date=dates[3], root="ES", raw_symbol="ESU4", instrument_id=3, volume=85, settle=121),
        _daily(trade_date=dates[4], root="ES", raw_symbol="ESM4", instrument_id=2, volume=145, settle=115),
        _daily(trade_date=dates[4], root="ES", raw_symbol="ESU4", instrument_id=3, volume=90, settle=122),
    ])

    lead_map, roll_events = build_lead_map_for_root(
        root="ES",
        contract_master=contract_master,
        contracts_daily=contracts_daily,
        instrument_config=_instrument_config(hard_roll_days=10),
        roll_config=_base_roll_config(dates[0], dates[-1]),
        snapshot_id="snapshot_test",
    )

    assert lead_map.loc[lead_map["as_of_date"] == dates[2], "lead_raw_symbol"].item() == "ESH4"
    d4_row = lead_map.loc[lead_map["as_of_date"] == dates[3]].iloc[0]
    assert d4_row["lead_raw_symbol"] == "ESM4"
    assert bool(d4_row["roll_flag"]) is True
    assert d4_row["selection_reason"] == "volume_3day"

    assert len(roll_events) == 1
    event = roll_events.iloc[0]
    assert event["trigger_date"] == dates[2]
    assert event["effective_date"] == dates[3]
    assert event["from_raw_symbol"] == "ESH4"
    assert event["to_raw_symbol"] == "ESM4"
    assert event["basis_at_roll"] == 11.0
    assert abs(float(event["ratio_adjustment"]) - (113 / 102)) < 1e-12


def test_missing_volume_resets_confirmation_and_rolls_later() -> None:
    dates = [
        date(2024, 1, 2),
        date(2024, 1, 3),
        date(2024, 1, 4),
        date(2024, 1, 5),
        date(2024, 1, 8),
        date(2024, 1, 9),
        date(2024, 1, 10),
    ]
    contract_master = pd.DataFrame([
        _contract(root="ES", raw_symbol="ESH4", instrument_id=1, last_trade_date=date(2024, 2, 15), first_trade_date=dates[0]),
        _contract(root="ES", raw_symbol="ESM4", instrument_id=2, last_trade_date=date(2024, 5, 15), first_trade_date=dates[0]),
    ])
    next_volumes = [110, 120, None, 130, 140, 150, 160]
    contracts_daily = pd.DataFrame([
        row
        for idx, trade_date in enumerate(dates)
        for row in (
            _daily(trade_date=trade_date, root="ES", raw_symbol="ESH4", instrument_id=1, volume=100, settle=100 + idx),
            _daily(trade_date=trade_date, root="ES", raw_symbol="ESM4", instrument_id=2, volume=next_volumes[idx], settle=110 + idx),
        )
    ])

    lead_map, roll_events = build_lead_map_for_root(
        root="ES",
        contract_master=contract_master,
        contracts_daily=contracts_daily,
        instrument_config=_instrument_config(hard_roll_days=10),
        roll_config=_base_roll_config(dates[0], dates[-1]),
        snapshot_id="snapshot_test",
    )

    assert lead_map.loc[lead_map["as_of_date"] == dates[5], "lead_raw_symbol"].item() == "ESH4"
    assert lead_map.loc[lead_map["as_of_date"] == dates[6], "lead_raw_symbol"].item() == "ESM4"
    assert roll_events.iloc[0]["trigger_date"] == dates[5]
    assert roll_events.iloc[0]["effective_date"] == dates[6]


def test_hard_roll_wins_and_no_rollback_after_roll() -> None:
    dates = [
        date(2024, 1, 2),
        date(2024, 1, 3),
        date(2024, 1, 4),
        date(2024, 1, 5),
        date(2024, 1, 8),
    ]
    contract_master = pd.DataFrame([
        _contract(root="ES", raw_symbol="ESH4", instrument_id=1, last_trade_date=dates[3], first_trade_date=dates[0]),
        _contract(root="ES", raw_symbol="ESM4", instrument_id=2, last_trade_date=date(2024, 5, 15), first_trade_date=dates[0]),
        _contract(root="ES", raw_symbol="ESU4", instrument_id=3, last_trade_date=date(2024, 8, 15), first_trade_date=dates[0]),
    ])
    contracts_daily = pd.DataFrame([
        _daily(trade_date=dates[0], root="ES", raw_symbol="ESH4", instrument_id=1, volume=100, settle=100),
        _daily(trade_date=dates[0], root="ES", raw_symbol="ESM4", instrument_id=2, volume=110, settle=111),
        _daily(trade_date=dates[1], root="ES", raw_symbol="ESH4", instrument_id=1, volume=100, settle=101),
        _daily(trade_date=dates[1], root="ES", raw_symbol="ESM4", instrument_id=2, volume=120, settle=112),
        _daily(trade_date=dates[2], root="ES", raw_symbol="ESH4", instrument_id=1, volume=100, settle=102),
        _daily(trade_date=dates[2], root="ES", raw_symbol="ESM4", instrument_id=2, volume=90, settle=113),
        _daily(trade_date=dates[2], root="ES", raw_symbol="ESU4", instrument_id=3, volume=80, settle=120),
        _daily(trade_date=dates[3], root="ES", raw_symbol="ESH4", instrument_id=1, volume=200, settle=103),
        _daily(trade_date=dates[3], root="ES", raw_symbol="ESM4", instrument_id=2, volume=150, settle=114),
        _daily(trade_date=dates[3], root="ES", raw_symbol="ESU4", instrument_id=3, volume=90, settle=121),
        _daily(trade_date=dates[4], root="ES", raw_symbol="ESH4", instrument_id=1, volume=210, settle=104),
        _daily(trade_date=dates[4], root="ES", raw_symbol="ESM4", instrument_id=2, volume=140, settle=115),
        _daily(trade_date=dates[4], root="ES", raw_symbol="ESU4", instrument_id=3, volume=95, settle=122),
    ])

    lead_map, roll_events = build_lead_map_for_root(
        root="ES",
        contract_master=contract_master,
        contracts_daily=contracts_daily,
        instrument_config=_instrument_config(hard_roll_days=1),
        roll_config=_base_roll_config(dates[0], dates[-1]),
        snapshot_id="snapshot_test",
    )

    assert roll_events.iloc[0]["roll_reason"] == "hard_roll"
    assert lead_map.loc[lead_map["as_of_date"] == dates[2], "lead_raw_symbol"].item() == "ESM4"
    assert "ESH4" not in lead_map.loc[lead_map["as_of_date"] >= dates[2], "lead_raw_symbol"].tolist()


def test_initial_lead_skips_contract_at_or_past_deadline() -> None:
    dates = [date(2024, 1, 3), date(2024, 1, 4), date(2024, 1, 5)]
    contract_master = pd.DataFrame([
        _contract(root="ES", raw_symbol="ESH4", instrument_id=1, last_trade_date=dates[1], first_trade_date=dates[0]),
        _contract(root="ES", raw_symbol="ESM4", instrument_id=2, last_trade_date=date(2024, 5, 15), first_trade_date=dates[0]),
    ])
    contracts_daily = pd.DataFrame([
        _daily(trade_date=trade_date, root="ES", raw_symbol="ESH4", instrument_id=1, volume=100, settle=100 + idx)
        for idx, trade_date in enumerate(dates)
    ] + [
        _daily(trade_date=trade_date, root="ES", raw_symbol="ESM4", instrument_id=2, volume=90, settle=110 + idx)
        for idx, trade_date in enumerate(dates)
    ])

    lead_map, _ = build_lead_map_for_root(
        root="ES",
        contract_master=contract_master,
        contracts_daily=contracts_daily,
        instrument_config=_instrument_config(hard_roll_days=1),
        roll_config=_base_roll_config(dates[0], dates[-1]),
        snapshot_id="snapshot_test",
    )

    assert lead_map.iloc[0]["lead_raw_symbol"] == "ESM4"


def test_filter_outright_contracts_excludes_spreads_and_synthetics() -> None:
    contract_master = pd.DataFrame([
        _contract(root="ES", raw_symbol="ESH4", instrument_id=1, last_trade_date=date(2024, 2, 15), first_trade_date=date(2024, 1, 2)),
        _contract(root="ES", raw_symbol="ES.FUT", instrument_id=2, last_trade_date=date(2024, 2, 15), first_trade_date=date(2024, 1, 2)),
        _contract(root="ES", raw_symbol="ESH4-ESM4", instrument_id=3, last_trade_date=date(2024, 2, 15), first_trade_date=date(2024, 1, 2), instrument_class="SPREAD"),
    ])

    filtered = filter_outright_contracts(contract_master, "ES")
    assert filtered["raw_symbol"].tolist() == ["ESH4"]


def test_validate_lead_map_catches_non_monotonic_rollbacks_and_missing_refs() -> None:
    contract_master = pd.DataFrame([
        _contract(root="ES", raw_symbol="ESH4", instrument_id=1, last_trade_date=date(2024, 2, 15), first_trade_date=date(2024, 1, 2)),
        _contract(root="ES", raw_symbol="ESM4", instrument_id=2, last_trade_date=date(2024, 5, 15), first_trade_date=date(2024, 1, 2)),
    ])
    contracts_daily = pd.DataFrame([
        _daily(trade_date=date(2024, 1, 2), root="ES", raw_symbol="ESH4", instrument_id=1, volume=100, settle=100),
        _daily(trade_date=date(2024, 1, 2), root="ES", raw_symbol="ESM4", instrument_id=2, volume=110, settle=111),
        _daily(trade_date=date(2024, 1, 3), root="ES", raw_symbol="ESH4", instrument_id=1, volume=100, settle=101),
        _daily(trade_date=date(2024, 1, 3), root="ES", raw_symbol="ESM4", instrument_id=2, volume=110, settle=112),
        _daily(trade_date=date(2024, 1, 4), root="ES", raw_symbol="ESH4", instrument_id=1, volume=100, settle=102),
        _daily(trade_date=date(2024, 1, 4), root="ES", raw_symbol="ESM4", instrument_id=2, volume=110, settle=113),
    ])
    lead_map = pd.DataFrame([
        {
            "as_of_date": date(2024, 1, 2),
            "root": "ES",
            "roll_policy_version": "volume3_hardroll_v1",
            "lead_raw_symbol": "ESH4",
            "next_raw_symbol": "ESM4",
            "prev_lead_raw_symbol": None,
            "roll_flag": False,
            "roll_event_id": None,
            "days_to_expiry": 10,
            "front_volume_tminus1": None,
            "next_volume_tminus1": None,
            "confirmation_count": 0,
            "hard_roll_deadline": date(2024, 1, 10),
            "selection_reason": "carry_forward",
            "builder_version": "roll_engine_v1",
            "snapshot_id": "snapshot_test",
        },
        {
            "as_of_date": date(2024, 1, 3),
            "root": "ES",
            "roll_policy_version": "volume3_hardroll_v1",
            "lead_raw_symbol": "ESM4",
            "next_raw_symbol": "ESH4",
            "prev_lead_raw_symbol": "ESH4",
            "roll_flag": True,
            "roll_event_id": "roll_valid",
            "days_to_expiry": 9,
            "front_volume_tminus1": 100.0,
            "next_volume_tminus1": 110.0,
            "confirmation_count": 0,
            "hard_roll_deadline": date(2024, 1, 10),
            "selection_reason": "carry_forward",
            "builder_version": "roll_engine_v1",
            "snapshot_id": "snapshot_test",
        },
        {
            "as_of_date": date(2024, 1, 4),
            "root": "ES",
            "roll_policy_version": "volume3_hardroll_v1",
            "lead_raw_symbol": "ESH4",
            "next_raw_symbol": "ESH4",
            "prev_lead_raw_symbol": "ESM4",
            "roll_flag": True,
            "roll_event_id": "roll_missing",
            "days_to_expiry": 9,
            "front_volume_tminus1": 100.0,
            "next_volume_tminus1": 100.0,
            "confirmation_count": 0,
            "hard_roll_deadline": date(2024, 1, 10),
            "selection_reason": "carry_forward",
            "builder_version": "roll_engine_v1",
            "snapshot_id": "snapshot_test",
        },
    ])
    roll_events = pd.DataFrame([
        {
            "roll_event_id": "roll_valid",
            "root": "ES",
            "from_raw_symbol": "ESH4",
            "to_raw_symbol": "ESM4",
            "trigger_date": date(2024, 1, 2),
            "effective_date": date(2024, 1, 3),
            "roll_reason": "volume_3day",
            "front_volume_tminus1": 100.0,
            "next_volume_tminus1": 110.0,
            "confirmation_count": 3,
            "from_settle": 100.0,
            "to_settle": 111.0,
            "ratio_adjustment": 1.11,
            "basis_at_roll": 11.0,
            "builder_version": "roll_engine_v1",
            "override_id": None,
        },
    ])

    report = validate_lead_map(
        lead_map=lead_map,
        roll_events=roll_events,
        contract_master=contract_master,
        contracts_daily=contracts_daily,
    )

    codes = {issue.code for issue in report.issues}
    assert "non_monotonic_expiry_order" in codes
    assert "rollback_to_old_contract" in codes
    assert "next_raw_symbol_equals_lead_raw_symbol" in codes
    assert "missing_roll_event_referenced_by_lead_map" in codes


def test_build_is_deterministic_for_same_inputs() -> None:
    dates = [
        date(2024, 1, 2),
        date(2024, 1, 3),
        date(2024, 1, 4),
        date(2024, 1, 5),
    ]
    contract_master = pd.DataFrame([
        _contract(root="ES", raw_symbol="ESH4", instrument_id=1, last_trade_date=date(2024, 2, 15), first_trade_date=dates[0]),
        _contract(root="ES", raw_symbol="ESM4", instrument_id=2, last_trade_date=date(2024, 5, 15), first_trade_date=dates[0]),
    ])
    contracts_daily = pd.DataFrame([
        _daily(trade_date=dates[0], root="ES", raw_symbol="ESH4", instrument_id=1, volume=100, settle=100),
        _daily(trade_date=dates[0], root="ES", raw_symbol="ESM4", instrument_id=2, volume=110, settle=111),
        _daily(trade_date=dates[1], root="ES", raw_symbol="ESH4", instrument_id=1, volume=100, settle=101),
        _daily(trade_date=dates[1], root="ES", raw_symbol="ESM4", instrument_id=2, volume=120, settle=112),
        _daily(trade_date=dates[2], root="ES", raw_symbol="ESH4", instrument_id=1, volume=100, settle=102),
        _daily(trade_date=dates[2], root="ES", raw_symbol="ESM4", instrument_id=2, volume=130, settle=113),
        _daily(trade_date=dates[3], root="ES", raw_symbol="ESH4", instrument_id=1, volume=90, settle=103),
        _daily(trade_date=dates[3], root="ES", raw_symbol="ESM4", instrument_id=2, volume=140, settle=114),
    ])
    kwargs = {
        "root": "ES",
        "contract_master": contract_master,
        "contracts_daily": contracts_daily,
        "instrument_config": _instrument_config(hard_roll_days=10),
        "roll_config": _base_roll_config(dates[0], dates[-1]),
        "snapshot_id": "snapshot_test",
    }

    lead_map_one, roll_events_one = build_lead_map_for_root(**kwargs)
    lead_map_two, roll_events_two = build_lead_map_for_root(**kwargs)

    assert lead_map_one.to_dict(orient="records") == lead_map_two.to_dict(orient="records")
    assert roll_events_one.to_dict(orient="records") == roll_events_two.to_dict(orient="records")


def test_no_eligible_initial_lead_raises_structured_error() -> None:
    dates = [date(2024, 1, 5), date(2024, 1, 8)]
    contract_master = pd.DataFrame([
        _contract(root="ES", raw_symbol="ESH4", instrument_id=1, last_trade_date=date(2024, 1, 5), first_trade_date=date(2024, 1, 2)),
    ])
    contracts_daily = pd.DataFrame([
        _daily(trade_date=trade_date, root="ES", raw_symbol="ESH4", instrument_id=1, volume=100, settle=100)
        for trade_date in dates
    ])

    try:
        build_lead_map_for_root(
            root="ES",
            contract_master=contract_master,
            contracts_daily=contracts_daily,
            instrument_config=_instrument_config(hard_roll_days=1),
            roll_config=_base_roll_config(dates[0], dates[-1]),
            snapshot_id="snapshot_test",
        )
    except RollBuildError as exc:
        assert exc.code == "no_eligible_initial_lead"
    else:
        raise AssertionError("expected RollBuildError")
