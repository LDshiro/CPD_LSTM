from __future__ import annotations

import json
from pathlib import Path

import pandas as pd


def write_walkforward_report(
    *,
    run_dir: str | Path,
    run_id: str,
    windows: pd.DataFrame,
    fold_metrics: pd.DataFrame,
    aggregate_metrics: pd.DataFrame,
    reversal_bucket_metrics: pd.DataFrame,
    gates: dict[str, object],
    warnings: list[str],
) -> tuple[Path, Path]:
    reports_dir = Path(run_dir) / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "run_id": run_id,
        "fold_count": int(len(windows)),
        "completed_fold_count": int((windows["status"] == "completed").sum())
        if "status" in windows.columns
        else 0,
        "aggregate_metrics": _records(aggregate_metrics),
        "fold_metrics": _records(fold_metrics),
        "reversal_bucket_metrics": _records(reversal_bucket_metrics),
        "gates": gates,
        "warnings": warnings,
    }
    json_path = reports_dir / "walkforward_report.json"
    md_path = reports_dir / "walkforward_report.md"
    json_path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    md_path.write_text(_markdown(payload), encoding="utf-8")
    return json_path, md_path


def _records(df: pd.DataFrame) -> list[dict[str, object]]:
    if df.empty:
        return []
    return df.where(pd.notna(df), None).to_dict(orient="records")


def _markdown(payload: dict[str, object]) -> str:
    gates = payload.get("gates", {})
    lines = [
        f"# WP10 Walk-forward Report {payload['run_id']}",
        "",
        f"- Folds: {payload.get('fold_count', 0)}",
        f"- Completed folds: {payload.get('completed_fold_count', 0)}",
        f"- Gate status: {gates.get('overall_status') if isinstance(gates, dict) else None}",
        f"- Warnings: {len(payload.get('warnings', []))}",
        "",
        "## Aggregate Metrics",
        "",
    ]
    for row in payload.get("aggregate_metrics", []):
        if not isinstance(row, dict):
            continue
        lines.append(
            f"- `{row.get('strategy_id')}` sharpe={row.get('sharpe')} "
            f"net_return={row.get('net_return')} cost_to_gross={row.get('cost_to_gross_pnl')}"
        )
    lines.extend(["", "## Gates", ""])
    if isinstance(gates, dict):
        for gate in gates.get("gates", []):
            if isinstance(gate, dict):
                lines.append(
                    f"- `{gate.get('status')}` `{gate.get('gate_id')}`: "
                    f"{gate.get('observed')} vs {gate.get('threshold')}"
                )
    lines.extend(["", "## Warnings", ""])
    warnings = payload.get("warnings", [])
    if warnings:
        for warning in warnings:
            lines.append(f"- {warning}")
    else:
        lines.append("- None")
    return "\n".join(lines)
