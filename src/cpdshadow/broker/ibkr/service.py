from __future__ import annotations

import json
import os
import subprocess
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Callable

import pandas as pd

from cpdshadow.broker.ibkr.client import RealIbkrTwsClient
from cpdshadow.broker.ibkr.contracts import (
    IbkrContractResolutionError,
    resolve_broker_snapshot_contract,
    resolve_ibkr_contract,
)
from cpdshadow.broker.ibkr.models import (
    BrokerMode,
    IbkrBrokerOpenOrder,
    IbkrBrokerPosition,
    IbkrCallbackEvent,
    IbkrClientProtocol,
    IbkrOpenOrderSnapshotRow,
    IbkrPositionSnapshotRow,
)
from cpdshadow.broker.ibkr.reconcile import normalize_broker_error, reconcile_order_intents
from cpdshadow.broker.ibkr.translator import (
    IbkrTranslationError,
    normalize_order_intents_frame,
    translate_order_request,
    validate_reduce_only_root,
)
from cpdshadow.config import AppConfig, BrokerIbkrConfig, load_data_schema_yaml, load_yaml
from cpdshadow.ids import (
    canonical_json_bytes,
    config_hash,
    file_sha256,
    make_file_id,
    make_run_id,
    request_params_hash,
    stable_sha256_hex,
)
from cpdshadow.instruments import InstrumentMaster, load_instrument_master
from cpdshadow.order_intents import JournalEvent
from cpdshadow.storage.parquet_io import ensure_directory, read_parquet_dataset, write_parquet_part
from cpdshadow.storage.registry import (
    FileRegistryRow,
    RunRegistryRow,
    append_file_registry_row,
    append_run_registry_row,
)


_POSITION_SNAPSHOT_COLUMNS = [
    "position_snapshot_id",
    "run_id",
    "execution_mode",
    "account_id",
    "broker_contract_id",
    "broker_contract_key",
    "raw_symbol",
    "root",
    "position_contracts",
    "avg_cost",
    "snapshot_time_utc",
    "source",
]
_OPEN_ORDER_SNAPSHOT_COLUMNS = [
    "open_orders_snapshot_id",
    "run_id",
    "execution_mode",
    "account_id",
    "captured_at_utc",
    "client_id",
    "ib_order_id",
    "perm_id",
    "broker_contract_id",
    "broker_contract_key",
    "raw_symbol",
    "root",
    "action",
    "total_quantity",
    "filled_quantity",
    "remaining_quantity",
    "order_type",
    "tif",
    "limit_price",
    "status",
    "source",
]
_JOURNAL_COLUMNS = [
    "event_id",
    "run_id",
    "execution_mode",
    "as_of_date",
    "execution_date",
    "root",
    "component",
    "severity",
    "code",
    "message",
    "details_json",
    "created_at_utc",
]


class IbkrBrokerServiceError(ValueError):
    pass


ClientFactory = Callable[[str | None], IbkrClientProtocol]


def build_real_ibkr_client(
    *,
    broker_config: BrokerIbkrConfig,
    account_id: str | None,
) -> IbkrClientProtocol:
    host = os.getenv(broker_config.host_env)
    port = os.getenv(broker_config.port_env)
    client_id = os.getenv(broker_config.client_id_env)
    if host in (None, "") or port in (None, "") or client_id in (None, ""):
        raise IbkrBrokerServiceError(
            "IBKR host/port/client id environment variables are not fully configured"
        )
    return RealIbkrTwsClient(
        host=host,
        port=int(port),
        client_id=int(client_id),
        account_id=account_id,
        account_summary_tags=broker_config.account_summary_tags,
    )


