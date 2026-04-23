from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any, Literal

import pandas as pd

from cpdshadow.broker.ibkr.models import (
    BrokerMode,
    IbkrResolvedContract,
    IbkrTranslatedOrderRequest,
)
from cpdshadow.config import BrokerIbkrConfig
from cpdshadow.ids import stable_sha256_hex


_BROKER_ORDER_INTENT_COLUMNS = [
    "order_intent_id",
    "run_id",
    "strategy_id",
    "execution_mode",
    "as_of_date",
    "execution_date",
    "root",
    "raw_symbol",
    "broker_contract_id",
    "broker_name",
    "broker_mode",
    "broker_contract_key",
    "broker_request_id",
    "ib_order_id",
    "perm_id",
    "account_id",
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
    "broker_error_code",
    "broker_error_message",
    "created_at_utc",
    "submitted_at_utc",
    "updated_at_utc",
]


class IbkrTranslationError(ValueError):
    pass


@dataclass(frozen=True)
class ReferencePrice:
    price: float
    source_field: str
    trade_date: date


def normalize_order_intents_frame(df: pd.DataFrame) -> pd.DataFrame:
    working = df.copy()
    for column in _BROKER_ORDER_INTENT_COLUMNS:
        if column not in working.columns:
            working[column] = None
    for column in ["as_of_date", "execution_date"]:
        working[column] = pd.to_datetime(working[column]).dt.date
    for column in [
        "created_at_utc",
        "submitted_at_utc",
        "updated_at_utc",
    ]:
        working[column] = pd.to_datetime(working[column], utc=True, errors="coerce")
    working["sequence_no"] = pd.to_numeric(
        working["sequence_no"], errors="coerce"
    ).fillna(0).astype(int)
    working["quantity"] = pd.to_numeric(
        working["quantity"], errors="coerce"
    ).fillna(0).astype(int)
    return working.reindex(columns=_BROKER_ORDER_INTENT_COLUMNS).sort_values(
        ["sequence_no", "order_intent_id"], kind="stable"
    ).reset_index(drop=True)


def resolve_reference_price(
    *,
    contracts_daily: pd.DataFrame,
    raw_symbol: str,
    as_of_date: date,
    max_age_days: int,
) -> ReferencePrice:
    if contracts_daily.empty:
        raise IbkrTranslationError("contracts_daily is empty; cannot derive paper reference price")
    working = contracts_daily.copy()
    working["trade_date"] = pd.to_datetime(working["trade_date"]).dt.date
    working = working[
        (working["raw_symbol"].astype(str) == str(raw_symbol))
        & (working["trade_date"] <= as_of_date)
    ].sort_values(["trade_date", "available_at_utc"], kind="stable")
    if working.empty:
        raise IbkrTranslationError(
            f"no contracts_daily row exists on or before {as_of_date} for {raw_symbol}"
        )
    row = working.iloc[-1]
    trade_date = pd.Timestamp(row["trade_date"]).date()
    age_days = (as_of_date - trade_date).days
    if age_days > max_age_days:
        raise IbkrTranslationError(
            f"reference price for {raw_symbol} is stale by {age_days} days"
        )
    for field in ["settle_price", "close_price"]:
        value = row.get(field)
        if value is not None and pd.notna(value):
            return ReferencePrice(price=float(value), source_field=field, trade_date=trade_date)
    raise IbkrTranslationError(f"no settle/close reference price available for {raw_symbol}")


def round_limit_price(*, price: float, tick_size: float, action: str) -> float:
    if tick_size <= 0:
        raise IbkrTranslationError("tick_size must be positive")
    steps = price / tick_size
    if action == "BUY":
        rounded = math.ceil(steps - 1.0e-12) * tick_size
    elif action == "SELL":
        rounded = math.floor(steps + 1.0e-12) * tick_size
    else:
        raise IbkrTranslationError(f"unsupported action {action!r}")
    return round(float(rounded), 12)


