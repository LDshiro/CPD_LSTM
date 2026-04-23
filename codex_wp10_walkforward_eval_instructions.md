# WP10 Codex Instructions — Walk-forward Evaluation / OOS Validation v1

## Goal

Implement **WP10: Walk-forward Evaluation / OOS Validation / Reversal Bucket Evaluation v1** for the CPD-LSTM Shadow Trading System.

WP10 turns the WP9 CPD-LSTM candidate and WP8 TSMOM fallback into a reproducible, comparable, out-of-sample evaluation pipeline. It must answer one question:

> Given the same data snapshot, same universe, same risk/cost layer, and same dates, does `cpd_lstm` add value over `tsmom`—especially during reversal / regime-change periods?

This is an evaluation and evidence-generation work package. It is **not** a model-promotion, broker, live trading, or hyperparameter-search work package.

---

## Current repository assumptions

Assume the repository already contains WP4–WP9 components:

- WP4 raw ingest / curated `contract_master` and `contracts_daily`.
- WP5 roll engine producing `lead_map` and `roll_events`.
- WP6 continuous builder producing signal-only `continuous_daily`.
- WP7 feature / CPD builder producing `cpd_daily` and `features_daily`.
- WP8 TSMOM signal infrastructure producing `signals_daily` with `strategy_id=tsmom` and `model_id=tsmom_v1`.
- WP9 CPD-LSTM model train/infer infrastructure producing `signals_daily` with `strategy_id=cpd_lstm`.
- Step 11 risk/cost/portfolio conversion utilities.
- WP3 schemas and lineage fields.

If names differ slightly in the existing code, adapt to the repo's current style while preserving the data contracts in this instruction.

---

## Non-goals

Do **not** implement the following in WP10:

1. Hyperparameter search.
2. Model promotion / release candidate approval. That is WP11.
3. IBKR, paper trading, order submission, or execution adapter.
4. Databento downloads or vendor API calls.
5. New feature engineering beyond existing `features_v1`.
6. New CPD algorithms.
7. Margin model improvements beyond the existing Step 11 approximation.
8. Any strategy other than `cpd_lstm` and `tsmom`.

WP10 must be fully offline once Parquet inputs are present.

---

## Design principles

1. **Same conditions or no comparison.** `cpd_lstm` and `tsmom` must use the same `features_daily`, `continuous_daily`, root universe, dates, NAV, risk/cost model, and portfolio conversion rules.
2. **No look-ahead.** Training, standardization, CPD thresholding, signal generation, sizing, and metrics must only use data available at or before the relevant as-of date.
3. **Fold artifacts are immutable.** A walk-forward run writes a deterministic set of fold artifacts and reports. Rerunning with the same config should produce the same hashes on synthetic data.
4. **Evaluation is separated from promotion.** WP10 computes evidence and gates. It does not mark a model as production-ready.
5. **Shadow-first realism.** Use conservative cost assumptions and daily signal convention. The output should be good enough to drive WP11 model decisioning and WP14 shadow batch design.

---

## Walk-forward split specification

### Default production split

For each quarterly OOS fold with anchor date `T`:

```text
train_start = first available date >= T - 10 calendar years
train_end   = last available date <  T - 2 calendar years
val_start   = first available date >= T - 2 calendar years
val_end     = last available date <  T
oos_start   = first available date >= T
oos_end     = last available date before next quarter anchor
```

Defaults:

```yaml
walkforward:
  frequency: quarterly
  train_years: 10
  val_years: 2
  min_train_days: 1260
  min_val_days: 252
  min_oos_days: 20
  sequence_length: 63
  label_horizon_days: 1
  global_seed: 1729
```

### Split safety rules

For train and validation samples:

- A feature sample ending at date `t` can be included only if all features use dates `<= t`.
- Its label date must also be inside the same segment.
- With `label_horizon_days=1`, exclude the final sample of a segment if its next root trading date falls outside the segment.

For OOS inference:

- Use features ending at `t`.
- The signal is evaluated against the next available return after `t`.
- Exclude OOS feature rows with no next return.

Do not use calendar-day arithmetic to index observations inside a root. Use the date sequence observed in `features_daily` / `continuous_daily`.

### Fold IDs

