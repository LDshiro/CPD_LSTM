from __future__ import annotations

import shutil
from pathlib import Path

import pandas as pd
from typer.testing import CliRunner

from cpdshadow.cli import app
from cpdshadow.storage.parquet_io import read_parquet_dataset, write_parquet_part


def test_wp12_broker_boundary_smoke_offline(tmp_path: Path) -> None:
    shutil.copytree(Path("config"), tmp_path / "config")
    (tmp_path / "data" / "shadow" / "inputs").mkdir(parents=True, exist_ok=True)
    (tmp_path / "data" / "curated").mkdir(parents=True, exist_ok=True)
    (tmp_path / "data" / "meta").mkdir(parents=True, exist_ok=True)
    _write_inputs(tmp_path)
    runner = CliRunner()

    build = runner.invoke(
        app,
        [
            "broker-boundary",
            "build-intents",
            "--targets-path",
            "data/shadow/inputs/targets_daily.parquet",
            "--positions-path",
            "data/shadow/inputs/broker_positions_snapshot.parquet",
            "--contract-master-path",
            "data/curated/contract_master.parquet",
            "--monitoring-path",
            "data/shadow/inputs/monitoring_daily.parquet",
            "--run-id",
            "shadow_run_001",
            "--execution-mode",
            "shadow",
            "--fixed-created-at-utc",
            "2026-04-23T00:00:00Z",
            "--repo-root",
            str(tmp_path),
        ],
    )
    assert build.exit_code == 0, build.stdout

    dry_run = runner.invoke(
        app,
        [
            "broker-boundary",
            "dry-run",
            "--planned-intents-path",
            "data/shadow/execution_boundary/run_id=shadow_run_001/planned_order_intents.parquet",
            "--contract-master-path",
            "data/curated/contract_master.parquet",
            "--fixed-created-at-utc",
            "2026-04-23T00:00:00Z",
            "--repo-root",
            str(tmp_path),
        ],
    )
    assert dry_run.exit_code == 0, dry_run.stdout

    qa = runner.invoke(
        app,
        [
            "broker-boundary",
            "qa",
            "--targets-path",
            "data/shadow/inputs/targets_daily.parquet",
            "--positions-path",
            "data/shadow/inputs/broker_positions_snapshot.parquet",
            "--final-order-intents-path",
            "data/shadow/execution_boundary/run_id=shadow_run_001/order_intents.parquet",
            "--monitoring-path",
            "data/shadow/inputs/monitoring_daily.parquet",
            "--fixed-created-at-utc",
            "2026-04-23T00:00:00Z",
            "--repo-root",
            str(tmp_path),
        ],
    )
    assert qa.exit_code == 0, qa.stdout

    workspace = tmp_path / "data" / "shadow" / "execution_boundary" / "run_id=shadow_run_001"
    assert (workspace / "planned_order_intents.parquet").exists()
    assert (workspace / "order_intents.parquet").exists()
    assert (workspace / "journal_events.parquet").exists()
    assert (workspace / "manifest.json").exists()
    assert (workspace / "build_summary.json").exists()
    assert (workspace / "dry_run_results.json").exists()
    assert (workspace / "qa_report.json").exists()
    assert (workspace / "dry_run_report.md").exists()

    final_intents = read_parquet_dataset(workspace / "order_intents.parquet")
    assert set(final_intents["status"].astype(str)) <= {"not_sent", "rejected"}
    assert final_intents["broker_contract_id"].isna().all()
    assert final_intents["limit_price"].isna().all()

    file_registry = read_parquet_dataset(tmp_path / "data" / "meta" / "data_file_registry")
    assert {"planned_order_intents", "order_intents", "journal_events"} <= set(
        file_registry["logical_table"].astype(str)
    )


def _write_inputs(tmp_path: Path) -> None:
    write_parquet_part(
        pd.DataFrame(
            [
                {
                    "run_id": "shadow_run_001",
                    "strategy_id": "cpd_lstm",
                    "execution_mode": "shadow",
                    "as_of_date": "2026-04-23",
                    "execution_date": "2026-04-24",
                    "root": "ES",
                    "lead_raw_symbol": "ESU7",
                    "target_contracts": 2,
                    "current_contracts": 2,
                    "order_delta_contracts": 0,
                    "control_action": "run_cpd_lstm",
                },
                {
                    "run_id": "shadow_run_001",
                    "strategy_id": "cpd_lstm",
                    "execution_mode": "shadow",
                    "as_of_date": "2026-04-23",
                    "execution_date": "2026-04-24",
                    "root": "NQ",
                    "lead_raw_symbol": "NQH7",
                    "target_contracts": -1,
                    "current_contracts": 0,
                    "order_delta_contracts": -1,
                    "control_action": "run_cpd_lstm",
                },
            ]
        ),
        tmp_path / "data" / "shadow" / "inputs" / "targets_daily.parquet",
    )
    write_parquet_part(
        pd.DataFrame(
            [
                {
                    "position_snapshot_id": "snapshot_001",
                    "run_id": "shadow_run_001",
                    "execution_mode": "shadow",
                    "account_id": "shadow_demo",
                    "broker_contract_id": None,
                    "raw_symbol": "ESM7",
                    "root": "ES",
                    "position_contracts": 2,
                    "snapshot_time_utc": "2026-04-23T00:00:00Z",
                }
            ]
        ),
        tmp_path / "data" / "shadow" / "inputs" / "broker_positions_snapshot.parquet",
    )
    write_parquet_part(
        pd.DataFrame(
            [
                {
                    "run_id": "shadow_run_001",
                    "execution_mode": "shadow",
                    "as_of_date": "2026-04-23",
                    "execution_date": "2026-04-24",
                    "final_action": "run_cpd_lstm",
                    "highest_severity": "info",
                    "alerts_json": "[]",
                    "created_at_utc": "2026-04-23T00:00:00Z",
                }
            ]
        ),
        tmp_path / "data" / "shadow" / "inputs" / "monitoring_daily.parquet",
    )
    write_parquet_part(
        pd.DataFrame(
            [
                {"root": "ES", "raw_symbol": "ESM7"},
                {"root": "ES", "raw_symbol": "ESU7"},
                {"root": "NQ", "raw_symbol": "NQH7"},
            ]
        ),
        tmp_path / "data" / "curated" / "contract_master.parquet",
    )
