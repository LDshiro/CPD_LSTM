# WP5 Codex Instructions — Roll Engine / Lead Map Builder v1

## 0. Operating context

Repository: `LDshiro/CPD_LSTM`.

You are implementing **WP5: Roll engine** for the CPD-LSTM Shadow Trading System. WP4 raw ingest is assumed to exist and to produce the WP3 schema inputs, especially:

- `contract_master`
- `contracts_daily`
- `data_snapshot_registry`
- `data_file_registry`
- `run_registry`

This work package must produce deterministic roll outputs that downstream WP6 can use to build adjusted continuous futures series.

Read these files first:

1. `docs/spec_v1.md`
2. `docs/data_schema_v1.md`
3. `sql/duckdb_schema_v1.sql`
4. `config/data_schema.yml`
5. `config/instruments.yml`
6. `config/settings.base.yml`
7. Current WP4 ingest modules and tests

Do not rewrite unrelated code. Preserve existing public APIs where practical. Prefer small pure functions with synthetic tests.

---

## 1. WP5 goal

Implement a deterministic, auditable roll engine that builds:

1. `data/curated/lead_map/`
2. `data/curated/roll_events/`
3. QA reports under `artifacts/reports/wp5_roll_engine/`
4. Registry entries for generated files, if the WP4 registry API exists

The engine must map each strategy `root` and `as_of_date` to the **actual tradable listed futures contract** to use as the lead contract.

### Non-goals

Do **not** implement these in WP5:

- backward ratio-adjusted continuous series construction (`continuous_daily`) — WP6
- feature construction or CPD — WP7
- TSMOM strategy — WP8
- model training or inference — WP9+
- IBKR broker adapter — later WP
- order generation — later WP

WP5 may compute roll reference fields such as `ratio_adjustment` and `basis_at_roll` for `roll_events`, but it must not build `continuous_daily`.

---

## 2. Required roll policy

Implement exactly this policy as v1:

```yaml
roll:
  policy_version: volume3_hardroll_v1
  builder_version: roll_engine_v1
  volume_confirmation_days: 3
  effective_lag_trading_days: 1
  volume_column: volume
  volume_trigger_operator: next_strictly_greater_than_front
  missing_volume_resets_confirmation: true
  hard_roll_anchor_preference:
    - last_trade_date
    - expiration_date
  calendar_source: root_contracts_daily_dates
  no_rollback: true
```

Add these values to `config/settings.base.yml` if an equivalent section does not already exist. The policy must be configurable, but the default config must match the above.

### 2.1 Root universe

Use the strategy roots in `config/instruments.yml` as the authoritative v1 universe.

Only process roots that exist in both:

- `config/instruments.yml`
- curated `contract_master` / `contracts_daily`

The roll engine should accept optional `--roots ES,NQ,...`, but the default should be all configured v1 roots.

### 2.2 Eligible contracts

For each root/date, eligible contracts are actual listed outright futures only.

A contract is eligible when:

- `contract_master.root == root`
- instrument class/security type indicates **outright future**, not spread, option, calendar spread, combo, or synthetic continuous symbol
- `first_trade_date` is null or `first_trade_date <= as_of_date`
- `expiration_date` or `last_trade_date` is after or equal to the current date
- the contract is not an already-rolled-away old contract for that root, unless a manual override explicitly says otherwise

Sort eligible contracts by:

1. `last_trade_date` when available, otherwise `expiration_date`
2. `expiration_date`
3. `raw_symbol`

This sorted order defines front, second, third, etc. The engine must never choose Databento continuous symbols as `lead_raw_symbol`; it must choose raw tradable contract symbols.

### 2.3 Trading calendar

For v1, do not add an external exchange calendar dependency unless the repo already uses one.

Use the observed root-level trading calendar:

```text
root_calendar[root] = sorted unique contracts_daily.trade_date for that root
```

Hard-roll business-day counts are counted over this root calendar.

If `last_trade_date` is not present in the calendar, anchor to the nearest prior root calendar date `<= last_trade_date`. If `last_trade_date` is missing, use `expiration_date` with the same fallback. If neither exists, emit a fatal QA failure for that contract.

### 2.4 Hard roll deadline

For the current lead contract:

```text
anchor_date = last_trade_date if available else expiration_date
hard_roll_deadline = N root trading sessions before anchor_date
```

`N` comes from `config/instruments.yml` for each root. The expected v1 policy is:

- financial futures: usually 5 trading sessions
- commodity futures: usually 10 trading sessions