Use stable fold IDs:

```text
wf_<run_id>_<YYYYQ#>
```

Example:

```text
wf_cpd_v_tsmom_2020_2020Q1
```

### Fold seed

Use deterministic fold seeds:

```text
fold_seed = stable_hash(global_seed, run_id, fold_id) % 2**31
```

---

## Daily backtest convention

The system is a daily futures strategy using settlement-based features.

Use this convention in WP10:

```text
feature date t
  -> signal s_t generated using features up to t
  -> target position q_t calculated using information available at t
  -> q_t earns next observed adjusted return r_{t+1} = P_adj(t+1) / P_adj(t) - 1
  -> turnover cost is charged when q_t differs from q_{t-1}
```

This convention matches WP9's next-day label design. It is intentionally simple and conservative enough for offline comparison. Do not introduce intraday fills, open prices, VWAP, or broker-specific execution logic in WP10.

---

## Inputs

WP10 must support explicit paths for all inputs.

Required:

```text
features_daily      # WP7 wide features
continuous_daily    # WP6 signal-only adjusted series
settings.base.yml   # risk/cost/portfolio settings
instruments.yml     # contract economics / root metadata
```

Optional but recommended:

```text
cpd_daily           # WP7 long-form CPD output, useful for diagnostics
existing signals    # precomputed TSMOM / CPD-LSTM signals for evaluate-only mode
```

Input filters:

- `snapshot_id`
- `feature_set_id = features_v1`
- `series_id = v1_back_ratio_settle`
- `roots`, comma-separated
- `start`, `end`

Do not silently mix different `snapshot_id`, `feature_set_id`, or `series_id` values inside a run.

---

## Outputs

Write all WP10 outputs under:

```text
data/research/walkforward/<run_id>/
```

Recommended layout:

```text
data/research/walkforward/<run_id>/
  walkforward_windows.parquet
  fold_metrics.parquet
  aggregate_metrics.parquet
  oos_signals_daily.parquet
  oos_targets_daily.parquet
  oos_pnl_daily.parquet
  reversal_events.parquet
  reversal_bucket_metrics.parquet
  gates.json
  manifest.json
  reports/
    walkforward_report.json
    walkforward_report.md
  folds/
    <fold_id>/
      model_artifact/              # copy or symlink of trained fold model
      train_manifest.json
      metrics.json
      signals_daily.parquet
      targets_daily.parquet
      pnl_daily.parquet
```

### `walkforward_windows.parquet`

Columns:

```text
run_id: string
fold_id: string
fold_index: int
anchor_date: date
train_start: date
train_end: date
val_start: date
val_end: date
oos_start: date
oos_end: date
n_train_days: int
n_val_days: int
n_oos_days: int
roots: list[string] or comma-separated string
status: string                 # planned | skipped | completed | failed
skip_reason: string | null
created_at_utc: timestamp
```

### `oos_signals_daily.parquet`

Use the existing WP8/WP9 `signals_daily` contract. It must include both strategies:

```text
strategy_id in {tsmom, cpd_lstm}
model_id    in {tsmom_v1, cpd_lstm_v1_wf_<run_id>_<fold_id>}
```

Add or preserve lineage fields where available:

```text
run_id
fold_id
snapshot_id
feature_set_id
config_hash
model_id
feature_hash
created_at_utc
```

### `oos_targets_daily.parquet`

This is evaluation-only target sizing output, not an order instruction.

Required columns:

```text
run_id
fold_id
strategy_id
model_id
as_of_date
root
signal
annualized_vol_60
price_for_sizing
multiplier
target_contracts
previous_target_contracts
turnover_contracts
ex_ante_annualized_dollar_risk
modeled_rebalance_cost_usd
quality_flags
```

### `oos_pnl_daily.parquet`

Required columns:

```text
run_id
fold_id
strategy_id
model_id
as_of_date
next_date
root
target_contracts
adj_settle_t
adj_settle_next
raw_return_next
gross_pnl_usd
modeled_cost_usd
net_pnl_usd
nav_usd
net_return
```

Also create portfolio-aggregated daily rows, either in the same table with `root='__PORTFOLIO__'` or in a separate internal dataframe before reporting. Prefer separate aggregation functions, but the parquet can include portfolio rows if existing conventions already do that.

