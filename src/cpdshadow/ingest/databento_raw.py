from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
import json
import os
import shutil
import subprocess
from typing import Any

import pandas as pd

from cpdshadow.config import DataSchemaConfig, DatabentoIngestConfig
from cpdshadow.ids import (
    config_hash as compute_config_hash,
    file_sha256,
    make_file_id,
    make_run_id,
    make_snapshot_id,
    manifest_hash as compute_manifest_hash,
    request_params_hash as compute_request_hash,
    stable_sha256_hex,
    utc_now,
)
from cpdshadow.ingest.normalize_databento import normalize_contract_master, normalize_contracts_daily
from cpdshadow.ingest.quality import QualityReport, run_quality_checks
from cpdshadow.instruments import InstrumentMaster
from cpdshadow.storage.parquet_io import atomic_replace_dir, make_staging_dir, read_parquet_dataset, write_parquet_dataset
from cpdshadow.storage.registry import (
    FileRegistryRow,
    RunRegistryRow,
    SnapshotRegistryRow,
    append_file_registry_row,
    append_run_registry_row,
    append_snapshot_registry_row,
    load_file_registry,
)
from cpdshadow.vendor.databento_client import DatabentoClientProtocol, HistoricalRequest, RequestEstimate


@dataclass(frozen=True)
class PlannedRequest:
    request_id: str
    request_params_hash: str
    dataset: str
    schema: str
    symbols: tuple[str, ...]
    start: str
    end: str
    stype_in: str
    stype_out: str
    year: int
    output_path: str


@dataclass(frozen=True)
class IngestPlan:
    dataset: str
    start: str
    end: str
    roots: tuple[str, ...]
    schemas: tuple[str, ...]
    config_hash: str
    request_plan_hash: str
    requests: tuple[PlannedRequest, ...]
    chunk_mode: str