def translate_order_request(
    *,
    intent_row: dict[str, Any],
    resolved_contract: IbkrResolvedContract,
    broker_mode: BrokerMode,
    account_id: str,
    client_id: int,
    broker_config: BrokerIbkrConfig,
    contracts_daily: pd.DataFrame,
    created_at_utc: datetime,
) -> IbkrTranslatedOrderRequest:
    action = _ib_action(intent_row["side"])
    broker_request_id = make_broker_request_id(
        order_intent_id=str(intent_row["order_intent_id"]),
        broker_mode=broker_mode,
        account_id=account_id,
    )
    limit_price: float | None = None
    if broker_mode == "paper_submit":
        reference = resolve_reference_price(
            contracts_daily=contracts_daily,
            raw_symbol=str(intent_row["raw_symbol"]),
            as_of_date=pd.Timestamp(intent_row["as_of_date"]).date(),
            max_age_days=broker_config.reference_max_age_days,
        )
        buffered = apply_buffer_bps(
            price=reference.price,
            action=action,
            buffer_bps=broker_config.paper_buffer_bps,
        )
        limit_price = round_limit_price(
            price=buffered,
            tick_size=resolved_contract.min_tick,
            action=action,
        )
    return IbkrTranslatedOrderRequest(
        order_intent_id=str(intent_row["order_intent_id"]),
        run_id=str(intent_row["run_id"]),
        broker_request_id=broker_request_id,
        broker_mode=broker_mode,
        account_id=account_id,
        client_id=client_id,
        broker_contract_key=resolved_contract.broker_contract_key,
        root=str(intent_row["root"]),
        raw_symbol=str(intent_row["raw_symbol"]),
        action=action,
        total_quantity=int(intent_row["quantity"]),
        order_type="LMT",
        tif=broker_config.default_tif,
        outside_rth=bool(broker_config.outside_rth),
        limit_price=limit_price,
        preview_only=broker_mode == "shadow_only",
        order_ref=broker_request_id,
        created_at_utc=_normalize_datetime(created_at_utc),
    )


def validate_reduce_only_root(
    *,
    root: str,
    root_requests: list[IbkrTranslatedOrderRequest],
    snapshot_positions: pd.DataFrame,
) -> str | None:
    if not root_requests:
        return None
    working = snapshot_positions.copy()
    if not working.empty and "root" in working.columns:
        working = working[working["root"].astype(str) == str(root)].copy()
    current_total = int(pd.to_numeric(working.get("position_contracts"), errors="coerce").fillna(0).sum())
    if current_total == 0:
        projected_total = sum(
            request.total_quantity if request.action == "BUY" else -request.total_quantity
            for request in root_requests
        )
        if projected_total != 0:
            return "reduce_only_opened_from_flat"
        return None
    projected_total = current_total
    for request in root_requests:
        projected_total += request.total_quantity if request.action == "BUY" else -request.total_quantity
    if abs(projected_total) > abs(current_total):
        return "reduce_only_increases_absolute_exposure"
    if projected_total != 0 and (projected_total > 0) != (current_total > 0):
        return "reduce_only_flips_sign"
    return None


def apply_buffer_bps(*, price: float, action: str, buffer_bps: float) -> float:
    if action == "BUY":
        return float(price) * (1.0 + buffer_bps / 10_000.0)
    if action == "SELL":
        return float(price) * (1.0 - buffer_bps / 10_000.0)
    raise IbkrTranslationError(f"unsupported action {action!r}")


def make_broker_request_id(
    *,
    order_intent_id: str,
    broker_mode: BrokerMode,
    account_id: str,
) -> str:
    return f"brq_{stable_sha256_hex([order_intent_id, broker_mode, account_id])[:16]}"


def _ib_action(side: object) -> Literal["BUY", "SELL"]:
    side_value = str(side).lower()
    if side_value == "buy":
        return "BUY"
    if side_value == "sell":
        return "SELL"
    raise IbkrTranslationError(f"unsupported intent side {side!r}")


def _normalize_datetime(value: datetime) -> datetime:
    current = value
    if current.tzinfo is None:
        current = current.replace(tzinfo=UTC)
    return current.astimezone(UTC)
