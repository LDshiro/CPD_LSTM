from __future__ import annotations

from datetime import UTC, datetime

import pandas as pd

from cpdshadow.config import WalkforwardGatesConfig


def evaluate_readiness_gates(
    *,
    run_id: str,
    aggregate_metrics: pd.DataFrame,
    fold_metrics: pd.DataFrame,
    reversal_bucket_metrics: pd.DataFrame,
    config: WalkforwardGatesConfig,
) -> dict[str, object]:
    gates: list[dict[str, object]] = []
    cpd = _strategy_row(aggregate_metrics, "cpd_lstm")
    tsmom = _strategy_row(aggregate_metrics, "tsmom")
    comparison = _strategy_row(aggregate_metrics, "cpd_lstm_minus_tsmom")
    gates.append(_min_gate(
        "full_oos_net_sharpe_min",
        _value(cpd, "sharpe"),
        config.full_oos_net_sharpe_min,
    ))
    gates.append(_min_gate(
        "last_8_quarters_net_sharpe_min",
        _value(cpd, "last_8_quarters_sharpe"),
        config.last_8_quarters_net_sharpe_min,
    ))
    gates.append(_drawdown_gate(
        observed_cpd=_value(cpd, "max_drawdown"),
        observed_tsmom=_value(tsmom, "max_drawdown"),
        threshold=config.max_drawdown_vs_tsmom_max_multiple,
    ))
    gates.append(_min_gate(
        "reversal_5d_diff_min",
        _reversal_diff(reversal_bucket_metrics, horizon=5),
        config.reversal_5d_diff_min,
    ))
    gates.append(_min_gate(
        "reversal_20d_diff_min",
        _reversal_diff(reversal_bucket_metrics, horizon=20),
        config.reversal_20d_diff_min,
    ))
    gates.append(_max_gate(
        "cost_to_gross_pnl_max",
        _value(cpd, "cost_to_gross_pnl"),
        config.cost_to_gross_pnl_max,
    ))
    if fold_metrics.empty or not {"strategy_id", "fold_id"}.issubset(fold_metrics.columns):
        completed_folds = 0
    else:
        completed_folds = int(
            fold_metrics.loc[fold_metrics["strategy_id"] == "cpd_lstm", "fold_id"].nunique()
        )
    gates.append(_min_gate("min_completed_folds", completed_folds, config.min_completed_folds))
    if comparison:
        gates.append({
            "gate_id": "cpd_lstm_minus_tsmom_observed",
            "status": "warning",
            "observed": comparison.get("mean_daily_return_diff"),
            "threshold": 0.0,
            "details": "Comparison row is informational; strategy promotion is not done in WP10.",
        })
    overall = "pass"
    if any(gate["status"] == "fail" for gate in gates):
        overall = "fail"
    elif any(gate["status"] == "warning" for gate in gates):
        overall = "warning"
    return {
        "run_id": run_id,
        "as_of_utc": datetime.now(UTC).isoformat(),
        "overall_status": overall,
        "gates": gates,
    }


def _first_row(df: pd.DataFrame) -> dict[str, object]:
    if df.empty:
        return {}
    return df.iloc[0].to_dict()


def _strategy_row(df: pd.DataFrame, strategy_id: str) -> dict[str, object]:
    if df.empty or "strategy_id" not in df.columns:
        return {}
    return _first_row(df[df["strategy_id"] == strategy_id])


def _value(row: dict[str, object], key: str) -> float | None:
    value = row.get(key)
    if value is None or pd.isna(value):
        return None
    return float(value)


def _min_gate(
    gate_id: str,
    observed: float | int | None,
    threshold: float | int,
) -> dict[str, object]:
    if observed is None:
        status = "warning"
        details = "Observed value is unavailable."
    else:
        status = "pass" if float(observed) >= float(threshold) else "fail"
        details = "Minimum threshold check."
    return {
        "gate_id": gate_id,
        "status": status,
        "observed": observed,
        "threshold": threshold,
        "details": details,
    }


def _max_gate(
    gate_id: str,
    observed: float | int | None,
    threshold: float | int,
) -> dict[str, object]:
    if observed is None:
        status = "warning"
        details = "Observed value is unavailable."
    else:
        status = "pass" if float(observed) <= float(threshold) else "fail"
        details = "Maximum threshold check."
    return {
        "gate_id": gate_id,
        "status": status,
        "observed": observed,
        "threshold": threshold,
        "details": details,
    }


def _drawdown_gate(
    *,
    observed_cpd: float | None,
    observed_tsmom: float | None,
    threshold: float,
) -> dict[str, object]:
    if observed_cpd is None or observed_tsmom is None:
        observed = None
        status = "warning"
    else:
        denominator = abs(observed_tsmom) if observed_tsmom != 0 else 1.0e-12
        observed = abs(observed_cpd) / denominator
        status = "pass" if observed <= threshold else "fail"
    return {
        "gate_id": "max_drawdown_vs_tsmom_max_multiple",
        "status": status,
        "observed": observed,
        "threshold": threshold,
        "details": "CPD-LSTM drawdown multiple relative to TSMOM.",
    }


def _reversal_diff(metrics: pd.DataFrame, *, horizon: int) -> float | None:
    if metrics.empty or not {"strategy_id", "horizon_days"}.issubset(metrics.columns):
        return None
    selected = metrics[
        (metrics["strategy_id"] == "cpd_lstm_minus_tsmom")
        & (metrics["horizon_days"].astype(int) == horizon)
    ]
    if selected.empty or "mean_diff_cpd_minus_tsmom" not in selected.columns:
        return None
    values = pd.to_numeric(selected["mean_diff_cpd_minus_tsmom"], errors="coerce").dropna()
    return float(values.mean()) if not values.empty else None