class IbkrBrokerService:
    def __init__(
        self,
        *,
        repo_root: Path,
        app_config: AppConfig | None = None,
        instrument_master: InstrumentMaster | None = None,
        client_factory: ClientFactory | None = None,
    ) -> None:
        self.repo_root = repo_root
        self.app_config = app_config or load_yaml(repo_root / "config" / "settings.base.yml")
        self.instrument_master = instrument_master or load_instrument_master(
            repo_root / "config" / "instruments.yml"
        )
        self.data_schema = load_data_schema_yaml(repo_root / "config" / "data_schema.yml")
        self.broker_config = self.app_config.broker.ibkr
        self.meta_root = repo_root / "data" / "meta"
        self._client_factory = client_factory or (
            lambda account_id: build_real_ibkr_client(
                broker_config=self.broker_config,
                account_id=account_id,
            )
        )

    def sync_state(
        self,
        *,
        run_id: str,
        broker_mode: BrokerMode,
        as_of_date: date,
        output_dir: Path | None = None,
        account_id: str | None = None,
        contract_master_path: Path | None = None,
        created_at_utc: datetime | None = None,
    ) -> dict[str, object]:
        created_at = _normalize_datetime(created_at_utc)
        resolved_account = _resolve_account_id(self.broker_config, account_id)
        _validate_broker_mode(self.broker_config, broker_mode)
        _validate_account(self.broker_config, resolved_account, broker_mode)
        workspace = _resolve_output_dir(self.repo_root, run_id, output_dir)
        contract_master = (
            read_parquet_dataset(contract_master_path)
            if contract_master_path is not None
            else pd.DataFrame()
        )
        client = self._client_factory(resolved_account)
        started_at = _utc_now()
        journal_events: list[JournalEvent] = []
        try:
            client.connect()
            journal_events.append(
                _journal_event(
                    run_id=run_id,
                    execution_mode=_execution_mode_from_broker_mode(broker_mode),
                    as_of_date=as_of_date,
                    execution_date=None,
                    root=None,
                    component="broker_ibkr",
                    severity="info",
                    code="IBKR_CONNECTED",
                    message="Connected to IBKR TWS/Gateway adapter.",
                    created_at_utc=created_at,
                    details={"broker_mode": broker_mode},
                )
            )
            next_valid_id = client.ensure_next_valid_id(self.broker_config.connect_timeout_seconds)
            journal_events.append(
                _journal_event(
                    run_id=run_id,
                    execution_mode=_execution_mode_from_broker_mode(broker_mode),
                    as_of_date=as_of_date,
                    execution_date=None,
                    root=None,
                    component="broker_ibkr",
                    severity="info",
                    code="IBKR_NEXT_VALID_ID",
                    message="Received IBKR next valid order id.",
                    created_at_utc=created_at,
                    details={"next_valid_order_id": next_valid_id},
                )
            )
            raw_positions, position_events = client.fetch_positions(
                self.broker_config.callback_timeout_seconds
            )
            raw_open_orders, open_order_events = client.fetch_open_orders(
                self.broker_config.callback_timeout_seconds
            )
            account_summary, account_events = client.fetch_account_summary(
                self.broker_config.callback_timeout_seconds
            )
        finally:
            client.disconnect()

        snapshot_id = f"ibkr_pos_{stable_sha256_hex([run_id, broker_mode, as_of_date, created_at])[:16]}"
        open_snapshot_id = f"ibkr_open_{stable_sha256_hex([run_id, broker_mode, as_of_date, created_at])[:16]}"
        positions_df = _normalize_positions_snapshot(
            raw_positions=raw_positions,
            contract_master=contract_master,
            run_id=run_id,
            execution_mode=_execution_mode_from_broker_mode(broker_mode),
            snapshot_id=snapshot_id,
            captured_at_utc=created_at,
        )
        open_orders_df = _normalize_open_orders_snapshot(
            raw_open_orders=raw_open_orders,
            contract_master=contract_master,
            run_id=run_id,
            execution_mode=_execution_mode_from_broker_mode(broker_mode),
            snapshot_id=open_snapshot_id,
            captured_at_utc=created_at,
        )
        callback_events = position_events + open_order_events + account_events
        journal_events.extend(
            [
                _journal_event(
                    run_id=run_id,
                    execution_mode=_execution_mode_from_broker_mode(broker_mode),
                    as_of_date=as_of_date,
                    execution_date=None,
                    root=None,
                    component="broker_ibkr",
                    severity="info",
                    code="IBKR_POSITIONS_SYNCED",
                    message="IBKR positions snapshot completed.",
                    created_at_utc=created_at,
                    details={"row_count": len(positions_df)},
                ),
                _journal_event(
                    run_id=run_id,
                    execution_mode=_execution_mode_from_broker_mode(broker_mode),
                    as_of_date=as_of_date,
                    execution_date=None,
                    root=None,
                    component="broker_ibkr",
                    severity="info",
                    code="IBKR_OPEN_ORDERS_SYNCED",
                    message="IBKR open orders snapshot completed.",
                    created_at_utc=created_at,
                    details={"row_count": len(open_orders_df)},
                ),
                _journal_event(
                    run_id=run_id,
                    execution_mode=_execution_mode_from_broker_mode(broker_mode),
                    as_of_date=as_of_date,
                    execution_date=None,
                    root=None,
                    component="broker_ibkr",
                    severity="info",
                    code="IBKR_ACCOUNT_SUMMARY_SYNCED",
                    message="IBKR account summary snapshot completed.",
                    created_at_utc=created_at,
                    details={"available": account_summary is not None},
                ),
            ]
        )
        events_df = _journal_events_frame(journal_events)
        callback_df = _callback_events_frame(callback_events)
        self._write_parquet(workspace / "broker_positions_snapshot.parquet", positions_df)
        self._write_json(workspace / "broker_positions_snapshot.json", positions_df.to_dict("records"))
        self._write_parquet(workspace / "broker_open_orders_snapshot.parquet", open_orders_df)
        self._write_json(
            workspace / "broker_open_orders_snapshot.json",
            open_orders_df.to_dict("records"),
        )
        self._write_parquet(workspace / "journal_events.parquet", events_df)
        self._write_json(workspace / "journal_events.json", events_df.to_dict("records"))
        self._write_json(
            workspace / "broker_callback_events.json",
            callback_df.to_dict("records"),
        )
        self._write_json(
            workspace / "account_summary_snapshot.json",
            account_summary.to_dict() if account_summary is not None else {},
        )
        self._write_json(
            workspace / "manifest.json",
            {
                "run_id": run_id,
                "broker_mode": broker_mode,
                "as_of_date": as_of_date,
                "workspace": workspace.as_posix(),
            },
        )
        report = {
            "run_id": run_id,
            "broker_mode": broker_mode,
            "positions_count": int(len(positions_df)),
            "open_orders_count": int(len(open_orders_df)),
            "callback_count": int(len(callback_df)),
            "account_summary_available": account_summary is not None,
        }
        self._write_json(workspace / "reconciliation_report.json", report)
        (workspace / "broker_report.md").write_text(
            _broker_report_markdown(report),
            encoding="utf-8",
        )
        params_hash = request_params_hash(
            {
                "command": "sync-state",
                "run_id": run_id,
                "broker_mode": broker_mode,
                "as_of_date": as_of_date,
                "account_id": resolved_account,
            }
        )
        self._register_output(
            logical_table="broker_positions_snapshot",
            path=workspace / "broker_positions_snapshot.parquet",
            run_id=run_id,
            request_hash=params_hash,
            logical_date=as_of_date,
        )
        self._register_output(
            logical_table="broker_open_orders_snapshot",
            path=workspace / "broker_open_orders_snapshot.parquet",
            run_id=run_id,
            request_hash=params_hash,
            logical_date=as_of_date,
        )
        self._register_output(
            logical_table="journal_events",
            path=workspace / "journal_events.parquet",
            run_id=run_id,
            request_hash=params_hash,
            logical_date=as_of_date,
        )
        self._register_run(
            command_name="broker-ibkr sync-state",
            status="succeeded",
            artifact_path=workspace / "reconciliation_report.json",
            started_at=started_at,
            finished_at=_utc_now(),
            run_id=run_id,
        )
        return report

    def submit_intents(
        self,
        *,
        order_intents_path: Path,
        contract_master_path: Path,
        contracts_daily_path: Path,
        monitoring_path: Path | None,
        broker_mode: BrokerMode,
        output_dir: Path | None = None,
        run_id: str | None = None,
        as_of_date: date | None = None,
        account_id: str | None = None,
        created_at_utc: datetime | None = None,
    ) -> dict[str, object]:
        created_at = _normalize_datetime(created_at_utc)
        resolved_account = _resolve_account_id(self.broker_config, account_id)
        _validate_broker_mode(self.broker_config, broker_mode)
        _validate_account(self.broker_config, resolved_account, broker_mode)
        intents = normalize_order_intents_frame(read_parquet_dataset(order_intents_path))
        if intents.empty:
            raise IbkrBrokerServiceError("order_intents is empty")
        if run_id is not None:
            intents = intents[intents["run_id"].astype(str) == str(run_id)].reset_index(drop=True)
        if as_of_date is not None:
            intents = intents[intents["as_of_date"] == as_of_date].reset_index(drop=True)
        if intents.empty:
            raise IbkrBrokerServiceError("order_intents became empty after filters")
        run_id_value = str(intents.iloc[0]["run_id"])
        as_of_value = pd.Timestamp(intents.iloc[0]["as_of_date"]).date()
        workspace = _resolve_output_dir(self.repo_root, run_id_value, output_dir)
        contract_master = read_parquet_dataset(contract_master_path)
        contracts_daily = read_parquet_dataset(contracts_daily_path)
        monitoring = (
            read_parquet_dataset(monitoring_path)
            if monitoring_path is not None
            else pd.DataFrame()
        )
        client = self._client_factory(resolved_account)
        started_at = _utc_now()
        journal_events: list[JournalEvent] = []
        try:
            client.connect()
            current_order_id = client.ensure_next_valid_id(self.broker_config.connect_timeout_seconds)
            raw_positions, position_events = client.fetch_positions(
                self.broker_config.callback_timeout_seconds
            )
            raw_open_orders, open_order_events = client.fetch_open_orders(
                self.broker_config.callback_timeout_seconds
            )
            account_summary, account_events = client.fetch_account_summary(
                self.broker_config.callback_timeout_seconds
            )
            journal_events.append(
                _journal_event(
                    run_id=run_id_value,
                    execution_mode=str(intents.iloc[0]["execution_mode"]),
                    as_of_date=as_of_value,
                    execution_date=pd.Timestamp(intents.iloc[0]["execution_date"]).date(),
                    root=None,
                    component="broker_ibkr",
                    severity="info",
                    code="IBKR_SESSION_READY",
                    message="IBKR session and initial broker state sync completed.",
                    created_at_utc=created_at,
                    details={"starting_order_id": current_order_id},
                )
            )
            positions_df = _normalize_positions_snapshot(
                raw_positions=raw_positions,
                contract_master=contract_master,
                run_id=run_id_value,
                execution_mode=_execution_mode_from_broker_mode(broker_mode),
                snapshot_id=f"ibkr_pos_{stable_sha256_hex([run_id_value, as_of_value])[:16]}",
                captured_at_utc=created_at,
            )
            open_orders_df = _normalize_open_orders_snapshot(
                raw_open_orders=raw_open_orders,
                contract_master=contract_master,
                run_id=run_id_value,
                execution_mode=_execution_mode_from_broker_mode(broker_mode),
                snapshot_id=f"ibkr_open_{stable_sha256_hex([run_id_value, as_of_value])[:16]}",
                captured_at_utc=created_at,
            )
            callback_events: list[IbkrCallbackEvent] = position_events + open_order_events + account_events
            translated_requests = []
            pre_reconcile_rows = []
            broker_errors = []
            monitoring_action = _monitoring_action(
                monitoring=monitoring,
                run_id=run_id_value,
                as_of_date=as_of_value,
                execution_date=pd.Timestamp(intents.iloc[0]["execution_date"]).date(),
                default_action=None,
            )
            client_id = int(os.getenv(self.broker_config.client_id_env, "0") or 0)
            candidate_requests_by_root: dict[str, list] = {}
            candidate_rows_by_root: dict[str, list[int]] = {}

            for row_index, row in enumerate(intents.to_dict(orient="records")):
                enriched = {
                    **row,
                    "broker_name": self.broker_config.broker_name,
                    "broker_mode": broker_mode,
                    "account_id": resolved_account,
                    "updated_at_utc": created_at,
                }
                if str(row["status"]) == "rejected":
                    pre_reconcile_rows.append(enriched)
                    continue
                effective_action = monitoring_action or str(row["control_action"])
                if effective_action == "hold":
                    enriched.update(
                        {
                            "status": "rejected",
                            "broker_error_code": "HOLD_BLOCK",
                            "broker_error_message": "Monitoring final_action=hold blocks broker submission.",
                        }
                    )
                    journal_events.append(
                        _journal_event(
                            run_id=run_id_value,
                            execution_mode=str(row["execution_mode"]),
                            as_of_date=pd.Timestamp(row["as_of_date"]).date(),
                            execution_date=pd.Timestamp(row["execution_date"]).date(),
                            root=str(row["root"]),
                            component="broker_ibkr",
                            severity="warning",
                            code="IBKR_HOLD_REJECT",
                            message="Order intent rejected because monitoring is in hold mode.",
                            created_at_utc=created_at,
                            details={"order_intent_id": row["order_intent_id"]},
                        )
                    )
                    pre_reconcile_rows.append(enriched)
                    continue
                try:
                    resolved_contract = resolve_ibkr_contract(
                        root=str(row["root"]),
                        raw_symbol=str(row["raw_symbol"]),
                        contract_master=contract_master,
                        instrument_master=self.instrument_master,
                    )
                    translated = translate_order_request(
                        intent_row=row,
                        resolved_contract=resolved_contract,
                        broker_mode=broker_mode,
                        account_id=resolved_account,
                        client_id=client_id,
                        broker_config=self.broker_config,
                        contracts_daily=contracts_daily,
                        created_at_utc=created_at,
                    )
                except (IbkrContractResolutionError, IbkrTranslationError) as exc:
                    enriched.update(
                        {
                            "status": "rejected",
                            "broker_error_code": type(exc).__name__,
                            "broker_error_message": str(exc),
                        }
                    )
                    journal_events.append(
                        _journal_event(
                            run_id=run_id_value,
                            execution_mode=str(row["execution_mode"]),
                            as_of_date=pd.Timestamp(row["as_of_date"]).date(),
                            execution_date=pd.Timestamp(row["execution_date"]).date(),
                            root=str(row["root"]),
                            component="broker_ibkr",
                            severity="warning",
                            code="IBKR_TRANSLATION_REJECTED",
                            message="Order intent was rejected before send during IBKR translation.",
                            created_at_utc=created_at,
                            details={
                                "order_intent_id": row["order_intent_id"],
                                "reason": str(exc),
                            },
                        )
                    )
                    pre_reconcile_rows.append(enriched)
                    continue
                enriched.update(
                    {
                        "broker_contract_key": translated.broker_contract_key,
                        "broker_request_id": translated.broker_request_id,
                        "account_id": resolved_account,
                        "broker_name": self.broker_config.broker_name,
                        "broker_mode": broker_mode,
                        "status": "not_sent" if broker_mode == "shadow_only" else "submitted",
                    }
                )
                translated_requests.append(translated)
                candidate_requests_by_root.setdefault(str(row["root"]), []).append(translated)
                candidate_rows_by_root.setdefault(str(row["root"]), []).append(len(pre_reconcile_rows))
                pre_reconcile_rows.append(enriched)
                journal_events.append(
                    _journal_event(
                        run_id=run_id_value,
                        execution_mode=str(row["execution_mode"]),
                        as_of_date=pd.Timestamp(row["as_of_date"]).date(),
                        execution_date=pd.Timestamp(row["execution_date"]).date(),
                        root=str(row["root"]),
                        component="broker_ibkr",
                        severity="info",
                        code="IBKR_INTENT_TRANSLATED",
                        message="Order intent translated into an IBKR futures order preview.",
                        created_at_utc=created_at,
                        details={
                            "order_intent_id": row["order_intent_id"],
                            "broker_request_id": translated.broker_request_id,
                        },
                    )
                )

            for root, requests in candidate_requests_by_root.items():
                row_indexes = candidate_rows_by_root.get(root, [])
                effective_action = monitoring_action or str(
                    pre_reconcile_rows[row_indexes[0]]["control_action"]
                )
                if effective_action != "reduce_only":
                    continue
                violation = validate_reduce_only_root(
                    root=root,
                    root_requests=requests,
                    snapshot_positions=positions_df,
                )
                if violation is None:
                    continue
                request_ids = {request.broker_request_id for request in requests}
                translated_requests = [
                    request
                    for request in translated_requests
                    if request.broker_request_id not in request_ids
                ]
                for row_index in row_indexes:
                    pre_reconcile_rows[row_index].update(
                        {
                            "status": "rejected",
                            "broker_error_code": "REDUCE_ONLY_BLOCK",
                            "broker_error_message": violation,
                        }
                    )
                journal_events.append(
                    _journal_event(
                        run_id=run_id_value,
                        execution_mode=str(intents.iloc[0]["execution_mode"]),
                        as_of_date=as_of_value,
                        execution_date=pd.Timestamp(intents.iloc[0]["execution_date"]).date(),
                        root=root,
                        component="broker_ibkr",
                        severity="warning",
                        code="IBKR_REDUCE_ONLY_REJECT",
                        message="Translated reduce-only intent would increase exposure or flip sign.",
                        created_at_utc=created_at,
                        details={"reason": violation},
                    )
                )

            if broker_mode == "paper_submit":
                _enforce_paper_submit_guard(self.broker_config, resolved_account)
                for translated in translated_requests:
                    order_id = current_order_id
                    current_order_id += 1
                    events = client.place_order(
                        translated,
                        ib_order_id=order_id,
                        timeout_seconds=self.broker_config.callback_timeout_seconds,
                    )
                    callback_events.extend(events)
                    journal_events.append(
                        _journal_event(
                            run_id=run_id_value,
                            execution_mode=str(intents.iloc[0]["execution_mode"]),
                            as_of_date=as_of_value,
                            execution_date=pd.Timestamp(intents.iloc[0]["execution_date"]).date(),
                            root=translated.root,
                            component="broker_ibkr",
                            severity="info",
                            code="IBKR_ORDER_SUBMITTED",
                            message="Paper order submitted to IBKR adapter.",
                            created_at_utc=created_at,
                            details={
                                "order_intent_id": translated.order_intent_id,
                                "broker_request_id": translated.broker_request_id,
                                "ib_order_id": order_id,
                            },
                        )
                    )
                    for event in events:
                        if event.error_code is not None:
                            broker_errors.append(
                                normalize_broker_error(
                                    code=event.error_code,
                                    message=event.error_message or "",
                                    broker_request_id=event.broker_request_id,
                                    ib_order_id=event.ib_order_id,
                        )
                    )
            translated_df = pd.DataFrame([request.to_dict() for request in translated_requests])
            callback_df = _callback_events_frame(callback_events)
            pre_reconcile_df = normalize_order_intents_frame(pd.DataFrame(pre_reconcile_rows))
            reconciled_df, reconciliation_report = reconcile_order_intents(
                order_intents=pre_reconcile_df,
                callback_events=callback_events,
                broker_errors=broker_errors,
            )
            journal_events.append(
                _journal_event(
                    run_id=run_id_value,
                    execution_mode=str(intents.iloc[0]["execution_mode"]),
                    as_of_date=as_of_value,
                    execution_date=pd.Timestamp(intents.iloc[0]["execution_date"]).date(),
                    root=None,
                    component="broker_ibkr",
                    severity="info",
                    code="IBKR_RECONCILIATION_COMPLETED",
                    message="IBKR callbacks were reconciled into normalized project statuses.",
                    created_at_utc=created_at,
                    details={
                        "callback_count": len(callback_events),
                        "status_counts": reconciliation_report["status_counts"],
                    },
                )
            )
        finally:
            client.disconnect()

        events_df = _journal_events_frame(journal_events)
        self._write_parquet(workspace / "broker_positions_snapshot.parquet", positions_df)
        self._write_json(workspace / "broker_positions_snapshot.json", positions_df.to_dict("records"))
        self._write_parquet(workspace / "broker_open_orders_snapshot.parquet", open_orders_df)
        self._write_json(
            workspace / "broker_open_orders_snapshot.json",
            open_orders_df.to_dict("records"),
        )
        self._write_parquet(workspace / "order_intents.parquet", reconciled_df)
        self._write_json(workspace / "order_intents.json", reconciled_df.to_dict("records"))
        self._write_parquet(workspace / "journal_events.parquet", events_df)
        self._write_json(workspace / "journal_events.json", events_df.to_dict("records"))
        self._write_json(
            workspace / "translated_order_requests.json",
            translated_df.to_dict("records"),
        )
        self._write_json(
            workspace / "broker_callback_events.json",
            callback_df.to_dict("records"),
        )
        self._write_json(
            workspace / "account_summary_snapshot.json",
            account_summary.to_dict() if account_summary is not None else {},
        )
        self._write_json(
            workspace / "manifest.json",
            {
                "run_id": run_id_value,
                "broker_mode": broker_mode,
                "as_of_date": as_of_value,
                "workspace": workspace.as_posix(),
            },
        )
        self._write_json(workspace / "reconciliation_report.json", reconciliation_report)
        (workspace / "broker_report.md").write_text(
            _broker_report_markdown(reconciliation_report),
            encoding="utf-8",
        )
        params_hash = request_params_hash(
            {
                "command": "submit-intents",
                "run_id": run_id_value,
                "broker_mode": broker_mode,
                "as_of_date": as_of_value,
                "account_id": resolved_account,
            }
        )
        self._register_output(
            logical_table="broker_positions_snapshot",
            path=workspace / "broker_positions_snapshot.parquet",
            run_id=run_id_value,
            request_hash=params_hash,
            logical_date=as_of_value,
        )
        self._register_output(
            logical_table="broker_open_orders_snapshot",
            path=workspace / "broker_open_orders_snapshot.parquet",
            run_id=run_id_value,
            request_hash=params_hash,
            logical_date=as_of_value,
        )
        self._register_output(
            logical_table="order_intents",
            path=workspace / "order_intents.parquet",
            run_id=run_id_value,
            request_hash=params_hash,
            logical_date=as_of_value,
        )
        self._register_output(
            logical_table="journal_events",
            path=workspace / "journal_events.parquet",
            run_id=run_id_value,
            request_hash=params_hash,
            logical_date=as_of_value,
        )
        self._register_run(
            command_name="broker-ibkr submit-intents",
            status="succeeded",
            artifact_path=workspace / "reconciliation_report.json",
            started_at=started_at,
            finished_at=_utc_now(),
            run_id=run_id_value,
        )
        return {
            "run_id": run_id_value,
            "broker_mode": broker_mode,
            "translated_request_count": int(len(translated_df)),
            "callback_count": int(len(callback_df)),
            "status_counts": reconciliation_report["status_counts"],
        }

    def qa(self, *, workspace: Path) -> dict[str, object]:
        resolved_workspace = workspace
        order_intents = normalize_order_intents_frame(
            read_parquet_dataset(resolved_workspace / "order_intents.parquet")
        )
        positions = read_parquet_dataset(resolved_workspace / "broker_positions_snapshot.parquet")
        open_orders = read_parquet_dataset(
            resolved_workspace / "broker_open_orders_snapshot.parquet"
        )
        translated_requests = json.loads(
            (resolved_workspace / "translated_order_requests.json").read_text(encoding="utf-8")
        ) if (resolved_workspace / "translated_order_requests.json").exists() else []
        manifest = json.loads(
            (resolved_workspace / "manifest.json").read_text(encoding="utf-8")
        ) if (resolved_workspace / "manifest.json").exists() else {}
        broker_mode = manifest.get("broker_mode", "unknown")
        issues: list[dict[str, object]] = []
        if order_intents.empty:
            issues.append(
                {"severity": "error", "code": "missing_order_intents", "message": "order_intents is empty"}
            )
        allowed_statuses = {
            "not_sent",
            "submitted",
            "pre_submitted",
            "filled",
            "partially_filled",
            "cancelled",
            "rejected",
            "api_error",
            "unknown",
        }
        invalid_statuses = sorted(set(order_intents["status"].astype(str)) - allowed_statuses)
        if invalid_statuses:
            issues.append(
                {
                    "severity": "error",
                    "code": "invalid_status",
                    "message": f"unexpected normalized broker statuses: {invalid_statuses}",
                }
            )
        if not positions.empty and (
            positions.get("source", pd.Series(dtype="object")).fillna("").astype(str) != "ibkr_tws_api"
        ).any():
            issues.append(
                {
                    "severity": "error",
                    "code": "invalid_positions_source",
                    "message": "broker_positions_snapshot source must be ibkr_tws_api",
                }
            )
        if not open_orders.empty and (
            open_orders.get("source", pd.Series(dtype="object")).fillna("").astype(str) != "ibkr_tws_api"
        ).any():
            issues.append(
                {
                    "severity": "error",
                    "code": "invalid_open_orders_source",
                    "message": "broker_open_orders_snapshot source must be ibkr_tws_api",
                }
            )
        if broker_mode == "shadow_only":
            shadow_statuses = set(order_intents["status"].astype(str))
            if shadow_statuses & {"submitted", "pre_submitted", "filled", "partially_filled"}:
                issues.append(
                    {
                        "severity": "error",
                        "code": "shadow_submitted",
                        "message": "shadow_only workspace contains submitted/fill statuses.",
                    }
                )
        if broker_mode == "paper_submit":
            translated_frame = pd.DataFrame(translated_requests)
            if not translated_frame.empty and translated_frame["limit_price"].isna().any():
                issues.append(
                    {
                        "severity": "error",
                        "code": "paper_missing_limit_price",
                        "message": "paper_submit translated requests must have deterministic limit prices.",
                    }
                )
        report = {
            "workspace": resolved_workspace.as_posix(),
            "broker_mode": broker_mode,
            "row_count": int(len(order_intents)),
            "issues": issues,
            "has_errors": any(issue["severity"] == "error" for issue in issues),
        }
        self._write_json(resolved_workspace / "qa_report.json", report)
        (resolved_workspace / "qa_report.md").write_text(
            _broker_report_markdown(report),
            encoding="utf-8",
        )
        run_id = str(manifest.get("run_id", resolved_workspace.name))
        self._register_run(
            command_name="broker-ibkr qa",
            status="failed" if report["has_errors"] else "succeeded",
            artifact_path=resolved_workspace / "qa_report.json",
            started_at=_utc_now(),
            finished_at=_utc_now(),
            run_id=run_id,
        )
        return report

    def _register_output(
        self,
        *,
        logical_table: str,
        path: Path,
        run_id: str,
        request_hash: str,
        logical_date: date,
    ) -> None:
        df = read_parquet_dataset(path)
        row = FileRegistryRow(
            file_id=make_file_id(f"broker_{run_id}", logical_table, path),
            snapshot_id=f"broker_{run_id}",
            run_id=run_id,
            logical_table=logical_table,
            source_schema="broker_ibkr_v1",
            path=path.as_posix(),
            content_sha256=file_sha256(path),
            row_count=int(len(df)),
            min_trade_date=logical_date,
            max_trade_date=logical_date,
            request_params_hash=request_hash,
            cached=False,
            created_at_utc=_utc_now(),
        )
        append_file_registry_row(self.meta_root / "data_file_registry", row)

    def _register_run(
        self,
        *,
        command_name: str,
        status: str,
        artifact_path: Path,
        started_at: datetime,
        finished_at: datetime,
        run_id: str,
    ) -> None:
        row = RunRegistryRow(
            run_id=make_run_id(),
            run_type="broker_ibkr",
            command_name=command_name,
            status=status,
            started_at_utc=started_at,
            finished_at_utc=finished_at,
            data_snapshot_id=f"broker_{run_id}",
            config_hash=config_hash(
                [
                    self.repo_root / "config" / "settings.base.yml",
                    self.repo_root / "config" / "data_schema.yml",
                    self.repo_root / "config" / "instruments.yml",
                ]
            ),
            git_commit=_git_commit(self.repo_root),
            notes=[],
            artifact_path=artifact_path.as_posix(),
        )
        append_run_registry_row(self.meta_root / "run_registry", row)

    def _write_parquet(self, path: Path, df: pd.DataFrame) -> None:
        ensure_directory(path.parent)
        write_parquet_part(df, path)

    def _write_json(self, path: Path, payload: object) -> None:
        ensure_directory(path.parent)
        path.write_bytes(canonical_json_bytes(payload))


