from __future__ import annotations

from math import sqrt
from typing import Sequence

import numpy as np
import pandas as pd


ANNUALIZATION_DAYS = 252.0


def compute_return_metrics(returns: pd.Series) -> dict[str, float | int | None]:
    clean = pd.to_numeric(returns, errors="coerce").dropna().astype(float)
    if clean.empty:
        return _empty_metrics()
    equity = (1.0 + clean).cumprod()
    drawdown = equity / equity.cummax() - 1.0
    downside = clean[clean < 0]
    ann_return = float(clean.mean() * ANNUALIZATION_DAYS)
    ann_vol = float(clean.std(ddof=0) * sqrt(ANNUALIZATION_DAYS))
    downside_vol = float(downside.std(ddof=0) * sqrt(ANNUALIZATION_DAYS)) if len(downside) else 0.0
    max_drawdown = float(drawdown.min()) if not drawdown.empty else 0.0
    return {
        "n_days": int(len(clean)),
        "ann_return": ann_return,
        "ann_vol": ann_vol,
        "sharpe": _safe_divide(ann_return, ann_vol),
        "sortino": _safe_divide(ann_return, downside_vol),
        "max_drawdown": max_drawdown,
        "calmar": _safe_divide(ann_return, abs(max_drawdown)),
        "hit_rate": float((clean > 0).mean()),
        "skew": float(clean.skew()) if len(clean) >= 3 else None,
        "kurtosis": float(clean.kurtosis()) if len(clean) >= 4 else None,
        "net_return": float(equity.iloc[-1] - 1.0),
    }


def compute_fold_metrics(
    *,
    pnl_daily: pd.DataFrame,
    targets_daily: pd.DataFrame,
) -> pd.DataFrame:
    if pnl_daily.empty or "root" not in pnl_daily.columns:
        return pd.DataFrame(columns=_FOLD_METRIC_COLUMNS)
    rows: list[dict[str, object]] = []
    portfolio = pnl_daily[pnl_daily["root"] == "__PORTFOLIO__"].copy()
    for (run_id, fold_id, strategy_id, model_id), group in portfolio.groupby(
        ["run_id", "fold_id", "strategy_id", "model_id"],
        sort=True,
    ):
        metrics = compute_return_metrics(group["net_return"])
        if targets_daily.empty or "run_id" not in targets_daily.columns:
            strategy_targets = pd.DataFrame()
        else:
            strategy_targets = targets_daily[
                (targets_daily["run_id"] == run_id)
                & (targets_daily["fold_id"] == fold_id)
                & (targets_daily["strategy_id"] == strategy_id)
                & (targets_daily["model_id"] == model_id)
            ]
        gross_pnl = float(pd.to_numeric(group["gross_pnl_usd"], errors="coerce").sum())
        cost_usd = float(pd.to_numeric(group["modeled_cost_usd"], errors="coerce").sum())
        rows.append({
            "run_id": run_id,
            "fold_id": fold_id,
            "strategy_id": strategy_id,
            "model_id": model_id,
            **metrics,
            "gross_return": _safe_divide(gross_pnl, float(group["nav_usd"].iloc[0])),
            "cost_usd": cost_usd,
            "cost_to_gross_pnl": _safe_divide(cost_usd, abs(gross_pnl)),
            "avg_daily_turnover_contracts": _mean_abs(
                strategy_targets.get("turnover_contracts")
            ),
            "avg_abs_signal": _mean_abs(strategy_targets.get("signal")),
            "avg_abs_contracts": _mean_abs(strategy_targets.get("target_contracts")),
            "max_abs_contracts": _max_abs(strategy_targets.get("target_contracts")),
            "max_ex_ante_annualized_risk_pct_nav": _safe_divide(
                _max_abs(strategy_targets.get("ex_ante_annualized_dollar_risk")),
                float(group["nav_usd"].iloc[0]),
            ),
        })
    return pd.DataFrame(rows, columns=_FOLD_METRIC_COLUMNS)


