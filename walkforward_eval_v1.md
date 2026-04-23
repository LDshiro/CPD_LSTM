# Walk-forward Evaluation v1

## Purpose

WP10 evaluates whether the CPD-LSTM candidate improves on the TSMOM fallback under identical data, risk, and cost assumptions. It produces out-of-sample evidence for WP11 model-release decisioning.

The evaluation focuses on two questions:

1. Does CPD-LSTM improve full-period net performance relative to TSMOM?
2. Does CPD-LSTM improve behavior around reversal / regime-change events identified by CPD scores?

## Inputs

- `features_daily` from WP7.
- `continuous_daily` from WP6.
- `settings.base.yml` from Step 11 / Step 15.
- `instruments.yml` from Step 4.
- Optional `cpd_daily` diagnostics.
- Optional precomputed `signals_daily` for evaluate-only mode.

All inputs must be filtered to a single `snapshot_id`, `feature_set_id`, and `series_id` per run.

## Default split

WP10 uses quarterly walk-forward folds by default.

For each OOS quarter anchor `T`:

- Train: 10 calendar years ending 2 years before `T`.
- Validation: 2 calendar years immediately before `T`.
- OOS: the quarter starting at `T`.

Shorter split parameters are allowed only for smoke tests.

## Daily PnL convention

The signal at feature date `t` is generated using information up to `t`. It is evaluated against the next observed adjusted settlement return after `t`.

```text
signal_t -> target_contracts_t -> earns adjusted return t -> t+1
```

Turnover cost is charged when `target_contracts_t` differs from `target_contracts_{t-1}`.

## Strategies

### CPD-LSTM

Each fold trains a fresh CPD-LSTM candidate using WP9 code and writes OOS signals using the existing `signals_daily` contract.

### TSMOM

Each fold generates TSMOM signals using WP8 code for exactly the same OOS roots and dates.

## Metrics

WP10 reports fold-level and aggregate metrics:

- Annualized return.
- Annualized volatility.
- Sharpe.
- Sortino.
- Maximum drawdown.
- Calmar.
- Hit rate.
- Skew.
- Kurtosis.
- Gross return.
- Net return.
- Modeled cost.
- Cost-to-gross-PnL.
- Turnover.
- Exposure / contract counts.

It also reports CPD-LSTM minus TSMOM differences.

## Reversal bucket

Reversal events are defined by high CPD scores:

```text
cpd21_score >= train_val_p95(cpd21_score)
OR
cpd63_score >= train_val_p95(cpd63_score)
```

Thresholds are computed per root and fold using train+validation rows only. OOS data must never be used to set event thresholds.

After an event is selected, later events for the same root are suppressed for 5 root observations by default. Event horizons are 1, 5, and 20 root observations.

WP10 reports event performance for both strategies and CPD-LSTM minus TSMOM.

## Gates

WP10 writes readiness gates to `gates.json`; it does not promote a model.

Default gates:

- Full OOS net Sharpe >= 0.90.
- Last 8 quarters net Sharpe >= 0.60.
- CPD-LSTM max drawdown <= 1.50 × TSMOM max drawdown.
- CPD-LSTM minus TSMOM reversal 5-day mean >= 0.
- CPD-LSTM minus TSMOM reversal 20-day mean >= 0.
- Cost-to-gross-PnL <= 0.50.
- Completed folds >= 8.

Gate failures are report findings, not program errors. Structural QA failures remain program errors.

## Outputs

All outputs are stored under:

```text
data/research/walkforward/<run_id>/
```

Required outputs:

- `walkforward_windows.parquet`
- `fold_metrics.parquet`
- `aggregate_metrics.parquet`
- `oos_signals_daily.parquet`
- `oos_targets_daily.parquet`
- `oos_pnl_daily.parquet`
- `reversal_events.parquet`
- `reversal_bucket_metrics.parquet`
- `gates.json`
- `manifest.json`
- `reports/walkforward_report.json`
- `reports/walkforward_report.md`

## Example commands

Plan:

```bash
python -m cpdshadow.cli research walkforward plan \
  --features-path data/features/features_daily \
  --continuous-path data/curated/continuous_daily \
  --snapshot-id databento_2020_2024 \
  --feature-set-id features_v1 \
  --series-id v1_back_ratio_settle \
  --roots ES,NQ,ZN \
  --oos-start 2020-01-02 \
  --oos-end 2024-12-31 \
  --run-id wf_cpd_lstm_v_tsmom_2020_2024 \
  --output-dir data/research/walkforward/wf_cpd_lstm_v_tsmom_2020_2024
```

Run:

```bash
python -m cpdshadow.cli research walkforward run \
  --features-path data/features/features_daily \
  --continuous-path data/curated/continuous_daily \
  --settings-path config/settings.base.yml \
  --instruments-path config/instruments.yml \
  --snapshot-id databento_2020_2024 \
  --feature-set-id features_v1 \
  --series-id v1_back_ratio_settle \
  --roots ES,NQ,ZN \
  --oos-start 2020-01-02 \
  --oos-end 2024-12-31 \
  --run-id wf_cpd_lstm_v_tsmom_2020_2024 \
  --output-dir data/research/walkforward/wf_cpd_lstm_v_tsmom_2020_2024 \
  --device auto
```

Smoke:

```bash
make wp10-walkforward-smoke-offline
```

## Known limitations

- WP10 uses daily settlement-based evaluation, not intraday execution simulation.
- WP10 does not perform hyperparameter search.
- WP10 does not promote a model to production.
- Reversal bucket statistics can be noisy when event counts are small.
- Real trading costs may exceed modeled costs; WP14–WP16 will compare shadow or paper slippage against modeled assumptions.
