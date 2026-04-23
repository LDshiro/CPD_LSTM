from __future__ import annotations

import json
import os
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pandas as pd
import typer
from rich.console import Console
from rich.table import Table

from cpdshadow.broker.ibkr.service import IbkrBrokerService
from cpdshadow.config import load_data_schema_yaml, load_databento_ingest_yaml, load_yaml
from cpdshadow.execution_boundary import ExecutionBoundaryService
from cpdshadow.ids import canonical_json_bytes
from cpdshadow.ingest.continuous_builder import ContinuousBuilderService
from cpdshadow.ingest.databento_raw import DatabentoIngestService
from cpdshadow.ingest.features_builder import FeaturesBuilderService
from cpdshadow.ingest.roll_engine import RollEngineService
from cpdshadow.ingest.signals_builder import SignalsBuilderService
from cpdshadow.instruments import load_instrument_master
from cpdshadow.model_release import (
    evaluate_model_release,
    package_model_release,
    qa_model_release,
)
from cpdshadow.research.reporting import write_walkforward_report
from cpdshadow.research.walkforward import (
    WalkforwardContext,
    evaluate_oos_signals,
    plan_walkforward_windows,
    qa_walkforward_run,
    run_walkforward,
)
from cpdshadow.storage.parquet_io import read_parquet_dataset, write_parquet_part
from cpdshadow.vendor.databento_client import HistoricalDatabentoClient

app = typer.Typer(no_args_is_help=True)
ingest_app = typer.Typer(no_args_is_help=True)
databento_app = typer.Typer(no_args_is_help=True)
roll_engine_app = typer.Typer(no_args_is_help=True)
continuous_app = typer.Typer(no_args_is_help=True)
features_app = typer.Typer(no_args_is_help=True)
signals_app = typer.Typer(no_args_is_help=True)
tsmom_app = typer.Typer(no_args_is_help=True)
models_app = typer.Typer(no_args_is_help=True)
cpd_lstm_app = typer.Typer(no_args_is_help=True)
research_app = typer.Typer(no_args_is_help=True)
walkforward_app = typer.Typer(no_args_is_help=True)
model_rc_app = typer.Typer(no_args_is_help=True)
broker_boundary_app = typer.Typer(no_args_is_help=True)
broker_ibkr_app = typer.Typer(no_args_is_help=True)
console = Console()

app.add_typer(ingest_app, name="ingest")
ingest_app.add_typer(databento_app, name="databento")
app.add_typer(roll_engine_app, name="roll-engine")
app.add_typer(continuous_app, name="continuous")
app.add_typer(features_app, name="features")
app.add_typer(signals_app, name="signals")
signals_app.add_typer(tsmom_app, name="tsmom")
app.add_typer(models_app, name="models")
models_app.add_typer(cpd_lstm_app, name="cpd-lstm")
app.add_typer(research_app, name="research")
research_app.add_typer(walkforward_app, name="walkforward")
app.add_typer(model_rc_app, name="model-rc")
app.add_typer(broker_boundary_app, name="broker-boundary")
app.add_typer(broker_ibkr_app, name="broker-ibkr")


def _build_service(
    repo_root: Path, *, client: HistoricalDatabentoClient | None = None
) -> DatabentoIngestService:
    ingest_config = load_databento_ingest_yaml(repo_root / "config" / "databento.ingest.yml")
    data_schema = load_data_schema_yaml(repo_root / "config" / "data_schema.yml")
    instrument_master = load_instrument_master(repo_root / "config" / "instruments.yml")
    return DatabentoIngestService(
        repo_root=repo_root,
        ingest_config=ingest_config,
        data_schema=data_schema,
        instrument_master=instrument_master,
        client=client,
    )


def _make_databento_client(repo_root: Path) -> HistoricalDatabentoClient:
    ingest_config = load_databento_ingest_yaml(repo_root / "config" / "databento.ingest.yml")
    return HistoricalDatabentoClient(
        api_key=os.getenv(ingest_config.client.key_env),
        max_retries=ingest_config.client.max_retries,
        retry_backoff_seconds=ingest_config.client.retry_backoff_seconds,
    )


def _build_roll_service(repo_root: Path, data_dir: Path) -> RollEngineService:
    app_config = load_yaml(repo_root / "config" / "settings.base.yml")
    data_schema = load_data_schema_yaml(repo_root / "config" / "data_schema.yml")
    instrument_master = load_instrument_master(repo_root / "config" / "instruments.yml")
    return RollEngineService(
        repo_root=repo_root,
        data_root=data_dir,
        app_config=app_config,
        data_schema=data_schema,
        instrument_master=instrument_master,
    )


def _build_continuous_service(
    repo_root: Path,
    data_dir: Path,
    *,
    output_dir: Path | None = None,
    artifacts_dir: Path | None = None,
) -> ContinuousBuilderService:
    app_config = load_yaml(repo_root / "config" / "settings.base.yml")
    data_schema = load_data_schema_yaml(repo_root / "config" / "data_schema.yml")
    return ContinuousBuilderService(
        repo_root=repo_root,
        data_root=data_dir,
        app_config=app_config,
        data_schema=data_schema,
        curated_output_root=output_dir,
        artifact_root=artifacts_dir,
    )


def _build_features_service(
    repo_root: Path,
    *,
    input_dir: Path | None = None,
    output_dir: Path | None = None,
    artifacts_dir: Path | None = None,
) -> FeaturesBuilderService:
    app_config = load_yaml(repo_root / "config" / "settings.base.yml")
    data_schema = load_data_schema_yaml(repo_root / "config" / "data_schema.yml")
    return FeaturesBuilderService(
        repo_root=repo_root,
        app_config=app_config,
        data_schema=data_schema,
        continuous_input_root=input_dir,
        features_output_root=output_dir,
        artifact_root=artifacts_dir,
    )


