from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
from typing import Literal

import pandas as pd

from cpdshadow.config import ExecutionBoundaryConfig
from cpdshadow.ids import canonical_json_bytes, stable_sha256_hex


ControlAction = Literal["run_cpd_lstm", "fallback_tsmom", "hold", "reduce_only"]
IntentReason = Literal["rebalance", "roll", "reduce_only", "flatten", "fallback"]
IntentSide = Literal["buy", "sell"]
JournalSeverity = Literal["info", "warning", "critical"]

_TARGET_COLUMNS = {
    "run_id",
    "strategy_id",
    "execution_mode",
    "as_of_date",
    "execution_date",
    "root",
    "lead_raw_symbol",
    "target_contracts",
    "current_contracts",
    "order_delta_contracts",
    "control_action",
}
_POSITION_COLUMNS = {
    "position_snapshot_id",
    "execution_mode",
    "raw_symbol",
    "root",
    "position_contracts",
    "snapshot_time_utc",
}
_CONTRACT_COLUMNS = {"root", "raw_symbol"}


class OrderIntentPlanningError(ValueError):
    pass


@dataclass(frozen=True)
class PlannedOrderIntent:
    order_intent_id: str
    run_id: str
    strategy_id: str
    execution_mode: str
    as_of_date: date
    execution_date: date
    root: str
    raw_symbol: str
    broker_contract_id: str | None
    side: IntentSide
    quantity: int
    order_type: str
    limit_price: float | None
    reason: IntentReason
    status: Literal["planned"]
    control_action: ControlAction
    position_snapshot_id: str
    sequence_no: int
    rejection_reason: str | None
    created_at_utc: datetime
    submitted_at_utc: datetime | None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class JournalEvent:
    event_id: str
    run_id: str
    execution_mode: str
    as_of_date: date | None
    execution_date: date | None
    root: str | None
    component: str
    severity: JournalSeverity
    code: str
    message: str
    details_json: str | None
    created_at_utc: datetime

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class PositionSnapshotSelection:
    position_snapshot_id: str
    execution_mode: str
    snapshot_time_utc: datetime
    row_count: int

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def select_position_snapshot(
    positions: pd.DataFrame,
    *,
    execution_mode: str,
    position_snapshot_id: str | None = None,
    decision_time_utc: datetime | None = None,
    account_id: str | None = None,
) -> tuple[pd.DataFrame, PositionSnapshotSelection]:
    _require_columns(positions, _POSITION_COLUMNS, "broker_positions_snapshot")
    working = positions.copy()
    if working.empty:
        raise OrderIntentPlanningError("broker_positions_snapshot is empty")
    working["execution_mode"] = working["execution_mode"].astype(str)
    working = working[working["execution_mode"] == str(execution_mode)]
    if account_id is not None and "account_id" in working.columns:
        working = working[working["account_id"].astype(str) == str(account_id)]
    if working.empty:
        raise OrderIntentPlanningError(
            f"no broker positions snapshot rows for execution_mode={execution_mode}"
        )
    working["snapshot_time_utc"] = pd.to_datetime(working["snapshot_time_utc"], utc=True)
    if position_snapshot_id is not None:
        selected = working[
            working["position_snapshot_id"].astype(str) == str(position_snapshot_id)
        ].copy()
        if selected.empty:
            raise OrderIntentPlanningError(
                f"position_snapshot_id={position_snapshot_id} was not found"
            )
        chosen_snapshot_id = str(position_snapshot_id)
    else:
        snapshot_times = (
            working.groupby("position_snapshot_id", sort=True)["snapshot_time_utc"]
            .max()
            .reset_index()
        )
        if decision_time_utc is not None:
            cutoff = pd.Timestamp(decision_time_utc)
            snapshot_times = snapshot_times[snapshot_times["snapshot_time_utc"] <= cutoff]
            if snapshot_times.empty:
                raise OrderIntentPlanningError(
                    "no position snapshot exists at or before the requested decision time"
                )
        snapshot_times = snapshot_times.sort_values(
            ["snapshot_time_utc", "position_snapshot_id"],
            ascending=[False, False],
            kind="stable",
        ).reset_index(drop=True)
        chosen_snapshot_id = str(snapshot_times.iloc[0]["position_snapshot_id"])
        selected = working[
            working["position_snapshot_id"].astype(str) == chosen_snapshot_id
        ].copy()
    selected = selected.sort_values(["root", "raw_symbol"], kind="stable").reset_index(drop=True)
    selection = PositionSnapshotSelection(
        position_snapshot_id=chosen_snapshot_id,
        execution_mode=str(execution_mode),
        snapshot_time_utc=selected["snapshot_time_utc"].max().to_pydatetime(),
        row_count=int(len(selected)),
    )
    return selected, selection


