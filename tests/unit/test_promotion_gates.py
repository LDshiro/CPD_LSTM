from __future__ import annotations

from pathlib import Path

import pandas as pd

from cpdshadow.ids import canonical_json_bytes
from cpdshadow.model_release import (
    decide_promotion,
    evaluate_promotion_gates,
    load_model_release_gates,
    load_promotion_evidence,
)


def test_passing_evidence_produces_shadow_candidate(tmp_path: Path) -> None:
    wf_dir, model_dir = _write_fixture(tmp_path)
    config = load_model_release_gates(Path("config/model_release_gates.yml"))

    evidence = load_promotion_evidence(
        walkforward_dir=wf_dir,
        candidate_model_dir=model_dir,
        gates_config=config,
    )
    gates = evaluate_promotion_gates(evidence, config)
    decision = decide_promotion(evidence, gates, config)

    assert decision.decision == "shadow_candidate"
    assert decision.hard_gate_status == "pass"
    assert decision.reversal_gate_status == "pass"


def test_low_full_oos_sharpe_routes_to_tsmom_only(tmp_path: Path) -> None:
    wf_dir, model_dir = _write_fixture(tmp_path, cpd_sharpe=0.10)
    config = load_model_release_gates(Path("config/model_release_gates.yml"))

    evidence = load_promotion_evidence(
        walkforward_dir=wf_dir,
        candidate_model_dir=model_dir,
        gates_config=config,
    )
    gates = evaluate_promotion_gates(evidence, config)
    decision = decide_promotion(evidence, gates, config)

    assert decision.decision == "tsmom_only"


def test_low_recent_8q_sharpe_blocks_shadow_candidate(tmp_path: Path) -> None:
    wf_dir, model_dir = _write_fixture(tmp_path, recent_8q_sharpe=0.10)
    config = load_model_release_gates(Path("config/model_release_gates.yml"))

    evidence = load_promotion_evidence(
        walkforward_dir=wf_dir,
        candidate_model_dir=model_dir,
        gates_config=config,
    )
    gates = evaluate_promotion_gates(evidence, config)
    decision = decide_promotion(evidence, gates, config)

    assert decision.decision != "shadow_candidate"
    assert any(gate.gate_id == "min_recent_8q_net_sharpe" and gate.status == "fail" for gate in gates)


def test_excess_drawdown_blocks_shadow_candidate(tmp_path: Path) -> None:
    wf_dir, model_dir = _write_fixture(tmp_path, cpd_drawdown=-0.30, tsmom_drawdown=-0.10)
    config = load_model_release_gates(Path("config/model_release_gates.yml"))

    evidence = load_promotion_evidence(
        walkforward_dir=wf_dir,
        candidate_model_dir=model_dir,
        gates_config=config,
    )
    gates = evaluate_promotion_gates(evidence, config)
    decision = decide_promotion(evidence, gates, config)

    assert decision.decision != "shadow_candidate"
    assert any(
        gate.gate_id == "max_drawdown_vs_tsmom_multiple" and gate.status == "fail"
        for gate in gates
    )


def test_missing_feature_rate_above_threshold_blocks_shadow_candidate(tmp_path: Path) -> None:
    wf_dir, model_dir = _write_fixture(tmp_path, invalid_cpd_signal_rows=1)
    config = load_model_release_gates(Path("config/model_release_gates.yml"))

    evidence = load_promotion_evidence(
        walkforward_dir=wf_dir,
        candidate_model_dir=model_dir,
        gates_config=config,
    )
    gates = evaluate_promotion_gates(evidence, config)
    decision = decide_promotion(evidence, gates, config)

    assert decision.decision != "shadow_candidate"
    assert any(gate.gate_id == "max_missing_feature_rate" and gate.status == "fail" for gate in gates)


def test_feature_set_mismatch_rejects_candidate(tmp_path: Path) -> None:
    wf_dir, model_dir = _write_fixture(tmp_path, model_feature_set_id="wrong_features")
    config = load_model_release_gates(Path("config/model_release_gates.yml"))

    evidence = load_promotion_evidence(
        walkforward_dir=wf_dir,
        candidate_model_dir=model_dir,
        gates_config=config,
    )
    gates = evaluate_promotion_gates(evidence, config)
    decision = decide_promotion(evidence, gates, config)

    assert decision.decision == "reject_candidate"
    assert any(gate.gate_id == "require_feature_set_match" and gate.status == "fail" for gate in gates)


def test_insufficient_reversal_events_defers_research(tmp_path: Path) -> None:
    wf_dir, model_dir = _write_fixture(tmp_path, event_count=5)
    config = load_model_release_gates(Path("config/model_release_gates.yml"))

    evidence = load_promotion_evidence(
        walkforward_dir=wf_dir,
        candidate_model_dir=model_dir,
        gates_config=config,
    )
    decision = decide_promotion(evidence, evaluate_promotion_gates(evidence, config), config)

    assert decision.decision == "defer_research"
    assert decision.reversal_gate_status == "insufficient"


def test_reversal_underperformance_defers_research(tmp_path: Path) -> None:
    wf_dir, model_dir = _write_fixture(tmp_path, reversal_5d=-0.01)
    config = load_model_release_gates(Path("config/model_release_gates.yml"))

    evidence = load_promotion_evidence(
        walkforward_dir=wf_dir,
        candidate_model_dir=model_dir,
        gates_config=config,
    )
    decision = decide_promotion(evidence, evaluate_promotion_gates(evidence, config), config)

    assert decision.decision == "defer_research"
    assert decision.reversal_gate_status == "mixed"