Do not hard-code category-level defaults inside the algorithm except as a last-resort validation error message. The source of truth should be `config/instruments.yml`.

If there is no next eligible contract on or before the hard roll deadline, fail QA loudly. Do not silently keep an expired or near-expired contract.

### 2.5 Volume confirmation rule

At the start of `as_of_date = t`, the roll engine may only use volumes through the previous root trading date `t-1`.

For the current lead contract and its next eligible contract:

```text
condition(d) = volume[next, d] > volume[lead, d]
```

where `d` is a completed trading date. The comparison is valid only if both volumes are non-null and non-negative. Missing volume resets the confirmation count.

A volume roll occurs when this condition is true for `volume_confirmation_days = 3` consecutive completed root trading dates. If the third confirming date is `d3`, then:

```text
trigger_date   = d3
actual_effective_date = next root trading date after d3
roll_reason    = volume_3day
```

This prevents look-ahead. `lead_map.as_of_date = actual_effective_date` is the first row where the new contract is lead.

### 2.6 Hard roll precedence

Hard roll may be known in advance. It does not require volume confirmation.

If the hard roll deadline arrives before a pending volume roll effective date, hard roll wins and the effective date is the hard roll deadline.

Precedence:

1. manual override, if implemented and active
2. hard_roll
3. volume_3day
4. carry_forward

Manual override support may be a no-op in WP5 if the repo has no override loader yet, but do not block future support.

### 2.7 No rollback

Once a root rolls from contract A to contract B, contract A must never become lead again for that root under the same `roll_policy_version`, unless a manual override explicitly records that exception.

This is critical. Volume may later flip back; ignore it. Futures roll is monotonic through expiry order.

### 2.8 Initial lead at backtest start

For each root at the first requested date:

1. choose the earliest eligible contract by expiry order,
2. but if that contract is already at/past its hard roll deadline, skip to the next eligible contract,
3. repeat until a valid initial lead is found.

If no valid initial lead exists, fail QA for that root.

---

## 3. Required output tables

Use the WP3 schema names and column semantics. Do not rename columns.

### 3.1 `roll_events`

One row per roll transition.

Required columns from WP3:

- `roll_event_id`
- `root`
- `from_raw_symbol`
- `to_raw_symbol`
- `trigger_date`
- `effective_date`
- `roll_reason`
- `front_volume_tminus1`
- `next_volume_tminus1`
- `confirmation_count`
- `from_settle`
- `to_settle`
- `ratio_adjustment`
- `basis_at_roll`
- `builder_version`
- `override_id`

Definitions for WP5:

- For `volume_3day`, `trigger_date` is the third completed confirmation date.
- For `hard_roll`, `trigger_date` is the previous root trading date before `effective_date`, unless an implementation-specific earlier decision date is already available.
- `from_settle` and `to_settle` are taken from `contracts_daily.settle_price` on `trigger_date`. If settlement is missing, use close only if `settle_status == close_fallback`; otherwise leave null and set QA warning.
- `basis_at_roll = to_settle - from_settle`, when both are available.
- `ratio_adjustment = to_settle / from_settle`, when both are positive and available. This is a helper for WP6 backward ratio adjustment.
- `roll_event_id` must be deterministic. Suggested format:

```text
roll_<first_16_hex_sha256(policy_version|root|from|to|trigger_date|effective_date|roll_reason|snapshot_id)>
```

### 3.2 `lead_map`

One row per root and market date.

Required columns from WP3:

- `as_of_date`
- `root`
- `roll_policy_version`
- `lead_raw_symbol`
- `next_raw_symbol`
- `prev_lead_raw_symbol`
- `roll_flag`
- `roll_event_id`
- `days_to_expiry`
- `front_volume_tminus1`
- `next_volume_tminus1`
- `confirmation_count`
- `hard_roll_deadline`
- `selection_reason`
- `builder_version`
- `snapshot_id`

Definitions for WP5:

- `as_of_date` is the market/session date whose lead mapping is being selected.
- `front_volume_tminus1` and `next_volume_tminus1` are measured on the previous root trading date relative to `as_of_date`.
- `roll_flag = true` only on the first `as_of_date` where the lead contract changed.
- `selection_reason` is one of:
  - `carry_forward`
  - `volume_3day`
  - `hard_roll`
  - `manual_override`
- `days_to_expiry` should be calendar days from `as_of_date` to `last_trade_date` when available, otherwise `expiration_date`.
- `snapshot_id` must be inherited from the source data snapshot consumed by the build.

---

## 4. Required implementation shape