def plan_order_intents(
    *,
    targets: pd.DataFrame,
    snapshot_positions: pd.DataFrame,
    contract_master: pd.DataFrame,
    config: ExecutionBoundaryConfig,
    created_at_utc: datetime | None = None,
) -> tuple[list[PlannedOrderIntent], list[JournalEvent], dict[str, object]]:
    _require_columns(targets, _TARGET_COLUMNS, "targets_daily")
    _require_columns(snapshot_positions, _POSITION_COLUMNS, "broker_positions_snapshot")
    _require_columns(contract_master, _CONTRACT_COLUMNS, "contract_master")
    if targets.empty:
        raise OrderIntentPlanningError("targets_daily is empty after filtering")

    created_at = _normalize_datetime(created_at_utc)
    target_rows = _prepare_targets(targets)
    position_rows = _prepare_positions(snapshot_positions)
    contract_pairs = {
        (str(row["root"]), str(row["raw_symbol"])) for _, row in contract_master.iterrows()
    }
    position_snapshot_ids = position_rows["position_snapshot_id"].astype(str).unique().tolist()
    if len(position_snapshot_ids) != 1:
        raise OrderIntentPlanningError("selected broker_positions_snapshot must contain exactly one snapshot id")
    position_snapshot_id = position_snapshot_ids[0]

    journal_events: list[JournalEvent] = []
    staged_records: list[dict[str, object]] = []
    blocked_roots: list[str] = []
    warning_count = 0

    for row in target_rows.sort_values(["root"], kind="stable").to_dict(orient="records"):
        root = str(row["root"])
        action = str(row["control_action"])
        strategy_id = str(row["strategy_id"])
        run_id = str(row["run_id"])
        execution_mode = str(row["execution_mode"])
        as_of_date = _to_date(row["as_of_date"])
        execution_date = _to_date(row["execution_date"])
        lead_raw_symbol = str(row["lead_raw_symbol"])
        desired = _to_int(row["target_contracts"])
        input_current = _to_int(row.get("current_contracts"))
        input_delta = _to_int(row.get("order_delta_contracts"))

        if action not in set(config.action_policy.supported_actions):
            raise OrderIntentPlanningError(f"unsupported control_action={action!r} for root={root}")
        expected_strategy = config.action_policy.expected_strategy_by_action.get(action)
        if expected_strategy is not None and strategy_id != expected_strategy:
            raise OrderIntentPlanningError(
                f"control_action={action!r} expects strategy_id={expected_strategy!r}, got {strategy_id!r}"
            )
        if (root, lead_raw_symbol) not in contract_pairs:
            journal_events.append(
                _journal_event(
                    run_id=run_id,
                    execution_mode=execution_mode,
                    as_of_date=as_of_date,
                    execution_date=execution_date,
                    root=root,
                    component="execution_boundary",
                    severity="warning",
                    code="PLAN_UNKNOWN_LEAD_CONTRACT",
                    message="Lead raw symbol was not found in contract_master; dry-run may reject it.",
                    created_at_utc=created_at,
                    details={"lead_raw_symbol": lead_raw_symbol},
                )
            )
            warning_count += 1

        root_positions = position_rows[position_rows["root"].astype(str) == root].copy()
        current_by_symbol = _positions_by_symbol(root_positions)
        current_total = sum(current_by_symbol.values())
        nonzero_signs = {1 if value > 0 else -1 for value in current_by_symbol.values() if value != 0}
        if len(nonzero_signs) > 1:
            journal_events.append(
                _journal_event(
                    run_id=run_id,
                    execution_mode=execution_mode,
                    as_of_date=as_of_date,
                    execution_date=execution_date,
                    root=root,
                    component="execution_boundary",
                    severity="critical",
                    code="PLAN_MIXED_SIGN_INVENTORY",
                    message="Mixed-sign inventory is unsupported in WP12; no intents were planned for this root.",
                    created_at_utc=created_at,
                    details={"current_by_symbol": current_by_symbol},
                )
            )
            blocked_roots.append(root)
            continue

        desired_effective = desired
        if action == "reduce_only":
            desired_effective = _reduce_only_target(desired=desired, current_total=current_total)
            if (
                not config.reduce_only.allow_replacement_roll
                and any(symbol != lead_raw_symbol for symbol in current_by_symbol)
                and current_by_symbol.get(lead_raw_symbol, 0) == 0
            ):
                desired_effective = 0
            if desired_effective != desired:
                journal_events.append(
                    _journal_event(
                        run_id=run_id,
                        execution_mode=execution_mode,
                        as_of_date=as_of_date,
                        execution_date=execution_date,
                        root=root,
                        component="execution_boundary",
                        severity="warning",
                        code="PLAN_REDUCE_ONLY_CLIPPED",
                        message="Target exposure was clipped under reduce-only rules.",
                        created_at_utc=created_at,
                        details={
                            "desired": desired,
                            "desired_effective": desired_effective,
                            "current_total": current_total,
                        },
                    )
                )
                warning_count += 1

        off_lead_positions = {
            symbol: qty
            for symbol, qty in current_by_symbol.items()
            if symbol != lead_raw_symbol and qty != 0
        }
        final_target = desired_effective if action == "reduce_only" else desired

        if action == "hold":
            if input_delta != 0:
                journal_events.append(
                    _journal_event(
                        run_id=run_id,
                        execution_mode=execution_mode,
                        as_of_date=as_of_date,
                        execution_date=execution_date,
                        root=root,
                        component="execution_boundary",
                        severity="warning",
                        code="PLAN_HOLD_SUPPRESSED_DELTA",
                        message="Hold action suppressed a nonzero target delta.",
                        created_at_utc=created_at,
                        details={
                            "input_current_contracts": input_current,
                            "input_order_delta_contracts": input_delta,
                            "snapshot_current_total": current_total,
                        },
                    )
                )
                warning_count += 1
            if off_lead_positions:
                journal_events.append(
                    _journal_event(
                        run_id=run_id,
                        execution_mode=execution_mode,
                        as_of_date=as_of_date,
                        execution_date=execution_date,
                        root=root,
                        component="execution_boundary",
                        severity="warning",
                        code="PLAN_HOLD_OFF_LEAD_INVENTORY",
                        message="Hold action left existing off-lead inventory unchanged.",
                        created_at_utc=created_at,
                        details={"off_lead_positions": off_lead_positions},
                    )
                )
                warning_count += 1
            continue

        final_target_nonzero = final_target != 0
        for raw_symbol, quantity_signed in sorted(off_lead_positions.items()):
            staged_records.append(
                _staged_record(
                    run_id=run_id,
                    strategy_id=strategy_id,
                    execution_mode=execution_mode,
                    as_of_date=as_of_date,
                    execution_date=execution_date,
                    root=root,
                    raw_symbol=raw_symbol,
                    side="sell" if quantity_signed > 0 else "buy",
                    quantity=abs(quantity_signed),
                    reason="roll" if final_target_nonzero else "flatten",
                    control_action=action,
                    position_snapshot_id=position_snapshot_id,
                    order_type=config.default_order_type,
                    created_at_utc=created_at,
                    sort_bucket=0,
                )
            )

        lead_current = int(current_by_symbol.get(lead_raw_symbol, 0))
        lead_delta = final_target - lead_current
        if lead_delta != 0:
            staged_records.append(
                _staged_record(
                    run_id=run_id,
                    strategy_id=strategy_id,
                    execution_mode=execution_mode,
                    as_of_date=as_of_date,
                    execution_date=execution_date,
                    root=root,
                    raw_symbol=lead_raw_symbol,
                    side="buy" if lead_delta > 0 else "sell",
                    quantity=abs(lead_delta),
                    reason=_lead_reason(
                        action=action,
                        had_off_lead=bool(off_lead_positions),
                        final_target=final_target,
                    ),
                    control_action=action,
                    position_snapshot_id=position_snapshot_id,
                    order_type=config.default_order_type,
                    created_at_utc=created_at,
                    sort_bucket=_lead_sort_bucket(lead_current=lead_current, lead_target=final_target),
                )
            )

    intents = _finalize_planned_records(staged_records)
    summary = {
        "run_id": str(target_rows.iloc[0]["run_id"]),
        "strategy_id": str(target_rows.iloc[0]["strategy_id"]),
        "execution_mode": str(target_rows.iloc[0]["execution_mode"]),
        "as_of_date": _to_date(target_rows.iloc[0]["as_of_date"]),
        "execution_date": _to_date(target_rows.iloc[0]["execution_date"]),
        "position_snapshot_id": position_snapshot_id,
        "root_count": int(target_rows["root"].nunique()),
        "planned_intent_count": int(len(intents)),
        "blocked_roots": sorted(set(blocked_roots)),
        "warning_count": int(warning_count),
        "counts_by_reason": _counts_by_reason(intents),
        "counts_by_control_action": _counts_by_control_action(intents),
    }
    return intents, journal_events, summary


