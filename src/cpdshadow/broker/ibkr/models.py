from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
from typing import Literal, Protocol

from cpdshadow.ids import stable_sha256_hex


BrokerMode = Literal["shadow_only", "paper_submit"]
NormalizedBrokerStatus = Literal[
    "not_sent",
    "submitted",
    "pre_submitted",
    "filled",
    "partially_filled",
    "cancelled",
    "rejected",
    "api_error",
    "unknown",
]
CallbackEventType = Literal[
    "nextValidId",
    "position",
    "positionEnd",
    "openOrder",
    "openOrderEnd",
    "accountSummary",
    "accountSummaryEnd",
    "orderStatus",
    "execDetails",
    "error",
]
BrokerErrorSeverity = Literal["info", "warning", "error", "critical"]


@dataclass(frozen=True)
class IbkrResolvedContract:
    root: str
    raw_symbol: str
    exchange: str
    currency: str
    local_symbol: str
    last_trade_date: date | None
    multiplier: float | None
    min_tick: float
    broker_contract_key: str
    contract_fields: dict[str, object]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class IbkrTranslatedOrderRequest:
    order_intent_id: str
    run_id: str
    broker_request_id: str
    broker_mode: BrokerMode
    account_id: str
    client_id: int
    broker_contract_key: str
    root: str
    raw_symbol: str
    action: Literal["BUY", "SELL"]
    total_quantity: int
    order_type: Literal["LMT"]
    tif: str
    outside_rth: bool
    limit_price: float | None
    preview_only: bool
    order_ref: str
    created_at_utc: datetime

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class IbkrBrokerPosition:
    account_id: str
    broker_contract_id: str | None
    local_symbol: str | None
    symbol: str | None
    exchange: str | None
    currency: str | None
    sec_type: str | None
    quantity: int
    avg_cost: float | None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class IbkrBrokerOpenOrder:
    account_id: str
    client_id: int | None
    ib_order_id: int | None
    perm_id: int | None
    broker_contract_id: str | None
    local_symbol: str | None
    symbol: str | None
    exchange: str | None
    currency: str | None
    action: str
    total_quantity: int
    filled_quantity: int
    remaining_quantity: int
    order_type: str
    tif: str
    limit_price: float | None
    status: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class IbkrPositionSnapshotRow:
    position_snapshot_id: str
    run_id: str
    execution_mode: str
    account_id: str | None
    broker_contract_id: str | None
    broker_contract_key: str | None
    raw_symbol: str | None
    root: str | None
    position_contracts: int
    avg_cost: float | None
    snapshot_time_utc: datetime
    source: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class IbkrOpenOrderSnapshotRow:
    open_orders_snapshot_id: str
    run_id: str
    execution_mode: str
    account_id: str
    captured_at_utc: datetime
    client_id: int | None
    ib_order_id: int | None
    perm_id: int | None
    broker_contract_id: str | None
    broker_contract_key: str | None
    raw_symbol: str | None
    root: str | None
    action: str
    total_quantity: int
    filled_quantity: int
    remaining_quantity: int
    order_type: str
    tif: str
    limit_price: float | None
    status: str
    source: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class IbkrAccountSummary:
    account_id: str
    captured_at_utc: datetime
    net_liquidation: float | None
    excess_liquidity: float | None
    init_margin_req: float | None
    maint_margin_req: float | None
    buying_power: float | None
    available_funds: float | None
    source: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class IbkrCallbackEvent:
    event_id: str
    event_type: CallbackEventType
    event_time_utc: datetime
    account_id: str | None
    client_id: int | None
    broker_request_id: str | None
    ib_order_id: int | None
    perm_id: int | None
    broker_contract_key: str | None
    raw_symbol: str | None
    root: str | None
    status: str | None
    filled_quantity: int | None
    remaining_quantity: int | None
    error_code: str | None
    error_message: str | None
    payload: dict[str, object] | None

    def to_dict(self) -> dict[str, object]:
        return {
            **asdict(self),
            "payload": self.payload or {},
        }


