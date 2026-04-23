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

---

## 1. Scope

### In scope

1. Load `targets_daily`.
2. Load the latest `broker_positions_snapshot` (or a specified snapshot).
3. Load `contract_master` and any minimal root metadata needed for validation.
4. Build deterministic `planned_order_intents`.
5. Apply control-action logic:
   - `run_cpd_lstm`
   - `fallback_tsmom`
   - `hold`
   - `reduce_only`
6. Handle contract rolls using `lead_raw_symbol`.
7. Run a **dry-run adapter** that validates planned intents and emits final `order_intents` with:
   - `status = not_sent` for valid intents
   - `status = rejected` for invalid intents
8. Write `journal_events` and QA reports.
9. Add CLI commands and offline synthetic tests.
10. Update README and Makefile.

### Out of scope

- No broker connection.
- No IBKR `conid` or contract lookup over network.
- No order submission.
- No `placeOrder`.
- No live or paper execution.
- No market data requests.
- No fill simulation.
- No PnL changes.
- No changes to model artifacts.
- No walk-forward recomputation.
- No target sizing recomputation.

---

## 2. Why WP12 is intentionally broker-neutral

WP13 will integrate the real broker adapter.  
WP12 should stay broker-neutral because:

1. **TWS API / IB Gateway integration is a separate concern.**
2. Real broker connections add operational state, session management, pacing, and error handling.
3. Paper trading is a simulator, not a faithful execution engine.
4. This project needs a reliable offline boundary first.

Therefore, in WP12:

- `broker_contract_id` stays nullable.
- `order_type` is an **intent policy**, not a submitted ticket.
- `limit_price` stays null.
- final output is **not sent**, not submitted.

---

## 3. Branch and implementation discipline

Create a new branch:

```bash
git checkout -b wp12-broker-boundary-dry-run
```

Keep all tests offline by default.

Do not require:

- Databento
- IBKR
- TWS
- IB Gateway
- Web API
- CUDA
- network access

All outputs must be deterministic when given the same inputs and fixed timestamps.

---

## 4. Expected new/modified files

Codex may adapt names to existing repo conventions, but preserve the semantic contract.

```text
src/cpdshadow/execution_boundary.py
src/cpdshadow/order_intents.py
src/cpdshadow/dry_run_adapter.py
src/cpdshadow/cli.py
config/execution_boundary.yml
docs/broker_boundary_v1.md
docs/decisions/0012-broker-boundary-dry-run-v1.md
docs/wp12_broker_boundary_review_checklist.md
tests/unit/test_order_intents.py
tests/unit/test_dry_run_adapter.py
tests/unit/test_execution_boundary_rules.py
tests/smoke/test_wp12_broker_boundary_smoke.py
Makefile
README.md
```

If there are already suitable helpers in the repo for config loading, hashing, report writing, JSON/Parquet IO, and identifiers, reuse them.

---

## 5. Inputs

WP12 should accept paths and/or logical selectors instead of hard-coding local assumptions.

### Required inputs

| Input | Meaning |
|---|---|
| `--targets-path` | `targets_daily` source for one run/as-of/execution context. |
| `--positions-path` | `broker_positions_snapshot` source. |
| `--contract-master-path` | Curated contract metadata. |
| `--output-dir` | Output directory for planned intents, finalized intents, reports. |

### Recommended inputs

| Input | Meaning |
|---|---|
| `--monitoring-path` | `monitoring_daily` table for action consistency. |
| `--as-of-date` | Date filter. |
| `--execution-date` | Intended execution date filter. |
| `--run-id` | Shadow/paper/live run id to isolate one batch. |
| `--strategy-id` | Optional filter. |
| `--execution-mode` | `shadow`, `paper`, or `live`. Default should be explicit, not implicit. |
| `--position-snapshot-id` | Optional exact snapshot selection override. |
| `--fixed-created-at-utc` | For deterministic tests. |
| `--strict` | Fail on structural problems. |
| `--allow-warnings` | Continue with warnings where safe. |

### Canonical table expectations

#### `targets_daily`

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

#### `broker_positions_snapshot`

Must include at least:

- `position_snapshot_id`
- `run_id` or capture context
- `execution_mode`
- `broker_contract_id` (nullable acceptable for offline mode if `raw_symbol` exists)
- `raw_symbol`
- `root`
- `position_contracts`
- `snapshot_time_utc`

#### `contract_master`

Must include enough metadata to validate contracts:

- `root`
- `raw_symbol`
- `exchange`
- `instrument_class` or equivalent
- `expiration_date` or `last_trade_date` if available
- `multiplier` if already curated

---

## 6. Canonical outputs

### 6.1 Planned intents (intermediate artifact)

Write an intermediate dataset, e.g.:

