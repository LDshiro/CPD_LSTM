# TSMOM Signal v1

Status: **frozen for WP8 implementation**

This document defines the v1 fallback signal for the CPD-LSTM Shadow Trading System.

## Purpose

TSMOM v1 is the always-available fallback and benchmark strategy. It must remain simple, deterministic, no-lookahead, and easy to audit. It is not intended to be the final alpha model.

## Input

TSMOM v1 consumes `features_daily` rows from `feature_set_id=features_v1`.

Required columns:

```text
as_of_date
root
ret_21
ret_63
ret_252
is_complete
warmup_status
feature_hash
snapshot_id
```

## Formula

```text
signal_raw = (sign(ret_21) + sign(ret_63) + sign(ret_252)) / 3
signal_clipped = clamp(signal_raw, -1, 1)
```

`sign(0)` is defined as `0`.

## Validity

A row is valid only if:

```text
is_complete == true
warmup_status == 'ok'
ret_21, ret_63, ret_252 are finite
feature_hash is present
```

Invalid rows produce null signals and a stable invalid reason.

## Output

WP8 writes WP3-compatible `signals_daily` rows:

```text
run_id
strategy_id = tsmom
model_id = tsmom_v1
as_of_date
root
signal_raw
signal_clipped
is_valid
invalid_reason
feature_hash
created_at_utc
```

## Design constraints

- No partial horizon in v1.
- No CPD gating in v1.
- No root-specific weights in v1.
- No use of raw contracts or broker state.
- No live/paper order generation in WP8.

## Relationship to later work

WP9 CPD-LSTM must use the same `signals_daily` contract. WP14 daily shadow batch will select between `cpd_lstm` and `tsmom` using the monitoring action from Step 15.
