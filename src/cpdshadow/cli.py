from __future__ import annotations

import json
import os
from datetime import UTC, date, datetime
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from cpdshadow.config import load_data_schema_yaml, load_databento_ingest_yaml, load_yaml
from cpdshadow.ids import canonical_json_bytes
from cpdshadow.ingest.continuous_builder import ContinuousBuilderService
from cpdshadow.ingest.databento_raw import DatabentoIngestService
from cpdshadow.ingest.features_builder import FeaturesBuilderService
from cpdshadow.ingest.roll_engine import RollEngineService
from cpdshadow.ingest.signals_builder import SignalsBuilderService
from cpdshadow.instruments import load_instrument_master
from cpdshadow.vendor.databento_client import HistoricalDatabentoClient

app = typer.Typer(no_args_is_help=True)
ingest_app = typer.Typer(no_args_is_help=True)
databento_app = typer.Typer(no_args_is_help=True)
roll_engine_app = typer.Typer(no_args_is_help=True)
continuous_app = typer.Typer(no_args_is_help=True)
features_app = typer.Typer(no_args_is_help=True)
signals_app = typer.Typer(no_args_is_help=True)
tsmom_app = typer.Typer(no_args_is_help=True)
console = Console()

app.add_typer(ingest_app, name="ingest")
ingest_app.add_typer(databento_app, name="databento")
app.add_typer(roll_engine_app, name="roll-engine")
app.add_typer(continuous_app, name="continuous")
app.add_typer(features_app, name="features")
app.add_typer(signals_app, name="signals")
signals_app.add_typer(tsmom_app, name="tsmom")


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


if __name__ == "__main__":
    app()