```text
<output-dir>/planned_order_intents.parquet
<output-dir>/planned_order_intents.json
```

These rows should use:

- `status = planned`
- `broker_contract_id = null`
- `order_type = marketable_limit` by default
- `limit_price = null`

### 6.2 Finalized offline intents (canonical WP12 output)

Write the canonical post-dry-run dataset:

```text
<output-dir>/order_intents.parquet
<output-dir>/order_intents.json
```

Rules:

- Valid planned intents become `status = not_sent`
- Invalid planned intents become `status = rejected`
- `submitted_at_utc` stays null
- No fills are generated

### 6.3 Additional artifacts

```text
<output-dir>/manifest.json
<output-dir>/build_summary.json
<output-dir>/dry_run_results.json
<output-dir>/qa_report.json
<output-dir>/dry_run_report.md
<output-dir>/journal_events.parquet
<output-dir>/journal_events.json
```

---

## 7. Configuration

Create `config/execution_boundary.yml` with defaults like these:

```yaml
execution_boundary:
  version: execution_boundary_v1

  default_order_type: marketable_limit

  lifecycle:
    planned_status: planned
    valid_final_status: not_sent
    invalid_final_status: rejected

  action_policy:
    supported_actions:
      - run_cpd_lstm
      - fallback_tsmom
      - hold
      - reduce_only

    expected_strategy_by_action:
      run_cpd_lstm: cpd_lstm
      fallback_tsmom: tsmom

  reduce_only:
    allow_replacement_roll: true
    allow_flip: false
    clip_to_current_abs: true

  validation:
    fail_on_unknown_contract: true
    fail_on_duplicate_target_root: true
    fail_on_mixed_sign_inventory: true
    fail_on_missing_monitoring_alignment: true
    fail_on_nonpositive_quantity: true
    fail_on_execution_date_before_asof: true

  sorting:
    close_non_lead_first: true
    close_lead_before_open_lead: true

  outputs:
    write_json: true
    write_parquet: true
    write_markdown_report: true
```

Reuse the repo’s existing config framework if one already exists.

---

## 8. Canonical execution-boundary logic

## 8.1 Position snapshot selection

If `--position-snapshot-id` is given, use it.

Otherwise, select the latest snapshot satisfying:

- same `execution_mode`
- same account context if the repo models that
- `snapshot_time_utc <= decision_time_utc` if decision time is available
- otherwise the latest available snapshot

Document the selection policy clearly in the report.

## 8.2 One target row per root

For a given `(run_id, strategy_id, as_of_date, execution_date, execution_mode, root)` there must be **at most one** target row.

Duplicate roots are structural errors.

## 8.3 Monitoring alignment

If `monitoring_daily` is provided, the selected target strategy must align with the action:

- `run_cpd_lstm` → target strategy should be `cpd_lstm`
- `fallback_tsmom` → target strategy should be `tsmom`
- `hold` and `reduce_only` may still reference either strategy, but action logic dominates

If `monitoring_daily` is absent and `strict=true`, fail clearly.
If absent and `strict=false`, continue with a warning.

---

## 9. Per-root order planning rules

For each root, build a deterministic plan from:

- target row
- current positions in snapshot for that root
- control action
- lead raw symbol

Let:

- `lead = target.lead_raw_symbol`
- `desired = target.target_contracts`
- `current_by_symbol = positions grouped by raw_symbol`
- `current_total = sum(position_contracts)`
- `lead_current = current_by_symbol.get(lead, 0)`

### 9.1 Mixed-sign inventory guard

If the same root has both positive and negative positions across symbols, treat this as unsupported inventory for v1.

Default behavior:

- emit critical journal event
- produce **no intents** for that root
- mark QA failure for that root

Do not silently net them and continue.

### 9.2 Hold

If `control_action == hold`:

- produce **no order intents**
- emit warning if `order_delta_contracts != 0`
- emit warning if off-lead inventory exists
- do not auto-roll
- do not flatten unless future work package explicitly adds forced maintenance overrides

### 9.3 Reduce-only

If `control_action == reduce_only`, compute an effective desired target:

```text
if current_total == 0:
    desired_effective = 0
elif sign(desired) != sign(current_total):
    desired_effective = 0
else:
    desired_effective = sign(current_total) * min(abs(desired), abs(current_total))
```

Interpretation:

- never increase absolute exposure
- never flip sign through zero
- pure replacement roll of the same exposure is allowed
- reducing size is allowed
- flattening is allowed

Use `desired_effective` for plan generation instead of raw `desired`.

### 9.4 Run CPD-LSTM / fallback TSMOM

If `control_action in {run_cpd_lstm, fallback_tsmom}`, use `desired` as-is.

### 9.5 Roll logic

