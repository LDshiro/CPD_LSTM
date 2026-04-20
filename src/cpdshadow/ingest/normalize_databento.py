from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pandas as pd

from cpdshadow.ids import stable_sha256_hex
from cpdshadow.instruments import InstrumentMaster


def normalize_contract_master(
    definition_df: pd.DataFrame,
    instrument_master: InstrumentMaster,
    *,
    dataset: str,
    ingested_at_utc: datetime | None = None,
) -> pd.DataFrame:
    ingested_at = ingested_at_utc or datetime.now(timezone.utc)
    if definition_df.empty:
        return pd.DataFrame(columns=[
            "dataset",
            "instrument_id",
            "raw_symbol",
            "root",
            "exchange",
            "currency",
            "expiration_date",
            "last_trade_date",
            "first_trade_date",
            "multiplier",
            "tick_size",
            "instrument_class",
            "valid_from_utc",
            "valid_to_utc",
            "definition_hash",
            "ingested_at_utc",
        ])

    allowed = {instrument.root: instrument for instrument in instrument_master.instruments}
    rows: list[dict[str, Any]] = []
    for _, row in definition_df.iterrows():
        if not _is_outright_future(row):
            continue
        root = _resolve_root(row, allowed)
        if root is None:
            continue
        valid_from = _coerce_timestamp(_first_present(row, "ts_event", "ts_recv", "ts_effective"))
        raw_symbol = str(row.get("raw_symbol"))
        instrument_id = int(row.get("instrument_id"))
        expiration_ts = _coerce_timestamp(_first_present(row, "expiration", "last_trade_ts", "last_trade_date"))
        activation_ts = _coerce_timestamp(_first_present(row, "activation", "first_trade_date", "first_trade_ts"))
        multiplier = _coerce_float(_first_present(row, "contract_multiplier", "multiplier"))
        tick_size = _coerce_float(_first_present(row, "min_price_increment", "tick_size"))
        if multiplier is None:
            multiplier = float(allowed[root].quote_multiplier_to_usd_notional)
        if tick_size is None:
            tick_size = float(allowed[root].min_price_increment)
        rows.append({
            "dataset": dataset,
            "instrument_id": instrument_id,
            "raw_symbol": raw_symbol,
            "root": root,
            "exchange": _coerce_string(_first_present(row, "exchange")),
            "currency": _coerce_string(_first_present(row, "currency", "settl_currency")),
            "expiration_date": expiration_ts.date() if expiration_ts is not None else None,
            "last_trade_date": expiration_ts.date() if expiration_ts is not None else None,
            "first_trade_date": activation_ts.date() if activation_ts is not None else None,
            "multiplier": multiplier,
            "tick_size": tick_size,
            "instrument_class": _canonical_instrument_class(row),
            "valid_from_utc": valid_from,
            "valid_to_utc": None,
            "definition_hash": stable_sha256_hex({
                "dataset": dataset,
                "instrument_id": instrument_id,
                "raw_symbol": raw_symbol,
                "root": root,
                "valid_from_utc": valid_from,
                "exchange": _coerce_string(_first_present(row, "exchange")),
                "currency": _coerce_string(_first_present(row, "currency", "settl_currency")),
                "expiration": expiration_ts,
                "activation": activation_ts,
                "multiplier": multiplier,
                "tick_size": tick_size,
            }),
            "ingested_at_utc": ingested_at,
            "_sort_ts_event": _coerce_timestamp(_first_present(row, "ts_event")),
            "_sort_ts_recv": _coerce_timestamp(_first_present(row, "ts_recv")),
        })

    if not rows:
        return pd.DataFrame(columns=[
            "dataset",
            "instrument_id",
            "raw_symbol",
            "root",
            "exchange",
            "currency",
            "expiration_date",
            "last_trade_date",
            "first_trade_date",
            "multiplier",
            "tick_size",
            "instrument_class",
            "valid_from_utc",
            "valid_to_utc",
            "definition_hash",
            "ingested_at_utc",
        ])

    df = pd.DataFrame(rows)
    df = df.sort_values(["dataset", "raw_symbol", "valid_from_utc", "_sort_ts_event", "_sort_ts_recv", "definition_hash"])
    df = df.drop_duplicates(subset=["dataset", "raw_symbol", "valid_from_utc"], keep="last")
    df["valid_to_utc"] = df.groupby(["dataset", "raw_symbol"])["valid_from_utc"].shift(-1)
    return df.drop(columns=["_sort_ts_event", "_sort_ts_recv"]).reset_index(drop=True)


