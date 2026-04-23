# WP12 Codex Instructions — Broker Boundary / Order Intent / Dry-Run Adapter v1

## 0. Mission

Implement **WP12: Broker Boundary / Order Intent / Dry-Run Adapter v1** for the CPD-LSTM Shadow Trading System.

WP12 is the execution boundary between **portfolio targets** and the future **IBKR adapter**.  
It must convert `targets_daily` plus the latest known position snapshot into deterministic, broker-neutral **order intents**, then run a **dry-run adapter** that validates those intents and produces final offline artifacts without connecting to any broker.

This work package is about:

- separating **strategy/risk logic** from **broker submission logic**
- making execution intent generation **deterministic and auditable**
- handling **rolls, rebalances, hold, fallback, and reduce-only**
- preparing the system for **WP13 real IBKR integration**
- keeping all tests **fully offline**

WP12 must **not** place any orders, connect to TWS, connect to IB Gateway, call the Web API, simulate fills, or mark anything as submitted.

## 1. Scope

### In scope

1. Load `targets_daily`.
2. Load the latest `broker_positions_snapshot` or a specified snapshot.
3. Load `contract_master` and minimal root metadata needed for validation.
4. Build deterministic `planned_order_intents`.
5. Apply control-action logic:
   - `run_cpd_lstm`
   - `fallback_tsmom`
   - `hold`
   - `reduce_only`
6. Handle contract rolls using `lead_raw_symbol`.
7. Run a dry-run adapter that validates planned intents and emits final `order_intents` with:
   - `status = not_sent` for valid intents
   - `status = rejected` for invalid intents
8. Write `journal_events` and QA reports.
9. Add CLI commands and offline synthetic tests.
10. Update README and Makefile.

### Out of scope

- No broker connection.
- No IBKR `conid` or contract lookup over network.
- No order submission.
- No live or paper execution.
- No market data requests.
- No fill simulation.
- No PnL changes.
- No changes to model artifacts.
- No walk-forward recomputation.
- No target sizing recomputation.

## 2. Why WP12 is intentionally broker-neutral

WP13 will integrate the real broker adapter.  
WP12 should stay broker-neutral because:

1. TWS API / IB Gateway integration is a separate concern.
2. Real broker connections add session management, pacing, and operational state.
3. Paper trading is a simulator, not a faithful execution engine.
4. The project needs a reliable offline boundary first.

Therefore, in WP12:

- `broker_contract_id` stays nullable.
- `order_type` is an intent policy, not a submitted ticket.
- `limit_price` stays null.
- final output is `not_sent`, not `submitted`.

## 3. Expected files

- `src/cpdshadow/execution_boundary.py`
- `src/cpdshadow/order_intents.py`
- `src/cpdshadow/dry_run_adapter.py`
- `src/cpdshadow/cli.py`
- `config/execution_boundary.yml`
- `docs/broker_boundary_v1.md`
- `docs/decisions/0014-broker-boundary-dry-run-v1.md`
- `docs/wp12_broker_boundary_review_checklist.md`
- `tests/unit/test_order_intents.py`
- `tests/unit/test_dry_run_adapter.py`
- `tests/unit/test_execution_boundary_rules.py`
- `tests/smoke/test_wp12_broker_boundary_smoke.py`
- `Makefile`
- `README.md`

## 4. Canonical inputs

### `targets_daily`

Must include at least:

- `run_id`
- `strategy_id`
- `as_of_date`
- `execution_date`
- `execution_mode`
- `root`
- `lead_raw_symbol`
- `target_contracts`
- `current_contracts`
- `order_delta_contracts`
- `control_action`

### `broker_positions_snapshot`

Must include at least:

- `position_snapshot_id`
- `execution_mode`
- `raw_symbol`
- `root`
- `position_contracts`
- `snapshot_time_utc`

### `contract_master`

Must include enough metadata to validate contracts:

- `root`
- `raw_symbol`
- `exchange`
- `instrument_class`

## 5. Canonical outputs

### Planned intents

Write:

- `<output-dir>/planned_order_intents.parquet`
- `<output-dir>/planned_order_intents.json`

with:

- `status = planned`
- `broker_contract_id = null`
- `order_type = marketable_limit`
- `limit_price = null`

### Finalized intents

Write:

- `<output-dir>/order_intents.parquet`
- `<output-dir>/order_intents.json`

Rules:

- Valid planned intents become `status = not_sent`
- Invalid planned intents become `status = rejected`
- `submitted_at_utc` stays null
- No fills are generated

### Additional artifacts

- `<output-dir>/manifest.json`
- `<output-dir>/build_summary.json`
- `<output-dir>/dry_run_results.json`
- `<output-dir>/qa_report.json`
- `<output-dir>/dry_run_report.md`
- `<output-dir>/journal_events.parquet`
- `<output-dir>/journal_events.json`

## 6. Core policy

- `hold`: emit no intents and warn on suppressed delta or off-lead inventory.
- `reduce_only`: never increase absolute exposure and never flip sign through zero.
- `run_cpd_lstm` / `fallback_tsmom`: use the target as-is.
- Off-lead inventory is flattened first from actual current raw-symbol positions.
- Lead delta is computed only against current lead inventory.
- Mixed-sign inventory is a structural issue and must not be silently netted.

## 7. Validation expectations

Dry-run finalization must reject invalid planned intents, including:

- unknown contracts
- `execution_date < as_of_date`
- contradictory same-symbol opposite-side intents
- duplicate intent ids
- illegal statuses outside `planned` on input

## 8. QA expectations

QA must verify:

- final statuses are only `not_sent` or `rejected`
- `broker_contract_id` remains null
- `limit_price` remains null
- deterministic sequence ordering
- per-root inventory outcome matches the selected control action
- no broker calls are made

## 9. Acceptance

- All tests stay offline and synthetic.
- `make test` passes.
- `make wp12-broker-boundary-smoke-offline` passes.
- Outputs are deterministic under fixed inputs and timestamps.
