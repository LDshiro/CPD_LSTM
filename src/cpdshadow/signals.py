from __future__ import annotations

from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pandas as pd

SIGNALS_DAILY_COLUMNS = [
    "run_id",
    "strategy_id",
    "model_id",
    "as_of_date",
    "root",
    "signal_raw",
    "signal_clipped",
    "is_valid",
    "invalid_reason",
    "feature_hash",
    "created_at_utc",
]


@dataclass(frozen=True)
class SignalBuildRequest:
    run_id: str
    strategy_id: str
    model_id: str
    feature_set_id: str
    snapshot_id: str | None
    start_date: date | None
    end_date: date | None
    roots: tuple[str, ...] | None
    created_at_utc: datetime


@dataclass(frozen=True)
class SignalQaIssue:
    code: str
    severity: str
    message: str
    details: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class SignalBuildResult:
    signals: pd.DataFrame
    qa: dict[str, Any]
    formula_artifact_path: Path | None = None
    formula_artifact_sha256: str | None = None
    model_registry_row: dict[str, object] | None = None


@dataclass(frozen=True)
class SignalQaReport:
    run_id: str
    strategy_id: str
    model_id: str
    feature_set_id: str
    snapshot_id: str | None
    summary: dict[str, object]
    issues: tuple[SignalQaIssue, ...] = ()

    @property
    def has_errors(self) -> bool:
        return any(issue.severity == "error" for issue in self.issues)

    @property
    def has_warnings(self) -> bool:
        return any(issue.severity == "warning" for issue in self.issues)

    def with_additional_issues(self, issues: Sequence[SignalQaIssue]) -> SignalQaReport:
        merged = tuple(
            sorted(
                [*self.issues, *issues], key=lambda item: (item.severity, item.code, item.message)
            )
        )
        summary = dict(self.summary)
        summary["fatal_error_count"] = sum(issue.severity == "error" for issue in merged)
        summary["warning_count"] = sum(issue.severity == "warning" for issue in merged)
        return SignalQaReport(
            run_id=self.run_id,
            strategy_id=self.strategy_id,
            model_id=self.model_id,
            feature_set_id=self.feature_set_id,
            snapshot_id=self.snapshot_id,
            summary=summary,
            issues=merged,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "run_id": self.run_id,
            "strategy_id": self.strategy_id,
            "model_id": self.model_id,
            "feature_set_id": self.feature_set_id,
            "snapshot_id": self.snapshot_id,
            "has_errors": self.has_errors,
            "has_warnings": self.has_warnings,
            "summary": self.summary,
            "issues": [asdict(issue) for issue in self.issues],
        }

    def to_markdown(self) -> str:
        lines = [
            f"# WP8 Signals QA {self.run_id}",
            "",
            f"- Strategy: `{self.strategy_id}`",
            f"- Model: `{self.model_id}`",
            f"- Feature set: `{self.feature_set_id}`",
            f"- Snapshot: `{self.snapshot_id}`",
            f"- Fatal errors: {self.summary.get('fatal_error_count', 0)}",
            f"- Warnings: {self.summary.get('warning_count', 0)}",
            "",
            "## Summary",
            "",
            f"- Date range: {self.summary.get('start_date')} -> {self.summary.get('end_date')}",
            f"- Root count: {self.summary.get('root_count', 0)}",
            f"- Row count: {self.summary.get('row_count', 0)}",
            f"- Valid rows: {self.summary.get('valid_row_count', 0)}",
            f"- Invalid rows: {self.summary.get('invalid_row_count', 0)}",
            f"- Coverage by root: {self.summary.get('coverage_by_root', {})}",
            f"- Invalid reasons: {self.summary.get('invalid_reason_counts', {})}",
            f"- Signal distribution: {self.summary.get('signal_distribution', {})}",
            f"- Long / short / flat: {self.summary.get('long_count', 0)} / "
            f"{self.summary.get('short_count', 0)} / {self.summary.get('flat_count', 0)}",
            "",
            "## Issues",
            "",
        ]
        if not self.issues:
            lines.append("- None")
            return "\n".join(lines)
        for issue in self.issues:
            details = f" {issue.details}" if issue.details else ""
            lines.append(f"- `{issue.severity}` `{issue.code}`: {issue.message}{details}")
        return "\n".join(lines)


