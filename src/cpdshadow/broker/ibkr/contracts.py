from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

import pandas as pd

from cpdshadow.broker.ibkr.models import IbkrResolvedContract
from cpdshadow.instruments import InstrumentMaster


class IbkrContractResolutionError(ValueError):
    pass


@dataclass(frozen=True)
class ResolvedBrokerSnapshotContract:
    broker_contract_key: str | None
    raw_symbol: str | None
    root: str | None


def resolve_ibkr_contract(
    *,
    root: str,
    raw_symbol: str,
    contract_master: pd.DataFrame,
    instrument_master: InstrumentMaster,
) -> IbkrResolvedContract:
    instrument = _instrument_lookup(instrument_master).get(root)
    if instrument is None:
        raise IbkrContractResolutionError(f"unknown root {root!r} in config/instruments.yml")
    matched = _latest_contract_rows(contract_master, raw_symbol=raw_symbol, root=root)
    if matched.empty:
        raise IbkrContractResolutionError(
            f"contract_master does not contain root={root!r}, raw_symbol={raw_symbol!r}"
        )
    if matched["exchange"].dropna().astype(str).nunique() > 1:
        exchanges = sorted(set(matched["exchange"].dropna().astype(str)))
        raise IbkrContractResolutionError(
            f"ambiguous exchange mapping for {root}/{raw_symbol}: {exchanges}"
        )

    row = matched.iloc[-1]
    currency = (
        str(row["currency"])
        if "currency" in matched.columns and pd.notna(row.get("currency"))
        else instrument.currency
    )
    exchange = (
        str(row["exchange"])
        if "exchange" in matched.columns and pd.notna(row.get("exchange"))
        else instrument.exchange
    )
    last_trade_date = _first_date(row, "last_trade_date", "expiration_date")
    last_trade_token = last_trade_date.strftime("%Y%m%d") if last_trade_date is not None else None
    broker_contract_key = make_broker_contract_key(
        exchange=exchange,
        raw_symbol=raw_symbol,
        currency=currency,
    )
    contract_fields = {
        "secType": "FUT",
        "symbol": root,
        "localSymbol": raw_symbol,
        "exchange": exchange,
        "currency": currency,
        "lastTradeDateOrContractMonth": last_trade_token,
        "multiplier": (
            str(row["multiplier"])
            if "multiplier" in matched.columns and pd.notna(row.get("multiplier"))
            else None
        ),
    }
    return IbkrResolvedContract(
        root=root,
        raw_symbol=raw_symbol,
        exchange=exchange,
        currency=currency,
        local_symbol=raw_symbol,
        last_trade_date=last_trade_date,
        multiplier=_coerce_float(row.get("multiplier")),
        min_tick=_resolve_min_tick(row, instrument),
        broker_contract_key=broker_contract_key,
        contract_fields={key: value for key, value in contract_fields.items() if value is not None},
    )


def resolve_broker_snapshot_contract(
    *,
    local_symbol: str | None,
    exchange: str | None,
    contract_master: pd.DataFrame,
) -> ResolvedBrokerSnapshotContract:
    if local_symbol is None or not str(local_symbol).strip():
        return ResolvedBrokerSnapshotContract(None, None, None)
    matched = _latest_contract_rows(contract_master, raw_symbol=str(local_symbol), root=None)
    if matched.empty:
        return ResolvedBrokerSnapshotContract(None, None, None)
    if exchange is not None and "exchange" in matched.columns:
        exact_exchange = matched[matched["exchange"].astype(str) == str(exchange)]
        if not exact_exchange.empty:
            matched = exact_exchange
    unique_pairs = matched[["root", "raw_symbol"]].drop_duplicates()
    if len(unique_pairs) != 1:
        raise IbkrContractResolutionError(
            f"ambiguous broker snapshot contract mapping for local_symbol={local_symbol!r}"
        )
    row = matched.iloc[-1]
    resolved_exchange = (
        str(row["exchange"])
        if "exchange" in matched.columns and pd.notna(row.get("exchange"))
        else str(exchange) if exchange is not None else "UNKNOWN"
    )
    currency = (
        str(row["currency"])
        if "currency" in matched.columns and pd.notna(row.get("currency"))
        else "USD"
    )
    return ResolvedBrokerSnapshotContract(
        broker_contract_key=make_broker_contract_key(
            exchange=resolved_exchange,
            raw_symbol=str(row["raw_symbol"]),
            currency=currency,
        ),
        raw_symbol=str(row["raw_symbol"]),
        root=str(row["root"]),
    )


def make_broker_contract_key(*, exchange: str, raw_symbol: str, currency: str) -> str:
    return f"IBKR|FUT|{exchange}|{raw_symbol}|{currency}"


def _latest_contract_rows(
    contract_master: pd.DataFrame,
    *,
    raw_symbol: str,
    root: str | None,
) -> pd.DataFrame:
    if contract_master.empty:
        return contract_master.copy()
    working = contract_master.copy()
    working = working[working["raw_symbol"].astype(str) == str(raw_symbol)]
    if root is not None:
        working = working[working["root"].astype(str) == str(root)]
    if working.empty:
        return working
    if "valid_from_utc" in working.columns:
        working = working.sort_values(["valid_from_utc", "raw_symbol"], kind="stable")
    return working.reset_index(drop=True)


def _instrument_lookup(instrument_master: InstrumentMaster) -> dict[str, Any]:
    return {instrument.root: instrument for instrument in instrument_master.instruments}


def _resolve_min_tick(row: pd.Series, instrument: Any) -> float:
    if pd.notna(row.get("tick_size")):
        return float(row["tick_size"])
    return float(instrument.min_price_increment)


def _coerce_float(value: object) -> float | None:
    if value is None or pd.isna(value):
        return None
    return float(value)


def _first_date(row: pd.Series, *columns: str) -> date | None:
    for column in columns:
        if column in row.index and pd.notna(row.get(column)):
            return pd.Timestamp(row[column]).date()
    return None
