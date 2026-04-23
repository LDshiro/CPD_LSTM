from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from cpdshadow.config import load_execution_boundary_yaml
from cpdshadow.dry_run_adapter import finalize_dry_run_intents


def test_valid_planned_intent_becomes_not_sent() -> None:
    finalized, events, summary = finalize_dry_run_intents(
        planned_intents=_planned_intents([_planned_intent(order_intent_id="oi_1", root="ES", raw_symbol="ESU7", side="buy", quantity=2)]),
        contract_master=pd.DataFrame([{"root": "ES", "raw_symbol": "ESU7"}]),
        config=_config(),
        created_at_utc=_created_at(),
    )

    assert [intent.status for intent in finalized] == ["not_sent"]
    assert summary["accepted_intents"] == 1
    assert summary["rejected_intents"] == 0
    assert any(event.code == "DRY_RUN_FINALIZED" for event in events)


def test_invalid_contract_is_rejected() -> None:
    finalized, _, summary = finalize_dry_run_intents(
        planned_intents=_planned_intents([_planned_intent(order_intent_id="oi_1", root="ES", raw_symbol="UNKNOWN", side="buy", quantity=1)]),
        contract_master=pd.DataFrame([{"root": "ES", "raw_symbol": "ESU7"}]),
        config=_config(),
        created_at_utc=_created_at(),
    )

    assert finalized[0].status == "rejected"
    assert finalized[0].rejection_reason == "unknown_contract"
    assert summary["rejected_intents"] == 1


def test_opposite_side_conflicts_are_rejected() -> None:
    finalized, _, _ = finalize_dry_run_intents(
        planned_intents=_planned_intents(
            [
                _planned_intent(order_intent_id="oi_1", root="ES", raw_symbol="ESU7", side="buy", quantity=1),
                _planned_intent(order_intent_id="oi_2", root="ES", raw_symbol="ESU7", side="sell", quantity=1, sequence_no=2),
            ]
        ),
        contract_master=pd.DataFrame([{"root": "ES", "raw_symbol": "ESU7"}]),
        config=_config(),
        created_at_utc=_created_at(),
    )

    assert {intent.status for intent in finalized} == {"rejected"}
    assert all("opposite_side_conflict" in str(intent.rejection_reason) for intent in finalized)


def test_final_statuses_are_only_not_sent_or_rejected() -> None:
    finalized, _, _ = finalize_dry_run_intents(
        planned_intents=_planned_intents(
            [
                _planned_intent(order_intent_id="oi_1", root="ES", raw_symbol="ESU7", side="buy", quantity=1),
                _planned_intent(order_intent_id="oi_2", root="ES", raw_symbol="BAD", side="buy", quantity=1, sequence_no=2),
            ]
        ),
        contract_master=pd.DataFrame([{"root": "ES", "raw_symbol": "ESU7"}]),
        config=_config(),
        created_at_utc=_created_at(),
    )

    assert {intent.status for intent in finalized} <= {"not_sent", "rejected"}


def _config():
    return load_execution_boundary_yaml(Path("config/execution_boundary.yml"))


def _created_at() -> datetime:
    return datetime(2026, 4, 23, tzinfo=UTC)


def _planned_intents(rows: list[dict[str, object]]) -> pd.DataFrame:
    return pd.DataFrame(rows)


def _planned_intent(
    *,
    order_intent_id: str,
    root: str,
    raw_symbol: str,
    side: str,
    quantity: int,
    sequence_no: int = 1,
) -> dict[str, object]:
    return {
        "order_intent_id": order_intent_id,
        "run_id": "shadow_run_001",
        "strategy_id": "cpd_lstm",
        "execution_mode": "shadow",
        "as_of_date": "2026-04-23",
        "execution_date": "2026-04-24",
        "root": root,
        "raw_symbol": raw_symbol,
        "broker_contract_id": None,
        "side": side,
        "quantity": quantity,
        "order_type": "marketable_limit",
        "limit_price": None,
        "reason": "rebalance",
        "status": "planned",
        "control_action": "run_cpd_lstm",
        "position_snapshot_id": "snapshot_001",
        "sequence_no": sequence_no,
        "rejection_reason": None,
        "created_at_utc": "2026-04-23T00:00:00Z",
        "submitted_at_utc": None,
    }
