from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from cpdshadow.ingest.normalize_databento import normalize_contract_master, normalize_contracts_daily
from cpdshadow.ingest.quality import run_quality_checks
from cpdshadow.instruments import load_instrument_master


def _instrument_master():
    return load_instrument_master(Path("config/instruments.yml"))


def _definition_df() -> pd.DataFrame:
    return pd.DataFrame([
        {
            "raw_symbol": "ESM4",
            "instrument_id": 1,
            "instrument_class": "F",
            "security_type": "FUT",
            "asset": "ES",
            "exchange": "CME",
            "currency": "USD",
            "expiration": "2024-06-21T13:30:00Z",
            "activation": "2023-09-15T00:00:00Z",
            "contract_multiplier": None,
            "min_price_increment": None,
            "ts_event": "2024-01-02T00:00:00Z",
            "ts_recv": "2024-01-02T00:00:01Z",
        },
        {
            "raw_symbol": "ESM4-ESU4",
            "instrument_id": 2,
            "instrument_class": "S",
            "security_type": "FUT",
            "asset": "ES",
            "exchange": "CME",
            "currency": "USD",
            "expiration": "2024-09-20T13:30:00Z",
            "activation": "2024-01-02T00:00:00Z",
            "contract_multiplier": 1,
            "min_price_increment": 0.25,
            "ts_event": "2024-01-02T00:00:00Z",
            "ts_recv": "2024-01-02T00:00:01Z",
        },
        {
            "raw_symbol": "NQM4",
            "instrument_id": 3,
            "instrument_class": "F",
            "security_type": "FUT",
            "asset": "NQ",
            "exchange": "CME",
            "currency": "USD",
            "expiration": "2024-06-21T13:30:00Z",
            "activation": "2023-09-15T00:00:00Z",
            "contract_multiplier": 20,
            "min_price_increment": 0.25,
            "ts_event": "2024-01-02T00:00:00Z",
            "ts_recv": "2024-01-02T00:00:01Z",
        },
    ])


def test_definition_normalizer_filters_spreads_and_falls_back_to_config() -> None:
    master = normalize_contract_master(_definition_df(), _instrument_master(), dataset="GLBX.MDP3")
    assert list(master["raw_symbol"]) == ["ESM4", "NQM4"]
    es_row = master.loc[master["raw_symbol"] == "ESM4"].iloc[0]
    assert es_row["multiplier"] == 50.0
    assert es_row["tick_size"] == 0.25


def test_statistics_pivot_uses_ts_ref_and_final_settlement() -> None:
    master = normalize_contract_master(_definition_df(), _instrument_master(), dataset="GLBX.MDP3")
    statistics_df = pd.DataFrame([
        {
            "raw_symbol": "ESM4",
            "instrument_id": 1,
            "ts_event": "2024-01-03T22:00:00Z",
            "ts_recv": "2024-01-03T22:00:01Z",
            "ts_ref": "2024-01-02T00:00:00Z",
            "stat_type": 3,
            "price": 5000.0,
            "quantity": None,
            "sequence": 1,
            "is_final": False,
        },
        {
            "raw_symbol": "ESM4",
            "instrument_id": 1,
            "ts_event": "2024-01-03T23:00:00Z",
            "ts_recv": "2024-01-03T23:00:01Z",
            "ts_ref": "2024-01-02T00:00:00Z",
            "stat_type": 3,
            "price": 5001.0,
            "quantity": None,
            "sequence": 2,
            "is_final": True,
        },
        {
            "raw_symbol": "ESM4",
            "instrument_id": 1,
            "ts_event": "2024-01-03T23:00:00Z",
            "ts_recv": "2024-01-03T23:00:01Z",
            "ts_ref": "2024-01-02T00:00:00Z",
            "stat_type": 6,
            "price": None,
            "quantity": 12345,
            "sequence": 3,
        },
        {
            "raw_symbol": "ESM4",
            "instrument_id": 1,
            "ts_event": "2024-01-03T23:00:00Z",
            "ts_recv": "2024-01-03T23:00:01Z",
            "ts_ref": "2024-01-02T00:00:00Z",
            "stat_type": 11,
            "price": 4999.0,
            "quantity": None,
            "sequence": 4,
        },
    ])
    result = normalize_contracts_daily(
        statistics_df,
        pd.DataFrame(),
        master,
        _instrument_master(),
        dataset="GLBX.MDP3",
        snapshot_id="snapshot_test",
        ingested_at_utc=datetime.now(timezone.utc),
    )
    row = result.iloc[0]
    assert row["trade_date"].isoformat() == "2024-01-02"
    assert row["settle_price"] == 5001.0
    assert row["settle_status"] == "final"
    assert row["volume"] == 12345.0