def _build_signals_service(
    repo_root: Path,
    *,
    features_path: Path | None = None,
    output_dir: Path | None = None,
    artifact_dir: Path | None = None,
) -> SignalsBuilderService:
    app_config = load_yaml(repo_root / "config" / "settings.base.yml")
    data_schema = load_data_schema_yaml(repo_root / "config" / "data_schema.yml")
    return SignalsBuilderService(
        repo_root=repo_root,
        app_config=app_config,
        data_schema=data_schema,
        features_input_root=features_path,
        signals_output_root=output_dir,
        artifact_root=artifact_dir,
    )


def _build_cpd_lstm_service(
    repo_root: Path,
    *,
    features_path: Path | None = None,
    continuous_path: Path | None = None,
    output_dir: Path | None = None,
    artifact_dir: Path | None = None,
) -> Any:
    from cpdshadow.ml.train import CpdLstmModelService

    app_config = load_yaml(repo_root / "config" / "settings.base.yml")
    data_schema = load_data_schema_yaml(repo_root / "config" / "data_schema.yml")
    return CpdLstmModelService(
        repo_root=repo_root,
        app_config=app_config,
        data_schema=data_schema,
        features_input_root=features_path,
        continuous_input_root=continuous_path,
        signals_output_root=output_dir,
        artifact_root=artifact_dir,
    )


def _build_walkforward_context(
    *,
    repo_root: Path,
    features_path: Path,
    continuous_path: Path,
    settings_path: Path,
    instruments_path: Path,
    snapshot_id: str,
    feature_set_id: str,
    series_id: str,
    roots: str,
    run_id: str,
    output_dir: Path,
) -> WalkforwardContext:
    settings_resolved = _resolve_repo_path(repo_root, settings_path)
    return WalkforwardContext(
        repo_root=repo_root,
        app_config=load_yaml(settings_resolved),
        data_schema=load_data_schema_yaml(repo_root / "config" / "data_schema.yml"),
        instrument_master=load_instrument_master(_resolve_repo_path(repo_root, instruments_path)),
        features_path=_resolve_repo_path(repo_root, features_path),
        continuous_path=_resolve_repo_path(repo_root, continuous_path),
        snapshot_id=snapshot_id,
        feature_set_id=feature_set_id,
        series_id=series_id,
        roots=tuple(_parse_csv(roots) or ()),
        run_id=run_id,
        output_dir=_resolve_repo_path(repo_root, output_dir),
        settings_path=settings_resolved,
    )


def _build_execution_boundary_service(repo_root: Path) -> ExecutionBoundaryService:
    return ExecutionBoundaryService(repo_root=repo_root)


def _build_ibkr_broker_service(repo_root: Path) -> IbkrBrokerService:
    return IbkrBrokerService(repo_root=repo_root)


def _resolve_repo_path(repo_root: Path, path: Path) -> Path:
    return path if path.is_absolute() else repo_root / path


def _parse_csv(value: str | None) -> list[str] | None:
    if value is None or not value.strip():
        return None
    return [item.strip() for item in value.split(",") if item.strip()]


def _default_output(repo_root: Path, prefix: str) -> Path:
    return repo_root / "artifacts" / "wp4" / f"{prefix}.json"


def _parse_utc_datetime(value: str) -> datetime:
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


@databento_app.command("plan")
def databento_plan(
    start: date = typer.Option(...),
    end: date = typer.Option(...),
    roots: str | None = typer.Option(None),
    schemas: str | None = typer.Option(None),
    output: Path | None = typer.Option(None),
    repo_root: Path = typer.Option(Path("."), hidden=True),
) -> None:
    service = _build_service(repo_root)
    plan = service.plan(
        start_date=start,
        end_date=end,
        roots=_parse_csv(roots),
        schemas=_parse_csv(schemas),
    )
    artifact = {
        "dataset": plan.dataset,
        "start": plan.start,
        "end": plan.end,
        "roots": list(plan.roots),
        "schemas": list(plan.schemas),
        "chunk_mode": plan.chunk_mode,
        "config_hash": plan.config_hash,
        "request_plan_hash": plan.request_plan_hash,
        "requests": [request.__dict__ for request in plan.requests],
    }
    target = output or _default_output(repo_root, f"plan_{start.isoformat()}_{end.isoformat()}")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(canonical_json_bytes(artifact))
    console.print(f"Wrote plan artifact to {target}")


@databento_app.command("smoke")
def databento_smoke(
    start: date = typer.Option(...),
    end: date = typer.Option(...),
    roots: str | None = typer.Option(None),
    schemas: str | None = typer.Option(None),
    max_cost_usd: float = typer.Option(1.0),
    execute: bool = typer.Option(False, help="Required to call Databento."),
    skip_preflight: bool = typer.Option(False),
    force: bool = typer.Option(False),
    repo_root: Path = typer.Option(Path("."), hidden=True),
) -> None:
    service = _build_service(repo_root, client=_make_databento_client(repo_root))
    max_days = service.ingest_config.cost_control.smoke_max_days
    if (end - start).days > max_days:
        raise typer.BadParameter(f"smoke range must be <= {max_days} days")
    plan = service.plan(
        start_date=start,
        end_date=end,
        roots=_parse_csv(roots),
        schemas=_parse_csv(schemas),
        chunk_mode="month",
    )
    artifact = service.execute(
        command_name="smoke",
        plan=plan,
        max_cost_usd=max_cost_usd,
        execute=execute,
        skip_preflight=skip_preflight,
        force=force,
    )
    console.print(json.dumps(artifact, indent=2, default=str))