def normalize_contracts_daily(
    statistics_df: pd.DataFrame,
    ohlcv_df: pd.DataFrame,
    contract_master_df: pd.DataFrame,
    instrument_master: InstrumentMaster,
    *,
    dataset: str,
    snapshot_id: str,
    ingested_at_utc: datetime | None = None,
) -> pd.DataFrame:
    ingested_at = ingested_at_utc or datetime.now(timezone.utc)
    lookup = _build_contract_lookup(contract_master_df)
    stats_wide = _normalize_statistics(statistics_df, lookup)
    ohlcv_wide = _normalize_ohlcv(ohlcv_df, lookup)

    if stats_wide.empty and ohlcv_wide.empty:
        return pd.DataFrame(columns=[
            "trade_date",
            "root",
            "raw_symbol",
            "dataset",
            "instrument_id",
            "open_price",
            "high_price",
            "low_price",
            "close_price",
            "settle_price",
            "settle_status",
            "volume",
            "open_interest",
            "price_source",
            "volume_source",
            "available_at_utc",
            "ingested_at_utc",
            "quality_flags",
            "override_id",
            "snapshot_id",
        ])

    merged = stats_wide.merge(
        ohlcv_wide,
        on=["trade_date", "root", "raw_symbol", "dataset", "instrument_id"],
        how="outer",
        suffixes=("_stats", "_ohlcv"),
    )
    merged["quality_flags"] = merged.apply(_build_quality_flags, axis=1)
    for field in ["open_price", "high_price", "low_price", "close_price"]:
        merged[field] = merged[f"{field}_stats"].combine_first(merged[f"{field}_ohlcv"])
    merged["volume"] = merged["volume_stats"].combine_first(merged["volume_ohlcv"])
    merged["open_interest"] = merged["open_interest_stats"]
    merged["volume_source"] = merged.apply(_volume_source, axis=1)
    merged["settle_price"] = merged["settle_price_stats"]
    merged["settle_status"] = merged["settle_status_stats"].fillna("missing")
    fallback_mask = merged["settle_price"].isna() & merged["close_price"].notna()
    merged.loc[fallback_mask, "settle_price"] = merged.loc[fallback_mask, "close_price"]
    merged.loc[fallback_mask, "settle_status"] = "close_fallback"
    merged.loc[fallback_mask, "quality_flags"] = merged.loc[fallback_mask, "quality_flags"].apply(
        lambda flags: _sorted_flags(flags + ["close_fallback_used"])
    )
    missing_mask = merged["settle_price"].isna()
    merged.loc[missing_mask, "settle_status"] = "missing"
    merged.loc[missing_mask, "quality_flags"] = merged.loc[missing_mask, "quality_flags"].apply(
        lambda flags: _sorted_flags(flags + ["missing_price"])
    )
    merged["price_source"] = merged.apply(_price_source, axis=1)
    merged["available_at_utc"] = merged[["available_at_utc_stats", "available_at_utc_ohlcv"]].max(axis=1)
    merged["ingested_at_utc"] = ingested_at
    merged["override_id"] = None
    merged["snapshot_id"] = snapshot_id
    merged["quality_flags"] = merged["quality_flags"].apply(_sorted_flags)
    merged = merged[[
        "trade_date",
        "root",
        "raw_symbol",
        "dataset",
        "instrument_id",
        "open_price",
        "high_price",
        "low_price",
        "close_price",
        "settle_price",
        "settle_status",
        "volume",
        "open_interest",
        "price_source",
        "volume_source",
        "available_at_utc",
        "ingested_at_utc",
        "quality_flags",
        "override_id",
        "snapshot_id",
    ]].sort_values(["trade_date", "root", "raw_symbol"]).reset_index(drop=True)
    if merged.duplicated(subset=["trade_date", "root", "raw_symbol"]).any():
        raise ValueError("duplicate (trade_date, root, raw_symbol) rows in contracts_daily")
    return merged


