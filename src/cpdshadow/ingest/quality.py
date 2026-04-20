from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path

import pandas as pd

from cpdshadow.ids import file_sha256
from cpdshadow.instruments import InstrumentMaster


@dataclass(frozen=True)
class QualityIssue:
    code: str
    severity: str
    message: str
    details: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class QualityReport:
    snapshot_id: str
    issues: tuple[QualityIssue, ...]

    @property
    def has_errors(self) -> bool:
        return any(issue.severity == "error" for issue in self.issues)

    @property
    def has_warnings(self) -> bool:
        return any(issue.severity == "warning" for issue in self.issues)

    def to_dict(self) -> dict[str, object]:
        return {
            "snapshot_id": self.snapshot_id,
            "has_errors": self.has_errors,
            "has_warnings": self.has_warnings,
            "issues": [asdict(issue) for issue in self.issues],
        }


def run_quality_checks(
    *,
    snapshot_id: str,
    instrument_master: InstrumentMaster,
    file_registry_df: pd.DataFrame,
    contract_master_df: pd.DataFrame,
    contracts_daily_df: pd.DataFrame,
    repo_root: Path | None = None,
) -> QualityReport:
    issues: list[QualityIssue] = []
    roots = {instrument.root: instrument for instrument in instrument_master.instruments}
    raw_files = file_registry_df[file_registry_df["logical_table"].astype(str).str.startswith("raw_")] if not file_registry_df.empty else pd.DataFrame()

    source_counts = raw_files.groupby("source_schema")["row_count"].sum().to_dict() if not raw_files.empty else {}
    for required in ("definition", "statistics"):
        if int(source_counts.get(required, 0)) == 0:
            issues.append(QualityIssue(
                code="required_schema_empty",
                severity="error",
                message=f"Required schema {required} returned zero rows for snapshot {snapshot_id}.",
                details={"source_schema": required},
            ))

    if not contract_master_df.empty:
        unknown_roots = sorted(set(contract_master_df["root"]) - set(roots))
        if unknown_roots:
            issues.append(QualityIssue(
                code="unknown_root",
                severity="error",
                message="contract_master contains roots outside config/instruments.yml.",
                details={"roots": unknown_roots},
            ))

        non_futures = contract_master_df[~contract_master_df["instrument_class"].astype(str).str.upper().isin(["F", "FUTURE"])]
        if not non_futures.empty:
            issues.append(QualityIssue(
                code="spread_leakage",
                severity="error",
                message="contract_master contains non-outright futures rows.",
                details={"rows": non_futures[["raw_symbol", "instrument_class"]].to_dict(orient="records")},
            ))

    if not contracts_daily_df.empty:
        duplicates = contracts_daily_df[contracts_daily_df.duplicated(subset=["trade_date", "root", "raw_symbol"], keep=False)]
        if not duplicates.empty:
            issues.append(QualityIssue(
                code="duplicate_daily_key",
                severity="error",
                message="contracts_daily contains duplicate logical keys.",
                details={"count": int(len(duplicates))},
            ))

        negative = contracts_daily_df[
            (contracts_daily_df["volume"].fillna(0) < 0) | (contracts_daily_df["open_interest"].fillna(0) < 0)
        ]
        if not negative.empty:
            issues.append(QualityIssue(
                code="negative_volume_or_oi",
                severity="error",
                message="contracts_daily contains negative volume or open interest.",
                details={"count": int(len(negative))},
            ))

        missing = contracts_daily_df[contracts_daily_df["settle_status"].isin(["missing", "close_fallback"])]
        if not missing.empty:
            issues.append(QualityIssue(
                code="missing_settlement",
                severity="warning",
                message="One or more rows have missing or fallback settlement.",
                details={"count": int(len(missing)), "roots": sorted(set(missing["root"]))},
            ))

        no_candidates = _no_active_contract_candidates(contract_master_df, contracts_daily_df)
        if no_candidates:
            issues.append(QualityIssue(
                code="no_active_contract_candidates",
                severity="warning",
                message="One or more root/date combinations have no active contract candidates with price or volume.",
                details={"pairs": no_candidates[:25]},
            ))

        outliers = _price_outliers(contracts_daily_df, roots)
        if outliers:
            issues.append(QualityIssue(
                code="price_outlier",
                severity="warning",
                message="One or more daily price moves exceed the pre-roll QA threshold.",
                details={"rows": outliers[:25]},
            ))

    stale_files = _stale_raw_files(raw_files, repo_root=repo_root)
    if stale_files:
        issues.append(QualityIssue(
            code="stale_raw_file",
            severity="warning",
            message="One or more raw files are missing or hash-mismatched against the registry.",
            details={"files": stale_files[:25]},
        ))

    issues_sorted = tuple(sorted(issues, key=lambda item: (item.severity, item.code)))
    return QualityReport(snapshot_id=snapshot_id, issues=issues_sorted)


def _stale_raw_files(file_registry_df: pd.DataFrame, *, repo_root: Path | None) -> list[dict[str, object]]:
    stale: list[dict[str, object]] = []
    if file_registry_df.empty:
        return stale
    for _, row in file_registry_df.iterrows():
        path = Path(str(row["path"]))
        if repo_root is not None and not path.is_absolute():
            path = repo_root / path
        if not path.exists():
            stale.append({"path": path.as_posix(), "reason": "missing"})
            continue
        if path.is_dir():
            continue
        current_hash = file_sha256(path)
        if current_hash != row["content_sha256"]:
            stale.append({"path": path.as_posix(), "reason": "hash_mismatch"})
    return stale


def _no_active_contract_candidates(contract_master_df: pd.DataFrame, contracts_daily_df: pd.DataFrame) -> list[dict[str, object]]:
    if contract_master_df.empty or contracts_daily_df.empty:
        return []
    master = contract_master_df[["root", "raw_symbol", "expiration_date"]].copy()
    merged = contracts_daily_df.merge(master, on=["root", "raw_symbol"], how="left")
    issues = []
    for (trade_date, root), group in merged.groupby(["trade_date", "root"]):
        eligible = group[
            (group["expiration_date"].isna() | (pd.to_datetime(group["expiration_date"]).dt.date >= trade_date))
            & (group["settle_price"].notna() | group["volume"].fillna(0) > 0)
        ]
        if eligible.empty:
            issues.append({"trade_date": trade_date.isoformat(), "root": root})
    return issues


def _price_outliers(contracts_daily_df: pd.DataFrame, roots: dict[str, object]) -> list[dict[str, object]]:
    issues: list[dict[str, object]] = []
    grouped = contracts_daily_df.sort_values(["raw_symbol", "trade_date"]).groupby("raw_symbol")
    for raw_symbol, group in grouped:
        previous = None
        root = str(group.iloc[0]["root"])
        asset_class = roots[root].asset_class if root in roots else "unknown"
        threshold = 0.25 if asset_class in {"equity_index", "rates", "fx"} else 0.50
        for _, row in group.iterrows():
            settle = row["settle_price"]
            if pd.isna(settle):
                continue
            if previous not in (None, 0):
                move = abs((float(settle) / float(previous)) - 1.0)
                if move > threshold:
                    issues.append({
                        "trade_date": row["trade_date"].isoformat() if isinstance(row["trade_date"], date) else str(row["trade_date"]),
                        "root": root,
                        "raw_symbol": raw_symbol,
                        "move": move,
                    })
            previous = settle
    return issues
