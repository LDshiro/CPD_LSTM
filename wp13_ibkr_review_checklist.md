# WP13 Review Checklist — IBKR Adapter / Paper-Shadow Integration v1

## Safety
- [ ] No usable `live_submit` path exists.
- [ ] `paper_submit` is blocked unless explicit env guard is set.
- [ ] `shadow_only` never calls `placeOrder`.
- [ ] Account allowlist / account mismatch protection exists.
- [ ] `reduce_only` orders never increase absolute exposure or flip sign.

## Boundary design
- [ ] Broker-neutral WP12 layer remains intact.
- [ ] IBKR code is isolated under `src/cpdshadow/broker/ibkr/`.
- [ ] Contract resolution uses tradable raw symbols, not `continuous_daily`.
- [ ] Ambiguous contract resolution rejects safely.

## IBKR integration
- [ ] TWS API / IB Gateway path is used, not Client Portal Web API.
- [ ] next valid order id handling exists.
- [ ] positions snapshot handling exists.
- [ ] open orders snapshot handling exists.
- [ ] account summary subset handling exists.

## Persistence
- [ ] `broker_positions_snapshot` is populated.
- [ ] `broker_open_orders_snapshot` is populated.
- [ ] `order_intents` are enriched with broker identifiers / statuses.
- [ ] `journal_events` are emitted for broker lifecycle events.

## Order translation
- [ ] Only futures + simple LMT orders are supported.
- [ ] Quantities are absolute integers with BUY/SELL action derived from sign.
- [ ] Paper price policy uses local reference hierarchy and tick rounding.
- [ ] Missing reference price causes rejection, not silent fallback.

## Reconciliation
- [ ] Raw callbacks are normalized into project statuses.
- [ ] Submitted / rejected / partial / cancel paths are covered.
- [ ] Errors are normalized with code, message, severity, retryable flag.
- [ ] Reconciliation is deterministic for repeated replay of the same callbacks.

## Tests
- [ ] `make test` passes.
- [ ] `make wp13-broker-smoke-offline` passes without IBKR.
- [ ] `make wp13-broker-smoke-mock` passes with fake callback flow.
- [ ] Optional live/paper test is skipped by default.
- [ ] No normal unit/integration test requires network access.

## Docs
- [ ] `docs/broker_ibkr_v1.md` exists.
- [ ] `docs/runbooks/wp13_ibkr_paper_runbook_v1.md` exists.
- [ ] ADR / decision memo for WP13 exists.