class DatabentoIngestService:
    def __init__(
        self,
        *,
        repo_root: str | Path,
        ingest_config: DatabentoIngestConfig,
        data_schema: DataSchemaConfig,
        instrument_master: InstrumentMaster,
        client: DatabentoClientProtocol | None = None,
    ) -> None:
        self.repo_root = Path(repo_root)
        self.ingest_config = ingest_config
        self.data_schema = data_schema
        self.instrument_master = instrument_master
        self.client = client
        self.meta_root = self.repo_root / "data" / "meta"
        self.run_registry_root = self.meta_root / "run_registry"
        self.snapshot_registry_root = self.meta_root / "data_snapshot_registry"
        self.file_registry_root = self.meta_root / "data_file_registry"
        self.raw_root = self.repo_root / self.ingest_config.raw_storage.root
        self.contract_master_root = self.repo_root / self.ingest_config.curated_storage.contract_master
        self.contracts_daily_root = self.repo_root / self.ingest_config.curated_storage.contracts_daily
        self.artifact_root = self.repo_root / "artifacts" / "wp4"

    def plan(
        self,
        *,
        start_date: date,
        end_date: date,
        roots: list[str] | None,
        schemas: list[str] | None,
        chunk_mode: str = "auto",
    ) -> IngestPlan:
        resolved_roots = tuple(sorted(roots or [instrument.root for instrument in self.instrument_master.instruments]))
        resolved_schemas = tuple(sorted(schemas or [
            name for name, cfg in self.ingest_config.requests.schemas.items() if cfg.enabled
        ]))
        config_hash = compute_config_hash([
            self.repo_root / "config" / "instruments.yml",
            self.repo_root / "config" / "databento.ingest.yml",
            self.repo_root / "config" / "data_schema.yml",
        ])
        requests: list[PlannedRequest] = []
        actual_chunk_mode = self._resolve_chunk_mode(start_date, end_date, chunk_mode)
        for schema in resolved_schemas:
            for chunk_start, chunk_end in _chunk_date_range(start_date, end_date, actual_chunk_mode):
                for symbols in _batched(parent_symbols_from_roots(list(resolved_roots)), self.ingest_config.client.max_symbols_per_request):
                    payload = {
                        "dataset": self.ingest_config.client.dataset,
                        "schema": schema,
                        "stype_in": self.ingest_config.client.default_stype_in,
                        "stype_out": self.ingest_config.client.default_stype_out,
                        "symbols": tuple(sorted(symbols)),
                        "start": chunk_start.isoformat(),
                        "end": chunk_end.isoformat(),
                        "schema_version": self.data_schema.version,
                        "config_hash": config_hash,
                    }
                    request_id = stable_sha256_hex("|".join([
                        payload["dataset"],
                        payload["schema"],
                        payload["stype_in"],
                        payload["stype_out"],
                        ",".join(payload["symbols"]),
                        payload["start"],
                        payload["end"],
                        payload["schema_version"],
                        payload["config_hash"],
                    ]))
                    output_path = self.raw_root / f"dataset={payload['dataset']}" / f"schema={schema}" / f"year={chunk_start.year}" / f"{request_id}.parquet"
                    requests.append(PlannedRequest(
                        request_id=request_id,
                        request_params_hash=compute_request_hash(payload),
                        dataset=payload["dataset"],
                        schema=schema,
                        symbols=tuple(symbols),
                        start=payload["start"],
                        end=payload["end"],
                        stype_in=payload["stype_in"],
                        stype_out=payload["stype_out"],
                        year=chunk_start.year,
                        output_path=self._relpath(output_path),
                    ))
        plan_hash = stable_sha256_hex([asdict(request) for request in requests])
        return IngestPlan(
            dataset=self.ingest_config.client.dataset,
            start=start_date.isoformat(),
            end=end_date.isoformat(),
            roots=resolved_roots,
            schemas=resolved_schemas,
            config_hash=config_hash,
            request_plan_hash=plan_hash,
            requests=tuple(requests),
            chunk_mode=actual_chunk_mode,
        )

    def execute(
        self,
        *,
        command_name: str,
        plan: IngestPlan,
        max_cost_usd: float,
        execute: bool,
        skip_preflight: bool,
        force: bool,
    ) -> dict[str, object]:
        if not execute:
            raise ValueError("execute=True is required for live ingest commands")
        if self.client is None:
            raise ValueError("a Databento client is required for execute")
        key_env = self.ingest_config.client.key_env
        if not os.getenv(key_env):
            raise ValueError(f"{key_env} is required for execute")

        run_id = make_run_id()
        snapshot_id = make_snapshot_id(run_id)
        started_at = utc_now()
        notes: list[str] = []
        if skip_preflight:
            notes.append("skip_preflight=true")

        artifact_path = self.artifact_root / f"{command_name}_{run_id}.json"
        self.artifact_root.mkdir(parents=True, exist_ok=True)
        run_row = RunRegistryRow(
            run_id=run_id,
            run_type="ingest",
            command_name=command_name,
            status="started",
            started_at_utc=started_at,
            finished_at_utc=None,
            data_snapshot_id=snapshot_id,
            config_hash=plan.config_hash,
            git_commit=_git_commit(self.repo_root),
            notes=notes,
            artifact_path=self._relpath(artifact_path),
        )
        append_run_registry_row(self.run_registry_root, run_row)

        try:
            estimates: list[dict[str, object]] = []
            if not skip_preflight:
                total_cost = 0.0
                for request in plan.requests:
                    estimate = self.client.estimate(_to_historical_request(request))
                    if estimate is None:
                        raise ValueError("preflight estimate is unavailable; rerun with --skip-preflight")
                    estimates.append(_estimate_to_dict(request.request_id, estimate))
                    total_cost += float(estimate.cost_usd or 0.0)
                if total_cost > max_cost_usd:
                    raise ValueError(f"preflight cost {total_cost:.4f} exceeds max_cost_usd={max_cost_usd:.4f}")

            file_rows: list[FileRegistryRow] = []
            request_results: list[dict[str, object]] = []
            for request in plan.requests:
                request_result = self._materialize_request(
                    run_id=run_id,
                    snapshot_id=snapshot_id,
                    request=request,
                    force=force,
                )
                file_rows.append(request_result["file_row"])
                request_results.append(request_result["artifact"])

            snapshot_row = SnapshotRegistryRow(
                snapshot_id=snapshot_id,
                run_id=run_id,
                vendor=self.ingest_config.vendor,
                dataset=plan.dataset,
                start_date=date.fromisoformat(plan.start),
                end_date=date.fromisoformat(plan.end),
                requested_roots=list(plan.roots),
                requested_schemas=list(plan.schemas),
                request_plan_hash=plan.request_plan_hash,
                manifest_hash=compute_manifest_hash({
                    "plan": asdict(plan),
                    "files": [asdict(row) for row in file_rows],
                }),
                created_at_utc=utc_now(),
            )
            append_snapshot_registry_row(self.snapshot_registry_root, snapshot_row)

            normalized = self.normalize_snapshot(snapshot_id=snapshot_id)
            qa_report = self.qa_snapshot(snapshot_id=snapshot_id)
            finished_at = utc_now()
            append_run_registry_row(self.run_registry_root, RunRegistryRow(
                run_id=run_id,
                run_type="ingest",
                command_name=command_name,
                status="succeeded",
                started_at_utc=started_at,
                finished_at_utc=finished_at,
                data_snapshot_id=snapshot_id,
                config_hash=plan.config_hash,
                git_commit=_git_commit(self.repo_root),
                notes=notes,
                artifact_path=self._relpath(artifact_path),
            ))

            artifact = {
                "run_id": run_id,
                "snapshot_id": snapshot_id,
                "command_name": command_name,
                "started_at_utc": started_at.isoformat(),
                "finished_at_utc": finished_at.isoformat(),
                "plan": asdict(plan),
                "preflight": estimates,
                "request_results": request_results,
                "normalize_artifact": normalized,
                "qa_artifact": qa_report.to_dict(),
            }
            artifact_path.write_bytes(json.dumps(artifact, indent=2, sort_keys=True, default=str).encode("utf-8"))
            return artifact
        except Exception as exc:
            finished_at = utc_now()
            append_run_registry_row(self.run_registry_root, RunRegistryRow(
                run_id=run_id,
                run_type="ingest",
                command_name=command_name,
                status="failed",
                started_at_utc=started_at,
                finished_at_utc=finished_at,
                data_snapshot_id=snapshot_id,
                config_hash=plan.config_hash,
                git_commit=_git_commit(self.repo_root),
                notes=notes + [f"error={type(exc).__name__}"],
                artifact_path=self._relpath(artifact_path),
            ))
            raise

    def normalize_snapshot(self, *, snapshot_id: str) -> dict[str, object]:
        definition_df = self._load_raw_schema(snapshot_id, "definition")
        statistics_df = self._load_raw_schema(snapshot_id, "statistics")
        ohlcv_df = self._load_raw_schema(snapshot_id, "ohlcv-1d")
        contract_master_df = normalize_contract_master(
            definition_df,
            self.instrument_master,
            dataset=self.ingest_config.client.dataset,
        )
        contracts_daily_df = normalize_contracts_daily(
            statistics_df,
            ohlcv_df,
            contract_master_df,
            self.instrument_master,
            dataset=self.ingest_config.client.dataset,
            snapshot_id=snapshot_id,
        )

        contract_master_path = self._write_curated_snapshot(
            df=contract_master_df,
            final_root=self.contract_master_root / f"snapshot_id={snapshot_id}",
            partition_cols=None,
        )
        contracts_daily_out = contracts_daily_df.assign(
            trade_year=pd.to_datetime(contracts_daily_df["trade_date"]).dt.year
            if not contracts_daily_df.empty
            else pd.Series(dtype="int64")
        )
        contracts_daily_path = self._write_curated_snapshot(
            df=contracts_daily_out,
            final_root=self.contracts_daily_root / f"snapshot_id={snapshot_id}",
            partition_cols=["trade_year"] if not contracts_daily_out.empty else None,
        )

        for logical_table, source_schema, path, df in [
            ("contract_master", "definition", contract_master_path, contract_master_df),
            ("contracts_daily", "statistics+ohlcv-1d", contracts_daily_path, contracts_daily_df),
        ]:
            file_row = FileRegistryRow(
                file_id=make_file_id(snapshot_id, logical_table, path),
                snapshot_id=snapshot_id,
                run_id=f"normalize_{snapshot_id}",
                logical_table=logical_table,
                source_schema=source_schema,
                path=self._relpath(path),
                content_sha256=_directory_manifest_hash(Path(path)),
                row_count=int(len(df)),
                min_trade_date=_min_trade_date(df),
                max_trade_date=_max_trade_date(df),
                request_params_hash=stable_sha256_hex({"snapshot_id": snapshot_id, "logical_table": logical_table}),
                cached=False,
                created_at_utc=utc_now(),
            )
            append_file_registry_row(self.file_registry_root, file_row)

        artifact = {
            "snapshot_id": snapshot_id,
            "contract_master_rows": int(len(contract_master_df)),
            "contracts_daily_rows": int(len(contracts_daily_df)),
            "contract_master_path": self._relpath(contract_master_path),
            "contracts_daily_path": self._relpath(contracts_daily_path),
        }
        artifact_path = self.artifact_root / f"normalize_{snapshot_id}.json"
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        artifact_path.write_bytes(json.dumps(artifact, indent=2, sort_keys=True, default=str).encode("utf-8"))
        return artifact

    def qa_snapshot(self, *, snapshot_id: str) -> QualityReport:
        file_registry_df = load_file_registry(self.file_registry_root)
        snapshot_files = file_registry_df[file_registry_df["snapshot_id"] == snapshot_id] if not file_registry_df.empty else pd.DataFrame()
        contract_master_df = read_parquet_dataset(self.contract_master_root / f"snapshot_id={snapshot_id}")
        if "snapshot_id" in contract_master_df.columns:
            contract_master_df = contract_master_df.drop(columns=["snapshot_id"])
        contracts_daily_df = read_parquet_dataset(self.contracts_daily_root / f"snapshot_id={snapshot_id}")
        if "trade_year" in contracts_daily_df.columns:
            contracts_daily_df = contracts_daily_df.drop(columns=["trade_year"])
        report = run_quality_checks(
            snapshot_id=snapshot_id,
            instrument_master=self.instrument_master,
            file_registry_df=snapshot_files,
            contract_master_df=contract_master_df,
            contracts_daily_df=contracts_daily_df,
            repo_root=self.repo_root,
        )
        artifact_path = self.artifact_root / f"qa_{snapshot_id}.json"
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        artifact_path.write_bytes(json.dumps(report.to_dict(), indent=2, sort_keys=True, default=str).encode("utf-8"))
        return report

    def _materialize_request(
        self,
        *,
        run_id: str,
        snapshot_id: str,
        request: PlannedRequest,
        force: bool,
    ) -> dict[str, object]:
        file_registry_df = load_file_registry(self.file_registry_root)
        same_request = file_registry_df[file_registry_df["request_params_hash"] == request.request_params_hash] if not file_registry_df.empty else pd.DataFrame()
        if not same_request.empty and not force:
            existing = same_request.sort_values("created_at_utc").iloc[-1]
            file_row = FileRegistryRow(
                file_id=make_file_id(snapshot_id, f"raw_{request.schema}", existing["path"]),
                snapshot_id=snapshot_id,
                run_id=run_id,
                logical_table=f"raw_{request.schema}",
                source_schema=request.schema,
                path=str(existing["path"]),
                content_sha256=str(existing["content_sha256"]),
                row_count=int(existing["row_count"]),
                min_trade_date=_coerce_date(existing.get("min_trade_date")),
                max_trade_date=_coerce_date(existing.get("max_trade_date")),
                request_params_hash=request.request_params_hash,
                cached=True,
                created_at_utc=utc_now(),
            )
            append_file_registry_row(self.file_registry_root, file_row)
            return {
                "file_row": file_row,
                "artifact": {
                    "request_id": request.request_id,
                    "cached": True,
                    "path": str(existing["path"]),
                },
            }

        store = self.client.get_range(_to_historical_request(request))
        temp_path = Path(self.repo_root / request.output_path).with_suffix(".tmp.parquet")
        temp_path.parent.mkdir(parents=True, exist_ok=True)
        store.to_parquet(
            temp_path,
            price_type=self.ingest_config.client.price_type,
            pretty_ts=self.ingest_config.client.pretty_ts,
            map_symbols=self.ingest_config.client.map_symbols,
            schema=request.schema,
        )
        temp_df = pd.read_parquet(temp_path)
        content_sha = file_sha256(temp_path)
        same_content = same_request[same_request["content_sha256"] == content_sha] if not same_request.empty else pd.DataFrame()
        warning: str | None = None
        if not same_content.empty:
            existing_path = Path(str(same_content.sort_values("created_at_utc").iloc[-1]["path"]))
            temp_path.unlink(missing_ok=True)
            final_path = existing_path
            cached = True
        else:
            final_path = self.repo_root / request.output_path
            cached = False
            if final_path.exists() or not same_request.empty:
                final_path = final_path.with_name(f"{final_path.stem}__{content_sha[:12]}{final_path.suffix}")
                warning = "vendor_changed_same_request"
            shutil.move(temp_path, final_path)
        file_row = FileRegistryRow(
            file_id=make_file_id(snapshot_id, f"raw_{request.schema}", final_path),
            snapshot_id=snapshot_id,
            run_id=run_id,
            logical_table=f"raw_{request.schema}",
            source_schema=request.schema,
            path=self._relpath(final_path),
            content_sha256=content_sha if not cached else str(same_content.iloc[-1]["content_sha256"]) if not same_content.empty else content_sha,
            row_count=int(len(temp_df)),
            min_trade_date=_extract_min_trade_date(temp_df, request.schema),
            max_trade_date=_extract_max_trade_date(temp_df, request.schema),
            request_params_hash=request.request_params_hash,
            cached=cached,
            created_at_utc=utc_now(),
        )
        append_file_registry_row(self.file_registry_root, file_row)
        return {
            "file_row": file_row,
            "artifact": {
                "request_id": request.request_id,
                "cached": cached,
                "path": self._relpath(final_path),
                "warning": warning,
                "row_count": int(len(temp_df)),
            },
        }

    def _load_raw_schema(self, snapshot_id: str, schema: str) -> pd.DataFrame:
        file_registry_df = load_file_registry(self.file_registry_root)
        if file_registry_df.empty:
            return pd.DataFrame()
        rows = file_registry_df[
            (file_registry_df["snapshot_id"] == snapshot_id)
            & (file_registry_df["source_schema"] == schema)
            & (file_registry_df["logical_table"].astype(str).str.startswith("raw_"))
        ]
        if rows.empty:
            return pd.DataFrame()
        paths = [self.repo_root / Path(str(path)) for path in rows["path"].tolist()]
        return pd.concat([pd.read_parquet(path) for path in paths], ignore_index=True) if paths else pd.DataFrame()

    def _write_curated_snapshot(
        self,
        *,
        df: pd.DataFrame,
        final_root: Path,
        partition_cols: list[str] | None,
    ) -> Path:
        staging_dir = make_staging_dir(final_root)
        write_parquet_dataset(
            df,
            staging_dir,
            compression=self.ingest_config.raw_storage.compression,
            partition_cols=partition_cols if not df.empty else None,
        )
        if not df.empty:
            dedupe_cols = ["trade_date", "root", "raw_symbol"] if "trade_date" in df.columns else ["dataset", "raw_symbol", "valid_from_utc"]
            if df.duplicated(subset=dedupe_cols).any():
                raise ValueError(f"duplicate rows detected for {final_root.name}")
        return atomic_replace_dir(staging_dir, final_root)

    def _resolve_chunk_mode(self, start_date: date, end_date: date, chunk_mode: str) -> str:
        if chunk_mode != "auto":
            return chunk_mode
        if (end_date - start_date).days > 366:
            return self.ingest_config.requests.chunking.bootstrap
        return self.ingest_config.requests.chunking.incremental

    def _relpath(self, path: str | Path) -> str:
        target = Path(path)
        if not target.is_absolute():
            return target.as_posix()
        return target.relative_to(self.repo_root).as_posix()


