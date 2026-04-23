from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
import pytest

from cpdshadow.ids import canonical_json_bytes
from cpdshadow.model_release import (
    ModelReleaseError,
    package_model_release,
    qa_model_release,
)


def test_model_release_package_is_deterministic_and_qa_passes(tmp_path: Path) -> None:
    wf_dir, model_dir = _write_pass_fixture(tmp_path)
    output_dir = tmp_path / "releases"
    created_at = datetime(2026, 4, 23, tzinfo=UTC)

    first = package_model_release(
        walkforward_dir=wf_dir,
        candidate_model_dir=model_dir,
        gates_path=Path("config/model_release_gates.yml"),
        output_dir=output_dir,
        release_id="rc_test",
        created_at_utc=created_at,
        settings_path=Path("config/settings.base.yml"),
        data_schema_path=Path("config/data_schema.yml"),
    )
    first_manifest = (output_dir / "rc_test" / "manifest.json").read_bytes()
    second = package_model_release(
        walkforward_dir=wf_dir,
        candidate_model_dir=model_dir,
        gates_path=Path("config/model_release_gates.yml"),
        output_dir=output_dir,
        release_id="rc_test",
        created_at_utc=created_at,
        settings_path=Path("config/settings.base.yml"),
        data_schema_path=Path("config/data_schema.yml"),
    )

    assert first["decision"] == "shadow_candidate"
    assert second["decision"] == "shadow_candidate"
    assert first_manifest == (output_dir / "rc_test" / "manifest.json").read_bytes()
    assert qa_model_release(output_dir / "rc_test")["has_errors"] is False


def test_missing_model_artifact_fails_clearly(tmp_path: Path) -> None:
    wf_dir, model_dir = _write_pass_fixture(tmp_path)
    (model_dir / "model.pt").unlink()

    with pytest.raises(ModelReleaseError, match="missing required files"):
        package_model_release(
            walkforward_dir=wf_dir,
            candidate_model_dir=model_dir,
            gates_path=Path("config/model_release_gates.yml"),
            output_dir=tmp_path / "releases",
            release_id="rc_bad",
            created_at_utc=datetime(2026, 4, 23, tzinfo=UTC),
        )


def test_missing_walkforward_metrics_fail_clearly(tmp_path: Path) -> None:
    wf_dir, model_dir = _write_pass_fixture(tmp_path)
    (wf_dir / "aggregate_metrics.parquet").unlink()

    with pytest.raises(ModelReleaseError, match="missing or empty WP10 aggregate metrics"):
        package_model_release(
            walkforward_dir=wf_dir,
            candidate_model_dir=model_dir,
            gates_path=Path("config/model_release_gates.yml"),
            output_dir=tmp_path / "releases",
            release_id="rc_bad",
            created_at_utc=datetime(2026, 4, 23, tzinfo=UTC),
        )


def test_model_release_qa_catches_bad_decision_and_approval(tmp_path: Path) -> None:
    release = tmp_path / "release"
    release.mkdir()
    for name in [
        "gate_results.json",
        "metrics_summary.json",
        "artifact_hashes.json",
        "input_manifest.json",
    ]:
        (release / name).write_bytes(canonical_json_bytes({}))
    (release / "manifest.json").write_bytes(canonical_json_bytes({"release_id": "rc_bad"}))
    (release / "promotion_decision.json").write_bytes(
        canonical_json_bytes({
            "release_id": "rc_bad",
            "decision": "live_approved",
            "human_approval_required": False,
            "live_approved": True,
        })
    )
    (release / "model_card.md").write_text("card", encoding="utf-8")
    (release / "release_report.md").write_text("report", encoding="utf-8")

    report = qa_model_release(release)

    assert report["has_errors"] is True
    codes = {issue["code"] for issue in report["issues"]}
    assert {
        "invalid_decision",
        "human_approval_not_required",
        "forbidden_live_or_paper_approval",
    }.issubset(codes)


