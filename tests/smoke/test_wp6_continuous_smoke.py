from __future__ import annotations

from datetime import date
from pathlib import Path
import shutil

import pandas as pd
from typer.testing import CliRunner

from cpdshadow.cli import app
from cpdshadow.storage.parquet_io import read_parquet_dataset, write_parquet_dataset


def _daily(
    *,
    trade_date: date,
    root: str,
    raw_symbol: str,
    instrument_id: int,
    settle: float,
    settle_status: str = "final",
) -> dict[str, object]:
    return {
        "trade_date": trade_date,
        "root": root,
        "raw_symbol": raw_symbol,
        "dataset": "GLBX.MDP3",
        "instrument_id": instrument_id,
        "open_price": settle,
        "high_price": settle,
        "low_price": settle,
        "close_price": settle,
        "settle_price": settle,
        "settle_status": settle_status,
        "volume": 100.0,
        "open_interest": 1000.0,
        "price_source": "statistics",
        "volume_source": "statistics",
        "available_at_utc": pd.Timestamp(f"{trade_date.isoformat()}T23:00:00Z"),
        "ingested_at_utc": pd.Timestamp(f"{trade_date.isoformat()}T23:30:00Z"),
        "quality_flags": [],
        "override_id": None,
        "snapshot_id": "snapshot_test",
    }


def _lead_row(
    *,
    as_of_date: date,
    root: str,
    lead_raw_symbol: str,
    roll_flag: bool = False,
    roll_event_id: str | None = None,
) -> dict[str, object]:
    return {
        "as_of_date": as_of_date,
        "root": root,
        "roll_policy_version": "volume3_hardroll_v1",
        "lead_raw_symbol": lead_raw_symbol,
        "next_raw_symbol": None,
        "prev_lead_raw_symbol": None,
        "roll_flag": roll_flag,
        "roll_event_id": roll_event_id,
        "days_to_expiry": 30,
        "front_volume_tminus1": 100.0,
        "next_volume_tminus1": 120.0,
        "confirmation_count": 0,
        "hard_roll_deadline": as_of_date,
        "selection_reason": "carry_forward",
        "builder_version": "roll_engine_v1",
        "snapshot_id": "snapshot_test",
    }


def _roll_event(
    *,
    root: str,
    roll_event_id: str,
    from_raw_symbol: str,
    to_raw_symbol: str,
    trigger_date: date,
    effective_date: date,
    ratio_adjustment: float,
    from_settle: float,
    to_settle: float,
) -> dict[str, object]:
    return {
        "roll_event_id": roll_event_id,
        "root": root,
        "from_raw_symbol": from_raw_symbol,
        "to_raw_symbol": to_raw_symbol,
        "trigger_date": trigger_date,
        "effective_date": effective_date,
        "roll_reason": "volume_3day",
        "front_volume_tminus1": 100.0,
        "next_volume_tminus1": 120.0,
        "confirmation_count": 3,
        "from_settle": from_settle,
        "to_settle": to_settle,
        "ratio_adjustment": ratio_adjustment,
        "basis_at_roll": to_settle - from_settle,
        "builder_version": "roll_engine_v1",
        "override_id": None,
    }


def _copy_repo_config(tmp_path: Path) -> None:
    shutil.copytree(Path("config"), tmp_path / "config")
    (tmp_path / "data" / "curated").mkdir(parents=True, exist_ok=True)
    (tmp_path / "data" / "meta").mkdir(parents=True, exist_ok=True)
    (tmp_path / "artifacts" / "wp6").mkdir(parents=True, exist_ok=True)


