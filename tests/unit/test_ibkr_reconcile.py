from __future__ import annotations

from datetime import UTC, datetime

import pandas as pd

from cpdshadow.broker.ibkr.models import make_callback_event
from cpdshadow.broker.ibkr.reconcile import normalize_broker_error, reconcile_order_intents


def test_normalize_broker_error_marks_connection_codes_retryable() -> None:
    error = normalize_broker_error(code=1100, message="Connectivity between IB and Trader Workstation has been lost.")

    assert error.severity == "warning"
    assert error.retryable is True


def test_reconciliation_is_deterministic_under_callback_reordering() -> None:
    order_intents = pd.DataFrame(
        [
            {
                "order_intent_id": "oi_es_001",
                "run_id": "shadow_run_001",
                "strategy_id": "cpd_lstm",
                "execution_mode": "paper",
                "as_of_date": "2026-04-23",
                "execution_date": "2026-04-24",
                "root": "ES",
                "raw_symbol": "ESM7",
                "broker_contract_id": None,
                "broker_name": "ibkr_tws_api",
                "broker_mode": "paper_submit",
                "broker_contract_key": "IBKR|FUT|CME|ESM7|USD",
                "broker_request_id": "brq_001",
                "ib_order_id": None,
                "perm_id": None,
                "account_id": "DU0000000",
                "side": "buy",
                "quantity": 2,
                "order_type": "LMT",
                "limit_price": 5102.75,
                "reason": "rebalance",
                "status": "submitted",
                "control_action": "run_cpd_lstm",
                "position_snapshot_id": "snapshot_001",
                "sequence_no": 1,
                "rejection_reason": None,
                "broker_error_code": None,
                "broker_error_message": None,
                "created_at_utc": "2026-04-23T00:00:00Z",
                "submitted_at_utc": None,
                "updated_at_utc": "2026-04-23T00:00:00Z",
            }
        ]
    )
    first_events = [
        _event("openOrder", status="PreSubmitted"),
        _event("orderStatus", status="Submitted", filled_quantity=0, remaining_quantity=2),
        _event("execDetails", filled_quantity=2, remaining_quantity=0),
    ]
    second_events = list(reversed(first_events))

    first_df, first_report = reconcile_order_intents(
        order_intents=order_intents,
        callback_events=first_events,
    )
    second_df, second_report = reconcile_order_intents(
        order_intents=order_intents,
        callback_events=second_events,
    )

    assert first_df["status"].tolist() == ["filled"]
    assert second_df["status"].tolist() == ["filled"]
    assert first_report["status_counts"] == second_report["status_counts"]


def _event(
    event_type: str,
    *,
    status: str | None = None,
    filled_quantity: int | None = None,
    remaining_quantity: int | None = None,
):
    return make_callback_event(
        event_type=event_type,  # type: ignore[arg-type]
        event_time_utc=datetime(2026, 4, 23, tzinfo=UTC),
        account_id="DU0000000",
        client_id=7,
        broker_request_id="brq_001",
        ib_order_id=1001,
        perm_id=5001,
        broker_contract_key="IBKR|FUT|CME|ESM7|USD",
        raw_symbol="ESM7",
        root="ES",
        status=status,
        filled_quantity=filled_quantity,
        remaining_quantity=remaining_quantity,
        payload={},
    )
