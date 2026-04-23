from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from datetime import UTC, datetime
from threading import Event, Lock, Thread
from typing import Any

from cpdshadow.broker.ibkr.models import (
    IbkrAccountSummary,
    IbkrBrokerOpenOrder,
    IbkrBrokerPosition,
    IbkrCallbackEvent,
    IbkrTranslatedOrderRequest,
    make_callback_event,
)


class IbkrClientUnavailableError(RuntimeError):
    pass


@dataclass
class _SessionState:
    lock: Lock = field(default_factory=Lock)
    next_valid_id: int | None = None
    next_valid_event: Event = field(default_factory=Event)
    positions: list[IbkrBrokerPosition] = field(default_factory=list)
    position_events: list[IbkrCallbackEvent] = field(default_factory=list)
    positions_done: Event = field(default_factory=Event)
    open_orders: list[IbkrBrokerOpenOrder] = field(default_factory=list)
    open_order_events: list[IbkrCallbackEvent] = field(default_factory=list)
    open_orders_done: Event = field(default_factory=Event)
    account_values: dict[str, str] = field(default_factory=dict)
    account_events: list[IbkrCallbackEvent] = field(default_factory=list)
    account_done: Event = field(default_factory=Event)
    order_events: dict[int, list[IbkrCallbackEvent]] = field(default_factory=dict)
    request_context: dict[int, dict[str, object]] = field(default_factory=dict)

    def reset_positions(self) -> None:
        with self.lock:
            self.positions = []
            self.position_events = []
            self.positions_done.clear()

    def reset_open_orders(self) -> None:
        with self.lock:
            self.open_orders = []
            self.open_order_events = []
            self.open_orders_done.clear()

    def reset_account_summary(self) -> None:
        with self.lock:
            self.account_values = {}
            self.account_events = []
            self.account_done.clear()

    def register_request(self, order_id: int, context: dict[str, object]) -> None:
        with self.lock:
            self.request_context[order_id] = context
            self.order_events[order_id] = []

    def append_order_event(self, order_id: int | None, event: IbkrCallbackEvent) -> None:
        if order_id is None:
            return
        with self.lock:
            self.order_events.setdefault(order_id, []).append(event)