def _write_source_snapshot(tmp_path: Path) -> None:
    snapshot_id = "snapshot_test"
    dates = [
        date(2024, 1, 2),
        date(2024, 1, 3),
        date(2024, 1, 4),
        date(2024, 1, 5),
        date(2024, 1, 8),
    ]
    contracts_daily = pd.DataFrame([
        _daily(trade_date=dates[0], root="ES", raw_symbol="ESH4", instrument_id=1, settle=100.0),
        _daily(trade_date=dates[1], root="ES", raw_symbol="ESH4", instrument_id=1, settle=101.0),
        _daily(trade_date=dates[2], root="ES", raw_symbol="ESH4", instrument_id=1, settle=102.0),
        _daily(trade_date=dates[2], root="ES", raw_symbol="ESM4", instrument_id=2, settle=204.0),
        _daily(trade_date=dates[3], root="ES", raw_symbol="ESM4", instrument_id=2, settle=206.0),
        _daily(trade_date=dates[4], root="ES", raw_symbol="ESM4", instrument_id=2, settle=208.0),
        _daily(trade_date=dates[0], root="NQ", raw_symbol="NQH4", instrument_id=10, settle=150.0),
        _daily(trade_date=dates[1], root="NQ", raw_symbol="NQH4", instrument_id=10, settle=151.0),
        _daily(trade_date=dates[2], root="NQ", raw_symbol="NQH4", instrument_id=10, settle=152.0),
        _daily(trade_date=dates[3], root="NQ", raw_symbol="NQH4", instrument_id=10, settle=153.0),
        _daily(trade_date=dates[4], root="NQ", raw_symbol="NQH4", instrument_id=10, settle=154.0, settle_status="close_fallback"),
    ])
    lead_map = pd.DataFrame([
        _lead_row(as_of_date=dates[0], root="ES", lead_raw_symbol="ESH4"),
        _lead_row(as_of_date=dates[1], root="ES", lead_raw_symbol="ESH4"),
        _lead_row(as_of_date=dates[2], root="ES", lead_raw_symbol="ESH4"),
        _lead_row(as_of_date=dates[3], root="ES", lead_raw_symbol="ESM4", roll_flag=True, roll_event_id="roll_es_1"),
        _lead_row(as_of_date=dates[4], root="ES", lead_raw_symbol="ESM4"),
        _lead_row(as_of_date=dates[0], root="NQ", lead_raw_symbol="NQH4"),
        _lead_row(as_of_date=dates[1], root="NQ", lead_raw_symbol="NQH4"),
        _lead_row(as_of_date=dates[2], root="NQ", lead_raw_symbol="NQH4"),
        _lead_row(as_of_date=dates[3], root="NQ", lead_raw_symbol="NQH4"),
        _lead_row(as_of_date=dates[4], root="NQ", lead_raw_symbol="NQH4"),
    ])
    roll_events = pd.DataFrame([
        _roll_event(
            root="ES",
            roll_event_id="roll_es_1",
            from_raw_symbol="ESH4",
            to_raw_symbol="ESM4",
            trigger_date=dates[2],
            effective_date=dates[3],
            ratio_adjustment=2.0,
            from_settle=102.0,
            to_settle=204.0,
        ),
    ])

    contracts_daily_out = contracts_daily.assign(
        trade_year=pd.to_datetime(contracts_daily["trade_date"]).dt.year,
    )
    write_parquet_dataset(
        contracts_daily_out,
        tmp_path / "data" / "curated" / "contracts_daily" / f"snapshot_id={snapshot_id}",
        compression="zstd",
        partition_cols=["trade_year"],
    )
    lead_map_out = lead_map.assign(
        year=pd.to_datetime(lead_map["as_of_date"]).dt.year,
    )
    write_parquet_dataset(
        lead_map_out,
        tmp_path / "data" / "curated" / "lead_map" / f"snapshot_id={snapshot_id}",
        compression="zstd",
        partition_cols=["year"],
    )
    roll_events_out = roll_events.assign(
        year=pd.to_datetime(roll_events["effective_date"]).dt.year,
    )
    write_parquet_dataset(
        roll_events_out,
        tmp_path / "data" / "curated" / "roll_events" / f"snapshot_id={snapshot_id}",
        compression="zstd",
        partition_cols=["year"],
    )


def test_continuous_builder_build_and_qa_offline(tmp_path: Path) -> None:
    _copy_repo_config(tmp_path)
    _write_source_snapshot(tmp_path)
    runner = CliRunner()

    build = runner.invoke(
        app,
        [
            "continuous",
            "build",
            "--snapshot-id",
            "snapshot_test",
            "--start",
            "2024-01-02",
            "--end",
            "2024-01-08",
            "--roots",
            "ES,NQ",
            "--repo-root",
            str(tmp_path),
        ],
    )
    assert build.exit_code == 0, build.stdout

    continuous_daily = read_parquet_dataset(
        tmp_path
        / "data"
        / "curated"
        / "continuous_daily"
        / "series_id=v1_back_ratio_settle"
        / "snapshot_id=snapshot_test"
    )
    assert not continuous_daily.empty
    assert not continuous_daily.duplicated(subset=["series_id", "as_of_date", "root"]).any()
    assert {"ES", "NQ"} == set(continuous_daily["root"])

    qa = runner.invoke(
        app,
        [
            "continuous",
            "qa",
            "--snapshot-id",
            "snapshot_test",
            "--repo-root",
            str(tmp_path),
        ],
    )
    assert qa.exit_code == 0, qa.stdout

    file_registry = read_parquet_dataset(tmp_path / "data" / "meta" / "data_file_registry")
    assert "continuous_daily" in set(file_registry["logical_table"])
