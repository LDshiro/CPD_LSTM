from __future__ import annotations

from pathlib import Path
import shutil

import numpy as np
import pandas as pd
from typer.testing import CliRunner

from cpdshadow.cli import app
from cpdshadow.storage.parquet_io import read_parquet_dataset, write_parquet_dataset


def _copy_repo_config(tmp_path: Path) -> None:
    shutil.copytree(Path("config"), tmp_path / "config")
    (tmp_path / "data" / "curated").mkdir(parents=True, exist_ok=True)
    (tmp_path / "data" / "features").mkdir(parents=True, exist_ok=True)
    (tmp_path / "data" / "meta").mkdir(parents=True, exist_ok=True)
    (tmp_path / "artifacts" / "wp7").mkdir(parents=True, exist_ok=True)


def _make_continuous_snapshot(tmp_path: Path) -> None:
    dates = pd.bdate_range("2022-06-01", periods=460)
    rows: list[dict[str, object]] = []
    for root_index, root in enumerate(("ES", "NQ")):
        price = 100.0 + 15.0 * root_index
        previous_price: float | None = None
        for idx, timestamp in enumerate(dates):
            growth = 0.0009 + 0.0003 * np.sin(idx / 13.0 + root_index)
            price *= 1.0 + growth
            daily_return = None if previous_price is None else price / previous_price - 1.0
            rows.append({
                "series_id": "v1_back_ratio_settle",
                "as_of_date": timestamp.date(),
                "root": root,
                "lead_raw_symbol": f"{root}H4",
                "raw_settle_price": price,
                "adj_settle_price": price,
                "adj_factor": 1.0,
                "daily_return": daily_return,
                "settle_status": "final",
                "roll_flag": False,
                "roll_event_id": None,
                "is_usable_for_signal": True,
                "quality_flags": [],
                "builder_version": "continuous_builder_v1",
                "snapshot_id": "snapshot_test",
            })
            previous_price = price
    continuous_daily = pd.DataFrame(rows)
    continuous_daily = continuous_daily.assign(
        year=pd.to_datetime(continuous_daily["as_of_date"]).dt.year,
    )
    write_parquet_dataset(
        continuous_daily,
        tmp_path
        / "data"
        / "curated"
        / "continuous_daily"
        / "series_id=v1_back_ratio_settle"
        / "snapshot_id=snapshot_test",
        compression="zstd",
        partition_cols=["year"],
    )


def test_features_build_and_qa_offline(tmp_path: Path) -> None:
    _copy_repo_config(tmp_path)
    _make_continuous_snapshot(tmp_path)
    runner = CliRunner()

    build = runner.invoke(
        app,
        [
            "features",
            "build",
            "--snapshot-id",
            "snapshot_test",
            "--start",
            "2024-01-02",
            "--end",
            "2024-03-29",
            "--series-id",
            "v1_back_ratio_settle",
            "--feature-set-id",
            "features_v1",
            "--roots",
            "ES,NQ",
            "--repo-root",
            str(tmp_path),
        ],
    )
    assert build.exit_code == 0, build.stdout

    cpd_daily = read_parquet_dataset(
        tmp_path
        / "data"
        / "features"
        / "cpd_daily"
        / "feature_set_id=features_v1"
        / "snapshot_id=snapshot_test"
    )
    features_daily = read_parquet_dataset(
        tmp_path
        / "data"
        / "features"
        / "features_daily"
        / "feature_set_id=features_v1"
        / "snapshot_id=snapshot_test"
    )
    assert not cpd_daily.empty
    assert not features_daily.empty
    assert not cpd_daily.duplicated(
        subset=["feature_set_id", "as_of_date", "root", "cpd_window_days"]
    ).any()
    assert not features_daily.duplicated(
        subset=["feature_set_id", "as_of_date", "root"]
    ).any()

    qa = runner.invoke(
        app,
        [
            "features",
            "qa",
            "--snapshot-id",
            "snapshot_test",
            "--feature-set-id",
            "features_v1",
            "--input-dir",
            "data/features",
            "--repo-root",
            str(tmp_path),
        ],
    )
    assert qa.exit_code == 0, qa.stdout

    assert (tmp_path / "artifacts" / "wp7" / "features_qa_snapshot_test_features_v1.json").exists()
    assert (tmp_path / "artifacts" / "wp7" / "features_qa_snapshot_test_features_v1.md").exists()

    file_registry = read_parquet_dataset(tmp_path / "data" / "meta" / "data_file_registry")
    assert {"cpd_daily", "features_daily"}.issubset(set(file_registry["logical_table"]))

    rerun_without_overwrite = runner.invoke(
        app,
        [
            "features",
            "build",
            "--snapshot-id",
            "snapshot_test",
            "--start",
            "2024-01-02",
            "--end",
            "2024-03-29",
            "--series-id",
            "v1_back_ratio_settle",
            "--feature-set-id",
            "features_v1",
            "--roots",
            "ES,NQ",
            "--repo-root",
            str(tmp_path),
        ],
    )
    assert rerun_without_overwrite.exit_code != 0

    rerun_with_overwrite = runner.invoke(
        app,
        [
            "features",
            "build",
            "--snapshot-id",
            "snapshot_test",
            "--start",
            "2024-01-02",
            "--end",
            "2024-03-29",
            "--series-id",
            "v1_back_ratio_settle",
            "--feature-set-id",
            "features_v1",
            "--roots",
            "ES,NQ",
            "--overwrite",
            "--repo-root",
            str(tmp_path),
        ],
    )
    assert rerun_with_overwrite.exit_code == 0, rerun_with_overwrite.stdout
