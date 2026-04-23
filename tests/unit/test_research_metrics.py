from __future__ import annotations

from datetime import date

import pandas as pd

from cpdshadow.research.metrics import (
    compute_aggregate_metrics,
    compute_fold_metrics,
    moving_block_bootstrap_ci,
)


def _pnl() -> pd.DataFrame:
    dates = list(pd.bdate_range("2024-01-02", periods=6).date)
    rows = []
    for strategy_id, model_id, returns in [
        ("cpd_lstm", "cpd_fold", [0.01, -0.005, 0.004, 0.002, -0.001, 0.006]),
        ("tsmom", "tsmom_v1", [0.002, -0.004, 0.001, 0.001, -0.002, 0.002]),
    ]:
        for as_of_date, net_return in zip(dates, returns, strict=True):
            rows.append({
                "run_id": "wf_test",
                "fold_id": "fold_1",
                "strategy_id": strategy_id,
                "model_id": model_id,
                "as_of_date": as_of_date,
                "next_date": as_of_date,
                "root": "__PORTFOLIO__",
                "gross_pnl_usd": net_return * 1_000_000 + 10.0,
                "modeled_cost_usd": 10.0,
                "net_pnl_usd": net_return * 1_000_000,
                "nav_usd": 1_000_000.0,
                "net_return": net_return,
            })
    return pd.DataFrame(rows)


def _targets() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "run_id": "wf_test",
                "fold_id": "fold_1",
                "strategy_id": strategy_id,
                "model_id": model_id,
                "as_of_date": date(2024, 1, 2),
                "root": "ES",
                "signal": 1.0,
                "target_contracts": 1,
                "turnover_contracts": 1,
                "ex_ante_annualized_dollar_risk": 10_000.0,
            }
            for strategy_id, model_id in [("cpd_lstm", "cpd_fold"), ("tsmom", "tsmom_v1")]
        ]
    )


def test_fold_and_aggregate_metrics_include_strategy_comparison() -> None:
    fold_metrics = compute_fold_metrics(pnl_daily=_pnl(), targets_daily=_targets())
    aggregate, warnings = compute_aggregate_metrics(
        pnl_daily=_pnl(),
        fold_metrics=fold_metrics,
        seed=7,
        block_length_days=2,
        n_bootstrap_samples=20,
        min_bootstrap_observations=3,
    )

    assert set(fold_metrics["strategy_id"]) == {"cpd_lstm", "tsmom"}
    assert "cpd_lstm_minus_tsmom" in set(aggregate["strategy_id"])
    assert warnings == []
    comparison = aggregate[aggregate["strategy_id"] == "cpd_lstm_minus_tsmom"].iloc[0]
    assert comparison["mean_daily_return_diff"] > 0
    assert pd.notna(comparison["bootstrap_ci_low"])


def test_moving_block_bootstrap_is_deterministic() -> None:
    values = [0.01, -0.02, 0.03, 0.04, -0.01, 0.02]

    first = moving_block_bootstrap_ci(
        values,
        seed=42,
        block_length_days=2,
        n_bootstrap_samples=50,
    )
    second = moving_block_bootstrap_ci(
        values,
        seed=42,
        block_length_days=2,
        n_bootstrap_samples=50,
    )

    assert first == second

