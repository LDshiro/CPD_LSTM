# Decision 0010 — TSMOM Signal Infrastructure v1

Status: accepted for WP8 implementation  
Date: 2026-04-21

## Context

The project needs a reliable strategy-output path before CPD-LSTM inference is
introduced. The fallback strategy must be simple enough to audit and robust
enough to run whenever the model layer is unavailable.

## Decision

Implement WP8 with:

- `signals_daily` as the canonical strategy output table
- `tsmom_v1` as the first formulaic fallback strategy
- exact formula:
  - `mean(sign(ret_21), sign(ret_63), sign(ret_252))`
- reusable signal strategy interface for future WP9 model-backed output
- stable formula artifact JSON and SHA-256
- snapshot-to-signal lineage through WP7 `features_daily`

Use run-partitioned signal storage:

```text
data/research/signals_daily/strategy_id=<strategy_id>/model_id=<model_id>/run_id=<run_id>/year=<YYYY>/
```

## Rationale

1. TSMOM is transparent and easy to validate as a fallback.
2. A stable signal contract lets WP9 reuse the same downstream path.
3. Run-partitioned storage keeps repeated offline builds and multiple signal runs
   from colliding.
4. Formula artifacts provide an auditable record even for non-ML strategies.

## Consequences

- The repo can generate fallback strategy outputs before CPD-LSTM inference
  exists.
- Monitoring and shadow orchestration can later choose between `tsmom` and
  `cpd_lstm` without redefining downstream interfaces.
- A persisted `model_registry` table is still deferred; WP8 only provides the
  pure row shape for formulaic strategies.

## Non-goals

- No CPD-LSTM training or inference
- No walk-forward evaluation
- No broker integration
- No order intents
- No live or paper execution
