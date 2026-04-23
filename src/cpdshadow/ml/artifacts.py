from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cpdshadow.config import CpdLstmModelConfig
from cpdshadow.ids import canonical_json_bytes, file_sha256, stable_sha256_hex
from cpdshadow.ml.dataset import Standardizer
from cpdshadow.ml.model import CpdLstmNet, create_model, require_torch, resolve_device
from cpdshadow.storage.parquet_io import ensure_directory


@dataclass(frozen=True)
class ModelArtifact:
    model: CpdLstmNet
    config: CpdLstmModelConfig
    standardizer: Standardizer
    metrics: dict[str, object]
    train_manifest: dict[str, object]
    artifact_sha256: str


def save_model_artifact(
    *,
    model: CpdLstmNet,
    config: CpdLstmModelConfig,
    standardizer: Standardizer,
    metrics: dict[str, object],
    train_manifest: dict[str, object],
    model_dir: str | Path,
) -> str:
    torch = require_torch()
    target = ensure_directory(model_dir)
    _write_json(target / "config.json", config.model_dump(mode="python"))
    _write_json(target / "feature_order.json", list(config.feature_order))
    _write_json(target / "standardizer.json", standardizer.to_dict())
    _write_json(target / "metrics.json", metrics)
    _write_json(target / "train_manifest.json", train_manifest)
    (target / "model_card.md").write_text(
        _model_card(config=config, metrics=metrics, train_manifest=train_manifest),
        encoding="utf-8",
    )
    torch.save(
        {
            "model_state_dict": model.state_dict(),
            "model_config": config.model_dump(mode="python"),
            "feature_order": list(config.feature_order),
            "standardizer": standardizer.to_dict(),
            "training_run_id": train_manifest.get("training_run_id"),
            "model_id": train_manifest.get("model_id"),
            "created_at_utc": train_manifest.get("created_at_utc"),
        },
        target / "model.pt",
    )
    artifact_sha256 = _artifact_payload_hash(target)
    manifest = dict(train_manifest)
    manifest["artifact_sha256"] = artifact_sha256
    _write_json(target / "train_manifest.json", manifest)
    write_sha256sums(target)
    return artifact_sha256


def load_model_artifact(model_dir: str | Path, *, device: str = "cpu") -> ModelArtifact:
    torch = require_torch()
    target = Path(model_dir)
    if not target.exists():
        raise FileNotFoundError(f"model artifact directory not found: {target}")
    config = CpdLstmModelConfig.model_validate(_read_json(target / "config.json"))
    standardizer = Standardizer.from_dict(_read_json(target / "standardizer.json"))
    resolved_device = resolve_device(device)
    model = create_model(config)
    checkpoint = torch.load(target / "model.pt", map_location=resolved_device, weights_only=False)
    model.load_state_dict(checkpoint["model_state_dict"])
    model.to(resolved_device)
    model.eval()
    metrics = _read_json(target / "metrics.json")
    manifest = _read_json(target / "train_manifest.json")
    return ModelArtifact(
        model=model,
        config=config,
        standardizer=standardizer,
        metrics=metrics,
        train_manifest=manifest,
        artifact_sha256=str(manifest.get("artifact_sha256", "")),
    )


def write_sha256sums(model_dir: str | Path) -> str:
    target = Path(model_dir)
    rows = []
    for path in sorted(target.iterdir(), key=lambda item: item.name):
        if not path.is_file() or path.name == "sha256sums.txt":
            continue
        rows.append((file_sha256(path), path.name))
    (target / "sha256sums.txt").write_text(
        "".join(f"{sha}  {name}\n" for sha, name in rows),
        encoding="utf-8",
    )
    return stable_sha256_hex([{"file": name, "sha256": sha} for sha, name in rows])


def _artifact_payload_hash(model_dir: Path) -> str:
    rows = []
    for path in sorted(model_dir.iterdir(), key=lambda item: item.name):
        if not path.is_file() or path.name in {"sha256sums.txt", "train_manifest.json"}:
            continue
        rows.append((file_sha256(path), path.name))
    return stable_sha256_hex([{"file": name, "sha256": sha} for sha, name in rows])


def _write_json(path: Path, payload: object) -> None:
    path.write_bytes(canonical_json_bytes(payload))


def _read_json(path: Path) -> dict[str, Any]:
    import json

    return json.loads(path.read_text(encoding="utf-8"))


def _model_card(
    *,
    config: CpdLstmModelConfig,
    metrics: dict[str, object],
    train_manifest: dict[str, object],
) -> str:
    return "\n".join(
        [
            "# CPD-LSTM v1 Model Card",
            "",
            "This artifact is a WP9 candidate/shadow model skeleton. It is not promoted for live trading.",
            "",
            f"- Model family: `{config.model_family}`",
            f"- Strategy: `{config.strategy_id}`",
            f"- Sequence length: {config.sequence_length}",
            f"- Feature set: `{config.feature_set_id}`",
            f"- Series: `{config.series_id}`",
            f"- Training run: `{train_manifest.get('training_run_id')}`",
            f"- Model id: `{train_manifest.get('model_id')}`",
            f"- Validation Sharpe ex-cost best: {metrics.get('val_sharpe_ex_cost_best')}",
            "",
            "Inputs are WP7 features only at inference time. Labels during training come from next "
            "root-observed WP6 continuous returns.",
        ]
    )