def _write_pass_fixture(tmp_path: Path) -> tuple[Path, Path]:
    wf_dir = tmp_path / "wf"
    model_dir = tmp_path / "model"
    wf_dir.mkdir()
    model_dir.mkdir()
    pd.DataFrame([
        {
            "strategy_id": "cpd_lstm",
            "model_id": "cpd_model",
            "sharpe": 1.1,
            "last_8_quarters_sharpe": 0.8,
            "max_drawdown": -0.1,
            "ann_return": 0.15,
            "ann_vol": 0.12,
            "cost_to_gross_pnl": 0.0002,
        },
        {
            "strategy_id": "tsmom",
            "model_id": "tsmom_v1",
            "sharpe": 0.7,
            "last_8_quarters_sharpe": 0.5,
            "max_drawdown": -0.12,
            "ann_return": 0.08,
            "ann_vol": 0.10,
        },
    ]).to_parquet(wf_dir / "aggregate_metrics.parquet", index=False)
    pd.DataFrame(
        [
            {
                "fold_id": f"fold_{idx}",
                "strategy_id": strategy,
                "model_id": "cpd_model" if strategy == "cpd_lstm" else "tsmom_v1",
                "net_return": 0.01,
            }
            for idx in range(8)
            for strategy in ["cpd_lstm", "tsmom"]
        ]
    ).to_parquet(wf_dir / "fold_metrics.parquet", index=False)
    pd.DataFrame([
        {
            "strategy_id": "cpd_lstm_minus_tsmom",
            "horizon_days": 5,
            "event_count": 25,
            "mean_diff_cpd_minus_tsmom": 0.01,
        },
        {
            "strategy_id": "cpd_lstm_minus_tsmom",
            "horizon_days": 20,
            "event_count": 25,
            "mean_diff_cpd_minus_tsmom": 0.02,
        },
    ]).to_parquet(wf_dir / "reversal_bucket_metrics.parquet", index=False)
    dates = list(pd.bdate_range("2024-01-02", periods=3).date)
    pd.DataFrame(
        [
            {
                "run_id": "wf_test",
                "fold_id": "fold_0",
                "strategy_id": strategy,
                "model_id": "cpd_model" if strategy == "cpd_lstm" else "tsmom_v1",
                "as_of_date": as_of_date,
                "root": root,
                "signal_raw": 0.5,
                "signal_clipped": 0.5,
                "is_valid": True,
                "invalid_reason": None,
                "feature_hash": f"{root}_{as_of_date}",
                "created_at_utc": "2026-04-23T00:00:00Z",
            }
            for as_of_date in dates
            for root in ["ES", "NQ"]
            for strategy in ["cpd_lstm", "tsmom"]
        ]
    ).to_parquet(wf_dir / "oos_signals_daily.parquet", index=False)
    pd.DataFrame([
        {
            "fold_id": "fold_0",
            "train_end": "2023-01-01",
            "val_start": "2023-01-02",
            "val_end": "2023-12-29",
            "oos_start": "2024-01-02",
            "status": "completed",
        }
    ]).to_parquet(wf_dir / "walkforward_windows.parquet", index=False)
    (wf_dir / "gates.json").write_bytes(canonical_json_bytes({"overall_status": "pass"}))
    (wf_dir / "manifest.json").write_bytes(canonical_json_bytes({"run_id": "wf_test"}))
    (model_dir / "model.pt").write_bytes(b"fake model bytes")
    (model_dir / "config.json").write_bytes(
        canonical_json_bytes({
            "model_family": "cpd_lstm_v1",
            "strategy_id": "cpd_lstm",
            "feature_set_id": "features_v1",
        })
    )
    (model_dir / "feature_order.json").write_bytes(canonical_json_bytes(["ret_1"]))
    (model_dir / "standardizer.json").write_bytes(canonical_json_bytes({"mean": [0.0]}))
    (model_dir / "metrics.json").write_bytes(canonical_json_bytes({"model_id": "cpd_model"}))
    (model_dir / "train_manifest.json").write_bytes(
        canonical_json_bytes({
            "model_id": "cpd_model",
            "feature_set_id": "features_v1",
            "artifact_sha256": "abc",
        })
    )
    (model_dir / "sha256sums.txt").write_text("abc  model.pt\n", encoding="utf-8")
    return wf_dir, model_dir