def _prepare_targets(targets: pd.DataFrame) -> pd.DataFrame:
    working = targets.copy()
    working["as_of_date"] = pd.to_datetime(working["as_of_date"]).dt.date
    working["execution_date"] = pd.to_datetime(working["execution_date"]).dt.date
    duplicates = working[working.duplicated(subset=["root"], keep=False)]
    if not duplicates.empty:
        roots = sorted(set(duplicates["root"].astype(str)))
        raise OrderIntentPlanningError(f"duplicate target roots are not allowed in one batch: {roots}")
    key_cols = ["run_id", "strategy_id", "execution_mode", "as_of_date", "execution_date"]
    uniqueness = working[key_cols].drop_duplicates()
    if len(uniqueness) != 1:
        raise OrderIntentPlanningError("selected targets must resolve to one run/strategy/date/execution batch")
    return working


def _prepare_positions(snapshot_positions: pd.DataFrame) -> pd.DataFrame:
    working = snapshot_positions.copy()
    working["snapshot_time_utc"] = pd.to_datetime(working["snapshot_time_utc"], utc=True)
    return working


def _positions_by_symbol(root_positions: pd.DataFrame) -> dict[str, int]:
    if root_positions.empty:
        return {}
    aggregated = (
        root_positions.groupby("raw_symbol", sort=True)["position_contracts"]
        .sum()
        .reset_index()
    )
    return {
        str(row["raw_symbol"]): int(row["position_contracts"])
        for _, row in aggregated.iterrows()
        if int(row["position_contracts"]) != 0
    }


