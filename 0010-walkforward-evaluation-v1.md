# ADR 0010 — Walk-forward Evaluation v1

## Status

Accepted for WP10 implementation.

## Context

The project has completed data ingestion, roll mapping, continuous series construction, feature/CPD construction, TSMOM fallback signals, and a CPD-LSTM train/inference skeleton. The next decision is how to evaluate CPD-LSTM against TSMOM before any model-release or shadow-batch wiring.

CPD-LSTM is intended to improve over TSMOM particularly around momentum turning points and rapid regime shifts. Therefore, evaluation must include both full-period OOS performance and reversal-event performance.

## Decision

WP10 will implement quarterly walk-forward evaluation with:

- Production default: 10 years train, 2 years validation, next quarter OOS.
- CPD-LSTM trained separately for each fold.
- TSMOM generated over the exact same OOS dates and roots.
- Same risk/cost/portfolio layer for both strategies.
- Daily settlement-based PnL convention: signal at `t` earns next observed adjusted return.
- Reversal events defined by CPD 21/63 scores above train+validation p95.
- Readiness gates written to `gates.json`, but no model promotion.

## Rationale

### Quarterly walk-forward

Quarterly folds are frequent enough to reveal regime drift while not requiring daily or weekly retraining. They also map naturally to the intended production retraining cadence.

### 10y train / 2y validation

The CPD-LSTM model needs enough history to learn cross-regime behavior. A 2-year validation window provides a recent holdout for early stopping and model diagnostics while preserving a long training history.

### No hyperparameter search in WP10

Hyperparameter search would mix model selection with evaluation and increase backtest overfitting risk. WP10 evaluates a fixed WP9 model family. Model promotion and any controlled future search belong in later work packages.

### Same risk/cost layer

Signal-level comparisons can be misleading. The project’s objective is tradeable shadow operation, so both CPD-LSTM and TSMOM must be evaluated after the same contract sizing, risk caps, and modeled costs.

### Next observed adjusted return

The project uses settlement-based daily features and labels. The simplest consistent evaluation convention is: signal at date `t` earns the next observed adjusted settlement return. This avoids intraday assumptions and aligns with WP9’s next-day label.

### Train+validation-only reversal thresholds

Reversal buckets must not use OOS information to define events. CPD thresholds are therefore computed per fold and root from train+validation only.

### Gates without promotion

WP10 should report whether a model appears ready, but it should not change model status. WP11 will handle model release candidates and promotion decisions.

## Consequences

Positive:

- Clear apples-to-apples CPD-LSTM vs TSMOM comparison.
- Strong lineage and reproducibility.
- Direct evidence for the model’s claimed edge in reversal regimes.
- Outputs can feed WP11 model-release decisions.

Negative / limitations:

- Daily evaluation does not capture intraday execution slippage.
- Event-bucket statistics may be sparse.
- Quarterly retraining is more expensive than a single static backtest.
- Performance gates can fail even when code is correct.

## Acceptance

The implementation is accepted when:

- `make test` passes.
- `make wp10-walkforward-smoke-offline` passes.
- Reports and gates are generated.
- Structural QA catches malformed outputs.
- Performance underperformance is represented as failed gates, not as a program crash.
