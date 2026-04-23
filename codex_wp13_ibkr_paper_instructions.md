# WP13 Codex Instructions — IBKR Adapter / Paper-Shadow Integration v1

## Goal

Implement **WP13: IBKR adapter / paper-shadow integration** on top of WP12 broker-neutral order intents.

This step must add a **real IBKR boundary** using the **TWS API / IB Gateway socket API**, while keeping the system safe:

- it must support **read state** from IBKR
- it must support **paper submission mode**
- it must support **shadow mode** where orders are translated and logged but **not sent**
- it must **not** enable live trading defaults
- it must preserve WP12 as the broker-neutral planning layer

The official TWS API is a TCP socket API connected to **TWS or IB Gateway**, and order placement is done through the API order flow rather than the Client Portal Web API. The Web API has a documented request-rate limit and is **not** the boundary chosen for this project. Paper trading is simulated and does **not** reflect production fills. Open orders and positions are available through TWS API request / callback flows such as `reqOpenOrders`, `reqAllOpenOrders`, `reqPositions`, `openOrder`, `orderStatus`, and `position`. Use those as the conceptual foundation for the adapter.  
Do **not** implement a Web API trading adapter in WP13.

## Scope of WP13

### In scope
1. IBKR connection/session abstraction for TWS / IB Gateway
2. Contract resolution for supported futures roots and active raw symbols
3. Read-only broker sync:
   - positions
   - open orders
   - account summary subset
4. Translation:
   - WP12 `order_intents` -> IBKR order requests
5. Two execution modes:
   - `shadow_only`
   - `paper_submit`
6. Reconciliation outputs back into project tables / artifacts
7. Offline and mocked tests
8. Optional live-smoke entrypoint guarded by explicit env vars

### Out of scope
1. live production order submission
2. advanced order types / algos
3. market data subscriptions for signal generation
4. combo spread execution
5. fill-based PnL accounting beyond storing what IBKR reports
6. auto-restart daemon / orchestration layer
7. daily full shadow batch orchestration (that is WP14)

---

## Architectural intent

Preserve the layering:

- WP12:
  - computes `planned_order_intents`
  - dry-run transforms into broker-neutral `order_intents`
- WP13:
  - resolves IBKR contracts
  - optionally submits paper orders
  - captures broker state and broker responses
  - writes broker-facing artifacts / tables
- WP14:
  - wires the full daily batch end-to-end

Add a dedicated package boundary such as:

- `src/cpdshadow/broker/ibkr/`
  - `client.py`
  - `contracts.py`
  - `translator.py`
  - `reconcile.py`
  - `models.py`
  - `service.py`
  - `mock.py`

Use explicit dataclasses / pydantic models for adapter I/O.

---

## Required design decisions

### 1. Connection boundary

Implement a thin service around the official Python TWS API client model:

- one component for outbound requests
- one event sink / wrapper for inbound callbacks
- one coordinator that waits for:
  - next valid order id
  - positions snapshot completion
  - open orders snapshot completion
  - account summary completion

Do **not** scatter callback state across unrelated modules.

Recommended pattern:
- thread-safe event collector
- explicit request lifecycle objects
- timeout handling
- deterministic completion signals

### 2. Execution modes

Add a strict enum:

- `shadow_only`
- `paper_submit`

And a separate environment switch for future work:
- `live_submit` must **not** be implemented as a usable mode in WP13

Rules:
- `shadow_only`:
  - connect to IBKR optional; if connected, read broker state
  - never call `placeOrder`
  - persist translated broker payload preview
  - persist final status as `not_sent`
- `paper_submit`:
  - may call `placeOrder`
  - only when explicit environment guard is enabled
  - must stamp `paper_account=true`
  - must persist outbound request payload and all callbacks observed

### 3. Contract resolution

You must resolve a tradable IBKR futures contract from:
- `root`
- `lead_raw_symbol`
- exchange metadata from instrument config / contract master

Implement a deterministic resolver:
- input: project symbol data
- output:
  - IBKR contract object fields
  - exchange
  - local symbol / last trade date where needed
  - multiplier if required for validation
  - a stable `broker_contract_key`

Do not use `continuous_daily` for broker routing.

If resolution is ambiguous:
- reject the order intent
- emit a structured alert / reason
- do not guess

### 4. Broker state snapshot

Support these read paths:

- positions snapshot
- open orders snapshot
- account summary subset:
  - NetLiquidation
  - ExcessLiquidity
  - InitMarginReq
  - MaintMarginReq
  - BuyingPower
  - AvailableFunds

