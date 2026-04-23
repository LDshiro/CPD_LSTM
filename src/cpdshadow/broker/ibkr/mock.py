from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime

from cpdshadow.broker.ibkr.models import (
    IbkrAccountSummary,
    IbkrBrokerOpenOrder,
    IbkrBrokerPosition,
    IbkrCallbackEvent,
    IbkrClientProtocol,
    IbkrTranslatedOrderRequest,
    make_callback_event,
)


class MockIbkrClient(IbkrClientProtocol):
    def __init__(
        self,
        *,
        account_id: str | None,
        client_id: int | None,
        next_valid_order_id: int = 1000,
        positions: list[IbkrBrokerPosition] | None = None,
        open_orders: list[IbkrBrokerOpenOrder] | None = None,
        account_summary: IbkrAccountSummary | None = None,
        callbacks_by_request_id: dict[str, list[IbkrCallbackEvent]] | None = None,
    ) -> None:
        self.account_id = account_id
        self.client_id = client_id
        self._next_valid_order_id = next_valid_order_id
        self._positions = positions or []
        self._open_orders = open_orders or []
        self._account_summary = account_summary
        self._callbacks_by_request_id = callbacks_by_request_id or {}
        self.place_order_calls = 0
        self.connected = False

    def connect(self) -> None:
        self.connected = True

    def disconnect(self) -> None:
        self.connected = False

    def ensure_next_valid_id(self, timeout_seconds: int) -> int:
        _ = timeout_seconds
        return int(self._next_valid_order_id)

    def fetch_positions(
        self, timeout_seconds: int
    ) -> tuple[list[IbkrBrokerPosition], list[IbkrCallbackEvent]]:
        _ = timeout_seconds
        events = [
            make_callback_event(
                event_type="position",
                account_id=position.account_id,
                client_id=self.client_id,
                raw_symbol=position.local_symbol,
                payload=position.to_dict(),
            )
            for position in self._positions
        ]
        events.append(
            make_callback_event(
                event_type="positionEnd",
                account_id=self.account_id,
                client_id=self.client_id,
                payload={"row_count": len(self._positions)},
            )
        )
        return deepcopy(self._positions), events

    def fetch_open_orders(
        self, timeout_seconds: int
    ) -> tuple[list[IbkrBrokerOpenOrder], list[IbkrCallbackEvent]]:
        _ = timeout_seconds
        events = [
            make_callback_event(
                event_type="openOrder",
                account_id=order.account_id,
                client_id=order.client_id,
                ib_order_id=order.ib_order_id,
                perm_id=order.perm_id,
                raw_symbol=order.local_symbol,
                status=order.status,
                payload=order.to_dict(),
            )
            for order in self._open_orders
        ]
        events.append(
            make_callback_event(
                event_type="openOrderEnd",
                account_id=self.account_id,
                client_id=self.client_id,
                payload={"row_count": len(self._open_orders)},
            )
        )
        return deepcopy(self._open_orders), events

    def fetch_account_summary(
        self, timeout_seconds: int
    ) -> tuple[IbkrAccountSummary | None, list[IbkrCallbackEvent]]:
        _ = timeout_seconds
        if self._account_summary is None:
            return None, [
                make_callback_event(
                    event_type="accountSummaryEnd",
                    account_id=self.account_id,
                    client_id=self.client_id,
                    payload={},
                )
            ]
        summary_events = [
            make_callback_event(
                event_type="accountSummary",
                account_id=self._account_summary.account_id,
                client_id=self.client_id,
                payload=self._account_summary.to_dict(),
            ),
            make_callback_event(
                event_type="accountSummaryEnd",
                account_id=self._account_summary.account_id,
                client_id=self.client_id,
                payload={},
            ),
        ]
        return deepcopy(self._account_summary), summary_events

    def place_order(
        self,
        request: IbkrTranslatedOrderRequest,
        *,
        ib_order_id: int,
        timeout_seconds: int,
    ) -> list[IbkrCallbackEvent]:
        _ = timeout_seconds
        self.place_order_calls += 1
        scripted = deepcopy(self._callbacks_by_request_id.get(request.broker_request_id, []))
        if scripted:
            return scripted
        current = datetime.now(UTC)
        return [
            make_callback_event(
                event_type="openOrder",
                event_time_utc=current,
                account_id=request.account_id,
                client_id=request.client_id,
                broker_request_id=request.broker_request_id,
                ib_order_id=ib_order_id,
                broker_contract_key=request.broker_contract_key,
                raw_symbol=request.raw_symbol,
                root=request.root,
                status="PreSubmitted",
                payload=request.to_dict(),
            ),
            make_callback_event(
                event_type="orderStatus",
                event_time_utc=current,
                account_id=request.account_id,
                client_id=request.client_id,
                broker_request_id=request.broker_request_id,
                ib_order_id=ib_order_id,
                broker_contract_key=request.broker_contract_key,
                raw_symbol=request.raw_symbol,
                root=request.root,
                status="Submitted",
                filled_quantity=0,
                remaining_quantity=request.total_quantity,
                payload=request.to_dict(),
            ),
        ]
