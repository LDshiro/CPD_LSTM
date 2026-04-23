# 0013 — IBKR paper-shadow integration v1

## Status
Accepted

## Context

The system now has:
- feature pipeline
- TSMOM fallback
- CPD-LSTM candidate flow
- walk-forward evaluation
- broker-neutral target / order intent planning

A broker boundary is needed before end-to-end shadow execution can be wired.

## Decision

Use **IBKR TWS API / IB Gateway** as the first broker adapter.

WP13 supports:
- `shadow_only`
- `paper_submit`

WP13 does not support:
- live submission
- Client Portal Web API trading
- complex order types
- combo execution
- market-data-driven limit pricing

## Consequences

### Positive
- narrow and auditable broker interface
- deterministic offline testing possible
- safe progression toward WP14 shadow batch
- explicit separation between planning and execution

### Negative
- paper execution behavior differs from live
- order management complexity begins here
- IBKR callback model requires careful state coordination

## Follow-up

WP14 will wire the daily shadow batch through:
ingest -> features -> signals -> targets -> monitoring -> broker sync / preview / paper submit -> reports
