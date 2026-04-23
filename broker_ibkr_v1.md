# Broker IBKR v1 — WP13 Summary

## Purpose

WP13 introduces the first **real broker-facing boundary** for the project.

It does **not** enable live trading. It adds:

- broker state sync from IBKR TWS API / IB Gateway
- paper-safe order translation and submission
- shadow-only preview mode
- normalized callback reconciliation

## Supported modes

- `shadow_only`
- `paper_submit`

No live mode is supported in v1.

## Input / output

### Inputs
- `targets_daily`
- `order_intents`
- `broker_positions_snapshot` (previous or live sync)
- `contract_master`
- `monitoring_daily`

### Outputs
- enriched `order_intents`
- `broker_positions_snapshot`
- `broker_open_orders_snapshot`
- `journal_events`
- broker reconciliation artifacts

## Contract routing

Route only from:
- root
- lead raw symbol
- contract metadata

Never route from `continuous_daily`.

## Order model

Supported in v1:
- futures
- `LMT`
- `DAY`

Not supported in v1:
- market orders
- algos
- brackets
- combos
- live trading

## Safety model

- account allowlist required
- explicit paper guard required
- `hold` -> no submission
- `reduce_only` -> only reduce, never increase / flip
- missing contract mapping -> reject
- missing paper reference price -> reject

## Paper price policy

Reference hierarchy:
1. latest settlement from `contracts_daily`
2. else latest daily close
3. else reject

Buffer:
- buy: +buffer_bps
- sell: -buffer_bps

Then round to tick size.

## Broker state

Read:
- positions
- open orders
- account summary subset

Persist normalized snapshots for audit and later orchestration.

## Why TWS API instead of Web API

This project uses the TWS / IB Gateway API boundary because it is the native socket trading API used for order placement and event callbacks. The Client Portal Web API remains out of scope for trading integration in WP13.