Persist to project outputs that are compatible with WP3 schema intent:
- `broker_positions_snapshot`
- broker-side open-order snapshot artifact or table
- journal event records

If the existing schema does not yet include a dedicated open-order snapshot table, add **one** now in a backwards-compatible way:
- `broker_open_orders_snapshot`

### 5. Translation from order intents to IBKR orders

For WP13, support only the simplest order family:

- futures only
- `LMT` orders only in `paper_submit`
- no bracket / attached / algo orders
- no combo orders

For `shadow_only`, build the same translation object but do not submit it.

Translation rules:
- buy/sell direction from signed quantity delta
- quantity must be absolute and integer
- action in `{BUY, SELL}`
- tif default `DAY`
- outsideRth false unless futures venue makes it irrelevant; store the chosen flag explicitly
- limit price:
  - WP13 may allow `null` if not submitting
  - for `paper_submit`, require a deterministic paper price policy

### 6. Paper price policy

Because paper fills are simulated and not representative of live execution, keep the policy simple and auditable.

Implement a pluggable paper limit price policy:
- `static_unpriced_preview` for shadow
- `best_effort_from_reference` for paper

For WP13 `best_effort_from_reference`, do **not** subscribe to real-time market data.
Instead use a deterministic reference hierarchy from local data:
1. latest available settlement from `contracts_daily`
2. else latest close / ohlcv daily close if available
3. else reject

Then apply a configurable aggressiveness buffer:
- buy: `reference_price * (1 + buffer_bps/10000)`
- sell: `reference_price * (1 - buffer_bps/10000)`

Round to the instrument tick size.

Default buffer:
- 5 bps for paper futures testing

### 7. Order id / correlation

Persist correlation identifiers:
- `run_id`
- `intent_id`
- `broker_request_id`
- `ib_order_id` if assigned
- `perm_id` if received
- `client_id`
- `account`

Every callback must be attributable to the originating intent where possible.

### 8. Reconciliation

Build a reconciliation layer that consumes:
- submitted order previews / requests
- IBKR callbacks:
  - openOrder
  - orderStatus
  - execDetails if available
  - error
- broker snapshots

Outputs:
- enriched `order_intents`
- `journal_events`
- structured reconciliation report

Status normalization should map raw broker events into project-level states such as:
- `not_sent`
- `submitted`
- `pre_submitted`
- `filled`
- `partially_filled`
- `cancelled`
- `rejected`
- `api_error`
- `unknown`

### 9. Error handling

Create a normalized broker error model with fields like:
- timestamp
- code
- message
- request context
- intent id
- severity
- retryable

Do not crash the whole process on:
- one contract resolution failure
- one order rejection
- one missing account field

Do fail the run on:
- inability to establish required broker session in `paper_submit`
- inability to obtain next valid order id in `paper_submit`
- gross account mismatch
- adapter schema corruption

### 10. Safety rails

Mandatory guards:
- no live submission mode
- explicit paper guard env var, for example:
  - `CPDSHADOW_ENABLE_PAPER_SUBMIT=1`
- explicit account allowlist
- explicit host/port/client-id config
- reject if account alias / id does not match allowed config
- reject if monitoring action from WP15 is:
  - `hold`
  - `reduce_only` and translated order would increase exposure

For `reduce_only`, implement translation logic that:
- only reduces existing absolute position
- never flips sign
- never increases gross contracts

---

## Files to add or update

Implement at least the following:

### New code
- `src/cpdshadow/broker/__init__.py`
- `src/cpdshadow/broker/ibkr/__init__.py`
- `src/cpdshadow/broker/ibkr/models.py`
- `src/cpdshadow/broker/ibkr/client.py`
- `src/cpdshadow/broker/ibkr/contracts.py`
- `src/cpdshadow/broker/ibkr/translator.py`
- `src/cpdshadow/broker/ibkr/reconcile.py`
- `src/cpdshadow/broker/ibkr/service.py`
- `src/cpdshadow/broker/ibkr/mock.py`

### CLI / application entrypoints
- `src/cpdshadow/cli/broker_ibkr.py`

### Config
- update `config/settings.base.yml`
- update config schema / settings models
- add `.env.example` entries for IBKR host/port/client id/account/paper guard

### SQL / schema
- add migration or schema update for:
  - `broker_open_orders_snapshot`
  - any minimal broker callback artifact table if needed