def _reduce_only_target(*, desired: int, current_total: int) -> int:
    if current_total == 0:
        return 0
    if desired == 0:
        return 0
    if (desired > 0 and current_total < 0) or (desired < 0 and current_total > 0):
        return 0
    sign = 1 if current_total > 0 else -1
    return sign * min(abs(desired), abs(current_total))


def _lead_reason(
    *,
    action: str,
    had_off_lead: bool,
    final_target: int,
) -> IntentReason:
    if had_off_lead and final_target != 0:
        return "roll"
    if action == "fallback_tsmom":
        return "fallback"
    if action == "reduce_only":
        return "reduce_only"
    if final_target == 0:
        return "flatten"
    return "rebalance"


def _lead_sort_bucket(*, lead_current: int, lead_target: int) -> int:
    if lead_current == 0:
        return 2
    if lead_target == 0:
        return 1
    if lead_current * lead_target <= 0:
        return 1
    if abs(lead_target) <= abs(lead_current):
        return 1
    return 2


def _staged_record(
    *,
    run_id: str,
    strategy_id: str,
    execution_mode: str,
    as_of_date: date,
    execution_date: date,
    root: str,
    raw_symbol: str,
    side: IntentSide,
    quantity: int,
    reason: IntentReason,
    control_action: str,
    position_snapshot_id: str,
    order_type: str,
    created_at_utc: datetime,
    sort_bucket: int,
) -> dict[str, object]:
    payload = {
        "run_id": run_id,
        "strategy_id": strategy_id,
        "execution_mode": execution_mode,
        "as_of_date": as_of_date.isoformat(),
        "execution_date": execution_date.isoformat(),
        "root": root,
        "raw_symbol": raw_symbol,
        "side": side,
        "quantity": quantity,
        "reason": reason,
    }
    order_intent_id = f"oi_{stable_sha256_hex(payload)[:16]}"
    return {
        "order_intent_id": order_intent_id,
        "run_id": run_id,
        "strategy_id": strategy_id,
        "execution_mode": execution_mode,
        "as_of_date": as_of_date,
        "execution_date": execution_date,
        "root": root,
        "raw_symbol": raw_symbol,
        "broker_contract_id": None,
        "side": side,
        "quantity": int(quantity),
        "order_type": order_type,
        "limit_price": None,
        "reason": reason,
        "status": "planned",
        "control_action": control_action,
        "position_snapshot_id": position_snapshot_id,
        "sequence_no": 0,
        "rejection_reason": None,
        "created_at_utc": created_at_utc,
        "submitted_at_utc": None,
        "_sort_bucket": sort_bucket,
    }