def parent_symbols_from_roots(roots: list[str]) -> list[str]:
    return [f"{root}.FUT" for root in sorted(set(roots))]


def _batched(values: list[str], size: int) -> list[list[str]]:
    return [values[idx:idx + size] for idx in range(0, len(values), size)]


def _chunk_date_range(start_date: date, end_date: date, chunk_mode: str) -> list[tuple[date, date]]:
    if chunk_mode == "year":
        chunks = []
        current = start_date
        while current < end_date:
            next_date = min(date(current.year + 1, 1, 1), end_date)
            chunks.append((current, next_date))
            current = next_date
        return chunks
    if chunk_mode == "month":
        chunks = []
        current = start_date
        while current < end_date:
            if current.month == 12:
                next_date = date(current.year + 1, 1, 1)
            else:
                next_date = date(current.year, current.month + 1, 1)
            next_date = min(next_date, end_date)
            chunks.append((current, next_date))
            current = next_date
        return chunks
    return [(start_date, end_date)]


def _to_historical_request(request: PlannedRequest) -> HistoricalRequest:
    return HistoricalRequest(
        dataset=request.dataset,
        symbols=request.symbols,
        schema=request.schema,
        start=request.start,
        end=request.end,
        stype_in=request.stype_in,
        stype_out=request.stype_out,
    )


