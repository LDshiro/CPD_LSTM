# Roll Engine v1

- Policy version: `volume3_hardroll_v1`
- Builder version: `roll_engine_v1`

## Purpose

WP5 builds deterministic roll outputs from WP4 curated `contract_master` and `contracts_daily`.
The outputs are:

- `data/curated/lead_map/snapshot_id=<snapshot_id>/year=<YYYY>/...`
- `data/curated/roll_events/snapshot_id=<snapshot_id>/year=<YYYY>/...`
- `artifacts/reports/wp5_roll_engine/roll_qa_<snapshot_id>.json`
- `artifacts/reports/wp5_roll_engine/roll_qa_<snapshot_id>.md`

`lead_map` maps each `(root, as_of_date)` to the actual listed outright futures contract that v1 should trade.
`roll_events` records each contract transition and the reference values needed by WP6 ratio adjustment.

## Policy

### Hard roll

- The lead contract is always a raw tradable listed contract, never a continuous symbol.
- Hard-roll days come from `config/instruments.yml` per root.
- The anchor uses `last_trade_date` when available, otherwise `expiration_date`.
- The effective hard-roll deadline is computed on the observed root trading calendar from `contracts_daily`.
- If the deadline is reached, the engine rolls on that deadline date.

### Three-day volume rule

- The comparison uses only completed root trading dates.
- At `as_of_date = t`, the engine may only look at volume through the previous root trading date.
- A volume roll is confirmed when `next volume > lead volume` for three consecutive completed root dates.
- If the third confirming date is `D3`, the new lead starts on the next root trading date `D4`.
- Missing volume resets the confirmation count.

### No look-ahead and no rollback

- `lead_map` does not switch on the third confirming day itself.
- Once a contract has been rolled away, it cannot become lead again under the same policy unless a future manual override layer explicitly records that exception.

## Inputs and outputs

### Inputs

- `contract_master`
- `contracts_daily`
- `config/instruments.yml`
- `config/settings.base.yml`

### Outputs

`lead_map` columns:

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

`roll_events` columns:

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

## QA report

The QA report records at least:

- root coverage
- date coverage
- number of `lead_map` rows
- number of roll events by reason
- missing-volume counts
- missing-settlement counts at roll reference dates
- hard-roll proximity statistics
- fatal errors
- warnings

Fatal checks include:

- no eligible initial lead
- lead passing hard-roll deadline while a next eligible contract exists
- non-monotonic expiry order
- rollback to an old contract
- missing lead contract in `contract_master`
- `next_raw_symbol == lead_raw_symbol`
- missing `roll_event_id` referenced by `lead_map`
- `volume_3day` with `effective_date <= trigger_date`
- duplicate primary keys
- continuous or synthetic lead symbol selection

## Known limitations

- v1 uses observed root-level dates from `contracts_daily` as the trading calendar.
- v1 does not use open interest to trigger rolls.
- v1 does not use Databento continuous symbols as source of truth.
- v1 does not apply manual overrides because no override loader exists yet.
- If a contract lacks a first-notice field in the curated schema, commodities fall back to `last_trade_date` then `expiration_date`.