Prefer this structure, adapting to current repo conventions:

```text
src/cpdshadow/rolls.py
src/cpdshadow/ingest/roll_engine.py      # or existing CLI style
src/cpdshadow/storage/parquet.py         # only if needed and not already present
src/cpdshadow/storage/registry.py        # reuse WP4 if present
config/settings.base.yml                 # add roll section if missing
docs/roll_engine_v1.md
docs/decisions/0006-roll-engine-v1.md
tests/unit/test_roll_engine.py
tests/integration/test_roll_engine_io.py
```

### 4.1 Pure functions to implement

At minimum, implement pure functions equivalent to:

```python
build_root_calendar(contracts_daily: pd.DataFrame, root: str) -> list[date]

filter_outright_contracts(contract_master: pd.DataFrame, root: str) -> pd.DataFrame

sort_contracts_by_expiry(contracts: pd.DataFrame) -> pd.DataFrame

compute_hard_roll_deadline(
    contract_row: Mapping[str, Any],
    root_calendar: Sequence[date],
    hard_roll_days: int,
) -> date

select_initial_lead(
    as_of_date: date,
    sorted_contracts: pd.DataFrame,
    root_calendar: Sequence[date],
    hard_roll_days: int,
) -> str

next_contract_after(
    lead_raw_symbol: str,
    sorted_contracts: pd.DataFrame,
    as_of_date: date,
) -> str | None

build_lead_map_for_root(
    root: str,
    contract_master: pd.DataFrame,
    contracts_daily: pd.DataFrame,
    instrument_config: Mapping[str, Any],
    roll_config: Mapping[str, Any],
    snapshot_id: str,
) -> tuple[pd.DataFrame, pd.DataFrame]  # lead_map, roll_events

validate_lead_map(
    lead_map: pd.DataFrame,
    roll_events: pd.DataFrame,
    contract_master: pd.DataFrame,
    contracts_daily: pd.DataFrame,
) -> RollQaReport
```

Keep I/O separate from logic. Unit tests should call pure functions using synthetic DataFrames.

### 4.2 CLI commands

Follow existing WP4 CLI style if one exists. Otherwise create a module runnable with `python -m`.

Required commands or equivalent subcommands:

```bash
python -m cpdshadow.ingest.roll_engine build \
  --start 2018-01-01 \
  --end 2025-12-31 \
  --roots ES,NQ,ZN,CL \
  --data-dir data \
  --snapshot-id <snapshot_id> \
  --overwrite

python -m cpdshadow.ingest.roll_engine qa \
  --data-dir data \
  --snapshot-id <snapshot_id>
```

Add a Make target:

```make
wp5-roll-smoke-offline
```

This target must run only synthetic/offline tests and must not call Databento or IBKR.

---

## 5. QA and invariant checks

Implement QA as code, not just documentation.

### 5.1 Fatal QA failures

Fail the build or return non-zero CLI exit on:

1. No eligible initial lead for a configured root.
2. Lead contract goes past its hard roll deadline while a next eligible contract exists.
3. Roll sequence is non-monotonic by expiry order.
4. A raw symbol that was rolled away becomes lead again without manual override.
5. `lead_raw_symbol` is not present in `contract_master` for that root.
6. `next_raw_symbol == lead_raw_symbol`.
7. A `roll_event_id` referenced by `lead_map` is missing from `roll_events`.
8. `volume_3day` roll has `effective_date <= trigger_date`.
9. Duplicate primary key rows in `lead_map` or `roll_events`.
10. Any generated lead symbol looks like a continuous/synthetic symbol instead of a raw listed contract.

### 5.2 Warnings

Emit warnings but do not necessarily fail on:

1. Missing volume for lead or next on a comparison date.
2. Missing settlement used to compute `basis_at_roll` / `ratio_adjustment`.
3. Large basis at roll relative to price.
4. Very late volume roll, within two sessions of hard-roll deadline.
5. Large divergence from optional vendor continuous symbol comparison, if implemented.

### 5.3 QA report

Write a machine-readable and human-readable QA output, e.g.:

```text
artifacts/reports/wp5_roll_engine/roll_qa_<snapshot_id>.json
artifacts/reports/wp5_roll_engine/roll_qa_<snapshot_id>.md
```

Include at least:

- root coverage
- date coverage
- number of lead_map rows
- number of roll events by reason
- missing-volume counts
- missing-settlement counts at roll reference dates
- hard-roll deadline proximity statistics
- fatal error list
- warning list

---

## 6. Tests required