@databento_app.command("run")
def databento_run(
    start: date = typer.Option(...),
    end: date = typer.Option(...),
    roots: str | None = typer.Option(None),
    roots_from_config: bool = typer.Option(False),
    schemas: str | None = typer.Option(None),
    max_cost_usd: float = typer.Option(5.0),
    execute: bool = typer.Option(False),
    skip_preflight: bool = typer.Option(False),
    force: bool = typer.Option(False),
    repo_root: Path = typer.Option(Path("."), hidden=True),
) -> None:
    service = _build_service(repo_root, client=_make_databento_client(repo_root))
    roots_value = None if roots_from_config else _parse_csv(roots)
    plan = service.plan(
        start_date=start,
        end_date=end,
        roots=roots_value,
        schemas=_parse_csv(schemas),
    )
    artifact = service.execute(
        command_name="run",
        plan=plan,
        max_cost_usd=max_cost_usd,
        execute=execute,
        skip_preflight=skip_preflight,
        force=force,
    )
    console.print(json.dumps(artifact, indent=2, default=str))


@databento_app.command("normalize")
def databento_normalize(
    snapshot_id: str = typer.Option(...),
    repo_root: Path = typer.Option(Path("."), hidden=True),
) -> None:
    service = _build_service(repo_root)
    artifact = service.normalize_snapshot(snapshot_id=snapshot_id)
    console.print(json.dumps(artifact, indent=2, default=str))


@databento_app.command("qa")
def databento_qa(
    snapshot_id: str = typer.Option(...),
    repo_root: Path = typer.Option(Path("."), hidden=True),
) -> None:
    service = _build_service(repo_root)
    report = service.qa_snapshot(snapshot_id=snapshot_id)
    table = Table(title=f"WP4 QA {snapshot_id}")
    table.add_column("Severity")
    table.add_column("Code")
    table.add_column("Message")
    for issue in report.issues:
        table.add_row(issue.severity, issue.code, issue.message)
    console.print(table)
    console.print(json.dumps(report.to_dict(), indent=2, default=str))


@roll_engine_app.command("build")
def roll_engine_build(
    start: date = typer.Option(...),
    end: date = typer.Option(...),
    snapshot_id: str = typer.Option(...),
    roots: str | None = typer.Option(None),
    data_dir: Path = typer.Option(Path("data")),
    overwrite: bool = typer.Option(False),
    repo_root: Path = typer.Option(Path("."), hidden=True),
) -> None:
    service = _build_roll_service(repo_root, data_dir)
    artifact = service.build(
        snapshot_id=snapshot_id,
        start_date=start,
        end_date=end,
        roots=_parse_csv(roots),
        overwrite=overwrite,
    )
    console.print(json.dumps(artifact, indent=2, default=str))


@roll_engine_app.command("qa")
def roll_engine_qa(
    snapshot_id: str = typer.Option(...),
    data_dir: Path = typer.Option(Path("data")),
    repo_root: Path = typer.Option(Path("."), hidden=True),
) -> None:
    service = _build_roll_service(repo_root, data_dir)
    report = service.qa(snapshot_id=snapshot_id)
    table = Table(title=f"WP5 Roll QA {snapshot_id}")
    table.add_column("Severity")
    table.add_column("Code")
    table.add_column("Message")
    for issue in report.issues:
        table.add_row(issue.severity, issue.code, issue.message)
    console.print(table)
    console.print(json.dumps(report.to_dict(), indent=2, default=str))
    if report.has_errors:
        raise typer.Exit(code=1)


@continuous_app.command("build")
def continuous_build(
    snapshot_id: str = typer.Option(...),
    start: date = typer.Option(...),
    end: date = typer.Option(...),
    roots: str | None = typer.Option(None),
    series_id: str | None = typer.Option(None),
    data_dir: Path = typer.Option(Path("data")),
    output_dir: Path | None = typer.Option(None),
    artifacts_dir: Path | None = typer.Option(None),
    overwrite: bool = typer.Option(False),
    repo_root: Path = typer.Option(Path("."), hidden=True),
) -> None:
    service = _build_continuous_service(
        repo_root,
        data_dir,
        output_dir=output_dir,
        artifacts_dir=artifacts_dir,
    )
    artifact = service.build(
        snapshot_id=snapshot_id,
        start_date=start,
        end_date=end,
        roots=_parse_csv(roots),
        series_id=series_id,
        overwrite=overwrite,
    )
    console.print(json.dumps(artifact, indent=2, default=str))


@continuous_app.command("qa")
def continuous_qa(
    snapshot_id: str = typer.Option(...),
    series_id: str | None = typer.Option(None),
    data_dir: Path = typer.Option(Path("data")),
    output_dir: Path | None = typer.Option(None),
    artifacts_dir: Path | None = typer.Option(None),
    repo_root: Path = typer.Option(Path("."), hidden=True),
) -> None:
    service = _build_continuous_service(
        repo_root,
        data_dir,
        output_dir=output_dir,
        artifacts_dir=artifacts_dir,
    )
    report = service.qa(snapshot_id=snapshot_id, series_id=series_id)
    table = Table(title=f"WP6 Continuous QA {snapshot_id}")
    table.add_column("Severity")
    table.add_column("Code")
    table.add_column("Message")
    for issue in report.issues:
        table.add_row(issue.severity, issue.code, issue.message)
    console.print(table)
    console.print(json.dumps(report.to_dict(), indent=2, default=str))
    if report.has_errors:
        raise typer.Exit(code=1)


