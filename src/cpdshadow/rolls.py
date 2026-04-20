from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Any, Mapping, Sequence

import pandas as pd

from cpdshadow.ids import stable_sha256_hex


FUTURE_CLASSES = {"F", "FUTURE"}
LEAD_MAP_COLUMNS = [
    "as_of_date",
    "root",
    "roll_policy_version",
    "lead_raw_symbol",
    "next_raw_symbol",
    "prev_lead_raw_symbol",
    "roll_flag",
    "roll_event_id",
    "days_to_expiry",
    "front_volume_tminus1",
    "next_volume_tminus1",
    "confirmation_count",
    "hard_roll_deadline",
    "selection_reason",
    "builder_version",
    "snapshot_id",
]
ROLL_EVENTS_COLUMNS = [
    "roll_event_id",
    "root",
    "from_raw_symbol",
    "to_raw_symbol",
    "trigger_date",
    "effective_date",
    "roll_reason",
    "front_volume_tminus1",
    "next_volume_tminus1",
    "confirmation_count",
    "from_settle",
    "to_settle",
    "ratio_adjustment",
    "basis_at_roll",
    "builder_version",
    "override_id",
]


@dataclass(frozen=True)
class RollQaIssue:
    code: str
    severity: str
    message: str
    details: dict[str, object] = field(default_factory=dict)