def compute_aggregate_metrics(
    *,
    pnl_daily: pd.DataFrame,
    fold_metrics: pd.DataFrame,
    seed: int,
    block_length_days: int,
    n_bootstrap_samples: int,
    min_bootstrap_observations: int,
) -> tuple[pd.DataFrame, list[str]]:
    warnings: list[str] = []
    if pnl_daily.empty or "root" not in pnl_daily.columns:
        return pd.DataFrame(columns=_AGGREGATE_METRIC_COLUMNS), [
            "aggregate metrics skipped because pnl_daily is empty"
        ]
    rows: list[dict[str, object]] = []
    portfolio = pnl_daily[pnl_daily["root"] == "__PORTFOLIO__"].copy()
    for (run_id, strategy_id, model_id), group in portfolio.groupby(
        ["run_id", "strategy_id", "model_id"],
        sort=True,
    ):
        group = group.sort_values("as_of_date", kind="stable")
        metrics = compute_return_metrics(group["net_return"])
        rows.append({
            "run_id": run_id,
            "strategy_id": strategy_id,
            "model_id": model_id,
            "full_oos_start": _min_date(group, "as_of_date"),
            "full_oos_end": _max_date(group, "as_of_date"),
            "n_folds": int(group["fold_id"].nunique()),
            **metrics,
            "cost_to_gross_pnl": _safe_divide(
                float(group["modeled_cost_usd"].sum()),
                abs(float(group["gross_pnl_usd"].sum())),
            ),
            "last_8_quarters_sharpe": _last_n_fold_sharpe(
                fold_metrics=fold_metrics,
                strategy_id=strategy_id,
                n=8,
            ),
        })

    comparison = _comparison_row(
        portfolio=portfolio,
        seed=seed,
        block_length_days=block_length_days,
        n_bootstrap_samples=n_bootstrap_samples,
        min_bootstrap_observations=min_bootstrap_observations,
    )
    if comparison.warning is not None:
        warnings.append(comparison.warning)
    if comparison.row:
        rows.append(comparison.row)
    return pd.DataFrame(rows, columns=_AGGREGATE_METRIC_COLUMNS), warnings


class _ComparisonResult:
    def __init__(self, row: dict[str, object] | None, warning: str | None = None) -> None:
        self.row = row
        self.warning = warning


def _comparison_row(
    *,
    portfolio: pd.DataFrame,
    seed: int,
    block_length_days: int,
    n_bootstrap_samples: int,
    min_bootstrap_observations: int,
) -> _ComparisonResult:
    if portfolio.empty:
        return _ComparisonResult(None, "comparison skipped because portfolio returns are empty")
    cpd = portfolio[portfolio["strategy_id"] == "cpd_lstm"][
        ["as_of_date", "net_return"]
    ].rename(columns={"net_return": "cpd"})
    tsmom = portfolio[portfolio["strategy_id"] == "tsmom"][
        ["as_of_date", "net_return"]
    ].rename(columns={"net_return": "tsmom"})
    merged = cpd.merge(tsmom, on="as_of_date", how="inner").sort_values("as_of_date")
    if merged.empty:
        return _ComparisonResult(None, "comparison skipped because no paired strategy dates exist")
    diff = (merged["cpd"] - merged["tsmom"]).astype(float).to_numpy()
    mean_diff = float(np.mean(diff))
    std_diff = float(np.std(diff, ddof=0))
    t_stat = None if std_diff == 0.0 else float(mean_diff / (std_diff / sqrt(len(diff))))
    ci_low: float | None = None
    ci_high: float | None = None
    warning: str | None = None
    if len(diff) >= min_bootstrap_observations and n_bootstrap_samples > 0:
        ci_low, ci_high = moving_block_bootstrap_ci(
            diff,
            seed=seed,
            block_length_days=block_length_days,
            n_bootstrap_samples=n_bootstrap_samples,
        )
    else:
        warning = "bootstrap CI skipped because paired observations are below threshold"
    return _ComparisonResult(
        {
            "run_id": str(portfolio["run_id"].iloc[0]),
            "strategy_id": "cpd_lstm_minus_tsmom",
            "model_id": "comparison",
            "full_oos_start": _min_date(merged, "as_of_date"),
            "full_oos_end": _max_date(merged, "as_of_date"),
            "n_folds": int(portfolio["fold_id"].nunique()),
            "n_days": int(len(diff)),
            "mean_daily_return_diff": mean_diff,
            "ann_return_diff": float(mean_diff * ANNUALIZATION_DAYS),
            "sharpe_diff": None,
            "paired_t_stat_daily_diff": t_stat,
            "bootstrap_ci_low": ci_low,
            "bootstrap_ci_high": ci_high,
        },
        warning=warning,
    )


