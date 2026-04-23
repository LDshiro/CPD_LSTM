from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import pandas as pd
import pytest

torch = pytest.importorskip("torch")

from cpdshadow.config import CpdLstmModelConfig  # noqa: E402
from cpdshadow.features import FEATURE_VECTOR_ORDER  # noqa: E402
from cpdshadow.ml.artifacts import ModelArtifact, load_model_artifact, save_model_artifact  # noqa: E402
from cpdshadow.ml.dataset import Standardizer  # noqa: E402
from cpdshadow.ml.infer import build_cpd_lstm_signals  # noqa: E402
from cpdshadow.ml.model import create_model, set_global_seed  # noqa: E402
from cpdshadow.ml.train import (  # noqa: E402
    _standardizer_fit_values,
    synthetic_wp9_frames,
    train_cpd_lstm_from_frames,
)
from cpdshadow.signals import SIGNALS_DAILY_COLUMNS, SignalBuildRequest  # noqa: E402


def _config(max_epochs: int = 1) -> CpdLstmModelConfig:
    base = CpdLstmModelConfig()
    training = base.training.model_copy(
        update={
            "max_epochs": max_epochs,
            "min_epochs": 1,
            "early_stopping_patience": 1,
            "device": "cpu",
            "deterministic_mode": "warn",
        }
    )
    return base.model_copy(update={"training": training})


def test_cpd_lstm_model_output_range() -> None:
    set_global_seed(123)
    config = _config()
    model = create_model(config)
    model.eval()
    x = torch.zeros((4, config.sequence_length, len(config.feature_order)), dtype=torch.float32)
    with torch.no_grad():
        output = model(x)

    assert torch.isfinite(output).all()
    assert torch.max(torch.abs(output)).item() <= 1.0


def test_cpd_lstm_standardizer_fit_train_only() -> None:
    features, _ = synthetic_wp9_frames(
        roots=["ES"],
        n_days=130,
        snapshot_id="snapshot_test",
        feature_set_id="features_v1",
        series_id="v1_back_ratio_settle",
    )
    train_start = date(2020, 4, 15)
    train_end = date(2020, 5, 15)
    values = _standardizer_fit_values(
        features_daily=features,
        config=_config(),
        snapshot_id="snapshot_test",
        roots=["ES"],
        train_start=train_start,
        train_end=train_end,
    )
    mutated = features.copy()
    mutated.loc[pd.to_datetime(mutated["as_of_date"]).dt.date > train_end, "ret_21"] = 99999.0
    mutated_values = _standardizer_fit_values(
        features_daily=mutated,
        config=_config(),
        snapshot_id="snapshot_test",
        roots=["ES"],
        train_start=train_start,
        train_end=train_end,
    )

    assert (values == mutated_values).all()


def test_cpd_lstm_artifact_round_trip_same_signals(tmp_path: Path) -> None:
    config = _config()
    set_global_seed(42)
    model = create_model(config)
    standardizer = Standardizer(
        feature_order=tuple(FEATURE_VECTOR_ORDER),
        mean=tuple(0.0 for _ in FEATURE_VECTOR_ORDER),
        std=tuple(1.0 for _ in FEATURE_VECTOR_ORDER),
        min_std=1.0e-6,
        clip_abs=10.0,
    )
    features, _ = synthetic_wp9_frames(
        roots=["ES"],
        n_days=100,
        snapshot_id="snapshot_test",
        feature_set_id="features_v1",
        series_id="v1_back_ratio_settle",
    )
    request = SignalBuildRequest(
        run_id="infer_test",
        strategy_id="cpd_lstm",
        model_id="cpd_lstm_test",
        feature_set_id="features_v1",
        snapshot_id="snapshot_test",
        start_date=date(2020, 4, 20),
        end_date=date(2020, 4, 21),
        roots=("ES",),
        created_at_utc=datetime(2026, 4, 23, tzinfo=UTC),
    )
    before = build_cpd_lstm_signals(
        features_daily=features,
        artifact=ModelArtifact(
            model=model,
            config=config,
            standardizer=standardizer,
            metrics={},
            train_manifest={},
            artifact_sha256="",
        ),
        request=request,
        snapshot_id="snapshot_test",
        feature_set_id="features_v1",
        series_id="v1_back_ratio_settle",
        roots=["ES"],
        start_date=date(2020, 4, 20),
        end_date=date(2020, 4, 21),
        device="cpu",
    ).signals
    save_model_artifact(
        model=model,
        config=config,
        standardizer=standardizer,
        metrics={"val_sharpe_ex_cost_best": 0.0},
        train_manifest={
            "training_run_id": "train_test",
            "model_id": "cpd_lstm_test",
            "created_at_utc": "2026-04-23T00:00:00+00:00",
        },
        model_dir=tmp_path / "model",
    )
    loaded = load_model_artifact(tmp_path / "model", device="cpu")
    after = build_cpd_lstm_signals(
        features_daily=features,
        artifact=loaded,
        request=request,
        snapshot_id="snapshot_test",
        feature_set_id="features_v1",
        series_id="v1_back_ratio_settle",
        roots=["ES"],
        start_date=date(2020, 4, 20),
        end_date=date(2020, 4, 21),
        device="cpu",
    ).signals

    assert before["signal_clipped"].tolist() == after["signal_clipped"].tolist()


def test_cpd_lstm_signals_schema_matches_wp3() -> None:
    assert SIGNALS_DAILY_COLUMNS == [
        "run_id",
        "strategy_id",
        "model_id",
        "as_of_date",
        "root",
        "signal_raw",
        "signal_clipped",
        "is_valid",
        "invalid_reason",
        "feature_hash",
        "created_at_utc",
    ]


def test_cpd_lstm_training_smoke_cpu_if_torch_available() -> None:
    features, continuous = synthetic_wp9_frames(
        roots=["ES", "NQ"],
        n_days=150,
        snapshot_id="snapshot_test",
        feature_set_id="features_v1",
        series_id="v1_back_ratio_settle",
    )
    result = train_cpd_lstm_from_frames(
        features_daily=features,
        continuous_daily=continuous,
        config=_config(max_epochs=1),
        snapshot_id="snapshot_test",
        roots=["ES", "NQ"],
        train_start=date(2020, 4, 15),
        train_end=date(2020, 5, 15),
        val_start=date(2020, 5, 18),
        val_end=date(2020, 6, 5),
        model_id="cpd_lstm_test",
        training_run_id="train_test",
    )

    assert result.metrics["n_train_samples"] > 0
    assert result.metrics["n_val_samples"] > 0
