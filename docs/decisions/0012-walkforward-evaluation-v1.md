# 0012 - Walk-forward Evaluation v1

## Status

Accepted for WP10 implementation.

## Context

WP4-WP9 provide raw/curated data, roll maps, continuous series, features/CPD, TSMOM signals, and a
CPD-LSTM train/inference skeleton. Before any model-release work, the project needs reproducible
evidence that CPD-LSTM improves on TSMOM under the same universe, data, risk, and cost assumptions.

The requested historical ADR number `0010` is already used by WP8 and `0011` is used by WP9, so the
canonical WP10 decision note is `0012`.

## Decision

Implement quarterly walk-forward evaluation with:

- 10 years train, 2 years validation, next quarter OOS as production defaults.
- Shortened smoke parameters that exercise the same code path.
- CPD-LSTM trained per fold by reusing WP9 model code.
- TSMOM generated for the same OOS dates and roots by reusing WP8 strategy code.
- Same Step 11 risk/cost/portfolio layer for both strategies.
- Daily convention where signal at `t` earns the next observed adjusted return.
- Reversal-bucket thresholds computed from train+validation only.
- Readiness gates written to `gates.json`, with no model promotion.

## Rationale

Quarterly folds match the intended retraining cadence and provide enough OOS observations to inspect
regime drift. A 10y/2y train/validation split gives the model cross-regime history while preserving a
recent holdout. Hyperparameter search is intentionally excluded to avoid mixing model selection with
evaluation.

Evaluating after the shared risk/cost layer keeps champion and fallback comparable. Train+validation
only reversal thresholds prevent OOS leakage in the bucket where CPD-LSTM is expected to help most.

## Consequences

Positive:

- Apples-to-apples CPD-LSTM versus TSMOM evidence.
- Deterministic reports and gates for later model-release decisions.
- Explicit diagnostics for reversal regimes.

Tradeoffs:

- Smoke runs need optional PyTorch installed.
- Daily settlement evaluation does not model intraday fills.
- Failed performance gates are expected possible outcomes and do not imply a program failure.

