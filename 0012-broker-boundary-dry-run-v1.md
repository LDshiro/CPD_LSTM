# ADR 0012 — Broker Boundary / Dry-Run Adapter v1

## Status

Accepted.

## Context

The system now has:

- curated contracts
- lead map
- continuous series
- features / CPD
- TSMOM fallback
- CPD-LSTM model skeleton
- walk-forward evaluation
- model release candidate packaging

The next missing boundary is between:

- strategy/risk outputs (`targets_daily`)
- future broker submission

This boundary must be stable before any real IBKR integration.

## Decision

We introduce a dedicated **broker-neutral execution boundary**.

WP12 will:

1. consume `targets_daily`
2. consume `broker_positions_snapshot`
3. generate deterministic `planned_order_intents`
4. finalize them with a **dry-run adapter**
5. emit `order_intents`, `journal_events`, and QA reports

WP12 will **not** connect to any broker.

## Key choices

### 1. Real inventory drives roll logic

Roll and rebalance decisions use `broker_positions_snapshot`, not root-level aggregates alone.

### 2. `order_intents` are broker-neutral

`raw_symbol` is authoritative in WP12.  
`broker_contract_id` remains null until WP13.

### 3. Dry-run is validation, not execution

Valid intents become `not_sent`; invalid intents become `rejected`.

### 4. Hold emits no orders

Even if there is suppressed delta, `hold` does not auto-trade.

### 5. Reduce-only clips target exposure

It may flatten or maintain/replace exposure, but never increase absolute exposure or flip sign.

### 6. Mixed-sign inventory is not silently repaired

It is treated as a structural issue.

## Consequences

### Positive

- clean separation of concerns
- deterministic and auditable execution intent generation
- safe offline testing
- easier IBKR integration later
- easier incident review

### Negative

- no real submission yet
- no price enrichment yet
- no fill simulation
- some operational edge cases remain deferred to WP13/WP14

## Follow-up

WP13 will integrate:

- broker contract resolution
- IBKR adapter
- submission / reject handling
- broker state reconciliation