def _resolve_output_dir(repo_root: Path, run_id: str, output_dir: Path | None) -> Path:
    if output_dir is not None:
        return ensure_directory(output_dir)
    return ensure_directory(repo_root / "data" / "shadow" / "broker" / "ibkr" / f"run_id={run_id}")


def _resolve_account_id(config: BrokerIbkrConfig, account_id: str | None) -> str:
    resolved = account_id or os.getenv(config.account_env)
    if resolved in (None, ""):
        raise IbkrBrokerServiceError("IBKR account id is required")
    return str(resolved)


def _validate_broker_mode(config: BrokerIbkrConfig, broker_mode: str) -> None:
    if broker_mode not in set(config.supported_modes):
        raise IbkrBrokerServiceError(f"unsupported broker_mode {broker_mode!r}")


def _validate_account(config: BrokerIbkrConfig, account_id: str, broker_mode: BrokerMode) -> None:
    if account_id not in set(config.allowed_accounts):
        raise IbkrBrokerServiceError(f"IBKR account {account_id!r} is not on the configured allowlist")
    if broker_mode == "paper_submit" and not any(
        account_id.startswith(prefix) for prefix in config.paper_account_prefixes
    ):
        raise IbkrBrokerServiceError(
            f"IBKR account {account_id!r} is not a permitted paper account"
        )


