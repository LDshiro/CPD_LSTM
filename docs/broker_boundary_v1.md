# Broker Boundary / Order Intent / Dry-Run Adapter v1

## Purpose

WP12 defines the execution boundary between upstream strategy outputs and future broker submission.
Its job is to turn `targets_daily` and current inventory into deterministic, auditable `order_intents`
 that a later broker adapter can consume safely.

WP12 is not a broker adapter. It is a broker-neutral planner and dry-run validator.

## Inputs

WP12 consumes:

- `targets_daily`
- `broker_positions_snapshot`
- `contract_master`
- optionally `monitoring_daily`

## Outputs

WP12 produces:

- `planned_order_intents`
- finalized offline `order_intents`
- `journal_events`
- QA reports
- dry-run reports

## Canonical concepts

### Target row

One root-level desired position after strategy selection, monitoring, sizing, and caps.

Important fields:

- `root`
- `lead_raw_symbol`
- `target_contracts`
- `control_action`

### Current inventory

Actual signed positions by contract from `broker_positions_snapshot`.

Roll logic cannot be inferred from root-level aggregate counts alone, so actual raw-symbol inventory is authoritative.

### Planned order intent

A deterministic instruction specifying:

- which contract
- which side
- which quantity
- why

with `status = planned`.

### Finalized offline order intent

The same logical row after dry-run validation:

- valid -> `status = not_sent`
- invalid -> `status = rejected`

## Control actions

### `run_cpd_lstm`

Use the target as-is.

### `fallback_tsmom`

Use the target as-is, but annotate non-roll lead deltas with `reason = fallback`.

### `hold`

Emit no intents. Inventory is unchanged.
If there is suppressed delta or off-lead inventory, emit warnings.

### `reduce_only`

Never increase absolute exposure and never flip sign through zero.

Effective desired target:

```text
if current_total == 0:
    desired_effective = 0
elif sign(target) != sign(current_total):
    desired_effective = 0
else:
    desired_effective = sign(current_total) * min(abs(target), abs(current_total))
```

Replacement roll of equal or smaller size is allowed.

## Intent reasons

Canonical reasons:

- `rebalance`
- `roll`
- `reduce_only`
- `flatten`
- `fallback`

## Status lifecycle

### Build phase

`planned`

### Dry-run finalize phase

- `not_sent`
- `rejected`

No `submitted`, `filled`, or `partial` statuses may be created in WP12.

## Examples

### Example 1: simple rebalance

Current:

- `ESU7 = +1`

Target:

- lead = `ESU7`
- target = `+3`
- action = `run_cpd_lstm`

Intent:

- buy 2 `ESU7`
- reason = `rebalance`

### Example 2: flatten

Current:

- `ESU7 = -2`

Target:

- lead = `ESU7`
- target = `0`
- action = `run_cpd_lstm`

Intent:

- buy 2 `ESU7`
- reason = `flatten`

### Example 3: roll same size

Current:

- `ESM7 = +2`

Target:

- lead = `ESU7`
- target = `+2`
- action = `run_cpd_lstm`

Intents:

1. sell 2 `ESM7` with `reason = roll`
2. buy 2 `ESU7` with `reason = roll`

### Example 4: reduce-only clipping

Current:

- `ESU7 = +3`

Target:

- lead = `ESU7`
- target = `+5`
- action = `reduce_only`

Effective target:

- `+3`

Intents:

- none

### Example 5: reduce-only replacement roll

Current:

- `ESM7 = +3`

Target:

- lead = `ESU7`
- target = `+5`
- action = `reduce_only`

Effective target:

- `+3`

Intents:

1. sell 3 `ESM7` with `reason = roll`
2. buy 3 `ESU7` with `reason = roll`

## Safety rules

- Mixed-sign inventory is a structural issue and must not be silently repaired.
- Unknown contracts are rejected or fail per config.
- `execution_date < as_of_date` is invalid.
- Contradictory same-symbol opposite-side intents are invalid.
- No fills are generated.

## Physical outputs

The default workspace is:

`data/shadow/execution_boundary/run_id=<run_id>/`

Typical files:

- `planned_order_intents.parquet`
- `planned_order_intents.json`
- `order_intents.parquet`
- `order_intents.json`
- `journal_events.parquet`
- `journal_events.json`
- `manifest.json`
- `build_summary.json`
- `dry_run_results.json`
- `qa_report.json`
- `dry_run_report.md`
