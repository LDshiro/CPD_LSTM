from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from math import exp, floor, isfinite, sqrt
from typing import Sequence

import numpy as np
import pandas as pd

from cpdshadow.config import FeaturesConfig


CPD_DAILY_COLUMNS = [
    "feature_set_id",
    "as_of_date",
    "root",
    "cpd_window_days",
    "cpd_score",
    "cpd_age_days",
    "cpd_location_index",
    "cpd_is_valid",
    "cpd_method",
    "builder_version",
    "snapshot_id",
]


@dataclass(frozen=True)
class CpdResult:
    cpd_score: float | None
    cpd_age_days: int | None
    cpd_location_index: int | None
    cpd_is_valid: bool
    cpd_method: str


def compute_two_sample_t_cpd(
    window_values: Sequence[float],
    window_days: int,
    min_segment_days: int,
    min_segment_fraction: float,
    epsilon: float,
    score_min: float,
    score_max: float,
) -> CpdResult:
    values = np.asarray(window_values, dtype=float)
    if values.shape[0] != window_days or not np.isfinite(values).all():
        return CpdResult(
            cpd_score=None,
            cpd_age_days=None,
            cpd_location_index=None,
            cpd_is_valid=False,
            cpd_method="two_sample_t_v1",
        )

    min_seg = max(int(min_segment_days), int(floor(min_segment_fraction * window_days)))
    latest_split = window_days - min_seg
    if min_seg < 1 or min_seg > latest_split:
        return CpdResult(
            cpd_score=None,
            cpd_age_days=None,
            cpd_location_index=None,
            cpd_is_valid=False,
            cpd_method="two_sample_t_v1",
        )

    best_stat = float("-inf")
    best_split: int | None = None
    for split in range(min_seg, latest_split + 1):
        left = values[:split]
        right = values[split:]
        var_left = float(np.var(left, ddof=1))
        var_right = float(np.var(right, ddof=1))
        denom = sqrt((var_left / split) + (var_right / (window_days - split)) + epsilon)
        t_stat = abs(float(np.mean(right)) - float(np.mean(left))) / denom
        if t_stat >= best_stat:
            best_stat = t_stat
            best_split = split

    if best_split is None or not isfinite(best_stat):
        return CpdResult(
            cpd_score=None,
            cpd_age_days=None,
            cpd_location_index=None,
            cpd_is_valid=False,
            cpd_method="two_sample_t_v1",
        )

    score = 1.0 - exp(-0.5 * (best_stat ** 2))
    score = min(max(score, score_min), score_max)
    return CpdResult(
        cpd_score=score,
        cpd_age_days=window_days - best_split,
        cpd_location_index=best_split,
        cpd_is_valid=True,
        cpd_method="two_sample_t_v1",
    )


def build_cpd_daily_for_root(
    *,
    root: str,
    source_df: pd.DataFrame,
    config: FeaturesConfig,
    feature_set_id: str,
    snapshot_id: str,
    start_date: date,
    end_date: date,
) -> pd.DataFrame:
    if source_df.empty:
        return pd.DataFrame(columns=CPD_DAILY_COLUMNS)

    df = source_df.copy()
    df["as_of_date"] = pd.to_datetime(df["as_of_date"]).dt.date
    df = df.sort_values(["as_of_date"], kind="stable").reset_index(drop=True)
    windows = list(config.cpd.windows)
    rows: list[dict[str, object]] = []

    for idx, row in df.iterrows():
        as_of_date = row["as_of_date"]
        if as_of_date < start_date or as_of_date > end_date:
            continue
        for window in windows:
            if idx + 1 < window:
                result = CpdResult(
                    cpd_score=None,
                    cpd_age_days=None,
                    cpd_location_index=None,
                    cpd_is_valid=False,
                    cpd_method=config.cpd.method,
                )
            else:
                window_values = (
                    pd.to_numeric(df.loc[idx - window + 1:idx, "z_t"], errors="coerce")
                    .to_numpy(dtype=float)
                )
                result = compute_two_sample_t_cpd(
                    window_values=window_values,
                    window_days=window,
                    min_segment_days=config.cpd.min_segment_days,
                    min_segment_fraction=config.cpd.min_segment_fraction,
                    epsilon=config.epsilon,
                    score_min=config.clipping.cpd_score_min,
                    score_max=config.clipping.cpd_score_max,
                )
            rows.append({
                "feature_set_id": feature_set_id,
                "as_of_date": as_of_date,
                "root": root,
                "cpd_window_days": int(window),
                "cpd_score": result.cpd_score,
                "cpd_age_days": result.cpd_age_days,
                "cpd_location_index": result.cpd_location_index,
                "cpd_is_valid": bool(result.cpd_is_valid),
                "cpd_method": result.cpd_method,
                "builder_version": config.cpd.builder_version,
                "snapshot_id": snapshot_id,
            })

    return pd.DataFrame(rows, columns=CPD_DAILY_COLUMNS)
