from __future__ import annotations

import json
import subprocess
from datetime import date, datetime
from pathlib import Path

import pandas as pd

from cpdshadow.config import AppConfig, DataSchemaConfig
from cpdshadow.ids import (
    config_hash as compute_config_hash,
)
from cpdshadow.ids import (
    file_sha256,
    make_file_id,
    make_run_id,
    stable_sha256_hex,
    utc_now,
)
from cpdshadow.ids import (
    manifest_hash as compute_manifest_hash,
)
from cpdshadow.signal_io import build_formulaic_model_registry_row, write_formula_artifact
from cpdshadow.signals import SignalBuildRequest, SignalQaReport, validate_signals_daily
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
from cpdshadow.strategies.tsmom import TsmomSignalStrategy


class SignalsBuilderService:
    def __init__(
        self,
        *,
        repo_root: str | Path,
        app_config: AppConfig,
        data_schema: DataSchemaConfig,
        features_input_root: str | Path | None = None,
        signals_output_root: str | Path | None = None,
        artifact_root: str | Path | None = None,
    ) -> None:
        self.repo_root = Path(repo_root)
        self.app_config = app_config
        self.data_schema = data_schema
        self.data_root = self.repo_root / "data"
        self.meta_root = self.data_root / "meta"
        self.run_registry_root = self.meta_root / "run_registry"
        self.file_registry_root = self.meta_root / "data_file_registry"
        if features_input_root is None:
            self.features_input_root = self.data_root / "features" / "features_daily"
        else:
            self.features_input_root = self._resolve_path(features_input_root)
        if signals_output_root is None:
            self.signals_output_root = self._resolve_path(self.app_config.signals.output_dataset)
        else:
            self.signals_output_root = self._resolve_path(signals_output_root)
        if artifact_root is None:
            self.artifact_root = self._resolve_path(self.app_config.signals.qa_artifact_dir)
        else:
            self.artifact_root = self._resolve_path(artifact_root)

    def build_tsmom(
        self,
        *,
        snapshot_id: str,
        feature_set_id: str | None,
        start_date: date,
        end_date: date,
        roots: list[str] | None,
        run_id: str,
        created_at_utc: datetime,
        overwrite: bool,
    ) -> dict[str, object]:
        started_at = utc_now()
        invocation_run_id = make_run_id()
        config_hash = self._config_hash()
        strategy_config = self.app_config.strategies.tsmom
        signals_config = self.app_config.signals
        effective_feature_set_id = feature_set_id or strategy_config.feature_set_id
        artifact_path = self.artifact_root / f"build_{run_id}.json"
        self.artifact_root.mkdir(parents=True, exist_ok=True)
        append_run_registry_row(
            self.run_registry_root,
            RunRegistryRow(
                run_id=invocation_run_id,
                run_type="signals_builder",
                command_name="build",
                status="started",
                started_at_utc=started_at,
                finished_at_utc=None,
                data_snapshot_id=snapshot_id,
                config_hash=config_hash,
                git_commit=_git_commit(self.repo_root),
                notes=[f"strategy_id={strategy_config.strategy_id}", f"signals_run_id={run_id}"],
                artifact_path=self._relpath(artifact_path),
            ),
        )

        try:
            features_daily = self._load_features_snapshot(
                snapshot_id=snapshot_id,
                feature_set_id=effective_feature_set_id,
            )
            selected_features = self._select_features(
                features_daily=features_daily,
                roots=roots,
                start_date=start_date,
                end_date=end_date,
            )
            if selected_features.empty:
                raise ValueError(
                    f"no features_daily rows found for snapshot {snapshot_id}, "
                    f"feature set {effective_feature_set_id}, and the requested range"
                )

            request = SignalBuildRequest(
                run_id=run_id,
                strategy_id=strategy_config.strategy_id,
                model_id=strategy_config.model_id,
                feature_set_id=effective_feature_set_id,
                snapshot_id=snapshot_id,
                start_date=start_date,
                end_date=end_date,
                roots=tuple(sorted(selected_features["root"].dropna().astype(str).unique())),
                created_at_utc=created_at_utc,
            )
            build_params_hash = stable_sha256_hex(
                {
                    "snapshot_id": snapshot_id,
                    "feature_set_id": effective_feature_set_id,
                    "strategy_id": strategy_config.strategy_id,
                    "model_id": strategy_config.model_id,
                    "start_date": start_date,
                    "end_date": end_date,
                    "roots": list(request.roots or ()),
                    "signals_config": self.app_config.signals.model_dump(mode="python"),
                    "strategy_config": strategy_config.model_dump(mode="python"),
                    "signals_run_id": run_id,
                    "created_at_utc": created_at_utc,
                    "config_hash": config_hash,
                }
            )

            output_root = (
                self.signals_output_root
                / f"strategy_id={strategy_config.strategy_id}"
                / f"model_id={strategy_config.model_id}"
                / f"run_id={run_id}"
            )
            if not overwrite and output_root.exists() and list(output_root.rglob("*.parquet")):
                raise ValueError(
                    f"output already exists for signals run {run_id}; rerun with --overwrite"
                )

            formula_path, _, formula_sha256 = write_formula_artifact(
                repo_root=self.repo_root,
                signals_config=signals_config,
                strategy_config=strategy_config,
            )
            model_registry_row = build_formulaic_model_registry_row(
                strategy_config=strategy_config,
                artifact_path=Path(self._relpath(formula_path)),
                artifact_sha256=formula_sha256,
            )

            strategy = TsmomSignalStrategy(
                signals_config=signals_config,
                strategy_config=strategy_config,
            )
            result = strategy.build_signals(selected_features, request)
            report = validate_signals_daily(
                signals_daily=result.signals,
                request=request,
                clip_min=signals_config.clip_min,
                clip_max=signals_config.clip_max,
                sort_keys=signals_config.stable_sort_keys,
                features_daily=selected_features,
            )
            self._write_qa_artifacts(run_id=run_id, report=report)

            artifact = {
                "run_id": run_id,
                "strategy_id": strategy_config.strategy_id,
                "model_id": strategy_config.model_id,
                "feature_set_id": effective_feature_set_id,
                "snapshot_id": snapshot_id,
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat(),
                "roots": list(request.roots or ()),
                "build_params_hash": build_params_hash,
                "row_count": int(len(result.signals)),
                "formula_artifact_path": self._relpath(formula_path),
                "formula_artifact_sha256": formula_sha256,
                "model_registry_row": model_registry_row,
                "qa_report": report.to_dict(),
            }
            artifact_path.write_bytes(json.dumps(artifact, indent=2, default=str).encode("utf-8"))
            if report.has_errors:
                raise ValueError("WP8 signals QA failed")

            output_path = self._write_output_table(
                df=result.signals,
                final_root=output_root,
                table_name="signals_daily",
                unique_key=["run_id", "strategy_id", "as_of_date", "root"],
            )
            append_file_registry_row(
                self.file_registry_root,
                FileRegistryRow(
                    file_id=make_file_id(snapshot_id, "signals_daily", output_path),
                    snapshot_id=snapshot_id,
                    run_id=invocation_run_id,
                    logical_table="signals_daily",
                    source_schema=strategy_config.signal_version,
                    path=self._relpath(output_path),
                    content_sha256=_directory_manifest_hash(output_path),
                    row_count=int(len(result.signals)),
                    min_trade_date=_min_date(result.signals, "as_of_date"),
                    max_trade_date=_max_date(result.signals, "as_of_date"),
                    request_params_hash=build_params_hash,
                    cached=False,
                    created_at_utc=utc_now(),
                ),
            )

            artifact["output_path"] = self._relpath(output_path)
            artifact_path.write_bytes(json.dumps(artifact, indent=2, default=str).encode("utf-8"))
            finished_at = utc_now()
            append_run_registry_row(
                self.run_registry_root,
                RunRegistryRow(
                    run_id=invocation_run_id,
                    run_type="signals_builder",
                    command_name="build",
                    status="succeeded",
                    started_at_utc=started_at,
                    finished_at_utc=finished_at,
                    data_snapshot_id=snapshot_id,
                    config_hash=config_hash,
                    git_commit=_git_commit(self.repo_root),
                    notes=[
                        f"strategy_id={strategy_config.strategy_id}",
                        f"signals_run_id={run_id}",
                    ],
                    artifact_path=self._relpath(artifact_path),
                ),
            )
            return artifact
        except Exception as exc:
            finished_at = utc_now()
            append_run_registry_row(
                self.run_registry_root,
                RunRegistryRow(
                    run_id=invocation_run_id,
                    run_type="signals_builder",
                    command_name="build",
                    status="failed",
                    started_at_utc=started_at,
                    finished_at_utc=finished_at,
                    data_snapshot_id=snapshot_id,
                    config_hash=config_hash,
                    git_commit=_git_commit(self.repo_root),
                    notes=[
                        f"strategy_id={strategy_config.strategy_id}",
                        f"signals_run_id={run_id}",
                        f"error={type(exc).__name__}",
                    ],
                    artifact_path=self._relpath(artifact_path),
                ),
            )
            raise

    def qa(
        self,
        *,
        run_id: str,
        signals_path: str | Path | None = None,
        artifact_dir: str | Path | None = None,
    ) -> SignalQaReport:
        started_at = utc_now()
        invocation_run_id = make_run_id()
        config_hash = self._config_hash()
        strategy_config = self.app_config.strategies.tsmom
        root_dir = (
            self.signals_output_root if signals_path is None else self._resolve_path(signals_path)
        )
        artifacts_root = (
            self.artifact_root if artifact_dir is None else self._resolve_path(artifact_dir)
        )
        artifact_path = artifacts_root / f"qa_{run_id}.json"
        artifacts_root.mkdir(parents=True, exist_ok=True)
        append_run_registry_row(
            self.run_registry_root,
            RunRegistryRow(
                run_id=invocation_run_id,
                run_type="signals_builder",
                command_name="qa",
                status="started",
                started_at_utc=started_at,
                finished_at_utc=None,
                data_snapshot_id=None,
                config_hash=config_hash,
                git_commit=_git_commit(self.repo_root),
                notes=[f"strategy_id={strategy_config.strategy_id}", f"signals_run_id={run_id}"],
                artifact_path=self._relpath(artifact_path),
            ),
        )

        try:
            build_artifact_path = artifacts_root / f"build_{run_id}.json"
            if not build_artifact_path.exists():
                raise ValueError(f"build artifact not found for signals run {run_id}")
            build_artifact = json.loads(build_artifact_path.read_text(encoding="utf-8"))
            snapshot_id = str(build_artifact["snapshot_id"])
            feature_set_id = str(build_artifact["feature_set_id"])
            request = SignalBuildRequest(
                run_id=run_id,
                strategy_id=strategy_config.strategy_id,
                model_id=strategy_config.model_id,
                feature_set_id=feature_set_id,
                snapshot_id=snapshot_id,
                start_date=date.fromisoformat(str(build_artifact["start_date"])),
                end_date=date.fromisoformat(str(build_artifact["end_date"])),
                roots=tuple(str(root) for root in build_artifact.get("roots", [])),
                created_at_utc=datetime.fromisoformat(
                    str(build_artifact["qa_report"]["summary"]["created_at_utc"])
                ),
            )
            signals_daily = read_parquet_dataset(
                root_dir
                / f"strategy_id={strategy_config.strategy_id}"
                / f"model_id={strategy_config.model_id}"
                / f"run_id={run_id}"
            )
            if "year" in signals_daily.columns:
                signals_daily = signals_daily.drop(columns=["year"])
            if signals_daily.empty:
                raise ValueError(f"no signals_daily outputs found for run {run_id}")

            features_daily = self._select_features(
                features_daily=self._load_features_snapshot(
                    snapshot_id=snapshot_id,
                    feature_set_id=feature_set_id,
                ),
                roots=list(request.roots or ()),
                start_date=request.start_date or date.min,
                end_date=request.end_date or date.max,
            )
            report = validate_signals_daily(
                signals_daily=signals_daily,
                request=request,
                clip_min=self.app_config.signals.clip_min,
                clip_max=self.app_config.signals.clip_max,
                sort_keys=self.app_config.signals.stable_sort_keys,
                features_daily=features_daily,
            )
            self._write_qa_artifacts(run_id=run_id, report=report, artifact_root=artifacts_root)
            artifact_path.write_bytes(
                json.dumps(report.to_dict(), indent=2, default=str).encode("utf-8")
            )
            finished_at = utc_now()
            append_run_registry_row(
                self.run_registry_root,
                RunRegistryRow(
                    run_id=invocation_run_id,
                    run_type="signals_builder",
                    command_name="qa",
                    status="succeeded",
                    started_at_utc=started_at,
                    finished_at_utc=finished_at,
                    data_snapshot_id=snapshot_id,
                    config_hash=config_hash,
                    git_commit=_git_commit(self.repo_root),
                    notes=[
                        f"strategy_id={strategy_config.strategy_id}",
                        f"signals_run_id={run_id}",
                    ],
                    artifact_path=self._relpath(artifact_path),
                ),
            )
            return report
        except Exception as exc:
            finished_at = utc_now()
            append_run_registry_row(
                self.run_registry_root,
                RunRegistryRow(
                    run_id=invocation_run_id,
                    run_type="signals_builder",
                    command_name="qa",
                    status="failed",
                    started_at_utc=started_at,
                    finished_at_utc=finished_at,
                    data_snapshot_id=None,
                    config_hash=config_hash,
                    git_commit=_git_commit(self.repo_root),
                    notes=[
                        f"strategy_id={strategy_config.strategy_id}",
                        f"signals_run_id={run_id}",
                        f"error={type(exc).__name__}",
                    ],
                    artifact_path=self._relpath(artifact_path),
                ),
            )
            raise

    def _load_features_snapshot(self, *, snapshot_id: str, feature_set_id: str) -> pd.DataFrame:
        root = (
            self.features_input_root
            / f"feature_set_id={feature_set_id}"
            / f"snapshot_id={snapshot_id}"
        )
        df = read_parquet_dataset(root)
        if "year" in df.columns:
            df = df.drop(columns=["year"])
        return df.reset_index(drop=True)

    def _select_features(
        self,
        *,
        features_daily: pd.DataFrame,
        roots: list[str] | None,
        start_date: date,
        end_date: date,
    ) -> pd.DataFrame:
        if features_daily.empty:
            return features_daily
        selected = features_daily.copy()
        selected["as_of_date"] = pd.to_datetime(selected["as_of_date"]).dt.date
        selected = selected[
            (selected["as_of_date"] >= start_date) & (selected["as_of_date"] <= end_date)
        ]
        if roots is not None:
            root_set = {str(root) for root in roots}
            selected = selected[selected["root"].astype(str).isin(root_set)]
        if selected.empty:
            return selected
        return selected.sort_values(
            self.app_config.signals.stable_sort_keys, kind="stable"
        ).reset_index(drop=True)

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
                output_df, staging_dir, compression="zstd", partition_cols=["year"]
            )
        else:
            write_parquet_dataset(output_df, staging_dir, compression="zstd", partition_cols=None)
        return atomic_replace_dir(staging_dir, final_root)

    def _write_qa_artifacts(
        self,
        *,
        run_id: str,
        report: SignalQaReport,
        artifact_root: Path | None = None,
    ) -> None:
        target_root = artifact_root or self.artifact_root
        target_root.mkdir(parents=True, exist_ok=True)
        json_path = target_root / f"signals_qa_{run_id}.json"
        md_path = target_root / f"signals_qa_{run_id}.md"
        json_path.write_bytes(json.dumps(report.to_dict(), indent=2, default=str).encode("utf-8"))
        md_path.write_text(report.to_markdown(), encoding="utf-8")

    def _config_hash(self) -> str:
        return compute_config_hash(
            [
                self.repo_root / "config" / "settings.base.yml",
                self.repo_root / "config" / "data_schema.yml",
            ]
        )

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