### `fold_metrics.parquet`

One row per `run_id, fold_id, strategy_id`.

Required metrics:

```text
n_days
ann_return
ann_vol
sharpe
sortino
max_drawdown
calmar
hit_rate
skew
kurtosis
gross_return
net_return
cost_usd
cost_to_gross_pnl
avg_daily_turnover_contracts
avg_abs_signal
avg_abs_contracts
max_abs_contracts
max_ex_ante_annualized_risk_pct_nav
```

### `aggregate_metrics.parquet`

One row per `run_id, strategy_id`, plus comparison rows if convenient.

Required:

```text
full_oos_start
full_oos_end
n_folds
n_days
ann_return
ann_vol
sharpe
sortino
max_drawdown
calmar
hit_rate
cost_to_gross_pnl
last_8_quarters_sharpe
```

For `strategy_id='cpd_lstm_minus_tsmom'`, include:

```text
mean_daily_return_diff
ann_return_diff
sharpe_diff
paired_t_stat_daily_diff
bootstrap_ci_low
bootstrap_ci_high
```

Bootstrap CI can be simple moving block bootstrap with deterministic seed. If this is too much for the first pass, implement paired t-stat first and leave block bootstrap behind a documented TODO. Do not fake a bootstrap output.

---

## Reversal bucket evaluation

The reversal bucket is the most important WP10 diagnostic because CPD-LSTM is intended to improve behavior near trend breaks.

### Event definition

Default event definition:

```text
reversal_event(root, as_of_date) =
  cpd21_score >= threshold_21(root, fold) OR
  cpd63_score >= threshold_63(root, fold)
```

Thresholds:

```text
threshold_21(root, fold) = 95th percentile of cpd21_score over train+val only
threshold_63(root, fold) = 95th percentile of cpd63_score over train+val only
```

Never compute thresholds using OOS data.

### Event cooldown

Default:

```yaml
reversal_bucket:
  threshold_quantile: 0.95
  cooldown_days: 5
  horizons: [1, 5, 20]
```

For each root, after selecting an event date, suppress additional events for the next `cooldown_days` root observations. Store both `raw_event_count` and `deduped_event_count`.

### Event metrics

For each strategy and horizon:

```text
run_id
fold_id
root
strategy_id
horizon_days
event_count
mean_event_net_return
median_event_net_return
hit_rate_event
mean_event_net_pnl_usd
```

For CPD-LSTM vs TSMOM comparison:

```text
mean_diff_cpd_minus_tsmom
median_diff_cpd_minus_tsmom
pct_events_cpd_outperforms
```

Also aggregate across roots and folds.

### Acceptance orientation

The reversal bucket does not need to be statistically significant in the first synthetic smoke. However, in real historical evaluation, the report should clearly show whether CPD-LSTM improves over TSMOM on 5-day and 20-day horizons after CPD event dates.

---

## Gates for WP11 readiness

WP10 should produce `gates.json`; it should not promote anything.

Default gates:

```yaml
gates:
  full_oos_net_sharpe_min: 0.90
  last_8_quarters_net_sharpe_min: 0.60
  max_drawdown_vs_tsmom_max_multiple: 1.50
  reversal_5d_diff_min: 0.0
  reversal_20d_diff_min: 0.0
  cost_to_gross_pnl_max: 0.50
  min_completed_folds: 8
```

Gate output schema:

```json
{
  "run_id": "...",
  "as_of_utc": "...",
  "overall_status": "pass|fail|warning",
  "gates": [
    {
      "gate_id": "full_oos_net_sharpe_min",
      "status": "pass|fail|warning",
      "observed": 0.87,
      "threshold": 0.90,
      "details": "..."
    }
  ]
}
```

---

## CLI requirements

Add a `research walkforward` command group to the existing CLI.

### Plan only

