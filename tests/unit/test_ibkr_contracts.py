from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from cpdshadow.broker.ibkr.contracts import (
    IbkrContractResolutionError,
    resolve_broker_snapshot_contract,
    resolve_ibkr_contract,
)
from cpdshadow.instruments import load_instrument_master


def test_resolve_ibkr_contract_success() -> None:
    resolved = resolve_ibkr_contract(
        root="ES",
        raw_symbol="ESM7",
        contract_master=_contract_master(
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

    assert resolved.exchange == "CME"
    assert resolved.currency == "USD"
    assert resolved.local_symbol == "ESM7"
    assert resolved.min_tick == 0.25
    assert resolved.broker_contract_key == "IBKR|FUT|CME|ESM7|USD"


def test_resolve_ibkr_contract_rejects_unknown_symbol() -> None:
    with pytest.raises(IbkrContractResolutionError, match="contract_master does not contain"):
        resolve_ibkr_contract(
            root="ES",
            raw_symbol="ESU7",
            contract_master=_contract_master(
                [
                    {
                        "root": "ES",
                        "raw_symbol": "ESM7",
                        "exchange": "CME",
                        "currency": "USD",
                    }
                ]
            ),
            instrument_master=load_instrument_master(Path("config/instruments.yml")),
        )


def test_resolve_broker_snapshot_contract_rejects_ambiguous_mapping() -> None:
    with pytest.raises(IbkrContractResolutionError, match="ambiguous broker snapshot contract mapping"):
        resolve_broker_snapshot_contract(
            local_symbol="ESM7",
            exchange=None,
            contract_master=_contract_master(
                [
                    {"root": "ES", "raw_symbol": "ESM7", "exchange": "CME", "currency": "USD"},
                    {"root": "MES", "raw_symbol": "ESM7", "exchange": "CME", "currency": "USD"},
                ]
            ),
        )


def _contract_master(rows: list[dict[str, object]]) -> pd.DataFrame:
    return pd.DataFrame(rows)