Create or update tests. Do not rely on live vendor data in normal test runs.

### 6.1 Unit tests

Synthetic tests must cover:

1. **Three-day volume confirmation**  
   If next volume exceeds front volume on D1, D2, D3, then the roll effective date is D4.

2. **No look-ahead**  
   `lead_map.as_of_date == D3` must still use the old lead. The first new lead row is D4.

3. **Missing volume resets confirmation**  
   True, true, missing, true, true, true rolls only after the final third valid confirmation.

4. **Hard roll wins**  
   If hard-roll deadline arrives before volume confirmation, roll reason is `hard_roll`.

5. **No rollback**  
   After rolling to next, later volume reversal must not return to the old contract.

6. **Initial lead skips stale contract**  
   If backtest starts after a front contract's hard-roll deadline, initial lead is the next contract.

7. **Monotonic expiry order**  
   Lead expiry rank never decreases.

8. **Deterministic IDs**  
   Same input produces the same `roll_event_id` and same output hashes.

9. **Ratio fields**  
   Given from/to settlements, `basis_at_roll` and `ratio_adjustment` are correct.

10. **Non-outright instruments filtered**  
    Synthetic spread/option rows must not become lead.

### 6.2 Integration tests

Use synthetic Parquet fixtures to test:

- read curated WP4-like `contract_master` and `contracts_daily`
- write `lead_map` and `roll_events`
- run QA command
- registry integration if available
- idempotent rerun with `--overwrite`

### 6.3 Existing tests

`make test` must pass. If existing tests fail due to stale assumptions, update tests in a minimal and well-documented way.

---

## 7. Storage and registry behavior

Use the WP3 physical layout:

```text
data/curated/lead_map/year=<YYYY>/part-*.parquet
data/curated/roll_events/year=<YYYY>/part-*.parquet
```

If the current repo uses a different partitioning helper from WP4, reuse it. Do not invent a second storage abstraction unnecessarily.

Generated output must be idempotent:

- same input + same config + same snapshot = same data rows
- content hashes should match across repeated runs
- rerun without `--overwrite` should refuse to clobber existing output
- rerun with `--overwrite` should replace only the requested snapshot/policy outputs

If registry utilities exist, register generated Parquet files into `data_file_registry` with:

- `logical_table = lead_map` or `roll_events`
- `source_schema = roll_engine_v1`
- `snapshot_id = source snapshot consumed`
- `content_sha256`
- `row_count`
- `min_trade_date` / `max_trade_date`
- `request_params_hash` or build params hash

---

## 8. Documentation updates required

Add:

1. `docs/roll_engine_v1.md`
2. `docs/decisions/0006-roll-engine-v1.md`

Update, if necessary:

1. `docs/spec_v1.md`
2. `README.md` with a short WP5 command snippet
3. `repo_tree.txt`, if the repo has been maintaining it manually

`docs/roll_engine_v1.md` must explain:

- policy version
- hard-roll rule
- 3-day volume rule
- no-lookahead timing
- no-rollback rule
- input/output tables
- QA report fields
- known limitations

Known limitations to state explicitly:

- v1 uses observed root-level dates as the trading calendar.
- v1 does not use open interest to trigger rolls.
- v1 does not use Databento continuous symbols as source of truth; those may be used later only as optional QA.
- v1 does not perform manual override application unless the repo already has an override loader.

---

## 9. Acceptance criteria

WP5 is complete when all of the following are true:

1. `lead_map` and `roll_events` can be built from WP4 curated data.
2. Roll selection is deterministic and idempotent.
3. Three-day volume rolls take effect on the next root trading date after confirmation.
4. Hard-roll deadlines cannot be missed silently.
5. Rolled-away contracts do not become lead again.
6. The engine chooses raw tradable contracts, not continuous symbols.
7. Synthetic unit tests cover all critical edge cases.
8. Offline integration smoke passes without Databento or IBKR credentials.
9. `make test` passes.
10. Documentation and decision record are updated.

---

## 10. Suggested implementation sequence for Codex

Use this order:

1. Inspect current WP4 storage/registry patterns.
2. Add roll config to `config/settings.base.yml` and config schema, if needed.
3. Implement pure roll logic in `src/cpdshadow/rolls.py`.
4. Add synthetic unit tests and make them pass.
5. Implement Parquet I/O and CLI wrapper.
6. Add integration tests with synthetic files.
7. Add QA report writer.
8. Add Make target.
9. Update docs.
10. Run full test suite.

Do not jump directly to real data before synthetic tests are green.

