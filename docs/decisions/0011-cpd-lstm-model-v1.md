# 0011 CPD-LSTM Model Skeleton v1

- Status: accepted
- Date: 2026-04-23

## Context

WP9 introduces the first trainable CPD-LSTM layer. It must consume the WP7
`features_daily` contract, use WP6 `continuous_daily.daily_return` only for
training labels, save reloadable artifacts, and emit the same `signals_daily`
contract established by WP8.

## Decision

- Implement a minimal pure-PyTorch `cpd_lstm_v1`: 63-session sequences, 14 fixed
  `features_v1` inputs, one LSTM layer, dropout, and a tanh signal head.
- Keep PyTorch in the optional `ml` dependency extra and lazy-load ML code from
  CLI commands so TSMOM and non-ML tests do not require torch.
- Fit feature standardization only on train-window feature rows, persist it with
  the model artifact, and reuse it for validation and inference.
- Store inference output under the WP8-compatible layout
  `data/research/signals_daily/strategy_id=cpd_lstm/model_id=<model_id>/run_id=<run_id>/year=<YYYY>`.
- Register candidate artifacts in `model_registry`; WP9 never promotes a model
  to `shadow`.

## Consequences

- WP10 can reuse the train/infer interface for walk-forward orchestration.
- TSMOM remains the fallback and continues to use the existing signal path.
- CUDA can be used locally, but CPU deterministic synthetic smoke is the WP9
  acceptance path.
