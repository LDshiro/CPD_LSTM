from __future__ import annotations

import copy
import json
import subprocess
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Sequence

import numpy as np
import pandas as pd

from cpdshadow.config import AppConfig, CpdLstmModelConfig, DataSchemaConfig
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
from cpdshadow.ml.artifacts import load_model_artifact, save_model_artifact
from cpdshadow.ml.dataset import (
    Standardizer,
    TrainingPanels,
    build_training_panels,
)
from cpdshadow.ml.infer import build_cpd_lstm_signals
from cpdshadow.ml.losses import sharpe_ex_cost_loss
from cpdshadow.ml.model import (
    create_model,
    require_torch,
    resolve_device,
    set_global_seed,
    set_torch_determinism,
)
from cpdshadow.ml.qa import MlQaIssue, TrainQaReport
from cpdshadow.signals import SignalBuildRequest, SignalQaReport, validate_signals_daily
from cpdshadow.storage.parquet_io import (
    atomic_replace_dir,
    make_staging_dir,
    read_parquet_dataset,
    write_parquet_dataset,
    write_parquet_part,
)
from cpdshadow.storage.registry import (
    FileRegistryRow,
    RunRegistryRow,
    append_file_registry_row,
    append_run_registry_row,
)


@dataclass(frozen=True)
class CpdLstmTrainResult:
    model: object
    standardizer: Standardizer
    metrics: dict[str, object]
    panels: TrainingPanels
    qa_report: TrainQaReport


