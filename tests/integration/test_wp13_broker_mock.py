from __future__ import annotations

import shutil
from datetime import UTC, date, datetime
from pathlib import Path

import pandas as pd
import pytest

from cpdshadow.broker.ibkr.mock import MockIbkrClient
from cpdshadow.broker.ibkr.models import (
    IbkrAccountSummary,
    IbkrBrokerOpenOrder,
    IbkrBrokerPosition,
    make_callback_event,
)
from cpdshadow.broker.ibkr.service import IbkrBrokerService, IbkrBrokerServiceError
from cpdshadow.broker.ibkr.translator import make_broker_request_id
from cpdshadow.storage.parquet_io import read_parquet_dataset, write_parquet_part


def test_wp13_paper_submit_mock_callbacks(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _init_repo(tmp_path)
    _write_submit_inputs(tmp_path, run_id="paper_run_001")
    monkeypatch.setenv("IBKR_CLIENT_ID", "7")
    monkeypatch.setenv("CPDSHADOW_ENABLE_PAPER_SUBMIT", "1")
    request_es = make_broker_request_id(
        order_intent_id="oi_es_001",
        broker_mode="paper_submit",
        account_id="DU0000000",
    )
    request_nq = make_broker_request_id(
        order_intent_id="oi_nq_001",
        broker_mode="paper_submit",
        account_id="DU0000000",
    )
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
        callbacks_by_request_id={
            request_es: [
                make_callback_event(
                    event_type="orderStatus",
                    event_time_utc=_created_at(),
                    account_id="DU0000000",
                    client_id=7,
                    broker_request_id=request_es,
                    ib_order_id=1101,
                    broker_contract_key="IBKR|FUT|CME|ESM7|USD",
                    raw_symbol="ESM7",
                    root="ES",
                    status="Submitted",
                    filled_quantity=1,
                    remaining_quantity=1,
                    payload={},
                )
            ],
            request_nq: [
                make_callback_event(
                    event_type="error",
                    event_time_utc=_created_at(),
                    account_id="DU0000000",
                    client_id=7,
                    broker_request_id=request_nq,
                    ib_order_id=1102,
                    broker_contract_key="IBKR|FUT|CME|NQM7|USD",
                    raw_symbol="NQM7",
                    root="NQ",
                    error_code="201",
                    error_message="Order rejected",
                    payload={},
                )
            ],
        },
    )
    service = IbkrBrokerService(
        repo_root=tmp_path,
        client_factory=lambda account_id: mock_client,
    )

    artifact = service.submit_intents(
        order_intents_path=tmp_path / "data" / "shadow" / "inputs" / "order_intents.parquet",
        contract_master_path=tmp_path / "data" / "curated" / "contract_master.parquet",
        contracts_daily_path=tmp_path / "data" / "curated" / "contracts_daily.parquet",
        monitoring_path=tmp_path / "data" / "shadow" / "inputs" / "monitoring_daily.parquet",
        broker_mode="paper_submit",
        run_id="paper_run_001",
        as_of_date=date(2026, 4, 23),
        account_id="DU0000000",
        created_at_utc=_created_at(),
    )

    workspace = tmp_path / "data" / "shadow" / "broker" / "ibkr" / "run_id=paper_run_001"
    output = read_parquet_dataset(workspace / "order_intents.parquet")
    assert mock_client.place_order_calls == 2
    assert artifact["status_counts"]["partially_filled"] == 1
    assert artifact["status_counts"]["rejected"] == 1
    assert set(output["status"].astype(str)) == {"partially_filled", "rejected"}
    assert output["limit_price"].notna().all()

    qa = service.qa(workspace=workspace)
    assert qa["has_errors"] is False
    assert (workspace / "qa_report.json").exists()
    assert (workspace / "qa_report.md").exists()