### Docs
- `docs/broker_ibkr_v1.md`
- `docs/runbooks/wp13_ibkr_paper_runbook_v1.md`
- `docs/decisions/0013-ibkr-paper-shadow-integration-v1.md`

### Tests
- unit tests for:
  - contract resolution
  - reduce_only translation
  - limit price rounding
  - error normalization
  - callback reconciliation
- integration tests with mock broker
- offline smoke target

---

## CLI requirements

Add a broker CLI group, for example:

```bash
python -m cpdshadow.cli.broker_ibkr sync-state   --as-of 2026-04-23   --mode shadow_only

python -m cpdshadow.cli.broker_ibkr submit-intents   --as-of 2026-04-23   --mode shadow_only

python -m cpdshadow.cli.broker_ibkr submit-intents   --as-of 2026-04-23   --mode paper_submit
```

Also add Makefile targets:

```make
make wp13-broker-smoke-offline
make wp13-broker-smoke-mock
make wp13-broker-sync-shadow
make wp13-broker-submit-paper
```

Rules:
- offline smoke must not require IBKR
- mock smoke must simulate callbacks
- real paper submit command must refuse to run unless the explicit guard env var is set

---

## Data contracts and persistence

### Broker positions snapshot
Write normalized broker positions into `broker_positions_snapshot` with at least:
- snapshot id
- run id
- account
- captured at
- root if resolvable
- raw symbol if resolvable
- broker contract key
- quantity
- avg cost
- source = `ibkr_tws_api`

### Broker open orders snapshot
Add:
- `broker_open_orders_snapshot`
with fields such as:
- snapshot id
- run id
- account
- captured at
- client id
- ib_order_id
- perm_id
- broker contract key
- raw symbol if resolvable
- action
- total quantity
- filled quantity
- remaining quantity
- order type
- tif
- lmt price
- status
- source = `ibkr_tws_api`

### Order intents enrichment
Update existing broker-boundary outputs so an order intent can carry:
- broker name
- broker mode
- broker contract key
- ib order id
- perm id
- final normalized status
- broker error code/message
- submitted at / updated at timestamps

### Journal events
Emit journal events for:
- connection established
- next valid id received
- positions snapshot completed
- open orders snapshot completed
- intent translated
- intent rejected before send
- order submitted
- broker callback received
- reconciliation completed

---

## Testing requirements

### Unit tests
Use pure offline synthetic data. No IBKR dependency.

### Mock integration tests
Implement a fake / mock adapter that simulates:
- nextValidId
- positions stream + end
- openOrder + orderStatus + end
- success path
- reject path
- timeout path

### Optional live tests
May exist, but must be skipped by default unless explicit env vars are present:
- host
- port
- client id
- account
- paper guard

### Required invariants
1. `shadow_only` never calls `placeOrder`
2. `paper_submit` refuses without env guard
3. `reduce_only` never increases absolute exposure
4. ambiguous contract resolution rejects safely
5. order status reconciliation is deterministic
6. stale / missing reference price rejects paper submission
7. account mismatch rejects the run

---

## Acceptance criteria

WP13 is done when all of the following are true:

1. `make test` passes
2. `make wp13-broker-smoke-offline` passes without IBKR
3. `make wp13-broker-smoke-mock` passes and shows:
   - submitted path
   - rejected path
   - partial fill / orderStatus path
4. broker state sync can populate:
   - `broker_positions_snapshot`
   - `broker_open_orders_snapshot`
5. `submit-intents --mode shadow_only`:
   - reads intents
   - translates them
   - writes not-sent results
   - produces no broker submission
6. `submit-intents --mode paper_submit`:
   - requires explicit env guard
   - writes submission artifacts
   - reconciles callbacks into normalized statuses
7. docs and runbook are present
8. no live submission path is enabled

---

## Implementation notes

- Prefer TWS API / IB Gateway, not Web API, for this project boundary.
- Keep the adapter narrow and auditable.
- Preserve broker-neutral upstream interfaces.
- Favor deterministic offline tests over clever abstractions.
- Keep the paper path simple and conservative because paper fills are simulated.

## Deliverables summary

At the end of WP13, I should be able to:

1. generate broker-neutral intents from WP12
2. connect to IBKR in a controlled way
3. sync positions and open orders
4. translate intents into IBKR paper orders
5. either preview them (`shadow_only`) or submit them to paper (`paper_submit`)
6. capture broker callbacks into project artifacts
7. do all of the above without enabling live trading
