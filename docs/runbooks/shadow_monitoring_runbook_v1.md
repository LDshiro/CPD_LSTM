# Shadow Monitoring Runbook v1.0

## Daily operator flow

1. Confirm data ingest completed.
2. Generate monitoring snapshots.
3. Evaluate the monitoring decision.
4. Apply the final action.
5. Store the decision and alerts with the day stamp.
6. Review unresolved critical alerts before the next session.

## Action handling

### `run_cpd_lstm`

- Use CPD-LSTM target contracts.
- Record warnings but do not gate the rebalance.

### `fallback_tsmom`

- Do not use champion signals for the day.
- Build target contracts with TSMOM on the same universe and risk layer.
- Flag the day for model-health review.

### `hold`

- Skip the rebalance.
- Keep current positions unchanged.
- Open an incident if the root cause is not resolved within the same day.

### `reduce_only`

- Allow only trades that lower gross risk, margin usage, or mismatch.
- Disable new builds even if the target layer asks for them.
- Require explicit incident closure before resuming normal mode.

## Weekly checks

- Review alert frequency by category.
- Review CPD vs TSMOM short-horizon performance.
- Review slippage ratio and fill ratio versus modeled baseline.

## Monthly checks

- Recalibrate warning thresholds if paper metrics are structurally different.
- Reconcile monitoring actions with realized incidents.
- Review whether any threshold changes need a decision note.