@features_app.command("build")
def features_build(
    snapshot_id: str = typer.Option(...),
    start: date = typer.Option(...),
    end: date = typer.Option(...),
    roots: str | None = typer.Option(None),
    series_id: str | None = typer.Option(None),
    feature_set_id: str | None = typer.Option(None),
    input_dir: Path | None = typer.Option(None),
    output_dir: Path | None = typer.Option(None),
    artifact_dir: Path | None = typer.Option(None),
    overwrite: bool = typer.Option(False),
    repo_root: Path = typer.Option(Path("."), hidden=True),
) -> None:
    service = _build_features_service(
        repo_root,
        input_dir=input_dir,
        output_dir=output_dir,
        artifacts_dir=artifact_dir,
    )
    artifact = service.build(
        snapshot_id=snapshot_id,
        start_date=start,
        end_date=end,
        roots=_parse_csv(roots),
        series_id=series_id,
        feature_set_id=feature_set_id,
        overwrite=overwrite,
    )
    console.print(json.dumps(artifact, indent=2, default=str))


@features_app.command("qa")
def features_qa(
    snapshot_id: str = typer.Option(...),
    feature_set_id: str | None = typer.Option(None),
    series_id: str | None = typer.Option(None),
    input_dir: Path | None = typer.Option(None),
    artifact_dir: Path | None = typer.Option(None),
    repo_root: Path = typer.Option(Path("."), hidden=True),
) -> None:
    service = _build_features_service(
        repo_root,
        output_dir=input_dir,
        artifacts_dir=artifact_dir,
    )
    report = service.qa(
        snapshot_id=snapshot_id,
        feature_set_id=feature_set_id,
        series_id=series_id,
    )
    table = Table(title=f"WP7 Features QA {snapshot_id}")
    table.add_column("Severity")
    table.add_column("Code")
    table.add_column("Message")
    for issue in report.issues:
        table.add_row(issue.severity, issue.code, issue.message)
    console.print(table)
    console.print(json.dumps(report.to_dict(), indent=2, default=str))
    if report.has_errors:
        raise typer.Exit(code=1)


@tsmom_app.command("build")
def signals_tsmom_build(
    snapshot_id: str = typer.Option(...),
    feature_set_id: str | None = typer.Option(None),
    start: date = typer.Option(...),
    end: date = typer.Option(...),
    roots: str | None = typer.Option(None),
    run_id: str = typer.Option(...),
    created_at_utc: str = typer.Option(...),
    features_path: Path | None = typer.Option(None),
    output_dir: Path | None = typer.Option(None),
    artifact_dir: Path | None = typer.Option(None),
    overwrite: bool = typer.Option(False),
    repo_root: Path = typer.Option(Path("."), hidden=True),
) -> None:
    service = _build_signals_service(
        repo_root,
        features_path=features_path,
        output_dir=output_dir,
        artifact_dir=artifact_dir,
    )
    artifact = service.build_tsmom(
        snapshot_id=snapshot_id,
        feature_set_id=feature_set_id,
        start_date=start,
        end_date=end,
        roots=_parse_csv(roots),
        run_id=run_id,
        created_at_utc=_parse_utc_datetime(created_at_utc),
        overwrite=overwrite,
    )
    console.print(json.dumps(artifact, indent=2, default=str))


@signals_app.command("qa")
def signals_qa(
    run_id: str = typer.Option(...),
    signals_path: Path | None = typer.Option(None),
    artifact_dir: Path | None = typer.Option(None),
    repo_root: Path = typer.Option(Path("."), hidden=True),
) -> None:
    service = _build_signals_service(
        repo_root,
        output_dir=signals_path,
        artifact_dir=artifact_dir,
    )
    report = service.qa(
        run_id=run_id,
        signals_path=signals_path,
        artifact_dir=artifact_dir,
    )
    table = Table(title=f"WP8 Signals QA {run_id}")
    table.add_column("Severity")
    table.add_column("Code")
    table.add_column("Message")
    for issue in report.issues:
        table.add_row(issue.severity, issue.code, issue.message)
    console.print(table)
    console.print(json.dumps(report.to_dict(), indent=2, default=str))
    if report.has_errors:
        raise typer.Exit(code=1)


@cpd_lstm_app.command("train")
def cpd_lstm_train(
    snapshot_id: str = typer.Option(...),
    train_start: date = typer.Option(...),
    train_end: date = typer.Option(...),
    val_start: date = typer.Option(...),
    val_end: date = typer.Option(...),
    model_id: str = typer.Option(...),
    training_run_id: str = typer.Option(...),
    roots: str | None = typer.Option(None),
    feature_set_id: str | None = typer.Option(None),
    series_id: str | None = typer.Option(None),
    features_path: Path | None = typer.Option(None),
    continuous_path: Path | None = typer.Option(None),
    output_dir: Path = typer.Option(...),
    artifact_dir: Path | None = typer.Option(None),
    max_epochs: int | None = typer.Option(None),
    min_epochs: int | None = typer.Option(None),
    device: str | None = typer.Option(None),
    repo_root: Path = typer.Option(Path("."), hidden=True),
) -> None:
    service = _build_cpd_lstm_service(
        repo_root,
        features_path=features_path,
        continuous_path=continuous_path,
        artifact_dir=artifact_dir,
    )
    artifact = service.train(
        snapshot_id=snapshot_id,
        feature_set_id=feature_set_id,
        series_id=series_id,
        roots=_parse_csv(roots),
        train_start=train_start,
        train_end=train_end,
        val_start=val_start,
        val_end=val_end,
        model_id=model_id,
        training_run_id=training_run_id,
        output_dir=output_dir,
        max_epochs=max_epochs,
        min_epochs=min_epochs,
        device=device,
    )
    console.print(json.dumps(artifact, indent=2, default=str))


