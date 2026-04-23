# WP9 Codex Instructions — CPD-LSTM Model Skeleton / Training-Inference Interface v1

Goal: implement the first production-shaped **CPD-LSTM model layer**. WP9 consumes WP7 `features_daily` and WP6 `continuous_daily` labels, trains a deterministic PyTorch LSTM model, registers model artifacts, and writes WP3/WP8-compatible `signals_daily` rows with `strategy_id=cpd_lstm`.

This is not yet the full walk-forward research harness. WP10 will own quarterly walk-forward evaluation, champion/challenger comparisons, and full OOS reporting. WP9's job is to make the model trainable, reloadable, inferable, schema-compatible, and testable offline.

---

## 0. Branch and scope

Create a new branch from the latest local repository state:

```bash
git checkout -b wp9-cpd-lstm-model
```

### In scope

Implement:

1. PyTorch dependency strategy that does not break non-ML tests.
2. CPD-LSTM configuration under `config/settings.base.yml`.
3. Feature tensor construction from WP7 `features_daily`.
4. Next-day label construction from WP6 `continuous_daily`.
5. Train-only feature standardization and artifact persistence.
6. A minimal CPD-LSTM v1 model: 1-layer LSTM + dropout + tanh head.
7. Ex-cost Sharpe-style training loss.
8. Training CLI that writes `training_runs` and model artifact files.
9. Inference CLI that writes `signals_daily` with the same schema contract as WP8.
10. QA CLI and offline synthetic smoke target.
11. Unit tests for no-lookahead, label alignment, artifact reload, deterministic smoke, and schema compliance.

### Out of scope

Do not implement:

- Walk-forward orchestration. That is WP10.
- Hyperparameter search. WP10/WP11 may use the hooks, but WP9 must use fixed v1 defaults.
- Broker, IBKR, target sizing, order intents, or paper/live execution.
- Portfolio target construction. Step 11 already owns sizing.
- Model release approval. WP11 will freeze RC artifacts.
- Transformer, DeePM, GP/OCPD replacement, or any architecture beyond CPD-LSTM v1.
- Live data/vendor calls.

WP9 must be fully testable offline with synthetic Parquet fixtures.

---

## 1. Design principles

1. **Same signal contract as WP8.** CPD-LSTM must write `signals_daily`, not targets or orders.
2. **No-lookahead by construction.** A signal at `as_of_date=t` may use features through `t`, and labels for training may use `t+1`, but inference must never require any future return.
3. **Train and infer must share the same tensor builder.** The only difference is that training also joins next-day returns.
4. **Feature order is frozen.** Do not infer feature columns alphabetically.
5. **Artifacts are self-describing.** A model folder must contain config, feature order, standardizer, checkpoint, metrics, hashes, and a small model card.
6. **CPU tests must pass.** CUDA/RTX5090 support is important locally, but CI/offline tests cannot require a GPU.
7. **TSMOM remains the fallback.** Do not delete or weaken WP8 behavior.

---

## 2. Inputs and outputs

### Required inputs

WP9 training consumes two datasets:

```text
data/features/features_daily/feature_set_id=<feature_set_id>/...
data/curated/continuous_daily/series_id=<series_id>/...
```

Inference consumes only:

```text
data/features/features_daily/feature_set_id=<feature_set_id>/...
```

Expected `features_daily` columns:

```text
feature_set_id
as_of_date
root
series_id
ret_1
ret_21
ret_63
ret_126
ret_252
macd_8_24
macd_16_48
macd_32_96
cpd21_score
cpd21_age
cpd63_score
cpd63_age
vol_20_60
vol_60_252
annualized_vol_60
is_complete
warmup_status
feature_hash
builder_version
snapshot_id
```

Expected `continuous_daily` columns for labels:

```text
series_id
as_of_date
root
adj_settle_price
daily_return
is_usable_for_signal
snapshot_id
```

Use only rows where:

```text
features_daily.feature_set_id == configured feature_set_id
features_daily.series_id == configured series_id
features_daily.snapshot_id == requested snapshot_id, unless explicitly using fixture mode
features_daily.is_complete == true
features_daily.warmup_status == 'ok'
continuous_daily.series_id == configured series_id
continuous_daily.snapshot_id == requested snapshot_id, unless explicitly using fixture mode
continuous_daily.is_usable_for_signal == true
```

### Outputs

Training writes:

```text
artifacts/models/cpd_lstm/<model_id>/
  model.pt
  config.json
  feature_order.json
  standardizer.json
  metrics.json
  train_manifest.json
  model_card.md
  sha256sums.txt

data/research/training_runs/strategy_id=cpd_lstm/year=<YYYY>/part-*.parquet
data/research/model_registry/strategy_id=cpd_lstm/model_id=<model_id>/part-*.parquet
```

Inference writes:

```text
data/research/signals_daily/strategy_id=cpd_lstm/model_id=<model_id>/year=<YYYY>/part-*.parquet
```

QA writes:

```text
artifacts/wp9/cpd_lstm_train_qa_<training_run_id>.json
artifacts/wp9/cpd_lstm_train_qa_<training_run_id>.md
artifacts/wp9/cpd_lstm_infer_qa_<run_id>_<model_id>.json
artifacts/wp9/cpd_lstm_infer_qa_<run_id>_<model_id>.md
```

---

## 3. Configuration

Extend `config/settings.base.yml` with a `models.cpd_lstm` section. Keep the names stable; WP10 and WP11 will rely on them.

```yaml
models:
  cpd_lstm:
    model_family: cpd_lstm_v1
    strategy_id: cpd_lstm
    feature_set_id: features_v1
    series_id: v1_back_ratio_settle
    sequence_length: 63
    annualization_factor: 252
    epsilon: 1.0e-12

    feature_order:
      - ret_1
      - ret_21
      - ret_63
      - ret_126
      - ret_252
      - macd_8_24
      - macd_16_48
      - macd_32_96
      - cpd21_score
      - cpd21_age
      - cpd63_score
      - cpd63_age
      - vol_20_60
      - vol_60_252

    labels:
      horizon_root_trading_days: 1
      return_source: continuous_daily.daily_return
      normalize_by: annualized_vol_60
      normalized_return_clip_abs: 10.0
      min_annualized_vol: 0.01

    standardization:
      enabled: true
      fit_scope: train_only
      method: zscore
      min_std: 1.0e-6
      clip_abs_after_standardization: 10.0

    architecture:
      input_size: 14
      hidden_size: 64
      num_layers: 1
      dropout_after_lstm: 0.20
      head_hidden_size: 32
      output_activation: tanh
      output_clip_abs: 1.0

    training:
      optimizer: adamw
      learning_rate: 0.0003
      weight_decay: 0.0001
      max_epochs: 50
      min_epochs: 5
      early_stopping_patience: 8
      gradient_clip_norm: 1.0
      seed: 42
      deterministic_mode: warn
      device: auto
      dtype: float32
      batch_mode: full_panel
      validation_metric: sharpe_ex_cost
      cost_bps_default: 2.0
      turnover_cost_multiplier: 1.0
      signal_l2_penalty: 1.0e-4
      turnover_l1_penalty: 0.0

    smoke:
      max_epochs: 3
      min_epochs: 1
      roots: [ES, NQ]
      n_days: 180
```

If the project already has typed config models, add a `CpdLstmModelSettings` or equivalent. Invalid configs must fail early.

### PyTorch dependency policy

Add PyTorch in a way that does not make the base non-ML test suite fragile.

Recommended approach:

- Add an optional `ml` extra in `pyproject.toml`, or document PyTorch as a manual ML dependency.
- Add `scripts/install_torch_cuda128.sh` for RTX5090/Blackwell local environments.
- Unit tests that import PyTorch should skip gracefully if torch is not installed, except `make wp9-cpd-lstm-smoke-offline`, which may require the ML extra.

Suggested local install script content:

```bash
python -m pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu128
```

Do not force CUDA wheels in default CI unless the current repo CI environment is known to support them.

---

## 4. Feature vector and sequence construction

The exact feature order is fixed:

```text
ret_1
ret_21
ret_63
ret_126
ret_252
macd_8_24
macd_16_48
macd_32_96
cpd21_score
cpd21_age
cpd63_score
cpd63_age
vol_20_60
vol_60_252
```

For each root independently:

1. Sort by `as_of_date`.
2. Keep only complete rows.
3. Build a sequence ending at date `t` using the previous 63 root-observed rows inclusive:

```text
X[root, t] = features[root, t-62 : t]
```

4. A training sample is valid only if:

```text
63 complete feature rows exist through t
next root-observed trading row t_next exists
continuous_daily.daily_return at t_next is finite
annualized_vol_60 at t is finite and >= min_annualized_vol
```

