# Decision 0009 — CPD-LSTM Model Skeleton v1

Date: 2026-04-23
Status: Accepted for WP9 implementation

## Context

WP4-WP8 provide the data, roll, continuous, feature/CPD, and TSMOM fallback layers. The next necessary step is a CPD-LSTM model that can train locally, save self-contained artifacts, and emit the same `signals_daily` contract as the fallback strategy.

The research motivation is Slow Momentum with Fast Reversion: a CPD module can help a momentum model respond to regime change and fast reversals while retaining slow trend exposure. For this project, the operational priority is reproducible shadow readiness rather than maximum model complexity.

## Decision

Implement a minimal CPD-LSTM v1 in pure PyTorch:

- Input: 63-day sequences of the 14 fixed `features_v1` columns.
- Model: 1-layer LSTM, hidden size 64, dropout after LSTM, small MLP head, tanh output.
- Output: dimensionless signal in [-1, 1].
- Training label: next root-observed adjusted daily return from `continuous_daily`, normalized by known 60-day volatility at the signal date.
- Loss: ex-cost Sharpe-style panel loss.
- Artifact: self-contained folder with checkpoint, config, feature order, standardizer, metrics, manifest, model card, and hashes.
- Inference output: WP3/WP8-compatible `signals_daily` with `strategy_id=cpd_lstm`.

## Consequences

Positive:

- Keeps CPD-LSTM compatible with the existing signal and target pipeline.
- Makes WP10 walk-forward orchestration straightforward.
- Avoids broker/data vendor coupling.
- Keeps model small enough to debug locally and run on CPU for tests.

Trade-offs:

- No cross-asset learning in v1.
- No Transformer/attention in v1.
- Cost modeling inside the loss is approximate.
- Performance is not judged until WP10/WP11.

## Non-goals

- No live or paper execution.
- No model promotion to shadow status.
- No walk-forward loop.
- No hyperparameter sweep.