@cpd_lstm_app.command("infer")
def cpd_lstm_infer(
    snapshot_id: str = typer.Option(...),
    model_dir: Path = typer.Option(...),
    model_id: str = typer.Option(...),
    start: date = typer.Option(...),
    end: date = typer.Option(...),
    run_id: str = typer.Option(...),
    created_at_utc: str = typer.Option(...),
    roots: str | None = typer.Option(None),
    feature_set_id: str | None = typer.Option(None),
    features_path: Path | None = typer.Option(None),
    output_dir: Path | None = typer.Option(None),
    artifact_dir: Path | None = typer.Option(None),
    overwrite: bool = typer.Option(False),
    device: str | None = typer.Option(None),
    repo_root: Path = typer.Option(Path("."), hidden=True),
) -> None:
    service = _build_cpd_lstm_service(
        repo_root,
        features_path=features_path,
        output_dir=output_dir,
        artifact_dir=artifact_dir,
    )
    artifact = service.infer(
        snapshot_id=snapshot_id,
        feature_set_id=feature_set_id,
        model_dir=model_dir,
        model_id=model_id,
        start_date=start,
        end_date=end,
        roots=_parse_csv(roots),
        run_id=run_id,
        created_at_utc=_parse_utc_datetime(created_at_utc),
        output_dir=output_dir,
        overwrite=overwrite,
        device=device,
    )
    console.print(json.dumps(artifact, indent=2, default=str))


@cpd_lstm_app.command("qa")
def cpd_lstm_qa(
    model_dir: Path = typer.Option(...),
    run_id: str = typer.Option(...),
    model_id: str | None = typer.Option(None),
    signals_path: Path | None = typer.Option(None),
    artifact_dir: Path | None = typer.Option(None),
    repo_root: Path = typer.Option(Path("."), hidden=True),
) -> None:
    service = _build_cpd_lstm_service(
        repo_root,
        output_dir=signals_path,
        artifact_dir=artifact_dir,
    )
    report = service.qa(
        model_dir=model_dir,
        signals_path=signals_path,
        run_id=run_id,
        model_id=model_id,
    )
    table = Table(title=f"WP9 CPD-LSTM QA {run_id}")
    table.add_column("Severity")
    table.add_column("Code")
    table.add_column("Message")
    for issue in report.issues:
        table.add_row(issue.severity, issue.code, issue.message)
    console.print(table)
    console.print(json.dumps(report.to_dict(), indent=2, default=str))
    if report.has_errors:
        raise typer.Exit(code=1)


@cpd_lstm_app.command("smoke")
def cpd_lstm_smoke(
    output_root: Path = typer.Option(Path("artifacts/wp9/smoke")),
    repo_root: Path = typer.Option(Path("."), hidden=True),
) -> None:
    service = _build_cpd_lstm_service(
        repo_root,
        artifact_dir=output_root / "artifacts" / "wp9",
        output_dir=output_root / "data" / "research" / "signals_daily",
    )
    artifact = service.smoke(output_root=output_root)
    console.print(json.dumps(artifact, indent=2, default=str))


@walkforward_app.command("plan")
def walkforward_plan(
    features_path: Path = typer.Option(...),
    continuous_path: Path = typer.Option(...),
    snapshot_id: str = typer.Option(...),
    feature_set_id: str = typer.Option("features_v1"),
    series_id: str = typer.Option("v1_back_ratio_settle"),
    roots: str = typer.Option(...),
    oos_start: date = typer.Option(...),
    oos_end: date = typer.Option(...),
    run_id: str = typer.Option(...),
    output_dir: Path = typer.Option(...),
    settings_path: Path = typer.Option(Path("config/settings.base.yml")),
    instruments_path: Path = typer.Option(Path("config/instruments.yml")),
    train_years: int | None = typer.Option(None),
    val_years: int | None = typer.Option(None),
    min_train_days: int | None = typer.Option(None),
    min_val_days: int | None = typer.Option(None),
    min_oos_days: int | None = typer.Option(None),
    repo_root: Path = typer.Option(Path("."), hidden=True),
) -> None:
    context = _build_walkforward_context(
        repo_root=repo_root,
        features_path=features_path,
        continuous_path=continuous_path,
        settings_path=settings_path,
        instruments_path=instruments_path,
        snapshot_id=snapshot_id,
        feature_set_id=feature_set_id,
        series_id=series_id,
        roots=roots,
        run_id=run_id,
        output_dir=output_dir,
    )
    features = read_parquet_dataset(
        context.features_path / f"feature_set_id={feature_set_id}" / f"snapshot_id={snapshot_id}"
    )
    if "year" in features.columns:
        features = features.drop(columns=["year"])
    if not features.empty:
        features["as_of_date"] = pd.to_datetime(features["as_of_date"]).dt.date
        features = features[
            (features["feature_set_id"].astype(str) == feature_set_id)
            & (features["series_id"].astype(str) == series_id)
            & (features["snapshot_id"].astype(str) == snapshot_id)
            & features["root"].astype(str).isin(set(context.roots))
        ]
    windows = plan_walkforward_windows(
        features_daily=features,
        run_id=run_id,
        roots=context.roots,
        oos_start=oos_start,
        oos_end=oos_end,
        config=context.app_config.walkforward,
        train_years=train_years,
        val_years=val_years,
        min_train_days=min_train_days,
        min_val_days=min_val_days,
        min_oos_days=min_oos_days,
    )
    context.output_dir.mkdir(parents=True, exist_ok=True)
    write_parquet_part(windows, context.output_dir / "walkforward_windows.parquet")
    artifact = {
        "run_id": run_id,
        "output_dir": context.output_dir.as_posix(),
        "fold_count": int(len(windows)),
        "planned_fold_count": int((windows["status"] == "planned").sum()),
    }
    (context.output_dir / "manifest.json").write_bytes(canonical_json_bytes(artifact))
    console.print(json.dumps(artifact, indent=2, default=str))


