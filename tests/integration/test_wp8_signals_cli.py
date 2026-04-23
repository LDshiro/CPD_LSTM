from __future__ import annotations

import shutil
from datetime import date
from pathlib import Path

import pandas as pd
from typer.testing import CliRunner

from cpdshadow.cli import app
from cpdshadow.config import load_yaml
from cpdshadow.portfolio import build_target_positions
from cpdshadow.risk import SizingInput
from cpdshadow.storage.parquet_io import read_parquet_dataset, write_parquet_dataset


def _copy_repo_config(tmp_path: Path) -> None:
    shutil.copytree(Path("config"), tmp_path / "config")
    (tmp_path / "data" / "features").mkdir(parents=True, exist_ok=True)
    (tmp_path / "data" / "meta").mkdir(parents=True, exist_ok=True)
    (tmp_path / "data" / "research").mkdir(parents=True, exist_ok=True)
    (tmp_path / "artifacts" / "wp8").mkdir(parents=True, exist_ok=True)


def _feature_row(
    *,
    as_of_date: str,
    root: str,
    ret_21: float,
    ret_63: float,
    ret_252: float,
    feature_hash: str,
    is_complete: bool = True,
    warmup_status: str = "ok",
) -> dict[str, object]:
    return {
        "feature_set_id": "features_v1",
        "as_of_date": pd.Timestamp(as_of_date).date(),
        "root": root,
        "series_id": "v1_back_ratio_settle",
        "ret_1": 0.1,
        "ret_21": ret_21,
        "ret_63": ret_63,
        "ret_126": 0.2,
        "ret_252": ret_252,
        "macd_8_24": 0.1,
        "macd_16_48": 0.1,
        "macd_32_96": 0.1,
        "cpd21_score": 0.1,
        "cpd21_age": 0.2,
        "cpd63_score": 0.3,
        "cpd63_age": 0.4,
        "vol_20_60": 1.0,
        "vol_60_252": 1.0,
        "annualized_vol_60": 0.2,
        "is_complete": is_complete,
        "warmup_status": warmup_status,
        "feature_hash": feature_hash,
        "builder_version": "features_builder_v1",
        "snapshot_id": "snapshot_test",
    }


def _write_features_snapshot(tmp_path: Path) -> None:
    features_daily = pd.DataFrame(
        [
            _feature_row(
                as_of_date="2024-01-02",
                root="ES",
                ret_21=1.0,
                ret_63=1.0,
                ret_252=1.0,
                feature_hash="es_1",
            ),
            _feature_row(
                as_of_date="2024-01-03",
                root="ES",
                ret_21=1.0,
                ret_63=-1.0,
                ret_252=1.0,
                feature_hash="es_2",
            ),
            _feature_row(
                as_of_date="2024-01-02",
                root="NQ",
                ret_21=-1.0,
                ret_63=-1.0,
                ret_252=1.0,
                feature_hash="nq_1",
            ),
            _feature_row(
                as_of_date="2024-01-03",
                root="NQ",
                ret_21=-1.0,
                ret_63=-1.0,
                ret_252=-1.0,
                feature_hash="nq_2",
            ),
        ]
    )
    features_daily = features_daily.assign(
        year=pd.to_datetime(features_daily["as_of_date"]).dt.year,
    )
    write_parquet_dataset(
        features_daily,
        tmp_path
        / "data"
        / "features"
        / "features_daily"
        / "feature_set_id=features_v1"
        / "snapshot_id=snapshot_test",
        compression="zstd",
        partition_cols=["year"],
    )


