# Broker Boundary / Order Intent / Dry-Run Adapter v1

## Purpose

WP12 defines the **execution boundary** between:

- upstream research + signal + target generation
- downstream broker integration

Its job is to turn `targets_daily` and current inventory into deterministic **order intents** that are auditable, testable, and safe to pass into a future broker adapter.

This is the first point in the stack where the system starts thinking in terms of **actual contracts** rather than abstract signals.

---

## What WP12 does

WP12 consumes:

- `targets_daily`
- `broker_positions_snapshot`
- `contract_master`
- optionally `monitoring_daily`

WP12 produces:

- `planned_order_intents`
- finalized offline `order_intents`
- `journal_events`
- QA reports
- dry-run reports

WP12 is **not** a broker adapter.  
It is a **broker boundary** and **dry-run validator**.

---

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

Important because roll logic cannot be inferred from root-level aggregate counts alone.

### Planned order intent

A deterministic instruction saying:

- which contract
- which side
- which quantity
- why

with `status = planned`.

### Finalized offline order intent

Same schema, but after dry-run validation:

- valid → `status = not_sent`
- invalid → `status = rejected`

---

## Why current raw-symbol inventory matters

A root-level target alone is not enough.

Example:

- current snapshot: `ESM7 = +2`
- target: `lead_raw_symbol = ESU7`, `target_contracts = +2`

Root-level aggregate delta is zero, but execution still requires:

1. sell 2 `ESM7`
2. buy 2 `ESU7`

This is why WP12 must use `broker_positions_snapshot` and not rely only on `targets_daily.current_contracts`.

---

## Control actions

## `run_cpd_lstm`

Use the target as-is.

## `fallback_tsmom`

Use the target as-is, but annotate non-roll deltas with `reason = fallback`.

## `hold`

Emit no intents.  
Inventory is unchanged.  
If there is suppressed delta or off-lead inventory, emit warnings.

## `reduce_only`

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

---

## Intent reasons

The canonical reasons are:

- `rebalance`
- `roll`
- `reduce_only`
- `flatten`
- `fallback`

### Meaning

- `roll`: move exposure from old contract to lead contract
- `flatten`: reduce target to zero
- `rebalance`: regular position change on lead contract
- `fallback`: lead-contract change driven under TSMOM fallback control action
- `reduce_only`: intentional exposure reduction under monitoring restriction

---

## Status lifecycle in WP12

### Build phase

`planned`

### Dry-run finalize phase

- `not_sent`
- `rejected`

No `submitted`, `filled`, or `partial` statuses may be created in WP12.

---

## Canonical planning examples

## Example 1 — simple rebalance

Current:

- `ESU7 = +1`

Target:

- lead = `ESU7`
- target = `+3`
- action = `run_cpd_lstm`

Intent:

- buy 2 `ESU7`
- reason = `rebalance`

## Example 2 — flatten

Current:

- `ESU7 = -2`

Target:

- lead = `ESU7`
- target = `0`
- action = `run_cpd_lstm`

Intent:

- buy 2 `ESU7`
- reason = `flatten`

## Example 3 — roll same size

Current:

- `ESM7 = +2`

Target:

- lead = `ESU7`
- target = `+2`
- action = `run_cpd_lstm`

Intents:

1. sell 2 `ESM7` (`roll`)
2. buy 2 `ESU7` (`roll`)

## Example 4 — reduce-only clipping

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

Warning:

- target clipped under reduce-only

## Example 5 — reduce-only replacement roll

Current:

- `ESM7 = +3`

Target:

- lead = `ESU7`
- target = `+5`
- action = `reduce_only`

Effective target:

- `+3`

Intents:

1. sell 3 `ESM7` (`roll`)
2. buy 3 `ESU7` (`roll`)

Exposure is not increased.

---

## Mixed-sign inventory rule

If a root contains both long and short positions across raw symbols, v1 treats that as unsupported inventory.

Example:

- `ESM7 = +1`
- `ESU7 = -1`

WP12 must not silently net this and continue.

Expected behavior:

- emit critical journal event
- produce no intents for that root
- fail QA for that root

---

## Output contract

## Intermediate

`planned_order_intents`

## Canonical final

`order_intents`

The canonical final dataset after dry-run should be safe to hand to later layers for:

- review
- reporting
- future broker submission plumbing

but not for actual execution yet.

---

## Future boundary

WP13 will add:

- broker contract resolution
- account selection
- price enrichment for marketable-limit logic
- real adapter plumbing to TWS / IB Gateway
- submission state transitions

WP12 deliberately stops before that line.
