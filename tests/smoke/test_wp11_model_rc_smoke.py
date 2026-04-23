from __future__ import annotations

import shutil
from pathlib import Path

import pandas as pd
from typer.testing import CliRunner

from cpdshadow.cli import app
from cpdshadow.ids import canonical_json_bytes


def test_wp11_model_rc_cli_smoke_offline(tmp_path: Path) -> None:
    shutil.copytree(Path("config"), tmp_path / "config")
    pass_wf, pass_model = _write_fixture(tmp_path / "pass", cpd_sharpe=1.10)
    fail_wf, fail_model = _write_fixture(tmp_path / "fail", cpd_sharpe=0.10)
    runner = CliRunner()

    result = runner.invoke(
        app,
        [
            "model-rc",
            "package",
            "--walkforward-dir",
            str(pass_wf.relative_to(tmp_path)),
            "--candidate-model-dir",
            str(pass_model.relative_to(tmp_path)),
            "--release-id",
            "rc_smoke",
            "--created-at-utc",
            "2026-04-23T00:00:00Z",
            "--output-dir",
            "artifacts/test_releases/model_rc",
            "--repo-root",
            str(tmp_path),
        ],
    )

    assert result.exit_code == 0, result.stdout
    release_dir = tmp_path / "artifacts" / "test_releases" / "model_rc" / "rc_smoke"
    assert (release_dir / "manifest.json").exists()
    decision = _read_json(release_dir / "promotion_decision.json")
    assert decision["decision"] == "shadow_candidate"
    assert decision["human_approval_required"] is True

    qa = runner.invoke(
        app,
        [
            "model-rc",
            "qa",
            "--release-dir",
            "artifacts/test_releases/model_rc/rc_smoke",
            "--repo-root",
            str(tmp_path),
        ],
    )
    assert qa.exit_code == 0, qa.stdout

    fail = runner.invoke(
        app,
        [
            "model-rc",
            "package",
            "--walkforward-dir",
            str(fail_wf.relative_to(tmp_path)),
            "--candidate-model-dir",
            str(fail_model.relative_to(tmp_path)),
            "--release-id",
            "rc_fail",
            "--created-at-utc",
            "2026-04-23T00:00:00Z",
            "--output-dir",
            "artifacts/test_releases/model_rc",
            "--repo-root",
            str(tmp_path),
        ],
    )
    assert fail.exit_code == 0, fail.stdout
    fail_decision = _read_json(
        tmp_path
        / "artifacts"
        / "test_releases"
        / "model_rc"
        / "rc_fail"
        / "promotion_decision.json"
    )
    assert fail_decision["decision"] != "shadow_candidate"


def _write_fixture(root: Path, *, cpd_sharpe: float) -> tuple[Path, Path]:
    wf_dir = root / "wf"
    model_dir = root / "model"
    wf_dir.mkdir(parents=True)
    model_dir.mkdir(parents=True)
    pd.DataFrame([
        {
            "strategy_id": "cpd_lstm",
            "model_id": "cpd_model",
            "sharpe": cpd_sharpe,
            "last_8_quarters_sharpe": 0.80,
            "max_drawdown": -0.10,
            "ann_return": 0.15,
            "ann_vol": 0.12,
            "cost_to_gross_pnl": 0.0002,
        },
        {
            "strategy_id": "tsmom",
            "model_id": "tsmom_v1",
            "sharpe": 0.70,
            "last_8_quarters_sharpe": 0.50,
            "max_drawdown": -0.12,
            "ann_return": 0.08,
            "ann_vol": 0.10,
        },
    ]).to_parquet(wf_dir / "aggregate_metrics.parquet", index=False)
    pd.DataFrame(
        [
            {"fold_id": f"fold_{idx}", "strategy_id": strategy, "net_return": 0.01}
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
    dates = list(pd.bdate_range("2024-01-02", periods=2).date)
    pd.DataFrame(
        [
            {
                "run_id": "wf_test",
                "fold_id": "fold_0",
                "strategy_id": strategy,
                "model_id": "cpd_model" if strategy == "cpd_lstm" else "tsmom_v1",
                "as_of_date": as_of_date,
                "root": "ES",
                "signal_raw": 0.5,
                "signal_clipped": 0.5,
                "is_valid": True,
                "invalid_reason": None,
                "feature_hash": f"ES_{as_of_date}",
                "created_at_utc": "2026-04-23T00:00:00Z",
            }
            for as_of_date in dates
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
        canonical_json_bytes({"model_id": "cpd_model", "feature_set_id": "features_v1"})
    )
    (model_dir / "sha256sums.txt").write_text("abc  model.pt\n", encoding="utf-8")
    return wf_dir, model_dir


def _read_json(path: Path) -> dict[str, object]:
    import json

    return json.loads(path.read_text(encoding="utf-8"))