def test_signals_build_and_qa_offline(tmp_path: Path) -> None:
    _copy_repo_config(tmp_path)
    _write_features_snapshot(tmp_path)
    runner = CliRunner()

    build = runner.invoke(
        app,
        [
            "signals",
            "tsmom",
            "build",
            "--features-path",
            "data/features/features_daily",
            "--snapshot-id",
            "snapshot_test",
            "--feature-set-id",
            "features_v1",
            "--start",
            "2024-01-02",
            "--end",
            "2024-01-03",
            "--roots",
            "ES,NQ",
            "--run-id",
            "infer_tsmom_2024",
            "--created-at-utc",
            "2026-04-20T00:00:00Z",
            "--output-dir",
            "data/research/signals_daily",
            "--artifact-dir",
            "artifacts/wp8",
            "--repo-root",
            str(tmp_path),
        ],
    )
    assert build.exit_code == 0, build.stdout

    signals_daily = read_parquet_dataset(
        tmp_path
        / "data"
        / "research"
        / "signals_daily"
        / "strategy_id=tsmom"
        / "model_id=tsmom_v1"
        / "run_id=infer_tsmom_2024"
    )
    assert not signals_daily.empty
    assert not signals_daily.duplicated(
        subset=["run_id", "strategy_id", "as_of_date", "root"]
    ).any()
    assert set(signals_daily["signal_raw"]) == {1.0, 1.0 / 3.0, -1.0 / 3.0, -1.0}

    qa = runner.invoke(
        app,
        [
            "signals",
            "qa",
            "--signals-path",
            "data/research/signals_daily",
            "--run-id",
            "infer_tsmom_2024",
            "--artifact-dir",
            "artifacts/wp8",
            "--repo-root",
            str(tmp_path),
        ],
    )
    assert qa.exit_code == 0, qa.stdout

    assert (tmp_path / "artifacts" / "wp8" / "signals_qa_infer_tsmom_2024.json").exists()
    assert (tmp_path / "artifacts" / "wp8" / "signals_qa_infer_tsmom_2024.md").exists()
    assert (tmp_path / "artifacts" / "strategies" / "tsmom_v1" / "formula.json").exists()
    assert (tmp_path / "artifacts" / "strategies" / "tsmom_v1" / "formula.sha256").exists()

    file_registry = read_parquet_dataset(tmp_path / "data" / "meta" / "data_file_registry")
    assert "signals_daily" in set(file_registry["logical_table"])

    rerun_without_overwrite = runner.invoke(
        app,
        [
            "signals",
            "tsmom",
            "build",
            "--features-path",
            "data/features/features_daily",
            "--snapshot-id",
            "snapshot_test",
            "--feature-set-id",
            "features_v1",
            "--start",
            "2024-01-02",
            "--end",
            "2024-01-03",
            "--roots",
            "ES,NQ",
            "--run-id",
            "infer_tsmom_2024",
            "--created-at-utc",
            "2026-04-20T00:00:00Z",
            "--output-dir",
            "data/research/signals_daily",
            "--artifact-dir",
            "artifacts/wp8",
            "--repo-root",
            str(tmp_path),
        ],
    )
    assert rerun_without_overwrite.exit_code != 0

    rerun_with_overwrite = runner.invoke(
        app,
        [
            "signals",
            "tsmom",
            "build",
            "--features-path",
            "data/features/features_daily",
            "--snapshot-id",
            "snapshot_test",
            "--feature-set-id",
            "features_v1",
            "--start",
            "2024-01-02",
            "--end",
            "2024-01-03",
            "--roots",
            "ES,NQ",
            "--run-id",
            "infer_tsmom_2024",
            "--created-at-utc",
            "2026-04-20T00:00:00Z",
            "--output-dir",
            "data/research/signals_daily",
            "--artifact-dir",
            "artifacts/wp8",
            "--overwrite",
            "--repo-root",
            str(tmp_path),
        ],
    )
    assert rerun_with_overwrite.exit_code == 0, rerun_with_overwrite.stdout


def test_tsmom_signals_can_feed_existing_sizing_layer(tmp_path: Path) -> None:
    _copy_repo_config(tmp_path)
    _write_features_snapshot(tmp_path)
    runner = CliRunner()
    build = runner.invoke(
        app,
        [
            "signals",
            "tsmom",
            "build",
            "--features-path",
            "data/features/features_daily",
            "--snapshot-id",
            "snapshot_test",
            "--feature-set-id",
            "features_v1",
            "--start",
            "2024-01-02",
            "--end",
            "2024-01-03",
            "--roots",
            "ES,NQ",
            "--run-id",
            "infer_tsmom_2024",
            "--created-at-utc",
            "2026-04-20T00:00:00Z",
            "--output-dir",
            "data/research/signals_daily",
            "--artifact-dir",
            "artifacts/wp8",
            "--repo-root",
            str(tmp_path),
        ],
    )
    assert build.exit_code == 0, build.stdout

    cfg = load_yaml(tmp_path / "config" / "settings.base.yml")
    signals_daily = read_parquet_dataset(
        tmp_path
        / "data"
        / "research"
        / "signals_daily"
        / "strategy_id=tsmom"
        / "model_id=tsmom_v1"
        / "run_id=infer_tsmom_2024"
    )
    latest = signals_daily[pd.to_datetime(signals_daily["as_of_date"]).dt.date == date(2024, 1, 3)]
    sizing_inputs = [
        SizingInput(
            root="ES",
            asset_class="equity_index",
            signal=float(latest.loc[latest["root"] == "ES", "signal_clipped"].iloc[0]),
            annualized_vol=0.20,
            lead_price=5000.0,
            quote_multiplier_to_usd_notional=50.0,
            initial_margin_per_contract_usd=15000.0,
            tick_value_usd=12.5,
        ),
        SizingInput(
            root="NQ",
            asset_class="equity_index",
            signal=float(latest.loc[latest["root"] == "NQ", "signal_clipped"].iloc[0]),
            annualized_vol=0.25,
            lead_price=19000.0,
            quote_multiplier_to_usd_notional=20.0,
            initial_margin_per_contract_usd=22000.0,
            tick_value_usd=5.0,
        ),
    ]

    result = build_target_positions(
        sizing_inputs,
        nav_usd=1_000_000.0,
        risk_config=cfg.risk,
        costs_config=cfg.costs,
    )

    assert len(result.positions) == 2
    assert any(position.final_contracts != 0 for position in result.positions)
