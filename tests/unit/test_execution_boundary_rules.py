from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from cpdshadow.config import load_execution_boundary_yaml
from cpdshadow.execution_boundary import qa_execution_boundary


def test_hold_root_remains_unchanged_in_qa() -> None:
    report = qa_execution_boundary(
        targets=_targets([_target(root="ES", lead_raw_symbol="ESU7", target_contracts=2, control_action="hold")]),
        snapshot_positions=_positions([_position(root="ES", raw_symbol="ESU7", position_contracts=2)]),
        final_order_intents=_final_intents([]),
        monitoring=_monitoring("hold"),
        config=_config(),
    )

    assert report.has_errors is False
    assert report.root_results[0]["passed"] is True


def test_reduce_only_never_increases_exposure_in_qa() -> None:
    report = qa_execution_boundary(
        targets=_targets([_target(root="ES", lead_raw_symbol="ESU7", target_contracts=5, control_action="reduce_only")]),
        snapshot_positions=_positions([_position(root="ES", raw_symbol="ESU7", position_contracts=3)]),
        final_order_intents=_final_intents([]),
        monitoring=_monitoring("reduce_only"),
        config=_config(),
    )

    assert report.has_errors is False


def test_qa_flags_incorrect_final_lead_position() -> None:
    report = qa_execution_boundary(
        targets=_targets([_target(root="ES", lead_raw_symbol="ESU7", target_contracts=2, control_action="run_cpd_lstm")]),
        snapshot_positions=_positions([_position(root="ES", raw_symbol="ESM7", position_contracts=2)]),
        final_order_intents=_final_intents(
            [
                _final_intent(root="ES", raw_symbol="ESM7", side="sell", quantity=2, sequence_no=1, reason="roll"),
                _final_intent(root="ES", raw_symbol="ESU7", side="buy", quantity=1, sequence_no=2, reason="roll"),
            ]
        ),
        monitoring=_monitoring("run_cpd_lstm"),
        config=_config(),
    )

    assert report.has_errors is True
    assert any(issue["code"] == "lead_target_mismatch" for issue in report.issues)


def _config():
    return load_execution_boundary_yaml(Path("config/execution_boundary.yml"))


def _targets(rows: list[dict[str, object]]) -> pd.DataFrame:
    return pd.DataFrame(rows)


def _positions(rows: list[dict[str, object]]) -> pd.DataFrame:
    return pd.DataFrame(rows)


def _monitoring(final_action: str) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "run_id": "shadow_run_001",
                "execution_mode": "shadow",
                "as_of_date": "2026-04-23",
                "execution_date": "2026-04-24",
                "final_action": final_action,
                "highest_severity": "info",
                "alerts_json": "[]",
                "created_at_utc": "2026-04-23T00:00:00Z",
            }
        ]
    )


def _target(*, root: str, lead_raw_symbol: str, target_contracts: int, control_action: str) -> dict[str, object]:
    return {
        "run_id": "shadow_run_001",
        "strategy_id": "tsmom" if control_action == "fallback_tsmom" else "cpd_lstm",
        "execution_mode": "shadow",
        "as_of_date": "2026-04-23",
        "execution_date": "2026-04-24",
        "root": root,
        "lead_raw_symbol": lead_raw_symbol,
        "target_contracts": target_contracts,
        "current_contracts": 0,
        "order_delta_contracts": target_contracts,
        "control_action": control_action,
    }


def _position(*, root: str, raw_symbol: str, position_contracts: int) -> dict[str, object]:
    return {
        "position_snapshot_id": "snapshot_001",
        "execution_mode": "shadow",
        "raw_symbol": raw_symbol,
        "root": root,
        "position_contracts": position_contracts,
        "snapshot_time_utc": datetime(2026, 4, 23, tzinfo=UTC),
    }


def _final_intents(rows: list[dict[str, object]]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=[
        "order_intent_id",
        "run_id",
        "strategy_id",
        "execution_mode",
        "as_of_date",
        "execution_date",
        "root",
        "raw_symbol",
        "broker_contract_id",
        "side",
        "quantity",
        "order_type",
        "limit_price",
        "reason",
        "status",
        "control_action",
        "position_snapshot_id",
        "sequence_no",
        "rejection_reason",
        "created_at_utc",
        "submitted_at_utc",
    ])


def _final_intent(
    *,
    root: str,
    raw_symbol: str,
    side: str,
    quantity: int,
    sequence_no: int,
    reason: str,
) -> dict[str, object]:
    return {
        "order_intent_id": f"oi_{sequence_no}",
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
        "reason": reason,
        "status": "not_sent",
        "control_action": "run_cpd_lstm",
        "position_snapshot_id": "snapshot_001",
        "sequence_no": sequence_no,
        "rejection_reason": None,
        "created_at_utc": "2026-04-23T00:00:00Z",
        "submitted_at_utc": None,
    }