def test_wp13_paper_submit_requires_guard(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _init_repo(tmp_path)
    _write_submit_inputs(tmp_path, run_id="paper_run_guard")
    monkeypatch.delenv("CPDSHADOW_ENABLE_PAPER_SUBMIT", raising=False)
    service = IbkrBrokerService(
        repo_root=tmp_path,
        client_factory=lambda account_id: MockIbkrClient(
            account_id=account_id,
            client_id=7,
        ),
    )

    with pytest.raises(IbkrBrokerServiceError, match="paper_submit requires"):
        service.submit_intents(
            order_intents_path=tmp_path / "data" / "shadow" / "inputs" / "order_intents.parquet",
            contract_master_path=tmp_path / "data" / "curated" / "contract_master.parquet",
            contracts_daily_path=tmp_path / "data" / "curated" / "contracts_daily.parquet",
            monitoring_path=tmp_path / "data" / "shadow" / "inputs" / "monitoring_daily.parquet",
            broker_mode="paper_submit",
            run_id="paper_run_guard",
            as_of_date=date(2026, 4, 23),
            account_id="DU0000000",
            created_at_utc=_created_at(),
        )


def test_wp13_account_allowlist_mismatch_rejects_run(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    service = IbkrBrokerService(
        repo_root=tmp_path,
        client_factory=lambda account_id: MockIbkrClient(
            account_id=account_id,
            client_id=7,
        ),
    )

    with pytest.raises(IbkrBrokerServiceError, match="allowlist"):
        service.sync_state(
            run_id="shadow_run_bad_account",
            broker_mode="shadow_only",
            as_of_date=date(2026, 4, 23),
            account_id="U9999999",
            created_at_utc=_created_at(),
        )


def _init_repo(tmp_path: Path) -> None:
    shutil.copytree(Path("config"), tmp_path / "config")
    (tmp_path / "data" / "curated").mkdir(parents=True, exist_ok=True)
    (tmp_path / "data" / "shadow" / "inputs").mkdir(parents=True, exist_ok=True)
    (tmp_path / "data" / "meta").mkdir(parents=True, exist_ok=True)


def _write_submit_inputs(tmp_path: Path, *, run_id: str) -> None:
    write_parquet_part(
        pd.DataFrame(
            [
                {
                    "order_intent_id": "oi_es_001",
                    "run_id": run_id,
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
                },
                {
                    "order_intent_id": "oi_nq_001",
                    "run_id": run_id,
                    "strategy_id": "cpd_lstm",
                    "execution_mode": "shadow",
                    "as_of_date": "2026-04-23",
                    "execution_date": "2026-04-24",
                    "root": "NQ",
                    "raw_symbol": "NQM7",
                    "broker_contract_id": None,
                    "side": "sell",
                    "quantity": 1,
                    "order_type": "marketable_limit",
                    "limit_price": None,
                    "reason": "rebalance",
                    "status": "not_sent",
                    "control_action": "run_cpd_lstm",
                    "position_snapshot_id": "snapshot_001",
                    "sequence_no": 2,
                    "rejection_reason": None,
                    "created_at_utc": "2026-04-23T00:00:00Z",
                    "submitted_at_utc": None,
                },
            ]
        ),
        tmp_path / "data" / "shadow" / "inputs" / "order_intents.parquet",
    )
    write_parquet_part(
        pd.DataFrame(
            [
                {
                    "run_id": run_id,
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
                },
                {
                    "root": "NQ",
                    "raw_symbol": "NQM7",
                    "exchange": "CME",
                    "currency": "USD",
                    "tick_size": 0.25,
                    "multiplier": 20.0,
                    "last_trade_date": "2027-06-18",
                },
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
                },
                {
                    "trade_date": "2026-04-22",
                    "root": "NQ",
                    "raw_symbol": "NQM7",
                    "settle_price": 18000.0,
                    "close_price": 18005.0,
                    "available_at_utc": "2026-04-22T22:00:00Z",
                },
            ]
        ),
        tmp_path / "data" / "curated" / "contracts_daily.parquet",
    )


def _created_at() -> datetime:
    return datetime(2026, 4, 23, tzinfo=UTC)
