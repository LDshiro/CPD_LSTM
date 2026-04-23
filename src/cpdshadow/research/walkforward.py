from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Sequence

import pandas as pd

from cpdshadow.config import AppConfig, DataSchemaConfig, WalkforwardConfig
from cpdshadow.ids import canonical_json_bytes, config_hash as compute_config_hash
from cpdshadow.ids import stable_sha256_hex
from cpdshadow.instruments import InstrumentMaster
from cpdshadow.ml.artifacts import ModelArtifact, save_model_artifact
from cpdshadow.ml.infer import build_cpd_lstm_signals
from cpdshadow.ml.train import train_cpd_lstm_from_frames
from cpdshadow.research.gates import evaluate_readiness_gates
from cpdshadow.research.metrics import compute_aggregate_metrics, compute_fold_metrics
from cpdshadow.research.pnl_eval import EvaluationInputs, evaluate_targets_and_pnl
from cpdshadow.research.reporting import write_walkforward_report
from cpdshadow.research.reversal import (
    compute_reversal_bucket_metrics,
    find_reversal_events,
)
from cpdshadow.signals import SignalBuildRequest
from cpdshadow.storage.parquet_io import (
    atomic_replace_dir,
    make_staging_dir,
    read_parquet_dataset,
    write_parquet_part,
)
from cpdshadow.strategies.tsmom import TsmomSignalStrategy


@dataclass(frozen=True)
class WalkforwardContext:
    repo_root: Path
    app_config: AppConfig
    data_schema: DataSchemaConfig
    instrument_master: InstrumentMaster
    features_path: Path
    continuous_path: Path
    snapshot_id: str
    feature_set_id: str
    series_id: str
    roots: tuple[str, ...]
    run_id: str
    output_dir: Path
    settings_path: Path


def plan_walkforward_windows(
    *,
    features_daily: pd.DataFrame,
    run_id: str,
    roots: Sequence[str],
    oos_start: date,
    oos_end: date,
    config: WalkforwardConfig,
    train_years: int | None = None,
    val_years: int | None = None,
    min_train_days: int | None = None,
    min_val_days: int | None = None,
    min_oos_days: int | None = None,
) -> pd.DataFrame:
    working = features_daily.copy()
    working["as_of_date"] = pd.to_datetime(working["as_of_date"]).dt.date
    observed_dates = sorted(set(working["as_of_date"]))
    if not observed_dates:
        return pd.DataFrame(columns=_WINDOW_COLUMNS)
    train_years = train_years or config.train_years
    val_years = val_years or config.val_years
    min_train_days = min_train_days or config.min_train_days
    min_val_days = min_val_days or config.min_val_days
    min_oos_days = min_oos_days or config.min_oos_days
    anchors = _quarter_anchors(oos_start=oos_start, oos_end=oos_end)
    rows: list[dict[str, object]] = []
    created_at = _stable_created_at()
    for fold_index, anchor in enumerate(anchors):
        next_anchor = _next_quarter(anchor)
        train_start_bound = (pd.Timestamp(anchor) - pd.DateOffset(years=train_years + val_years)).date()
        val_start_bound = (pd.Timestamp(anchor) - pd.DateOffset(years=val_years)).date()
        train_start = _first_on_or_after(observed_dates, train_start_bound)
        train_end = _last_before(observed_dates, val_start_bound)
        val_start = _first_on_or_after(observed_dates, val_start_bound)
        val_end = _last_before(observed_dates, anchor)
        fold_oos_start = _first_on_or_after(observed_dates, anchor)
        fold_oos_end = (
            _last_before(observed_dates, next_anchor)
            if next_anchor <= oos_end
            else _last_on_or_before(observed_dates, oos_end)
        )
        fold_id = f"wf_{run_id}_{_quarter_label(anchor)}"
        counts = {
            "n_train_days": _count_dates(observed_dates, train_start, train_end),
            "n_val_days": _count_dates(observed_dates, val_start, val_end),
            "n_oos_days": _count_dates(observed_dates, fold_oos_start, fold_oos_end),
        }
        skip_reason = _skip_reason(
            train_start=train_start,
            train_end=train_end,
            val_start=val_start,
            val_end=val_end,
            oos_start=fold_oos_start,
            oos_end=fold_oos_end,
            counts=counts,
            min_train_days=min_train_days,
            min_val_days=min_val_days,
            min_oos_days=min_oos_days,
        )
        rows.append({
            "run_id": run_id,
            "fold_id": fold_id,
            "fold_index": fold_index,
            "anchor_date": anchor,
            "train_start": train_start,
            "train_end": train_end,
            "val_start": val_start,
            "val_end": val_end,
            "oos_start": fold_oos_start,
            "oos_end": fold_oos_end,
            **counts,
            "roots": ",".join(roots),
            "status": "skipped" if skip_reason else "planned",
            "skip_reason": skip_reason,
            "created_at_utc": created_at,
        })
    return pd.DataFrame(rows, columns=_WINDOW_COLUMNS)


