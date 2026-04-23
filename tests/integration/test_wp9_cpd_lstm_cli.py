from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

pytest.importorskip("torch")

from cpdshadow.cli import app  # noqa: E402
from cpdshadow.storage.parquet_io import read_parquet_dataset  # noqa: E402


def test_wp9_cpd_lstm_smoke_cli_offline(tmp_path: Path) -> None:
    shutil.copytree(Path("config"), tmp_path / "config")
    runner = CliRunner()

    result = runner.invoke(
        app,
        [
            "models",
            "cpd-lstm",
            "smoke",
            "--output-root",
            "artifacts/wp9/smoke",
            "--repo-root",
            str(tmp_path),
        ],
    )

    assert result.exit_code == 0, result.stdout
    model_dir = tmp_path / "artifacts" / "wp9" / "smoke" / "artifacts" / "models" / "cpd_lstm" / "cpd_lstm_v1_smoke"
    assert (model_dir / "model.pt").exists()
    assert (model_dir / "standardizer.json").exists()
    assert (model_dir / "sha256sums.txt").exists()
    signals = read_parquet_dataset(
        tmp_path
        / "artifacts"
        / "wp9"
        / "smoke"
        / "data"
        / "research"
        / "signals_daily"
        / "strategy_id=cpd_lstm"
        / "model_id=cpd_lstm_v1_smoke"
        / "run_id=infer_cpd_lstm_v1_smoke"
    )
    assert not signals.empty
    assert set(signals["strategy_id"]) == {"cpd_lstm"}
    assert not signals.duplicated(subset=["run_id", "strategy_id", "as_of_date", "root"]).any()
    assert (
        tmp_path
        / "artifacts"
        / "wp9"
        / "smoke"
        / "artifacts"
        / "wp9"
        / "cpd_lstm_infer_qa_infer_cpd_lstm_v1_smoke_cpd_lstm_v1_smoke.json"
    ).exists()
    file_registry = read_parquet_dataset(tmp_path / "data" / "meta" / "data_file_registry")
    assert "signals_daily" in set(file_registry["logical_table"])