def _estimate_to_dict(request_id: str, estimate: RequestEstimate) -> dict[str, object]:
    return {
        "request_id": request_id,
        "cost_usd": estimate.cost_usd,
        "billable_size_bytes": estimate.billable_size_bytes,
    }


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


def _extract_min_trade_date(df: pd.DataFrame, schema: str) -> date | None:
    series = _trade_date_series(df, schema)
    if series.empty:
        return None
    return pd.to_datetime(series).dt.date.min()


def _extract_max_trade_date(df: pd.DataFrame, schema: str) -> date | None:
    series = _trade_date_series(df, schema)
    if series.empty:
        return None
    return pd.to_datetime(series).dt.date.max()


def _trade_date_series(df: pd.DataFrame, schema: str) -> pd.Series:
    if df.empty:
        return pd.Series(dtype="datetime64[ns, UTC]")
    if schema == "definition":
        return pd.to_datetime(df["ts_event"] if "ts_event" in df.columns else df["ts_recv"], utc=True)
    if schema == "statistics":
        column = "ts_ref" if "ts_ref" in df.columns and df["ts_ref"].notna().any() else "ts_event"
        return pd.to_datetime(df[column], utc=True)
    return pd.to_datetime(df["ts_event"], utc=True)


def _directory_manifest_hash(path: Path) -> str:
    if path.is_file():
        return file_sha256(path)
    payload = []
    for item in sorted(path.rglob("*.parquet")):
        payload.append({"path": item.relative_to(path).as_posix(), "sha256": file_sha256(item)})
    return compute_manifest_hash(payload)


def _min_trade_date(df: pd.DataFrame) -> date | None:
    if df.empty:
        return None
    if "trade_date" in df.columns:
        return pd.to_datetime(df["trade_date"]).dt.date.min()
    return None


def _max_trade_date(df: pd.DataFrame) -> date | None:
    if df.empty:
        return None
    if "trade_date" in df.columns:
        return pd.to_datetime(df["trade_date"]).dt.date.max()
    return None


def _coerce_date(value: Any) -> date | None:
    if value is None or pd.isna(value):
        return None
    return pd.Timestamp(value).date()
