# TSMOM Signal v1

Status: frozen for WP8 implementation

## Purpose

TSMOM v1 is the always-available fallback and benchmark strategy. It must stay
simple, deterministic, auditable, and no-lookahead.

## Input

TSMOM v1 consumes `features_daily` rows from `feature_set_id=features_v1`.

Required fields:

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
warmup_status == "ok"
ret_21, ret_63, ret_252 are finite
feature_hash is present
```

Invalid rows produce null signals and a stable invalid reason.

## Output

WP8 writes `signals_daily` rows:

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

## Constraints

- No partial horizons in v1
- No CPD gating in v1
- No root-specific weighting in v1
- No broker or execution logic in WP8
- `signals_daily` is reusable by both fallback and future CPD-LSTM model output