def _enforce_paper_submit_guard(config: BrokerIbkrConfig, account_id: str) -> None:
    if os.getenv(config.paper_guard_env) != "1":
        raise IbkrBrokerServiceError("paper_submit requires CPDSHADOW_ENABLE_PAPER_SUBMIT=1")
    _validate_account(config, account_id, "paper_submit")


def _normalize_positions_snapshot(
    *,
    raw_positions: list[IbkrBrokerPosition],
    contract_master: pd.DataFrame,
    run_id: str,
    execution_mode: str,
    snapshot_id: str,
    captured_at_utc: datetime,
) -> pd.DataFrame:
    rows = []
    for raw in raw_positions:
        try:
            resolved = resolve_broker_snapshot_contract(
                local_symbol=raw.local_symbol,
                exchange=raw.exchange,
                contract_master=contract_master,
            )
        except IbkrContractResolutionError:
            resolved = resolve_broker_snapshot_contract(
                local_symbol=None,
                exchange=None,
                contract_master=pd.DataFrame(),
            )
        rows.append(
            IbkrPositionSnapshotRow(
                position_snapshot_id=snapshot_id,
                run_id=run_id,
                execution_mode=execution_mode,
                account_id=raw.account_id,
                broker_contract_id=raw.broker_contract_id,
                broker_contract_key=resolved.broker_contract_key,
                raw_symbol=resolved.raw_symbol,
                root=resolved.root,
                position_contracts=int(raw.quantity),
                avg_cost=raw.avg_cost,
                snapshot_time_utc=captured_at_utc,
                source="ibkr_tws_api",
            ).to_dict()
        )
    return pd.DataFrame(rows, columns=_POSITION_SNAPSHOT_COLUMNS)