def test_warning_gates_do_not_block_shadow_candidate(tmp_path: Path) -> None:
    wf_dir, model_dir = _write_fixture(tmp_path, n_folds=1)
    config = load_model_release_gates(Path("config/model_release_gates.yml"))

    evidence = load_promotion_evidence(
        walkforward_dir=wf_dir,
        candidate_model_dir=model_dir,
        gates_config=config,
    )
    gates = evaluate_promotion_gates(evidence, config)
    decision = decide_promotion(evidence, gates, config)

    assert any(gate.gate_id == "min_oos_quarters" and gate.status == "warn" for gate in gates)
    assert decision.decision == "shadow_candidate"


def _write_fixture(
    tmp_path: Path,
    *,
    cpd_sharpe: float = 1.10,
    recent_8q_sharpe: float = 0.80,
    cpd_drawdown: float = -0.10,
    tsmom_drawdown: float = -0.12,
    event_count: int = 25,
    reversal_5d: float = 0.01,
    reversal_20d: float = 0.02,
    model_feature_set_id: str = "features_v1",
    n_folds: int = 8,
    invalid_cpd_signal_rows: int = 0,
) -> tuple[Path, Path]:
    wf_dir = tmp_path / "wf"
    model_dir = tmp_path / "model"
    wf_dir.mkdir()
    model_dir.mkdir()
    pd.DataFrame([
        {
            "run_id": "wf_test",
            "strategy_id": "cpd_lstm",
            "model_id": "cpd_model",
            "sharpe": cpd_sharpe,
            "last_8_quarters_sharpe": recent_8q_sharpe,
            "max_drawdown": cpd_drawdown,
            "ann_return": 0.15,
            "ann_vol": 0.12,
            "cost_to_gross_pnl": 0.0002,
        },
        {
            "run_id": "wf_test",
            "strategy_id": "tsmom",
            "model_id": "tsmom_v1",
            "sharpe": 0.70,
            "last_8_quarters_sharpe": 0.50,
            "max_drawdown": tsmom_drawdown,
            "ann_return": 0.08,
            "ann_vol": 0.10,
        },
    ]).to_parquet(wf_dir / "aggregate_metrics.parquet", index=False)
    pd.DataFrame(
        [
            {
                "run_id": "wf_test",
                "fold_id": f"fold_{idx}",
                "strategy_id": strategy,
                "model_id": "cpd_model" if strategy == "cpd_lstm" else "tsmom_v1",
                "net_return": 0.01,
            }
            for idx in range(n_folds)
            for strategy in ["cpd_lstm", "tsmom"]
        ]
    ).to_parquet(wf_dir / "fold_metrics.parquet", index=False)
    pd.DataFrame([
        {
            "run_id": "wf_test",
            "fold_id": "fold_0",
            "root": "ES",
            "strategy_id": "cpd_lstm_minus_tsmom",
            "horizon_days": 5,
            "event_count": event_count,
            "mean_diff_cpd_minus_tsmom": reversal_5d,
        },
        {
            "run_id": "wf_test",
            "fold_id": "fold_0",
            "root": "ES",
            "strategy_id": "cpd_lstm_minus_tsmom",
            "horizon_days": 20,
            "event_count": event_count,
            "mean_diff_cpd_minus_tsmom": reversal_20d,
        },
    ]).to_parquet(wf_dir / "reversal_bucket_metrics.parquet", index=False)
    dates = list(pd.bdate_range("2024-01-02", periods=3).date)
    root_dates = [(as_of_date, root) for as_of_date in dates for root in ["ES", "NQ"]]
    invalid_keys = set(root_dates[:invalid_cpd_signal_rows])
    signal_rows = []
    for as_of_date, root in root_dates:
        invalid_pair = (as_of_date, root) in invalid_keys
        for strategy in ["cpd_lstm", "tsmom"]:
            signal_rows.append({
                "run_id": "wf_test",
                "fold_id": "fold_0",
                "strategy_id": strategy,
                "model_id": "cpd_model" if strategy == "cpd_lstm" else "tsmom_v1",
                "as_of_date": as_of_date,
                "root": root,
                "signal_raw": 0.5,
                "signal_clipped": 0.5,
                "is_valid": not invalid_pair,
                "invalid_reason": "missing_feature" if invalid_pair else None,
                "feature_hash": f"{root}_{as_of_date}",
                "created_at_utc": "2026-04-23T00:00:00Z",
                "feature_set_id": "features_v1",
            })
    pd.DataFrame(signal_rows).to_parquet(wf_dir / "oos_signals_daily.parquet", index=False)
    pd.DataFrame([
        {
            "run_id": "wf_test",
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
            "feature_set_id": model_feature_set_id,
        })
    )
    (model_dir / "feature_order.json").write_bytes(canonical_json_bytes(["ret_1"]))
    (model_dir / "standardizer.json").write_bytes(canonical_json_bytes({"mean": [0.0]}))
    (model_dir / "metrics.json").write_bytes(canonical_json_bytes({"model_id": "cpd_model"}))
    (model_dir / "train_manifest.json").write_bytes(
        canonical_json_bytes({
            "model_id": "cpd_model",
            "feature_set_id": model_feature_set_id,
            "artifact_sha256": "abc",
        })
    )
    (model_dir / "sha256sums.txt").write_text("abc  model.pt\n", encoding="utf-8")
    return wf_dir, model_dir
