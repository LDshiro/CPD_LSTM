from __future__ import annotations

from pathlib import Path

from cpdshadow.config import SignalsConfig, TsmomStrategyConfig
from cpdshadow.ids import canonical_json_bytes, stable_sha256_hex
from cpdshadow.storage.parquet_io import ensure_directory


def build_tsmom_formula_payload(
    *,
    signals_config: SignalsConfig,
    strategy_config: TsmomStrategyConfig,
) -> dict[str, object]:
    return {
        "strategy_id": strategy_config.strategy_id,
        "model_id": strategy_config.model_id,
        "signal_version": strategy_config.signal_version,
        "feature_set_id": strategy_config.feature_set_id,
        "required_features": list(strategy_config.required_features),
        "horizons_days": list(strategy_config.horizons_days),
        "weights": [float(weight) for weight in strategy_config.weights],
        "formula": "mean(sign(ret_21), sign(ret_63), sign(ret_252))",
        "clip": [float(signals_config.clip_min), float(signals_config.clip_max)],
        "allow_partial_horizons": bool(strategy_config.allow_partial_horizons),
        "sign_zero_policy": strategy_config.sign_zero_policy,
    }


def write_formula_artifact(
    *,
    repo_root: Path,
    signals_config: SignalsConfig,
    strategy_config: TsmomStrategyConfig,
) -> tuple[Path, Path, str]:
    artifact_dir = ensure_directory(repo_root / strategy_config.formula_artifact_dir)
    formula_path = artifact_dir / "formula.json"
    sha_path = artifact_dir / "formula.sha256"
    payload = build_tsmom_formula_payload(
        signals_config=signals_config,
        strategy_config=strategy_config,
    )
    encoded = canonical_json_bytes(payload)
    sha256 = stable_sha256_hex(encoded)
    formula_path.write_bytes(encoded)
    sha_path.write_text(sha256, encoding="utf-8")
    return formula_path, sha_path, sha256


def build_formulaic_model_registry_row(
    *,
    strategy_config: TsmomStrategyConfig,
    artifact_path: Path,
    artifact_sha256: str,
) -> dict[str, object]:
    return {
        "model_id": strategy_config.model_id,
        "strategy_id": strategy_config.strategy_id,
        "training_run_id": None,
        "feature_set_id": strategy_config.feature_set_id,
        "model_status": "shadow",
        "artifact_path": artifact_path.as_posix(),
        "artifact_sha256": artifact_sha256,
    }
