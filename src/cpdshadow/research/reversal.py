from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

import pandas as pd

from cpdshadow.config import WalkforwardReversalBucketConfig


@dataclass(frozen=True)
class ReversalThreshold:
    fold_id: str
    root: str
    threshold_21: float | None
    threshold_63: float | None
    raw_event_count: int
    deduped_event_count: int


def find_reversal_events(
    *,
    features_daily: pd.DataFrame,
    windows: pd.DataFrame,
    config: WalkforwardReversalBucketConfig,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    features = features_daily.copy()
    features["as_of_date"] = pd.to_datetime(features["as_of_date"]).dt.date
    events: list[dict[str, object]] = []
    threshold_rows: list[dict[str, object]] = []
    for _, window in windows.iterrows():
        if str(window["status"]) == "skipped":
            continue
        fold_id = str(window["fold_id"])
        for root in _roots_from_window(window):
            root_features = features[features["root"].astype(str) == root].sort_values(
                "as_of_date", kind="stable"
            )
            train_val = root_features[
                (root_features["as_of_date"] >= window["train_start"])
                & (root_features["as_of_date"] <= window["val_end"])
            ]
            oos = root_features[
                (root_features["as_of_date"] >= window["oos_start"])
                & (root_features["as_of_date"] <= window["oos_end"])
            ]
            threshold_21 = _quantile(train_val.get("cpd21_score"), config.threshold_quantile)
            threshold_63 = _quantile(train_val.get("cpd63_score"), config.threshold_quantile)
            raw_candidates, deduped = _event_candidates_with_cooldown(
                oos=oos,
                threshold_21=threshold_21,
                threshold_63=threshold_63,
                cooldown_days=config.cooldown_days,
            )
            threshold_rows.append({
                "fold_id": fold_id,
                "root": root,
                "threshold_21": threshold_21,
                "threshold_63": threshold_63,
                "raw_event_count": int(len(raw_candidates)),
                "deduped_event_count": int(len(deduped)),
            })
            for event_index, event_row in enumerate(deduped):
                event_date = pd.Timestamp(event_row["as_of_date"]).date()
                events.append({
                    "run_id": window["run_id"],
                    "fold_id": fold_id,
                    "root": root,
                    "event_id": f"{fold_id}_{root}_{event_date.isoformat()}",
                    "event_index": event_index,
                    "as_of_date": event_date,
                    "cpd21_score": event_row.get("cpd21_score"),
                    "cpd63_score": event_row.get("cpd63_score"),
                    "threshold_21": threshold_21,
                    "threshold_63": threshold_63,
                    "cooldown_days": config.cooldown_days,
                })
    return (
        pd.DataFrame(events, columns=_REVERSAL_EVENT_COLUMNS),
        pd.DataFrame(threshold_rows, columns=_REVERSAL_THRESHOLD_COLUMNS),
    )


def compute_reversal_bucket_metrics(
    *,
    events: pd.DataFrame,
    pnl_daily: pd.DataFrame,
    horizons: Sequence[int],
) -> pd.DataFrame:
    if events.empty or pnl_daily.empty:
        return pd.DataFrame(columns=_REVERSAL_BUCKET_METRIC_COLUMNS)
    root_pnl = pnl_daily[pnl_daily["root"] != "__PORTFOLIO__"].copy()
    root_pnl["as_of_date"] = pd.to_datetime(root_pnl["as_of_date"]).dt.date
    event_returns: list[dict[str, object]] = []
    for _, event in events.iterrows():
        for horizon in horizons:
            for strategy_id, strategy_group in root_pnl.groupby("strategy_id", sort=True):
                selected = strategy_group[
                    (strategy_group["fold_id"] == event["fold_id"])
                    & (strategy_group["root"] == event["root"])
                    & (strategy_group["as_of_date"] >= event["as_of_date"])
                ].sort_values("as_of_date", kind="stable").head(int(horizon))
                if selected.empty:
                    continue
                event_returns.append({
                    "run_id": event["run_id"],
                    "fold_id": event["fold_id"],
                    "root": event["root"],
                    "event_id": event["event_id"],
                    "strategy_id": strategy_id,
                    "horizon_days": int(horizon),
                    "event_net_return": float(selected["net_return"].sum()),
                    "event_net_pnl_usd": float(selected["net_pnl_usd"].sum()),
                })
    event_returns_df = pd.DataFrame(event_returns)
    if event_returns_df.empty:
        return pd.DataFrame(columns=_REVERSAL_BUCKET_METRIC_COLUMNS)
    rows: list[dict[str, object]] = []
    for key, group in event_returns_df.groupby(
        ["run_id", "fold_id", "root", "strategy_id", "horizon_days"],
        sort=True,
    ):
        run_id, fold_id, root, strategy_id, horizon = key
        rows.append({
            "run_id": run_id,
            "fold_id": fold_id,
            "root": root,
            "strategy_id": strategy_id,
            "horizon_days": int(horizon),
            "event_count": int(group["event_id"].nunique()),
            "mean_event_net_return": float(group["event_net_return"].mean()),
            "median_event_net_return": float(group["event_net_return"].median()),
            "hit_rate_event": float((group["event_net_return"] > 0).mean()),
            "mean_event_net_pnl_usd": float(group["event_net_pnl_usd"].mean()),
        })
    rows.extend(_comparison_rows(event_returns_df))
    return pd.DataFrame(rows, columns=_REVERSAL_BUCKET_METRIC_COLUMNS)


def _comparison_rows(event_returns: pd.DataFrame) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    cpd = event_returns[event_returns["strategy_id"] == "cpd_lstm"]
    tsmom = event_returns[event_returns["strategy_id"] == "tsmom"]
    merged = cpd.merge(
        tsmom,
        on=["run_id", "fold_id", "root", "event_id", "horizon_days"],
        suffixes=("_cpd", "_tsmom"),
    )
    for key, group in merged.groupby(["run_id", "fold_id", "root", "horizon_days"], sort=True):
        run_id, fold_id, root, horizon = key
        diff = group["event_net_return_cpd"] - group["event_net_return_tsmom"]
        rows.append({
            "run_id": run_id,
            "fold_id": fold_id,
            "root": root,
            "strategy_id": "cpd_lstm_minus_tsmom",
            "horizon_days": int(horizon),
            "event_count": int(len(group)),
            "mean_diff_cpd_minus_tsmom": float(diff.mean()),
            "median_diff_cpd_minus_tsmom": float(diff.median()),
            "pct_events_cpd_outperforms": float((diff > 0).mean()),
        })
    return rows


def _event_candidates_with_cooldown(
    *,
    oos: pd.DataFrame,
    threshold_21: float | None,
    threshold_63: float | None,
    cooldown_days: int,
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    raw_candidates: list[dict[str, object]] = []
    deduped: list[dict[str, object]] = []
    cooldown_remaining = 0
    for _, row in oos.iterrows():
        score_21 = _float_or_none(row.get("cpd21_score"))
        score_63 = _float_or_none(row.get("cpd63_score"))
        triggered_21 = threshold_21 is not None and score_21 is not None and score_21 >= threshold_21
        triggered_63 = threshold_63 is not None and score_63 is not None and score_63 >= threshold_63
        if triggered_21 or triggered_63:
            raw_candidates.append(row.to_dict())
        if cooldown_remaining > 0:
            cooldown_remaining -= 1
            continue
        if triggered_21 or triggered_63:
            deduped.append(row.to_dict())
            cooldown_remaining = cooldown_days
    return raw_candidates, deduped


def _roots_from_window(window: pd.Series) -> list[str]:
    roots_value = window.get("roots")
    if isinstance(roots_value, list):
        return [str(root) for root in roots_value]
    return [item.strip() for item in str(roots_value).split(",") if item.strip()]


def _quantile(series: pd.Series | None, quantile: float) -> float | None:
    if series is None:
        return None
    values = pd.to_numeric(series, errors="coerce").dropna()
    return float(values.quantile(quantile)) if not values.empty else None


def _float_or_none(value: object) -> float | None:
    if value is None or pd.isna(value):
        return None
    return float(value)


_REVERSAL_EVENT_COLUMNS = [
    "run_id",
    "fold_id",
    "root",
    "event_id",
    "event_index",
    "as_of_date",
    "cpd21_score",
    "cpd63_score",
    "threshold_21",
    "threshold_63",
    "cooldown_days",
]

_REVERSAL_THRESHOLD_COLUMNS = [
    "fold_id",
    "root",
    "threshold_21",
    "threshold_63",
    "raw_event_count",
    "deduped_event_count",
]

_REVERSAL_BUCKET_METRIC_COLUMNS = [
    "run_id",
    "fold_id",
    "root",
    "strategy_id",
    "horizon_days",
    "event_count",
    "mean_event_net_return",
    "median_event_net_return",
    "hit_rate_event",
    "mean_event_net_pnl_usd",
    "mean_diff_cpd_minus_tsmom",
    "median_diff_cpd_minus_tsmom",
    "pct_events_cpd_outperforms",
]
