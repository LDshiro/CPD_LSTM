from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime
from pathlib import Path

import pandas as pd
import pytest

from cpdshadow.broker.ibkr.contracts import resolve_ibkr_contract
from cpdshadow.broker.ibkr.translator import (
    IbkrTranslationError,
    apply_buffer_bps,
    resolve_reference_price,
    round_limit_price,
    translate_order_request,
    validate_reduce_only_root,
)
from cpdshadow.config import load_yaml
from cpdshadow.instruments import load_instrument_master


def test_shadow_only_translation_keeps_limit_price_null() -> None:
    config = load_yaml(Path("config/settings.base.yml")).broker.ibkr
    translated = translate_order_request(
        intent_row=_intent_row(),
        resolved_contract=_resolved_contract(),
        broker_mode="shadow_only",
        account_id="DU0000000",
        client_id=7,
        broker_config=config,
        contracts_daily=_contracts_daily([{"trade_date": "2026-04-22", "raw_symbol": "ESM7", "settle_price": 5100.0}]),
        created_at_utc=_created_at(),
    )

    assert translated.preview_only is True
    assert translated.limit_price is None
    assert translated.order_type == "LMT"


def test_paper_translation_uses_reference_and_rounds_to_tick() -> None:
    config = load_yaml(Path("config/settings.base.yml")).broker.ibkr
    translated = translate_order_request(
        intent_row=_intent_row(side="buy"),
        resolved_contract=_resolved_contract(),
        broker_mode="paper_submit",
        account_id="DU0000000",
        client_id=7,
        broker_config=config,
        contracts_daily=_contracts_daily(
            [{"trade_date": "2026-04-22", "raw_symbol": "ESM7", "settle_price": 5100.03}]
        ),
        created_at_utc=_created_at(),
    )

    assert translated.preview_only is False
    assert translated.limit_price == 5102.75


def test_stale_reference_price_rejects_paper_submit() -> None:
    with pytest.raises(IbkrTranslationError, match="stale"):
        resolve_reference_price(
            contracts_daily=_contracts_daily(
                [{"trade_date": "2026-04-01", "raw_symbol": "ESM7", "settle_price": 5000.0}]
            ),
            raw_symbol="ESM7",
            as_of_date=date(2026, 4, 23),
            max_age_days=7,
        )


def test_reduce_only_validation_blocks_exposure_increase() -> None:
    violation = validate_reduce_only_root(
        root="ES",
        root_requests=[
            _translated_request(action="BUY", total_quantity=3),
            _translated_request(action="SELL", total_quantity=1),
        ],
        snapshot_positions=pd.DataFrame(
            [{"root": "ES", "position_contracts": 1, "raw_symbol": "ESM7"}]
        ),
    )

    assert violation == "reduce_only_increases_absolute_exposure"


def test_apply_buffer_bps_and_round_limit_price() -> None:
    buffered = apply_buffer_bps(price=100.0, action="SELL", buffer_bps=5.0)
    assert buffered == pytest.approx(99.95)
    assert round_limit_price(price=99.95, tick_size=0.25, action="SELL") == 99.75


def _resolved_contract():
    return resolve_ibkr_contract(
        root="ES",
        raw_symbol="ESM7",
        contract_master=pd.DataFrame(
            [
                {
                    "root": "ES",
                    "raw_symbol": "ESM7",
                    "exchange": "CME",
                    "currency": "USD",
                    "tick_size": 0.25,
                    "multiplier": 50.0,
                    "last_trade_date": "2027-06-18",
                }
            ]
        ),
        instrument_master=load_instrument_master(Path("config/instruments.yml")),
    )


def _intent_row(*, side: str = "buy") -> dict[str, object]:
    return {
        "order_intent_id": "oi_es_001",
        "run_id": "shadow_run_001",
        "strategy_id": "cpd_lstm",
        "execution_mode": "shadow",
        "as_of_date": date(2026, 4, 23),
        "execution_date": date(2026, 4, 24),
        "root": "ES",
        "raw_symbol": "ESM7",
        "side": side,
        "quantity": 2,
        "control_action": "run_cpd_lstm",
        "reason": "rebalance",
        "position_snapshot_id": "snapshot_001",
        "sequence_no": 1,
        "status": "not_sent",
    }


def _contracts_daily(rows: list[dict[str, object]]) -> pd.DataFrame:
    frame = pd.DataFrame(rows)
    frame["available_at_utc"] = "2026-04-22T22:00:00Z"
    frame["close_price"] = frame.get("close_price", frame.get("settle_price"))
    return frame


def _translated_request(*, action: str, total_quantity: int):
    config = load_yaml(Path("config/settings.base.yml")).broker.ibkr
    base = translate_order_request(
        intent_row=_intent_row(side="buy" if action == "BUY" else "sell"),
        resolved_contract=_resolved_contract(),
        broker_mode="shadow_only",
        account_id="DU0000000",
        client_id=7,
        broker_config=config,
        contracts_daily=_contracts_daily([{"trade_date": "2026-04-22", "raw_symbol": "ESM7", "settle_price": 5100.0}]),
        created_at_utc=_created_at(),
    )
    return replace(base, action=action, total_quantity=total_quantity)


def _created_at() -> datetime:
    return datetime(2026, 4, 23, tzinfo=UTC)
