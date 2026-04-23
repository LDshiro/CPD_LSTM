from __future__ import annotations

import shutil
from pathlib import Path

import pandas as pd
import pytest
from typer.testing import CliRunner

pytest.importorskip("torch")

from cpdshadow.cli import app  # noqa: E402
from cpdshadow.ml.train import synthetic_wp9_frames  # noqa: E402
from cpdshadow.storage.parquet_io import read_parquet_dataset, write_parquet_dataset  # noqa: E402


def test_wp10_walkforward_smoke_cli_offline(tmp_path: Path) -> None:
    shutil.copytree(Path("config"), tmp_path / "config")
    snapshot_id = "snapshot_wp10_smoke"
    feature_set_id = "features_v1"
    series_id = "v1_back_ratio_settle"
    roots = ("ES", "NQ")
    features, continuous = synthetic_wp9_frames(
        roots=roots,
        n_days=570,
        snapshot_id=snapshot_id,
        feature_set_id=feature_set_id,
        series_id=series_id,
    )
    features_root = tmp_path / "data" / "features" / "features_daily"
    continuous_root = tmp_path / "data" / "curated" / "continuous_daily"
    write_parquet_dataset(
        features.assign(year=pd.to_datetime(features["as_of_date"]).dt.year),
        features_root / f"feature_set_id={feature_set_id}" / f"snapshot_id={snapshot_id}",
        partition_cols=["year"],
    )
    write_parquet_dataset(
        continuous.assign(year=pd.to_datetime(continuous["as_of_date"]).dt.year),
        continuous_root / f"series_id={series_id}" / f"snapshot_id={snapshot_id}",
        partition_cols=["year"],
    )

    runner = CliRunner()
    output_dir = tmp_path / "data" / "research" / "walkforward" / "smoke_wp10"
    result = runner.invoke(
        app,
        [
            "research",
            "walkforward",
            "run",
            "--features-path",
            "data/features/features_daily",
            "--continuous-path",
            "data/curated/continuous_daily",
            "--snapshot-id",
            snapshot_id,
            "--feature-set-id",
            feature_set_id,
            "--series-id",
            series_id,
            "--roots",
            "ES,NQ",
            "--oos-start",
            "2022-01-03",
            "--oos-end",
            "2022-01-31",
            "--run-id",
            "smoke_wp10",
            "--output-dir",
            "data/research/walkforward/smoke_wp10",
            "--train-years",
            "1",
            "--val-years",
            "1",
            "--min-train-days",
            "100",
            "--min-val-days",
            "60",
            "--min-oos-days",
            "10",
            "--max-epochs",
            "1",
            "--min-epochs",
            "1",
            "--device",
            "cpu",
            "--overwrite",
            "--repo-root",
            str(tmp_path),
        ],
    )

    assert result.exit_code == 0, result.stdout
    for relative in [
        "walkforward_windows.parquet",
        "oos_signals_daily.parquet",
        "oos_targets_daily.parquet",
        "oos_pnl_daily.parquet",
        "fold_metrics.parquet",
        "aggregate_metrics.parquet",
        "reversal_events.parquet",
        "reversal_bucket_metrics.parquet",
        "gates.json",
        "manifest.json",
        "reports/walkforward_report.json",
        "reports/walkforward_report.md",
    ]:
        assert (output_dir / relative).exists(), relative

    signals = read_parquet_dataset(output_dir / "oos_signals_daily.parquet")
    assert {"cpd_lstm", "tsmom"} == set(signals["strategy_id"])
    assert not signals.duplicated(
        subset=["run_id", "fold_id", "strategy_id", "root", "as_of_date"]
    ).any()
    assert set(signals["snapshot_id"]) == {snapshot_id}
    assert set(signals["feature_set_id"]) == {feature_set_id}

    qa = runner.invoke(
        app,
        [
            "research",
            "walkforward",
            "qa",
            "--run-dir",
            "data/research/walkforward/smoke_wp10",
            "--repo-root",
            str(tmp_path),
        ],
    )
    assert qa.exit_code == 0, qa.stdout

    no_overwrite = runner.invoke(
        app,
        [
            "research",
            "walkforward",
            "run",
            "--features-path",
            "data/features/features_daily",
            "--continuous-path",
            "data/curated/continuous_daily",
            "--snapshot-id",
            snapshot_id,
            "--roots",
            "ES,NQ",
            "--oos-start",
            "2022-01-03",
            "--oos-end",
            "2022-01-31",
            "--run-id",
            "smoke_wp10",
            "--output-dir",
            "data/research/walkforward/smoke_wp10",
            "--repo-root",
            str(tmp_path),
        ],
    )
    assert no_overwrite.exit_code != 0