class RealIbkrTwsClient:
    def __init__(
        self,
        *,
        host: str,
        port: int,
        client_id: int,
        account_id: str | None,
        account_summary_tags: list[str] | None = None,
    ) -> None:
        self.host = host
        self.port = int(port)
        self.client_id = int(client_id)
        self.account_id = account_id
        self.account_summary_tags = tuple(account_summary_tags or ())
        self.place_order_calls = 0
        self._ibapi = _load_ibapi()
        self._state = _SessionState()
        self._app = self._build_app()
        self._thread: Thread | None = None

    def connect(self) -> None:
        self._app.connect(self.host, self.port, self.client_id)
        self._thread = Thread(target=self._app.run, daemon=True)
        self._thread.start()

    def disconnect(self) -> None:
        try:
            self._app.disconnect()
        finally:
            if self._thread is not None:
                self._thread.join(timeout=1.0)

    def ensure_next_valid_id(self, timeout_seconds: int) -> int:
        if self._state.next_valid_id is not None:
            return int(self._state.next_valid_id)
        if not self._state.next_valid_event.wait(timeout_seconds):
            raise TimeoutError("timed out waiting for IBKR nextValidId")
        if self._state.next_valid_id is None:
            raise TimeoutError("IBKR nextValidId callback did not set an order id")
        return int(self._state.next_valid_id)

    def fetch_positions(
        self, timeout_seconds: int
    ) -> tuple[list[IbkrBrokerPosition], list[IbkrCallbackEvent]]:
        self._state.reset_positions()
        self._app.reqPositions()
        if not self._state.positions_done.wait(timeout_seconds):
            raise TimeoutError("timed out waiting for IBKR positions snapshot")
        return deepcopy(self._state.positions), deepcopy(self._state.position_events)

    def fetch_open_orders(
        self, timeout_seconds: int
    ) -> tuple[list[IbkrBrokerOpenOrder], list[IbkrCallbackEvent]]:
        self._state.reset_open_orders()
        self._app.reqAllOpenOrders()
        if not self._state.open_orders_done.wait(timeout_seconds):
            raise TimeoutError("timed out waiting for IBKR open orders snapshot")
        return deepcopy(self._state.open_orders), deepcopy(self._state.open_order_events)

    def fetch_account_summary(
        self, timeout_seconds: int
    ) -> tuple[IbkrAccountSummary | None, list[IbkrCallbackEvent]]:
        self._state.reset_account_summary()
        request_id = 7001
        tags = ",".join(
            self.account_summary_tags
            or (
                "NetLiquidation",
                "ExcessLiquidity",
                "InitMarginReq",
                "MaintMarginReq",
                "BuyingPower",
                "AvailableFunds",
            )
        )
        self._app.reqAccountSummary(request_id, "All", tags)
        if not self._state.account_done.wait(timeout_seconds):
            raise TimeoutError("timed out waiting for IBKR account summary snapshot")
        self._app.cancelAccountSummary(request_id)
        summary = _build_account_summary(
            account_id=self.account_id,
            account_values=self._state.account_values,
        )
        return summary, deepcopy(self._state.account_events)

    def place_order(
        self,
        request: IbkrTranslatedOrderRequest,
        *,
        ib_order_id: int,
        timeout_seconds: int,
    ) -> list[IbkrCallbackEvent]:
        contract = self._ibapi.Contract()
        for key, value in request_to_contract_fields(request).items():
            setattr(contract, key, value)
        order = self._ibapi.Order()
        order.action = request.action
        order.totalQuantity = request.total_quantity
        order.orderType = request.order_type
        order.tif = request.tif
        order.outsideRth = request.outside_rth
        order.account = request.account_id
        order.orderRef = request.order_ref
        if request.limit_price is not None:
            order.lmtPrice = request.limit_price
        self._state.register_request(
            ib_order_id,
            {
                "broker_request_id": request.broker_request_id,
                "broker_contract_key": request.broker_contract_key,
                "raw_symbol": request.raw_symbol,
                "root": request.root,
            },
        )
        self.place_order_calls += 1
        self._app.placeOrder(ib_order_id, contract, order)
        started = datetime.now(UTC)
        while (datetime.now(UTC) - started).total_seconds() < timeout_seconds:
            with self._state.lock:
                if self._state.order_events.get(ib_order_id):
                    return deepcopy(self._state.order_events[ib_order_id])
        return []

    def _build_app(self) -> Any:
        EWrapper = self._ibapi.EWrapper
        EClient = self._ibapi.EClient
        state = self._state
        outer = self

        class _IbkrApp(EWrapper, EClient):
            def __init__(self) -> None:
                EClient.__init__(self, self)

            def nextValidId(self, orderId: int) -> None:  # noqa: N802
                state.next_valid_id = int(orderId)
                state.next_valid_event.set()
                state.append_order_event(
                    None,
                    make_callback_event(
                        event_type="nextValidId",
                        account_id=outer.account_id,
                        client_id=outer.client_id,
                        payload={"orderId": int(orderId)},
                    ),
                )

            def position(  # noqa: N802
                self,
                account: str,
                contract: Any,
                position: float,
                avgCost: float,
            ) -> None:
                snapshot = IbkrBrokerPosition(
                    account_id=account,
                    broker_contract_id=str(getattr(contract, "conId", "")) or None,
                    local_symbol=str(getattr(contract, "localSymbol", "")) or None,
                    symbol=str(getattr(contract, "symbol", "")) or None,
                    exchange=str(getattr(contract, "exchange", "")) or None,
                    currency=str(getattr(contract, "currency", "")) or None,
                    sec_type=str(getattr(contract, "secType", "")) or None,
                    quantity=int(position),
                    avg_cost=float(avgCost) if avgCost is not None else None,
                )
                with state.lock:
                    state.positions.append(snapshot)
                    state.position_events.append(
                        make_callback_event(
                            event_type="position",
                            account_id=account,
                            client_id=outer.client_id,
                            raw_symbol=snapshot.local_symbol,
                            payload=snapshot.to_dict(),
                        )
                    )

            def positionEnd(self) -> None:  # noqa: N802
                with state.lock:
                    state.position_events.append(
                        make_callback_event(
                            event_type="positionEnd",
                            account_id=outer.account_id,
                            client_id=outer.client_id,
                            payload={"row_count": len(state.positions)},
                        )
                    )
                state.positions_done.set()

            def openOrder(  # noqa: N802
                self,
                orderId: int,
                contract: Any,
                order: Any,
                orderState: Any,
            ) -> None:
                snapshot = IbkrBrokerOpenOrder(
                    account_id=str(getattr(order, "account", "")) or outer.account_id or "",
                    client_id=int(getattr(order, "clientId", outer.client_id or 0)),
                    ib_order_id=int(orderId),
                    perm_id=int(getattr(order, "permId", 0)) if getattr(order, "permId", None) else None,
                    broker_contract_id=str(getattr(contract, "conId", "")) or None,
                    local_symbol=str(getattr(contract, "localSymbol", "")) or None,
                    symbol=str(getattr(contract, "symbol", "")) or None,
                    exchange=str(getattr(contract, "exchange", "")) or None,
                    currency=str(getattr(contract, "currency", "")) or None,
                    action=str(getattr(order, "action", "")),
                    total_quantity=int(getattr(order, "totalQuantity", 0)),
                    filled_quantity=0,
                    remaining_quantity=int(getattr(order, "totalQuantity", 0)),
                    order_type=str(getattr(order, "orderType", "")),
                    tif=str(getattr(order, "tif", "")),
                    limit_price=float(getattr(order, "lmtPrice", 0.0))
                    if getattr(order, "lmtPrice", None) is not None
                    else None,
                    status=str(getattr(orderState, "status", "")),
                )
                with state.lock:
                    state.open_orders.append(snapshot)
                    event = make_callback_event(
                        event_type="openOrder",
                        account_id=snapshot.account_id,
                        client_id=snapshot.client_id,
                        ib_order_id=snapshot.ib_order_id,
                        perm_id=snapshot.perm_id,
                        raw_symbol=snapshot.local_symbol,
                        status=snapshot.status,
                        payload=snapshot.to_dict(),
                    )
                    state.open_order_events.append(event)
                    state.append_order_event(int(orderId), event)

            def openOrderEnd(self) -> None:  # noqa: N802
                with state.lock:
                    state.open_order_events.append(
                        make_callback_event(
                            event_type="openOrderEnd",
                            account_id=outer.account_id,
                            client_id=outer.client_id,
                            payload={"row_count": len(state.open_orders)},
                        )
                    )
                state.open_orders_done.set()

            def accountSummary(  # noqa: N802
                self,
                reqId: int,
                account: str,
                tag: str,
                value: str,
                currency: str,
            ) -> None:
                _ = reqId
                _ = currency
                with state.lock:
                    state.account_values[tag] = value
                    state.account_events.append(
                        make_callback_event(
                            event_type="accountSummary",
                            account_id=account,
                            client_id=outer.client_id,
                            payload={"tag": tag, "value": value},
                        )
                    )

            def accountSummaryEnd(self, reqId: int) -> None:  # noqa: N802
                _ = reqId
                with state.lock:
                    state.account_events.append(
                        make_callback_event(
                            event_type="accountSummaryEnd",
                            account_id=outer.account_id,
                            client_id=outer.client_id,
                            payload={"row_count": len(state.account_values)},
                        )
                    )
                state.account_done.set()

            def orderStatus(  # noqa: N802
                self,
                orderId: int,
                status: str,
                filled: float,
                remaining: float,
                avgFillPrice: float,
                permId: int,
                parentId: int,
                lastFillPrice: float,
                clientId: int,
                whyHeld: str,
                mktCapPrice: float = 0.0,
            ) -> None:
                _ = avgFillPrice
                _ = parentId
                _ = lastFillPrice
                _ = whyHeld
                _ = mktCapPrice
                context = state.request_context.get(int(orderId), {})
                event = make_callback_event(
                    event_type="orderStatus",
                    account_id=outer.account_id,
                    client_id=clientId,
                    broker_request_id=context.get("broker_request_id"),
                    ib_order_id=int(orderId),
                    perm_id=int(permId) if permId else None,
                    broker_contract_key=context.get("broker_contract_key"),
                    raw_symbol=context.get("raw_symbol"),
                    root=context.get("root"),
                    status=status,
                    filled_quantity=int(filled),
                    remaining_quantity=int(remaining),
                    payload={"status": status},
                )
                state.append_order_event(int(orderId), event)

            def execDetails(self, reqId: int, contract: Any, execution: Any) -> None:  # noqa: N802
                _ = reqId
                order_id = int(getattr(execution, "orderId", 0))
                context = state.request_context.get(order_id, {})
                event = make_callback_event(
                    event_type="execDetails",
                    account_id=str(getattr(execution, "acctNumber", "")) or outer.account_id,
                    client_id=outer.client_id,
                    broker_request_id=context.get("broker_request_id"),
                    ib_order_id=order_id,
                    perm_id=int(getattr(execution, "permId", 0))
                    if getattr(execution, "permId", None)
                    else None,
                    broker_contract_key=context.get("broker_contract_key"),
                    raw_symbol=str(getattr(contract, "localSymbol", "")) or context.get("raw_symbol"),
                    root=context.get("root"),
                    filled_quantity=int(getattr(execution, "shares", 0)),
                    remaining_quantity=0,
                    payload={"execId": str(getattr(execution, "execId", ""))},
                )
                state.append_order_event(order_id, event)

            def error(  # noqa: N802
                self,
                reqId: int,
                errorCode: int,
                errorString: str,
                advancedOrderRejectJson: str = "",
            ) -> None:
                _ = advancedOrderRejectJson
                context = state.request_context.get(int(reqId), {})
                event = make_callback_event(
                    event_type="error",
                    account_id=outer.account_id,
                    client_id=outer.client_id,
                    broker_request_id=context.get("broker_request_id"),
                    ib_order_id=int(reqId) if reqId > 0 else None,
                    broker_contract_key=context.get("broker_contract_key"),
                    raw_symbol=context.get("raw_symbol"),
                    root=context.get("root"),
                    error_code=str(errorCode),
                    error_message=errorString,
                    payload={"reqId": int(reqId), "errorCode": int(errorCode)},
                )
                state.append_order_event(int(reqId) if reqId > 0 else None, event)

        return _IbkrApp()