```bash
python -m cpdshadow.cli research walkforward plan \
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

### Run full offline walk-forward

```bash
python -m cpdshadow.cli research walkforward run \
  --features-path data/features/features_daily \
  --continuous-path data/curated/continuous_daily \
  --settings-path config/settings.base.yml \
  --instruments-path config/instruments.yml \
  --snapshot-id <snapshot_id> \
  --feature-set-id features_v1 \
  --series-id v1_back_ratio_settle \
  --roots ES,NQ,ZN \
  --oos-start 2020-01-02 \
  --oos-end 2024-12-31 \
  --run-id wf_cpd_lstm_v_tsmom_2020_2024 \
  --output-dir data/research/walkforward/wf_cpd_lstm_v_tsmom_2020_2024 \
  --device auto
```

### Evaluate precomputed signals only

This is useful if fold models were trained separately.

```bash
python -m cpdshadow.cli research walkforward evaluate-signals \
  --signals-path data/research/signals_daily \
  --features-path data/features/features_daily \
  --continuous-path data/curated/continuous_daily \
  --settings-path config/settings.base.yml \
  --instruments-path config/instruments.yml \
  --run-id wf_eval_only_2020_2024 \
  --output-dir data/research/walkforward/wf_eval_only_2020_2024
```

### QA/report

```bash
python -m cpdshadow.cli research walkforward qa \
  --run-dir data/research/walkforward/wf_cpd_lstm_v_tsmom_2020_2024

python -m cpdshadow.cli research walkforward report \
  --run-dir data/research/walkforward/wf_cpd_lstm_v_tsmom_2020_2024
```

---

## Make targets

Add:

```makefile
wp10-walkforward-smoke-offline:
	python -m cpdshadow.cli research walkforward run \
	  --features-path tests/fixtures/wp10/features_daily \
	  --continuous-path tests/fixtures/wp10/continuous_daily \
	  --settings-path config/settings.base.yml \
	  --instruments-path config/instruments.yml \
	  --snapshot-id synthetic_wp10 \
	  --feature-set-id features_v1 \
	  --series-id v1_back_ratio_settle \
	  --roots ES,NQ \
	  --oos-start 2020-01-02 \
	  --oos-end 2020-06-30 \
	  --run-id smoke_wp10 \
	  --output-dir logs/wp10_smoke \
	  --train-years 2 \
	  --val-years 1 \
	  --min-train-days 120 \
	  --min-val-days 40 \
	  --min-oos-days 10 \
	  --device cpu
