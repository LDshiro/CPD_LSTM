from __future__ import annotations

from datetime import date
from pathlib import Path
import shutil

import pandas as pd
from typer.testing import CliRunner

from cpdshadow.cli import app
from cpdshadow.storage.parquet_io import read_parquet_dataset, write_parquet_dataset


def _contract(
    *,
    raw_symbol: str,
    instrument_id: int,
    last_trade_date: date,
) -> dict[str, object]:
    return {
        "dataset": "GLBX.MDP3",
        "instrument_id": instrument_id,
        "raw_symbol": raw_symbol,
        "root": "ES",
        "exchange": "CME",
        "currency": "USD",
        "expiration_date": last_trade_date,
        "last_trade_date": last_trade_date,
        "first_trade_date": date(2024, 1, 2),
        "multiplier": 50.0,
        "tick_size": 0.25,
        "instrument_class": "F",
        "valid_from_utc": pd.Timestamp("2024-01-01T00:00:00Z"),
        "valid_to_utc": None,
        "definition_hash": f"hash-{raw_symbol}",
        "ingested_at_utc": pd.Timestamp("2024-01-01T00:00:00Z"),
    }


def _daily(
    *,
    trade_date: date,
    raw_symbol: str,
    instrument_id: int,
    volume: float,
    settle: float,
) -> dict[str, object]:
    return {
        "trade_date": trade_date,
        "root": "ES",
        "raw_symbol": raw_symbol,
        "dataset": "GLBX.MDP3",
        "instrument_id": instrument_id,
        "open_price": settle,
        "high_price": settle,
        "low_price": settle,
        "close_price": settle,
        "settle_price": settle,
        "settle_status": "final",
        "volume": volume,
        "open_interest": 1000.0,
        "price_source": "statistics",
        "volume_source": "statistics",
        "available_at_utc": pd.Timestamp(f"{trade_date.isoformat()}T23:00:00Z"),
        "ingested_at_utc": pd.Timestamp(f"{trade_date.isoformat()}T23:30:00Z"),
        "quality_flags": [],
        "override_id": None,
        "snapshot_id": "snapshot_test",
    }


def _copy_repo_config(tmp_path: Path) -> None:
    shutil.copytree(Path("config"), tmp_path / "config")
    (tmp_path / "data" / "curated").mkdir(parents=True, exist_ok=True)
    (tmp_path / "data" / "meta").mkdir(parents=True, exist_ok=True)
    (tmp_path / "artifacts" / "reports" / "wp5_roll_engine").mkdir(parents=True, exist_ok=True)


def _write_source_snapshot(tmp_path: Path) -> None:
    snapshot_id = "snapshot_test"
    contract_master = pd.DataFrame([
        _contract(raw_symbol="ESH4", instrument_id=1, last_trade_date=date(2024, 2, 15)),
        _contract(raw_symbol="ESM4", instrument_id=2, last_trade_date=date(2024, 5, 15)),
        _contract(raw_symbol="ESU4", instrument_id=3, last_trade_date=date(2024, 8, 15)),
    ])
    trade_dates = [
        date(2024, 1, 2),
        date(2024, 1, 3),
        date(2024, 1, 4),
        date(2024, 1, 5),
        date(2024, 1, 8),
    ]
    contracts_daily = pd.DataFrame([
        _daily(trade_date=trade_dates[0], raw_symbol="ESH4", instrument_id=1, volume=100, settle=100),
        _daily(trade_date=trade_dates[0], raw_symbol="ESM4", instrument_id=2, volume=110, settle=111),
        _daily(trade_date=trade_dates[1], raw_symbol="ESH4", instrument_id=1, volume=100, settle=101),
        _daily(trade_date=trade_dates[1], raw_symbol="ESM4", instrument_id=2, volume=120, settle=112),
        _daily(trade_date=trade_dates[2], raw_symbol="ESH4", instrument_id=1, volume=100, settle=102),
        _daily(trade_date=trade_dates[2], raw_symbol="ESM4", instrument_id=2, volume=130, settle=113),
        _daily(trade_date=trade_dates[2], raw_symbol="ESU4", instrument_id=3, volume=80, settle=120),
        _daily(trade_date=trade_dates[3], raw_symbol="ESH4", instrument_id=1, volume=90, settle=103),
        _daily(trade_date=trade_dates[3], raw_symbol="ESM4", instrument_id=2, volume=140, settle=114),
        _daily(trade_date=trade_dates[3], raw_symbol="ESU4", instrument_id=3, volume=85, settle=121),
        _daily(trade_date=trade_dates[4], raw_symbol="ESM4", instrument_id=2, volume=145, settle=115),
        _daily(trade_date=trade_dates[4], raw_symbol="ESU4", instrument_id=3, volume=90, settle=122),
    ])
    write_parquet_dataset(
        contract_master,
        tmp_path / "data" / "curated" / "contract_master" / f"snapshot_id={snapshot_id}",
        compression="zstd",
        partition_cols=None,
    )
    contracts_daily_out = contracts_daily.assign(
        trade_year=pd.to_datetime(contracts_daily["trade_date"]).dt.year,
    )
    write_parquet_dataset(
        contracts_daily_out,
        tmp_path / "data" / "curated" / "contracts_daily" / f"snapshot_id={snapshot_id}",
        compression="zstd",
        partition_cols=["trade_year"],
    )


def test_roll_engine_build_and_qa_offline(tmp_path: Path) -> None:
    _copy_repo_config(tmp_path)
    _write_source_snapshot(tmp_path)
    runner = CliRunner()

    build = runner.invoke(
        app,
        [
            "roll-engine",
            "build",
            "--start",
            "2024-01-02",
            "--end",
            "2024-01-08",
            "--snapshot-id",
            "snapshot_test",
            "--roots",
            "ES",
            "--repo-root",
            str(tmp_path),
        ],
    )
    assert build.exit_code == 0, build.stdout

    lead_map = read_parquet_dataset(
        tmp_path / "data" / "curated" / "lead_map" / "snapshot_id=snapshot_test"
    )
    roll_events = read_parquet_dataset(
        tmp_path / "data" / "curated" / "roll_events" / "snapshot_id=snapshot_test"
    )
    assert not lead_map.empty
    assert not roll_events.empty
    assert not lead_map.duplicated(subset=["as_of_date", "root"]).any()
    assert not roll_events.duplicated(subset=["roll_event_id"]).any()

    qa = runner.invoke(
        app,
        [
            "roll-engine",
            "qa",
            "--snapshot-id",
            "snapshot_test",
            "--repo-root",
            str(tmp_path),
        ],
    )
    assert qa.exit_code == 0, qa.stdout

    file_registry = read_parquet_dataset(tmp_path / "data" / "meta" / "data_file_registry")
    assert {"lead_map", "roll_events"}.issubset(set(file_registry["logical_table"]))

    rerun_without_overwrite = runner.invoke(
        app,
        [
            "roll-engine",
            "build",
            "--start",
            "2024-01-02",
            "--end",
            "2024-01-08",
            "--snapshot-id",
            "snapshot_test",
            "--roots",
            "ES",
            "--repo-root",
            str(tmp_path),
        ],
    )
    assert rerun_without_overwrite.exit_code != 0

    rerun_with_overwrite = runner.invoke(
        app,
        [
            "roll-engine",
            "build",
            "--start",
            "2024-01-02",
            "--end",
            "2024-01-08",
            "--snapshot-id",
            "snapshot_test",
            "--roots",
            "ES",
            "--overwrite",
            "--repo-root",
            str(tmp_path),
        ],
    )
    assert rerun_with_overwrite.exit_code == 0, rerun_with_overwrite.stdout
