from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date
from math import ceil, isfinite, sqrt
from typing import Any, Sequence

import numpy as np
import pandas as pd

from cpdshadow.config import FeaturesConfig
from cpdshadow.cpd import CPD_DAILY_COLUMNS, build_cpd_daily_for_root
from cpdshadow.ids import stable_sha256_hex


FEATURE_VECTOR_ORDER = [
    "ret_1",
    "ret_21",
    "ret_63",
    "ret_126",
    "ret_252",
    "macd_8_24",
    "macd_16_48",
    "macd_32_96",
    "cpd21_score",
    "cpd21_age",
    "cpd63_score",
    "cpd63_age",
    "vol_20_60",
    "vol_60_252",
]

FEATURES_DAILY_COLUMNS = [
    "feature_set_id",
    "as_of_date",
    "root",
    "series_id",
    *FEATURE_VECTOR_ORDER,
    "annualized_vol_60",
    "is_complete",
    "warmup_status",
    "feature_hash",
    "builder_version",
    "snapshot_id",
]

RETURN_VALIDATION_TOLERANCE = 1.0e-8


@dataclass(frozen=True)
class FeatureQaIssue:
    code: str
    severity: str
    message: str
    details: dict[str, object] = field(default_factory=dict)


class FeatureBuildError(ValueError):
    def __init__(self, code: str, message: str, details: dict[str, object] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.details = details or {}


@dataclass(frozen=True)
class FeatureQaReport:
    snapshot_id: str
    feature_set_id: str
    series_id: str
    builder_version: str
    cpd_builder_version: str
    summary: dict[str, object]
    issues: tuple[FeatureQaIssue, ...] = ()

    @property
    def has_errors(self) -> bool:
        return any(issue.severity == "error" for issue in self.issues)

    @property
    def has_warnings(self) -> bool:
        return any(issue.severity == "warning" for issue in self.issues)

    def with_additional_issues(self, issues: Sequence[FeatureQaIssue]) -> FeatureQaReport:
        merged = tuple(sorted([*self.issues, *issues], key=lambda item: (item.severity, item.code, item.message)))
        summary = dict(self.summary)
        summary["fatal_error_count"] = sum(issue.severity == "error" for issue in merged)
        summary["warning_count"] = sum(issue.severity == "warning" for issue in merged)
        return FeatureQaReport(
            snapshot_id=self.snapshot_id,
            feature_set_id=self.feature_set_id,
            series_id=self.series_id,
            builder_version=self.builder_version,
            cpd_builder_version=self.cpd_builder_version,
            summary=summary,
            issues=merged,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "snapshot_id": self.snapshot_id,
            "feature_set_id": self.feature_set_id,
            "series_id": self.series_id,
            "builder_version": self.builder_version,
            "cpd_builder_version": self.cpd_builder_version,
            "has_errors": self.has_errors,
            "has_warnings": self.has_warnings,
            "summary": self.summary,
            "issues": [asdict(issue) for issue in self.issues],
        }

    def to_markdown(self) -> str:
        lines = [
            f"# WP7 Features QA {self.snapshot_id}",
            "",
            f"- Feature set: `{self.feature_set_id}`",
            f"- Series: `{self.series_id}`",
            f"- Builder: `{self.builder_version}`",
            f"- CPD builder: `{self.cpd_builder_version}`",
            f"- Fatal errors: {self.summary.get('fatal_error_count', 0)}",
            f"- Warnings: {self.summary.get('warning_count', 0)}",
            f"- Feature rows: {self.summary.get('feature_rows', 0)}",
            f"- CPD rows: {self.summary.get('cpd_rows', 0)}",
            "",
            "## Summary",
            "",
            f"- Roots: {self.summary.get('roots', [])}",
            f"- Date range: {self.summary.get('start_date')} -> {self.summary.get('end_date')}",
            f"- Complete feature ratio by root: {self.summary.get('complete_feature_ratio_by_root', {})}",
            f"- CPD valid ratio by root and window: {self.summary.get('cpd_valid_ratio_by_root_and_window', {})}",
            f"- Warmup status counts: {self.summary.get('warmup_status_counts', {})}",
            f"- Missing input counts: {self.summary.get('missing_input_counts', {})}",
            f"- Blocked quality counts: {self.summary.get('blocked_quality_counts', {})}",
            f"- Return validation warnings: {self.summary.get('return_validation_warning_count', 0)}",
            "",
            "## Issues",
            "",
        ]
        if not self.issues:
            lines.append("- None")
            return "\n".join(lines)
        for issue in self.issues:
            detail_text = f" {issue.details}" if issue.details else ""
            lines.append(f"- `{issue.severity}` `{issue.code}`: {issue.message}{detail_text}")
        return "\n".join(lines)


def compute_feature_hash(
    *,
    feature_set_id: str,
    series_id: str,
    root: str,
    as_of_date: date,
    feature_values: Sequence[float | None],
) -> str:
    payload = {
        "feature_set_id": feature_set_id,
        "series_id": series_id,
        "root": root,
        "as_of_date": as_of_date.isoformat(),
        "features": [None if value is None else float(value) for value in feature_values],
    }
    return stable_sha256_hex(payload)


def build_features_for_root(
    *,
    root: str,
    continuous_daily: pd.DataFrame,
    config: FeaturesConfig,
    feature_set_id: str,
    snapshot_id: str,
    start_date: date,
    end_date: date,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    root_df = continuous_daily[continuous_daily["root"] == root].copy()
    if root_df.empty:
        return (
            pd.DataFrame(columns=CPD_DAILY_COLUMNS),
            pd.DataFrame(columns=FEATURES_DAILY_COLUMNS),
        )

    root_df["as_of_date"] = pd.to_datetime(root_df["as_of_date"]).dt.date
    root_df = root_df.sort_values(["as_of_date"], kind="stable").reset_index(drop=True)
    if root_df.duplicated(subset=["as_of_date"], keep=False).any():
        raise FeatureBuildError(
            "duplicate_source_primary_keys",
            "continuous_daily contains duplicate (root, as_of_date) rows.",
            {"root": root},
        )

    price = pd.to_numeric(root_df[config.price_column], errors="coerce")
    source_usable = (
        root_df["is_usable_for_signal"].fillna(False).astype(bool)
        & price.notna()
        & np.isfinite(price)
        & (price > 0)
    )
    usable_price = price.where(source_usable)
    prev_usable_price = usable_price.shift(1)
    prev_source_usable = source_usable.shift(1, fill_value=False)

    recomputed_daily_return = pd.Series(np.nan, index=root_df.index, dtype=float)
    valid_return_mask = source_usable & prev_source_usable & prev_usable_price.notna() & (prev_usable_price > 0)
    recomputed_daily_return.loc[valid_return_mask] = (
        usable_price.loc[valid_return_mask] / prev_usable_price.loc[valid_return_mask] - 1.0
    )

    sigma20_daily = _ewm_std(recomputed_daily_return, span=20, min_periods=min(20, config.volatility.min_periods))
    sigma60_daily = _ewm_std(
        recomputed_daily_return,
        span=config.volatility.span_days,
        min_periods=config.volatility.min_periods,
    )
    sigma252_daily = _ewm_std(recomputed_daily_return, span=252, min_periods=config.volatility.min_periods)
    annualized_vol_60 = sigma60_daily * sqrt(config.annualization_factor)

    horizon_features: dict[str, pd.Series] = {}
    for horizon in config.horizons.normalized_returns:
        lag_price = usable_price.shift(horizon)
        raw_ret = pd.Series(np.nan, index=root_df.index, dtype=float)
        valid_mask = source_usable & lag_price.notna() & (lag_price > 0) & sigma60_daily.notna()
        raw_ret.loc[valid_mask] = usable_price.loc[valid_mask] / lag_price.loc[valid_mask] - 1.0
        normalized = pd.Series(np.nan, index=root_df.index, dtype=float)
        normalized.loc[valid_mask] = raw_ret.loc[valid_mask] / (
            sigma60_daily.loc[valid_mask] * sqrt(horizon) + config.epsilon
        )
        horizon_features[f"ret_{horizon}"] = normalized.clip(
            lower=-config.clipping.normalized_return_abs_max,
            upper=config.clipping.normalized_return_abs_max,
        )

    vol_20_60 = _clip_ratio(
        sigma20_daily,
        sigma60_daily,
        epsilon=config.epsilon,
        ratio_min=config.clipping.vol_ratio_min,
        ratio_max=config.clipping.vol_ratio_max,
    )
    vol_60_252 = _clip_ratio(
        sigma60_daily,
        sigma252_daily,
        epsilon=config.epsilon,
        ratio_min=config.clipping.vol_ratio_min,
        ratio_max=config.clipping.vol_ratio_max,
    )

    log_price = np.log(usable_price)
    macd_features: dict[str, pd.Series] = {}
    for fast, slow in config.macd.pairs:
        macd_raw = log_price.ewm(span=fast, adjust=False).mean() - log_price.ewm(span=slow, adjust=False).mean()
        macd_std = macd_raw.ewm(
            span=config.macd.zscore_span_days,
            adjust=False,
            min_periods=config.macd.zscore_min_periods,
        ).std(bias=False)
        macd_value = macd_raw / (macd_std + config.epsilon)
        macd_features[f"macd_{fast}_{slow}"] = macd_value.clip(
            lower=-config.clipping.macd_abs_max,
            upper=config.clipping.macd_abs_max,
        )

    z_t = pd.Series(np.nan, index=root_df.index, dtype=float)
    valid_z_mask = recomputed_daily_return.notna() & sigma60_daily.notna()
    z_t.loc[valid_z_mask] = recomputed_daily_return.loc[valid_z_mask] / (
        sigma60_daily.loc[valid_z_mask] + config.epsilon
    )
    z_t = z_t.clip(lower=-10.0, upper=10.0)

    cpd_source = root_df[["as_of_date", "root"]].copy()
    cpd_source["z_t"] = z_t
    cpd_daily = build_cpd_daily_for_root(
        root=root,
        source_df=cpd_source,
        config=config,
        feature_set_id=feature_set_id,
        snapshot_id=snapshot_id,
        start_date=start_date,
        end_date=end_date,
    )
    cpd_lookup = _build_cpd_lookup(cpd_daily)

    required_history_rows = _required_history_rows(config)
    rows: list[dict[str, object]] = []
    for idx, row in root_df.iterrows():
        as_of_date = row["as_of_date"]
        if as_of_date < start_date or as_of_date > end_date:
            continue

        feature_values = {
            **{name: _finite_or_none(series.iloc[idx]) for name, series in horizon_features.items()},
            **{name: _finite_or_none(series.iloc[idx]) for name, series in macd_features.items()},
            "vol_20_60": _finite_or_none(vol_20_60.iloc[idx]),
            "vol_60_252": _finite_or_none(vol_60_252.iloc[idx]),
        }
        annualized_value = _finite_or_none(annualized_vol_60.iloc[idx])

        for window in config.cpd.windows:
            cpd_row = cpd_lookup.get((as_of_date, int(window)))
            if cpd_row is None or not bool(cpd_row["cpd_is_valid"]):
                feature_values[f"cpd{window}_score"] = None
                feature_values[f"cpd{window}_age"] = None
            else:
                score = _finite_or_none(cpd_row["cpd_score"])
                age_days = _finite_or_none(cpd_row["cpd_age_days"])
                feature_values[f"cpd{window}_score"] = score
                feature_values[f"cpd{window}_age"] = (
                    None if age_days is None else float(age_days) / float(window)
                )

        if not bool(source_usable.iloc[idx]):
            warmup_status = "blocked_quality"
        elif idx < required_history_rows:
            warmup_status = "warmup"
        elif annualized_value is None or any(feature_values[name] is None for name in FEATURE_VECTOR_ORDER):
            warmup_status = "missing_input"
        else:
            warmup_status = "ok"

        is_complete = warmup_status == "ok"
        if not is_complete:
            feature_values = {name: None for name in FEATURE_VECTOR_ORDER}
            annualized_value = None

        ordered_feature_values = [feature_values[name] for name in FEATURE_VECTOR_ORDER]
        rows.append({
            "feature_set_id": feature_set_id,
            "as_of_date": as_of_date,
            "root": root,
            "series_id": config.series_id,
            **feature_values,
            "annualized_vol_60": annualized_value,
            "is_complete": is_complete,
            "warmup_status": warmup_status,
            "feature_hash": compute_feature_hash(
                feature_set_id=feature_set_id,
                series_id=config.series_id,
                root=root,
                as_of_date=as_of_date,
                feature_values=ordered_feature_values,
            ),
            "builder_version": config.builder_version,
            "snapshot_id": snapshot_id,
        })

    features_daily = pd.DataFrame(rows, columns=FEATURES_DAILY_COLUMNS)
    return cpd_daily, features_daily


def build_feature_outputs(
    *,
    continuous_daily: pd.DataFrame,
    config: FeaturesConfig,
    feature_set_id: str,
    snapshot_id: str,
    roots: Sequence[str] | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    if start_date is None or end_date is None:
        raise FeatureBuildError(
            "missing_build_range",
            "build_feature_outputs requires both start_date and end_date.",
        )
    if continuous_daily.empty:
        return (
            pd.DataFrame(columns=CPD_DAILY_COLUMNS),
            pd.DataFrame(columns=FEATURES_DAILY_COLUMNS),
        )

    selected = continuous_daily.copy()
    selected["as_of_date"] = pd.to_datetime(selected["as_of_date"]).dt.date
    selected = selected[selected["as_of_date"] <= end_date]
    if roots is not None:
        root_set = {str(root) for root in roots}
        selected = selected[selected["root"].astype(str).isin(root_set)]
    if selected.empty:
        return (
            pd.DataFrame(columns=CPD_DAILY_COLUMNS),
            pd.DataFrame(columns=FEATURES_DAILY_COLUMNS),
        )

    resolved_roots = sorted(selected["root"].dropna().astype(str).unique())
    cpd_frames: list[pd.DataFrame] = []
    feature_frames: list[pd.DataFrame] = []
    for root in resolved_roots:
        cpd_daily, features_daily = build_features_for_root(
            root=root,
            continuous_daily=selected,
            config=config,
            feature_set_id=feature_set_id,
            snapshot_id=snapshot_id,
            start_date=start_date,
            end_date=end_date,
        )
        cpd_frames.append(cpd_daily)
        feature_frames.append(features_daily)

    cpd_output = pd.concat(cpd_frames, ignore_index=True) if cpd_frames else pd.DataFrame(columns=CPD_DAILY_COLUMNS)
    features_output = (
        pd.concat(feature_frames, ignore_index=True)
        if feature_frames
        else pd.DataFrame(columns=FEATURES_DAILY_COLUMNS)
    )
    if not cpd_output.empty:
        cpd_output = cpd_output.sort_values(["root", "as_of_date", "cpd_window_days"], kind="stable").reset_index(drop=True)
    if not features_output.empty:
        features_output = features_output.sort_values(["root", "as_of_date"], kind="stable").reset_index(drop=True)
    return cpd_output, features_output


def validate_feature_outputs(
    *,
    features_daily: pd.DataFrame,
    cpd_daily: pd.DataFrame,
    continuous_daily: pd.DataFrame,
    config: FeaturesConfig,
) -> FeatureQaReport:
    issues: list[FeatureQaIssue] = []
    features_daily = features_daily.copy()
    cpd_daily = cpd_daily.copy()
    continuous_daily = continuous_daily.copy()

    if "as_of_date" in features_daily.columns:
        features_daily["as_of_date"] = pd.to_datetime(features_daily["as_of_date"]).dt.date
    if "as_of_date" in cpd_daily.columns:
        cpd_daily["as_of_date"] = pd.to_datetime(cpd_daily["as_of_date"]).dt.date
    if "as_of_date" in continuous_daily.columns:
        continuous_daily["as_of_date"] = pd.to_datetime(continuous_daily["as_of_date"]).dt.date

    snapshot_id = (
        _first_string(features_daily.get("snapshot_id"))
        or _first_string(cpd_daily.get("snapshot_id"))
        or _first_string(continuous_daily.get("snapshot_id"))
        or "unknown"
    )
    feature_set_id = _first_string(features_daily.get("feature_set_id")) or config.feature_set_id
    series_id = _first_string(features_daily.get("series_id")) or config.series_id
    builder_version = _first_string(features_daily.get("builder_version")) or config.builder_version
    cpd_builder_version = _first_string(cpd_daily.get("builder_version")) or config.cpd.builder_version

    missing_feature_columns = [column for column in FEATURES_DAILY_COLUMNS if column not in features_daily.columns]
    if missing_feature_columns:
        issues.append(FeatureQaIssue(
            code="missing_required_columns_features_daily",
            severity="error",
            message="features_daily is missing required columns.",
            details={"columns": missing_feature_columns},
        ))
    missing_cpd_columns = [column for column in CPD_DAILY_COLUMNS if column not in cpd_daily.columns]
    if missing_cpd_columns:
        issues.append(FeatureQaIssue(
            code="missing_required_columns_cpd_daily",
            severity="error",
            message="cpd_daily is missing required columns.",
            details={"columns": missing_cpd_columns},
        ))

    feature_duplicate_count = 0
    cpd_duplicate_count = 0
    if not features_daily.empty:
        feature_duplicates = features_daily[
            features_daily.duplicated(subset=["feature_set_id", "as_of_date", "root"], keep=False)
        ]
        feature_duplicate_count = int(len(feature_duplicates))
        if feature_duplicate_count:
            issues.append(FeatureQaIssue(
                code="duplicate_primary_keys_features_daily",
                severity="error",
                message="features_daily contains duplicate primary keys.",
                details={"count": feature_duplicate_count},
            ))
    if not cpd_daily.empty:
        cpd_duplicates = cpd_daily[
            cpd_daily.duplicated(subset=["feature_set_id", "as_of_date", "root", "cpd_window_days"], keep=False)
        ]
        cpd_duplicate_count = int(len(cpd_duplicates))
        if cpd_duplicate_count:
            issues.append(FeatureQaIssue(
                code="duplicate_primary_keys_cpd_daily",
                severity="error",
                message="cpd_daily contains duplicate primary keys.",
                details={"count": cpd_duplicate_count},
            ))

    if not features_daily.empty:
        numeric_check = features_daily["is_complete"].fillna(False).astype(bool)
        for column in [*FEATURE_VECTOR_ORDER, "annualized_vol_60"]:
            if column not in features_daily.columns:
                continue
            invalid_rows = numeric_check & ~pd.to_numeric(features_daily[column], errors="coerce").map(_is_finite_number)
            if int(invalid_rows.sum()):
                issues.append(FeatureQaIssue(
                    code="nonfinite_complete_feature_values",
                    severity="error",
                    message="is_complete rows must have finite feature values.",
                    details={"column": column, "count": int(invalid_rows.sum())},
                ))

        for column in ("cpd21_age", "cpd63_age"):
            if column not in features_daily.columns:
                continue
            invalid_age = features_daily[column].map(_coerce_float).map(
                lambda value: value is not None and not (0.0 <= value <= 1.0)
            )
            if int(invalid_age.sum()):
                issues.append(FeatureQaIssue(
                    code="cpd_age_out_of_bounds_features_daily",
                    severity="error",
                    message="normalized CPD ages in features_daily must be within [0, 1].",
                    details={"column": column, "count": int(invalid_age.sum())},
                ))

        missing_hash = features_daily["feature_hash"].isna() | (features_daily["feature_hash"].astype(str).str.strip() == "")
        if int(missing_hash.sum()):
            issues.append(FeatureQaIssue(
                code="missing_feature_hash",
                severity="error",
                message="features_daily contains rows with missing feature_hash.",
                details={"count": int(missing_hash.sum())},
            ))
        hash_duplicates = features_daily[
            features_daily.duplicated(subset=["feature_hash"], keep=False)
        ] if "feature_hash" in features_daily.columns else pd.DataFrame()
        if not hash_duplicates.empty:
            issues.append(FeatureQaIssue(
                code="duplicate_feature_hash",
                severity="error",
                message="features_daily contains duplicate feature_hash values.",
                details={"count": int(len(hash_duplicates))},
            ))
        feature_hash_duplicate_count = int(len(hash_duplicates))
    else:
        feature_hash_duplicate_count = 0

    if not cpd_daily.empty:
        invalid_score = cpd_daily["cpd_score"].map(_coerce_float).map(
            lambda value: value is not None and not (
                config.clipping.cpd_score_min <= value <= config.clipping.cpd_score_max
            )
        )
        if int(invalid_score.sum()):
            issues.append(FeatureQaIssue(
                code="cpd_score_out_of_bounds",
                severity="error",
                message="cpd_daily contains cpd_score values outside configured bounds.",
                details={"count": int(invalid_score.sum())},
            ))
        invalid_age_days = cpd_daily.apply(_invalid_cpd_age_days, axis=1)
        if int(invalid_age_days.sum()):
            issues.append(FeatureQaIssue(
                code="cpd_age_days_out_of_bounds",
                severity="error",
                message="cpd_daily contains cpd_age_days outside [0, window].",
                details={"count": int(invalid_age_days.sum())},
            ))

    source_lookup = _build_source_lookup(continuous_daily)
    if not features_daily.empty and {"root", "as_of_date", "is_complete"}.issubset(features_daily.columns):
        invalid_complete_source = 0
        for _, row in features_daily.iterrows():
            if not bool(row["is_complete"]):
                continue
            source_row = source_lookup.get((str(row["root"]), row["as_of_date"]))
            if source_row is None or not bool(source_row.get("is_usable_for_signal", False)):
                invalid_complete_source += 1
        if invalid_complete_source:
            issues.append(FeatureQaIssue(
                code="complete_row_uses_unusable_source",
                severity="error",
                message="is_complete rows must originate from usable continuous_daily rows.",
                details={"count": invalid_complete_source},
            ))

    return_validation_warning_count = _return_validation_warning_count(
        continuous_daily=continuous_daily,
        features_daily=features_daily,
    )
    if return_validation_warning_count:
        issues.append(FeatureQaIssue(
            code="return_validation_mismatch",
            severity="warning",
            message="continuous_daily.daily_return differs from adjusted-price implied returns.",
            details={"count": return_validation_warning_count},
        ))

    complete_feature_ratio_by_root: dict[str, float] = {}
    if not features_daily.empty:
        for root, group in features_daily.groupby("root", sort=True):
            non_warmup = group[group["warmup_status"] != "warmup"]
            ratio = float(non_warmup["is_complete"].mean()) if not non_warmup.empty else 0.0
            complete_feature_ratio_by_root[str(root)] = ratio
            if not non_warmup.empty and ratio < 0.95:
                issues.append(FeatureQaIssue(
                    code="low_complete_feature_ratio",
                    severity="warning",
                    message="complete feature ratio after warmup is below 95%.",
                    details={"root": str(root), "ratio": round(ratio, 6)},
                ))

    cpd_valid_ratio_by_root_and_window: dict[str, dict[str, float]] = {}
    if not cpd_daily.empty:
        for (root, window), group in cpd_daily.groupby(["root", "cpd_window_days"], sort=True):
            ratio = float(group["cpd_is_valid"].fillna(False).mean())
            cpd_valid_ratio_by_root_and_window.setdefault(str(root), {})[str(int(window))] = ratio
            if not bool(group["cpd_is_valid"].fillna(False).any()):
                issues.append(FeatureQaIssue(
                    code="zero_valid_cpd_rows",
                    severity="warning",
                    message="a root/window combination has zero valid CPD rows.",
                    details={"root": str(root), "cpd_window_days": int(window)},
                ))

    clipping_issues = _detect_clipping_warnings(features_daily, config)
    issues.extend(clipping_issues)

    warmup_status_counts = (
        features_daily["warmup_status"].value_counts(dropna=False).to_dict()
        if "warmup_status" in features_daily.columns and not features_daily.empty
        else {}
    )
    missing_input_counts = _status_counts_by_root(features_daily, "missing_input")
    blocked_quality_counts = _status_counts_by_root(features_daily, "blocked_quality")

    summary = {
        "snapshot_id": snapshot_id,
        "feature_set_id": feature_set_id,
        "series_id": series_id,
        "builder_version": builder_version,
        "cpd_builder_version": cpd_builder_version,
        "start_date": _date_min(features_daily, "as_of_date"),
        "end_date": _date_max(features_daily, "as_of_date"),
        "roots": sorted(features_daily["root"].dropna().astype(str).unique()) if not features_daily.empty else [],
        "feature_rows": int(len(features_daily)),
        "cpd_rows": int(len(cpd_daily)),
        "complete_feature_ratio_by_root": complete_feature_ratio_by_root,
        "cpd_valid_ratio_by_root_and_window": cpd_valid_ratio_by_root_and_window,
        "warmup_status_counts": warmup_status_counts,
        "missing_input_counts": missing_input_counts,
        "blocked_quality_counts": blocked_quality_counts,
        "max_abs_by_feature": _max_abs_by_feature(features_daily),
        "annualized_vol_60_summary_by_root": _annualized_vol_summary(features_daily),
        "return_validation_warning_count": int(return_validation_warning_count),
        "feature_hash_duplicate_count": int(feature_hash_duplicate_count),
        "primary_key_duplicate_count": int(feature_duplicate_count + cpd_duplicate_count),
        "fatal_error_count": int(sum(issue.severity == "error" for issue in issues)),
        "warning_count": int(sum(issue.severity == "warning" for issue in issues)),
    }

    return FeatureQaReport(
        snapshot_id=snapshot_id,
        feature_set_id=feature_set_id,
        series_id=series_id,
        builder_version=builder_version,
        cpd_builder_version=cpd_builder_version,
        summary=summary,
        issues=tuple(sorted(issues, key=lambda item: (item.severity, item.code, item.message))),
    )


def _ewm_std(series: pd.Series, *, span: int, min_periods: int) -> pd.Series:
    return series.ewm(span=span, adjust=False, min_periods=min_periods).std(bias=False)


def _clip_ratio(
    numerator: pd.Series,
    denominator: pd.Series,
    *,
    epsilon: float,
    ratio_min: float,
    ratio_max: float,
) -> pd.Series:
    ratio = pd.Series(np.nan, index=numerator.index, dtype=float)
    valid = numerator.notna() & denominator.notna() & (denominator.abs() > epsilon)
    ratio.loc[valid] = numerator.loc[valid] / denominator.loc[valid]
    return ratio.clip(lower=ratio_min, upper=ratio_max)


def _build_cpd_lookup(cpd_daily: pd.DataFrame) -> dict[tuple[date, int], dict[str, object]]:
    if cpd_daily.empty:
        return {}
    lookup: dict[tuple[date, int], dict[str, object]] = {}
    for _, row in cpd_daily.iterrows():
        lookup[(row["as_of_date"], int(row["cpd_window_days"]))] = row.to_dict()
    return lookup


def _build_source_lookup(continuous_daily: pd.DataFrame) -> dict[tuple[str, date], dict[str, object]]:
    if continuous_daily.empty or "root" not in continuous_daily.columns or "as_of_date" not in continuous_daily.columns:
        return {}
    lookup: dict[tuple[str, date], dict[str, object]] = {}
    for _, row in continuous_daily.iterrows():
        row_date = _coerce_date(row.get("as_of_date"))
        if row_date is None:
            continue
        lookup[(str(row["root"]), row_date)] = row.to_dict()
    return lookup


def _return_validation_warning_count(
    *,
    continuous_daily: pd.DataFrame,
    features_daily: pd.DataFrame,
) -> int:
    if continuous_daily.empty or features_daily.empty:
        return 0
    source = continuous_daily.copy()
    source["as_of_date"] = pd.to_datetime(source["as_of_date"]).dt.date
    count = 0
    for root, group in source.sort_values(["root", "as_of_date"], kind="stable").groupby("root", sort=True):
        price = pd.to_numeric(group["adj_settle_price"], errors="coerce")
        usable = (
            group["is_usable_for_signal"].fillna(False).astype(bool)
            & price.notna()
            & (price > 0)
            & np.isfinite(price)
        )
        prev_price = price.where(usable).shift(1)
        prev_usable = usable.shift(1, fill_value=False)
        recomputed = pd.Series(np.nan, index=group.index, dtype=float)
        valid = usable & prev_usable & prev_price.notna() & (prev_price > 0)
        recomputed.loc[valid] = price.loc[valid] / prev_price.loc[valid] - 1.0
        source_return = pd.to_numeric(group["daily_return"], errors="coerce")
        in_output = group["as_of_date"].isin(set(features_daily.loc[features_daily["root"] == root, "as_of_date"]))
        mismatch = (
            in_output
            & source_return.notna()
            & recomputed.notna()
            & ((source_return - recomputed).abs() > RETURN_VALIDATION_TOLERANCE)
        )
        count += int(mismatch.sum())
    return count


def _status_counts_by_root(features_daily: pd.DataFrame, target_status: str) -> dict[str, int]:
    if features_daily.empty or "warmup_status" not in features_daily.columns:
        return {}
    filtered = features_daily[features_daily["warmup_status"] == target_status]
    if filtered.empty:
        return {}
    return filtered.groupby("root")["as_of_date"].count().astype(int).to_dict()


def _detect_clipping_warnings(features_daily: pd.DataFrame, config: FeaturesConfig) -> list[FeatureQaIssue]:
    issues: list[FeatureQaIssue] = []
    if features_daily.empty:
        return issues

    clip_specs: dict[str, tuple[float, float] | tuple[float, float, bool]] = {
        "ret_1": (-config.clipping.normalized_return_abs_max, config.clipping.normalized_return_abs_max),
        "ret_21": (-config.clipping.normalized_return_abs_max, config.clipping.normalized_return_abs_max),
        "ret_63": (-config.clipping.normalized_return_abs_max, config.clipping.normalized_return_abs_max),
        "ret_126": (-config.clipping.normalized_return_abs_max, config.clipping.normalized_return_abs_max),
        "ret_252": (-config.clipping.normalized_return_abs_max, config.clipping.normalized_return_abs_max),
        "macd_8_24": (-config.clipping.macd_abs_max, config.clipping.macd_abs_max),
        "macd_16_48": (-config.clipping.macd_abs_max, config.clipping.macd_abs_max),
        "macd_32_96": (-config.clipping.macd_abs_max, config.clipping.macd_abs_max),
        "vol_20_60": (config.clipping.vol_ratio_min, config.clipping.vol_ratio_max),
        "vol_60_252": (config.clipping.vol_ratio_min, config.clipping.vol_ratio_max),
        "cpd21_score": (config.clipping.cpd_score_min, config.clipping.cpd_score_max),
        "cpd63_score": (config.clipping.cpd_score_min, config.clipping.cpd_score_max),
    }

    for column, (lower, upper) in clip_specs.items():
        if column not in features_daily.columns:
            continue
        values = pd.to_numeric(features_daily[column], errors="coerce")
        valid = values.notna()
        if not bool(valid.any()):
            continue
        clipped = valid & (
            ((values - lower).abs() <= 1.0e-12)
            | ((values - upper).abs() <= 1.0e-12)
        )
        threshold = max(3, int(ceil(float(valid.sum()) * 0.05)))
        if int(clipped.sum()) >= threshold:
            issues.append(FeatureQaIssue(
                code="frequent_feature_clipping",
                severity="warning",
                message="feature values are hitting configured clipping bounds frequently.",
                details={"column": column, "count": int(clipped.sum()), "threshold": threshold},
            ))
    return issues


def _max_abs_by_feature(features_daily: pd.DataFrame) -> dict[str, float | None]:
    output: dict[str, float | None] = {}
    if features_daily.empty:
        return output
    for column in [*FEATURE_VECTOR_ORDER, "annualized_vol_60"]:
        if column not in features_daily.columns:
            continue
        series = pd.to_numeric(features_daily[column], errors="coerce").dropna()
        output[column] = float(series.abs().max()) if not series.empty else None
    return output


def _annualized_vol_summary(features_daily: pd.DataFrame) -> dict[str, dict[str, float | None]]:
    if features_daily.empty or "annualized_vol_60" not in features_daily.columns:
        return {}
    output: dict[str, dict[str, float | None]] = {}
    for root, group in features_daily.groupby("root", sort=True):
        series = pd.to_numeric(group["annualized_vol_60"], errors="coerce").dropna()
        output[str(root)] = {
            "min": float(series.min()) if not series.empty else None,
            "median": float(series.median()) if not series.empty else None,
            "max": float(series.max()) if not series.empty else None,
        }
    return output


def _invalid_cpd_age_days(row: pd.Series) -> bool:
    age_days = _coerce_float(row.get("cpd_age_days"))
    if age_days is None:
        return False
    window = _coerce_float(row.get("cpd_window_days"))
    if window is None:
        return True
    return not (0.0 <= age_days <= window)


def _date_min(df: pd.DataFrame, column: str) -> str | None:
    if df.empty or column not in df.columns:
        return None
    value = pd.to_datetime(df[column]).dt.date.min()
    return value.isoformat() if value is not None else None


def _date_max(df: pd.DataFrame, column: str) -> str | None:
    if df.empty or column not in df.columns:
        return None
    value = pd.to_datetime(df[column]).dt.date.max()
    return value.isoformat() if value is not None else None


def _coerce_date(value: Any) -> date | None:
    if value is None or pd.isna(value):
        return None
    return pd.Timestamp(value).date()


def _coerce_float(value: Any) -> float | None:
    if value is None or pd.isna(value):
        return None
    numeric = float(value)
    if not isfinite(numeric):
        return None
    return numeric


def _is_finite_number(value: Any) -> bool:
    numeric = _coerce_float(value)
    return numeric is not None and isfinite(numeric)


def _finite_or_none(value: Any) -> float | None:
    numeric = _coerce_float(value)
    return numeric if numeric is not None else None


def _first_string(series: pd.Series | None) -> str | None:
    if series is None or series.empty:
        return None
    cleaned = series.dropna()
    if cleaned.empty:
        return None
    return str(cleaned.iloc[0])


def _required_history_rows(config: FeaturesConfig) -> int:
    max_return_horizon = max(config.horizons.normalized_returns)
    max_cpd_window = max(config.cpd.windows)
    max_macd_history = max(max(fast, slow) for fast, slow in config.macd.pairs)
    max_vol_ratio_horizon = max(max(left, right) for left, right in config.volatility.ratio_pairs)
    return max(
        config.warmup_days,
        max_return_horizon,
        max_cpd_window - 1,
        config.macd.zscore_min_periods - 1,
        config.volatility.min_periods - 1,
        max_macd_history - 1,
        max_vol_ratio_horizon - 1,
    )