5. An inference sample is valid if the 63-row feature sequence exists. It does not require `t_next`.

Do not assume a global calendar. The root trading calendar is the observed sorted dates for that root.

---

## 5. Label alignment

For a training sample ending at root date `t`, the model output is interpreted as the signal that would have been known at `t` and used for the next root trading interval. Therefore the training return is:

```text
r_next[root, t] = continuous_daily.daily_return[root, t_next]
```

where `t_next` is the next observed root trading date after `t`. This `daily_return` is the adjusted return from `t` to `t_next`.

Vol-normalized training return:

```text
sigma_daily[root, t] = max(annualized_vol_60[root, t] / sqrt(252), min_annualized_vol / sqrt(252))
y_norm[root, t] = clip(r_next[root, t] / sigma_daily[root, t], -10, +10)
```

Never use `features_daily.ret_1` at `t_next` as the label. It may be normalized and transformed. Use `continuous_daily.daily_return` for label clarity and auditability.

---

## 6. Standardization

Although WP7 features are already scaled, WP9 should add a train-only standardizer to stabilize the neural model.

Fit standardization only on training sequences:

```text
mean[f] = average of feature f over all valid train sequence elements
std[f]  = std of feature f over all valid train sequence elements, floored at min_std
```

Apply to train, validation, and inference using the stored artifact:

```text
x_std = clip((x - mean) / std, -10, +10)
```

Persist as:

```text
standardizer.json
```

The artifact must include feature order and numeric mean/std arrays.

---

## 7. Model architecture

Implement pure PyTorch modules, no Lightning dependency in WP9.

```text
Input:  [batch, sequence_length=63, n_features=14]
LSTM:   input_size=14, hidden_size=64, num_layers=1, batch_first=True
State:  last hidden state h_T
Dropout: p=0.20 after h_T
Head:   Linear(64 -> 32), ReLU, Linear(32 -> 1), Tanh
Output: signal_raw in [-1, 1]
```

Suggested files:

```text
src/cpdshadow/ml/__init__.py
src/cpdshadow/ml/dataset.py
src/cpdshadow/ml/model.py
src/cpdshadow/ml/losses.py
src/cpdshadow/ml/artifacts.py
src/cpdshadow/ml/train.py
src/cpdshadow/ml/infer.py
src/cpdshadow/ml/qa.py
```

Keep the model small and auditable. Do not add attention, bidirectional LSTMs, embeddings, root-specific parameters, or root one-hot features in WP9.

---

## 8. Training objective

The v1 training objective is a portfolio-style ex-cost Sharpe loss computed from all valid root/date samples in the training or validation panel.

Let:

```text
s[root,t] = model signal for features through t
r_norm[root,t] = next-day vol-normalized return y_norm[root,t]
cost_norm[root,t] = cost_bps[root] / 10000 / sigma_daily[root,t]
```

Approximate normalized asset PnL:

```text
asset_pnl[root,t] = s[root,t] * r_norm[root,t]
```

Approximate turnover cost:

```text
turnover[root,t] = abs(s[root,t] - s[root,t_prev])
turnover_cost[root,t] = turnover[root,t] * cost_norm[root,t] * turnover_cost_multiplier
```

Daily portfolio PnL over active valid roots:

```text
portfolio_pnl[t] = mean_over_valid_roots(asset_pnl[root,t] - turnover_cost[root,t])
```

Loss:

```text
sharpe = mean(portfolio_pnl) / (std(portfolio_pnl) + epsilon)
loss = -sharpe + signal_l2_penalty * mean(s^2) + turnover_l1_penalty * mean(turnover)
```

Notes:

- This is a research/training proxy. Step 11 remains the source of truth for actual contract sizing and USD costs.
- If implementing exact panel loss is too intrusive, implement a function-level panel evaluator first and use it for validation; but do not silently replace the configured loss with MSE.
- Cost by root should be read from existing instrument/cost settings if available; otherwise use `cost_bps_default` and surface a QA warning.

---

## 9. Train/validation split behavior

WP9 should support explicit date windows via CLI. Do not hard-code periods.

Required parameters:

```text
--train-start YYYY-MM-DD
--train-end YYYY-MM-DD
--val-start YYYY-MM-DD
--val-end YYYY-MM-DD
```

Interpretation:

- Train samples have `as_of_date` in `[train_start, train_end]`.
- Validation samples have `as_of_date` in `[val_start, val_end]`.
- Warmup history before `train_start` may be read to construct the first sequences, but it must not contribute to standardizer fitting or training metrics unless its `as_of_date` is inside train.
- `val_start` should normally be after `train_end`, but the CLI should validate and fail if windows overlap.

WP10 will call this repeatedly for walk-forward folds.

---

## 10. CLI contract

Extend the project CLI. Names can adapt to the existing CLI style, but the user-facing commands should be close to this:

### Train

```bash
python -m cpdshadow.cli models cpd-lstm train \
  --features-path data/features/features_daily \
  --continuous-path data/curated/continuous_daily \
  --snapshot-id <snapshot_id> \
  --feature-set-id features_v1 \
  --series-id v1_back_ratio_settle \
  --roots ES,NQ,RTY,YM,ZT,ZF,ZN,ZB,6E,6J,6B,6A,GC,SI,HG,CL,NG,ZC,ZW,ZS \
  --train-start 2014-01-02 \
  --train-end 2021-12-31 \
  --val-start 2022-01-03 \
  --val-end 2023-12-29 \
  --model-id cpd_lstm_v1_YYYYMMDD \
  --training-run-id train_cpd_lstm_v1_YYYYMMDD \
  --output-dir artifacts/models/cpd_lstm/cpd_lstm_v1_YYYYMMDD
```

### Infer

```bash
python -m cpdshadow.cli models cpd-lstm infer \
  --features-path data/features/features_daily \
  --snapshot-id <snapshot_id> \
  --feature-set-id features_v1 \
  --model-dir artifacts/models/cpd_lstm/cpd_lstm_v1_YYYYMMDD \
  --model-id cpd_lstm_v1_YYYYMMDD \
  --start 2024-01-02 \
  --end 2024-12-31 \
  --roots ES,NQ,ZN \
  --run-id infer_cpd_lstm_2024 \
  --created-at-utc 2026-04-23T00:00:00Z \
  --output-dir data/research/signals_daily
```

### QA

```bash
python -m cpdshadow.cli models cpd-lstm qa \
  --model-dir artifacts/models/cpd_lstm/cpd_lstm_v1_YYYYMMDD \
  --signals-path data/research/signals_daily \
  --run-id infer_cpd_lstm_2024
```

### Offline smoke

Add a Makefile target:

```make
wp9-cpd-lstm-smoke-offline:
	python -m cpdshadow.cli models cpd-lstm smoke --output-root artifacts/wp9/smoke
```

The smoke command must:

1. Create synthetic `features_daily` and `continuous_daily` fixtures.
2. Train a tiny model for 3 epochs on CPU if needed.
3. Save artifact files.
4. Run inference.
5. Validate `signals_daily` schema.
6. Emit QA JSON/Markdown.

---

## 11. Artifact contract

Each model artifact directory must contain:

### `config.json`

Full resolved CPD-LSTM model/training config, including feature order and seed.

### `feature_order.json`

The exact list of feature columns.

### `standardizer.json`

```json
{
  "method": "zscore",
  "fit_scope": "train_only",
  "feature_order": ["ret_1", "ret_21", "..."],
  "mean": [0.0, 0.0],
  "std": [1.0, 1.0],
  "min_std": 1e-6,
  "clip_abs_after_standardization": 10.0
}
```

### `model.pt`

PyTorch checkpoint containing:

```text
model_state_dict
model_config
feature_order
standardizer
training_run_id
model_id
created_at_utc
```

### `metrics.json`

At minimum:

```text
train_loss_last
val_loss_best
val_sharpe_ex_cost_best
val_mean_pnl
val_std_pnl
n_train_samples
n_val_samples
n_roots
n_features
epochs_completed
best_epoch
seed
```

### `train_manifest.json`

At minimum:

```text
training_run_id
model_id
snapshot_id
feature_set_id
series_id
train_start_date
train_end_date
validation_start_date
validation_end_date
git_commit
config_hash
input_manifest_hash
artifact_sha256
created_at_utc
```

### `model_card.md`

Human-readable summary: model purpose, inputs, windows, known limitations, metrics, and whether it is only candidate/shadow.

### `sha256sums.txt`

Hash all artifact files except `sha256sums.txt` itself.

---

## 12. Research table rows

Write or append WP3-compatible rows where the project has a helper for partitioned datasets. If there is no helper yet, implement a small deterministic writer.

### `training_runs`

One row per train invocation:

```text
training_run_id
strategy_id = cpd_lstm
feature_set_id
train_start_date
train_end_date
validation_start_date
validation_end_date
seed
hyperparams_hash
config_hash
data_snapshot_id
status
best_validation_metric
created_at_utc
completed_at_utc
```

### `model_registry`

One row per artifact:

```text
model_id
strategy_id = cpd_lstm
training_run_id
artifact_path
artifact_sha256
feature_set_id
config_hash
model_status = candidate
registered_at_utc
notes
```

Do not set `model_status=shadow` in WP9. WP11 will promote models.

---

## 13. Inference output contract

Write WP3-compatible `signals_daily` rows:

```text
run_id
strategy_id = cpd_lstm
model_id
as_of_date
root
signal_raw
signal_clipped
is_valid
invalid_reason
feature_hash
created_at_utc
```

Rules:

- `signal_raw` is the tanh output before final explicit clipping.
- `signal_clipped = clip(signal_raw, -1, 1)`.
- Invalid sequence rows must be emitted if the existing WP8 signal writer emits invalid rows; otherwise document and test the chosen behavior. Prefer emitting invalid rows for auditability.
- `feature_hash` should be the hash of the feature row at the sequence end date `t`, not a hash of the whole sequence.
- Do not write targets or order intents.

---

## 14. Determinism and reproducibility

Add utilities:

```text
set_global_seed(seed)
resolve_device(device)
set_torch_determinism(mode)
```

Behavior:

- `device=auto` chooses CUDA if available, else CPU.
- Unit tests should force CPU unless specifically marked GPU.
- `deterministic_mode=strict` should call deterministic algorithms and fail on unsupported ops.
- `deterministic_mode=warn` should set seeds and deterministic flags where practical, but warn if full determinism is not guaranteed.
- Record device, torch version, CUDA availability, and seed in `metrics.json` and `train_manifest.json`.

Do not require bit-identical CUDA training across driver versions. For WP9 acceptance, CPU synthetic smoke should be deterministic; GPU reproducibility can be approximate and logged.

---

## 15. QA requirements

Training QA should check:

- Nonzero train and validation samples.
- All required features present in the configured order.
- No NaN/inf after standardization.
- Label alignment spot-checks pass.
- Validation Sharpe can be computed.
- Model outputs are finite and within [-1, 1].
- Artifact files and hashes exist.

Inference QA should check:

- `signals_daily` schema matches WP3.
- Every valid signal has finite `signal_raw` and `signal_clipped`.
- `signal_clipped` is within [-1, 1].
- `feature_hash` is present for valid rows.
- Duplicate key check: `run_id, strategy_id, as_of_date, root`.
- Future feature mutation test exists in unit tests, even if not in QA CLI.

---

## 16. Tests

Add or extend tests under `tests/unit` and `tests/smoke`.

Required tests:

1. `test_cpd_lstm_feature_order_is_fixed`
2. `test_cpd_lstm_sequence_builder_uses_63_rows`
3. `test_cpd_lstm_label_uses_next_root_trading_day_return`
4. `test_cpd_lstm_no_lookahead_in_inference`
5. `test_cpd_lstm_standardizer_fit_train_only`
6. `test_cpd_lstm_model_output_range`
7. `test_cpd_lstm_artifact_round_trip_same_signals`
8. `test_cpd_lstm_signals_schema_matches_wp3`
9. `test_cpd_lstm_training_smoke_cpu_if_torch_available`
10. `test_tsmom_still_runs_after_wp9`

Live vendor/API/broker calls must not be required for tests.

---

## 17. Acceptance criteria

WP9 is complete when:

1. `make test` passes.
2. `make wp9-cpd-lstm-smoke-offline` passes locally.
3. A synthetic model artifact is created with all required files.
4. Synthetic inference writes valid `signals_daily` rows with `strategy_id=cpd_lstm`.
5. TSMOM fallback tests still pass.
6. No vendor/API/broker calls are made.
7. The model code supports CPU and `device=auto` without requiring CUDA.
8. The implementation is ready for WP10 walk-forward orchestration.

---

## 18. Implementation notes for Codex

Prefer small, reviewable commits:

1. Config + optional dependency plumbing.
2. Dataset/standardizer utilities.
3. Model/loss implementation.
4. Training artifact writer.
5. Inference signal writer.
6. CLI + Makefile target.
7. Tests + docs.

Do not over-engineer. A small deterministic CPD-LSTM that writes correct artifacts is more valuable at this stage than a complex model with uncertain lineage.
