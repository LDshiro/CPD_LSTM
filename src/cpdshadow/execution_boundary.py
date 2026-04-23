from __future__ import annotations

import json
import subprocess
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
from pathlib import Path

import pandas as pd

from cpdshadow.config import (
    DataSchemaConfig,
    ExecutionBoundaryConfig,
    load_data_schema_yaml,
    load_execution_boundary_yaml,
)
from cpdshadow.dry_run_adapter import FinalOrderIntent, finalize_dry_run_intents
from cpdshadow.ids import (
    canonical_json_bytes,
    config_hash,
    file_sha256,
    make_file_id,
    make_run_id,
    request_params_hash,
    stable_sha256_hex,
)
from cpdshadow.order_intents import (
    JournalEvent,
    OrderIntentPlanningError,
    PlannedOrderIntent,
    plan_order_intents,
    select_position_snapshot,
)
from cpdshadow.storage.parquet_io import ensure_directory, read_parquet_dataset, write_parquet_part
from cpdshadow.storage.registry import (
    FileRegistryRow,
    RunRegistryRow,
    append_file_registry_row,
    append_run_registry_row,
)


_ORDER_INTENT_COLUMNS = {
    "order_intent_id",
    "run_id",
    "strategy_id",
    "execution_mode",
    "as_of_date",
    "execution_date",
    "root",
    "raw_symbol",
    "broker_contract_id",
    "broker_name",
    "broker_mode",
    "broker_contract_key",
    "broker_request_id",
    "ib_order_id",
    "perm_id",
    "account_id",
    "side",
    "quantity",
    "order_type",
    "limit_price",
    "reason",
    "status",
    "control_action",
    "position_snapshot_id",
    "sequence_no",
    "rejection_reason",
    "broker_error_code",
    "broker_error_message",
    "created_at_utc",
    "submitted_at_utc",
    "updated_at_utc",
}
_ORDER_INTENT_COLUMN_ORDER = [
    "order_intent_id",
    "run_id",
    "strategy_id",
    "execution_mode",
    "as_of_date",
    "execution_date",
    "root",
    "raw_symbol",
    "broker_contract_id",
    "broker_name",
    "broker_mode",
    "broker_contract_key",
    "broker_request_id",
    "ib_order_id",
    "perm_id",
    "account_id",
    "side",
    "quantity",
    "order_type",
    "limit_price",
    "reason",
    "status",
    "control_action",
    "position_snapshot_id",
    "sequence_no",
    "rejection_reason",
    "broker_error_code",
    "broker_error_message",
    "created_at_utc",
    "submitted_at_utc",
    "updated_at_utc",
]
_TARGET_COLUMNS = {
    "run_id",
    "strategy_id",
    "execution_mode",
    "as_of_date",
    "execution_date",
    "root",
    "lead_raw_symbol",
    "target_contracts",
    "current_contracts",
    "order_delta_contracts",
    "control_action",
}
_JOURNAL_EVENT_COLUMN_ORDER = [
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
_POSITION_COLUMNS = {
    "position_snapshot_id",
    "execution_mode",
    "raw_symbol",
    "root",
    "position_contracts",
    "snapshot_time_utc",
}


class ExecutionBoundaryError(ValueError):
    pass


@dataclass(frozen=True)
class ExecutionBoundaryQaReport:
    run_id: str
    execution_mode: str
    position_snapshot_id: str | None
    accepted_intents: int
    rejected_intents: int
    counts_by_reason: dict[str, int]
    counts_by_control_action: dict[str, int]
    root_results: list[dict[str, object]]
    issues: list[dict[str, object]]
    broker_calls_made: bool

    @property
    def has_errors(self) -> bool:
        return any(issue["severity"] == "error" for issue in self.issues)

    def to_dict(self) -> dict[str, object]:
        return {
            "run_id": self.run_id,
            "execution_mode": self.execution_mode,
            "position_snapshot_id": self.position_snapshot_id,
            "accepted_intents": self.accepted_intents,
            "rejected_intents": self.rejected_intents,
            "counts_by_reason": self.counts_by_reason,
            "counts_by_control_action": self.counts_by_control_action,
            "root_results": self.root_results,
            "issues": self.issues,
            "broker_calls_made": self.broker_calls_made,
            "has_errors": self.has_errors,
        }


def qa_execution_boundary(
    *,
    targets: pd.DataFrame,
    snapshot_positions: pd.DataFrame,
    final_order_intents: pd.DataFrame,
    monitoring: pd.DataFrame | None = None,
    config: ExecutionBoundaryConfig,
) -> ExecutionBoundaryQaReport:
    issues: list[dict[str, object]] = []
    missing_targets = sorted(_TARGET_COLUMNS - set(targets.columns))
    if missing_targets:
        issues.append(
            {
                "severity": "error",
                "code": "missing_target_columns",
                "message": f"targets_daily is missing required columns: {missing_targets}",
            }
        )
        return ExecutionBoundaryQaReport(
            run_id="unknown",
            execution_mode="unknown",
            position_snapshot_id=None,
            accepted_intents=0,
            rejected_intents=0,
            counts_by_reason={},
            counts_by_control_action={},
            root_results=[],
            issues=issues,
            broker_calls_made=False,
        )
    missing_positions = sorted(_POSITION_COLUMNS - set(snapshot_positions.columns))
    if missing_positions:
        issues.append(
            {
                "severity": "error",
                "code": "missing_position_columns",
                "message": f"broker_positions_snapshot is missing required columns: {missing_positions}",
            }
        )
    missing_intents = sorted(_ORDER_INTENT_COLUMNS - set(final_order_intents.columns))
    if missing_intents:
        issues.append(
            {
                "severity": "error",
                "code": "missing_order_intent_columns",
                "message": f"order_intents is missing required columns: {missing_intents}",
            }
        )
        return ExecutionBoundaryQaReport(
            run_id=str(targets.iloc[0]["run_id"]),
            execution_mode=str(targets.iloc[0]["execution_mode"]),
            position_snapshot_id=None,
            accepted_intents=0,
            rejected_intents=0,
            counts_by_reason={},
            counts_by_control_action={},
            root_results=[],
            issues=issues,
            broker_calls_made=False,
        )

    targets_working = _normalize_target_batch(targets)
    intents_working = _normalize_intents(final_order_intents)
    positions_working = _normalize_positions(snapshot_positions)
    monitoring_working = _normalize_monitoring(monitoring) if monitoring is not None else None
    run_id = str(targets_working.iloc[0]["run_id"])
    execution_mode = str(targets_working.iloc[0]["execution_mode"])
    position_snapshot_id = (
        str(intents_working["position_snapshot_id"].iloc[0])
        if not intents_working.empty
        else (
            str(positions_working["position_snapshot_id"].iloc[0])
            if not positions_working.empty
            else None
        )
    )

    if monitoring_working is not None and not monitoring_working.empty:
        monitoring_actions = set(monitoring_working["final_action"].astype(str))
        target_actions = set(targets_working["control_action"].astype(str))
        if len(monitoring_actions) != 1 or monitoring_actions != target_actions:
            issues.append(
                {
                    "severity": "error",
                    "code": "monitoring_alignment_failed",
                    "message": "monitoring_daily final_action does not align with target control_action.",
                }
            )

    invalid_statuses = sorted(
        set(intents_working["status"].astype(str)) - {"not_sent", "rejected"}
    )
    if invalid_statuses:
        issues.append(
            {
                "severity": "error",
                "code": "invalid_final_status",
                "message": f"finalized statuses must be only not_sent/rejected, got {invalid_statuses}",
            }
        )
    if (pd.to_numeric(intents_working["quantity"], errors="coerce").fillna(0) <= 0).any():
        issues.append(
            {
                "severity": "error",
                "code": "nonpositive_quantity",
                "message": "final order intents contain nonpositive quantity values.",
            }
        )
    if intents_working["broker_contract_id"].notna().any():
        issues.append(
            {
                "severity": "error",
                "code": "broker_contract_id_present",
                "message": "broker_contract_id must remain null in WP12.",
            }
        )
    if intents_working["limit_price"].notna().any():
        issues.append(
            {
                "severity": "error",
                "code": "limit_price_present",
                "message": "limit_price must remain null in WP12.",
            }
        )
    expected_sequence = list(range(1, len(intents_working) + 1))
    actual_sequence = intents_working["sequence_no"].astype(int).tolist()
    if actual_sequence != expected_sequence:
        issues.append(
            {
                "severity": "error",
                "code": "nondeterministic_sequence",
                "message": "sequence_no must be contiguous and preserve deterministic ordering.",
            }
        )

    accepted = intents_working[intents_working["status"].astype(str) == "not_sent"].copy()
    rejected = intents_working[intents_working["status"].astype(str) == "rejected"].copy()
    root_results: list[dict[str, object]] = []

    current_inventory = _inventory_by_root(positions_working)
    final_inventory = _inventory_by_root(positions_working)
    for record in accepted.sort_values(["sequence_no"], kind="stable").to_dict(orient="records"):
        final_inventory.setdefault(str(record["root"]), {})
        delta = int(record["quantity"]) if str(record["side"]) == "buy" else -int(record["quantity"])
        final_inventory[str(record["root"])][str(record["raw_symbol"])] = (
            final_inventory[str(record["root"])].get(str(record["raw_symbol"]), 0) + delta
        )

    for root, row in targets_working.sort_values(["root"], kind="stable").set_index("root").iterrows():
        root_current = _drop_zero_positions(current_inventory.get(str(root), {}))
        root_final = _drop_zero_positions(final_inventory.get(str(root), {}))
        root_accepted = accepted[accepted["root"].astype(str) == str(root)]
        root_rejected = rejected[rejected["root"].astype(str) == str(root)]
        current_total = sum(root_current.values())
        final_total = sum(root_final.values())
        control_action = str(row["control_action"])
        lead_raw_symbol = str(row["lead_raw_symbol"])
        target_contracts = int(row["target_contracts"])
        mixed_sign = len({1 if qty > 0 else -1 for qty in root_current.values() if qty != 0}) > 1
        root_issue_count_before = len(issues)

        if mixed_sign:
            issues.append(
                {
                    "severity": "error",
                    "code": "mixed_sign_inventory_present",
                    "message": f"Root {root} still has mixed-sign current inventory, which WP12 does not support.",
                    "root": str(root),
                }
            )

        if control_action == "hold":
            if not root_accepted.empty:
                issues.append(
                    {
                        "severity": "error",
                        "code": "hold_emitted_orders",
                        "message": f"Hold root {root} emitted accepted order intents.",
                        "root": str(root),
                    }
                )
            if root_current != root_final:
                issues.append(
                    {
                        "severity": "error",
                        "code": "hold_changed_inventory",
                        "message": f"Hold root {root} changed inventory during QA simulation.",
                        "root": str(root),
                    }
                )
        elif control_action in {"run_cpd_lstm", "fallback_tsmom"}:
            nonlead = {
                symbol: qty for symbol, qty in root_final.items() if symbol != lead_raw_symbol and qty != 0
            }
            if nonlead:
                issues.append(
                    {
                        "severity": "error",
                        "code": "nonlead_inventory_remaining",
                        "message": f"Root {root} still has non-lead inventory after accepted intents.",
                        "root": str(root),
                    }
                )
            if int(root_final.get(lead_raw_symbol, 0)) != target_contracts:
                issues.append(
                    {
                        "severity": "error",
                        "code": "lead_target_mismatch",
                        "message": f"Root {root} final lead inventory does not match target_contracts.",
                        "root": str(root),
                    }
                )
        elif control_action == "reduce_only":
            if abs(final_total) > abs(current_total):
                issues.append(
                    {
                        "severity": "error",
                        "code": "reduce_only_exposure_increase",
                        "message": f"Reduce-only root {root} increased absolute exposure.",
                        "root": str(root),
                    }
                )
            if current_total == 0 and final_total != 0:
                issues.append(
                    {
                        "severity": "error",
                        "code": "reduce_only_opened_from_flat",
                        "message": f"Reduce-only root {root} opened exposure from a flat position.",
                        "root": str(root),
                    }
                )
            if current_total != 0 and final_total != 0:
                if (current_total > 0 and final_total < 0) or (current_total < 0 and final_total > 0):
                    issues.append(
                        {
                            "severity": "error",
                            "code": "reduce_only_sign_flip",
                            "message": f"Reduce-only root {root} flipped sign through zero.",
                            "root": str(root),
                        }
                    )
            remaining_nonlead = {
                symbol: qty for symbol, qty in root_final.items() if symbol != lead_raw_symbol and qty != 0
            }
            if not root_accepted.empty and remaining_nonlead:
                issues.append(
                    {
                        "severity": "error",
                        "code": "reduce_only_nonlead_remaining",
                        "message": f"Reduce-only root {root} left non-lead inventory after accepted intents.",
                        "root": str(root),
                    }
                )

        root_errors = len(issues) - root_issue_count_before
        root_results.append(
            {
                "root": str(root),
                "control_action": control_action,
                "lead_raw_symbol": lead_raw_symbol,
                "target_contracts": int(target_contracts),
                "current_total": int(current_total),
                "final_total": int(final_total),
                "accepted_intent_count": int(len(root_accepted)),
                "rejected_intent_count": int(len(root_rejected)),
                "passed": root_errors == 0,
                "current_inventory": root_current,
                "final_inventory": root_final,
            }
        )

    return ExecutionBoundaryQaReport(
        run_id=run_id,
        execution_mode=execution_mode,
        position_snapshot_id=position_snapshot_id,
        accepted_intents=int(len(accepted)),
        rejected_intents=int(len(rejected)),
        counts_by_reason=_counts_by_column(intents_working, "reason"),
        counts_by_control_action=_counts_by_column(intents_working, "control_action"),
        root_results=root_results,
        issues=issues,
        broker_calls_made=False,
    )


class ExecutionBoundaryService:
    def __init__(
        self,
        *,
        repo_root: Path,
        config_path: Path | None = None,
        data_schema_path: Path | None = None,
    ) -> None:
        self.repo_root = repo_root
        self.config_path = config_path or (repo_root / "config" / "execution_boundary.yml")
        self.data_schema_path = data_schema_path or (repo_root / "config" / "data_schema.yml")
        self.config: ExecutionBoundaryConfig = load_execution_boundary_yaml(self.config_path)
        self.data_schema: DataSchemaConfig = load_data_schema_yaml(self.data_schema_path)
        self.meta_root = repo_root / "data" / "meta"

    def build_intents(
        self,
        *,
        targets_path: Path,
        positions_path: Path,
        contract_master_path: Path,
        monitoring_path: Path | None,
        output_dir: Path | None,
        run_id: str | None,
        strategy_id: str | None,
        execution_mode: str | None,
        as_of_date: date | None,
        execution_date: date | None,
        position_snapshot_id: str | None,
        strict: bool,
        created_at_utc: datetime | None,
    ) -> dict[str, object]:
        started_at = _utc_now()
        targets = read_parquet_dataset(targets_path)
        positions = read_parquet_dataset(positions_path)
        contract_master = read_parquet_dataset(contract_master_path)
        monitoring = read_parquet_dataset(monitoring_path) if monitoring_path is not None else pd.DataFrame()
        filtered_targets = _filter_targets(
            targets,
            run_id=run_id,
            strategy_id=strategy_id,
            execution_mode=execution_mode,
            as_of_date=as_of_date,
            execution_date=execution_date,
        )
        batch = _batch_identity(filtered_targets)
        workspace = _resolve_output_dir(
            repo_root=self.repo_root,
            run_id=batch["run_id"],
            output_dir=output_dir,
        )
        created_at = _normalize_datetime(created_at_utc)
        monitor_events = _validate_monitoring_alignment(
            monitoring=monitoring,
            targets=filtered_targets,
            strict=strict,
            fail_on_missing=self.config.validation.fail_on_missing_monitoring_alignment,
            created_at_utc=created_at,
        )
        selected_positions, selection = select_position_snapshot(
            positions,
            execution_mode=batch["execution_mode"],
            position_snapshot_id=position_snapshot_id,
            decision_time_utc=created_at,
        )
        intents, plan_events, summary = plan_order_intents(
            targets=filtered_targets,
            snapshot_positions=selected_positions,
            contract_master=contract_master,
            config=self.config,
            created_at_utc=created_at,
        )
        all_events = monitor_events + plan_events + [
            _journal_event(
                run_id=batch["run_id"],
                execution_mode=batch["execution_mode"],
                as_of_date=batch["as_of_date"],
                execution_date=batch["execution_date"],
                root=None,
                component="execution_boundary",
                severity="info",
                code="BUILD_INTENTS_COMPLETED",
                message="Planned order intents were built for the selected execution batch.",
                created_at_utc=created_at,
                details={
                    "planned_intent_count": summary["planned_intent_count"],
                    "blocked_roots": summary["blocked_roots"],
                    "position_snapshot_id": selection.position_snapshot_id,
                },
            )
        ]
        planned_df = _records_frame(intents, columns=_ORDER_INTENT_COLUMN_ORDER)
        events_df = self._append_workspace_events(workspace, all_events)
        self._write_workspace_frame(workspace / "planned_order_intents.parquet", planned_df)
        self._write_workspace_json(workspace / "planned_order_intents.json", planned_df)
        self._write_workspace_frame(workspace / "journal_events.parquet", events_df)
        self._write_workspace_json(workspace / "journal_events.json", events_df)
        build_summary = {
            **summary,
            "workspace": workspace.as_posix(),
            "position_snapshot_selection": selection.to_dict(),
            "input_paths": {
                "targets_path": targets_path.as_posix(),
                "positions_path": positions_path.as_posix(),
                "contract_master_path": contract_master_path.as_posix(),
                "monitoring_path": monitoring_path.as_posix() if monitoring_path is not None else None,
            },
        }
        self._write_json(workspace / "build_summary.json", build_summary)
        self._write_manifest(
            workspace=workspace,
            payload={
                "run_id": batch["run_id"],
                "execution_mode": batch["execution_mode"],
                "as_of_date": batch["as_of_date"],
                "execution_date": batch["execution_date"],
                "position_snapshot_id": selection.position_snapshot_id,
                "config_hash": config_hash([self.config_path, self.data_schema_path]),
                "build_summary_file": "build_summary.json",
            },
        )
        shadow_snapshot_id = _shadow_snapshot_id(batch)
        params_hash = request_params_hash(
            {
                "command": "build-intents",
                "batch": batch,
                "position_snapshot_id": selection.position_snapshot_id,
            }
        )
        self._register_output(
            logical_table="planned_order_intents",
            source_schema=self.config.version,
            path=workspace / "planned_order_intents.parquet",
            request_hash=params_hash,
            batch=batch,
            shadow_snapshot_id=shadow_snapshot_id,
        )
        self._register_output(
            logical_table="journal_events",
            source_schema=self.config.version,
            path=workspace / "journal_events.parquet",
            request_hash=params_hash,
            batch=batch,
            shadow_snapshot_id=shadow_snapshot_id,
        )
        self._register_run(
            command_name="broker-boundary build-intents",
            status="succeeded",
            artifact_path=workspace / "build_summary.json",
            batch=batch,
            shadow_snapshot_id=shadow_snapshot_id,
            started_at=started_at,
            finished_at=_utc_now(),
            notes=[],
        )
        return {
            "workspace": workspace.as_posix(),
            "run_id": batch["run_id"],
            "execution_mode": batch["execution_mode"],
            "planned_intent_count": int(len(planned_df)),
            "blocked_roots": summary["blocked_roots"],
            "position_snapshot_id": selection.position_snapshot_id,
        }

    def dry_run(
        self,
        *,
        planned_intents_path: Path,
        contract_master_path: Path,
        output_dir: Path | None,
        created_at_utc: datetime | None,
    ) -> dict[str, object]:
        started_at = _utc_now()
        planned_intents = read_parquet_dataset(planned_intents_path)
        contract_master = read_parquet_dataset(contract_master_path)
        if planned_intents.empty:
            raise ExecutionBoundaryError("planned_order_intents is empty")
        batch = _batch_identity(planned_intents)
        workspace = output_dir or planned_intents_path.parent
        workspace = _resolve_output_dir(repo_root=self.repo_root, run_id=batch["run_id"], output_dir=workspace)
        created_at = _normalize_datetime(created_at_utc)
        finalized, events, summary = finalize_dry_run_intents(
            planned_intents=planned_intents,
            contract_master=contract_master,
            config=self.config,
            created_at_utc=created_at,
        )
        finalized_df = _records_frame(finalized, columns=_ORDER_INTENT_COLUMN_ORDER)
        events_df = self._append_workspace_events(workspace, events)
        self._write_workspace_frame(workspace / "order_intents.parquet", finalized_df)
        self._write_workspace_json(workspace / "order_intents.json", finalized_df)
        self._write_workspace_frame(workspace / "journal_events.parquet", events_df)
        self._write_workspace_json(workspace / "journal_events.json", events_df)
        self._write_json(workspace / "dry_run_results.json", summary)
        self._write_manifest(
            workspace=workspace,
            payload={
                "run_id": batch["run_id"],
                "execution_mode": batch["execution_mode"],
                "as_of_date": batch["as_of_date"],
                "execution_date": batch["execution_date"],
                "dry_run_results_file": "dry_run_results.json",
            },
        )
        shadow_snapshot_id = _shadow_snapshot_id(batch)
        params_hash = request_params_hash({"command": "dry-run", "batch": batch})
        self._register_output(
            logical_table="order_intents",
            source_schema=self.config.version,
            path=workspace / "order_intents.parquet",
            request_hash=params_hash,
            batch=batch,
            shadow_snapshot_id=shadow_snapshot_id,
        )
        self._register_output(
            logical_table="journal_events",
            source_schema=self.config.version,
            path=workspace / "journal_events.parquet",
            request_hash=params_hash,
            batch=batch,
            shadow_snapshot_id=shadow_snapshot_id,
        )
        self._register_run(
            command_name="broker-boundary dry-run",
            status="succeeded",
            artifact_path=workspace / "dry_run_results.json",
            batch=batch,
            shadow_snapshot_id=shadow_snapshot_id,
            started_at=started_at,
            finished_at=_utc_now(),
            notes=[],
        )
        return {
            "workspace": workspace.as_posix(),
            "run_id": batch["run_id"],
            "execution_mode": batch["execution_mode"],
            **summary,
        }

    def qa(
        self,
        *,
        targets_path: Path,
        positions_path: Path,
        final_order_intents_path: Path,
        monitoring_path: Path | None,
        output_dir: Path | None,
        created_at_utc: datetime | None,
    ) -> ExecutionBoundaryQaReport:
        started_at = _utc_now()
        final_intents = read_parquet_dataset(final_order_intents_path)
        if final_intents.empty:
            raise ExecutionBoundaryError("order_intents is empty")
        batch = _batch_identity(final_intents)
        workspace = output_dir or final_order_intents_path.parent
        workspace = _resolve_output_dir(repo_root=self.repo_root, run_id=batch["run_id"], output_dir=workspace)
        created_at = _normalize_datetime(created_at_utc)
        targets = _filter_targets(
            read_parquet_dataset(targets_path),
            run_id=batch["run_id"],
            strategy_id=None,
            execution_mode=batch["execution_mode"],
            as_of_date=batch["as_of_date"],
            execution_date=batch["execution_date"],
        )
        positions = read_parquet_dataset(positions_path)
        selected_positions, _ = select_position_snapshot(
            positions,
            execution_mode=batch["execution_mode"],
            position_snapshot_id=(
                str(final_intents["position_snapshot_id"].iloc[0])
                if "position_snapshot_id" in final_intents.columns
                else None
            ),
        )
        monitoring = read_parquet_dataset(monitoring_path) if monitoring_path is not None else pd.DataFrame()
        filtered_monitoring = _filter_monitoring(
            monitoring,
            run_id=batch["run_id"],
            execution_mode=batch["execution_mode"],
            as_of_date=batch["as_of_date"],
            execution_date=batch["execution_date"],
        )
        report = qa_execution_boundary(
            targets=targets,
            snapshot_positions=selected_positions,
            final_order_intents=final_intents,
            monitoring=filtered_monitoring,
            config=self.config,
        )
        self._write_json(workspace / "qa_report.json", report.to_dict())
        markdown = _qa_report_markdown(report)
        (ensure_directory(workspace) / "dry_run_report.md").write_text(markdown, encoding="utf-8")
        events = [
            _journal_event(
                run_id=report.run_id,
                execution_mode=report.execution_mode,
                as_of_date=batch["as_of_date"],
                execution_date=batch["execution_date"],
                root=None,
                component="execution_qa",
                severity="warning" if report.has_errors else "info",
                code="EXECUTION_QA_COMPLETED",
                message="Execution-boundary QA completed.",
                created_at_utc=created_at,
                details={
                    "has_errors": report.has_errors,
                    "accepted_intents": report.accepted_intents,
                    "rejected_intents": report.rejected_intents,
                },
            )
        ]
        events_df = self._append_workspace_events(workspace, events)
        self._write_workspace_frame(workspace / "journal_events.parquet", events_df)
        self._write_workspace_json(workspace / "journal_events.json", events_df)
        self._write_manifest(
            workspace=workspace,
            payload={
                "run_id": batch["run_id"],
                "execution_mode": batch["execution_mode"],
                "as_of_date": batch["as_of_date"],
                "execution_date": batch["execution_date"],
                "qa_report_file": "qa_report.json",
                "dry_run_report_file": "dry_run_report.md",
            },
        )
        shadow_snapshot_id = _shadow_snapshot_id(batch)
        params_hash = request_params_hash({"command": "qa", "batch": batch})
        self._register_output(
            logical_table="journal_events",
            source_schema=self.config.version,
            path=workspace / "journal_events.parquet",
            request_hash=params_hash,
            batch=batch,
            shadow_snapshot_id=shadow_snapshot_id,
        )
        self._register_run(
            command_name="broker-boundary qa",
            status="failed" if report.has_errors else "succeeded",
            artifact_path=workspace / "qa_report.json",
            batch=batch,
            shadow_snapshot_id=shadow_snapshot_id,
            started_at=started_at,
            finished_at=_utc_now(),
            notes=[],
        )
        return report

    def _append_workspace_events(
        self,
        workspace: Path,
        events: list[JournalEvent],
    ) -> pd.DataFrame:
        existing_path = workspace / "journal_events.parquet"
        existing = read_parquet_dataset(existing_path)
        new_events = _records_frame(events, columns=_JOURNAL_EVENT_COLUMN_ORDER)
        combined = pd.concat([existing, new_events], ignore_index=True) if not existing.empty else new_events
        if combined.empty:
            return combined
        combined = combined.drop_duplicates(subset=["event_id"], keep="last")
        return combined.sort_values(
            ["created_at_utc", "component", "code", "root", "event_id"],
            kind="stable",
        ).reset_index(drop=True)

    def _write_workspace_frame(self, path: Path, df: pd.DataFrame) -> None:
        ensure_directory(path.parent)
        write_parquet_part(df, path)

    def _write_workspace_json(self, path: Path, df: pd.DataFrame) -> None:
        if not self.config.outputs.write_json:
            return
        records = df.to_dict(orient="records")
        path.write_bytes(canonical_json_bytes(records))

    def _write_json(self, path: Path, payload: object) -> None:
        path.write_bytes(canonical_json_bytes(payload))

    def _write_manifest(self, *, workspace: Path, payload: dict[str, object]) -> None:
        manifest_path = workspace / "manifest.json"
        current = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
        current.update(payload)
        current["workspace"] = workspace.as_posix()
        manifest_path.write_bytes(canonical_json_bytes(current))

    def _register_output(
        self,
        *,
        logical_table: str,
        source_schema: str,
        path: Path,
        request_hash: str,
        batch: dict[str, object],
        shadow_snapshot_id: str,
    ) -> None:
        df = read_parquet_dataset(path)
        row = FileRegistryRow(
            file_id=make_file_id(shadow_snapshot_id, logical_table, path),
            snapshot_id=shadow_snapshot_id,
            run_id=str(batch["run_id"]),
            logical_table=logical_table,
            source_schema=source_schema,
            path=path.as_posix(),
            content_sha256=file_sha256(path),
            row_count=int(len(df)),
            min_trade_date=batch["execution_date"],
            max_trade_date=batch["execution_date"],
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
        batch: dict[str, object],
        shadow_snapshot_id: str,
        started_at: datetime,
        finished_at: datetime,
        notes: list[str],
    ) -> None:
        row = RunRegistryRow(
            run_id=make_run_id(),
            run_type="broker_boundary",
            command_name=command_name,
            status=status,
            started_at_utc=started_at,
            finished_at_utc=finished_at,
            data_snapshot_id=shadow_snapshot_id,
            config_hash=config_hash([self.config_path, self.data_schema_path]),
            git_commit=_git_commit(self.repo_root),
            notes=notes,
            artifact_path=artifact_path.as_posix(),
        )
        append_run_registry_row(self.meta_root / "run_registry", row)


def _filter_targets(
    targets: pd.DataFrame,
    *,
    run_id: str | None,
    strategy_id: str | None,
    execution_mode: str | None,
    as_of_date: date | None,
    execution_date: date | None,
) -> pd.DataFrame:
    if targets.empty:
        raise ExecutionBoundaryError("targets_daily is empty")
    working = _normalize_target_batch(targets)
    if run_id is not None:
        working = working[working["run_id"].astype(str) == str(run_id)]
    if strategy_id is not None:
        working = working[working["strategy_id"].astype(str) == str(strategy_id)]
    if execution_mode is not None:
        working = working[working["execution_mode"].astype(str) == str(execution_mode)]
    if as_of_date is not None:
        working = working[working["as_of_date"] == as_of_date]
    if execution_date is not None:
        working = working[working["execution_date"] == execution_date]
    if working.empty:
        raise ExecutionBoundaryError("targets_daily became empty after applying filters")
    return working.reset_index(drop=True)


def _filter_monitoring(
    monitoring: pd.DataFrame,
    *,
    run_id: str,
    execution_mode: str,
    as_of_date: date,
    execution_date: date,
) -> pd.DataFrame:
    if monitoring.empty:
        return monitoring
    working = _normalize_monitoring(monitoring)
    return working[
        (working["run_id"].astype(str) == str(run_id))
        & (working["execution_mode"].astype(str) == str(execution_mode))
        & (working["as_of_date"] == as_of_date)
        & (working["execution_date"] == execution_date)
    ].reset_index(drop=True)


def _validate_monitoring_alignment(
    *,
    monitoring: pd.DataFrame,
    targets: pd.DataFrame,
    strict: bool,
    fail_on_missing: bool,
    created_at_utc: datetime,
) -> list[JournalEvent]:
    batch = _batch_identity(targets)
    filtered = _filter_monitoring(
        monitoring,
        run_id=batch["run_id"],
        execution_mode=batch["execution_mode"],
        as_of_date=batch["as_of_date"],
        execution_date=batch["execution_date"],
    )
    if filtered.empty:
        if strict and fail_on_missing:
            raise ExecutionBoundaryError(
                "monitoring_daily alignment is required in strict mode but no matching row was found"
            )
        return [
            _journal_event(
                run_id=batch["run_id"],
                execution_mode=batch["execution_mode"],
                as_of_date=batch["as_of_date"],
                execution_date=batch["execution_date"],
                root=None,
                component="execution_boundary",
                severity="warning",
                code="MONITORING_ALIGNMENT_MISSING",
                message="monitoring_daily was missing; continuing in non-strict mode.",
                created_at_utc=created_at_utc,
                details={},
            )
        ] if not strict else []
    monitoring_actions = set(filtered["final_action"].astype(str))
    target_actions = set(targets["control_action"].astype(str))
    if len(monitoring_actions) != 1 or monitoring_actions != target_actions:
        if strict and fail_on_missing:
            raise ExecutionBoundaryError(
                "monitoring_daily final_action does not align with selected target control_action"
            )
        return [
            _journal_event(
                run_id=batch["run_id"],
                execution_mode=batch["execution_mode"],
                as_of_date=batch["as_of_date"],
                execution_date=batch["execution_date"],
                root=None,
                component="execution_boundary",
                severity="warning",
                code="MONITORING_ALIGNMENT_MISMATCH",
                message="monitoring_daily final_action did not align with target control_action.",
                created_at_utc=created_at_utc,
                details={
                    "monitoring_actions": sorted(monitoring_actions),
                    "target_actions": sorted(target_actions),
                },
            )
        ]
    return []


def _batch_identity(df: pd.DataFrame) -> dict[str, object]:
    key_cols = ["run_id", "execution_mode", "as_of_date", "execution_date"]
    working = df.copy()
    if "as_of_date" in working.columns:
        working["as_of_date"] = pd.to_datetime(working["as_of_date"]).dt.date
    if "execution_date" in working.columns:
        working["execution_date"] = pd.to_datetime(working["execution_date"]).dt.date
    unique = working[key_cols].drop_duplicates()
    if len(unique) != 1:
        raise ExecutionBoundaryError("selected batch must resolve to one run/execution/date identity")
    row = unique.iloc[0]
    return {
        "run_id": str(row["run_id"]),
        "execution_mode": str(row["execution_mode"]),
        "as_of_date": row["as_of_date"],
        "execution_date": row["execution_date"],
    }


def _normalize_target_batch(df: pd.DataFrame) -> pd.DataFrame:
    working = df.copy()
    working["as_of_date"] = pd.to_datetime(working["as_of_date"]).dt.date
    working["execution_date"] = pd.to_datetime(working["execution_date"]).dt.date
    return working


def _normalize_positions(df: pd.DataFrame) -> pd.DataFrame:
    working = df.copy()
    if "snapshot_time_utc" in working.columns:
        working["snapshot_time_utc"] = pd.to_datetime(working["snapshot_time_utc"], utc=True)
    return working


def _normalize_intents(df: pd.DataFrame) -> pd.DataFrame:
    working = df.copy()
    working["as_of_date"] = pd.to_datetime(working["as_of_date"]).dt.date
    working["execution_date"] = pd.to_datetime(working["execution_date"]).dt.date
    working["sequence_no"] = pd.to_numeric(working["sequence_no"], errors="coerce").fillna(0).astype(int)
    working["quantity"] = pd.to_numeric(working["quantity"], errors="coerce").fillna(0).astype(int)
    return working.sort_values(["sequence_no", "order_intent_id"], kind="stable").reset_index(drop=True)


def _normalize_monitoring(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    working = df.copy()
    working["as_of_date"] = pd.to_datetime(working["as_of_date"]).dt.date
    working["execution_date"] = pd.to_datetime(working["execution_date"]).dt.date
    return working


def _resolve_output_dir(*, repo_root: Path, run_id: str, output_dir: Path | None) -> Path:
    if output_dir is not None:
        return ensure_directory(output_dir)
    return ensure_directory(repo_root / "data" / "shadow" / "execution_boundary" / f"run_id={run_id}")


def _records_frame(records: list[object], *, columns: list[str] | None = None) -> pd.DataFrame:
    if not records:
        return pd.DataFrame(columns=columns or [])
    if hasattr(records[0], "to_dict"):
        payload = [record.to_dict() for record in records]  # type: ignore[union-attr]
    else:
        payload = [asdict(record) for record in records]  # type: ignore[arg-type]
    frame = pd.DataFrame(payload)
    return frame.reindex(columns=columns) if columns is not None else frame


def _counts_by_column(df: pd.DataFrame, column: str) -> dict[str, int]:
    counts = df[column].astype(str).value_counts(dropna=False).sort_index()
    return {str(index): int(value) for index, value in counts.items()}


def _inventory_by_root(positions: pd.DataFrame) -> dict[str, dict[str, int]]:
    inventory: dict[str, dict[str, int]] = {}
    for _, row in positions.iterrows():
        root = str(row["root"])
        raw_symbol = str(row["raw_symbol"])
        inventory.setdefault(root, {})
        inventory[root][raw_symbol] = inventory[root].get(raw_symbol, 0) + int(row["position_contracts"])
    return {root: _drop_zero_positions(symbols) for root, symbols in inventory.items()}


def _drop_zero_positions(positions: dict[str, int]) -> dict[str, int]:
    return {symbol: qty for symbol, qty in sorted(positions.items()) if qty != 0}


def _qa_report_markdown(report: ExecutionBoundaryQaReport) -> str:
    lines = [
        f"# WP12 Dry-Run Report {report.run_id}",
        "",
        f"- Execution mode: `{report.execution_mode}`",
        f"- Position snapshot id: `{report.position_snapshot_id}`",
        f"- Accepted intents: {report.accepted_intents}",
        f"- Rejected intents: {report.rejected_intents}",
        f"- Broker calls made: {str(report.broker_calls_made).lower()}",
        "",
        "## Counts",
        "",
    ]
    for reason, count in report.counts_by_reason.items():
        lines.append(f"- Reason `{reason}`: {count}")
    lines.extend(["", "## Root Results", ""])
    for result in report.root_results:
        lines.append(
            f"- Root `{result['root']}`: passed={str(result['passed']).lower()}, "
            f"current_total={result['current_total']}, final_total={result['final_total']}, "
            f"accepted={result['accepted_intent_count']}, rejected={result['rejected_intent_count']}"
        )
    lines.extend(["", "## Issues", ""])
    if not report.issues:
        lines.append("- No QA issues.")
    else:
        for issue in report.issues:
            lines.append(
                f"- `{issue['severity']}` `{issue['code']}`: {issue['message']}"
            )
    return "\n".join(lines)


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
    details: dict[str, object] | None,
) -> JournalEvent:
    details_json = canonical_json_bytes(details).decode("utf-8") if details else None
    payload = {
        "run_id": run_id,
        "execution_mode": execution_mode,
        "as_of_date": as_of_date.isoformat() if as_of_date else None,
        "execution_date": execution_date.isoformat() if execution_date else None,
        "root": root,
        "component": component,
        "severity": severity,
        "code": code,
        "message": message,
        "details_json": details_json,
    }
    return JournalEvent(
        event_id=f"je_{stable_sha256_hex(payload)[:16]}",
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


def _shadow_snapshot_id(batch: dict[str, object]) -> str:
    return f"shadow_{stable_sha256_hex(batch)[:16]}"


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
