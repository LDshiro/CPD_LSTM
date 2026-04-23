from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime
from typing import Any

import pandas as pd

from cpdshadow.broker.ibkr.models import (
    IbkrBrokerError,
    IbkrCallbackEvent,
    NormalizedBrokerStatus,
)
from cpdshadow.broker.ibkr.translator import normalize_order_intents_frame


def normalize_broker_error(
    *,
    code: str | int | None,
    message: str,
    order_intent_id: str | None = None,
    broker_request_id: str | None = None,
    ib_order_id: int | None = None,
) -> IbkrBrokerError:
    code_text = "" if code is None else str(code)
    code_int = int(code) if code is not None and str(code).isdigit() else None
    if code_int in {1100, 1101, 1102}:
        severity = "warning"
        retryable = True
    elif code_int in {201, 202, 203, 399}:
        severity = "error"
        retryable = False
    else:
        severity = "error"
        retryable = False
    return IbkrBrokerError(
        code=code_text,
        message=message,
        severity=severity,
        retryable=retryable,
        order_intent_id=order_intent_id,
        broker_request_id=broker_request_id,
        ib_order_id=ib_order_id,
    )


def normalize_ibkr_status(
    *,
    raw_status: str | None,
    filled_quantity: int | None = None,
    remaining_quantity: int | None = None,
) -> NormalizedBrokerStatus:
    status = (raw_status or "").strip().lower()
    filled = int(filled_quantity or 0)
    remaining = int(remaining_quantity or 0)
    if filled > 0 and remaining > 0:
        return "partially_filled"
    if filled > 0 and remaining == 0:
        return "filled"
    if status in {"presubmitted"}:
        return "pre_submitted"
    if status in {"submitted", "pendingsubmit", "apipending"}:
        return "submitted"
    if status in {"filled"}:
        return "filled"
    if status in {"cancelled", "apicancelled", "pendingcancel"}:
        return "cancelled"
    if status in {"inactive"}:
        return "rejected"
    return "unknown"


def reconcile_order_intents(
    *,
    order_intents: pd.DataFrame,
    callback_events: list[IbkrCallbackEvent],
    broker_errors: list[IbkrBrokerError] | None = None,
) -> tuple[pd.DataFrame, dict[str, object]]:
    working = normalize_order_intents_frame(order_intents)
    if working.empty:
        return working, {
            "row_count": 0,
            "status_counts": {},
            "error_count": 0,
            "callback_count": len(callback_events),
        }
    errors = broker_errors or []
    events = sorted(
        callback_events,
        key=lambda event: (
            _to_utc(event.event_time_utc),
            event.event_id,
        ),
    )
    events_by_request: dict[str, list[IbkrCallbackEvent]] = {}
    for event in events:
        if event.broker_request_id is None:
            continue
        events_by_request.setdefault(str(event.broker_request_id), []).append(event)

    errors_by_request: dict[str, list[IbkrBrokerError]] = {}
    for error in errors:
        if error.broker_request_id is None:
            continue
        errors_by_request.setdefault(str(error.broker_request_id), []).append(error)

    reconciled_rows: list[dict[str, Any]] = []
    for row in working.to_dict(orient="records"):
        broker_request_id = row.get("broker_request_id")
        request_id = None
        if broker_request_id not in (None, "") and not pd.isna(broker_request_id):
            request_id = str(broker_request_id)
        row_events = events_by_request.get(request_id or "", [])
        row_errors = errors_by_request.get(request_id or "", [])
        status = str(row["status"])
        submitted_at = row.get("submitted_at_utc")
        updated_at = row.get("updated_at_utc")
        ib_order_id = row.get("ib_order_id")
        perm_id = row.get("perm_id")
        broker_error_code = row.get("broker_error_code")
        broker_error_message = row.get("broker_error_message")
        account_id = row.get("account_id")
        broker_contract_key = row.get("broker_contract_key")

        for event in row_events:
            event_status = _status_from_event(event)
            if event_status is not None:
                status = event_status
            if event.ib_order_id is not None:
                ib_order_id = event.ib_order_id
            if event.perm_id is not None:
                perm_id = event.perm_id
            if event.account_id is not None:
                account_id = event.account_id
            if event.broker_contract_key is not None:
                broker_contract_key = event.broker_contract_key
            if status in {"submitted", "pre_submitted", "filled", "partially_filled"}:
                submitted_at = submitted_at or _to_utc(event.event_time_utc)
            updated_at = _to_utc(event.event_time_utc)
            if event.error_code is not None:
                broker_error_code = event.error_code
                broker_error_message = event.error_message

        for error in row_errors:
            if status not in {"filled", "partially_filled", "rejected", "cancelled"}:
                status = "api_error"
            broker_error_code = error.code
            broker_error_message = error.message

        reconciled_rows.append(
            {
                **row,
                "status": status,
                "submitted_at_utc": submitted_at,
                "updated_at_utc": updated_at or row.get("updated_at_utc"),
                "ib_order_id": ib_order_id,
                "perm_id": perm_id,
                "account_id": account_id,
                "broker_contract_key": broker_contract_key,
                "broker_error_code": broker_error_code,
                "broker_error_message": broker_error_message,
            }
        )

    reconciled = normalize_order_intents_frame(pd.DataFrame(reconciled_rows))
    status_counts = Counter(reconciled["status"].astype(str))
    return reconciled, {
        "row_count": int(len(reconciled)),
        "status_counts": dict(sorted(status_counts.items())),
        "error_count": int(sum(1 for row in reconciled["broker_error_code"] if pd.notna(row))),
        "callback_count": int(len(callback_events)),
    }


def _status_from_event(event: IbkrCallbackEvent) -> NormalizedBrokerStatus | None:
    if event.event_type == "orderStatus":
        return normalize_ibkr_status(
            raw_status=event.status,
            filled_quantity=event.filled_quantity,
            remaining_quantity=event.remaining_quantity,
        )
    if event.event_type == "execDetails":
        if (event.remaining_quantity or 0) > 0:
            return "partially_filled"
        return "filled"
    if event.event_type == "error":
        normalized = normalize_broker_error(
            code=event.error_code,
            message=event.error_message or "",
            broker_request_id=event.broker_request_id,
            ib_order_id=event.ib_order_id,
        )
        return "api_error" if normalized.retryable else "rejected"
    return None


def _to_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
