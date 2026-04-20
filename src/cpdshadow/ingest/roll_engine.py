from __future__ import annotations

from datetime import date
from pathlib import Path
import json
import subprocess

import pandas as pd

from cpdshadow.config import AppConfig, DataSchemaConfig
from cpdshadow.ids import (
    config_hash as compute_config_hash,
    file_sha256,
    make_file_id,
    make_run_id,
    manifest_hash as compute_manifest_hash,
    stable_sha256_hex,
    utc_now,
)
from cpdshadow.instruments import InstrumentMaster
from cpdshadow.rolls import (
    RollBuildError,
    RollQaIssue,
    RollQaReport,
    build_lead_map_for_root,
    validate_lead_map,
)
from cpdshadow.storage.parquet_io import (
    atomic_replace_dir,
    make_staging_dir,
    read_parquet_dataset,
    write_parquet_dataset,
)
from cpdshadow.storage.registry import (
    FileRegistryRow,
    RunRegistryRow,
    append_file_registry_row,
    append_run_registry_row,
)


class RollEngineService:
    def __init__(
        self,
        *,
        repo_root: str | Path,
        data_root: str | Path,
        app_config: AppConfig,
        data_schema: DataSchemaConfig,
        instrument_master: InstrumentMaster,
    ) -> None:
        self.repo_root = Path(repo_root)
        data_root_path = Path(data_root)
        self.data_root = data_root_path if data_root_path.is_absolute() else self.repo_root / data_root_path
        self.app_config = app_config
        self.data_schema = data_schema
        self.instrument_master = instrument_master
        self.meta_root = self.data_root / "meta"
        self.run_registry_root = self.meta_root / "run_registry"
        self.file_registry_root = self.meta_root / "data_file_registry"
        self.contract_master_root = self.data_root / "curated" / "contract_master"
        self.contracts_daily_root = self.data_root / "curated" / "contracts_daily"
        self.lead_map_root = self.data_root / "curated" / "lead_map"
        self.roll_events_root = self.data_root / "curated" / "roll_events"
        self.artifact_root = self.repo_root / "artifacts" / "reports" / "wp5_roll_engine"

    def build(
        self,
        *,
        snapshot_id: str,
        start_date: date,
        end_date: date,
        roots: list[str] | None,
        overwrite: bool,
    ) -> dict[str, object]:
        started_at = utc_now()
        run_id = make_run_id()
        artifact_path = self.artifact_root / f"build_{snapshot_id}.json"
        config_hash = self._config_hash()
        self.artifact_root.mkdir(parents=True, exist_ok=True)
        append_run_registry_row(self.run_registry_root, RunRegistryRow(
            run_id=run_id,
            run_type="roll_engine",
            command_name="build",
            status="started",
            started_at_utc=started_at,
            finished_at_utc=None,
            data_snapshot_id=snapshot_id,
            config_hash=config_hash,
            git_commit=_git_commit(self.repo_root),
            notes=[],
            artifact_path=self._relpath(artifact_path),
        ))

        try:
            contract_master_df = self._load_snapshot_table(self.contract_master_root, snapshot_id)
            contracts_daily_df = self._load_snapshot_table(
                self.contracts_daily_root,
                snapshot_id,
                drop_partition_cols=["trade_year"],
            )
            if contract_master_df.empty or contracts_daily_df.empty:
                raise ValueError(f"source snapshot {snapshot_id} is missing WP4 curated inputs")

            resolved_roots, skipped_roots = self._resolve_roots(roots, contract_master_df, contracts_daily_df)
            if not resolved_roots:
                raise ValueError("no roots are available for the requested snapshot and date range")

            roll_config = self.app_config.roll.model_dump(mode="python")
            roll_config["start_date"] = start_date
            roll_config["end_date"] = end_date
            build_params_hash = stable_sha256_hex({
                "snapshot_id": snapshot_id,
                "start_date": start_date,
                "end_date": end_date,
                "roots": resolved_roots,
                "roll_config": roll_config,
                "config_hash": config_hash,
            })

            lead_output_root = self.lead_map_root / f"snapshot_id={snapshot_id}"
            roll_output_root = self.roll_events_root / f"snapshot_id={snapshot_id}"
            if not overwrite:
                for output_root in (lead_output_root, roll_output_root):
                    if output_root.exists() and list(output_root.rglob("*.parquet")):
                        raise ValueError(
                            f"output already exists for snapshot {snapshot_id}; rerun with --overwrite"
                        )

            instrument_lookup = {
                instrument.root: instrument.model_dump(mode="python")
                for instrument in self.instrument_master.instruments
            }
            build_issues: list[RollQaIssue] = []
            lead_frames: list[pd.DataFrame] = []
            roll_frames: list[pd.DataFrame] = []
            for root in resolved_roots:
                try:
                    lead_map, roll_events = build_lead_map_for_root(
                        root=root,
                        contract_master=contract_master_df,
                        contracts_daily=contracts_daily_df,
                        instrument_config=instrument_lookup[root],
                        roll_config=roll_config,
                        snapshot_id=snapshot_id,
                    )
                except RollBuildError as exc:
                    build_issues.append(RollQaIssue(
                        code=exc.code,
                        severity="error",
                        message=str(exc),
                        details={"root": root, **exc.details},
                    ))
                    continue
                lead_frames.append(lead_map)
                roll_frames.append(roll_events)

            if skipped_roots:
                build_issues.append(RollQaIssue(
                    code="missing_source_root",
                    severity="warning",
                    message="one or more configured roots are missing from the source snapshot.",
                    details={"roots": skipped_roots},
                ))

            lead_map_df = (
                pd.concat(lead_frames, ignore_index=True)
                if lead_frames
                else pd.DataFrame(columns=self.data_schema.tables["lead_map"].column_names)
            )
            roll_events_df = (
                pd.concat(roll_frames, ignore_index=True)
                if roll_frames
                else pd.DataFrame(columns=self.data_schema.tables["roll_events"].column_names)
            )
            report = validate_lead_map(
                lead_map=lead_map_df,
                roll_events=roll_events_df,
                contract_master=contract_master_df,
                contracts_daily=contracts_daily_df,
            ).with_additional_issues(build_issues)
            self._write_qa_artifacts(snapshot_id=snapshot_id, report=report)

            if report.has_errors:
                artifact = {
                    "snapshot_id": snapshot_id,
                    "start_date": start_date.isoformat(),
                    "end_date": end_date.isoformat(),
                    "roots": resolved_roots,
                    "build_params_hash": build_params_hash,
                    "qa_report": report.to_dict(),
                }
                artifact_path.write_bytes(json.dumps(artifact, indent=2, default=str).encode("utf-8"))
                raise ValueError("roll engine QA failed")

            lead_map_path = self._write_output_table(
                df=lead_map_df,
                final_root=lead_output_root,
                date_column="as_of_date",
                partition_column="year",
                unique_key=["as_of_date", "root"],
            )
            roll_events_path = self._write_output_table(
                df=roll_events_df,
                final_root=roll_output_root,
                date_column="effective_date",
                partition_column="year",
                unique_key=["roll_event_id"],
            )

            for logical_table, path, df, date_column in [
                ("lead_map", lead_map_path, lead_map_df, "as_of_date"),
                ("roll_events", roll_events_path, roll_events_df, "effective_date"),
            ]:
                append_file_registry_row(self.file_registry_root, FileRegistryRow(
                    file_id=make_file_id(snapshot_id, logical_table, path),
                    snapshot_id=snapshot_id,
                    run_id=run_id,
                    logical_table=logical_table,
                    source_schema=self.app_config.roll.builder_version,
                    path=self._relpath(path),
                    content_sha256=_directory_manifest_hash(path),
                    row_count=int(len(df)),
                    min_trade_date=_min_date(df, date_column),
                    max_trade_date=_max_date(df, date_column),
                    request_params_hash=build_params_hash,
                    cached=False,
                    created_at_utc=utc_now(),
                ))

            artifact = {
                "snapshot_id": snapshot_id,
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat(),
                "roots": resolved_roots,
                "skipped_roots": skipped_roots,
                "build_params_hash": build_params_hash,
                "lead_map_rows": int(len(lead_map_df)),
                "roll_event_rows": int(len(roll_events_df)),
                "lead_map_path": self._relpath(lead_map_path),
                "roll_events_path": self._relpath(roll_events_path),
                "qa_report": report.to_dict(),
            }
            artifact_path.write_bytes(json.dumps(artifact, indent=2, default=str).encode("utf-8"))
            finished_at = utc_now()
            append_run_registry_row(self.run_registry_root, RunRegistryRow(
                run_id=run_id,
                run_type="roll_engine",
                command_name="build",
                status="succeeded",
                started_at_utc=started_at,
                finished_at_utc=finished_at,
                data_snapshot_id=snapshot_id,
                config_hash=config_hash,
                git_commit=_git_commit(self.repo_root),
                notes=[],
                artifact_path=self._relpath(artifact_path),
            ))
            return artifact
        except Exception as exc:
            finished_at = utc_now()
            append_run_registry_row(self.run_registry_root, RunRegistryRow(
                run_id=run_id,
                run_type="roll_engine",
                command_name="build",
                status="failed",
                started_at_utc=started_at,
                finished_at_utc=finished_at,
                data_snapshot_id=snapshot_id,
                config_hash=config_hash,
                git_commit=_git_commit(self.repo_root),
                notes=[f"error={type(exc).__name__}"],
                artifact_path=self._relpath(artifact_path),
            ))
            raise

    def qa(self, *, snapshot_id: str) -> RollQaReport:
        started_at = utc_now()
        run_id = make_run_id()
        artifact_path = self.artifact_root / f"qa_{snapshot_id}.json"
        config_hash = self._config_hash()
        self.artifact_root.mkdir(parents=True, exist_ok=True)
        append_run_registry_row(self.run_registry_root, RunRegistryRow(
            run_id=run_id,
            run_type="roll_engine",
            command_name="qa",
            status="started",
            started_at_utc=started_at,
            finished_at_utc=None,
            data_snapshot_id=snapshot_id,
            config_hash=config_hash,
            git_commit=_git_commit(self.repo_root),
            notes=[],
            artifact_path=self._relpath(artifact_path),
        ))

        try:
            contract_master_df = self._load_snapshot_table(self.contract_master_root, snapshot_id)
            contracts_daily_df = self._load_snapshot_table(
                self.contracts_daily_root,
                snapshot_id,
                drop_partition_cols=["trade_year"],
            )
            lead_map_df = self._load_snapshot_table(self.lead_map_root, snapshot_id, drop_partition_cols=["year"])
            roll_events_df = self._load_snapshot_table(
                self.roll_events_root,
                snapshot_id,
                drop_partition_cols=["year"],
            )
            if lead_map_df.empty and roll_events_df.empty:
                raise ValueError(f"no WP5 outputs found for snapshot {snapshot_id}")
            report = validate_lead_map(
                lead_map=lead_map_df,
                roll_events=roll_events_df,
                contract_master=contract_master_df,
                contracts_daily=contracts_daily_df,
            )
            self._write_qa_artifacts(snapshot_id=snapshot_id, report=report)
            artifact_path.write_bytes(json.dumps(report.to_dict(), indent=2, default=str).encode("utf-8"))
            finished_at = utc_now()
            append_run_registry_row(self.run_registry_root, RunRegistryRow(
                run_id=run_id,
                run_type="roll_engine",
                command_name="qa",
                status="succeeded",
                started_at_utc=started_at,
                finished_at_utc=finished_at,
                data_snapshot_id=snapshot_id,
                config_hash=config_hash,
                git_commit=_git_commit(self.repo_root),
                notes=[],
                artifact_path=self._relpath(artifact_path),
            ))
            return report
        except Exception as exc:
            finished_at = utc_now()
            append_run_registry_row(self.run_registry_root, RunRegistryRow(
                run_id=run_id,
                run_type="roll_engine",
                command_name="qa",
                status="failed",
                started_at_utc=started_at,
                finished_at_utc=finished_at,
                data_snapshot_id=snapshot_id,
                config_hash=config_hash,
                git_commit=_git_commit(self.repo_root),
                notes=[f"error={type(exc).__name__}"],
                artifact_path=self._relpath(artifact_path),
            ))
            raise

    def _resolve_roots(
        self,
        roots: list[str] | None,
        contract_master_df: pd.DataFrame,
        contracts_daily_df: pd.DataFrame,
    ) -> tuple[list[str], list[str]]:
        configured = {instrument.root for instrument in self.instrument_master.instruments}
        requested = sorted(roots) if roots else sorted(configured)
        unknown = sorted(set(requested) - configured)
        if unknown:
            raise ValueError(f"unknown roots requested: {unknown}")
        source_roots = set(contract_master_df["root"].dropna().astype(str)) & set(
            contracts_daily_df["root"].dropna().astype(str)
        )
        resolved = [root for root in requested if root in source_roots]
        skipped = [root for root in requested if root not in source_roots]
        return resolved, skipped

    def _load_snapshot_table(
        self,
        root: Path,
        snapshot_id: str,
        *,
        drop_partition_cols: list[str] | None = None,
    ) -> pd.DataFrame:
        df = read_parquet_dataset(root / f"snapshot_id={snapshot_id}")
        for column in drop_partition_cols or []:
            if column in df.columns:
                df = df.drop(columns=[column])
        return df.reset_index(drop=True)

    def _write_output_table(
        self,
        *,
        df: pd.DataFrame,
        final_root: Path,
        date_column: str,
        partition_column: str,
        unique_key: list[str],
    ) -> Path:
        output_df = df.copy()
        if not output_df.empty and output_df.duplicated(subset=unique_key).any():
            raise ValueError(f"duplicate rows detected for {final_root.name}")
        staging_dir = make_staging_dir(final_root)
        if not output_df.empty:
            output_df[partition_column] = pd.to_datetime(output_df[date_column]).dt.year
            write_parquet_dataset(
                output_df,
                staging_dir,
                compression="zstd",
                partition_cols=[partition_column],
            )
        else:
            write_parquet_dataset(output_df, staging_dir, compression="zstd", partition_cols=None)
        return atomic_replace_dir(staging_dir, final_root)

    def _write_qa_artifacts(self, *, snapshot_id: str, report: RollQaReport) -> None:
        self.artifact_root.mkdir(parents=True, exist_ok=True)
        json_path = self.artifact_root / f"roll_qa_{snapshot_id}.json"
        md_path = self.artifact_root / f"roll_qa_{snapshot_id}.md"
        json_path.write_bytes(json.dumps(report.to_dict(), indent=2, default=str).encode("utf-8"))
        md_path.write_text(report.to_markdown(), encoding="utf-8")

    def _config_hash(self) -> str:
        return compute_config_hash([
            self.repo_root / "config" / "settings.base.yml",
            self.repo_root / "config" / "instruments.yml",
            self.repo_root / "config" / "data_schema.yml",
        ])

    def _relpath(self, path: str | Path) -> str:
        target = Path(path)
        if not target.is_absolute():
            return target.as_posix()
        return target.relative_to(self.repo_root).as_posix()


def _git_commit(repo_root: Path) -> str:
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_root,
            check=True,
            capture_output=True,
            text=True,
        )
    except Exception:
        return "unknown-local"
    commit = completed.stdout.strip()
    return commit or "unknown-local"


def _directory_manifest_hash(path: Path) -> str:
    if path.is_file():
        return file_sha256(path)
    payload = []
    for item in sorted(path.rglob("*.parquet")):
        payload.append({"path": item.relative_to(path).as_posix(), "sha256": file_sha256(item)})
    return compute_manifest_hash(payload)


def _min_date(df: pd.DataFrame, column: str) -> date | None:
    if df.empty:
        return None
    return pd.to_datetime(df[column]).dt.date.min()


def _max_date(df: pd.DataFrame, column: str) -> date | None:
    if df.empty:
        return None
    return pd.to_datetime(df[column]).dt.date.max()
