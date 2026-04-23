from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
import pytest

from cpdshadow.config import load_execution_boundary_yaml
from cpdshadow.order_intents import plan_order_intents


def test_simple_lead_rebalance() -> None:
    intents, events, summary = plan_order_intents(
        targets=_targets([_target(root="ES", strategy_id="cpd_lstm", lead_raw_symbol="ESU7", target_contracts=3)]),
        snapshot_positions=_positions([_position(root="ES", raw_symbol="ESU7", position_contracts=1)]),
        contract_master=_contract_master(["ESU7"]),
        config=_config(),
        created_at_utc=_created_at(),
    )

    assert summary["planned_intent_count"] == 1
    assert events == []
    assert intents[0].side == "buy"
    assert intents[0].quantity == 2
    assert intents[0].reason == "rebalance"


def test_flatten_to_zero() -> None:
    intents, _, _ = plan_order_intents(
        targets=_targets([_target(root="ES", strategy_id="cpd_lstm", lead_raw_symbol="ESU7", target_contracts=0)]),
        snapshot_positions=_positions([_position(root="ES", raw_symbol="ESU7", position_contracts=-2)]),
        contract_master=_contract_master(["ESU7"]),
        config=_config(),
        created_at_utc=_created_at(),
    )

    assert len(intents) == 1
    assert intents[0].side == "buy"
    assert intents[0].quantity == 2
    assert intents[0].reason == "flatten"


def test_same_size_roll_closes_old_contract_first() -> None:
    intents, _, _ = plan_order_intents(
        targets=_targets([_target(root="ES", strategy_id="cpd_lstm", lead_raw_symbol="ESU7", target_contracts=2)]),
        snapshot_positions=_positions([_position(root="ES", raw_symbol="ESM7", position_contracts=2)]),
        contract_master=_contract_master(["ESM7", "ESU7"]),
        config=_config(),
        created_at_utc=_created_at(),
    )

    assert [intent.raw_symbol for intent in intents] == ["ESM7", "ESU7"]
    assert [intent.side for intent in intents] == ["sell", "buy"]
    assert all(intent.reason == "roll" for intent in intents)


def test_roll_with_size_reduction() -> None:
    intents, _, _ = plan_order_intents(
        targets=_targets([_target(root="ES", strategy_id="cpd_lstm", lead_raw_symbol="ESU7", target_contracts=1)]),
        snapshot_positions=_positions([_position(root="ES", raw_symbol="ESM7", position_contracts=3)]),
        contract_master=_contract_master(["ESM7", "ESU7"]),
        config=_config(),
        created_at_utc=_created_at(),
    )

    assert [(intent.raw_symbol, intent.side, intent.quantity) for intent in intents] == [
        ("ESM7", "sell", 3),
        ("ESU7", "buy", 1),
    ]


def test_fallback_non_roll_uses_fallback_reason() -> None:
    intents, _, _ = plan_order_intents(
        targets=_targets([_target(root="NQ", strategy_id="tsmom", lead_raw_symbol="NQH7", target_contracts=-2, control_action="fallback_tsmom")]),
        snapshot_positions=_positions([]),
        contract_master=_contract_master(["NQH7"]),
        config=_config(),
        created_at_utc=_created_at(),
    )

    assert len(intents) == 1
    assert intents[0].side == "sell"
    assert intents[0].reason == "fallback"


def test_hold_emits_no_intents() -> None:
    intents, events, _ = plan_order_intents(
        targets=_targets([_target(root="ES", strategy_id="cpd_lstm", lead_raw_symbol="ESU7", target_contracts=2, control_action="hold", order_delta_contracts=1)]),
        snapshot_positions=_positions([_position(root="ES", raw_symbol="ESM7", position_contracts=2)]),
        contract_master=_contract_master(["ESM7", "ESU7"]),
        config=_config(),
        created_at_utc=_created_at(),
    )

    assert intents == []
    codes = {event.code for event in events}
    assert {"PLAN_HOLD_SUPPRESSED_DELTA", "PLAN_HOLD_OFF_LEAD_INVENTORY"} <= codes


def test_reduce_only_clips_attempted_increase() -> None:
    intents, events, _ = plan_order_intents(
        targets=_targets([_target(root="ES", strategy_id="cpd_lstm", lead_raw_symbol="ESU7", target_contracts=5, control_action="reduce_only")]),
        snapshot_positions=_positions([_position(root="ES", raw_symbol="ESU7", position_contracts=3)]),
        contract_master=_contract_master(["ESU7"]),
        config=_config(),
        created_at_utc=_created_at(),
    )

    assert intents == []
    assert any(event.code == "PLAN_REDUCE_ONLY_CLIPPED" for event in events)


