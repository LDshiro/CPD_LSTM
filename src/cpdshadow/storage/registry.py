from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from pathlib import Path
import uuid

import pandas as pd

from cpdshadow.storage.parquet_io import ensure_directory, read_parquet_dataset, write_parquet_part


@dataclass(frozen=True)
class RunRegistryRow:
    run_id: str
    run_type: str
    command_name: str
    status: str
    started_at_utc: datetime
    finished_at_utc: datetime | None
    data_snapshot_id: str | None
    config_hash: str
    git_commit: str
    notes: list[str] = field(default_factory=list)
    artifact_path: str | None = None


@dataclass(frozen=True)
class SnapshotRegistryRow:
    snapshot_id: str
    run_id: str
    vendor: str
    dataset: str
    start_date: date
    end_date: date
    requested_roots: list[str]
    requested_schemas: list[str]
    request_plan_hash: str
    manifest_hash: str
    created_at_utc: datetime


@dataclass(frozen=True)
class FileRegistryRow:
    file_id: str
    snapshot_id: str
    run_id: str
    logical_table: str
    source_schema: str | None
    path: str
    content_sha256: str
    row_count: int
    min_trade_date: date | None
    max_trade_date: date | None
    request_params_hash: str
    cached: bool
    created_at_utc: datetime


def append_run_registry_row(root: str | Path, row: RunRegistryRow) -> Path:
    return _append_registry_row(Path(root), asdict(row), row.run_id)


def append_snapshot_registry_row(root: str | Path, row: SnapshotRegistryRow) -> Path:
    return _append_registry_row(Path(root), asdict(row), row.snapshot_id)


def append_file_registry_row(root: str | Path, row: FileRegistryRow) -> Path:
    return _append_registry_row(Path(root), asdict(row), row.file_id)


def load_run_registry(root: str | Path) -> pd.DataFrame:
    return read_parquet_dataset(root)


def load_snapshot_registry(root: str | Path) -> pd.DataFrame:
    return read_parquet_dataset(root)


def load_file_registry(root: str | Path) -> pd.DataFrame:
    return read_parquet_dataset(root)


def _append_registry_row(root: Path, row: dict[str, object], part_name: str) -> Path:
    target_dir = ensure_directory(root)
    df = pd.DataFrame([row])
    return write_parquet_part(df, target_dir / f"{part_name}__{uuid.uuid4().hex[:8]}.parquet")
