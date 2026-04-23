from __future__ import annotations

from pathlib import Path

from cpdshadow.config import load_yaml
from cpdshadow.ids import canonical_json_bytes, stable_sha256_hex
from cpdshadow.signal_io import (
    build_formulaic_model_registry_row,
    build_tsmom_formula_payload,
    write_formula_artifact,
)


def test_formula_artifact_payload_and_sha_are_stable(tmp_path: Path) -> None:
    cfg = load_yaml(Path("config/settings.base.yml"))

    formula_path, sha_path, sha256 = write_formula_artifact(
        repo_root=tmp_path,
        signals_config=cfg.signals,
        strategy_config=cfg.strategies.tsmom,
    )
    payload = build_tsmom_formula_payload(
        signals_config=cfg.signals,
        strategy_config=cfg.strategies.tsmom,
    )
    expected_bytes = canonical_json_bytes(payload)
    expected_sha = stable_sha256_hex(expected_bytes)

    assert formula_path.read_bytes() == expected_bytes
    assert sha_path.read_text(encoding="utf-8") == expected_sha
    assert sha256 == expected_sha

    second_formula_path, second_sha_path, second_sha = write_formula_artifact(
        repo_root=tmp_path,
        signals_config=cfg.signals,
        strategy_config=cfg.strategies.tsmom,
    )
    assert second_formula_path.read_bytes() == expected_bytes
    assert second_sha_path.read_text(encoding="utf-8") == expected_sha
    assert second_sha == expected_sha


def test_formulaic_model_registry_row_shape_is_stable(tmp_path: Path) -> None:
    cfg = load_yaml(Path("config/settings.base.yml"))
    artifact_path = tmp_path / "artifacts" / "strategies" / "tsmom_v1" / "formula.json"
    row = build_formulaic_model_registry_row(
        strategy_config=cfg.strategies.tsmom,
        artifact_path=artifact_path,
        artifact_sha256="abc123",
    )

    assert row == {
        "model_id": "tsmom_v1",
        "strategy_id": "tsmom",
        "training_run_id": None,
        "feature_set_id": "features_v1",
        "model_status": "shadow",
        "artifact_path": artifact_path.as_posix(),
        "artifact_sha256": "abc123",
    }
