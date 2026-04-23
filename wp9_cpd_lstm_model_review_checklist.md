# WP9 Review Checklist — CPD-LSTM Model Skeleton

Use this after Codex implements WP9.

## Scope and boundaries

- [ ] WP9 writes `signals_daily`, not `targets_daily` or `order_intents`.
- [ ] WP9 does not call Databento, IBKR, or any live network service.
- [ ] WP9 does not implement walk-forward orchestration; that remains WP10.
- [ ] WP8 TSMOM fallback still works and its tests still pass.

## Configuration

- [ ] `config/settings.base.yml` has a stable `models.cpd_lstm` section.
- [ ] Feature order is explicitly listed and not inferred from dataframe column order.
- [ ] Sequence length is fixed at 63.
- [ ] Model defaults match v1: 1-layer LSTM, hidden 64, dropout after LSTM 0.20, tanh output.
- [ ] PyTorch dependency does not break default non-ML tests.
- [ ] RTX5090/CUDA install instructions are present but not forced in CI.

## Dataset and no-lookahead

- [ ] Training reads `features_daily` and `continuous_daily`; inference reads `features_daily` only.
- [ ] A sequence ending at `t` uses only rows through `t`.
- [ ] Training label for `t` uses `continuous_daily.daily_return` at the next root-observed trading date.
- [ ] No global trading calendar is assumed.
- [ ] Warmup and incomplete feature rows are excluded or marked invalid consistently.
- [ ] A unit test mutates future features/returns and confirms past inference outputs do not change.

## Standardization

- [ ] Standardizer is fit on train data only.
- [ ] Standardizer is saved to `standardizer.json`.
- [ ] Reloaded artifacts use the saved standardizer, not a newly fit one.
- [ ] NaN/inf values are rejected before model training.

## Loss and model behavior

- [ ] The training objective is Sharpe/ex-cost style, not MSE by mistake.
- [ ] Cost settings are read from existing config if available, otherwise default cost emits a QA warning.
- [ ] Model output is finite and clipped to [-1, 1].
- [ ] Gradient clipping is applied.
- [ ] CPU smoke training is deterministic for the same seed.

## Artifact contract

- [ ] Artifact directory contains `model.pt`, `config.json`, `feature_order.json`, `standardizer.json`, `metrics.json`, `train_manifest.json`, `model_card.md`, and `sha256sums.txt`.
- [ ] `model.pt` includes model state, model config, feature order, standardizer, training run id, and model id.
- [ ] `sha256sums.txt` is stable and excludes itself.
- [ ] `training_runs` receives a WP3-compatible row.
- [ ] `model_registry` receives a WP3-compatible row with `model_status=candidate`.

## Inference and signal contract

- [ ] CPD-LSTM inference writes WP3-compatible `signals_daily` rows.
- [ ] `strategy_id=cpd_lstm`.
- [ ] `model_id` comes from the loaded artifact/CLI and is stable.
- [ ] `feature_hash` is copied from the sequence end row.
- [ ] Duplicate keys are rejected or reported.
- [ ] Invalid rows have `is_valid=false` and a stable `invalid_reason`.

## CLI and smoke

- [ ] Train CLI accepts explicit train/validation windows.
- [ ] Infer CLI accepts explicit start/end windows.
- [ ] QA CLI emits JSON and Markdown reports.
- [ ] `make wp9-cpd-lstm-smoke-offline` creates synthetic data, trains, infers, and validates signals.
- [ ] `make test` passes without vendor/broker credentials.

## Readiness for WP10

- [ ] Train CLI can be called repeatedly with different date windows.
- [ ] Model artifacts are self-contained and reloadable.
- [ ] Metrics and manifests include config hash, snapshot id, seed, torch version, and git commit.
- [ ] No hard-coded smoke dates leak into production commands.
