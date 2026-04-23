# WP12 Review Checklist — Broker Boundary / Dry-Run Adapter v1

Use this after Codex finishes WP12.

## Structural review

- [ ] New branch was created from the latest repo state.
- [ ] No network dependency was added to default tests.
- [ ] No IBKR, TWS, Gateway, or Web API calls exist in WP12 code paths.
- [ ] README and Makefile were updated.
- [ ] `config/execution_boundary.yml` exists and is loaded.

## Data contract review

- [ ] `targets_daily` is consumed, not recomputed.
- [ ] `broker_positions_snapshot` is consumed for actual raw-symbol inventory.
- [ ] `contract_master` is used for validation.
- [ ] `monitoring_daily` alignment is checked when provided.
- [ ] Duplicate target roots fail clearly.

## Planning logic review

- [ ] Hold emits no intents.
- [ ] Reduce-only never increases absolute exposure.
- [ ] Reduce-only never flips sign through zero.
- [ ] Replacement roll under reduce-only is allowed only when exposure does not increase.
- [ ] Off-lead inventory triggers roll or flatten planning from current positions.
- [ ] Lead delta is computed against current lead inventory only.
- [ ] Zero-quantity intents are never emitted.
- [ ] Roll same-size and roll-with-size-change both work.
- [ ] Fallback non-roll lead delta uses `reason=fallback`.

## Safety review

- [ ] Mixed-sign inventory is not silently netted.
- [ ] Unknown contracts are rejected or fail per config.
- [ ] `execution_date < as_of_date` is rejected.
- [ ] Contradictory same-symbol opposite-side intents are rejected.
- [ ] Finalized statuses are only `not_sent` or `rejected`.
- [ ] No fills are generated.

## Determinism review

- [ ] `order_intent_id` is deterministic.
- [ ] Intent ordering is deterministic.
- [ ] Re-running with the same fixtures produces byte-stable JSON or logically identical Parquet.
- [ ] Journal events are deterministic under fixed timestamps.

## Artifact review

- [ ] `planned_order_intents.parquet/json` are written.
- [ ] Final `order_intents.parquet/json` are written.
- [ ] `dry_run_results.json` is written.
- [ ] `qa_report.json` is written.
- [ ] `dry_run_report.md` is written.
- [ ] `journal_events.parquet/json` are written.

## Test review

- [ ] Unit tests cover rebalance, flatten, roll, fallback, hold, reduce-only, mixed-sign inventory.
- [ ] Smoke test is fully offline.
- [ ] `make test` passes.
- [ ] `make wp12-broker-boundary-smoke-offline` passes.

## Integration readiness

- [ ] WP12 output can be consumed later by a broker adapter without changing the `order_intents` contract.
- [ ] `broker_contract_id` remains null in WP12.
- [ ] `limit_price` remains null in WP12.
- [ ] `order_type` is treated as policy, not an already submitted broker ticket.
