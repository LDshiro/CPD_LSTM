# WP5 Roll Engine Review Checklist

Use this after Codex implements WP5.

## Functional checks

- [ ] `lead_map` has one row per configured root and root trading date.
- [ ] `roll_events` has one row per actual roll transition.
- [ ] `lead_raw_symbol` values are raw tradable listed contracts, not continuous symbols.
- [ ] Volume confirmation uses only completed prior trading dates.
- [ ] Third confirmation on D3 rolls effective on D4, not D3.
- [ ] Hard-roll deadline is never missed silently.
- [ ] Rolled-away contracts never reappear as lead without override.
- [ ] Initial lead skips contracts already past hard-roll deadline.
- [ ] Missing volume resets confirmation count.
- [ ] `ratio_adjustment = to_settle / from_settle` when both are positive and available.

## Schema checks

- [ ] `lead_map` columns match `docs/data_schema_v1.md`.
- [ ] `roll_events` columns match `docs/data_schema_v1.md`.
- [ ] Primary keys are unique.
- [ ] `snapshot_id`, `builder_version`, and `roll_policy_version` are populated.
- [ ] Generated files are registered if the repo has registry utilities.

## Test checks

- [ ] Unit tests cover volume roll, hard roll, no-lookahead, missing volume, no-rollback, initial stale contract, deterministic IDs, ratio fields, and non-outright filtering.
- [ ] Integration tests use synthetic Parquet, not vendor calls.
- [ ] `make wp5-roll-smoke-offline` passes.
- [ ] `make test` passes.

## Documentation checks

- [ ] `docs/roll_engine_v1.md` exists.
- [ ] `docs/decisions/0006-roll-engine-v1.md` exists.
- [ ] README or docs mention the WP5 build/QA command.
- [ ] Known limitations are explicit.

## Red flags

- [ ] Same-day full-volume data is used to choose the same day's lead contract.
- [ ] Databento continuous symbols are used as source of truth for tradable lead.
- [ ] Roll logic is embedded inside CLI code without pure unit-testable functions.
- [ ] Roll policy parameters are hard-coded in multiple places.
- [ ] A vendor/API call is required for normal tests.