@dataclass(frozen=True)
class IbkrBrokerError:
    code: str
    message: str
    severity: BrokerErrorSeverity
    retryable: bool
    order_intent_id: str | None
    broker_request_id: str | None
    ib_order_id: int | None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class IbkrSyncResult:
    next_valid_order_id: int | None
    positions: list[IbkrBrokerPosition]
    open_orders: list[IbkrBrokerOpenOrder]
    account_summary: IbkrAccountSummary | None
    callback_events: list[IbkrCallbackEvent]

    def to_dict(self) -> dict[str, object]:
        return {
            "next_valid_order_id": self.next_valid_order_id,
            "positions": [row.to_dict() for row in self.positions],
            "open_orders": [row.to_dict() for row in self.open_orders],
            "account_summary": (
                self.account_summary.to_dict() if self.account_summary is not None else None
            ),
            "callback_events": [event.to_dict() for event in self.callback_events],
        }


@dataclass(frozen=True)
class IbkrSubmitResult:
    starting_order_id: int | None
    translated_requests: list[IbkrTranslatedOrderRequest]
    callback_events: list[IbkrCallbackEvent]
    broker_errors: list[IbkrBrokerError]

    def to_dict(self) -> dict[str, object]:
        return {
            "starting_order_id": self.starting_order_id,
            "translated_requests": [row.to_dict() for row in self.translated_requests],
            "callback_events": [event.to_dict() for event in self.callback_events],
            "broker_errors": [error.to_dict() for error in self.broker_errors],
        }


class IbkrClientProtocol(Protocol):
    account_id: str | None
    client_id: int | None
    place_order_calls: int

    def connect(self) -> None: ...

    def disconnect(self) -> None: ...

    def ensure_next_valid_id(self, timeout_seconds: int) -> int: ...

    def fetch_positions(self, timeout_seconds: int) -> tuple[list[IbkrBrokerPosition], list[IbkrCallbackEvent]]: ...

    def fetch_open_orders(
        self, timeout_seconds: int
    ) -> tuple[list[IbkrBrokerOpenOrder], list[IbkrCallbackEvent]]: ...

    def fetch_account_summary(
        self, timeout_seconds: int
    ) -> tuple[IbkrAccountSummary | None, list[IbkrCallbackEvent]]: ...

    def place_order(
        self,
        request: IbkrTranslatedOrderRequest,
        *,
        ib_order_id: int,
        timeout_seconds: int,
    ) -> list[IbkrCallbackEvent]: ...


def make_callback_event(
    *,
    event_type: CallbackEventType,
    event_time_utc: datetime | None = None,
    account_id: str | None = None,
    client_id: int | None = None,
    broker_request_id: str | None = None,
    ib_order_id: int | None = None,
    perm_id: int | None = None,
    broker_contract_key: str | None = None,
    raw_symbol: str | None = None,
    root: str | None = None,
    status: str | None = None,
    filled_quantity: int | None = None,
    remaining_quantity: int | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
    payload: dict[str, object] | None = None,
) -> IbkrCallbackEvent:
    current = event_time_utc or datetime.now(UTC)
    payload_hash = {
        "event_type": event_type,
        "event_time_utc": current,
        "account_id": account_id,
        "client_id": client_id,
        "broker_request_id": broker_request_id,
        "ib_order_id": ib_order_id,
        "perm_id": perm_id,
        "broker_contract_key": broker_contract_key,
        "raw_symbol": raw_symbol,
        "root": root,
        "status": status,
        "filled_quantity": filled_quantity,
        "remaining_quantity": remaining_quantity,
        "error_code": error_code,
        "error_message": error_message,
        "payload": payload or {},
    }
    return IbkrCallbackEvent(
        event_id=f"ibev_{stable_sha256_hex(payload_hash)[:16]}",
        event_type=event_type,
        event_time_utc=current,
        account_id=account_id,
        client_id=client_id,
        broker_request_id=broker_request_id,
        ib_order_id=ib_order_id,
        perm_id=perm_id,
        broker_contract_key=broker_contract_key,
        raw_symbol=raw_symbol,
        root=root,
        status=status,
        filled_quantity=filled_quantity,
        remaining_quantity=remaining_quantity,
        error_code=error_code,
        error_message=error_message,
        payload=payload or {},
    )
