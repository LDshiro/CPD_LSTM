# 0015 — IBKR Paper-Shadow Integration v1

## Status

Accepted

## Context

WP12 introduced broker-neutral dry-run `order_intents`, but the repo still lacked a real broker adapter boundary.
The next step needs broker state sync, paper-safe translation, and callback reconciliation without enabling live trading.

## Decision

Use the IBKR TWS API / IB Gateway socket API as the first broker adapter.

WP13 supports:
- `shadow_only`
- `paper_submit`

WP13 does not support:
- live submission
- Client Portal Web API trading
- combo, algo, or bracket orders
- market-data-driven price discovery

The canonical workspace is:
- `data/shadow/broker/ibkr/run_id=<run_id>/`

The canonical CLI entrypoint is:
- `python -m cpdshadow.cli broker-ibkr ...`

## Consequences

### Positive

- preserves WP12 as the broker-neutral planning layer
- adds a narrow, auditable broker adapter
- keeps offline and mock testing deterministic
- makes `shadow_only` and `paper_submit` explicit and separately guarded

### Negative

- paper fills remain simulated and non-production
- contract resolution and callback correlation add operational complexity
- the real adapter still depends on a local TWS / Gateway session outside tests

## Notes

- `shadow_only` never calls `placeOrder`
- `paper_submit` requires `CPDSHADOW_ENABLE_PAPER_SUBMIT=1`
- account summary remains a run-local artifact rather than a new global canonical table
- the root-level WP13 planning docs remain as planning inputs; canonical docs live under `docs/`