def sort_signals_daily(signals_daily: pd.DataFrame, *, sort_keys: Sequence[str]) -> pd.DataFrame:
    if signals_daily.empty:
        return signals_daily.copy()
    return signals_daily.sort_values(list(sort_keys), kind="stable").reset_index(drop=True)


def validate_signals_daily(
    *,
    signals_daily: pd.DataFrame,
    request: SignalBuildRequest,
    clip_min: float,
    clip_max: float,
    sort_keys: Sequence[str],
    features_daily: pd.DataFrame | None = None,
) -> SignalQaReport:
    issues: list[SignalQaIssue] = []
    signals_daily = signals_daily.copy()
    if "as_of_date" in signals_daily.columns:
        signals_daily["as_of_date"] = pd.to_datetime(signals_daily["as_of_date"]).dt.date

    missing_columns = [
        column for column in SIGNALS_DAILY_COLUMNS if column not in signals_daily.columns
    ]
    if missing_columns:
        issues.append(
            SignalQaIssue(
                code="missing_required_columns",
                severity="error",
                message="signals_daily is missing required columns.",
                details={"columns": missing_columns},
            )
        )

    duplicate_count = 0
    if not signals_daily.empty and {"run_id", "strategy_id", "as_of_date", "root"}.issubset(
        signals_daily.columns
    ):
        duplicates = signals_daily[
            signals_daily.duplicated(
                subset=["run_id", "strategy_id", "as_of_date", "root"], keep=False
            )
        ]
        duplicate_count = int(len(duplicates))
        if duplicate_count:
            issues.append(
                SignalQaIssue(
                    code="duplicate_primary_keys",
                    severity="error",
                    message="signals_daily contains duplicate primary keys.",
                    details={"count": duplicate_count},
                )
            )

    if (
        not signals_daily.empty
        and list(sort_keys)
        and all(key in signals_daily.columns for key in sort_keys)
    ):
        sorted_df = sort_signals_daily(signals_daily, sort_keys=sort_keys)
        if not signals_daily.reset_index(drop=True).equals(sorted_df):
            issues.append(
                SignalQaIssue(
                    code="unsorted_output_rows",
                    severity="error",
                    message="signals_daily must be sorted before writing.",
                    details={"sort_keys": list(sort_keys)},
                )
            )

    if not signals_daily.empty:
        clipped = pd.to_numeric(signals_daily["signal_clipped"], errors="coerce")
        out_of_bounds = clipped.notna() & ((clipped < clip_min) | (clipped > clip_max))
        if int(out_of_bounds.sum()):
            issues.append(
                SignalQaIssue(
                    code="signal_clipped_out_of_bounds",
                    severity="error",
                    message="signal_clipped must be null or within clip bounds.",
                    details={
                        "count": int(out_of_bounds.sum()),
                        "clip_min": clip_min,
                        "clip_max": clip_max,
                    },
                )
            )

        is_valid = signals_daily["is_valid"].fillna(False).astype(bool)
        invalid_reason = signals_daily["invalid_reason"].astype("string")
        invalid_reason_present = invalid_reason.notna() & (invalid_reason.str.strip() != "")
        invalid_missing_reason = (~is_valid) & ~invalid_reason_present
        if int(invalid_missing_reason.sum()):
            issues.append(
                SignalQaIssue(
                    code="invalid_rows_missing_reason",
                    severity="error",
                    message="invalid rows must carry a non-empty invalid_reason.",
                    details={"count": int(invalid_missing_reason.sum())},
                )
            )

        valid_with_reason = is_valid & invalid_reason_present
        if int(valid_with_reason.sum()):
            issues.append(
                SignalQaIssue(
                    code="valid_rows_have_invalid_reason",
                    severity="error",
                    message="valid rows must not carry invalid_reason.",
                    details={"count": int(valid_with_reason.sum())},
                )
            )

        valid_missing_hash = is_valid & ~_nonempty_string_mask(signals_daily["feature_hash"])
        if int(valid_missing_hash.sum()):
            issues.append(
                SignalQaIssue(
                    code="valid_rows_missing_feature_hash",
                    severity="error",
                    message="valid rows must carry a non-empty feature_hash.",
                    details={"count": int(valid_missing_hash.sum())},
                )
            )

        valid_null_signal = is_valid & (
            clipped.isna() | pd.to_numeric(signals_daily["signal_raw"], errors="coerce").isna()
        )
        if int(valid_null_signal.sum()):
            issues.append(
                SignalQaIssue(
                    code="valid_rows_missing_signal_values",
                    severity="error",
                    message="valid rows must carry non-null signal_raw and signal_clipped.",
                    details={"count": int(valid_null_signal.sum())},
                )
            )

        invalid_nonnull_signal = (~is_valid) & (
            clipped.notna() | pd.to_numeric(signals_daily["signal_raw"], errors="coerce").notna()
        )
        if int(invalid_nonnull_signal.sum()):
            issues.append(
                SignalQaIssue(
                    code="invalid_rows_have_signal_values",
                    severity="error",
                    message="invalid rows must have null signal values.",
                    details={"count": int(invalid_nonnull_signal.sum())},
                )
            )

        invalid_dates = pd.to_datetime(signals_daily["as_of_date"], errors="coerce").isna()
        if int(invalid_dates.sum()):
            issues.append(
                SignalQaIssue(
                    code="invalid_as_of_date",
                    severity="error",
                    message="signals_daily contains invalid as_of_date values.",
                    details={"count": int(invalid_dates.sum())},
                )
            )

    if features_daily is not None and not features_daily.empty:
        features_daily = features_daily.copy()
        features_daily["as_of_date"] = pd.to_datetime(features_daily["as_of_date"]).dt.date
        expected_rows = features_daily[["as_of_date", "root"]].drop_duplicates()
        actual_rows = (
            signals_daily[["as_of_date", "root"]].drop_duplicates()
            if not signals_daily.empty
            else pd.DataFrame(columns=["as_of_date", "root"])
        )
        missing_rows = expected_rows.merge(
            actual_rows, how="left", on=["as_of_date", "root"], indicator=True
        )
        missing_count = int((missing_rows["_merge"] == "left_only").sum())
        if missing_count:
            issues.append(
                SignalQaIssue(
                    code="missing_signal_rows",
                    severity="error",
                    message="signals_daily must contain one row per selected feature row.",
                    details={"count": missing_count},
                )
            )
        merged = (
            signals_daily.merge(
                features_daily[["as_of_date", "root", "feature_hash"]],
                on=["as_of_date", "root"],
                how="left",
                suffixes=("", "_source"),
            )
            if not signals_daily.empty
            else pd.DataFrame()
        )
        if not merged.empty:
            mismatch = (
                merged["is_valid"].fillna(False).astype(bool)
                & _nonempty_string_mask(merged["feature_hash_source"])
                & (
                    merged["feature_hash"].astype("string")
                    != merged["feature_hash_source"].astype("string")
                )
            )
            if int(mismatch.sum()):
                issues.append(
                    SignalQaIssue(
                        code="feature_hash_mismatch",
                        severity="error",
                        message="valid rows must carry through the source feature_hash.",
                        details={"count": int(mismatch.sum())},
                    )
                )

    coverage_by_root = _coverage_by_root(signals_daily)
    invalid_reason_counts = (
        signals_daily.loc[~signals_daily["is_valid"].fillna(False).astype(bool), "invalid_reason"]
        .dropna()
        .astype(str)
        .value_counts()
        .to_dict()
        if not signals_daily.empty
        else {}
    )
    valid_signals = (
        pd.to_numeric(
            signals_daily.loc[
                signals_daily["is_valid"].fillna(False).astype(bool), "signal_clipped"
            ],
            errors="coerce",
        ).dropna()
        if not signals_daily.empty
        else pd.Series(dtype=float)
    )
    signal_distribution = {
        "min": float(valid_signals.min()) if not valid_signals.empty else None,
        "p05": float(valid_signals.quantile(0.05)) if not valid_signals.empty else None,
        "median": float(valid_signals.median()) if not valid_signals.empty else None,
        "p95": float(valid_signals.quantile(0.95)) if not valid_signals.empty else None,
        "max": float(valid_signals.max()) if not valid_signals.empty else None,
    }
    long_count = int((valid_signals > 0).sum())
    short_count = int((valid_signals < 0).sum())
    flat_count = int((valid_signals == 0).sum())
    invalid_count = (
        int((~signals_daily["is_valid"].fillna(False).astype(bool)).sum())
        if not signals_daily.empty
        else 0
    )
    valid_count = (
        int(signals_daily["is_valid"].fillna(False).astype(bool).sum())
        if not signals_daily.empty
        else 0
    )

    non_warmup_invalid_count = 0
    if not signals_daily.empty:
        invalid_reason_series = signals_daily["invalid_reason"].astype("string")
        non_warmup_invalid_count = int(
            (
                (~signals_daily["is_valid"].fillna(False).astype(bool))
                & (invalid_reason_series != "warmup_not_ok")
            ).sum()
        )
        if len(signals_daily) > 0 and (non_warmup_invalid_count / len(signals_daily)) > 0.05:
            issues.append(
                SignalQaIssue(
                    code="high_invalid_signal_ratio",
                    severity="warning",
                    message="invalid signal ratio exceeds 5% outside warmup rows.",
                    details={
                        "invalid_ratio": round(non_warmup_invalid_count / len(signals_daily), 6),
                        "invalid_count": non_warmup_invalid_count,
                        "row_count": int(len(signals_daily)),
                    },
                )
            )

    summary = {
        "run_id": request.run_id,
        "strategy_id": request.strategy_id,
        "model_id": request.model_id,
        "feature_set_id": request.feature_set_id,
        "snapshot_id": request.snapshot_id,
        "start_date": request.start_date.isoformat() if request.start_date is not None else None,
        "end_date": request.end_date.isoformat() if request.end_date is not None else None,
        "root_count": int(signals_daily["root"].dropna().astype(str).nunique())
        if not signals_daily.empty
        else 0,
        "row_count": int(len(signals_daily)),
        "valid_row_count": valid_count,
        "invalid_row_count": invalid_count,
        "coverage_by_root": coverage_by_root,
        "invalid_reason_counts": invalid_reason_counts,
        "signal_distribution": signal_distribution,
        "long_count": long_count,
        "short_count": short_count,
        "flat_count": flat_count,
        "missing_required_feature_count": int(
            invalid_reason_counts.get("missing_required_feature", 0)
        ),
        "created_at_utc": request.created_at_utc.isoformat(),
        "primary_key_duplicate_count": duplicate_count,
        "fatal_error_count": int(sum(issue.severity == "error" for issue in issues)),
        "warning_count": int(sum(issue.severity == "warning" for issue in issues)),
    }
    return SignalQaReport(
        run_id=request.run_id,
        strategy_id=request.strategy_id,
        model_id=request.model_id,
        feature_set_id=request.feature_set_id,
        snapshot_id=request.snapshot_id,
        summary=summary,
        issues=tuple(sorted(issues, key=lambda item: (item.severity, item.code, item.message))),
    )


def _coverage_by_root(signals_daily: pd.DataFrame) -> dict[str, dict[str, object]]:
    if signals_daily.empty or "root" not in signals_daily.columns:
        return {}
    output: dict[str, dict[str, object]] = {}
    for root, group in signals_daily.groupby("root", sort=True):
        dates = pd.to_datetime(group["as_of_date"]).dt.date
        valid = group["is_valid"].fillna(False).astype(bool)
        output[str(root)] = {
            "min_date": dates.min().isoformat() if not dates.empty else None,
            "max_date": dates.max().isoformat() if not dates.empty else None,
            "row_count": int(len(group)),
            "valid_row_count": int(valid.sum()),
            "invalid_row_count": int((~valid).sum()),
        }
    return output


def _nonempty_string_mask(series: pd.Series) -> pd.Series:
    as_string = series.astype("string")
    return as_string.notna() & (as_string.str.strip() != "")
