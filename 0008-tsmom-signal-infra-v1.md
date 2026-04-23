# Decision 0008 — TSMOM Fallback / Signal Infrastructure v1

Status: Accepted for WP8 implementation

## Context

The project needs a reliable strategy output path before CPD-LSTM is introduced. The fallback strategy must be simple enough to audit and robust enough to run whenever the model layer fails.

## Decision

Implement TSMOM v1 as the formulaic fallback strategy:

```text
signal = mean(sign(ret_21), sign(ret_63), sign(ret_252))
```

TSMOM consumes WP7 `features_daily` and writes WP3-compatible `signals_daily`. WP8 also introduces a generic signal interface for future CPD-LSTM inference.

## Rationale

A 1/3/12-month time-series momentum baseline is transparent, easy to test, consistent with the project's prior v1 design, and suitable as a fallback/benchmark. It also shares the same signal-to-target path that the CPD-LSTM model will later use.

## Consequences

- The project can produce valid strategy signals before model training exists.
- WP9 can focus on model inference without redefining output schemas.
- WP14 can implement champion/fallback selection by choosing between `cpd_lstm` and `tsmom` signals.
- TSMOM remains deliberately simple; performance enhancements are out of scope for WP8.

## Non-goals

- No CPD gating.
- No dynamic lookback weighting.
- No broker integration.
- No walk-forward evaluation.