class RollBuildError(ValueError):
    def __init__(self, code: str, message: str, details: dict[str, object] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.details = details or {}


@dataclass(frozen=True)
class RollQaReport:
    snapshot_id: str
    policy_version: str
    builder_version: str
    summary: dict[str, object]
    issues: tuple[RollQaIssue, ...] = ()

    @property
    def has_errors(self) -> bool:
        return any(issue.severity == "error" for issue in self.issues)

    @property
    def has_warnings(self) -> bool:
        return any(issue.severity == "warning" for issue in self.issues)

    def with_additional_issues(self, issues: Sequence[RollQaIssue]) -> RollQaReport:
        merged = sorted([*self.issues, *issues], key=lambda item: (item.severity, item.code, item.message))
        summary = dict(self.summary)
        summary["fatal_error_count"] = sum(issue.severity == "error" for issue in merged)
        summary["warning_count"] = sum(issue.severity == "warning" for issue in merged)
        return RollQaReport(
            snapshot_id=self.snapshot_id,
            policy_version=self.policy_version,
            builder_version=self.builder_version,
            summary=summary,
            issues=tuple(merged),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "snapshot_id": self.snapshot_id,
            "policy_version": self.policy_version,
            "builder_version": self.builder_version,
            "has_errors": self.has_errors,
            "has_warnings": self.has_warnings,
            "summary": self.summary,
            "issues": [asdict(issue) for issue in self.issues],
        }

    def to_markdown(self) -> str:
        lines = [
            f"# WP5 Roll QA {self.snapshot_id}",
            "",
            f"- Policy: `{self.policy_version}`",
            f"- Builder: `{self.builder_version}`",
            f"- Fatal errors: {self.summary.get('fatal_error_count', 0)}",
            f"- Warnings: {self.summary.get('warning_count', 0)}",
            f"- Lead map rows: {self.summary.get('lead_map_rows', 0)}",
            f"- Roll event rows: {self.summary.get('roll_event_rows', 0)}",
            "",
            "## Summary",
            "",
            f"- Root coverage: {self.summary.get('root_coverage', {})}",
            f"- Date coverage: {self.summary.get('date_coverage', {})}",
            f"- Roll events by reason: {self.summary.get('roll_events_by_reason', {})}",
            f"- Missing volume count: {self.summary.get('missing_volume_count', 0)}",
            f"- Missing settlement count: {self.summary.get('missing_settlement_count_at_roll', 0)}",
            (
                "- Hard-roll proximity stats: "
                f"{self.summary.get('hard_roll_deadline_proximity', {})}"
            ),
            "",
            "## Issues",
            "",
        ]
        if not self.issues:
            lines.append("- None")
            return "\n".join(lines)
        for issue in self.issues:
            detail_text = ""
            if issue.details:
                detail_text = f" {issue.details}"
            lines.append(f"- `{issue.severity}` `{issue.code}`: {issue.message}{detail_text}")
        return "\n".join(lines)


def build_root_calendar(contracts_daily: pd.DataFrame, root: str) -> list[date]:
    root_daily = contracts_daily[contracts_daily["root"] == root].copy()
    if root_daily.empty:
        return []
    calendar = pd.to_datetime(root_daily["trade_date"]).dt.date.dropna().drop_duplicates().sort_values()
    return calendar.tolist()


def filter_outright_contracts(contract_master: pd.DataFrame, root: str) -> pd.DataFrame:
    root_contracts = contract_master[contract_master["root"] == root].copy()
    if root_contracts.empty:
        return _empty_contracts_df(root_contracts.columns.tolist())
    if "valid_from_utc" in root_contracts.columns:
        root_contracts = root_contracts.sort_values(["raw_symbol", "valid_from_utc"])
        root_contracts = root_contracts.drop_duplicates(subset=["raw_symbol"], keep="last")
    instrument_class = root_contracts["instrument_class"].astype(str).str.upper()
    raw_symbol = root_contracts["raw_symbol"].astype(str)
    mask = instrument_class.isin(FUTURE_CLASSES) & ~raw_symbol.map(_looks_continuous_symbol)
    return root_contracts.loc[mask].reset_index(drop=True)


def sort_contracts_by_expiry(contracts: pd.DataFrame) -> pd.DataFrame:
    if contracts.empty:
        return contracts.copy()
    sorted_df = contracts.copy()
    sorted_df["_primary_expiry"] = sorted_df.apply(_primary_expiry_date, axis=1)
    sorted_df["_expiration_sort"] = sorted_df["expiration_date"].map(_coerce_date)
    sorted_df = sorted_df.sort_values(
        by=["_primary_expiry", "_expiration_sort", "raw_symbol"],
        kind="stable",
        na_position="last",
    )
    return sorted_df.drop(columns=["_primary_expiry", "_expiration_sort"]).reset_index(drop=True)


def compute_hard_roll_deadline(
    contract_row: Mapping[str, Any],
    root_calendar: Sequence[date],
    hard_roll_days: int,
) -> date:
    if not root_calendar:
        raise RollBuildError("empty_root_calendar", "root calendar is empty")
    anchor_date = _hard_roll_anchor_date(contract_row)
    if anchor_date is None:
        raw_symbol = contract_row.get("raw_symbol", "<unknown>")
        raise RollBuildError(
            "missing_contract_expiry",
            f"contract {raw_symbol} is missing last_trade_date and expiration_date",
            {"raw_symbol": raw_symbol},
        )
    anchor_on_calendar = _nearest_calendar_date(root_calendar, anchor_date)
    if anchor_on_calendar is None:
        raise RollBuildError(
            "anchor_before_calendar_start",
            f"contract anchor date {anchor_date.isoformat()} is before the root calendar starts",
            {"anchor_date": anchor_date.isoformat()},
        )
    anchor_index = root_calendar.index(anchor_on_calendar)
    deadline_index = max(anchor_index - int(hard_roll_days), 0)
    return root_calendar[deadline_index]


def select_initial_lead(
    as_of_date: date,
    sorted_contracts: pd.DataFrame,
    root_calendar: Sequence[date],
    hard_roll_days: int,
) -> str:
    for _, row in sorted_contracts.iterrows():
        if not _is_eligible_contract(row, as_of_date, rolled_away=set()):
            continue
        deadline = compute_hard_roll_deadline(row, root_calendar, hard_roll_days)
        if as_of_date >= deadline:
            continue
        return str(row["raw_symbol"])
    raise RollBuildError(
        "no_eligible_initial_lead",
        f"no eligible initial lead for {as_of_date.isoformat()}",
        {"as_of_date": as_of_date.isoformat()},
    )


def next_contract_after(
    lead_raw_symbol: str,
    sorted_contracts: pd.DataFrame,
    as_of_date: date,
) -> str | None:
    return _next_contract_after(
        lead_raw_symbol,
        sorted_contracts,
        as_of_date,
        rolled_away=set(),
    )


def build_lead_map_for_root(
    root: str,
    contract_master: pd.DataFrame,
    contracts_daily: pd.DataFrame,
    instrument_config: Mapping[str, Any],
    roll_config: Mapping[str, Any],
    snapshot_id: str,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    full_root_calendar = build_root_calendar(contracts_daily, root)
    start_date = _coerce_optional_date(roll_config.get("start_date"))
    end_date = _coerce_optional_date(roll_config.get("end_date"))
    build_calendar = [
        trade_date for trade_date in full_root_calendar
        if (start_date is None or trade_date >= start_date)
        and (end_date is None or trade_date <= end_date)
    ]
    if not build_calendar:
        return _empty_lead_map_df(), _empty_roll_events_df()

    sorted_contracts = sort_contracts_by_expiry(filter_outright_contracts(contract_master, root))
    if sorted_contracts.empty:
        raise RollBuildError(
            "no_eligible_initial_lead",
            f"no outright futures available for root {root}",
            {"root": root},
        )
    if "hard_roll_anchor" not in sorted_contracts.columns:
        sorted_contracts = sorted_contracts.assign(
            hard_roll_anchor=instrument_config.get("hard_roll_anchor", "last_trade_date")
        )

    hard_roll_days = int(instrument_config["hard_roll_business_days_before"])
    current_lead = select_initial_lead(
        build_calendar[0],
        sorted_contracts,
        full_root_calendar,
        hard_roll_days,
    )
    root_daily = _prepare_root_daily_lookup(contracts_daily, root)
    policy_version = str(roll_config["policy_version"])
    builder_version = str(roll_config["builder_version"])
    confirmation_days = int(roll_config["volume_confirmation_days"])
    effective_lag_days = int(roll_config["effective_lag_trading_days"])

    lead_rows: list[dict[str, Any]] = []
    roll_rows: list[dict[str, Any]] = []
    rolled_away: set[str] = set()
    pending_volume_roll: dict[str, Any] | None = None
    confirmation_count = 0
    confirmation_pair: tuple[str, str | None] | None = None
    prior_day_lead: str | None = None

    for idx, as_of_date in enumerate(build_calendar):
        current_row = _require_contract_row(
            current_lead,
            sorted_contracts,
            as_of_date,
            rolled_away=rolled_away,
        )
        pre_roll_next = _next_contract_after(
            current_lead,
            sorted_contracts,
            as_of_date,
            rolled_away=rolled_away,
        )
        current_deadline = compute_hard_roll_deadline(current_row, full_root_calendar, hard_roll_days)
        if as_of_date > current_deadline and pre_roll_next is not None:
            raise RollBuildError(
                "lead_past_hard_roll_deadline",
                f"lead {current_lead} passed hard-roll deadline on {as_of_date.isoformat()}",
                {"root": root, "lead_raw_symbol": current_lead, "as_of_date": as_of_date.isoformat()},
            )

        prev_build_date = build_calendar[idx - 1] if idx > 0 else None
        front_volume_prev = _volume_on_date(root_daily, prev_build_date, current_lead)
        next_volume_prev = _volume_on_date(root_daily, prev_build_date, pre_roll_next)
        pair = (current_lead, pre_roll_next)
        if confirmation_pair != pair:
            confirmation_count = 0
            confirmation_pair = pair
            pending_volume_roll = None

        if prev_build_date is None or pre_roll_next is None:
            confirmation_count = 0
        elif _valid_volume_pair(front_volume_prev, next_volume_prev) and next_volume_prev > front_volume_prev:
            confirmation_count += 1
            if confirmation_count >= confirmation_days and pending_volume_roll is None:
                effective_date = _nth_next_date(build_calendar, prev_build_date, effective_lag_days)
                if effective_date is not None:
                    pending_volume_roll = {
                        "from_raw_symbol": current_lead,
                        "to_raw_symbol": pre_roll_next,
                        "trigger_date": prev_build_date,
                        "effective_date": effective_date,
                        "front_volume_tminus1": front_volume_prev,
                        "next_volume_tminus1": next_volume_prev,
                        "confirmation_count": confirmation_count,
                    }
        else:
            confirmation_count = 0
            pending_volume_roll = None

        hard_roll_today = as_of_date == current_deadline
        volume_roll_today = (
            pending_volume_roll is not None
            and pending_volume_roll["effective_date"] == as_of_date
            and pending_volume_roll["from_raw_symbol"] == current_lead
        )
        selection_reason = "carry_forward"
        roll_event_id: str | None = None

        if hard_roll_today:
            if pre_roll_next is None:
                raise RollBuildError(
                    "lead_past_hard_roll_deadline",
                    f"lead {current_lead} hit hard-roll deadline with no next eligible contract",
                    {"root": root, "lead_raw_symbol": current_lead, "as_of_date": as_of_date.isoformat()},
                )
            roll_event = _build_roll_event(
                root=root,
                from_raw_symbol=current_lead,
                to_raw_symbol=pre_roll_next,
                trigger_date=prev_build_date or as_of_date,
                effective_date=as_of_date,
                roll_reason="hard_roll",
                front_volume_tminus1=front_volume_prev,
                next_volume_tminus1=next_volume_prev,
                confirmation_count=confirmation_count,
                builder_version=builder_version,
                policy_version=policy_version,
                snapshot_id=snapshot_id,
                root_daily=root_daily,
            )
            roll_rows.append(roll_event)
            rolled_away.add(current_lead)
            current_lead = pre_roll_next
            selection_reason = "hard_roll"
            roll_event_id = str(roll_event["roll_event_id"])
            confirmation_count = 0
            confirmation_pair = None
            pending_volume_roll = None
        elif volume_roll_today and pending_volume_roll is not None:
            roll_event = _build_roll_event(
                root=root,
                from_raw_symbol=str(pending_volume_roll["from_raw_symbol"]),
                to_raw_symbol=str(pending_volume_roll["to_raw_symbol"]),
                trigger_date=_coerce_date(pending_volume_roll["trigger_date"]) or as_of_date,
                effective_date=as_of_date,
                roll_reason="volume_3day",
                front_volume_tminus1=_coerce_float(pending_volume_roll["front_volume_tminus1"]),
                next_volume_tminus1=_coerce_float(pending_volume_roll["next_volume_tminus1"]),
                confirmation_count=int(pending_volume_roll["confirmation_count"]),
                builder_version=builder_version,
                policy_version=policy_version,
                snapshot_id=snapshot_id,
                root_daily=root_daily,
            )
            roll_rows.append(roll_event)
            rolled_away.add(current_lead)
            current_lead = str(pending_volume_roll["to_raw_symbol"])
            selection_reason = "volume_3day"
            roll_event_id = str(roll_event["roll_event_id"])
            confirmation_count = 0
            confirmation_pair = None
            pending_volume_roll = None

        post_roll_next = _next_contract_after(
            current_lead,
            sorted_contracts,
            as_of_date,
            rolled_away=rolled_away,
        )
        row_front_volume = _volume_on_date(root_daily, prev_build_date, current_lead)
        row_next_volume = _volume_on_date(root_daily, prev_build_date, post_roll_next)
        lead_row = _require_contract_row(current_lead, sorted_contracts, as_of_date, rolled_away=rolled_away)
        lead_deadline = compute_hard_roll_deadline(lead_row, full_root_calendar, hard_roll_days)
        lead_rows.append({
            "as_of_date": as_of_date,
            "root": root,
            "roll_policy_version": policy_version,
            "lead_raw_symbol": current_lead,
            "next_raw_symbol": post_roll_next,
            "prev_lead_raw_symbol": prior_day_lead,
            "roll_flag": roll_event_id is not None,
            "roll_event_id": roll_event_id,
            "days_to_expiry": _days_to_expiry(lead_row, as_of_date),
            "front_volume_tminus1": row_front_volume,
            "next_volume_tminus1": row_next_volume,
            "confirmation_count": confirmation_count,
            "hard_roll_deadline": lead_deadline,
            "selection_reason": selection_reason,
            "builder_version": builder_version,
            "snapshot_id": snapshot_id,
        })
        prior_day_lead = current_lead

    lead_map = pd.DataFrame(lead_rows, columns=LEAD_MAP_COLUMNS)
    roll_events = pd.DataFrame(roll_rows, columns=ROLL_EVENTS_COLUMNS)
    return lead_map, roll_events


def validate_lead_map(
    lead_map: pd.DataFrame,
    roll_events: pd.DataFrame,
    contract_master: pd.DataFrame,
    contracts_daily: pd.DataFrame,
) -> RollQaReport:
    snapshot_id = _first_string(lead_map.get("snapshot_id")) if not lead_map.empty else "unknown"
    policy_version = _first_string(lead_map.get("roll_policy_version")) if not lead_map.empty else "unknown"
    builder_version = (
        _first_string(lead_map.get("builder_version"))
        if not lead_map.empty
        else _first_string(roll_events.get("builder_version")) or "unknown"
    )
    issues: list[RollQaIssue] = []

    lead_map = lead_map.copy()
    roll_events = roll_events.copy()
    if not lead_map.empty:
        lead_map["as_of_date"] = pd.to_datetime(lead_map["as_of_date"]).dt.date
        lead_map["hard_roll_deadline"] = pd.to_datetime(lead_map["hard_roll_deadline"]).dt.date
    if not roll_events.empty:
        roll_events["trigger_date"] = pd.to_datetime(roll_events["trigger_date"]).dt.date
        roll_events["effective_date"] = pd.to_datetime(roll_events["effective_date"]).dt.date

    if not lead_map.empty:
        duplicates = lead_map[lead_map.duplicated(subset=["as_of_date", "root"], keep=False)]
        if not duplicates.empty:
            issues.append(RollQaIssue(
                code="duplicate_primary_keys",
                severity="error",
                message="lead_map contains duplicate (as_of_date, root) keys.",
                details={"count": int(len(duplicates))},
            ))
    if not roll_events.empty:
        duplicates = roll_events[roll_events.duplicated(subset=["roll_event_id"], keep=False)]
        if not duplicates.empty:
            issues.append(RollQaIssue(
                code="duplicate_primary_keys",
                severity="error",
                message="roll_events contains duplicate roll_event_id values.",
                details={"count": int(len(duplicates))},
            ))

    contract_lookup = _contract_lookup(contract_master)
    rank_lookup = _rank_lookup(contract_master)
    event_ids = set(roll_events["roll_event_id"].dropna().astype(str)) if not roll_events.empty else set()
    rolled_away_by_root: dict[str, set[str]] = {}

    for root, group in lead_map.sort_values(["root", "as_of_date"]).groupby("root", sort=False):
        previous_rank: int | None = None
        previous_lead: str | None = None
        root_calendar = build_root_calendar(contracts_daily, root)
        for _, row in group.iterrows():
            lead_symbol = str(row["lead_raw_symbol"])
            next_symbol = _first_string(pd.Series([row.get("next_raw_symbol")]))
            if _looks_continuous_symbol(lead_symbol):
                issues.append(RollQaIssue(
                    code="continuous_or_synthetic_symbol_selected",
                    severity="error",
                    message=f"lead_raw_symbol {lead_symbol} looks continuous or synthetic.",
                    details={"root": root, "as_of_date": row["as_of_date"].isoformat()},
                ))
            if lead_symbol not in contract_lookup.get(root, {}):
                issues.append(RollQaIssue(
                    code="lead_raw_symbol_missing_from_contract_master",
                    severity="error",
                    message=f"lead_raw_symbol {lead_symbol} is missing from contract_master for {root}.",
                    details={"root": root, "as_of_date": row["as_of_date"].isoformat()},
                ))
                continue
            if next_symbol and next_symbol == lead_symbol:
                issues.append(RollQaIssue(
                    code="next_raw_symbol_equals_lead_raw_symbol",
                    severity="error",
                    message="next_raw_symbol equals lead_raw_symbol.",
                    details={"root": root, "as_of_date": row["as_of_date"].isoformat()},
                ))
            if row.get("roll_event_id") and str(row["roll_event_id"]) not in event_ids:
                issues.append(RollQaIssue(
                    code="missing_roll_event_referenced_by_lead_map",
                    severity="error",
                    message="lead_map references a missing roll_event_id.",
                    details={"root": root, "roll_event_id": row["roll_event_id"]},
                ))
            deadline = _coerce_date(row.get("hard_roll_deadline"))
            if deadline is not None and row["as_of_date"] > deadline and next_symbol is not None:
                issues.append(RollQaIssue(
                    code="lead_past_hard_roll_deadline",
                    severity="error",
                    message="lead passed its hard-roll deadline while a next eligible contract exists.",
                    details={"root": root, "as_of_date": row["as_of_date"].isoformat()},
                ))

            current_rank = rank_lookup.get(root, {}).get(lead_symbol)
            if current_rank is not None and previous_rank is not None and current_rank < previous_rank:
                issues.append(RollQaIssue(
                    code="non_monotonic_expiry_order",
                    severity="error",
                    message="lead sequence moved backward in expiry order.",
                    details={"root": root, "as_of_date": row["as_of_date"].isoformat()},
                ))
            if previous_lead is not None and lead_symbol != previous_lead:
                rolled_away_by_root.setdefault(root, set()).add(previous_lead)
            if (
                previous_lead is not None
                and lead_symbol in rolled_away_by_root.setdefault(root, set())
                and row["selection_reason"] != "manual_override"
            ):
                issues.append(RollQaIssue(
                    code="rollback_to_old_contract",
                    severity="error",
                    message="a rolled-away contract became lead again without manual override.",
                    details={"root": root, "as_of_date": row["as_of_date"].isoformat()},
                ))
            previous_rank = current_rank if current_rank is not None else previous_rank
            previous_lead = lead_symbol

            if next_symbol and (
                pd.isna(row.get("front_volume_tminus1")) or pd.isna(row.get("next_volume_tminus1"))
            ):
                issues.append(RollQaIssue(
                    code="missing_volume",
                    severity="warning",
                    message="missing lead or next volume on the comparison date.",
                    details={"root": root, "as_of_date": row["as_of_date"].isoformat()},
                ))

            if (
                row["selection_reason"] == "volume_3day"
                and deadline is not None
                and _sessions_until_deadline(root_calendar, row["as_of_date"], deadline) <= 2
            ):
                issues.append(RollQaIssue(
                    code="late_volume_roll",
                    severity="warning",
                    message="volume roll occurred within two root sessions of the hard-roll deadline.",
                    details={"root": root, "as_of_date": row["as_of_date"].isoformat()},
                ))

    for _, row in roll_events.iterrows():
        if row["roll_reason"] == "volume_3day" and row["effective_date"] <= row["trigger_date"]:
            issues.append(RollQaIssue(
                code="volume_3day_effective_date_le_trigger_date",
                severity="error",
                message="volume_3day roll has effective_date <= trigger_date.",
                details={"roll_event_id": row["roll_event_id"]},
            ))
        if pd.isna(row.get("from_settle")) or pd.isna(row.get("to_settle")):
            issues.append(RollQaIssue(
                code="missing_settlement_at_roll",
                severity="warning",
                message="missing settlement prevented a complete roll reference calculation.",
                details={"roll_event_id": row["roll_event_id"]},
            ))
        from_settle = _coerce_float(row.get("from_settle"))
        basis = _coerce_float(row.get("basis_at_roll"))
        if from_settle not in (None, 0.0) and basis is not None:
            if abs(basis / from_settle) > 0.20:
                issues.append(RollQaIssue(
                    code="large_basis_at_roll",
                    severity="warning",
                    message="basis at roll is large relative to the front settle.",
                    details={"roll_event_id": row["roll_event_id"]},
                ))

    root_coverage = lead_map.groupby("root")["as_of_date"].count().to_dict() if not lead_map.empty else {}
    date_coverage = {
        "min_as_of_date": lead_map["as_of_date"].min().isoformat() if not lead_map.empty else None,
        "max_as_of_date": lead_map["as_of_date"].max().isoformat() if not lead_map.empty else None,
    }
    hard_roll_proximity = _hard_roll_proximity_stats(lead_map, contracts_daily)
    summary = {
        "root_coverage": root_coverage,
        "date_coverage": date_coverage,
        "lead_map_rows": int(len(lead_map)),
        "roll_event_rows": int(len(roll_events)),
        "roll_events_by_reason": (
            roll_events.groupby("roll_reason")["roll_event_id"].count().to_dict()
            if not roll_events.empty
            else {}
        ),
        "missing_volume_count": int(sum(issue.code == "missing_volume" for issue in issues)),
        "missing_settlement_count_at_roll": int(
            sum(issue.code == "missing_settlement_at_roll" for issue in issues)
        ),
        "hard_roll_deadline_proximity": hard_roll_proximity,
        "fatal_error_count": int(sum(issue.severity == "error" for issue in issues)),
        "warning_count": int(sum(issue.severity == "warning" for issue in issues)),
    }
    sorted_issues = tuple(sorted(issues, key=lambda item: (item.severity, item.code, item.message)))
    return RollQaReport(
        snapshot_id=snapshot_id,
        policy_version=policy_version,
        builder_version=builder_version,
        summary=summary,
        issues=sorted_issues,
    )


def _empty_contracts_df(columns: Sequence[str]) -> pd.DataFrame:
    return pd.DataFrame(columns=list(columns))


def _empty_lead_map_df() -> pd.DataFrame:
    return pd.DataFrame(columns=LEAD_MAP_COLUMNS)


def _empty_roll_events_df() -> pd.DataFrame:
    return pd.DataFrame(columns=ROLL_EVENTS_COLUMNS)


def _primary_expiry_date(row: Mapping[str, Any]) -> date | None:
    return _coerce_date(row.get("last_trade_date")) or _coerce_date(row.get("expiration_date"))


def _hard_roll_anchor_date(contract_row: Mapping[str, Any]) -> date | None:
    anchor = str(contract_row.get("hard_roll_anchor", "last_trade_date"))
    if anchor == "first_notice_or_last_trade_date":
        return (
            _coerce_date(contract_row.get("first_notice_date"))
            or _coerce_date(contract_row.get("last_trade_date"))
            or _coerce_date(contract_row.get("expiration_date"))
        )
    return _coerce_date(contract_row.get("last_trade_date")) or _coerce_date(contract_row.get("expiration_date"))


def _nearest_calendar_date(root_calendar: Sequence[date], anchor_date: date) -> date | None:
    candidates = [trade_date for trade_date in root_calendar if trade_date <= anchor_date]
    if not candidates:
        return None
    return candidates[-1]


def _is_eligible_contract(
    row: Mapping[str, Any],
    as_of_date: date,
    *,
    rolled_away: set[str],
) -> bool:
    raw_symbol = str(row["raw_symbol"])
    if raw_symbol in rolled_away:
        return False
    first_trade_date = _coerce_date(row.get("first_trade_date"))
    expiry_date = _primary_expiry_date(row)
    if expiry_date is None:
        return False
    if first_trade_date is not None and first_trade_date > as_of_date:
        return False
    return expiry_date >= as_of_date


def _next_contract_after(
    lead_raw_symbol: str,
    sorted_contracts: pd.DataFrame,
    as_of_date: date,
    *,
    rolled_away: set[str],
) -> str | None:
    matches = sorted_contracts.index[sorted_contracts["raw_symbol"].astype(str) == lead_raw_symbol].tolist()
    if not matches:
        return None
    current_index = matches[0]
    for idx in range(current_index + 1, len(sorted_contracts)):
        row = sorted_contracts.iloc[idx]
        if _is_eligible_contract(row, as_of_date, rolled_away=rolled_away):
            return str(row["raw_symbol"])
    return None


def _require_contract_row(
    raw_symbol: str,
    sorted_contracts: pd.DataFrame,
    as_of_date: date,
    *,
    rolled_away: set[str],
) -> Mapping[str, Any]:
    matches = sorted_contracts[sorted_contracts["raw_symbol"].astype(str) == raw_symbol]
    if matches.empty:
        raise RollBuildError(
            "lead_raw_symbol_missing_from_contract_master",
            f"missing contract row for {raw_symbol}",
            {"raw_symbol": raw_symbol},
        )
    row = matches.iloc[0]
    if not _is_eligible_contract(row, as_of_date, rolled_away=rolled_away):
        raise RollBuildError(
            "contract_no_longer_eligible",
            f"contract {raw_symbol} is no longer eligible on {as_of_date.isoformat()}",
            {"raw_symbol": raw_symbol, "as_of_date": as_of_date.isoformat()},
        )
    return row


def _prepare_root_daily_lookup(contracts_daily: pd.DataFrame, root: str) -> pd.DataFrame:
    root_daily = contracts_daily[contracts_daily["root"] == root].copy()
    if root_daily.empty:
        return root_daily
    root_daily["trade_date"] = pd.to_datetime(root_daily["trade_date"]).dt.date
    root_daily = root_daily.sort_values(["trade_date", "raw_symbol"])
    return root_daily.set_index(["trade_date", "raw_symbol"], drop=False)


def _volume_on_date(root_daily: pd.DataFrame, trade_date: date | None, raw_symbol: str | None) -> float | None:
    return _lookup_value(root_daily, trade_date, raw_symbol, "volume")


def _roll_price_on_date(root_daily: pd.DataFrame, trade_date: date, raw_symbol: str) -> float | None:
    settle = _lookup_value(root_daily, trade_date, raw_symbol, "settle_price")
    if settle is not None:
        return settle
    settle_status = _lookup_value(root_daily, trade_date, raw_symbol, "settle_status")
    if settle_status == "close_fallback":
        return _lookup_value(root_daily, trade_date, raw_symbol, "close_price")
    return None


def _lookup_value(
    root_daily: pd.DataFrame,
    trade_date: date | None,
    raw_symbol: str | None,
    column: str,
) -> Any:
    if trade_date is None or raw_symbol is None or root_daily.empty:
        return None
    try:
        value = root_daily.loc[(trade_date, raw_symbol), column]
    except KeyError:
        return None
    if isinstance(value, pd.Series):
        value = value.iloc[-1]
    if pd.isna(value):
        return None
    if column == "settle_status":
        return str(value)
    return float(value) if column not in {"trade_date", "raw_symbol"} else value


def _valid_volume_pair(front: float | None, next_value: float | None) -> bool:
    if front is None or next_value is None:
        return False
    return front >= 0 and next_value >= 0


def _nth_next_date(calendar: Sequence[date], current_date: date, offset: int) -> date | None:
    if offset < 1:
        raise ValueError("offset must be >= 1")
    if current_date not in calendar:
        return None
    index = calendar.index(current_date) + offset
    if index >= len(calendar):
        return None
    return calendar[index]


def _build_roll_event(
    *,
    root: str,
    from_raw_symbol: str,
    to_raw_symbol: str,
    trigger_date: date,
    effective_date: date,
    roll_reason: str,
    front_volume_tminus1: float | None,
    next_volume_tminus1: float | None,
    confirmation_count: int,
    builder_version: str,
    policy_version: str,
    snapshot_id: str,
    root_daily: pd.DataFrame,
) -> dict[str, Any]:
    from_settle = _roll_price_on_date(root_daily, trigger_date, from_raw_symbol)
    to_settle = _roll_price_on_date(root_daily, trigger_date, to_raw_symbol)
    basis_at_roll = None
    ratio_adjustment = None
    if from_settle is not None and to_settle is not None:
        basis_at_roll = to_settle - from_settle
        if from_settle > 0:
            ratio_adjustment = to_settle / from_settle
    roll_event_id = (
        "roll_"
        + stable_sha256_hex(
            f"{policy_version}|{root}|{from_raw_symbol}|{to_raw_symbol}|"
            f"{trigger_date.isoformat()}|{effective_date.isoformat()}|{roll_reason}|{snapshot_id}"
        )[:16]
    )
    return {
        "roll_event_id": roll_event_id,
        "root": root,
        "from_raw_symbol": from_raw_symbol,
        "to_raw_symbol": to_raw_symbol,
        "trigger_date": trigger_date,
        "effective_date": effective_date,
        "roll_reason": roll_reason,
        "front_volume_tminus1": front_volume_tminus1,
        "next_volume_tminus1": next_volume_tminus1,
        "confirmation_count": confirmation_count,
        "from_settle": from_settle,
        "to_settle": to_settle,
        "ratio_adjustment": ratio_adjustment,
        "basis_at_roll": basis_at_roll,
        "builder_version": builder_version,
        "override_id": None,
    }


def _days_to_expiry(contract_row: Mapping[str, Any], as_of_date: date) -> int | None:
    expiry_date = _primary_expiry_date(contract_row)
    if expiry_date is None:
        return None
    return (expiry_date - as_of_date).days


def _contract_lookup(contract_master: pd.DataFrame) -> dict[str, dict[str, Mapping[str, Any]]]:
    by_root: dict[str, dict[str, Mapping[str, Any]]] = {}
    if contract_master.empty:
        return by_root
    for root in sorted(contract_master["root"].dropna().astype(str).unique()):
        for _, row in sort_contracts_by_expiry(filter_outright_contracts(contract_master, root)).iterrows():
            by_root.setdefault(str(root), {})[str(row["raw_symbol"])] = row
    return by_root


def _rank_lookup(contract_master: pd.DataFrame) -> dict[str, dict[str, int]]:
    ranks: dict[str, dict[str, int]] = {}
    if contract_master.empty:
        return ranks
    for root in sorted(contract_master["root"].dropna().astype(str).unique()):
        sorted_contracts = sort_contracts_by_expiry(filter_outright_contracts(contract_master, root))
        ranks[root] = {
            str(row["raw_symbol"]): idx
            for idx, (_, row) in enumerate(sorted_contracts.iterrows())
        }
    return ranks


def _sessions_until_deadline(root_calendar: Sequence[date], as_of_date: date, deadline: date | None) -> int:
    if deadline is None or as_of_date not in root_calendar:
        return 9999
    nearest_deadline = _nearest_calendar_date(root_calendar, deadline)
    if nearest_deadline is None or nearest_deadline not in root_calendar:
        return 9999
    return max(root_calendar.index(nearest_deadline) - root_calendar.index(as_of_date), 0)


def _hard_roll_proximity_stats(lead_map: pd.DataFrame, contracts_daily: pd.DataFrame) -> dict[str, object]:
    if lead_map.empty:
        return {"minimum_sessions_to_deadline": None, "rows_within_two_sessions": 0}
    minimum: int | None = None
    within_two = 0
    calendars = {
        root: build_root_calendar(contracts_daily, root)
        for root in sorted(lead_map["root"].dropna().astype(str).unique())
    }
    for _, row in lead_map.iterrows():
        deadline = _coerce_date(row.get("hard_roll_deadline"))
        if deadline is None:
            continue
        sessions = _sessions_until_deadline(calendars.get(str(row["root"]), []), row["as_of_date"], deadline)
        if minimum is None or sessions < minimum:
            minimum = sessions
        if sessions <= 2:
            within_two += 1
    return {
        "minimum_sessions_to_deadline": minimum,
        "rows_within_two_sessions": within_two,
    }


def _looks_continuous_symbol(raw_symbol: str) -> bool:
    upper = raw_symbol.upper()
    if ".FUT" in upper or "CONT" in upper or ".C." in upper:
        return True
    return upper.endswith("1!") or upper.endswith("2!")


def _coerce_date(value: Any) -> date | None:
    if value is None or pd.isna(value):
        return None
    return pd.Timestamp(value).date()


def _coerce_optional_date(value: Any) -> date | None:
    if value in (None, ""):
        return None
    return _coerce_date(value)


def _coerce_float(value: Any) -> float | None:
    if value is None or pd.isna(value):
        return None
    return float(value)


def _first_string(series: pd.Series | None) -> str | None:
    if series is None or series.empty:
        return None
    value = series.dropna().iloc[0] if not series.dropna().empty else None
    return str(value) if value is not None else None