def _normalize_open_orders_snapshot(
    *,
    raw_open_orders: list[IbkrBrokerOpenOrder],
    contract_master: pd.DataFrame,
    run_id: str,
    execution_mode: str,
    snapshot_id: str,
    captured_at_utc: datetime,
) -> pd.DataFrame:
    rows = []
    for raw in raw_open_orders:
        try:
            resolved = resolve_broker_snapshot_contract(
                local_symbol=raw.local_symbol,
                exchange=raw.exchange,
                contract_master=contract_master,
            )
        except IbkrContractResolutionError:
            resolved = resolve_broker_snapshot_contract(
                local_symbol=None,
                exchange=None,
                contract_master=pd.DataFrame(),
            )
        rows.append(
            IbkrOpenOrderSnapshotRow(
                open_orders_snapshot_id=snapshot_id,
                run_id=run_id,
                execution_mode=execution_mode,
                account_id=raw.account_id,
                captured_at_utc=captured_at_utc,
                client_id=raw.client_id,
                ib_order_id=raw.ib_order_id,
                perm_id=raw.perm_id,
                broker_contract_id=raw.broker_contract_id,
                broker_contract_key=resolved.broker_contract_key,
                raw_symbol=resolved.raw_symbol,
                root=resolved.root,
                action=raw.action,
                total_quantity=int(raw.total_quantity),
                filled_quantity=int(raw.filled_quantity),
                remaining_quantity=int(raw.remaining_quantity),
                order_type=raw.order_type,
                tif=raw.tif,
                limit_price=raw.limit_price,
                status=raw.status,
                source="ibkr_tws_api",
            ).to_dict()
        )
    return pd.DataFrame(rows, columns=_OPEN_ORDER_SNAPSHOT_COLUMNS)