@walkforward_app.command("run")
def walkforward_run(
    features_path: Path = typer.Option(...),
    continuous_path: Path = typer.Option(...),
    snapshot_id: str = typer.Option(...),
    feature_set_id: str = typer.Option("features_v1"),
    series_id: str = typer.Option("v1_back_ratio_settle"),
    roots: str = typer.Option(...),
    oos_start: date = typer.Option(...),
    oos_end: date = typer.Option(...),
    run_id: str = typer.Option(...),
    output_dir: Path = typer.Option(...),
    settings_path: Path = typer.Option(Path("config/settings.base.yml")),
    instruments_path: Path = typer.Option(Path("config/instruments.yml")),
    device: str = typer.Option("auto"),
    overwrite: bool = typer.Option(False),
    train_years: int | None = typer.Option(None),
    val_years: int | None = typer.Option(None),
    min_train_days: int | None = typer.Option(None),
    min_val_days: int | None = typer.Option(None),
    min_oos_days: int | None = typer.Option(None),
    max_epochs: int | None = typer.Option(None),
    min_epochs: int | None = typer.Option(None),
    repo_root: Path = typer.Option(Path("."), hidden=True),
) -> None:
    context = _build_walkforward_context(
        repo_root=repo_root,
        features_path=features_path,
        continuous_path=continuous_path,
        settings_path=settings_path,
        instruments_path=instruments_path,
        snapshot_id=snapshot_id,
        feature_set_id=feature_set_id,
        series_id=series_id,
        roots=roots,
        run_id=run_id,
        output_dir=output_dir,
    )
    artifact = run_walkforward(
        context=context,
        oos_start=oos_start,
        oos_end=oos_end,
        device=device,
        overwrite=overwrite,
        train_years=train_years,
        val_years=val_years,
        min_train_days=min_train_days,
        min_val_days=min_val_days,
        min_oos_days=min_oos_days,
        max_epochs=max_epochs,
        min_epochs=min_epochs,
    )
    console.print(json.dumps(artifact, indent=2, default=str))


@walkforward_app.command("evaluate-signals")
def walkforward_evaluate_signals(
    signals_path: Path = typer.Option(...),
    features_path: Path = typer.Option(...),
    continuous_path: Path = typer.Option(...),
    snapshot_id: str = typer.Option(...),
    feature_set_id: str = typer.Option("features_v1"),
    series_id: str = typer.Option("v1_back_ratio_settle"),
    roots: str = typer.Option(...),
    run_id: str = typer.Option(...),
    output_dir: Path = typer.Option(...),
    settings_path: Path = typer.Option(Path("config/settings.base.yml")),
    instruments_path: Path = typer.Option(Path("config/instruments.yml")),
    repo_root: Path = typer.Option(Path("."), hidden=True),
) -> None:
    context = _build_walkforward_context(
        repo_root=repo_root,
        features_path=features_path,
        continuous_path=continuous_path,
        settings_path=settings_path,
        instruments_path=instruments_path,
        snapshot_id=snapshot_id,
        feature_set_id=feature_set_id,
        series_id=series_id,
        roots=roots,
        run_id=run_id,
        output_dir=output_dir,
    )
    signals = read_parquet_dataset(_resolve_repo_path(repo_root, signals_path))
    if "fold_id" not in signals.columns:
        signals = signals.assign(
            fold_id="eval_only",
            snapshot_id=snapshot_id,
            feature_set_id=feature_set_id,
            config_hash="eval_only",
        )
    artifact = evaluate_oos_signals(
        context=context,
        signals_daily=signals,
        output_dir=context.output_dir,
    )
    console.print(json.dumps(artifact, indent=2, default=str))


@walkforward_app.command("qa")
def walkforward_qa(
    run_dir: Path = typer.Option(...),
    repo_root: Path = typer.Option(Path("."), hidden=True),
) -> None:
    report = qa_walkforward_run(_resolve_repo_path(repo_root, run_dir))
    console.print(json.dumps(report, indent=2, default=str))
    if report["has_errors"]:
        raise typer.Exit(code=1)


@walkforward_app.command("report")
def walkforward_report(
    run_dir: Path = typer.Option(...),
    repo_root: Path = typer.Option(Path("."), hidden=True),
) -> None:
    resolved = _resolve_repo_path(repo_root, run_dir)
    windows = read_parquet_dataset(resolved / "walkforward_windows.parquet")
    fold_metrics = read_parquet_dataset(resolved / "fold_metrics.parquet")
    aggregate_metrics = read_parquet_dataset(resolved / "aggregate_metrics.parquet")
    reversal_metrics = read_parquet_dataset(resolved / "reversal_bucket_metrics.parquet")
    gates_path = resolved / "gates.json"
    gates = json.loads(gates_path.read_text(encoding="utf-8")) if gates_path.exists() else {}
    manifest_path = resolved / "manifest.json"
    run_id = (
        json.loads(manifest_path.read_text(encoding="utf-8")).get("run_id")
        if manifest_path.exists()
        else resolved.name
    )
    json_path, md_path = write_walkforward_report(
        run_dir=resolved,
        run_id=str(run_id),
        windows=windows,
        fold_metrics=fold_metrics,
        aggregate_metrics=aggregate_metrics,
        reversal_bucket_metrics=reversal_metrics,
        gates=gates,
        warnings=[],
    )
    console.print(json.dumps({"json": json_path.as_posix(), "markdown": md_path.as_posix()}))


