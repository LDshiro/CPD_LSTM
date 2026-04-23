# Walk-forward Evaluation v1

## Purpose

WP10 evaluates whether `cpd_lstm` improves on the `tsmom` fallback under identical data, risk, and
cost assumptions. It produces OOS evidence, reversal-bucket diagnostics, readiness gates, and
reports for later model-release decisioning.

## Inputs

- WP7 `features_daily`
- WP6 `continuous_daily`
- `config/settings.base.yml`
- `config/instruments.yml`
- Optional precomputed `signals_daily` for evaluate-only mode

Each run is filtered to one `snapshot_id`, `feature_set_id`, `series_id`, and root universe.

## Split Logic

Default folds are quarterly. Each fold uses 10 calendar years of training, 2 calendar years of
validation, and the next quarter as OOS. Smoke runs may use shorter windows via CLI overrides.

The fold planner uses observed root dates from `features_daily`; it does not infer missing calendar
sessions.

## Daily PnL Convention

Signal and target at date `t` earn the next observed adjusted settlement return:

```text
features_t -> signal_t -> target_t -> P_adj(t+1) / P_adj(t) - 1
```

Turnover costs are charged when target contracts differ from the prior target for the same strategy,
model, and root.

## Metrics

WP10 writes fold and aggregate metrics including annualized return, volatility, Sharpe, Sortino,
drawdown, Calmar, hit rate, gross/net return, modeled cost, turnover, exposure, and CPD-LSTM minus
TSMOM paired daily return differences. Moving-block bootstrap confidence intervals are deterministic
under the configured seed; if too few paired observations exist, the CI is null with an explicit
warning.

## Reversal Bucket

Reversal events are OOS rows where `cpd21_score` or `cpd63_score` exceeds the fold/root train+val
p95 threshold. Thresholds never use OOS scores. A 5-observation cooldown suppresses clustered events.
Default horizons are 1, 5, and 20 root observations.

## Gates

`gates.json` records readiness gates for full OOS Sharpe, recent Sharpe, drawdown versus TSMOM,
reversal-bucket edge, cost-to-gross-PnL, and completed fold count. Gate failure does not crash the
program; structural QA errors do.

## Example

```bash
python -m cpdshadow.cli research walkforward run \
  --features-path data/features/features_daily \
  --continuous-path data/curated/continuous_daily \
  --snapshot-id <snapshot_id> \
  --feature-set-id features_v1 \
  --series-id v1_back_ratio_settle \
  --roots ES,NQ,ZN \
  --oos-start 2020-01-02 \
  --oos-end 2024-12-31 \
  --run-id wf_cpd_lstm_v_tsmom_2020_2024 \
  --output-dir data/research/walkforward/wf_cpd_lstm_v_tsmom_2020_2024
```

## Limitations

WP10 does not perform model promotion, broker integration, order generation, hyperparameter search,
or intraday execution simulation.