def _normalize_statistics(df: pd.DataFrame, lookup: dict[tuple[int, str], dict[str, Any]]) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(columns=[
            "trade_date",
            "root",
            "raw_symbol",
            "dataset",
            "instrument_id",
            "open_price_stats",
            "high_price_stats",
            "low_price_stats",
            "close_price_stats",
            "settle_price_stats",
            "settle_status_stats",
            "volume_stats",
            "open_interest_stats",
            "available_at_utc_stats",
            "quality_flags_stats",
        ])
    prepared = []
    for _, row in df.iterrows():
        raw_symbol = _coerce_string(_first_present(row, "raw_symbol", "symbol"))
        instrument_id = int(_first_present(row, "instrument_id"))
        key = (instrument_id, raw_symbol)
        if key not in lookup:
            continue
        trade_date = _statistics_trade_date(row)
        target = _stat_type_target(_first_present(row, "stat_type"))
        if target is None:
            continue
        prepared.append({
            "trade_date": trade_date,
            "root": lookup[key]["root"],
            "raw_symbol": raw_symbol,
            "dataset": lookup[key]["dataset"],
            "instrument_id": instrument_id,
            "target_field": target,
            "value": _stat_value(target, row),
            "available_at_utc": _coerce_timestamp(_first_present(row, "ts_recv", "ts_event", "ts_ref")),
            "is_final_settlement": _settlement_is_final(row),
            "quality_flags": ["stats_missing_ts_ref"] if _missing_ts_ref(row) else [],
            "_sort_ts_event": _coerce_timestamp(_first_present(row, "ts_event")),
            "_sort_ts_recv": _coerce_timestamp(_first_present(row, "ts_recv")),
            "_sequence": int(_first_present(row, "sequence", default=0)),
        })
    if not prepared:
        return pd.DataFrame(columns=["trade_date", "root", "raw_symbol", "dataset", "instrument_id"])
    long_df = pd.DataFrame(prepared)
    long_df["settlement_rank"] = long_df.apply(
        lambda row: 1 if row["target_field"] == "settle_price" and row["is_final_settlement"] else 0,
        axis=1,
    )
    long_df = long_df.sort_values(
        [
            "trade_date",
            "root",
            "raw_symbol",
            "target_field",
            "settlement_rank",
            "_sort_ts_event",
            "_sort_ts_recv",
            "_sequence",
        ]
    )
    long_df = long_df.drop_duplicates(
        subset=["trade_date", "root", "raw_symbol", "target_field"],
        keep="last",
    )

    records: list[dict[str, Any]] = []
    group_cols = ["trade_date", "root", "raw_symbol", "dataset", "instrument_id"]
    for group_key, group in long_df.groupby(group_cols, dropna=False):
        trade_date, root, raw_symbol, dataset, instrument_id = group_key
        record: dict[str, Any] = {
            "trade_date": trade_date,
            "root": root,
            "raw_symbol": raw_symbol,
            "dataset": dataset,
            "instrument_id": instrument_id,
            "open_price_stats": None,
            "high_price_stats": None,
            "low_price_stats": None,
            "close_price_stats": None,
            "settle_price_stats": None,
            "settle_status_stats": None,
            "volume_stats": None,
            "open_interest_stats": None,
            "available_at_utc_stats": None,
        }
        flags: list[str] = []
        for _, item in group.iterrows():
            record[f"{item['target_field']}_stats"] = item["value"]
            if item["target_field"] == "settle_price":
                record["settle_status_stats"] = "final" if item["is_final_settlement"] else "preliminary"
            if record["available_at_utc_stats"] is None or item["available_at_utc"] > record["available_at_utc_stats"]:
                record["available_at_utc_stats"] = item["available_at_utc"]
            flags.extend(item["quality_flags"])
        record["quality_flags_stats"] = _sorted_flags(flags)
        records.append(record)
    return pd.DataFrame(records)


def _normalize_ohlcv(df: pd.DataFrame, lookup: dict[tuple[int, str], dict[str, Any]]) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(columns=[
            "trade_date",
            "root",
            "raw_symbol",
            "dataset",
            "instrument_id",
            "open_price_ohlcv",
            "high_price_ohlcv",
            "low_price_ohlcv",
            "close_price_ohlcv",
            "volume_ohlcv",
            "available_at_utc_ohlcv",
        ])
    records = []
    for _, row in df.iterrows():
        raw_symbol = _coerce_string(_first_present(row, "raw_symbol", "symbol"))
        instrument_id = int(_first_present(row, "instrument_id"))
        key = (instrument_id, raw_symbol)
        if key not in lookup:
            continue
        ts_event = _coerce_timestamp(_first_present(row, "ts_event"))
        records.append({
            "trade_date": ts_event.date() if ts_event is not None else None,
            "root": lookup[key]["root"],
            "raw_symbol": raw_symbol,
            "dataset": lookup[key]["dataset"],
            "instrument_id": instrument_id,
            "open_price_ohlcv": _coerce_float(_first_present(row, "open")),
            "high_price_ohlcv": _coerce_float(_first_present(row, "high")),
            "low_price_ohlcv": _coerce_float(_first_present(row, "low")),
            "close_price_ohlcv": _coerce_float(_first_present(row, "close")),
            "volume_ohlcv": _coerce_float(_first_present(row, "volume")),
            "available_at_utc_ohlcv": _coerce_timestamp(_first_present(row, "ts_recv", "ts_event")),
        })
    if not records:
        return pd.DataFrame(columns=[
            "trade_date",
            "root",
            "raw_symbol",
            "dataset",
            "instrument_id",
            "open_price_ohlcv",
            "high_price_ohlcv",
            "low_price_ohlcv",
            "close_price_ohlcv",
            "volume_ohlcv",
            "available_at_utc_ohlcv",
        ])
    df_out = pd.DataFrame(records)
    df_out = df_out.sort_values(["trade_date", "root", "raw_symbol", "available_at_utc_ohlcv"])
    return df_out.drop_duplicates(subset=["trade_date", "root", "raw_symbol"], keep="last").reset_index(drop=True)


