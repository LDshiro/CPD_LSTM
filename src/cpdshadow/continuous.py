from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date
from math import isfinite
from typing import Any, Sequence

import pandas as pd


CONTINUOUS_DAILY_COLUMNS = [
    "series_id",
    "as_of_date",
    "root",
    "lead_raw_symbol",
    "raw_settle_price",
    "adj_settle_price",
    "adj_factor",
    "daily_return",
    "settle_status",
    "roll_flag",
    "roll_event_id",
    "is_usable_for_signal",
    "quality_flags",
    "builder_version",
    "snapshot_id",
]


@dataclass(frozen=True)
class ContinuousSeriesConfig:
    series_id: str = "v1_back_ratio_settle"
    builder_version: str = "continuous_builder_v1"
    strict_roll_ratio: bool = True
    allow_close_fallback: bool = True
    allowed_settle_statuses: tuple[str, ...] = ("final", "preliminary", "close_fallback")
    blocked_settle_statuses: tuple[str, ...] = ("missing",)
    max_abs_daily_return_warning: float = 0.20
    max_abs_daily_return_error: float = 0.50


@dataclass(frozen=True)
class ContinuousQaIssue:
    code: str
    severity: str
    message: str
    details: dict[str, object] = field(default_factory=dict)


class ContinuousBuildError(ValueError):
    def __init__(self, code: str, message: str, details: dict[str, object] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.details = details or {}


@dataclass(frozen=True)
class ContinuousQaReport:
    snapshot_id: str
    series_id: str
    builder_version: str
    summary: dict[str, object]
    issues: tuple[ContinuousQaIssue, ...] = ()

    @property
    def has_errors(self) -> bool:
        return any(issue.severity == "error" for issue in self.issues)

    @property
    def has_warnings(self) -> bool:
        return any(issue.severity == "warning" for issue in self.issues)

    def with_additional_issues(self, issues: Sequence[ContinuousQaIssue]) -> ContinuousQaReport:
        merged = tuple(sorted([*self.issues, *issues], key=lambda item: (item.severity, item.code, item.message)))
        summary = dict(self.summary)
        summary["fatal_error_count"] = sum(issue.severity == "error" for issue in merged)
        summary["warning_count"] = sum(issue.severity == "warning" for issue in merged)
        return ContinuousQaReport(
            snapshot_id=self.snapshot_id,
            series_id=self.series_id,
            builder_version=self.builder_version,
            summary=summary,
            issues=merged,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "snapshot_id": self.snapshot_id,
            "series_id": self.series_id,
            "builder_version": self.builder_version,
            "has_errors": self.has_errors,
            "has_warnings": self.has_warnings,
            "summary": self.summary,
            "issues": [asdict(issue) for issue in self.issues],
        }

    def to_markdown(self) -> str:
        lines = [
            f"# WP6 Continuous QA {self.snapshot_id}",
            "",
            f"- Series: `{self.series_id}`",
            f"- Builder: `{self.builder_version}`",
            f"- Fatal errors: {self.summary.get('fatal_error_count', 0)}",
            f"- Warnings: {self.summary.get('warning_count', 0)}",
            f"- Row count: {self.summary.get('row_count', 0)}",
            "",
            "## Summary",
            "",
            f"- Root coverage: {self.summary.get('root_coverage', {})}",
            f"- Date coverage: {self.summary.get('date_coverage', {})}",
            f"- Usable fraction by root: {self.summary.get('usable_fraction_by_root', {})}",
            f"- Roll count by root: {self.summary.get('roll_count_by_root', {})}",
            f"- Close fallback count: {self.summary.get('close_fallback_count', 0)}",
            f"- Missing price count: {self.summary.get('missing_price_count', 0)}",
            f"- Missing return count: {self.summary.get('missing_return_count', 0)}",
            f"- Large return count: {self.summary.get('large_return_count', 0)}",
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


def build_continuous_for_root(
    root: str,
    contracts_daily: pd.DataFrame,
    lead_map: pd.DataFrame,
    roll_events: pd.DataFrame,
    config: ContinuousSeriesConfig,
    snapshot_id: str,
) -> pd.DataFrame:
    root_lead_map = lead_map[lead_map["root"] == root].copy()
    if root_lead_map.empty:
        return _empty_continuous_df()

    root_lead_map["as_of_date"] = pd.to_datetime(root_lead_map["as_of_date"]).dt.date
    root_lead_map = root_lead_map.sort_values(["as_of_date", "lead_raw_symbol"], kind="stable").reset_index(drop=True)
    build_start = root_lead_map["as_of_date"].min()
    build_end = root_lead_map["as_of_date"].max()

    root_daily_lookup = _prepare_contracts_lookup(contracts_daily, root)
    event_map = _prepare_roll_event_map(
        roll_events=roll_events,
        root=root,
        config=config,
        build_start=build_start,
        build_end=build_end,
    )
    factor_by_date = _build_factor_by_date(
        dates=root_lead_map["as_of_date"].tolist(),
        event_map=event_map,
    )

    rows: list[dict[str, object]] = []
    previous_adj_price: float | None = None
    first_row = True
    for _, row in root_lead_map.iterrows():
        as_of_date = _coerce_date(row["as_of_date"])
        if as_of_date is None:
            raise ContinuousBuildError(
                "invalid_as_of_date",
                "lead_map row contains an invalid as_of_date.",
                {"root": root},
            )
        lead_raw_symbol = _string_or_none(row.get("lead_raw_symbol")) or ""
        settle_status = "missing"
        raw_settle_price: float | None = None
        quality_flags: set[str] = set()

        contract_row = _lookup_contract_row(root_daily_lookup, as_of_date, lead_raw_symbol)
        if not lead_raw_symbol:
            quality_flags.add("missing_lead_raw_symbol")
        elif contract_row is None:
            quality_flags.add("missing_raw_price")
        else:
            settle_status = _string_or_none(contract_row.get("settle_status")) or "missing"
            raw_settle_price = _coerce_float(contract_row.get("settle_price"))
            if raw_settle_price is None:
                quality_flags.add("missing_raw_price")
            elif raw_settle_price <= 0:
                quality_flags.add("nonpositive_raw_price")
            if settle_status == "close_fallback":
                quality_flags.add("close_fallback")
            if settle_status in config.blocked_settle_statuses:
                quality_flags.add(f"blocked_settle_status_{settle_status}")
            elif settle_status not in config.allowed_settle_statuses:
                quality_flags.add(f"unexpected_settle_status_{settle_status}")
            if settle_status == "close_fallback" and not config.allow_close_fallback:
                quality_flags.add("close_fallback_blocked")

        adj_factor = factor_by_date[as_of_date]
        adj_settle_price = (
            raw_settle_price * adj_factor
            if raw_settle_price is not None and raw_settle_price > 0
            else None
        )
        daily_return: float | None = None
        is_usable_for_signal = True
        if not lead_raw_symbol:
            is_usable_for_signal = False
        if raw_settle_price is None or raw_settle_price <= 0:
            is_usable_for_signal = False
        if any(flag.startswith("blocked_settle_status_") for flag in quality_flags):
            is_usable_for_signal = False
        if any(flag.startswith("unexpected_settle_status_") for flag in quality_flags):
            is_usable_for_signal = False
        if "close_fallback_blocked" in quality_flags:
            is_usable_for_signal = False

        if first_row:
            quality_flags.add("warmup_first_row")
            is_usable_for_signal = False
        elif (
            adj_settle_price is None
            or previous_adj_price is None
            or adj_settle_price <= 0
            or previous_adj_price <= 0
        ):
            quality_flags.add("missing_return_input")
            is_usable_for_signal = False
        else:
            daily_return = (adj_settle_price / previous_adj_price) - 1.0

        rows.append({
            "series_id": config.series_id,
            "as_of_date": as_of_date,
            "root": root,
            "lead_raw_symbol": lead_raw_symbol,
            "raw_settle_price": raw_settle_price,
            "adj_settle_price": adj_settle_price,
            "adj_factor": adj_factor,
            "daily_return": daily_return,
            "settle_status": settle_status,
            "roll_flag": bool(row.get("roll_flag", False)),
            "roll_event_id": _string_or_none(row.get("roll_event_id")),
            "is_usable_for_signal": is_usable_for_signal,
            "quality_flags": sorted(quality_flags),
            "builder_version": config.builder_version,
            "snapshot_id": snapshot_id,
        })

        previous_adj_price = adj_settle_price
        first_row = False

    return pd.DataFrame(rows, columns=CONTINUOUS_DAILY_COLUMNS)


def build_continuous_daily(
    contracts_daily: pd.DataFrame,
    lead_map: pd.DataFrame,
    roll_events: pd.DataFrame,
    config: ContinuousSeriesConfig,
    snapshot_id: str,
    roots: Sequence[str] | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
) -> pd.DataFrame:
    selected_lead_map = lead_map.copy()
    if selected_lead_map.empty:
        return _empty_continuous_df()
    selected_lead_map["as_of_date"] = pd.to_datetime(selected_lead_map["as_of_date"]).dt.date
    if start_date is not None:
        selected_lead_map = selected_lead_map[selected_lead_map["as_of_date"] >= start_date]
    if end_date is not None:
        selected_lead_map = selected_lead_map[selected_lead_map["as_of_date"] <= end_date]
    if roots is not None:
        root_set = {str(root) for root in roots}
        selected_lead_map = selected_lead_map[selected_lead_map["root"].astype(str).isin(root_set)]
    if selected_lead_map.empty:
        return _empty_continuous_df()

    selected_roots = sorted(selected_lead_map["root"].dropna().astype(str).unique())
    frames = [
        build_continuous_for_root(
            root=root,
            contracts_daily=contracts_daily,
            lead_map=selected_lead_map,
            roll_events=roll_events,
            config=config,
            snapshot_id=snapshot_id,
        )
        for root in selected_roots
    ]
    if not frames:
        return _empty_continuous_df()
    result = pd.concat(frames, ignore_index=True)
    return result.sort_values(["root", "as_of_date", "lead_raw_symbol"], kind="stable").reset_index(drop=True)


def validate_continuous_daily(
    continuous_daily: pd.DataFrame,
    lead_map: pd.DataFrame,
    roll_events: pd.DataFrame,
    contracts_daily: pd.DataFrame,
    config: ContinuousSeriesConfig,
) -> ContinuousQaReport:
    issues: list[ContinuousQaIssue] = []
    continuous_daily = continuous_daily.copy()
    lead_map = lead_map.copy()
    roll_events = roll_events.copy()
    contracts_daily = contracts_daily.copy()

    if "as_of_date" in continuous_daily.columns:
        continuous_daily["as_of_date"] = pd.to_datetime(continuous_daily["as_of_date"]).dt.date
    if "as_of_date" in lead_map.columns:
        lead_map["as_of_date"] = pd.to_datetime(lead_map["as_of_date"]).dt.date
    if "effective_date" in roll_events.columns:
        roll_events["effective_date"] = pd.to_datetime(roll_events["effective_date"]).dt.date
    if "trade_date" in contracts_daily.columns:
        contracts_daily["trade_date"] = pd.to_datetime(contracts_daily["trade_date"]).dt.date

    snapshot_id = _first_string(continuous_daily.get("snapshot_id")) or _first_string(lead_map.get("snapshot_id")) or "unknown"
    series_id = _first_string(continuous_daily.get("series_id")) or config.series_id
    builder_version = _first_string(continuous_daily.get("builder_version")) or config.builder_version

    missing_columns = [column for column in CONTINUOUS_DAILY_COLUMNS if column not in continuous_daily.columns]
    if missing_columns:
        issues.append(ContinuousQaIssue(
            code="missing_required_columns",
            severity="error",
            message="continuous_daily is missing required columns.",
            details={"columns": missing_columns},
        ))

    if not continuous_daily.empty:
        duplicates = continuous_daily[
            continuous_daily.duplicated(subset=["series_id", "as_of_date", "root"], keep=False)
        ]
        if not duplicates.empty:
            issues.append(ContinuousQaIssue(
                code="duplicate_primary_keys",
                severity="error",
                message="continuous_daily contains duplicate primary keys.",
                details={"count": int(len(duplicates))},
            ))

    invalid_factor_mask = pd.Series(False, index=continuous_daily.index)
    if "adj_factor" in continuous_daily.columns:
        invalid_factor_mask = continuous_daily["adj_factor"].map(_is_invalid_factor)
        invalid_factor_count = int(invalid_factor_mask.sum())
        if invalid_factor_count:
            issues.append(ContinuousQaIssue(
                code="invalid_adj_factor",
                severity="error",
                message="continuous_daily contains null, nonpositive, or non-finite adj_factor values.",
                details={"count": invalid_factor_count},
            ))

    usable_bad_adj = pd.Series(False, index=continuous_daily.index)
    if {"is_usable_for_signal", "adj_settle_price"}.issubset(continuous_daily.columns):
        usable_bad_adj = continuous_daily["is_usable_for_signal"].fillna(False) & continuous_daily["adj_settle_price"].map(
            _is_invalid_factor
        )
        if int(usable_bad_adj.sum()):
            issues.append(ContinuousQaIssue(
                code="usable_row_nonpositive_adjusted_price",
                severity="error",
                message="usable rows must have positive finite adj_settle_price.",
                details={"count": int(usable_bad_adj.sum())},
            ))

    if "lead_raw_symbol" in lead_map.columns:
        missing_lead_rows = lead_map[
            lead_map["lead_raw_symbol"].isna() | (lead_map["lead_raw_symbol"].astype(str).str.strip() == "")
        ]
        if not missing_lead_rows.empty:
            issues.append(ContinuousQaIssue(
                code="lead_raw_symbol_missing_in_lead_map",
                severity="error",
                message="lead_map contains rows with missing lead_raw_symbol.",
                details={"count": int(len(missing_lead_rows))},
            ))

    relevant_roll_events = _filter_relevant_roll_events(roll_events, lead_map)
    if not relevant_roll_events.empty:
        duplicate_effective = relevant_roll_events[
            relevant_roll_events.duplicated(subset=["root", "effective_date"], keep=False)
        ]
        if not duplicate_effective.empty:
            issues.append(ContinuousQaIssue(
                code="multiple_roll_events_same_effective_date",
                severity="error",
                message="multiple roll events exist for the same root and effective date.",
                details={"count": int(len(duplicate_effective))},
            ))
        if config.strict_roll_ratio:
            invalid_ratio = relevant_roll_events[~relevant_roll_events.apply(_row_has_valid_ratio, axis=1)]
            if not invalid_ratio.empty:
                issues.append(ContinuousQaIssue(
                    code="invalid_roll_ratio_in_strict_mode",
                    severity="error",
                    message="strict mode requires a positive finite roll ratio for each in-range roll event.",
                    details={"count": int(len(invalid_ratio))},
                ))

    expected_keys = {
        (config.series_id, _coerce_date(row["as_of_date"]), str(row["root"]))
        for _, row in lead_map.iterrows()
        if _coerce_date(row.get("as_of_date")) is not None
    }
    actual_keys = set()
    if {"series_id", "as_of_date", "root"}.issubset(continuous_daily.columns):
        actual_keys = {
            (str(row["series_id"]), _coerce_date(row["as_of_date"]), str(row["root"]))
            for _, row in continuous_daily.iterrows()
            if _coerce_date(row.get("as_of_date")) is not None
        }
    missing_output_keys = sorted(expected_keys - actual_keys)
    if missing_output_keys:
        issues.append(ContinuousQaIssue(
            code="missing_output_row_for_lead_map",
            severity="error",
            message="continuous_daily is missing one or more rows implied by lead_map.",
            details={"count": len(missing_output_keys), "sample": missing_output_keys[:5]},
        ))

    close_fallback_count = _count_flag(continuous_daily, "close_fallback")
    missing_price_count = _count_flag(continuous_daily, "missing_raw_price")
    missing_return_count = _count_flag(continuous_daily, "missing_return_input")

    if close_fallback_count:
        issues.append(ContinuousQaIssue(
            code="close_fallback_present",
            severity="warning",
            message="continuous_daily contains rows sourced from close_fallback settlement.",
            details={"count": close_fallback_count},
        ))
    if missing_price_count:
        issues.append(ContinuousQaIssue(
            code="missing_raw_price",
            severity="warning",
            message="one or more continuous rows are missing the selected raw settle.",
            details={"count": missing_price_count},
        ))
    if missing_return_count:
        issues.append(ContinuousQaIssue(
            code="missing_return_input",
            severity="warning",
            message="one or more rows cannot compute a daily return from the previous calendar row.",
            details={"count": missing_return_count},
        ))

    if "daily_return" in continuous_daily.columns:
        large_error = continuous_daily["daily_return"].map(_coerce_float).map(
            lambda value: value is not None and abs(value) > config.max_abs_daily_return_error
        )
        large_warning = continuous_daily["daily_return"].map(_coerce_float).map(
            lambda value: value is not None and config.max_abs_daily_return_warning < abs(value) <= config.max_abs_daily_return_error
        )
        if int(large_error.sum()):
            issues.append(ContinuousQaIssue(
                code="daily_return_over_error_threshold",
                severity="error",
                message="abs(daily_return) exceeds the configured error threshold.",
                details={"count": int(large_error.sum())},
            ))
        if int(large_warning.sum()):
            issues.append(ContinuousQaIssue(
                code="daily_return_over_warning_threshold",
                severity="warning",
                message="abs(daily_return) exceeds the configured warning threshold.",
                details={"count": int(large_warning.sum())},
            ))
        large_return_count = int((large_error | large_warning).sum())
    else:
        large_return_count = 0

    if not relevant_roll_events.empty:
        large_basis = relevant_roll_events[
            relevant_roll_events.apply(_row_has_large_basis, axis=1)
        ]
        if not large_basis.empty:
            issues.append(ContinuousQaIssue(
                code="large_roll_basis",
                severity="warning",
                message="one or more roll events has a large basis relative to the from_settle price.",
                details={"count": int(len(large_basis))},
            ))

    usable_fraction_by_root: dict[str, float] = {}
    if not continuous_daily.empty and {"root", "is_usable_for_signal"}.issubset(continuous_daily.columns):
        for root, group in continuous_daily.groupby("root", sort=True):
            usable_fraction = float(group["is_usable_for_signal"].fillna(False).mean())
            usable_fraction_by_root[str(root)] = usable_fraction
            if 0 < len(group) and usable_fraction < 0.50:
                issues.append(ContinuousQaIssue(
                    code="low_usable_fraction",
                    severity="warning",
                    message="root has too few usable continuous rows.",
                    details={"root": str(root), "usable_fraction": round(usable_fraction, 6)},
                ))

    lead_coverage = _coverage_by_root(lead_map, "as_of_date")
    continuous_coverage = _coverage_by_root(continuous_daily, "as_of_date")
    for root, coverage in lead_coverage.items():
        output_coverage = continuous_coverage.get(root)
        if output_coverage is None:
            continue
        if coverage["min_date"] != output_coverage["min_date"] or coverage["max_date"] != output_coverage["max_date"]:
            issues.append(ContinuousQaIssue(
                code="short_root_coverage",
                severity="warning",
                message="continuous coverage is shorter than lead_map coverage for a root.",
                details={
                    "root": root,
                    "expected": coverage,
                    "actual": output_coverage,
                },
            ))

    summary = {
        "row_count": int(len(continuous_daily)),
        "root_coverage": (
            continuous_daily.groupby("root")["as_of_date"].count().to_dict()
            if not continuous_daily.empty and "root" in continuous_daily.columns
            else {}
        ),
        "date_coverage": {
            "min_as_of_date": _date_min(continuous_daily, "as_of_date"),
            "max_as_of_date": _date_max(continuous_daily, "as_of_date"),
        },
        "usable_fraction_by_root": usable_fraction_by_root,
        "roll_count_by_root": (
            continuous_daily.groupby("root")["roll_flag"].sum().astype(int).to_dict()
            if not continuous_daily.empty and {"root", "roll_flag"}.issubset(continuous_daily.columns)
            else {}
        ),
        "close_fallback_count": close_fallback_count,
        "missing_price_count": missing_price_count,
        "missing_return_count": missing_return_count,
        "large_return_count": large_return_count,
        "fatal_error_count": int(sum(issue.severity == "error" for issue in issues)),
        "warning_count": int(sum(issue.severity == "warning" for issue in issues)),
    }
    return ContinuousQaReport(
        snapshot_id=snapshot_id,
        series_id=series_id,
        builder_version=builder_version,
        summary=summary,
        issues=tuple(sorted(issues, key=lambda item: (item.severity, item.code, item.message))),
    )


def _empty_continuous_df() -> pd.DataFrame:
    return pd.DataFrame(columns=CONTINUOUS_DAILY_COLUMNS)


def _prepare_contracts_lookup(contracts_daily: pd.DataFrame, root: str) -> pd.DataFrame:
    root_daily = contracts_daily[contracts_daily["root"] == root].copy()
    if root_daily.empty:
        return root_daily
    root_daily["trade_date"] = pd.to_datetime(root_daily["trade_date"]).dt.date
    sort_columns = ["trade_date", "raw_symbol"]
    if "available_at_utc" in root_daily.columns:
        sort_columns.append("available_at_utc")
    root_daily = root_daily.sort_values(sort_columns, kind="stable")
    root_daily = root_daily.drop_duplicates(subset=["trade_date", "raw_symbol"], keep="last")
    return root_daily.set_index(["trade_date", "raw_symbol"], drop=False)


def _prepare_roll_event_map(
    *,
    roll_events: pd.DataFrame,
    root: str,
    config: ContinuousSeriesConfig,
    build_start: date,
    build_end: date,
) -> dict[date, dict[str, object]]:
    required = {"root", "effective_date"}
    if roll_events.empty or not required.issubset(roll_events.columns):
        return {}
    root_events = roll_events[roll_events["root"] == root].copy()
    if root_events.empty:
        return {}
    root_events["effective_date"] = pd.to_datetime(root_events["effective_date"]).dt.date
    root_events = root_events[
        (root_events["effective_date"] >= build_start) & (root_events["effective_date"] <= build_end)
    ].sort_values(["effective_date", "roll_event_id"], kind="stable")
    if root_events.empty:
        return {}
    if root_events.duplicated(subset=["effective_date"], keep=False).any():
        duplicate_dates = sorted({
            item.isoformat()
            for item in root_events.loc[
                root_events.duplicated(subset=["effective_date"], keep=False),
                "effective_date",
            ]
        })
        raise ContinuousBuildError(
            "multiple_roll_events_same_effective_date",
            "multiple roll events exist for the same root and effective date.",
            {"root": root, "effective_dates": duplicate_dates},
        )
    event_map: dict[date, dict[str, object]] = {}
    for _, row in root_events.iterrows():
        effective_date = _coerce_date(row["effective_date"])
        if effective_date is None:
            continue
        resolved_ratio = _resolve_roll_ratio(row, config)
        event_map[effective_date] = {
            **row.to_dict(),
            "resolved_ratio": resolved_ratio,
        }
    return event_map


def _build_factor_by_date(
    *,
    dates: Sequence[date],
    event_map: dict[date, dict[str, object]],
) -> dict[date, float]:
    factor = 1.0
    factor_by_date: dict[date, float] = {}
    for as_of_date in reversed(sorted({_coerce_date(item) for item in dates if _coerce_date(item) is not None})):
        factor_by_date[as_of_date] = factor
        event = event_map.get(as_of_date)
        if event is not None:
            ratio = _coerce_float(event.get("resolved_ratio"))
            if ratio is not None and ratio > 0:
                factor *= ratio
    return factor_by_date


def _resolve_roll_ratio(row: pd.Series | dict[str, object], config: ContinuousSeriesConfig) -> float | None:
    ratio = _coerce_float(row.get("ratio_adjustment"))
    if ratio is not None and ratio > 0 and isfinite(ratio):
        return ratio
    from_settle = _coerce_float(row.get("from_settle"))
    to_settle = _coerce_float(row.get("to_settle"))
    if from_settle is not None and from_settle > 0 and to_settle is not None and to_settle > 0:
        recomputed = to_settle / from_settle
        if recomputed > 0 and isfinite(recomputed):
            return recomputed
    if config.strict_roll_ratio:
        raise ContinuousBuildError(
            "invalid_roll_ratio_in_strict_mode",
            "strict mode requires a positive finite ratio_adjustment or recomputable settle ratio.",
            {"roll_event_id": _string_or_none(row.get("roll_event_id"))},
        )
    return None


def _lookup_contract_row(
    lookup: pd.DataFrame,
    trade_date: date,
    raw_symbol: str,
) -> dict[str, object] | None:
    if lookup.empty or not raw_symbol:
        return None
    try:
        row = lookup.loc[(trade_date, raw_symbol)]
    except KeyError:
        return None
    if isinstance(row, pd.DataFrame):
        row = row.iloc[-1]
    return row.to_dict()


def _filter_relevant_roll_events(roll_events: pd.DataFrame, lead_map: pd.DataFrame) -> pd.DataFrame:
    if roll_events.empty or lead_map.empty:
        return roll_events.iloc[0:0].copy()
    roots = sorted(lead_map["root"].dropna().astype(str).unique())
    start_date = pd.to_datetime(lead_map["as_of_date"]).dt.date.min()
    end_date = pd.to_datetime(lead_map["as_of_date"]).dt.date.max()
    filtered = roll_events.copy()
    filtered["effective_date"] = pd.to_datetime(filtered["effective_date"]).dt.date
    return filtered[
        filtered["root"].astype(str).isin(roots)
        & (filtered["effective_date"] >= start_date)
        & (filtered["effective_date"] <= end_date)
    ].reset_index(drop=True)


def _row_has_valid_ratio(row: pd.Series) -> bool:
    ratio = _coerce_float(row.get("ratio_adjustment"))
    if ratio is not None and ratio > 0 and isfinite(ratio):
        return True
    from_settle = _coerce_float(row.get("from_settle"))
    to_settle = _coerce_float(row.get("to_settle"))
    if from_settle is None or to_settle is None or from_settle <= 0 or to_settle <= 0:
        return False
    recomputed = to_settle / from_settle
    return recomputed > 0 and isfinite(recomputed)


def _row_has_large_basis(row: pd.Series) -> bool:
    from_settle = _coerce_float(row.get("from_settle"))
    basis = _coerce_float(row.get("basis_at_roll"))
    if from_settle is None or from_settle == 0 or basis is None:
        return False
    return abs(basis / from_settle) > 0.20


def _count_flag(df: pd.DataFrame, flag: str) -> int:
    if "quality_flags" not in df.columns or df.empty:
        return 0
    return int(df["quality_flags"].map(lambda value: flag in _normalize_flags(value)).sum())


def _normalize_flags(value: Any) -> list[str]:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return []
    if isinstance(value, list):
        return [str(item) for item in value]
    if isinstance(value, tuple):
        return [str(item) for item in value]
    if isinstance(value, set):
        return sorted(str(item) for item in value)
    if isinstance(value, str):
        return [value] if value else []
    return [str(value)]


def _coverage_by_root(df: pd.DataFrame, date_column: str) -> dict[str, dict[str, str | None]]:
    if df.empty or date_column not in df.columns or "root" not in df.columns:
        return {}
    output: dict[str, dict[str, str | None]] = {}
    for root, group in df.groupby("root", sort=True):
        dates = pd.to_datetime(group[date_column]).dt.date
        output[str(root)] = {
            "min_date": dates.min().isoformat() if not dates.empty else None,
            "max_date": dates.max().isoformat() if not dates.empty else None,
        }
    return output


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


def _is_invalid_factor(value: Any) -> bool:
    numeric = _coerce_float(value)
    return numeric is None or numeric <= 0 or not isfinite(numeric)


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


def _string_or_none(value: Any) -> str | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    text = str(value).strip()
    return text or None


def _first_string(series: pd.Series | None) -> str | None:
    if series is None or series.empty:
        return None
    cleaned = series.dropna()
    if cleaned.empty:
        return None
    return _string_or_none(cleaned.iloc[0])