class CpdLstmModelService:
    def __init__(
        self,
        *,
        repo_root: str | Path,
        app_config: AppConfig,
        data_schema: DataSchemaConfig,
        features_input_root: str | Path | None = None,
        continuous_input_root: str | Path | None = None,
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
        self.features_input_root = self._resolve_path(
            features_input_root or self.data_root / "features" / "features_daily"
        )
        self.continuous_input_root = self._resolve_path(
            continuous_input_root or self.data_root / "curated" / "continuous_daily"
        )
        self.signals_output_root = self._resolve_path(
            signals_output_root or self.app_config.signals.output_dataset
        )
        self.artifact_root = self._resolve_path(artifact_root or "artifacts/wp9")

    def train(
        self,
        *,
        snapshot_id: str,
        feature_set_id: str | None,
        series_id: str | None,
        roots: Sequence[str] | None,
        train_start: date,
        train_end: date,
        val_start: date,
        val_end: date,
        model_id: str,
        training_run_id: str,
        output_dir: str | Path,
        max_epochs: int | None = None,
        min_epochs: int | None = None,
        device: str | None = None,
    ) -> dict[str, object]:
        started_at = utc_now()
        invocation_run_id = make_run_id()
        config = self._effective_model_config(
            feature_set_id=feature_set_id,
            series_id=series_id,
            max_epochs=max_epochs,
            min_epochs=min_epochs,
            device=device,
        )
        config_hash = self._config_hash()
        artifact_path = self.artifact_root / f"cpd_lstm_train_qa_{training_run_id}.json"
        self.artifact_root.mkdir(parents=True, exist_ok=True)
        append_run_registry_row(
            self.run_registry_root,
            RunRegistryRow(
                run_id=invocation_run_id,
                run_type="cpd_lstm_train",
                command_name="train",
                status="started",
                started_at_utc=started_at,
                finished_at_utc=None,
                data_snapshot_id=snapshot_id,
                config_hash=config_hash,
                git_commit=_git_commit(self.repo_root),
                notes=[f"model_id={model_id}", f"training_run_id={training_run_id}"],
                artifact_path=self._relpath(artifact_path),
            ),
        )
        try:
            features_daily = self._load_features_snapshot(
                snapshot_id=snapshot_id,
                feature_set_id=config.feature_set_id,
            )
            continuous_daily = self._load_continuous_snapshot(
                snapshot_id=snapshot_id,
                series_id=config.series_id,
            )
            result = train_cpd_lstm_from_frames(
                features_daily=features_daily,
                continuous_daily=continuous_daily,
                config=config,
                snapshot_id=snapshot_id,
                roots=roots,
                train_start=train_start,
                train_end=train_end,
                val_start=val_start,
                val_end=val_end,
                model_id=model_id,
                training_run_id=training_run_id,
            )
            self._write_train_qa_artifacts(result.qa_report)
            if result.qa_report.has_errors:
                raise ValueError("WP9 CPD-LSTM training QA failed")
            created_at = utc_now()
            input_manifest_hash = stable_sha256_hex(
                {
                    "features_rows": int(len(features_daily)),
                    "continuous_rows": int(len(continuous_daily)),
                    "snapshot_id": snapshot_id,
                    "feature_set_id": config.feature_set_id,
                    "series_id": config.series_id,
                    "train_start": train_start,
                    "train_end": train_end,
                    "val_start": val_start,
                    "val_end": val_end,
                    "roots": list(roots or []),
                }
            )
            metrics = dict(result.metrics)
            metrics["device"] = config.training.device
            train_manifest = {
                "training_run_id": training_run_id,
                "model_id": model_id,
                "snapshot_id": snapshot_id,
                "feature_set_id": config.feature_set_id,
                "series_id": config.series_id,
                "train_start_date": train_start.isoformat(),
                "train_end_date": train_end.isoformat(),
                "validation_start_date": val_start.isoformat(),
                "validation_end_date": val_end.isoformat(),
                "git_commit": _git_commit(self.repo_root),
                "config_hash": config_hash,
                "input_manifest_hash": input_manifest_hash,
                "artifact_sha256": "",
                "created_at_utc": created_at.isoformat(),
            }
            resolved_output_dir = self._resolve_path(output_dir)
            artifact_sha256 = save_model_artifact(
                model=result.model,
                config=config,
                standardizer=result.standardizer,
                metrics=metrics,
                train_manifest=train_manifest,
                model_dir=resolved_output_dir,
            )
            completed_at = utc_now()
            self._append_training_run_row(
                {
                    "training_run_id": training_run_id,
                    "strategy_id": config.strategy_id,
                    "feature_set_id": config.feature_set_id,
                    "train_start_date": train_start,
                    "train_end_date": train_end,
                    "validation_start_date": val_start,
                    "validation_end_date": val_end,
                    "seed": int(config.training.seed),
                    "hyperparams_hash": stable_sha256_hex(config.model_dump(mode="python")),
                    "config_hash": config_hash,
                    "data_snapshot_id": snapshot_id,
                    "status": "succeeded",
                    "best_validation_metric": metrics.get("val_sharpe_ex_cost_best"),
                    "created_at_utc": created_at,
                    "completed_at_utc": completed_at,
                }
            )
            self._append_model_registry_row(
                {
                    "model_id": model_id,
                    "strategy_id": config.strategy_id,
                    "training_run_id": training_run_id,
                    "artifact_path": self._relpath(resolved_output_dir),
                    "artifact_sha256": artifact_sha256,
                    "feature_set_id": config.feature_set_id,
                    "config_hash": config_hash,
                    "model_status": "candidate",
                    "registered_at_utc": completed_at,
                    "notes": "WP9 candidate artifact; not promoted to shadow.",
                }
            )
            artifact = {
                "training_run_id": training_run_id,
                "model_id": model_id,
                "strategy_id": config.strategy_id,
                "snapshot_id": snapshot_id,
                "feature_set_id": config.feature_set_id,
                "series_id": config.series_id,
                "artifact_path": self._relpath(resolved_output_dir),
                "artifact_sha256": artifact_sha256,
                "metrics": metrics,
                "qa_report": result.qa_report.to_dict(),
            }
            artifact_path.write_bytes(json.dumps(artifact, indent=2, default=str).encode("utf-8"))
            append_run_registry_row(
                self.run_registry_root,
                RunRegistryRow(
                    run_id=invocation_run_id,
                    run_type="cpd_lstm_train",
                    command_name="train",
                    status="succeeded",
                    started_at_utc=started_at,
                    finished_at_utc=completed_at,
                    data_snapshot_id=snapshot_id,
                    config_hash=config_hash,
                    git_commit=_git_commit(self.repo_root),
                    notes=[f"model_id={model_id}", f"training_run_id={training_run_id}"],
                    artifact_path=self._relpath(artifact_path),
                ),
            )
            return artifact
        except Exception as exc:
            append_run_registry_row(
                self.run_registry_root,
                RunRegistryRow(
                    run_id=invocation_run_id,
                    run_type="cpd_lstm_train",
                    command_name="train",
                    status="failed",
                    started_at_utc=started_at,
                    finished_at_utc=utc_now(),
                    data_snapshot_id=snapshot_id,
                    config_hash=config_hash,
                    git_commit=_git_commit(self.repo_root),
                    notes=[
                        f"model_id={model_id}",
                        f"training_run_id={training_run_id}",
                        f"error={type(exc).__name__}",
                    ],
                    artifact_path=self._relpath(artifact_path),
                ),
            )
            raise

    def infer(
        self,
        *,
        snapshot_id: str,
        feature_set_id: str | None,
        model_dir: str | Path,
        model_id: str,
        start_date: date,
        end_date: date,
        roots: Sequence[str] | None,
        run_id: str,
        created_at_utc: datetime,
        output_dir: str | Path | None,
        overwrite: bool,
        device: str | None = None,
    ) -> dict[str, object]:
        started_at = utc_now()
        invocation_run_id = make_run_id()
        config_hash = self._config_hash()
        resolved_model_dir = self._resolve_path(model_dir)
        artifact = load_model_artifact(
            resolved_model_dir,
            device=device or self.app_config.models.cpd_lstm.training.device,
        )
        config = artifact.config
        effective_feature_set_id = feature_set_id or config.feature_set_id
        qa_artifact_path = self.artifact_root / f"cpd_lstm_infer_qa_{run_id}_{model_id}.json"
        self.artifact_root.mkdir(parents=True, exist_ok=True)
        append_run_registry_row(
            self.run_registry_root,
            RunRegistryRow(
                run_id=invocation_run_id,
                run_type="cpd_lstm_infer",
                command_name="infer",
                status="started",
                started_at_utc=started_at,
                finished_at_utc=None,
                data_snapshot_id=snapshot_id,
                config_hash=config_hash,
                git_commit=_git_commit(self.repo_root),
                notes=[f"model_id={model_id}", f"signals_run_id={run_id}"],
                artifact_path=self._relpath(qa_artifact_path),
            ),
        )
        try:
            features_daily = self._load_features_snapshot(
                snapshot_id=snapshot_id,
                feature_set_id=effective_feature_set_id,
            )
            request = SignalBuildRequest(
                run_id=run_id,
                strategy_id=config.strategy_id,
                model_id=model_id,
                feature_set_id=effective_feature_set_id,
                snapshot_id=snapshot_id,
                start_date=start_date,
                end_date=end_date,
                roots=tuple(str(root) for root in roots) if roots is not None else None,
                created_at_utc=created_at_utc,
            )
            result = build_cpd_lstm_signals(
                features_daily=features_daily,
                artifact=artifact,
                request=request,
                snapshot_id=snapshot_id,
                feature_set_id=effective_feature_set_id,
                series_id=config.series_id,
                roots=roots,
                start_date=start_date,
                end_date=end_date,
                device=device or config.training.device,
            )
            selected_features = self._select_features_for_qa(
                features_daily=features_daily,
                roots=roots,
                start_date=start_date,
                end_date=end_date,
            )
            report = validate_signals_daily(
                signals_daily=result.signals,
                request=request,
                clip_min=-config.architecture.output_clip_abs,
                clip_max=config.architecture.output_clip_abs,
                sort_keys=self.app_config.signals.stable_sort_keys,
                features_daily=selected_features,
            )
            self._write_infer_qa_artifacts(run_id=run_id, model_id=model_id, report=report)
            if report.has_errors:
                raise ValueError("WP9 CPD-LSTM inference QA failed")
            output_root_base = self._resolve_path(output_dir or self.signals_output_root)
            final_root = (
                output_root_base
                / f"strategy_id={config.strategy_id}"
                / f"model_id={model_id}"
                / f"run_id={run_id}"
            )
            if not overwrite and final_root.exists() and list(final_root.rglob("*.parquet")):
                raise ValueError(f"output already exists for CPD-LSTM signal run {run_id}")
            output_path = self._write_output_table(
                df=result.signals,
                final_root=final_root,
                table_name="signals_daily",
                unique_key=["run_id", "strategy_id", "as_of_date", "root"],
            )
            build_params_hash = stable_sha256_hex(
                {
                    "snapshot_id": snapshot_id,
                    "feature_set_id": effective_feature_set_id,
                    "series_id": config.series_id,
                    "model_id": model_id,
                    "model_dir": self._relpath(resolved_model_dir),
                    "start_date": start_date,
                    "end_date": end_date,
                    "roots": list(roots or []),
                    "run_id": run_id,
                    "created_at_utc": created_at_utc,
                    "config_hash": config_hash,
                }
            )
            append_file_registry_row(
                self.file_registry_root,
                FileRegistryRow(
                    file_id=make_file_id(snapshot_id, "signals_daily", output_path),
                    snapshot_id=snapshot_id,
                    run_id=invocation_run_id,
                    logical_table="signals_daily",
                    source_schema=config.model_family,
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
            artifact_payload = {
                "run_id": run_id,
                "model_id": model_id,
                "strategy_id": config.strategy_id,
                "snapshot_id": snapshot_id,
                "feature_set_id": effective_feature_set_id,
                "series_id": config.series_id,
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat(),
                "roots": list(roots or []),
                "output_path": self._relpath(output_path),
                "qa_report": report.to_dict(),
            }
            qa_artifact_path.write_bytes(
                json.dumps(artifact_payload, indent=2, default=str).encode("utf-8")
            )
            finished_at = utc_now()
            append_run_registry_row(
                self.run_registry_root,
                RunRegistryRow(
                    run_id=invocation_run_id,
                    run_type="cpd_lstm_infer",
                    command_name="infer",
                    status="succeeded",
                    started_at_utc=started_at,
                    finished_at_utc=finished_at,
                    data_snapshot_id=snapshot_id,
                    config_hash=config_hash,
                    git_commit=_git_commit(self.repo_root),
                    notes=[f"model_id={model_id}", f"signals_run_id={run_id}"],
                    artifact_path=self._relpath(qa_artifact_path),
                ),
            )
            return artifact_payload
        except Exception as exc:
            append_run_registry_row(
                self.run_registry_root,
                RunRegistryRow(
                    run_id=invocation_run_id,
                    run_type="cpd_lstm_infer",
                    command_name="infer",
                    status="failed",
                    started_at_utc=started_at,
                    finished_at_utc=utc_now(),
                    data_snapshot_id=snapshot_id,
                    config_hash=config_hash,
                    git_commit=_git_commit(self.repo_root),
                    notes=[
                        f"model_id={model_id}",
                        f"signals_run_id={run_id}",
                        f"error={type(exc).__name__}",
                    ],
                    artifact_path=self._relpath(qa_artifact_path),
                ),
            )
            raise

    def qa(
        self,
        *,
        model_dir: str | Path,
        signals_path: str | Path | None,
        run_id: str,
        model_id: str | None = None,
    ) -> SignalQaReport:
        artifact = load_model_artifact(self._resolve_path(model_dir), device="cpu")
        effective_model_id = model_id or str(artifact.train_manifest.get("model_id"))
        root_dir = self._resolve_path(signals_path or self.signals_output_root)
        signals_daily = read_parquet_dataset(
            root_dir
            / f"strategy_id={artifact.config.strategy_id}"
            / f"model_id={effective_model_id}"
            / f"run_id={run_id}"
        )
        if "year" in signals_daily.columns:
            signals_daily = signals_daily.drop(columns=["year"])
        if signals_daily.empty:
            raise ValueError(f"no CPD-LSTM signals found for run {run_id}")
        created_at = pd.to_datetime(signals_daily["created_at_utc"]).max().to_pydatetime()
        request = SignalBuildRequest(
            run_id=run_id,
            strategy_id=artifact.config.strategy_id,
            model_id=effective_model_id,
            feature_set_id=artifact.config.feature_set_id,
            snapshot_id=str(artifact.train_manifest.get("snapshot_id")),
            start_date=_min_date(signals_daily, "as_of_date"),
            end_date=_max_date(signals_daily, "as_of_date"),
            roots=tuple(sorted(signals_daily["root"].dropna().astype(str).unique())),
            created_at_utc=created_at,
        )
        report = validate_signals_daily(
            signals_daily=signals_daily,
            request=request,
            clip_min=-artifact.config.architecture.output_clip_abs,
            clip_max=artifact.config.architecture.output_clip_abs,
            sort_keys=self.app_config.signals.stable_sort_keys,
            features_daily=None,
        )
        self._write_infer_qa_artifacts(run_id=run_id, model_id=effective_model_id, report=report)
        return report

    def smoke(self, *, output_root: str | Path) -> dict[str, object]:
        output = self._resolve_path(output_root)
        snapshot_id = "snapshot_wp9_smoke"
        feature_set_id = self.app_config.models.cpd_lstm.feature_set_id
        series_id = self.app_config.models.cpd_lstm.series_id
        roots = tuple(self.app_config.models.cpd_lstm.smoke.roots)
        features_daily, continuous_daily = synthetic_wp9_frames(
            roots=roots,
            n_days=self.app_config.models.cpd_lstm.smoke.n_days,
            snapshot_id=snapshot_id,
            feature_set_id=feature_set_id,
            series_id=series_id,
        )
        features_root = output / "data" / "features" / "features_daily"
        continuous_root = output / "data" / "curated" / "continuous_daily"
        write_parquet_dataset(
            features_daily.assign(year=pd.to_datetime(features_daily["as_of_date"]).dt.year),
            features_root / f"feature_set_id={feature_set_id}" / f"snapshot_id={snapshot_id}",
            compression="zstd",
            partition_cols=["year"],
        )
        write_parquet_dataset(
            continuous_daily.assign(year=pd.to_datetime(continuous_daily["as_of_date"]).dt.year),
            continuous_root / f"series_id={series_id}" / f"snapshot_id={snapshot_id}",
            compression="zstd",
            partition_cols=["year"],
        )
        smoke_service = CpdLstmModelService(
            repo_root=self.repo_root,
            app_config=self.app_config,
            data_schema=self.data_schema,
            features_input_root=features_root,
            continuous_input_root=continuous_root,
            signals_output_root=output / "data" / "research" / "signals_daily",
            artifact_root=output / "artifacts" / "wp9",
        )
        train_artifact = smoke_service.train(
            snapshot_id=snapshot_id,
            feature_set_id=feature_set_id,
            series_id=series_id,
            roots=roots,
            train_start=date(2020, 3, 16),
            train_end=date(2020, 6, 30),
            val_start=date(2020, 7, 1),
            val_end=date(2020, 8, 15),
            model_id="cpd_lstm_v1_smoke",
            training_run_id="train_cpd_lstm_v1_smoke",
            output_dir=output / "artifacts" / "models" / "cpd_lstm" / "cpd_lstm_v1_smoke",
            max_epochs=self.app_config.models.cpd_lstm.smoke.max_epochs,
            min_epochs=self.app_config.models.cpd_lstm.smoke.min_epochs,
            device="cpu",
        )
        infer_artifact = smoke_service.infer(
            snapshot_id=snapshot_id,
            feature_set_id=feature_set_id,
            model_dir=output / "artifacts" / "models" / "cpd_lstm" / "cpd_lstm_v1_smoke",
            model_id="cpd_lstm_v1_smoke",
            start_date=date(2020, 8, 3),
            end_date=date(2020, 8, 31),
            roots=roots,
            run_id="infer_cpd_lstm_v1_smoke",
            created_at_utc=datetime(2026, 4, 23, tzinfo=UTC),
            output_dir=output / "data" / "research" / "signals_daily",
            overwrite=True,
            device="cpu",
        )
        return {"train": train_artifact, "infer": infer_artifact, "output_root": self._relpath(output)}

    def _effective_model_config(
        self,
        *,
        feature_set_id: str | None,
        series_id: str | None,
        max_epochs: int | None,
        min_epochs: int | None,
        device: str | None,
    ) -> CpdLstmModelConfig:
        config = self.app_config.models.cpd_lstm
        updates: dict[str, object] = {}
        if feature_set_id is not None:
            updates["feature_set_id"] = feature_set_id
        if series_id is not None:
            updates["series_id"] = series_id
        training_updates: dict[str, object] = {}
        if max_epochs is not None:
            training_updates["max_epochs"] = max_epochs
        if min_epochs is not None:
            training_updates["min_epochs"] = min_epochs
        if device is not None:
            training_updates["device"] = device
        if training_updates:
            updates["training"] = config.training.model_copy(update=training_updates)
        return config.model_copy(update=updates)

    def _load_features_snapshot(self, *, snapshot_id: str, feature_set_id: str) -> pd.DataFrame:
        root = self.features_input_root / f"feature_set_id={feature_set_id}" / f"snapshot_id={snapshot_id}"
        df = read_parquet_dataset(root)
        return _drop_partition_columns(df)

    def _load_continuous_snapshot(self, *, snapshot_id: str, series_id: str) -> pd.DataFrame:
        root = self.continuous_input_root / f"series_id={series_id}" / f"snapshot_id={snapshot_id}"
        df = read_parquet_dataset(root)
        return _drop_partition_columns(df)

    def _select_features_for_qa(
        self,
        *,
        features_daily: pd.DataFrame,
        roots: Sequence[str] | None,
        start_date: date,
        end_date: date,
    ) -> pd.DataFrame:
        selected = features_daily.copy()
        selected["as_of_date"] = pd.to_datetime(selected["as_of_date"]).dt.date
        selected = selected[
            (selected["as_of_date"] >= start_date) & (selected["as_of_date"] <= end_date)
        ]
        if roots is not None:
            selected = selected[selected["root"].astype(str).isin({str(root) for root in roots})]
        return selected.sort_values(["as_of_date", "root"], kind="stable").reset_index(drop=True)

    def _append_training_run_row(self, row: dict[str, object]) -> Path:
        target = self.data_root / "research" / "training_runs" / "strategy_id=cpd_lstm"
        year = pd.Timestamp(row["created_at_utc"]).year
        return write_parquet_part(
            pd.DataFrame([{**row, "year": year}]),
            target / f"year={year}" / f"{row['training_run_id']}.parquet",
        )

    def _append_model_registry_row(self, row: dict[str, object]) -> Path:
        target = (
            self.data_root
            / "research"
            / "model_registry"
            / "strategy_id=cpd_lstm"
            / f"model_id={row['model_id']}"
        )
        return write_parquet_part(pd.DataFrame([row]), target / "part-00000.parquet")

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
        output_df["year"] = pd.to_datetime(output_df["as_of_date"]).dt.year
        write_parquet_dataset(output_df, staging_dir, compression="zstd", partition_cols=["year"])
        return atomic_replace_dir(staging_dir, final_root)

    def _write_train_qa_artifacts(self, report: TrainQaReport) -> None:
        self.artifact_root.mkdir(parents=True, exist_ok=True)
        json_path = self.artifact_root / f"cpd_lstm_train_qa_{report.training_run_id}.json"
        md_path = self.artifact_root / f"cpd_lstm_train_qa_{report.training_run_id}.md"
        json_path.write_bytes(json.dumps(report.to_dict(), indent=2, default=str).encode("utf-8"))
        md_path.write_text(report.to_markdown(), encoding="utf-8")

    def _write_infer_qa_artifacts(
        self,
        *,
        run_id: str,
        model_id: str,
        report: SignalQaReport,
    ) -> None:
        self.artifact_root.mkdir(parents=True, exist_ok=True)
        json_path = self.artifact_root / f"cpd_lstm_infer_qa_{run_id}_{model_id}.json"
        md_path = self.artifact_root / f"cpd_lstm_infer_qa_{run_id}_{model_id}.md"
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


def train_cpd_lstm_from_frames(
    *,
    features_daily: pd.DataFrame,
    continuous_daily: pd.DataFrame,
    config: CpdLstmModelConfig,
    snapshot_id: str,
    roots: Sequence[str] | None,
    train_start: date,
    train_end: date,
    val_start: date,
    val_end: date,
    model_id: str,
    training_run_id: str,
) -> CpdLstmTrainResult:
    torch = require_torch()
    set_global_seed(config.training.seed)
    set_torch_determinism(config.training.deterministic_mode)
    device = resolve_device(config.training.device)
    panels = build_training_panels(
        features_daily=features_daily,
        continuous_daily=continuous_daily,
        config=config,
        snapshot_id=snapshot_id,
        feature_set_id=config.feature_set_id,
        series_id=config.series_id,
        roots=roots,
        train_start=train_start,
        train_end=train_end,
        val_start=val_start,
        val_end=val_end,
    )
    fit_values = _standardizer_fit_values(
        features_daily=features_daily,
        config=config,
        snapshot_id=snapshot_id,
        roots=roots,
        train_start=train_start,
        train_end=train_end,
    )
    standardizer = Standardizer.fit(
        fit_values,
        feature_order=config.feature_order,
        min_std=config.standardization.min_std,
        clip_abs=config.standardization.clip_abs_after_standardization,
    )
    x_train = torch.as_tensor(standardizer.transform(panels.train.x), dtype=torch.float32, device=device)
    y_train = torch.as_tensor(panels.train.y_norm, dtype=torch.float32, device=device)
    vol_train = torch.as_tensor(panels.train.annualized_vol, dtype=torch.float32, device=device)
    x_val = torch.as_tensor(standardizer.transform(panels.validation.x), dtype=torch.float32, device=device)
    y_val = torch.as_tensor(panels.validation.y_norm, dtype=torch.float32, device=device)
    vol_val = torch.as_tensor(panels.validation.annualized_vol, dtype=torch.float32, device=device)

    model = create_model(config).to(device)
    optimizer = torch.optim.AdamW(
        model.parameters(),
        lr=config.training.learning_rate,
        weight_decay=config.training.weight_decay,
    )
    best_metric = float("-inf")
    best_epoch = 0
    best_state: dict[str, object] | None = None
    epochs_without_improvement = 0
    train_loss_last = 0.0
    val_loss_best = 0.0
    val_mean_pnl = 0.0
    val_std_pnl = 0.0

    for epoch in range(1, config.training.max_epochs + 1):
        model.train()
        optimizer.zero_grad(set_to_none=True)
        train_signals = model(x_train)
        train_loss = sharpe_ex_cost_loss(
            signals=train_signals,
            y_norm=y_train,
            dates=panels.train.dates,
            roots=panels.train.roots,
            annualized_vol=vol_train,
            config=config,
        )
        train_loss.loss.backward()
        torch.nn.utils.clip_grad_norm_(model.parameters(), config.training.gradient_clip_norm)
        optimizer.step()
        train_loss_last = float(train_loss.loss.detach().cpu().item())

        model.eval()
        with torch.no_grad():
            val_signals = model(x_val)
            val_loss = sharpe_ex_cost_loss(
                signals=val_signals,
                y_norm=y_val,
                dates=panels.validation.dates,
                roots=panels.validation.roots,
                annualized_vol=vol_val,
                config=config,
            )
        if val_loss.sharpe > best_metric:
            best_metric = val_loss.sharpe
            best_epoch = epoch
            best_state = copy.deepcopy(model.state_dict())
            val_loss_best = float(val_loss.loss.detach().cpu().item())
            val_mean_pnl = val_loss.mean_pnl
            val_std_pnl = val_loss.std_pnl
            epochs_without_improvement = 0
        else:
            epochs_without_improvement += 1
        if (
            epoch >= config.training.min_epochs
            and epochs_without_improvement >= config.training.early_stopping_patience
        ):
            break

    if best_state is not None:
        model.load_state_dict(best_state)
    model.eval()
    with torch.no_grad():
        train_output = model(x_train)
        val_output = model(x_val)
    issues: list[MlQaIssue] = []
    if not bool(torch.isfinite(train_output).all()) or not bool(torch.isfinite(val_output).all()):
        issues.append(MlQaIssue("nonfinite_model_output", "error", "model outputs must be finite"))
    if float(torch.max(torch.abs(val_output)).detach().cpu().item()) > config.architecture.output_clip_abs + 1.0e-6:
        issues.append(MlQaIssue("model_output_out_of_range", "error", "tanh output exceeded clip bounds"))
    if panels.warnings:
        issues.append(
            MlQaIssue(
                "skipped_samples",
                "warning",
                "some candidate train/validation rows were skipped",
                {"count": len(panels.warnings)},
            )
        )

    metrics = {
        "train_loss_last": train_loss_last,
        "val_loss_best": val_loss_best,
        "val_sharpe_ex_cost_best": best_metric,
        "val_mean_pnl": val_mean_pnl,
        "val_std_pnl": val_std_pnl,
        "n_train_samples": panels.train.sample_count,
        "n_val_samples": panels.validation.sample_count,
        "n_roots": len(set(panels.train.roots) | set(panels.validation.roots)),
        "n_features": len(config.feature_order),
        "epochs_completed": max(best_epoch, 1),
        "best_epoch": best_epoch,
        "seed": config.training.seed,
        "torch_version": torch.__version__,
        "cuda_available": bool(torch.cuda.is_available()),
    }
    summary = dict(metrics)
    summary["fatal_error_count"] = sum(issue.severity == "error" for issue in issues)
    summary["warning_count"] = sum(issue.severity == "warning" for issue in issues)
    qa_report = TrainQaReport(
        training_run_id=training_run_id,
        model_id=model_id,
        strategy_id=config.strategy_id,
        summary=summary,
        issues=tuple(issues),
    )
    return CpdLstmTrainResult(
        model=model,
        standardizer=standardizer,
        metrics=metrics,
        panels=panels,
        qa_report=qa_report,
    )


def synthetic_wp9_frames(
    *,
    roots: Sequence[str],
    n_days: int,
    snapshot_id: str,
    feature_set_id: str,
    series_id: str,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    dates = list(pd.bdate_range("2020-01-01", periods=n_days).date)
    rows_features: list[dict[str, object]] = []
    rows_continuous: list[dict[str, object]] = []
    feature_columns = list(CpdLstmModelConfig().feature_order)
    for root_idx, root in enumerate(roots):
        price = 100.0 + root_idx * 25.0
        for idx, stamp in enumerate(dates):
            as_of_date = pd.Timestamp(stamp).date()
            drift = 0.0005 * ((idx % 11) - 5) + 0.0002 * (root_idx + 1)
            daily_return = drift + 0.001 * np.sin(idx / 7.0 + root_idx)
            price *= 1.0 + daily_return
            complete = idx >= 70
            feature_payload: dict[str, object] = {}
            for feature_idx, column in enumerate(feature_columns):
                feature_payload[column] = (
                    float(np.sin(idx / (feature_idx + 5.0) + root_idx))
                    if complete
                    else None
                )
            rows_features.append({
                "feature_set_id": feature_set_id,
                "as_of_date": as_of_date,
                "root": root,
                "series_id": series_id,
                **feature_payload,
                "annualized_vol_60": 0.18 + 0.01 * root_idx if complete else None,
                "is_complete": complete,
                "warmup_status": "ok" if complete else "warmup",
                "feature_hash": stable_sha256_hex({"root": root, "date": as_of_date.isoformat()}),
                "builder_version": "features_builder_v1",
                "snapshot_id": snapshot_id,
            })
            rows_continuous.append({
                "series_id": series_id,
                "as_of_date": as_of_date,
                "root": root,
                "lead_raw_symbol": f"{root}H0",
                "raw_settle_price": price,
                "adj_settle_price": price,
                "adj_factor": 1.0,
                "daily_return": daily_return if idx > 0 else None,
                "settle_status": "final",
                "roll_flag": False,
                "roll_event_id": None,
                "is_usable_for_signal": idx > 0,
                "quality_flags": [],
                "builder_version": "continuous_builder_v1",
                "snapshot_id": snapshot_id,
            })
    return pd.DataFrame(rows_features), pd.DataFrame(rows_continuous)


def _drop_partition_columns(df: pd.DataFrame) -> pd.DataFrame:
    if "year" in df.columns:
        return df.drop(columns=["year"]).reset_index(drop=True)
    return df.reset_index(drop=True)


def _standardizer_fit_values(
    *,
    features_daily: pd.DataFrame,
    config: CpdLstmModelConfig,
    snapshot_id: str,
    roots: Sequence[str] | None,
    train_start: date,
    train_end: date,
) -> np.ndarray:
    selected = features_daily.copy()
    selected["as_of_date"] = pd.to_datetime(selected["as_of_date"]).dt.date
    selected = selected[
        (selected["feature_set_id"].astype(str) == config.feature_set_id)
        & (selected["series_id"].astype(str) == config.series_id)
        & (selected["snapshot_id"].astype(str) == snapshot_id)
        & (selected["as_of_date"] >= train_start)
        & (selected["as_of_date"] <= train_end)
        & selected["is_complete"].fillna(False).astype(bool)
        & (selected["warmup_status"].astype(str) == "ok")
    ]
    if roots is not None:
        selected = selected[selected["root"].astype(str).isin({str(root) for root in roots})]
    values = selected[list(config.feature_order)].apply(pd.to_numeric, errors="coerce").to_numpy(dtype=np.float32)
    if values.size == 0 or not np.isfinite(values).all():
        raise ValueError("cannot fit CPD-LSTM standardizer on selected train rows")
    return values.reshape(values.shape[0], 1, values.shape[1])


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
    return completed.stdout.strip() or "unknown-local"


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