def _build_contract_lookup(contract_master_df: pd.DataFrame) -> dict[tuple[int, str], dict[str, Any]]:
    lookup: dict[tuple[int, str], dict[str, Any]] = {}
    for _, row in contract_master_df.iterrows():
        lookup[(int(row["instrument_id"]), str(row["raw_symbol"]))] = {
            "root": str(row["root"]),
            "dataset": str(row["dataset"]),
            "expiration_date": row.get("expiration_date"),
        }
    return lookup


def _resolve_root(row: pd.Series, allowed: dict[str, Any]) -> str | None:
    asset = _coerce_string(_first_present(row, "asset", "group"))
    if asset in allowed:
        return asset
    raw_symbol = _coerce_string(_first_present(row, "raw_symbol", "symbol"))
    if raw_symbol is None:
        return None
    matches = [root for root in allowed if raw_symbol.startswith(root)]
    if not matches:
        return None
    matches.sort(key=len, reverse=True)
    longest = matches[0]
    if len(matches) == 1 or len(longest) > len(matches[1]):
        return longest
    return None


def _canonical_instrument_class(row: pd.Series) -> str:
    raw = _first_present(row, "instrument_class", "security_type")
    if hasattr(raw, "name"):
        return str(raw.name)
    return str(raw)


def _is_outright_future(row: pd.Series) -> bool:
    instrument_class = _canonical_instrument_class(row).upper()
    security_type = _coerce_string(_first_present(row, "security_type"))
    leg_count = _first_present(row, "leg_count", default=0)
    if security_type is not None and security_type.upper() not in {"FUT", "FUTURE"}:
        return False
    if instrument_class in {"F", "FUTURE"} and int(leg_count or 0) == 0:
        return True
    return False


def _statistics_trade_date(row: pd.Series) -> datetime.date:
    ts_ref = _coerce_timestamp(_first_present(row, "ts_ref"))
    if ts_ref is not None:
        return ts_ref.date()
    ts_event = _coerce_timestamp(_first_present(row, "ts_event", "ts_recv"))
    if ts_event is None:
        raise ValueError("statistics row is missing both ts_ref and ts_event")
    return ts_event.date()


def _missing_ts_ref(row: pd.Series) -> bool:
    return _coerce_timestamp(_first_present(row, "ts_ref")) is None


def _stat_type_target(value: Any) -> str | None:
    stat_type = int(value)
    mapping = {
        1: "open_price",
        3: "settle_price",
        4: "low_price",
        5: "high_price",
        6: "volume",
        9: "open_interest",
        11: "close_price",
    }
    return mapping.get(stat_type)


def _stat_value(target_field: str, row: pd.Series) -> float | None:
    if target_field in {"volume", "open_interest"}:
        return _coerce_float(_first_present(row, "quantity", "size"))
    return _coerce_float(_first_present(row, "price", target_field))


def _settlement_is_final(row: pd.Series) -> bool:
    for field in ("is_final", "settlement_is_final", "final"):
        value = row.get(field)
        if pd.notna(value):
            return bool(value)
    text = _coerce_string(_first_present(row, "stat_flags_text"))
    if text is not None:
        upper = text.upper()
        if "FINAL" in upper:
            return True
        if "PRELIM" in upper:
            return False
    return False


def _build_quality_flags(row: pd.Series) -> list[str]:
    flags = []
    flags.extend(row.get("quality_flags_stats") or [])
    return _sorted_flags(flags)


def _price_source(row: pd.Series) -> str:
    status = row["settle_status"]
    if status in {"final", "preliminary"}:
        return "statistics"
    if status == "close_fallback":
        return "ohlcv"
    return "missing"


def _volume_source(row: pd.Series) -> str:
    if pd.notna(row["volume_stats"]):
        return "statistics"
    if pd.notna(row["volume_ohlcv"]):
        return "ohlcv"
    return "missing"


def _sorted_flags(flags: list[str]) -> list[str]:
    return sorted(set(flag for flag in flags if flag))


def _coerce_timestamp(value: Any) -> datetime | None:
    if value is None or (isinstance(value, float) and pd.isna(value)) or value is pd.NaT:
        return None
    ts = pd.Timestamp(value)
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    else:
        ts = ts.tz_convert("UTC")
    return ts.to_pydatetime()


def _coerce_float(value: Any) -> float | None:
    if value is None or pd.isna(value):
        return None
    return float(value)


def _coerce_string(value: Any) -> str | None:
    if value is None or pd.isna(value):
        return None
    return str(value)


def _first_present(row: pd.Series, *names: str, default: Any = None) -> Any:
    for name in names:
        if name in row and pd.notna(row[name]):
            return row[name]
    return default