def _monitoring_action(
    *,
    monitoring: pd.DataFrame,
    run_id: str,
    as_of_date: date,
    execution_date: date,
    default_action: str | None,
) -> str | None:
    if monitoring.empty:
        return default_action
    working = monitoring.copy()
    working["as_of_date"] = pd.to_datetime(working["as_of_date"]).dt.date
    working["execution_date"] = pd.to_datetime(working["execution_date"]).dt.date
    filtered = working[
        (working["run_id"].astype(str) == str(run_id))
        & (working["as_of_date"] == as_of_date)
        & (working["execution_date"] == execution_date)
    ]
    if filtered.empty:
        return default_action
    actions = sorted(set(filtered["final_action"].dropna().astype(str)))
    return actions[-1] if actions else default_action


def _journal_event(
    *,
    run_id: str,
    execution_mode: str,
    as_of_date: date | None,
    execution_date: date | None,
    root: str | None,
    component: str,
    severity: str,
    code: str,
    message: str,
    created_at_utc: datetime,
    details: dict[str, object],
) -> JournalEvent:
    details_json = json.dumps(details, sort_keys=True, separators=(",", ":")) if details else None
    return JournalEvent(
        event_id=f"je_{stable_sha256_hex([run_id, code, root, details_json])[:16]}",
        run_id=run_id,
        execution_mode=execution_mode,
        as_of_date=as_of_date,
        execution_date=execution_date,
        root=root,
        component=component,
        severity=severity,  # type: ignore[arg-type]
        code=code,
        message=message,
        details_json=details_json,
        created_at_utc=created_at_utc,
    )


def _journal_events_frame(events: list[JournalEvent]) -> pd.DataFrame:
    payload = [event.to_dict() for event in events]
    return pd.DataFrame(payload, columns=_JOURNAL_COLUMNS)


def _callback_events_frame(events: list[IbkrCallbackEvent]) -> pd.DataFrame:
    return pd.DataFrame([event.to_dict() for event in events])


def _broker_report_markdown(payload: dict[str, object]) -> str:
    lines = ["# WP13 Broker Report", ""]
    for key, value in payload.items():
        lines.append(f"- {key}: `{value}`")
    return "\n".join(lines)


def _execution_mode_from_broker_mode(broker_mode: BrokerMode) -> str:
    return "shadow" if broker_mode == "shadow_only" else "paper"


def _normalize_datetime(value: datetime | None) -> datetime:
    current = value or datetime.now(UTC)
    if current.tzinfo is None:
        current = current.replace(tzinfo=UTC)
    return current.astimezone(UTC)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _git_commit(repo_root: Path) -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_root,
            check=True,
            capture_output=True,
            text=True,
        )
    except Exception:
        return "unknown-local"
    return result.stdout.strip() or "unknown-local"
