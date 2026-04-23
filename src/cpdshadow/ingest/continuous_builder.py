from __future__ import annotations

from datetime import date
from pathlib import Path
import json
import subprocess

import pandas as pd

from cpdshadow.config import AppConfig, DataSchemaConfig
from cpdshadow.continuous import (
    CONTINUOUS_DAILY_COLUMNS,
    ContinuousBuildError,
    ContinuousQaIssue,
    ContinuousQaReport,
    ContinuousSeriesConfig,
    build_continuous_for_root,
    validate_continuous_daily,
)
from cpdshadow.ids import (
    config_hash as compute_config_hash,
    file_sha256,
    make_file_id,
    make_run_id,
    manifest_hash as compute_manifest_hash,
    stable_sha256_hex,
    utc_now,
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


class ContinuousBuilderService:
    def __init__(
        self,
        *,
        repo_root: str | Path,
        data_root: str | Path,
        app_config: AppConfig,
        data_schema: DataSchemaConfig,
        curated_output_root: str | Path | None = None,
        artifact_root: str | Path | None = None,
    ) -> None:
        self.repo_root = Path(repo_root)
        data_root_path = Path(data_root)
        self.data_root = data_root_path if data_root_path.is_absolute() else self.repo_root / data_root_path
        self.app_config = app_config
        self.data_schema = data_schema
        self.meta_root = self.data_root / "meta"
        self.run_registry_root = self.meta_root / "run_registry"
        self.file_registry_root = self.meta_root / "data_file_registry"
        self.contracts_daily_root = self.data_root / "curated" / "contracts_daily"
        self.lead_map_root = self.data_root / "curated" / "lead_map"
        self.roll_events_root = self.data_root / "curated" / "roll_events"
        if curated_output_root is None:
            self.continuous_daily_root = self.data_root / "curated" / "continuous_daily"
        else:
            output_root = Path(curated_output_root)
            self.continuous_daily_root = output_root if output_root.is_absolute() else self.repo_root / output_root
        if artifact_root is None:
            self.artifact_root = self.repo_root / "artifacts" / "wp6"
        else:
            artifacts = Path(artifact_root)
            self.artifact_root = artifacts if artifacts.is_absolute() else self.repo_root / artifacts

    def build(
        self,
        *,
        snapshot_id: str,
        start_date: date,
        end_date: date,
        roots: list[str] | None,
        series_id: str | None,
        overwrite: bool,
    ) -> dict[str, object]:
        started_at = utc_now()
        run_id = make_run_id()
        config_hash = self._config_hash()
        config = self._continuous_config(series_id=series_id)
        artifact_path = self.artifact_root / f"build_{snapshot_id}_{config.series_id}.json"
        self.artifact_root.mkdir(parents=True, exist_ok=True)
        append_run_registry_row(self.run_registry_root, RunRegistryRow(
            run_id=run_id,
            run_type="continuous_builder",
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
            contracts_daily_df = self._load_snapshot_table(
                self.contracts_daily_root,
                snapshot_id,
                drop_partition_cols=["trade_year"],
            )
            lead_map_df = self._load_snapshot_table(
                self.lead_map_root,
                snapshot_id,
                drop_partition_cols=["year"],
            )
            roll_events_df = self._load_snapshot_table(
                self.roll_events_root,
                snapshot_id,
                drop_partition_cols=["year"],
            )
            if contracts_daily_df.empty or lead_map_df.empty:
                raise ValueError(f"source snapshot {snapshot_id} is missing WP4/WP5 curated inputs")

            selected_lead_map = self._select_lead_map(
                lead_map_df=lead_map_df,
                roots=roots,
                start_date=start_date,
                end_date=end_date,
            )
            resolved_roots = sorted(selected_lead_map["root"].dropna().astype(str).unique())
            if not resolved_roots:
                raise ValueError("no lead_map rows are available for the requested snapshot/date range")

            build_params_hash = stable_sha256_hex({
                "snapshot_id": snapshot_id,
                "series_id": config.series_id,
                "start_date": start_date,
                "end_date": end_date,
                "roots": resolved_roots,
                "continuous_config": {
                    "series_id": config.series_id,
                    "builder_version": config.builder_version,
                    "strict_roll_ratio": config.strict_roll_ratio,
                    "allow_close_fallback": config.allow_close_fallback,
                    "allowed_settle_statuses": list(config.allowed_settle_statuses),
                    "blocked_settle_statuses": list(config.blocked_settle_statuses),
                    "max_abs_daily_return_warning": config.max_abs_daily_return_warning,
                    "max_abs_daily_return_error": config.max_abs_daily_return_error,
                },
                "config_hash": config_hash,
            })

            output_root = (
                self.continuous_daily_root
                / f"series_id={config.series_id}"
                / f"snapshot_id={snapshot_id}"
            )
            if not overwrite and output_root.exists() and list(output_root.rglob("*.parquet")):
                raise ValueError(
                    f"output already exists for snapshot {snapshot_id} and series {config.series_id}; "
                    "rerun with --overwrite"
                )

            build_issues: list[ContinuousQaIssue] = []
            frames: list[pd.DataFrame] = []
            for root in resolved_roots:
                try:
                    frame = build_continuous_for_root(
                        root=root,
                        contracts_daily=contracts_daily_df,
                        lead_map=selected_lead_map,
                        roll_events=roll_events_df,
                        config=config,
                        snapshot_id=snapshot_id,
                    )
                except ContinuousBuildError as exc:
                    build_issues.append(ContinuousQaIssue(
                        code=exc.code,
                        severity="error",
                        message=str(exc),
                        details={"root": root, **exc.details},
                    ))
                    continue
                frames.append(frame)

            continuous_daily_df = (
                pd.concat(frames, ignore_index=True)
                if frames
                else pd.DataFrame(columns=self.data_schema.tables["continuous_daily"].column_names)
            )
            report = validate_continuous_daily(
                continuous_daily=continuous_daily_df,
                lead_map=selected_lead_map,
                roll_events=roll_events_df,
                contracts_daily=contracts_daily_df,
                config=config,
            ).with_additional_issues(build_issues)
            self._write_qa_artifacts(snapshot_id=snapshot_id, series_id=config.series_id, report=report)

            artifact = {
                "snapshot_id": snapshot_id,
                "series_id": config.series_id,
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat(),
                "roots": resolved_roots,
                "build_params_hash": build_params_hash,
                "row_count": int(len(continuous_daily_df)),
                "qa_report": report.to_dict(),
            }
            artifact_path.write_bytes(json.dumps(artifact, indent=2, default=str).encode("utf-8"))
            if report.has_errors:
                raise ValueError("continuous builder QA failed")

            output_path = self._write_output_table(
                df=continuous_daily_df,
                final_root=output_root,
                date_column="as_of_date",
                partition_column="year",
                unique_key=["series_id", "as_of_date", "root"],
            )
            append_file_registry_row(self.file_registry_root, FileRegistryRow(
                file_id=make_file_id(snapshot_id, "continuous_daily", output_path),
                snapshot_id=snapshot_id,
                run_id=run_id,
                logical_table="continuous_daily",
                source_schema=config.builder_version,
                path=self._relpath(output_path),
                content_sha256=_directory_manifest_hash(output_path),
                row_count=int(len(continuous_daily_df)),
                min_trade_date=_min_date(continuous_daily_df, "as_of_date"),
                max_trade_date=_max_date(continuous_daily_df, "as_of_date"),
                request_params_hash=build_params_hash,
                cached=False,
                created_at_utc=utc_now(),
            ))

            artifact["output_path"] = self._relpath(output_path)
            artifact_path.write_bytes(json.dumps(artifact, indent=2, default=str).encode("utf-8"))
            finished_at = utc_now()
            append_run_registry_row(self.run_registry_root, RunRegistryRow(
                run_id=run_id,
                run_type="continuous_builder",
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
                run_type="continuous_builder",
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

    def qa(
        self,
        *,
        snapshot_id: str,
        series_id: str | None,
    ) -> ContinuousQaReport:
        started_at = utc_now()
        run_id = make_run_id()
        config_hash = self._config_hash()
        config = self._continuous_config(series_id=series_id)
        artifact_path = self.artifact_root / f"qa_{snapshot_id}_{config.series_id}.json"
        self.artifact_root.mkdir(parents=True, exist_ok=True)
        append_run_registry_row(self.run_registry_root, RunRegistryRow(
            run_id=run_id,
            run_type="continuous_builder",
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
            contracts_daily_df = self._load_snapshot_table(
                self.contracts_daily_root,
                snapshot_id,
                drop_partition_cols=["trade_year"],
            )
            lead_map_df = self._load_snapshot_table(
                self.lead_map_root,
                snapshot_id,
                drop_partition_cols=["year"],
            )
            roll_events_df = self._load_snapshot_table(
                self.roll_events_root,
                snapshot_id,
                drop_partition_cols=["year"],
            )
            continuous_root = (
                self.continuous_daily_root
                / f"series_id={config.series_id}"
                / f"snapshot_id={snapshot_id}"
            )
            continuous_daily_df = read_parquet_dataset(continuous_root)
            if "year" in continuous_daily_df.columns:
                continuous_daily_df = continuous_daily_df.drop(columns=["year"])
            if continuous_daily_df.empty:
                raise ValueError(f"no WP6 outputs found for snapshot {snapshot_id} and series {config.series_id}")

            report = validate_continuous_daily(
                continuous_daily=continuous_daily_df,
                lead_map=lead_map_df,
                roll_events=roll_events_df,
                contracts_daily=contracts_daily_df,
                config=config,
            )
            self._write_qa_artifacts(snapshot_id=snapshot_id, series_id=config.series_id, report=report)
            artifact_path.write_bytes(json.dumps(report.to_dict(), indent=2, default=str).encode("utf-8"))
            finished_at = utc_now()
            append_run_registry_row(self.run_registry_root, RunRegistryRow(
                run_id=run_id,
                run_type="continuous_builder",
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
                run_type="continuous_builder",
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

    def _continuous_config(self, *, series_id: str | None) -> ContinuousSeriesConfig:
        payload = self.app_config.continuous.model_dump(mode="python")
        if series_id:
            payload["series_id"] = series_id
        payload["allowed_settle_statuses"] = tuple(payload["allowed_settle_statuses"])
        payload["blocked_settle_statuses"] = tuple(payload["blocked_settle_statuses"])
        return ContinuousSeriesConfig(**payload)

    def _select_lead_map(
        self,
        *,
        lead_map_df: pd.DataFrame,
        roots: list[str] | None,
        start_date: date,
        end_date: date,
    ) -> pd.DataFrame:
        selected = lead_map_df.copy()
        selected["as_of_date"] = pd.to_datetime(selected["as_of_date"]).dt.date
        selected = selected[
            (selected["as_of_date"] >= start_date)
            & (selected["as_of_date"] <= end_date)
        ]
        if roots is not None:
            root_set = {str(root) for root in roots}
            selected = selected[selected["root"].astype(str).isin(root_set)]
        return selected.reset_index(drop=True)

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
        if output_df.empty:
            output_df = pd.DataFrame(columns=CONTINUOUS_DAILY_COLUMNS)
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

    def _write_qa_artifacts(
        self,
        *,
        snapshot_id: str,
        series_id: str,
        report: ContinuousQaReport,
    ) -> None:
        self.artifact_root.mkdir(parents=True, exist_ok=True)
        json_path = self.artifact_root / f"continuous_qa_{snapshot_id}_{series_id}.json"
        md_path = self.artifact_root / f"continuous_qa_{snapshot_id}_{series_id}.md"
        json_path.write_bytes(json.dumps(report.to_dict(), indent=2, default=str).encode("utf-8"))
        md_path.write_text(report.to_markdown(), encoding="utf-8")

    def _config_hash(self) -> str:
        return compute_config_hash([
            self.repo_root / "config" / "settings.base.yml",
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
