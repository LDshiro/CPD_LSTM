# Broker IBKR v1

## Purpose

WP13 adds the first real broker adapter boundary on top of WP12 broker-neutral `order_intents`.

It supports:
- IBKR state sync through TWS API / IB Gateway
- `shadow_only` preview mode
- guarded `paper_submit`
- deterministic callback reconciliation

It does not support:
- live submission
- Client Portal Web API trading
- combo, algo, or bracket orders
- market-data-driven pricing

## Supported modes

- `shadow_only`
- `paper_submit`

`shadow_only` may read broker state but never calls `placeOrder`.
`paper_submit` requires `CPDSHADOW_ENABLE_PAPER_SUBMIT=1`, an allowlisted account, and a paper-account prefix match.

## Inputs

- WP12 `order_intents`
- `contract_master`
- `contracts_daily`
- optional `monitoring_daily`

## Outputs

- enriched `order_intents`
- `broker_positions_snapshot`
- `broker_open_orders_snapshot`
- `journal_events`
- `account_summary_snapshot.json`
- `translated_order_requests.json`
- `broker_callback_events.json`
- QA/report artifacts under `data/shadow/broker/ibkr/run_id=<run_id>/`

## Routing and translation

Route only from listed futures metadata:
- `root`
- `raw_symbol`
- `contract_master`
- `config/instruments.yml`

Never route from `continuous_daily`.

Supported order family in WP13:
- futures only
- `LMT`
- `DAY`

## Paper price policy

Reference hierarchy:
1. latest `contracts_daily.settle_price`
2. else latest `contracts_daily.close_price`
3. else reject

Then:
- buy: `reference * (1 + buffer_bps / 10000)`
- sell: `reference * (1 - buffer_bps / 10000)`
- round to tick size

## Safety rails

- no live submit mode
- allowlist + paper-account prefix guard
- `hold` blocks broker translation/submission
- `reduce_only` must not increase absolute exposure or flip sign
- ambiguous contract mapping rejects safely
- stale or missing reference prices reject paper submission
