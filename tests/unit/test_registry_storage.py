from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd

from cpdshadow.ids import file_sha256, make_file_id, make_run_id, make_snapshot_id
from cpdshadow.storage.parquet_io import read_parquet_dataset, write_parquet_part
from cpdshadow.storage.registry import (
    FileRegistryRow,
    RunRegistryRow,
    SnapshotRegistryRow,
    append_file_registry_row,
    append_run_registry_row,
    append_snapshot_registry_row,
    load_file_registry,
    load_run_registry,
    load_snapshot_registry,
)


def test_file_sha256_and_registry_append(tmp_path: Path) -> None:
    raw_file = tmp_path / "raw.parquet"
    write_parquet_part(pd.DataFrame([{"value": 1}]), raw_file)
    digest = file_sha256(raw_file)
    assert len(digest) == 64

    run_id = make_run_id()
    snapshot_id = make_snapshot_id(run_id)
    append_run_registry_row(
        tmp_path / "data" / "meta" / "run_registry",
        RunRegistryRow(
            run_id=run_id,
            run_type="ingest",
            command_name="smoke",
            status="started",
            started_at_utc=datetime.now(timezone.utc),
            finished_at_utc=None,
            data_snapshot_id=snapshot_id,
            config_hash="abc",
            git_commit="unknown-local",
            notes=["test"],
            artifact_path="artifacts/wp4/test.json",
        ),
    )
    append_snapshot_registry_row(
        tmp_path / "data" / "meta" / "data_snapshot_registry",
        SnapshotRegistryRow(
            snapshot_id=snapshot_id,
            run_id=run_id,
            vendor="databento",
            dataset="GLBX.MDP3",
            start_date=date(2024, 1, 1),
            end_date=date(2024, 1, 2),
            requested_roots=["ES"],
            requested_schemas=["definition"],
            request_plan_hash="plan",
            manifest_hash="manifest",
            created_at_utc=datetime.now(timezone.utc),
        ),
    )
    append_file_registry_row(
        tmp_path / "data" / "meta" / "data_file_registry",
        FileRegistryRow(
            file_id=make_file_id(snapshot_id, "raw_definition", raw_file),
            snapshot_id=snapshot_id,
            run_id=run_id,
            logical_table="raw_definition",
            source_schema="definition",
            path=raw_file.as_posix(),
            content_sha256=digest,
            row_count=1,
            min_trade_date=date(2024, 1, 1),
            max_trade_date=date(2024, 1, 1),
            request_params_hash="req",
            cached=False,
            created_at_utc=datetime.now(timezone.utc),
        ),
    )

    assert len(load_run_registry(tmp_path / "data" / "meta" / "run_registry")) == 1
    assert len(load_snapshot_registry(tmp_path / "data" / "meta" / "data_snapshot_registry")) == 1
    assert len(load_file_registry(tmp_path / "data" / "meta" / "data_file_registry")) == 1
    assert len(read_parquet_dataset(tmp_path / "data" / "meta" / "data_file_registry")) == 1