def test_reduce_only_allows_equal_size_replacement_roll() -> None:
    intents, _, _ = plan_order_intents(
        targets=_targets([_target(root="ES", strategy_id="cpd_lstm", lead_raw_symbol="ESU7", target_contracts=5, control_action="reduce_only")]),
        snapshot_positions=_positions([_position(root="ES", raw_symbol="ESM7", position_contracts=3)]),
        contract_master=_contract_master(["ESM7", "ESU7"]),
        config=_config(),
        created_at_utc=_created_at(),
    )

    assert [(intent.raw_symbol, intent.side, intent.quantity, intent.reason) for intent in intents] == [
        ("ESM7", "sell", 3, "roll"),
        ("ESU7", "buy", 3, "roll"),
    ]


def test_mixed_sign_inventory_blocks_root() -> None:
    intents, events, summary = plan_order_intents(
        targets=_targets([_target(root="ES", strategy_id="cpd_lstm", lead_raw_symbol="ESU7", target_contracts=1)]),
        snapshot_positions=_positions(
            [
                _position(root="ES", raw_symbol="ESM7", position_contracts=1),
                _position(root="ES", raw_symbol="ESU7", position_contracts=-1),
            ]
        ),
        contract_master=_contract_master(["ESM7", "ESU7"]),
        config=_config(),
        created_at_utc=_created_at(),
    )

    assert intents == []
    assert summary["blocked_roots"] == ["ES"]
    assert any(event.code == "PLAN_MIXED_SIGN_INVENTORY" for event in events)


def test_duplicate_target_roots_fail() -> None:
    with pytest.raises(ValueError, match="duplicate target roots"):
        plan_order_intents(
            targets=_targets(
                [
                    _target(root="ES", strategy_id="cpd_lstm", lead_raw_symbol="ESU7", target_contracts=1),
                    _target(root="ES", strategy_id="cpd_lstm", lead_raw_symbol="ESU7", target_contracts=2),
                ]
            ),
            snapshot_positions=_positions([]),
            contract_master=_contract_master(["ESU7"]),
            config=_config(),
            created_at_utc=_created_at(),
        )


def test_order_intent_id_is_deterministic() -> None:
    kwargs = {
        "targets": _targets([_target(root="ES", strategy_id="cpd_lstm", lead_raw_symbol="ESU7", target_contracts=2)]),
        "snapshot_positions": _positions([_position(root="ES", raw_symbol="ESM7", position_contracts=2)]),
        "contract_master": _contract_master(["ESM7", "ESU7"]),
        "config": _config(),
        "created_at_utc": _created_at(),
    }
    first, _, _ = plan_order_intents(**kwargs)
    second, _, _ = plan_order_intents(**kwargs)

    assert [intent.order_intent_id for intent in first] == [intent.order_intent_id for intent in second]


def _config():
    return load_execution_boundary_yaml(Path("config/execution_boundary.yml"))


def _created_at() -> datetime:
    return datetime(2026, 4, 23, tzinfo=UTC)


def _targets(rows: list[dict[str, object]]) -> pd.DataFrame:
    return pd.DataFrame(rows)


def _positions(rows: list[dict[str, object]]) -> pd.DataFrame:
    return pd.DataFrame(
        rows
        or [
            {
                "position_snapshot_id": "snapshot_001",
                "execution_mode": "shadow",
                "raw_symbol": "DUMMY",
                "root": "DUMMY",
                "position_contracts": 0,
                "snapshot_time_utc": "2026-04-23T00:00:00Z",
            }
        ]
    )


def _contract_master(raw_symbols: list[str]) -> pd.DataFrame:
    rows = []
    for raw_symbol in raw_symbols:
        root = raw_symbol[:2] if raw_symbol.startswith("NQ") else raw_symbol[:2]
        rows.append({"root": "NQ" if raw_symbol.startswith("NQ") else "ES", "raw_symbol": raw_symbol})
    return pd.DataFrame(rows)


def _target(
    *,
    root: str,
    strategy_id: str,
    lead_raw_symbol: str,
    target_contracts: int,
    control_action: str = "run_cpd_lstm",
    order_delta_contracts: int | None = None,
) -> dict[str, object]:
    return {
        "run_id": "shadow_run_001",
        "strategy_id": strategy_id,
        "execution_mode": "shadow",
        "as_of_date": "2026-04-23",
        "execution_date": "2026-04-24",
        "root": root,
        "lead_raw_symbol": lead_raw_symbol,
        "target_contracts": target_contracts,
        "current_contracts": 0,
        "order_delta_contracts": target_contracts if order_delta_contracts is None else order_delta_contracts,
        "control_action": control_action,
    }


def _position(*, root: str, raw_symbol: str, position_contracts: int) -> dict[str, object]:
    return {
        "position_snapshot_id": "snapshot_001",
        "execution_mode": "shadow",
        "raw_symbol": raw_symbol,
        "root": root,
        "position_contracts": position_contracts,
        "snapshot_time_utc": "2026-04-23T00:00:00Z",
    }