def run_walkforward(
    *,
    context: WalkforwardContext,
    oos_start: date,
    oos_end: date,
    device: str,
    overwrite: bool,
    train_years: int | None = None,
    val_years: int | None = None,
    min_train_days: int | None = None,
    min_val_days: int | None = None,
    min_oos_days: int | None = None,
    max_epochs: int | None = None,
    min_epochs: int | None = None,
) -> dict[str, object]:
    final_dir = context.output_dir
    if final_dir.exists() and list(final_dir.iterdir()) and not overwrite:
        raise ValueError(f"walk-forward output already exists: {final_dir}")
    staging_dir = make_staging_dir(final_dir)
    try:
        result = _run_walkforward_into_dir(
            context=context,
            run_dir=staging_dir,
            oos_start=oos_start,
            oos_end=oos_end,
            device=device,
            train_years=train_years,
            val_years=val_years,
            min_train_days=min_train_days,
            min_val_days=min_val_days,
            min_oos_days=min_oos_days,
            max_epochs=max_epochs,
            min_epochs=min_epochs,
        )
        atomic_replace_dir(staging_dir, final_dir)
        result["output_dir"] = final_dir.as_posix()
        result["qa"] = qa_walkforward_run(final_dir)
        return result
    except Exception:
        if staging_dir.exists():
            shutil.rmtree(staging_dir)
        raise


