from __future__ import annotations

import shutil
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
from typer.testing import CliRunner

import cpdshadow.cli as cli_module
from cpdshadow.broker.ibkr.mock import MockIbkrClient
from cpdshadow.broker.ibkr.models import (
    IbkrAccountSummary,
    IbkrBrokerOpenOrder,
    IbkrBrokerPosition,
)
from cpdshadow.broker.ibkr.service import IbkrBrokerService
from cpdshadow.storage.parquet_io import read_parquet_dataset, write_parquet_part


def test_wp13_broker_shadow_smoke(monkeypatch, tmp_path: Path) -> None:
    shutil.copytree(Path("config"), tmp_path / "config")
    (tmp_path / "data" / "curated").mkdir(parents=True, exist_ok=True)
    (tmp_path / "data" / "shadow" / "inputs").mkdir(parents=True, exist_ok=True)
    (tmp_path / "data" / "meta").mkdir(parents=True, exist_ok=True)
    _write_inputs(tmp_path)
    mock_client = MockIbkrClient(
        account_id="DU0000000",
        client_id=7,
        positions=[
            IbkrBrokerPosition(
                account_id="DU0000000",
                broker_contract_id="1001",
                local_symbol="ESM7",
                symbol="ES",
                exchange="CME",
                currency="USD",
                sec_type="FUT",
                quantity=1,
                avg_cost=5000.0,
            )
        ],
        open_orders=[
            IbkrBrokerOpenOrder(
                account_id="DU0000000",
                client_id=7,
                ib_order_id=1000,
                perm_id=9000,
                broker_contract_id="1001",
                local_symbol="ESM7",
                symbol="ES",
                exchange="CME",
                currency="USD",
                action="BUY",
                total_quantity=1,
                filled_quantity=0,
                remaining_quantity=1,
                order_type="LMT",
                tif="DAY",
                limit_price=5100.0,
                status="Submitted",
            )
        ],
        account_summary=IbkrAccountSummary(
            account_id="DU0000000",
            captured_at_utc=_created_at(),
            net_liquidation=1_000_000.0,
            excess_liquidity=900_000.0,
            init_margin_req=100_000.0,
            maint_margin_req=80_000.0,
            buying_power=2_000_000.0,
            available_funds=900_000.0,
            source="ibkr_tws_api",
        ),
    )
    monkeypatch.setenv("IBKR_CLIENT_ID", "7")
    monkeypatch.setattr(
        cli_module,
        "_build_ibkr_broker_service",
        lambda repo_root: IbkrBrokerService(
            repo_root=repo_root,
            client_factory=lambda account_id: mock_client,
        ),
    )
    runner = CliRunner()

    sync = runner.invoke(
        cli_module.app,
        [
            "broker-ibkr",
            "sync-state",
            "--run-id",
            "shadow_ibkr_run_001",
            "--as-of",
            "2026-04-23",
            "--mode",
            "shadow_only",
            "--account-id",
            "DU0000000",
            "--contract-master-path",
            "data/curated/contract_master.parquet",
            "--fixed-created-at-utc",
            "2026-04-23T00:00:00Z",
            "--repo-root",
            str(tmp_path),
        ],
    )
    assert sync.exit_code == 0, sync.stdout

    submit = runner.invoke(
        cli_module.app,
        [
            "broker-ibkr",
            "submit-intents",
            "--order-intents-path",
            "data/shadow/inputs/order_intents.parquet",
            "--contract-master-path",
            "data/curated/contract_master.parquet",
            "--contracts-daily-path",
            "data/curated/contracts_daily.parquet",
            "--monitoring-path",
            "data/shadow/inputs/monitoring_daily.parquet",
            "--mode",
            "shadow_only",
            "--run-id",
            "shadow_ibkr_run_001",
            "--as-of",
            "2026-04-23",
            "--account-id",
            "DU0000000",
            "--fixed-created-at-utc",
            "2026-04-23T00:00:00Z",
            "--repo-root",
            str(tmp_path),
        ],
    )
    assert submit.exit_code == 0, submit.stdout

    qa = runner.invoke(
        cli_module.app,
        [
            "broker-ibkr",
            "qa",
            "--workspace",
            "data/shadow/broker/ibkr/run_id=shadow_ibkr_run_001",
            "--repo-root",
            str(tmp_path),
        ],
    )
    assert qa.exit_code == 0, qa.stdout

    workspace = tmp_path / "data" / "shadow" / "broker" / "ibkr" / "run_id=shadow_ibkr_run_001"
    assert mock_client.place_order_calls == 0
    assert (workspace / "broker_positions_snapshot.parquet").exists()
    assert (workspace / "broker_open_orders_snapshot.parquet").exists()
    assert (workspace / "order_intents.parquet").exists()
    assert (workspace / "journal_events.parquet").exists()
    assert (workspace / "translated_order_requests.json").exists()
    assert (workspace / "qa_report.json").exists()
    assert (workspace / "qa_report.md").exists()

    intents = read_parquet_dataset(workspace / "order_intents.parquet")
    assert set(intents["status"].astype(str)) <= {"not_sent", "rejected"}


def _write_inputs(tmp_path: Path) -> None:
    write_parquet_part(
        pd.DataFrame(
            [
                {
                    "order_intent_id": "oi_es_001",
                    "run_id": "shadow_ibkr_run_001",
                    "strategy_id": "cpd_lstm",
                    "execution_mode": "shadow",
                    "as_of_date": "2026-04-23",
                    "execution_date": "2026-04-24",
                    "root": "ES",
                    "raw_symbol": "ESM7",
                    "broker_contract_id": None,
                    "side": "buy",
                    "quantity": 2,
                    "order_type": "marketable_limit",
                    "limit_price": None,
                    "reason": "rebalance",
                    "status": "not_sent",
                    "control_action": "run_cpd_lstm",
                    "position_snapshot_id": "snapshot_001",
                    "sequence_no": 1,
                    "rejection_reason": None,
                    "created_at_utc": "2026-04-23T00:00:00Z",
                    "submitted_at_utc": None,
                }
            ]
        ),
        tmp_path / "data" / "shadow" / "inputs" / "order_intents.parquet",
    )
    write_parquet_part(
        pd.DataFrame(
            [
                {
                    "run_id": "shadow_ibkr_run_001",
                    "execution_mode": "shadow",
                    "as_of_date": "2026-04-23",
                    "execution_date": "2026-04-24",
                    "final_action": "run_cpd_lstm",
                    "created_at_utc": "2026-04-23T00:00:00Z",
                }
            ]
        ),
        tmp_path / "data" / "shadow" / "inputs" / "monitoring_daily.parquet",
    )
    write_parquet_part(
        pd.DataFrame(
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
        tmp_path / "data" / "curated" / "contract_master.parquet",
    )
    write_parquet_part(
        pd.DataFrame(
            [
                {
                    "trade_date": "2026-04-22",
                    "root": "ES",
                    "raw_symbol": "ESM7",
                    "settle_price": 5100.0,
                    "close_price": 5101.0,
                    "available_at_utc": "2026-04-22T22:00:00Z",
                }
            ]
        ),
        tmp_path / "data" / "curated" / "contracts_daily.parquet",
    )


def _created_at() -> datetime:
    return datetime(2026, 4, 23, tzinfo=UTC)
