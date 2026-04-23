# WP13 IBKR Paper Runbook v1

## Preconditions

- TWS or IB Gateway is running locally
- the configured account is a paper account
- `IBKR_HOST`, `IBKR_PORT`, `IBKR_CLIENT_ID`, and `IBKR_ACCOUNT` are set
- `CPDSHADOW_ENABLE_PAPER_SUBMIT=1` is set only when you intentionally want paper submission

## Shadow-only sync

```bash
python -m cpdshadow.cli broker-ibkr sync-state \
  --run-id shadow_ibkr_demo \
  --as-of 2026-04-23 \
  --mode shadow_only \
  --account-id $IBKR_ACCOUNT \
  --contract-master-path data/curated/contract_master
```

## Shadow-only intent preview

```bash
python -m cpdshadow.cli broker-ibkr submit-intents \
  --order-intents-path data/shadow/execution_boundary/run_id=shadow_run_001/order_intents.parquet \
  --contract-master-path data/curated/contract_master \
  --contracts-daily-path data/curated/contracts_daily \
  --mode shadow_only \
  --run-id shadow_run_001 \
  --as-of 2026-04-23 \
  --account-id $IBKR_ACCOUNT
```

## Guarded paper submit

```bash
export CPDSHADOW_ENABLE_PAPER_SUBMIT=1

python -m cpdshadow.cli broker-ibkr submit-intents \
  --order-intents-path data/shadow/execution_boundary/run_id=shadow_run_001/order_intents.parquet \
  --contract-master-path data/curated/contract_master \
  --contracts-daily-path data/curated/contracts_daily \
  --mode paper_submit \
  --run-id shadow_run_001 \
  --as-of 2026-04-23 \
  --account-id $IBKR_ACCOUNT
```

## QA

```bash
python -m cpdshadow.cli broker-ibkr qa \
  --workspace data/shadow/broker/ibkr/run_id=shadow_run_001
```

## What to inspect

- `broker_positions_snapshot.parquet`
- `broker_open_orders_snapshot.parquet`
- `order_intents.parquet`
- `translated_order_requests.json`
- `broker_callback_events.json`
- `reconciliation_report.json`
- `qa_report.json`

## Stop conditions

- unexpected account id
- missing or stale local reference prices in `paper_submit`
- any rejected translation caused by ambiguous contract mapping
- any `shadow_only` workspace that contains submitted/fill statuses