def evaluate_oos_signals(
    *,
    context: WalkforwardContext,
    signals_daily: pd.DataFrame,
    output_dir: Path,
) -> dict[str, object]:
    signals_daily = _normalize_external_signals(
        signals_daily=signals_daily,
        context=context,
    )
    features = _load_features(context)
    continuous = _load_continuous(context)
    targets, pnl = evaluate_targets_and_pnl(
        EvaluationInputs(
            signals=signals_daily,
            features=features,
            continuous=continuous,
            app_config=context.app_config,
            instrument_master=context.instrument_master,
            nav_usd=context.app_config.walkforward.nav_usd,
            initial_margin_fraction_of_notional=(
                context.app_config.walkforward.initial_margin_fraction_of_notional
            ),
        )
    )
    fold_metrics = compute_fold_metrics(pnl_daily=pnl, targets_daily=targets)
    aggregate_metrics, warnings = compute_aggregate_metrics(
        pnl_daily=pnl,
        fold_metrics=fold_metrics,
        seed=context.app_config.walkforward.global_seed,
        block_length_days=context.app_config.walkforward.report_bootstrap.block_length_days,
        n_bootstrap_samples=context.app_config.walkforward.report_bootstrap.n_bootstrap_samples,
        min_bootstrap_observations=(
            context.app_config.walkforward.report_bootstrap.min_observations
        ),
    )
    windows = _windows_from_signals(signals_daily=signals_daily, context=context)
    empty_reversals = pd.DataFrame(columns=[
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
    ])
    empty_reversal_metrics = pd.DataFrame(columns=[
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
    ])
    gates = evaluate_readiness_gates(
        run_id=context.run_id,
        aggregate_metrics=aggregate_metrics,
        fold_metrics=fold_metrics,
        reversal_bucket_metrics=empty_reversal_metrics,
        config=context.app_config.walkforward.gates,
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    _write_outputs(
        run_dir=output_dir,
        tables={
            "walkforward_windows.parquet": windows,
            "oos_signals_daily.parquet": signals_daily,
            "oos_targets_daily.parquet": targets,
            "oos_pnl_daily.parquet": pnl,
            "fold_metrics.parquet": fold_metrics,
            "aggregate_metrics.parquet": aggregate_metrics,
            "reversal_events.parquet": empty_reversals,
            "reversal_bucket_metrics.parquet": empty_reversal_metrics,
        },
    )
    (output_dir / "gates.json").write_text(
        json.dumps(gates, indent=2, default=str),
        encoding="utf-8",
    )
    manifest = {
        "run_id": context.run_id,
        "snapshot_id": context.snapshot_id,
        "feature_set_id": context.feature_set_id,
        "series_id": context.series_id,
        "roots": list(context.roots),
        "mode": "evaluate_signals",
        "warnings": warnings,
    }
    (output_dir / "manifest.json").write_bytes(canonical_json_bytes(manifest))
    write_walkforward_report(
        run_dir=output_dir,
        run_id=context.run_id,
        windows=windows,
        fold_metrics=fold_metrics,
        aggregate_metrics=aggregate_metrics,
        reversal_bucket_metrics=empty_reversal_metrics,
        gates=gates,
        warnings=warnings,
    )
    return {
        "run_id": context.run_id,
        "row_count": int(len(signals_daily)),
        "warnings": warnings,
    }


def qa_walkforward_run(run_dir: str | Path) -> dict[str, object]:
    root = Path(run_dir)
    issues: list[dict[str, object]] = []
    required = [
        "walkforward_windows.parquet",
        "fold_metrics.parquet",
        "aggregate_metrics.parquet",
        "oos_signals_daily.parquet",
        "oos_targets_daily.parquet",
        "oos_pnl_daily.parquet",
        "reversal_events.parquet",
        "reversal_bucket_metrics.parquet",
        "gates.json",
        "manifest.json",
        "reports/walkforward_report.json",
        "reports/walkforward_report.md",
    ]
    for relative in required:
        if not (root / relative).exists():
            issues.append({"severity": "error", "code": "missing_output_file", "path": relative})
    signals = _read_file(root / "oos_signals_daily.parquet")
    windows = _read_file(root / "walkforward_windows.parquet")
    if signals.empty:
        issues.append({"severity": "error", "code": "empty_oos_signals"})
    else:
        strategies = set(signals["strategy_id"].dropna().astype(str))
        if not {"cpd_lstm", "tsmom"}.issubset(strategies):
            issues.append({"severity": "error", "code": "missing_strategy_rows"})
        if signals.duplicated(
            subset=["run_id", "fold_id", "strategy_id", "root", "as_of_date"]
        ).any():
            issues.append({"severity": "error", "code": "duplicate_oos_signal_keys"})
    if not windows.empty:
        for _, row in windows.iterrows():
            if row["status"] in {"skipped", "evaluated"}:
                continue
            if not (row["train_end"] < row["val_start"] <= row["val_end"] < row["oos_start"]):
                issues.append({"severity": "error", "code": "overlapping_windows"})
                break
    report = {
        "run_dir": root.as_posix(),
        "has_errors": any(issue["severity"] == "error" for issue in issues),
        "issues": issues,
    }
    (root / "reports").mkdir(parents=True, exist_ok=True)
    (root / "reports" / "walkforward_qa.json").write_text(
        json.dumps(report, indent=2, default=str),
        encoding="utf-8",
    )
    return report


def _run_walkforward_into_dir(
    *,
    context: WalkforwardContext,
    run_dir: Path,
    oos_start: date,
    oos_end: date,
    device: str,
    train_years: int | None,
    val_years: int | None,
    min_train_days: int | None,
    min_val_days: int | None,
    min_oos_days: int | None,
    max_epochs: int | None,
    min_epochs: int | None,
) -> dict[str, object]:
    features = _load_features(context)
    continuous = _load_continuous(context)
    windows = plan_walkforward_windows(
        features_daily=features,
        run_id=context.run_id,
        roots=context.roots,
        oos_start=oos_start,
        oos_end=oos_end,
        config=context.app_config.walkforward,
        train_years=train_years,
        val_years=val_years,
        min_train_days=min_train_days,
        min_val_days=min_val_days,
        min_oos_days=min_oos_days,
    )
    config_hash = _config_hash(context)
    all_signals: list[pd.DataFrame] = []
    warnings: list[str] = []
    windows = windows.copy()
    for idx, window in windows.iterrows():
        if window["status"] == "skipped":
            warnings.append(f"fold skipped: {window['fold_id']} {window['skip_reason']}")
            continue
        fold_signals = _run_fold(
            context=context,
            run_dir=run_dir,
            features=features,
            continuous=continuous,
            window=window,
            device=device,
            max_epochs=max_epochs,
            min_epochs=min_epochs,
            config_hash=config_hash,
        )
        if fold_signals.empty:
            windows.loc[idx, "status"] = "failed"
            warnings.append(f"fold failed to produce signals: {window['fold_id']}")
            continue
        windows.loc[idx, "status"] = "completed"
        all_signals.append(fold_signals)
    signals = pd.concat(all_signals, ignore_index=True) if all_signals else pd.DataFrame()
    targets, pnl = evaluate_targets_and_pnl(
        EvaluationInputs(
            signals=signals,
            features=features,
            continuous=continuous,
            app_config=context.app_config,
            instrument_master=context.instrument_master,
            nav_usd=context.app_config.walkforward.nav_usd,
            initial_margin_fraction_of_notional=(
                context.app_config.walkforward.initial_margin_fraction_of_notional
            ),
        )
    )
    fold_metrics = compute_fold_metrics(pnl_daily=pnl, targets_daily=targets)
    aggregate_metrics, metric_warnings = compute_aggregate_metrics(
        pnl_daily=pnl,
        fold_metrics=fold_metrics,
        seed=context.app_config.walkforward.global_seed,
        block_length_days=context.app_config.walkforward.report_bootstrap.block_length_days,
        n_bootstrap_samples=context.app_config.walkforward.report_bootstrap.n_bootstrap_samples,
        min_bootstrap_observations=(
            context.app_config.walkforward.report_bootstrap.min_observations
        ),
    )
    warnings.extend(metric_warnings)
    reversal_events, thresholds = find_reversal_events(
        features_daily=features,
        windows=windows,
        config=context.app_config.walkforward.reversal_bucket,
    )
    reversal_metrics = compute_reversal_bucket_metrics(
        events=reversal_events,
        pnl_daily=pnl,
        horizons=context.app_config.walkforward.reversal_bucket.horizons,
    )
    gates = evaluate_readiness_gates(
        run_id=context.run_id,
        aggregate_metrics=aggregate_metrics,
        fold_metrics=fold_metrics,
        reversal_bucket_metrics=reversal_metrics,
        config=context.app_config.walkforward.gates,
    )
    run_dir.mkdir(parents=True, exist_ok=True)
    _write_outputs(
        run_dir=run_dir,
        tables={
            "walkforward_windows.parquet": windows,
            "oos_signals_daily.parquet": signals,
            "oos_targets_daily.parquet": targets,
            "oos_pnl_daily.parquet": pnl,
            "fold_metrics.parquet": fold_metrics,
            "aggregate_metrics.parquet": aggregate_metrics,
            "reversal_events.parquet": reversal_events,
            "reversal_bucket_metrics.parquet": reversal_metrics,
            "reversal_thresholds.parquet": thresholds,
        },
    )
    (run_dir / "gates.json").write_text(json.dumps(gates, indent=2, default=str), encoding="utf-8")
    manifest = {
        "run_id": context.run_id,
        "snapshot_id": context.snapshot_id,
        "feature_set_id": context.feature_set_id,
        "series_id": context.series_id,
        "roots": list(context.roots),
        "config_hash": config_hash,
        "created_at_utc": datetime.now(UTC).isoformat(),
        "warnings": warnings,
    }
    (run_dir / "manifest.json").write_bytes(canonical_json_bytes(manifest))
    write_walkforward_report(
        run_dir=run_dir,
        run_id=context.run_id,
        windows=windows,
        fold_metrics=fold_metrics,
        aggregate_metrics=aggregate_metrics,
        reversal_bucket_metrics=reversal_metrics,
        gates=gates,
        warnings=warnings,
    )
    qa_report = qa_walkforward_run(run_dir)
    return {
        "run_id": context.run_id,
        "output_dir": run_dir.as_posix(),
        "completed_folds": int((windows["status"] == "completed").sum()),
        "warnings": warnings,
        "gates": gates,
        "qa": qa_report,
    }


def _run_fold(
    *,
    context: WalkforwardContext,
    run_dir: Path,
    features: pd.DataFrame,
    continuous: pd.DataFrame,
    window: pd.Series,
    device: str,
    max_epochs: int | None,
    min_epochs: int | None,
    config_hash: str,
) -> pd.DataFrame:
    fold_id = str(window["fold_id"])
    fold_dir = run_dir / "folds" / fold_id
    fold_dir.mkdir(parents=True, exist_ok=True)
    model_id = f"cpd_lstm_v1_wf_{context.run_id}_{fold_id}"
    train_config = context.app_config.models.cpd_lstm.model_copy(
        update={
            "feature_set_id": context.feature_set_id,
            "series_id": context.series_id,
            "training": context.app_config.models.cpd_lstm.training.model_copy(
                update={
                    "device": device,
                    "seed": _fold_seed(
                        context.app_config.walkforward.global_seed,
                        context.run_id,
                        fold_id,
                    ),
                    **({"max_epochs": max_epochs} if max_epochs is not None else {}),
                    **({"min_epochs": min_epochs} if min_epochs is not None else {}),
                }
            )
        }
    )
    train_result = train_cpd_lstm_from_frames(
        features_daily=features,
        continuous_daily=continuous,
        config=train_config,
        snapshot_id=context.snapshot_id,
        roots=context.roots,
        train_start=window["train_start"],
        train_end=window["train_end"],
        val_start=window["val_start"],
        val_end=window["val_end"],
        model_id=model_id,
        training_run_id=f"train_{fold_id}",
    )
    artifact_sha = save_model_artifact(
        model=train_result.model,
        config=train_config,
        standardizer=train_result.standardizer,
        metrics=train_result.metrics,
        train_manifest={
            "training_run_id": f"train_{fold_id}",
            "model_id": model_id,
            "snapshot_id": context.snapshot_id,
            "feature_set_id": context.feature_set_id,
            "series_id": context.series_id,
            "train_start_date": window["train_start"].isoformat(),
            "train_end_date": window["train_end"].isoformat(),
            "validation_start_date": window["val_start"].isoformat(),
            "validation_end_date": window["val_end"].isoformat(),
            "git_commit": "unknown-local",
            "config_hash": config_hash,
            "input_manifest_hash": stable_sha256_hex({"fold_id": fold_id}),
            "artifact_sha256": "",
            "created_at_utc": datetime.now(UTC).isoformat(),
        },
        model_dir=fold_dir / "model_artifact",
    )
    model_artifact = ModelArtifact(
        model=train_result.model,
        config=train_config,
        standardizer=train_result.standardizer,
        metrics=train_result.metrics,
        train_manifest={"model_id": model_id, "artifact_sha256": artifact_sha},
        artifact_sha256=artifact_sha,
    )
    request = SignalBuildRequest(
        run_id=context.run_id,
        strategy_id="cpd_lstm",
        model_id=model_id,
        feature_set_id=context.feature_set_id,
        snapshot_id=context.snapshot_id,
        start_date=window["oos_start"],
        end_date=window["oos_end"],
        roots=context.roots,
        created_at_utc=_stable_created_at(),
    )
    cpd_signals = build_cpd_lstm_signals(
        features_daily=features,
        artifact=model_artifact,
        request=request,
        snapshot_id=context.snapshot_id,
        feature_set_id=context.feature_set_id,
        series_id=context.series_id,
        roots=context.roots,
        start_date=window["oos_start"],
        end_date=window["oos_end"],
        device=device,
    ).signals
    tsmom_features = _select_features(
        features=features,
        roots=context.roots,
        start_date=window["oos_start"],
        end_date=window["oos_end"],
    )
    tsmom_request = SignalBuildRequest(
        run_id=context.run_id,
        strategy_id="tsmom",
        model_id=context.app_config.strategies.tsmom.model_id,
        feature_set_id=context.feature_set_id,
        snapshot_id=context.snapshot_id,
        start_date=window["oos_start"],
        end_date=window["oos_end"],
        roots=context.roots,
        created_at_utc=_stable_created_at(),
    )
    tsmom_signals = TsmomSignalStrategy(
        signals_config=context.app_config.signals,
        strategy_config=context.app_config.strategies.tsmom,
    ).build_signals(tsmom_features, tsmom_request).signals
    signals = _common_valid_signals(pd.concat([cpd_signals, tsmom_signals], ignore_index=True))
    if signals.empty:
        return signals
    signals["fold_id"] = fold_id
    signals["snapshot_id"] = context.snapshot_id
    signals["feature_set_id"] = context.feature_set_id
    signals["config_hash"] = config_hash
    signals = signals.sort_values(
        ["run_id", "fold_id", "strategy_id", "as_of_date", "root"],
        kind="stable",
    ).reset_index(drop=True)
    write_parquet_part(signals, fold_dir / "signals_daily.parquet")
    (fold_dir / "metrics.json").write_text(
        json.dumps(train_result.metrics, indent=2, default=str),
        encoding="utf-8",
    )
    return signals


def _common_valid_signals(signals: pd.DataFrame) -> pd.DataFrame:
    if signals.empty:
        return signals
    working = signals.copy()
    working["as_of_date"] = pd.to_datetime(working["as_of_date"]).dt.date
    valid = working[working["is_valid"].fillna(False).astype(bool)].copy()
    cpd_keys = valid.loc[valid["strategy_id"] == "cpd_lstm", ["as_of_date", "root"]]
    tsmom_keys = valid.loc[valid["strategy_id"] == "tsmom", ["as_of_date", "root"]]
    common = cpd_keys.merge(tsmom_keys, on=["as_of_date", "root"], how="inner")
    return valid.merge(common.drop_duplicates(), on=["as_of_date", "root"], how="inner")


def _normalize_external_signals(
    *,
    signals_daily: pd.DataFrame,
    context: WalkforwardContext,
) -> pd.DataFrame:
    signals = signals_daily.copy()
    if signals.empty:
        return pd.DataFrame()
    signals["run_id"] = context.run_id
    if "fold_id" not in signals.columns:
        signals["fold_id"] = "eval_only"
    signals["snapshot_id"] = context.snapshot_id
    signals["feature_set_id"] = context.feature_set_id
    if "config_hash" not in signals.columns:
        signals["config_hash"] = _config_hash(context)
    signals["as_of_date"] = pd.to_datetime(signals["as_of_date"]).dt.date
    return signals.sort_values(
        ["run_id", "fold_id", "strategy_id", "as_of_date", "root"],
        kind="stable",
    ).reset_index(drop=True)


def _windows_from_signals(
    *,
    signals_daily: pd.DataFrame,
    context: WalkforwardContext,
) -> pd.DataFrame:
    if signals_daily.empty:
        return pd.DataFrame(columns=_WINDOW_COLUMNS)
    rows: list[dict[str, object]] = []
    created_at = _stable_created_at()
    for index, (fold_id, group) in enumerate(signals_daily.groupby("fold_id", sort=True)):
        dates = sorted(pd.to_datetime(group["as_of_date"]).dt.date.unique())
        rows.append({
            "run_id": context.run_id,
            "fold_id": str(fold_id),
            "fold_index": int(index),
            "anchor_date": dates[0],
            "train_start": None,
            "train_end": None,
            "val_start": None,
            "val_end": None,
            "oos_start": dates[0],
            "oos_end": dates[-1],
            "n_train_days": None,
            "n_val_days": None,
            "n_oos_days": int(len(dates)),
            "roots": ",".join(context.roots),
            "status": "evaluated",
            "skip_reason": None,
            "created_at_utc": created_at,
        })
    return pd.DataFrame(rows, columns=_WINDOW_COLUMNS)


def _load_features(context: WalkforwardContext) -> pd.DataFrame:
    root = context.features_path / f"feature_set_id={context.feature_set_id}" / (
        f"snapshot_id={context.snapshot_id}"
    )
    df = read_parquet_dataset(root)
    if "year" in df.columns:
        df = df.drop(columns=["year"])
    df["as_of_date"] = pd.to_datetime(df["as_of_date"]).dt.date
    return df[
        (df["feature_set_id"].astype(str) == context.feature_set_id)
        & (df["series_id"].astype(str) == context.series_id)
        & (df["snapshot_id"].astype(str) == context.snapshot_id)
        & df["root"].astype(str).isin(set(context.roots))
    ].reset_index(drop=True)


def _load_continuous(context: WalkforwardContext) -> pd.DataFrame:
    root = context.continuous_path / f"series_id={context.series_id}" / (
        f"snapshot_id={context.snapshot_id}"
    )
    df = read_parquet_dataset(root)
    if "year" in df.columns:
        df = df.drop(columns=["year"])
    df["as_of_date"] = pd.to_datetime(df["as_of_date"]).dt.date
    return df[
        (df["series_id"].astype(str) == context.series_id)
        & (df["snapshot_id"].astype(str) == context.snapshot_id)
        & df["root"].astype(str).isin(set(context.roots))
    ].reset_index(drop=True)


def _select_features(
    *,
    features: pd.DataFrame,
    roots: Sequence[str],
    start_date: date,
    end_date: date,
) -> pd.DataFrame:
    selected = features.copy()
    selected["as_of_date"] = pd.to_datetime(selected["as_of_date"]).dt.date
    return selected[
        (selected["as_of_date"] >= start_date)
        & (selected["as_of_date"] <= end_date)
        & selected["root"].astype(str).isin(set(roots))
    ].sort_values(["as_of_date", "root"], kind="stable").reset_index(drop=True)


def _write_outputs(*, run_dir: Path, tables: dict[str, pd.DataFrame]) -> None:
    for filename, df in tables.items():
        write_parquet_part(df, run_dir / filename)


def _read_file(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    return pd.read_parquet(path)


def _config_hash(context: WalkforwardContext) -> str:
    paths = [context.settings_path, context.repo_root / "config" / "data_schema.yml"]
    return compute_config_hash(paths)


def _quarter_anchors(*, oos_start: date, oos_end: date) -> list[date]:
    anchors: list[date] = []
    current = oos_start
    while current <= oos_end:
        anchors.append(current)
        current = _next_quarter(current)
    return anchors


def _next_quarter(value: date) -> date:
    timestamp = pd.Timestamp(value)
    month = ((timestamp.month - 1) // 3 + 1) * 3 + 1
    year = timestamp.year
    if month > 12:
        month = 1
        year += 1
    return date(year, month, 1)


def _quarter_label(value: date) -> str:
    quarter = (value.month - 1) // 3 + 1
    return f"{value.year}Q{quarter}"


def _first_on_or_after(dates: Sequence[date], bound: date) -> date | None:
    return next((item for item in dates if item >= bound), None)


def _last_before(dates: Sequence[date], bound: date) -> date | None:
    candidates = [item for item in dates if item < bound]
    return candidates[-1] if candidates else None


def _last_on_or_before(dates: Sequence[date], bound: date) -> date | None:
    candidates = [item for item in dates if item <= bound]
    return candidates[-1] if candidates else None


def _count_dates(dates: Sequence[date], start: date | None, end: date | None) -> int:
    if start is None or end is None:
        return 0
    return int(sum(start <= item <= end for item in dates))


def _skip_reason(
    *,
    train_start: date | None,
    train_end: date | None,
    val_start: date | None,
    val_end: date | None,
    oos_start: date | None,
    oos_end: date | None,
    counts: dict[str, int],
    min_train_days: int,
    min_val_days: int,
    min_oos_days: int,
) -> str | None:
    if any(item is None for item in [train_start, train_end, val_start, val_end, oos_start, oos_end]):
        return "missing_window_boundary"
    if counts["n_train_days"] < min_train_days:
        return "insufficient_train_days"
    if counts["n_val_days"] < min_val_days:
        return "insufficient_val_days"
    if counts["n_oos_days"] < min_oos_days:
        return "insufficient_oos_days"
    return None


def _fold_seed(global_seed: int, run_id: str, fold_id: str) -> int:
    payload = {"global_seed": global_seed, "run_id": run_id, "fold_id": fold_id}
    return int(stable_sha256_hex(payload)[:8], 16) % (2**31)


def _stable_created_at() -> datetime:
    return datetime(1970, 1, 1, tzinfo=UTC)


_WINDOW_COLUMNS = [
    "run_id",
    "fold_id",
    "fold_index",
    "anchor_date",
    "train_start",
    "train_end",
    "val_start",
    "val_end",
    "oos_start",
    "oos_end",
    "n_train_days",
    "n_val_days",
    "n_oos_days",
    "roots",
    "status",
    "skip_reason",
    "created_at_utc",
]