@model_rc_app.command("evaluate")
def model_rc_evaluate(
    walkforward_dir: Path = typer.Option(...),
    candidate_model_dir: Path = typer.Option(...),
    gates_path: Path = typer.Option(Path("config/model_release_gates.yml")),
    feature_set_id: str = typer.Option("features_v1"),
    strategy_id: str = typer.Option("cpd_lstm"),
    baseline_strategy_id: str = typer.Option("tsmom"),
    output_file: Path | None = typer.Option(None),
    repo_root: Path = typer.Option(Path("."), hidden=True),
) -> None:
    payload = evaluate_model_release(
        walkforward_dir=_resolve_repo_path(repo_root, walkforward_dir),
        candidate_model_dir=_resolve_repo_path(repo_root, candidate_model_dir),
        gates_path=_resolve_repo_path(repo_root, gates_path),
        feature_set_id=feature_set_id,
        strategy_id=strategy_id,
        baseline_strategy_id=baseline_strategy_id,
    )
    if output_file is not None:
        target = _resolve_repo_path(repo_root, output_file)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(canonical_json_bytes(payload))
    console.print(json.dumps(payload, indent=2, default=str))


@model_rc_app.command("package")
def model_rc_package(
    walkforward_dir: Path = typer.Option(...),
    candidate_model_dir: Path = typer.Option(...),
    release_id: str | None = typer.Option(None),
    output_dir: Path = typer.Option(Path("artifacts/releases/model_rc")),
    gates_path: Path = typer.Option(Path("config/model_release_gates.yml")),
    settings_path: Path = typer.Option(Path("config/settings.base.yml")),
    data_schema_path: Path = typer.Option(Path("config/data_schema.yml")),
    feature_set_id: str = typer.Option("features_v1"),
    strategy_id: str = typer.Option("cpd_lstm"),
    baseline_strategy_id: str = typer.Option("tsmom"),
    created_at_utc: str | None = typer.Option(None),
    copy_artifacts: bool = typer.Option(False),
    write_alias: bool = typer.Option(False),
    repo_root: Path = typer.Option(Path("."), hidden=True),
) -> None:
    artifact = package_model_release(
        walkforward_dir=_resolve_repo_path(repo_root, walkforward_dir),
        candidate_model_dir=_resolve_repo_path(repo_root, candidate_model_dir),
        gates_path=_resolve_repo_path(repo_root, gates_path),
        output_dir=_resolve_repo_path(repo_root, output_dir),
        release_id=release_id,
        created_at_utc=_parse_utc_datetime(created_at_utc) if created_at_utc else None,
        feature_set_id=feature_set_id,
        strategy_id=strategy_id,
        baseline_strategy_id=baseline_strategy_id,
        settings_path=_resolve_repo_path(repo_root, settings_path),
        data_schema_path=_resolve_repo_path(repo_root, data_schema_path),
        copy_artifacts=copy_artifacts,
        write_alias=write_alias,
    )
    console.print(json.dumps(artifact, indent=2, default=str))


@model_rc_app.command("qa")
def model_rc_qa(
    release_dir: Path = typer.Option(...),
    repo_root: Path = typer.Option(Path("."), hidden=True),
) -> None:
    report = qa_model_release(_resolve_repo_path(repo_root, release_dir))
    console.print(json.dumps(report, indent=2, default=str))
    if report["has_errors"]:
        raise typer.Exit(code=1)


@broker_boundary_app.command("build-intents")
def broker_boundary_build_intents(
    targets_path: Path = typer.Option(...),
    positions_path: Path = typer.Option(...),
    contract_master_path: Path = typer.Option(...),
    monitoring_path: Path | None = typer.Option(None),
    output_dir: Path | None = typer.Option(None),
    run_id: str | None = typer.Option(None),
    strategy_id: str | None = typer.Option(None),
    execution_mode: str | None = typer.Option(None),
    as_of_date: date | None = typer.Option(None),
    execution_date: date | None = typer.Option(None),
    position_snapshot_id: str | None = typer.Option(None),
    fixed_created_at_utc: str | None = typer.Option(None),
    strict: bool = typer.Option(True),
    repo_root: Path = typer.Option(Path("."), hidden=True),
) -> None:
    service = _build_execution_boundary_service(repo_root)
    artifact = service.build_intents(
        targets_path=_resolve_repo_path(repo_root, targets_path),
        positions_path=_resolve_repo_path(repo_root, positions_path),
        contract_master_path=_resolve_repo_path(repo_root, contract_master_path),
        monitoring_path=(
            _resolve_repo_path(repo_root, monitoring_path)
            if monitoring_path is not None
            else None
        ),
        output_dir=_resolve_repo_path(repo_root, output_dir) if output_dir is not None else None,
        run_id=run_id,
        strategy_id=strategy_id,
        execution_mode=execution_mode,
        as_of_date=as_of_date,
        execution_date=execution_date,
        position_snapshot_id=position_snapshot_id,
        strict=strict,
        created_at_utc=_parse_utc_datetime(fixed_created_at_utc) if fixed_created_at_utc else None,
    )
    console.print(json.dumps(artifact, indent=2, default=str))