```

The smoke target may use shortened windows to keep CI fast. The production defaults remain 10y/2y/quarterly.

---

## Suggested modules and files

Add or update:

```text
src/cpdshadow/research/__init__.py
src/cpdshadow/research/walkforward.py
src/cpdshadow/research/metrics.py
src/cpdshadow/research/reversal.py
src/cpdshadow/research/reporting.py
src/cpdshadow/research/gates.py
src/cpdshadow/research/pnl_eval.py
```

Tests:

```text
tests/unit/test_walkforward_windows.py
tests/unit/test_research_metrics.py
tests/unit/test_reversal_bucket.py
tests/unit/test_walkforward_pnl_alignment.py
tests/integration/test_wp10_walkforward_smoke.py
```

Docs:

```text
docs/walkforward_eval_v1.md
docs/decisions/0010-walkforward-evaluation-v1.md
docs/wp10_walkforward_review_checklist.md
```

---

## Integration details

### CPD-LSTM training/inference

Do not duplicate WP9 model code. Prefer calling library functions used by the WP9 CLI. If WP9 only exposes CLI code, refactor minimally so the same code is callable from WP10 without shelling out.

Required behavior per fold:

1. Build train/val datasets from `features_daily` and `continuous_daily`.
2. Fit standardizer only on train rows.
3. Train CPD-LSTM candidate for that fold.
4. Save fold artifact under `folds/<fold_id>/model_artifact/`.
5. Run inference for the fold OOS period.
6. Write CPD-LSTM OOS signals.

If the real CPD-LSTM training is too slow for smoke tests, provide a `--fast-smoke` or synthetic tiny config path, not a fake production path. The production code path should still be exercised at small scale.

### TSMOM generation

Use WP8 TSMOM code. Do not reimplement formula in WP10 unless WP8 lacks callable functions. The TSMOM output must be limited to the exact same OOS dates and roots as CPD-LSTM for each fold.

### Target sizing and PnL

Use Step 11 portfolio/risk/cost utilities. If existing functions expect a different dataframe shape, write a thin adapter in `research/pnl_eval.py`.

Core invariants:

- Same NAV for both strategies.
- Same target-vol setting.
- Same cost model.
- Same root universe.
- Same OOS dates.
- Same price series.
- Costs charged on changes in contracts.

### Missing data handling

Default policy:

- Missing feature row for a root/date: exclude that root/date from both strategies and log warning.
- Missing next return: exclude that root/date from PnL and log warning.
- Missing price for sizing: exclude that root/date and log warning.
- Too many exclusions in a fold: mark fold `failed` or `skipped` according to thresholds.

Add summary counts in the report.

---

## Required tests

### 1. Split generation

Create synthetic daily dates and verify:

- Train/val/OOS windows do not overlap.
- OOS windows are quarterly and contiguous within requested range.
- Fold IDs are stable.
- Short smoke parameters generate at least one valid fold.

### 2. No-lookahead in splits

Mutate feature rows after an OOS date and verify earlier fold plans and metrics do not change.

### 3. Standardizer leakage

Use a synthetic dataset where validation/OOS values have a large mean shift. Verify the standardizer statistics are based on train only.

### 4. PnL alignment

Given three prices `[100, 110, 121]` and constant one-contract signal:

- Signal at day 1 earns return day 1 -> day 2.
- Signal at day 2 earns return day 2 -> day 3.
- No same-day future return is used before the signal date.

### 5. Strategy parity

Verify CPD-LSTM and TSMOM evaluation rows share the same roots and dates after filtering.

### 6. Reversal threshold no-lookahead

Create train+val CPD scores with threshold below an OOS spike. Verify threshold is computed only from train+val, not including the OOS spike.

### 7. Reversal cooldown

Create consecutive high CPD scores and verify only the first event is retained until cooldown expires.

### 8. Report generation

Verify JSON and Markdown reports are produced and contain:

- Run metadata.
- Fold summary.
- Aggregate metrics.
- CPD-LSTM vs TSMOM comparison.
- Reversal bucket table.
- Gates.
- Warnings.

### 9. Idempotency

Run the same synthetic smoke twice into separate output dirs and compare key outputs after ignoring `created_at_utc` fields.

---

## QA rules

The `qa` command should fail on:

- Missing required output files.
- Empty OOS signals.
- Missing either `cpd_lstm` or `tsmom` rows.
- Duplicate `run_id, fold_id, strategy_id, root, as_of_date` rows.
- Overlapping train/val/OOS windows.
- OOS metrics without net returns.
- Reversal thresholds computed from OOS data.
- `gates.json` missing.

It may warn on:

- Fold skipped due to insufficient data.
- Root excluded for sparse data.
- Cost-to-gross-pnl above warning threshold.
- CPD-LSTM failing one or more readiness gates.

A failed performance gate should not fail the program. It should fail the **readiness gate** in `gates.json`.

---

## Documentation requirements

Update `README.md` with a WP10 section similar to prior WP sections.

Create `docs/walkforward_eval_v1.md` covering:

1. Purpose.
2. Inputs and outputs.
3. Split logic.
4. Daily PnL convention.
5. Metrics.
6. Reversal bucket definition.
7. Gates.
8. Example commands.
9. Known limitations.

Create ADR `docs/decisions/0010-walkforward-evaluation-v1.md` covering:

- Why quarterly walk-forward is used.
- Why train 10y / val 2y is the production default.
- Why WP10 does not do hyperparameter search.
- Why signal evaluation uses next observed adjusted return.
- Why reversal bucket thresholds are train+val only.
- Why WP10 computes gates but does not promote models.

---

## Acceptance criteria

WP10 is complete when:

1. `make test` passes.
2. `make wp10-walkforward-smoke-offline` passes without vendor/API/broker access.
3. A walk-forward `plan` command writes deterministic windows.
4. A walk-forward `run` command produces CPD-LSTM and TSMOM OOS signals.
5. Both strategies are evaluated through the same risk/cost layer.
6. Aggregate and fold-level metrics are written.
7. Reversal bucket metrics are written.
8. `gates.json` is written.
9. JSON and Markdown reports are written.
10. QA fails on structural errors but does not fail merely because CPD-LSTM underperforms.