def _finalize_planned_records(staged_records: list[dict[str, object]]) -> list[PlannedOrderIntent]:
    if not staged_records:
        return []
    working = pd.DataFrame(staged_records)
    working = working[working["quantity"].astype(int) > 0].copy()
    if working.empty:
        return []
    working["_side_bucket"] = working["side"].map({"sell": 0, "buy": 1}).fillna(9)
    working = working.sort_values(
        ["_sort_bucket", "root", "raw_symbol", "_side_bucket", "quantity", "order_intent_id"],
        kind="stable",
    ).reset_index(drop=True)
    working["sequence_no"] = range(1, len(working) + 1)
    records = working.drop(columns=["_sort_bucket", "_side_bucket"]).to_dict(orient="records")
    return [PlannedOrderIntent(**record) for record in records]


def _counts_by_reason(intents: list[PlannedOrderIntent]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for intent in intents:
        counts[intent.reason] = counts.get(intent.reason, 0) + 1
    return dict(sorted(counts.items()))


def _counts_by_control_action(intents: list[PlannedOrderIntent]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for intent in intents:
        counts[intent.control_action] = counts.get(intent.control_action, 0) + 1
    return dict(sorted(counts.items()))


def _journal_event(
    *,
    run_id: str,
    execution_mode: str,
    as_of_date: date | None,
    execution_date: date | None,
    root: str | None,
    component: str,
    severity: JournalSeverity,
    code: str,
    message: str,
    created_at_utc: datetime,
    details: dict[str, object] | None = None,
) -> JournalEvent:
    details_json = (
        canonical_json_bytes(details).decode("utf-8")
        if details not in (None, {})
        else None
    )
    payload = {
        "run_id": run_id,
        "execution_mode": execution_mode,
        "as_of_date": as_of_date.isoformat() if as_of_date is not None else None,
        "execution_date": execution_date.isoformat() if execution_date is not None else None,
        "root": root,
        "component": component,
        "severity": severity,
        "code": code,
        "message": message,
        "details_json": details_json,
    }
    return JournalEvent(
        event_id=f"je_{stable_sha256_hex(payload)[:16]}",
        run_id=run_id,
        execution_mode=execution_mode,
        as_of_date=as_of_date,
        execution_date=execution_date,
        root=root,
        component=component,
        severity=severity,
        code=code,
        message=message,
        details_json=details_json,
        created_at_utc=created_at_utc,
    )


def _require_columns(df: pd.DataFrame, required: set[str], name: str) -> None:
    missing = sorted(required - set(df.columns))
    if missing:
        raise OrderIntentPlanningError(f"{name} is missing required columns: {missing}")


def _to_date(value: object) -> date:
    return pd.Timestamp(value).date()


def _to_int(value: object) -> int:
    if value is None or pd.isna(value):
        return 0
    return int(value)


def _normalize_datetime(value: datetime | None) -> datetime:
    current = value or datetime.now(UTC)
    if current.tzinfo is None:
        current = current.replace(tzinfo=UTC)
    return current.astimezone(UTC)