@broker_boundary_app.command("dry-run")
def broker_boundary_dry_run(
    planned_intents_path: Path = typer.Option(...),
    contract_master_path: Path = typer.Option(...),
    output_dir: Path | None = typer.Option(None),
    fixed_created_at_utc: str | None = typer.Option(None),
    repo_root: Path = typer.Option(Path("."), hidden=True),
) -> None:
    service = _build_execution_boundary_service(repo_root)
    artifact = service.dry_run(
        planned_intents_path=_resolve_repo_path(repo_root, planned_intents_path),
        contract_master_path=_resolve_repo_path(repo_root, contract_master_path),
        output_dir=_resolve_repo_path(repo_root, output_dir) if output_dir is not None else None,
        created_at_utc=_parse_utc_datetime(fixed_created_at_utc) if fixed_created_at_utc else None,
    )
    console.print(json.dumps(artifact, indent=2, default=str))


@broker_boundary_app.command("qa")
def broker_boundary_qa(
    targets_path: Path = typer.Option(...),
    positions_path: Path = typer.Option(...),
    final_order_intents_path: Path = typer.Option(...),
    monitoring_path: Path | None = typer.Option(None),
    output_dir: Path | None = typer.Option(None),
    fixed_created_at_utc: str | None = typer.Option(None),
    repo_root: Path = typer.Option(Path("."), hidden=True),
) -> None:
    service = _build_execution_boundary_service(repo_root)
    report = service.qa(
        targets_path=_resolve_repo_path(repo_root, targets_path),
        positions_path=_resolve_repo_path(repo_root, positions_path),
        final_order_intents_path=_resolve_repo_path(repo_root, final_order_intents_path),
        monitoring_path=(
            _resolve_repo_path(repo_root, monitoring_path)
            if monitoring_path is not None
            else None
        ),
        output_dir=_resolve_repo_path(repo_root, output_dir) if output_dir is not None else None,
        created_at_utc=_parse_utc_datetime(fixed_created_at_utc) if fixed_created_at_utc else None,
    )
    console.print(json.dumps(report.to_dict(), indent=2, default=str))
    if report.has_errors:
        raise typer.Exit(code=1)


@broker_ibkr_app.command("sync-state")
def broker_ibkr_sync_state(
    run_id: str = typer.Option(...),
    as_of: date = typer.Option(...),
    mode: str = typer.Option(...),
    output_dir: Path | None = typer.Option(None),
    account_id: str | None = typer.Option(None),
    contract_master_path: Path | None = typer.Option(None),
    fixed_created_at_utc: str | None = typer.Option(None),
    repo_root: Path = typer.Option(Path("."), hidden=True),
) -> None:
    service = _build_ibkr_broker_service(repo_root)
    artifact = service.sync_state(
        run_id=run_id,
        broker_mode=mode,  # type: ignore[arg-type]
        as_of_date=as_of,
        output_dir=_resolve_repo_path(repo_root, output_dir) if output_dir is not None else None,
        account_id=account_id,
        contract_master_path=(
            _resolve_repo_path(repo_root, contract_master_path)
            if contract_master_path is not None
            else None
        ),
        created_at_utc=_parse_utc_datetime(fixed_created_at_utc) if fixed_created_at_utc else None,
    )
    console.print(json.dumps(artifact, indent=2, default=str))


@broker_ibkr_app.command("submit-intents")
def broker_ibkr_submit_intents(
    order_intents_path: Path = typer.Option(...),
    contract_master_path: Path = typer.Option(...),
    contracts_daily_path: Path = typer.Option(...),
    mode: str = typer.Option(...),
    monitoring_path: Path | None = typer.Option(None),
    output_dir: Path | None = typer.Option(None),
    run_id: str | None = typer.Option(None),
    as_of: date | None = typer.Option(None),
    account_id: str | None = typer.Option(None),
    fixed_created_at_utc: str | None = typer.Option(None),
    repo_root: Path = typer.Option(Path("."), hidden=True),
) -> None:
    service = _build_ibkr_broker_service(repo_root)
    artifact = service.submit_intents(
        order_intents_path=_resolve_repo_path(repo_root, order_intents_path),
        contract_master_path=_resolve_repo_path(repo_root, contract_master_path),
        contracts_daily_path=_resolve_repo_path(repo_root, contracts_daily_path),
        monitoring_path=(
            _resolve_repo_path(repo_root, monitoring_path)
            if monitoring_path is not None
            else None
        ),
        broker_mode=mode,  # type: ignore[arg-type]
        output_dir=_resolve_repo_path(repo_root, output_dir) if output_dir is not None else None,
        run_id=run_id,
        as_of_date=as_of,
        account_id=account_id,
        created_at_utc=_parse_utc_datetime(fixed_created_at_utc) if fixed_created_at_utc else None,
    )
    console.print(json.dumps(artifact, indent=2, default=str))


@broker_ibkr_app.command("qa")
def broker_ibkr_qa(
    workspace: Path = typer.Option(...),
    repo_root: Path = typer.Option(Path("."), hidden=True),
) -> None:
    service = _build_ibkr_broker_service(repo_root)
    report = service.qa(workspace=_resolve_repo_path(repo_root, workspace))
    console.print(json.dumps(report, indent=2, default=str))
    if report["has_errors"]:
        raise typer.Exit(code=1)


if __name__ == "__main__":
    app()