def moving_block_bootstrap_ci(
    values: Sequence[float],
    *,
    seed: int,
    block_length_days: int,
    n_bootstrap_samples: int,
) -> tuple[float, float]:
    array = np.asarray(values, dtype=float)
    rng = np.random.default_rng(seed)
    means = []
    n = len(array)
    for _ in range(n_bootstrap_samples):
        sample: list[float] = []
        while len(sample) < n:
            start = int(rng.integers(0, max(1, n - block_length_days + 1)))
            sample.extend(array[start : start + block_length_days].tolist())
        means.append(float(np.mean(sample[:n])))
    return float(np.quantile(means, 0.025)), float(np.quantile(means, 0.975))


def _last_n_fold_sharpe(*, fold_metrics: pd.DataFrame, strategy_id: str, n: int) -> float | None:
    if fold_metrics.empty or "strategy_id" not in fold_metrics.columns:
        return None
    selected = fold_metrics[fold_metrics["strategy_id"] == strategy_id].sort_values("fold_id")
    if selected.empty:
        return None
    return float(pd.to_numeric(selected.tail(n)["sharpe"], errors="coerce").mean())


def _empty_metrics() -> dict[str, float | int | None]:
    return {
        "n_days": 0,
        "ann_return": None,
        "ann_vol": None,
        "sharpe": None,
        "sortino": None,
        "max_drawdown": None,
        "calmar": None,
        "hit_rate": None,
        "skew": None,
        "kurtosis": None,
        "net_return": None,
    }


def _safe_divide(numerator: float | int | None, denominator: float | int | None) -> float | None:
    if numerator is None or denominator is None or float(denominator) == 0.0:
        return None
    return float(numerator) / float(denominator)


def _mean_abs(series: pd.Series | None) -> float | None:
    if series is None:
        return None
    values = pd.to_numeric(series, errors="coerce").dropna()
    return float(values.abs().mean()) if not values.empty else None


def _max_abs(series: pd.Series | None) -> float | None:
    if series is None:
        return None
    values = pd.to_numeric(series, errors="coerce").dropna()
    return float(values.abs().max()) if not values.empty else None


def _min_date(df: pd.DataFrame, column: str) -> str | None:
    if df.empty:
        return None
    return pd.to_datetime(df[column]).dt.date.min().isoformat()


def _max_date(df: pd.DataFrame, column: str) -> str | None:
    if df.empty:
        return None
    return pd.to_datetime(df[column]).dt.date.max().isoformat()


_FOLD_METRIC_COLUMNS = [
    "run_id",
    "fold_id",
    "strategy_id",
    "model_id",
    "n_days",
    "ann_return",
    "ann_vol",
    "sharpe",
    "sortino",
    "max_drawdown",
    "calmar",
    "hit_rate",
    "skew",
    "kurtosis",
    "net_return",
    "gross_return",
    "cost_usd",
    "cost_to_gross_pnl",
    "avg_daily_turnover_contracts",
    "avg_abs_signal",
    "avg_abs_contracts",
    "max_abs_contracts",
    "max_ex_ante_annualized_risk_pct_nav",
]

_AGGREGATE_METRIC_COLUMNS = [
    "run_id",
    "strategy_id",
    "model_id",
    "full_oos_start",
    "full_oos_end",
    "n_folds",
    "n_days",
    "ann_return",
    "ann_vol",
    "sharpe",
    "sortino",
    "max_drawdown",
    "calmar",
    "hit_rate",
    "skew",
    "kurtosis",
    "net_return",
    "cost_to_gross_pnl",
    "last_8_quarters_sharpe",
    "mean_daily_return_diff",
    "ann_return_diff",
    "sharpe_diff",
    "paired_t_stat_daily_diff",
    "bootstrap_ci_low",
    "bootstrap_ci_high",
]
