from __future__ import annotations

from datetime import date
from pathlib import Path
import json
import subprocess

import pandas as pd

from cpdshadow.config import AppConfig, DataSchemaConfig, FeaturesConfig
from cpdshadow.features import (
    FeatureQaReport,
    build_feature_outputs,
    validate_feature_outputs,
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


class FeaturesBuilderService:
    def __init__(
        self,
        *,
        repo_root: str | Path,
        app_config: AppConfig,
        data_schema: DataSchemaConfig,
        continuous_input_root: str | Path | None = None,
        features_output_root: str | Path | None = None,
        artifact_root: str | Path | None = None,
    ) -> None:
        self.repo_root = Path(repo_root)
        self.app_config = app_config
        self.data_schema = data_schema
        self.data_root = self.repo_root / "data"
        self.meta_root = self.data_root / "meta"
        self.run_registry_root = self.meta_root / "run_registry"
        self.file_registry_root = self.meta_root / "data_file_registry"
        if continuous_input_root is None:
            self.continuous_input_root = self.data_root / "curated" / "continuous_daily"
        else:
            self.continuous_input_root = self._resolve_path(continuous_input_root)
        if features_output_root is None:
            self.features_output_root = self.data_root / "features"
        else:
            self.features_output_root = self._resolve_path(features_output_root)
        if artifact_root is None:
            self.artifact_root = self.repo_root / "artifacts" / "wp7"
        else:
            self.artifact_root = self._resolve_path(artifact_root)

    def build(
        self,
        *,
        snapshot_id: str,
        start_date: date,
        end_date: date,
        roots: list[str] | None,
        series_id: str | None,
        feature_set_id: str | None,
        overwrite: bool,
    ) -> dict[str, object]:
        started_at = utc_now()
        run_id = make_run_id()
        config_hash = self._config_hash()
        config = self._features_config(series_id=series_id, feature_set_id=feature_set_id)
        artifact_path = self.artifact_root / f"build_{snapshot_id}_{config.feature_set_id}.json"
        self.artifact_root.mkdir(parents=True, exist_ok=True)
        append_run_registry_row(self.run_registry_root, RunRegistryRow(
            run_id=run_id,
            run_type="features_builder",
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
            continuous_daily_df = self._load_source_snapshot(
                snapshot_id=snapshot_id,
                series_id=config.series_id,
            )
            selected_source = self._select_source_rows(
                continuous_daily_df=continuous_daily_df,
                roots=roots,
                start_date=start_date,
                end_date=end_date,
                warmup_days=config.warmup_days,
            )
            if selected_source.empty:
                raise ValueError(
                    f"no continuous_daily rows found for snapshot {snapshot_id}, "
                    f"series {config.series_id}, and the requested date range"
                )

            resolved_roots = sorted(selected_source["root"].dropna().astype(str).unique())
            build_params_hash = stable_sha256_hex({
                "snapshot_id": snapshot_id,
                "feature_set_id": config.feature_set_id,
                "series_id": config.series_id,
                "start_date": start_date,
                "end_date": end_date,
                "roots": resolved_roots,
                "features_config": config.model_dump(mode="python"),
                "config_hash": config_hash,
            })

            cpd_output_root = (
                self.features_output_root
                / "cpd_daily"
                / f"feature_set_id={config.feature_set_id}"
                / f"snapshot_id={snapshot_id}"
            )
            features_output_root = (
                self.features_output_root
                / "features_daily"
                / f"feature_set_id={config.feature_set_id}"
                / f"snapshot_id={snapshot_id}"
            )
            if not overwrite:
                for output_root in (cpd_output_root, features_output_root):
                    if output_root.exists() and list(output_root.rglob("*.parquet")):
                        raise ValueError(
                            f"output already exists for snapshot {snapshot_id} and feature set "
                            f"{config.feature_set_id}; rerun with --overwrite"
                        )

            cpd_daily_df, features_daily_df = build_feature_outputs(
                continuous_daily=selected_source,
                config=config,
                feature_set_id=config.feature_set_id,
                snapshot_id=snapshot_id,
                roots=resolved_roots,
                start_date=start_date,
                end_date=end_date,
            )
            report = validate_feature_outputs(
                features_daily=features_daily_df,
                cpd_daily=cpd_daily_df,
                continuous_daily=selected_source,
                config=config,
            )
            self._write_qa_artifacts(
                snapshot_id=snapshot_id,
                feature_set_id=config.feature_set_id,
                report=report,
            )

            artifact = {
                "snapshot_id": snapshot_id,
                "feature_set_id": config.feature_set_id,
                "series_id": config.series_id,
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat(),
                "roots": resolved_roots,
                "build_params_hash": build_params_hash,
                "cpd_rows": int(len(cpd_daily_df)),
                "feature_rows": int(len(features_daily_df)),
                "qa_report": report.to_dict(),
            }
            artifact_path.write_bytes(json.dumps(artifact, indent=2, default=str).encode("utf-8"))
            if report.has_errors:
                raise ValueError("WP7 features QA failed")

            cpd_output_path = self._write_output_table(
                df=cpd_daily_df,
                final_root=cpd_output_root,
                table_name="cpd_daily",
                unique_key=["feature_set_id", "as_of_date", "root", "cpd_window_days"],
            )
            features_output_path = self._write_output_table(
                df=features_daily_df,
                final_root=features_output_root,
                table_name="features_daily",
                unique_key=["feature_set_id", "as_of_date", "root"],
            )
            append_file_registry_row(self.file_registry_root, FileRegistryRow(
                file_id=make_file_id(snapshot_id, "cpd_daily", cpd_output_path),
                snapshot_id=snapshot_id,
                run_id=run_id,
                logical_table="cpd_daily",
                source_schema=config.builder_version,
                path=self._relpath(cpd_output_path),
                content_sha256=_directory_manifest_hash(cpd_output_path),
                row_count=int(len(cpd_daily_df)),
                min_trade_date=_min_date(cpd_daily_df, "as_of_date"),
                max_trade_date=_max_date(cpd_daily_df, "as_of_date"),
                request_params_hash=build_params_hash,
                cached=False,
                created_at_utc=utc_now(),
            ))
            append_file_registry_row(self.file_registry_root, FileRegistryRow(
                file_id=make_file_id(snapshot_id, "features_daily", features_output_path),
                snapshot_id=snapshot_id,
                run_id=run_id,
                logical_table="features_daily",
                source_schema=config.builder_version,
                path=self._relpath(features_output_path),
                content_sha256=_directory_manifest_hash(features_output_path),
                row_count=int(len(features_daily_df)),
                min_trade_date=_min_date(features_daily_df, "as_of_date"),
                max_trade_date=_max_date(features_daily_df, "as_of_date"),
                request_params_hash=build_params_hash,
                cached=False,
                created_at_utc=utc_now(),
            ))

            artifact["output_paths"] = {
                "cpd_daily": self._relpath(cpd_output_path),
                "features_daily": self._relpath(features_output_path),
            }
            artifact_path.write_bytes(json.dumps(artifact, indent=2, default=str).encode("utf-8"))
            finished_at = utc_now()
            append_run_registry_row(self.run_registry_root, RunRegistryRow(
                run_id=run_id,
                run_type="features_builder",
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
                run_type="features_builder",
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
        feature_set_id: str | None,
        series_id: str | None,
    ) -> FeatureQaReport:
        started_at = utc_now()
        run_id = make_run_id()
        config_hash = self._config_hash()
        config = self._features_config(series_id=series_id, feature_set_id=feature_set_id)
        artifact_path = self.artifact_root / f"qa_{snapshot_id}_{config.feature_set_id}.json"
        self.artifact_root.mkdir(parents=True, exist_ok=True)
        append_run_registry_row(self.run_registry_root, RunRegistryRow(
            run_id=run_id,
            run_type="features_builder",
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
            continuous_daily_df = self._load_source_snapshot(
                snapshot_id=snapshot_id,
                series_id=config.series_id,
            )
            cpd_output_root = (
                self.features_output_root
                / "cpd_daily"
                / f"feature_set_id={config.feature_set_id}"
                / f"snapshot_id={snapshot_id}"
            )
            features_output_root = (
                self.features_output_root
                / "features_daily"
                / f"feature_set_id={config.feature_set_id}"
                / f"snapshot_id={snapshot_id}"
            )
            cpd_daily_df = read_parquet_dataset(cpd_output_root)
            features_daily_df = read_parquet_dataset(features_output_root)
            for frame in (cpd_daily_df, features_daily_df):
                if "year" in frame.columns:
                    frame.drop(columns=["year"], inplace=True)
            if cpd_daily_df.empty or features_daily_df.empty:
                raise ValueError(
                    f"no WP7 outputs found for snapshot {snapshot_id} and feature set {config.feature_set_id}"
                )

            report = validate_feature_outputs(
                features_daily=features_daily_df,
                cpd_daily=cpd_daily_df,
                continuous_daily=continuous_daily_df,
                config=config,
            )
            self._write_qa_artifacts(
                snapshot_id=snapshot_id,
                feature_set_id=config.feature_set_id,
                report=report,
            )
            artifact_path.write_bytes(json.dumps(report.to_dict(), indent=2, default=str).encode("utf-8"))
            finished_at = utc_now()
            append_run_registry_row(self.run_registry_root, RunRegistryRow(
                run_id=run_id,
                run_type="features_builder",
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
                run_type="features_builder",
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

    def _features_config(
        self,
        *,
        series_id: str | None,
        feature_set_id: str | None,
    ) -> FeaturesConfig:
        payload = self.app_config.features.model_dump(mode="python")
        if series_id:
            payload["series_id"] = series_id
        if feature_set_id:
            payload["feature_set_id"] = feature_set_id
        return FeaturesConfig.model_validate(payload)

    def _load_source_snapshot(self, *, snapshot_id: str, series_id: str) -> pd.DataFrame:
        root = self.continuous_input_root / f"series_id={series_id}" / f"snapshot_id={snapshot_id}"
        df = read_parquet_dataset(root)
        if "year" in df.columns:
            df = df.drop(columns=["year"])
        return df.reset_index(drop=True)

    def _select_source_rows(
        self,
        *,
        continuous_daily_df: pd.DataFrame,
        roots: list[str] | None,
        start_date: date,
        end_date: date,
        warmup_days: int,
    ) -> pd.DataFrame:
        if continuous_daily_df.empty:
            return continuous_daily_df
        selected = continuous_daily_df.copy()
        selected["as_of_date"] = pd.to_datetime(selected["as_of_date"]).dt.date
        selected = selected[selected["as_of_date"] <= end_date]
        if roots is not None:
            root_set = {str(root) for root in roots}
            selected = selected[selected["root"].astype(str).isin(root_set)]
        if selected.empty:
            return selected

        frames: list[pd.DataFrame] = []
        for _, group in selected.sort_values(["root", "as_of_date"], kind="stable").groupby("root", sort=True):
            pre_start = group[group["as_of_date"] < start_date].tail(warmup_days)
            in_range = group[(group["as_of_date"] >= start_date) & (group["as_of_date"] <= end_date)]
            if in_range.empty:
                continue
            frames.append(pd.concat([pre_start, in_range], ignore_index=True))
        if not frames:
            return pd.DataFrame(columns=selected.columns)
        return pd.concat(frames, ignore_index=True)

    def _write_output_table(
        self,
        *,
        df: pd.DataFrame,
        final_root: Path,
        table_name: str,
        unique_key: list[str],
    ) -> Path:
        output_df = df.copy()
        if output_df.empty:
            output_df = pd.DataFrame(columns=self.data_schema.tables[table_name].column_names)
        if not output_df.empty and output_df.duplicated(subset=unique_key).any():
            raise ValueError(f"duplicate rows detected for {table_name}")
        staging_dir = make_staging_dir(final_root)
        if not output_df.empty:
            output_df["year"] = pd.to_datetime(output_df["as_of_date"]).dt.year
            write_parquet_dataset(
                output_df,
                staging_dir,
                compression="zstd",
                partition_cols=["year"],
            )
        else:
            write_parquet_dataset(output_df, staging_dir, compression="zstd", partition_cols=None)
        return atomic_replace_dir(staging_dir, final_root)

    def _write_qa_artifacts(
        self,
        *,
        snapshot_id: str,
        feature_set_id: str,
        report: FeatureQaReport,
    ) -> None:
        self.artifact_root.mkdir(parents=True, exist_ok=True)
        json_path = self.artifact_root / f"features_qa_{snapshot_id}_{feature_set_id}.json"
        md_path = self.artifact_root / f"features_qa_{snapshot_id}_{feature_set_id}.md"
        json_path.write_bytes(json.dumps(report.to_dict(), indent=2, default=str).encode("utf-8"))
        md_path.write_text(report.to_markdown(), encoding="utf-8")

    def _config_hash(self) -> str:
        return compute_config_hash([
            self.repo_root / "config" / "settings.base.yml",
            self.repo_root / "config" / "data_schema.yml",
        ])

    def _resolve_path(self, path: str | Path) -> Path:
        target = Path(path)
        if target.is_absolute():
            return target
        return self.repo_root / target

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