def test_ohlcv_fallback_does_not_override_official_settlement() -> None:
    master = normalize_contract_master(_definition_df(), _instrument_master(), dataset="GLBX.MDP3")
    statistics_df = pd.DataFrame([
        {
            "raw_symbol": "ESM4",
            "instrument_id": 1,
            "ts_event": "2024-01-03T23:00:00Z",
            "ts_recv": "2024-01-03T23:00:01Z",
            "ts_ref": "2024-01-02T00:00:00Z",
            "stat_type": 3,
            "price": 5000.0,
            "quantity": None,
            "sequence": 1,
            "is_final": True,
        },
    ])
    ohlcv_df = pd.DataFrame([
        {
            "raw_symbol": "ESM4",
            "instrument_id": 1,
            "ts_event": "2024-01-02T23:59:59Z",
            "open": 4990.0,
            "high": 5010.0,
            "low": 4980.0,
            "close": 4995.0,
            "volume": 10000,
        }
    ])
    result = normalize_contracts_daily(
        statistics_df,
        ohlcv_df,
        master,
        _instrument_master(),
        dataset="GLBX.MDP3",
        snapshot_id="snapshot_test",
    )
    row = result.iloc[0]
    assert row["settle_price"] == 5000.0
    assert row["price_source"] == "statistics"
    assert row["close_price"] == 4995.0


def test_missing_settlement_surfaces_warning() -> None:
    master = normalize_contract_master(_definition_df(), _instrument_master(), dataset="GLBX.MDP3")
    ohlcv_df = pd.DataFrame([
        {
            "raw_symbol": "ESM4",
            "instrument_id": 1,
            "ts_event": "2024-01-02T23:59:59Z",
            "open": 4990.0,
            "high": 5010.0,
            "low": 4980.0,
            "close": 4995.0,
            "volume": 10000,
        }
    ])
    result = normalize_contracts_daily(
        pd.DataFrame(),
        ohlcv_df,
        master,
        _instrument_master(),
        dataset="GLBX.MDP3",
        snapshot_id="snapshot_test",
    )
    report = run_quality_checks(
        snapshot_id="snapshot_test",
        instrument_master=_instrument_master(),
        file_registry_df=pd.DataFrame(),
        contract_master_df=master,
        contracts_daily_df=result,
        repo_root=Path("."),
    )
    assert any(issue.code == "missing_settlement" for issue in report.issues)


def test_duplicate_daily_key_raises_in_qa() -> None:
    duplicated = pd.DataFrame([
        {
            "trade_date": pd.Timestamp("2024-01-02").date(),
            "root": "ES",
            "raw_symbol": "ESM4",
            "dataset": "GLBX.MDP3",
            "instrument_id": 1,
            "open_price": 1.0,
            "high_price": 1.0,
            "low_price": 1.0,
            "close_price": 1.0,
            "settle_price": 1.0,
            "settle_status": "final",
            "volume": 1.0,
            "open_interest": 1.0,
            "price_source": "statistics",
            "volume_source": "statistics",
            "available_at_utc": pd.Timestamp("2024-01-02T00:00:00Z"),
            "ingested_at_utc": pd.Timestamp("2024-01-02T00:00:00Z"),
            "quality_flags": [],
            "override_id": None,
            "snapshot_id": "snapshot_test",
        },
        {
            "trade_date": pd.Timestamp("2024-01-02").date(),
            "root": "ES",
            "raw_symbol": "ESM4",
            "dataset": "GLBX.MDP3",
            "instrument_id": 1,
            "open_price": 1.0,
            "high_price": 1.0,
            "low_price": 1.0,
            "close_price": 1.0,
            "settle_price": 1.0,
            "settle_status": "final",
            "volume": 1.0,
            "open_interest": 1.0,
            "price_source": "statistics",
            "volume_source": "statistics",
            "available_at_utc": pd.Timestamp("2024-01-02T00:00:00Z"),
            "ingested_at_utc": pd.Timestamp("2024-01-02T00:00:00Z"),
            "quality_flags": [],
            "override_id": None,
            "snapshot_id": "snapshot_test",
        },
    ])
    report = run_quality_checks(
        snapshot_id="snapshot_test",
        instrument_master=_instrument_master(),
        file_registry_df=pd.DataFrame(),
        contract_master_df=normalize_contract_master(_definition_df(), _instrument_master(), dataset="GLBX.MDP3"),
        contracts_daily_df=duplicated,
        repo_root=Path("."),
    )
    assert any(issue.code == "duplicate_daily_key" for issue in report.issues)