If current positions exist in symbols other than `lead`, create flatten intents for all non-lead symbols first.

For each non-lead symbol with signed position `p`:

- if `p > 0`: create `sell | abs(p)`
- if `p < 0`: create `buy | abs(p)`

Use:

- `reason = roll` if a nonzero target will remain for the root after migration
- `reason = flatten` if the final desired target is zero

### 9.6 Lead-contract delta after flattening old contracts

After accounting for non-lead flattening, compute the required lead delta against **current lead inventory only**:

```text
lead_delta = desired_effective_or_desired - lead_current
```

If `lead_delta > 0` → `buy`
If `lead_delta < 0` → `sell`
Quantity is `abs(lead_delta)`.

Reason rules:

- if root had off-lead positions and final target is nonzero → `roll`
- else if `control_action == fallback_tsmom` → `fallback`
- else if `control_action == reduce_only` → `reduce_only`
- else if final target is zero → `flatten`
- else → `rebalance`

### 9.7 No zero-quantity intents

Never emit an intent with quantity `<= 0`.

---

## 10. Deterministic ordering

The generated plan must have a deterministic submission order even before WP13.

Canonical sort priority:

1. closing non-lead positions
2. closing lead position if it is being reduced
3. opening/increasing lead position
4. sort ties by:
   - root
   - raw_symbol
   - side (`sell` before `buy` only when it preserves the previous rules)
   - quantity
   - order_intent_id

Do **not** add randomization.

If the schema lacks an explicit sequence column, preserve this order in the written dataset.

---

## 11. Order intent field mapping

Populate the WP3 schema fields as follows:

| Field | Rule |
|---|---|
| `order_intent_id` | Deterministic hash of key fields |
| `run_id` | From selected run |
| `strategy_id` | From target row |
| `execution_mode` | From target row / CLI |
| `as_of_date` | From target row |
| `execution_date` | From target row |
| `root` | From target row |
| `raw_symbol` | Contract actually intended to trade |
| `broker_contract_id` | `null` in WP12 |
| `side` | `buy` or `sell` |
| `quantity` | Positive integer |
| `order_type` | `marketable_limit` by default |
| `limit_price` | `null` in WP12 |
| `reason` | `rebalance`, `roll`, `reduce_only`, `flatten`, or `fallback` |
| `status` | `planned` in build phase; `not_sent` or `rejected` after dry-run |
| `created_at_utc` | Deterministic fixed timestamp in tests; actual UTC in production |
| `submitted_at_utc` | `null` |

Suggested deterministic ID payload:

```text
run_id | strategy_id | execution_mode | as_of_date | execution_date | root | raw_symbol | side | quantity | reason
```

Hash this normalized string with SHA-256 and keep a readable prefix.

---

## 12. Dry-run adapter behavior

WP12 dry-run is a **validator**, not an execution simulator.

### 12.1 Validation checks per intent

Validate at least:

1. `raw_symbol` exists in `contract_master`
2. `root`/`raw_symbol` mapping is valid
3. `quantity > 0`
4. `side in {buy, sell}`
5. `status == planned`
6. `execution_date >= as_of_date`
7. no duplicate `order_intent_id`
8. no duplicate contradictory intents on the same `(root, raw_symbol, side, quantity, reason)` unless explicitly identical duplicates are rejected
9. no same-root same-symbol opposite-side intents in the same batch

### 12.2 Finalization

For each planned intent:

- if valid → `status = not_sent`
- if invalid → `status = rejected`

Do not assign `submitted_at_utc`.

### 12.3 Dry-run reports

Produce:

- total intents
- accepted intents
- rejected intents
- counts by reason
- counts by root
- counts by control action
- any structural warnings

---

## 13. Required QA invariants

Implement a QA pass that simulates the planned intent effect on current positions.

For each root:

### `run_cpd_lstm` / `fallback_tsmom`

Expected final state:

- only `lead_raw_symbol` may remain nonzero
- all other symbols must go to zero
- lead position must equal target contracts

### `reduce_only`

Expected final state:

- no increase in absolute exposure
- no sign flip
- off-lead positions may be migrated to lead only if replacement roll does not increase absolute exposure
- if replacement is disallowed by clipping, final exposure must be smaller or zero

### `hold`

Expected final state:

- unchanged from current snapshot
- zero emitted intents

### Structural QA

- no zero-quantity intents
- no missing required columns
- deterministic order preserved
- all finalized intents have status in `{not_sent, rejected}`
- no broker calls were made

Write both:

```text
<output-dir>/qa_report.json
<output-dir>/dry_run_report.md
```

---

## 14. Journal events

Write append-only operational events following the existing `journal_events` schema.

At minimum emit:

1. one `decision` or `info` event summarizing build phase
2. one `warning`/`critical` event for each root with structural issues
3. one `info` event summarizing dry-run validation results
4. one `warning` event when `hold` suppresses a nonzero delta
5. one `warning` event when `reduce_only` clips target exposure

Suggested components:

- `execution_boundary`
- `dry_run_adapter`
- `execution_qa`

Do not emit fake broker events.

---

## 15. CLI

Add subcommands under the existing CLI pattern.

### 15.1 Build intents

```bash
python -m cpdshadow.cli broker-boundary build-intents \
  --targets-path data/shadow/targets_daily/targets.parquet \
  --positions-path data/shadow/broker_positions_snapshot/positions.parquet \
  --contract-master-path data/curated/contract_master/contracts.parquet \
  --monitoring-path data/shadow/monitoring_daily/monitoring.parquet \
  --output-dir artifacts/execution_boundary/demo_run \
  --run-id shadow_20260115T120000Z_demo \
  --execution-mode shadow
```

### 15.2 Dry-run finalize

```bash
python -m cpdshadow.cli broker-boundary dry-run \
  --planned-intents-path artifacts/execution_boundary/demo_run/planned_order_intents.parquet \
  --contract-master-path data/curated/contract_master/contracts.parquet \
  --output-dir artifacts/execution_boundary/demo_run
```

### 15.3 QA

```bash
python -m cpdshadow.cli broker-boundary qa \
  --targets-path data/shadow/targets_daily/targets.parquet \
  --positions-path data/shadow/broker_positions_snapshot/positions.parquet \
  --final-order-intents-path artifacts/execution_boundary/demo_run/order_intents.parquet \
  --monitoring-path data/shadow/monitoring_daily/monitoring.parquet \
  --output-dir artifacts/execution_boundary/demo_run
```

A convenience end-to-end wrapper is fine too, but preserve the separable stages.

---

## 16. Makefile additions

Add an offline smoke target:

```make
wp12-broker-boundary-smoke-offline:
\tpytest -q tests/unit/test_order_intents.py \
\t        tests/unit/test_dry_run_adapter.py \
\t        tests/unit/test_execution_boundary_rules.py \
\t        tests/smoke/test_wp12_broker_boundary_smoke.py
```

If the repo uses another task runner, adapt accordingly.

---

## 17. Required test scenarios

Use only synthetic fixtures.

### Unit tests

1. simple rebalance on lead contract
2. flatten to zero
3. roll same size from old contract to new lead
4. roll with size reduction
5. fallback action creates `reason=fallback` on non-roll lead delta
6. hold emits no intents
7. reduce_only clips increase
8. reduce_only allows replacement roll without size increase
9. mixed-sign inventory triggers critical handling
10. duplicate target roots fail
11. invalid contract rejects in dry-run
12. deterministic order_intent_id is stable
13. final statuses are only `not_sent` or `rejected`

### Smoke test

One synthetic end-to-end case should:

- load minimal target rows
- load minimal positions snapshot
- load contract master
- build planned intents
- dry-run finalize
- run QA
- write reports
- assert no network access and no broker dependency

---

## 18. Acceptance criteria

WP12 is complete only when all of the following hold:

1. `make test` passes.
2. `make wp12-broker-boundary-smoke-offline` passes.
3. Planned intents are deterministic.
4. Finalized intents contain only `not_sent` / `rejected`.
5. `hold` never emits orders.
6. `reduce_only` never increases absolute exposure or flips sign.
7. Rolls are handled from current raw-symbol inventory, not from aggregate root counts alone.
8. `broker_contract_id` remains null in WP12.
9. No network calls are made in default tests.
10. README documents WP12 commands and boundaries.

---

## 19. Non-negotiable guardrails

- Do not call TWS, IB Gateway, or the Web API.
- Do not simulate fills.
- Do not set anything to `submitted`.
- Do not infer lead contracts from vendor continuous symbols here; use `targets_daily.lead_raw_symbol`.
- Do not silently ignore mixed-sign inventory.
- Do not silently auto-fix structural data issues.
- Do not recompute targets in WP12.
- Do not mutate source datasets in place; write artifacts to the output directory.

---

## 20. Suggested implementation order

1. Implement pure planning functions at root level.
2. Implement dataset loaders/adapters.
3. Implement planned intent writer.
4. Implement dry-run validator/finalizer.
5. Implement QA simulation.
6. Emit reports and journal events.
7. Add CLI.
8. Add tests.
9. Update README and Makefile.

---

## 21. Deliverables expected in the PR

At the end of the branch, the PR/repo diff should include:

- code
- configs
- docs
- tests
- smoke target
- README updates

and demonstrate:

```bash
make test
make wp12-broker-boundary-smoke-offline
```

with no broker/network dependency.
