from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
from typing import Literal

import pandas as pd

from cpdshadow.config import ExecutionBoundaryConfig
from cpdshadow.ids import canonical_json_bytes, stable_sha256_hex
from cpdshadow.order_intents import JournalEvent


IntentStatus = Literal["not_sent", "rejected"]
_PLANNED_COLUMNS = {
    "order_intent_id",
    "run_id",
    "strategy_id",
    "execution_mode",
    "as_of_date",
    "execution_date",
    "root",
    "raw_symbol",
    "broker_contract_id",
    "side",
    "quantity",
    "order_type",
    "limit_price",
    "reason",
    "status",
    "control_action",
    "position_snapshot_id",
    "sequence_no",
    "rejection_reason",
    "created_at_utc",
    "submitted_at_utc",
}


class DryRunValidationError(ValueError):
    pass


@dataclass(frozen=True)
class FinalOrderIntent:
    order_intent_id: str
    run_id: str
    strategy_id: str
    execution_mode: str
    as_of_date: date
    execution_date: date
    root: str
    raw_symbol: str
    broker_contract_id: str | None
    side: Literal["buy", "sell"]
    quantity: int
    order_type: str
    limit_price: float | None
    reason: str
    status: IntentStatus
    control_action: str
    position_snapshot_id: str
    sequence_no: int
    rejection_reason: str | None
    created_at_utc: datetime
    submitted_at_utc: datetime | None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def finalize_dry_run_intents(
    *,
    planned_intents: pd.DataFrame,
    contract_master: pd.DataFrame,
    config: ExecutionBoundaryConfig,
    created_at_utc: datetime | None = None,
) -> tuple[list[FinalOrderIntent], list[JournalEvent], dict[str, object]]:
    _require_columns(planned_intents, _PLANNED_COLUMNS, "planned_order_intents")
    _require_columns(contract_master, {"root", "raw_symbol"}, "contract_master")
    if planned_intents.empty:
        return [], [], {"accepted_intents": 0, "rejected_intents": 0, "total_intents": 0}

    created_at = _normalize_datetime(created_at_utc)
    working = planned_intents.copy()
    working["as_of_date"] = pd.to_datetime(working["as_of_date"]).dt.date
    working["execution_date"] = pd.to_datetime(working["execution_date"]).dt.date
    working["sequence_no"] = pd.to_numeric(working["sequence_no"], errors="coerce").fillna(0).astype(int)
    working["quantity"] = pd.to_numeric(working["quantity"], errors="coerce").fillna(0).astype(int)
    working = working.sort_values(["sequence_no", "order_intent_id"], kind="stable").reset_index(drop=True)

    contract_pairs = {
        (str(row["root"]), str(row["raw_symbol"])) for _, row in contract_master.iterrows()
    }
    rejection_reasons: dict[str, set[str]] = {}

    def reject(mask: pd.Series, reason: str) -> None:
        for order_intent_id in working.loc[mask, "order_intent_id"].astype(str).tolist():
            rejection_reasons.setdefault(order_intent_id, set()).add(reason)

    if config.validation.fail_on_nonpositive_quantity:
        reject(working["quantity"] <= 0, "nonpositive_quantity")
    reject(~working["side"].astype(str).isin({"buy", "sell"}), "invalid_side")
    reject(working["status"].astype(str) != config.lifecycle.planned_status, "invalid_planned_status")
    if config.validation.fail_on_execution_date_before_asof:
        reject(
            pd.to_datetime(working["execution_date"]) < pd.to_datetime(working["as_of_date"]),
            "execution_date_before_asof",
        )
    if config.validation.fail_on_unknown_contract:
        unknown_mask = ~working.apply(
            lambda row: (str(row["root"]), str(row["raw_symbol"])) in contract_pairs,
            axis=1,
        )
        reject(unknown_mask, "unknown_contract")
    duplicate_id_mask = working["order_intent_id"].duplicated(keep=False)
    reject(duplicate_id_mask, "duplicate_order_intent_id")
    duplicate_key_mask = working.duplicated(
        subset=["root", "raw_symbol", "side", "quantity", "reason"],
        keep=False,
    )
    reject(duplicate_key_mask, "duplicate_intent_key")

    side_counts = (
        working.groupby(["root", "raw_symbol"], sort=True)["side"]
        .nunique()
        .reset_index(name="n_sides")
    )
    conflicting_pairs = side_counts[side_counts["n_sides"] > 1][["root", "raw_symbol", "n_sides"]]
    if not conflicting_pairs.empty:
        conflicting_mask = (
            working.merge(conflicting_pairs, on=["root", "raw_symbol"], how="left")["n_sides"]
            .fillna(0)
            .astype(int)
            > 1
        )
        reject(
            conflicting_mask,
            "opposite_side_conflict",
        )

    finalized: list[FinalOrderIntent] = []
    for record in working.to_dict(orient="records"):
        order_intent_id = str(record["order_intent_id"])
        reasons = sorted(rejection_reasons.get(order_intent_id, set()))
        finalized.append(
            FinalOrderIntent(
                **{
                    **record,
                    "status": (
                        config.lifecycle.invalid_final_status
                        if reasons
                        else config.lifecycle.valid_final_status
                    ),
                    "rejection_reason": "|".join(reasons) if reasons else None,
                    "submitted_at_utc": None,
                }
            )
        )

    accepted = [intent for intent in finalized if intent.status == config.lifecycle.valid_final_status]
    rejected = [intent for intent in finalized if intent.status == config.lifecycle.invalid_final_status]
    journal_events = [
        _journal_event(
            run_id=finalized[0].run_id,
            execution_mode=finalized[0].execution_mode,
            as_of_date=finalized[0].as_of_date,
            execution_date=finalized[0].execution_date,
            root=None,
            component="dry_run_adapter",
            severity="info",
            code="DRY_RUN_FINALIZED",
            message="Dry-run adapter finalized planned intents without broker submission.",
            created_at_utc=created_at,
            details={
                "accepted_intents": len(accepted),
                "rejected_intents": len(rejected),
                "total_intents": len(finalized),
            },
        )
    ]
    if rejected:
        rejected_frame = pd.DataFrame([intent.to_dict() for intent in rejected])
        for root, group in rejected_frame.groupby("root", sort=True):
            reasons = sorted(
                {
                    reason
                    for reason_list in group["rejection_reason"].dropna().astype(str)
                    for reason in reason_list.split("|")
                    if reason
                }
            )
            journal_events.append(
                _journal_event(
                    run_id=finalized[0].run_id,
                    execution_mode=finalized[0].execution_mode,
                    as_of_date=finalized[0].as_of_date,
                    execution_date=finalized[0].execution_date,
                    root=str(root),
                    component="dry_run_adapter",
                    severity="warning",
                    code="DRY_RUN_REJECTIONS_PRESENT",
                    message="One or more intents were rejected during dry-run validation.",
                    created_at_utc=created_at,
                    details={"rejection_reasons": reasons, "rejected_count": int(len(group))},
                )
            )
    summary = {
        "run_id": finalized[0].run_id,
        "execution_mode": finalized[0].execution_mode,
        "accepted_intents": int(len(accepted)),
        "rejected_intents": int(len(rejected)),
        "total_intents": int(len(finalized)),
        "counts_by_reason": _counts_by_column(finalized, "reason"),
        "counts_by_root": _counts_by_column(finalized, "root"),
        "counts_by_control_action": _counts_by_column(finalized, "control_action"),
        "rejection_reason_counts": _rejection_reason_counts(rejected),
    }
    return finalized, journal_events, summary


def _counts_by_column(intents: list[FinalOrderIntent], column: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for intent in intents:
        key = str(getattr(intent, column))
        counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items()))


def _rejection_reason_counts(rejected: list[FinalOrderIntent]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for intent in rejected:
        if intent.rejection_reason is None:
            continue
        for reason in intent.rejection_reason.split("|"):
            counts[reason] = counts.get(reason, 0) + 1
    return dict(sorted(counts.items()))


def _journal_event(
    *,
    run_id: str,
    execution_mode: str,
    as_of_date: date | None,
    execution_date: date | None,
    root: str | None,
    component: str,
    severity: Literal["info", "warning", "critical"],
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
        raise DryRunValidationError(f"{name} is missing required columns: {missing}")


def _normalize_datetime(value: datetime | None) -> datetime:
    current = value or datetime.now(UTC)
    if current.tzinfo is None:
        current = current.replace(tzinfo=UTC)
    return current.astimezone(UTC)
