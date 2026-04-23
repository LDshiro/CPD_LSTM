from __future__ import annotations

from datetime import date
from typing import Sequence

import numpy as np
import pandas as pd

from cpdshadow.config import CpdLstmModelConfig
from cpdshadow.ml.artifacts import ModelArtifact
from cpdshadow.ml.dataset import build_inference_sequences
from cpdshadow.ml.model import require_torch, resolve_device
from cpdshadow.signals import (
    SIGNALS_DAILY_COLUMNS,
    SignalBuildRequest,
    SignalBuildResult,
    sort_signals_daily,
)


def build_cpd_lstm_signals(
    *,
    features_daily: pd.DataFrame,
    artifact: ModelArtifact,
    request: SignalBuildRequest,
    snapshot_id: str,
    feature_set_id: str,
    series_id: str,
    roots: Sequence[str] | None,
    start_date: date,
    end_date: date,
    device: str = "cpu",
) -> SignalBuildResult:
    torch = require_torch()
    config: CpdLstmModelConfig = artifact.config
    sequences = build_inference_sequences(
        features_daily=features_daily,
        config=config,
        snapshot_id=snapshot_id,
        feature_set_id=feature_set_id,
        series_id=series_id,
        roots=roots,
        start_date=start_date,
        end_date=end_date,
    )
    prediction_lookup: dict[tuple[str, date], float] = {}
    if sequences.panel.sample_count:
        x = artifact.standardizer.transform(sequences.panel.x)
        resolved_device = resolve_device(device)
        artifact.model.to(resolved_device)
        artifact.model.eval()
        with torch.no_grad():
            values = torch.as_tensor(x, dtype=torch.float32, device=resolved_device)
            predictions = artifact.model(values).detach().cpu().numpy()
        for root, as_of_date, signal in zip(
            sequences.panel.roots,
            sequences.panel.dates,
            predictions,
            strict=True,
        ):
            prediction_lookup[(root, as_of_date)] = float(np.clip(signal, -1.0, 1.0))

    rows: list[dict[str, object]] = []
    for _, audit in sequences.audit_rows.iterrows():
        row_date = pd.Timestamp(audit["as_of_date"]).date()
        root = str(audit["root"])
        signal = prediction_lookup.get((root, row_date))
        is_valid = signal is not None
        rows.append({
            "run_id": request.run_id,
            "strategy_id": request.strategy_id,
            "model_id": request.model_id,
            "as_of_date": row_date,
            "root": root,
            "signal_raw": signal if is_valid else None,
            "signal_clipped": float(np.clip(signal, -1.0, 1.0)) if is_valid else None,
            "is_valid": is_valid,
            "invalid_reason": None if is_valid else str(audit["invalid_reason"]),
            "feature_hash": audit["feature_hash"],
            "created_at_utc": request.created_at_utc,
        })
    signals = pd.DataFrame(rows, columns=SIGNALS_DAILY_COLUMNS)
    signals = sort_signals_daily(signals, sort_keys=("as_of_date", "root"))
    return SignalBuildResult(
        signals=signals,
        qa={
            "valid_sequence_count": sequences.panel.sample_count,
            "audit_row_count": int(len(sequences.audit_rows)),
        },
        formula_artifact_path=None,
        formula_artifact_sha256=None,
        model_registry_row=None,
    )
