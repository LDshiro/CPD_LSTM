# WP10 Codex Instruction - Walk-forward Evaluation v1

Status: ready for local Codex implementation

## Goal

Implement offline walk-forward evaluation for the CPD-LSTM candidate versus the TSMOM fallback.
WP10 consumes WP7 `features_daily` and WP6 `continuous_daily`, reuses WP9 CPD-LSTM
training/inference code, reuses WP8 TSMOM signal generation, and evaluates both strategies through
the same research risk/cost/portfolio layer.

WP10 is evidence generation only. It must not promote models, submit orders, connect to IBKR, call
Databento, run hyperparameter search, or introduce new feature/CPD algorithms.

## Required CLI

Add `python -m cpdshadow.cli research walkforward` commands:

- `plan`
- `run`
- `evaluate-signals`
- `qa`
- `report`

All commands are offline and path-driven.

## Split Policy

Production defaults are quarterly folds with 10 calendar years of training, 2 calendar years of
validation, and the next quarter as OOS. Smoke tests may override the year/day thresholds but must
exercise the same code path.

For OOS anchor `T`:

- `train_start`: first observed date at or after `T - train_years - val_years`
- `train_end`: last observed date before `T - val_years`
- `val_start`: first observed date at or after `T - val_years`
- `val_end`: last observed date before `T`
- `oos_start`: first observed date at or after `T`
- `oos_end`: last observed date before the next quarterly anchor, capped by the requested end date

Fold IDs are deterministic: `wf_<run_id>_<YYYYQ#>`.

## Evaluation Convention

Signal and target at `t` earn the next observed adjusted settlement return `t -> t+1`.
Turnover cost is charged when target contracts change. CPD-LSTM and TSMOM must share the same roots,
dates, NAV, risk config, cost config, continuous prices, and instrument economics.

## Outputs

Write all run-local outputs under `data/research/walkforward/<run_id>/`:

- `walkforward_windows.parquet`
- `oos_signals_daily.parquet`
- `oos_targets_daily.parquet`
- `oos_pnl_daily.parquet`
- `fold_metrics.parquet`
- `aggregate_metrics.parquet`
- `reversal_events.parquet`
- `reversal_bucket_metrics.parquet`
- `gates.json`
- `manifest.json`
- `reports/walkforward_report.json`
- `reports/walkforward_report.md`

`oos_signals_daily.parquet` preserves the WP8/WP9 `signals_daily` columns and adds run-local lineage
columns such as `fold_id`, `snapshot_id`, `feature_set_id`, and `config_hash`.

## Reversal Bucket

For each fold/root, compute `cpd21_score` and `cpd63_score` p95 thresholds from train+validation
rows only. OOS reversal events are rows where either score exceeds its fold/root threshold.
Apply a 5-root-observation cooldown by default and evaluate horizons `[1, 5, 20]`.

## Gates

Write readiness gates to `gates.json`. Failed performance gates are findings, not program failures.
Structural QA failures remain program failures.