def request_to_contract_fields(request: IbkrTranslatedOrderRequest) -> dict[str, object]:
    return {
        "secType": "FUT",
        "symbol": request.root,
        "localSymbol": request.raw_symbol,
        "exchange": request.broker_contract_key.split("|")[2],
        "currency": request.broker_contract_key.split("|")[4],
    }


def _build_account_summary(
    *,
    account_id: str | None,
    account_values: dict[str, str],
) -> IbkrAccountSummary | None:
    if account_id is None and not account_values:
        return None

    def _to_float(tag: str) -> float | None:
        value = account_values.get(tag)
        if value in (None, ""):
            return None
        return float(value)

    return IbkrAccountSummary(
        account_id=account_id or "",
        captured_at_utc=datetime.now(UTC),
        net_liquidation=_to_float("NetLiquidation"),
        excess_liquidity=_to_float("ExcessLiquidity"),
        init_margin_req=_to_float("InitMarginReq"),
        maint_margin_req=_to_float("MaintMarginReq"),
        buying_power=_to_float("BuyingPower"),
        available_funds=_to_float("AvailableFunds"),
        source="ibkr_tws_api",
    )


def _load_ibapi() -> Any:
    try:
        from ibapi.client import EClient
        from ibapi.contract import Contract
        from ibapi.order import Order
        from ibapi.wrapper import EWrapper
    except ImportError as exc:
        raise IbkrClientUnavailableError(
            "IBKR TWS API Python package is not installed. Install ibapi to use real broker mode."
        ) from exc
    return type(
        "_IbApiNamespace",
        (),
        {"EClient": EClient, "EWrapper": EWrapper, "Contract": Contract, "Order": Order},
    )
