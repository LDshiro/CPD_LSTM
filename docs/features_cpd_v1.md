# Features / CPD v1

WP7 defines the deterministic feature layer that sits on top of WP6
`continuous_daily`.

## Scope

- Input: `continuous_daily` from `series_id=v1_back_ratio_settle`
- Outputs:
  - `cpd_daily`
  - `features_daily`
- Usage:
  - signal/research only
  - never broker-facing

## Authoritative price

`adj_settle_price` is the only authoritative price for WP7 feature math.
`continuous_daily.daily_return` may be compared for QA, but feature values are
always recomputed from adjusted prices.

## Feature vector

The ordered model vector is fixed:

1. `ret_1`
2. `ret_21`
3. `ret_63`
4. `ret_126`
5. `ret_252`
6. `macd_8_24`
7. `macd_16_48`
8. `macd_32_96`
9. `cpd21_score`
10. `cpd21_age`
11. `cpd63_score`
12. `cpd63_age`
13. `vol_20_60`
14. `vol_60_252`

`feature_hash` is computed from this exact ordered vector plus
`feature_set_id`, `series_id`, `root`, and `as_of_date`.

## CPD

WP7 uses a deterministic placeholder backend:

- method: `two_sample_t_v1`
- windows: `21`, `63`
- outputs:
  - severity: `cpd_score`
  - location: `cpd_location_index`
  - age:
    - raw integer in `cpd_daily.cpd_age_days`
    - normalized to `[0, 1]` in `features_daily.cpd21_age` and `cpd63_age`

## Completeness and warmup

`features_daily.is_complete=true` only when:

- the source row is usable
- all 14 model features are finite
- `annualized_vol_60` is finite
- `warmup_status == ok`

Allowed `warmup_status` values:

- `ok`
- `warmup`
- `missing_input`
- `blocked_quality`

Priority order:

```text
blocked_quality > missing_input > warmup > ok
```

## Physical storage

Feature outputs are snapshot-partitioned for reproducible lineage:

```text
data/features/cpd_daily/feature_set_id=<feature_set_id>/snapshot_id=<snapshot_id>/year=<YYYY>/
data/features/features_daily/feature_set_id=<feature_set_id>/snapshot_id=<snapshot_id>/year=<YYYY>/
```

## QA

WP7 writes both JSON and Markdown QA reports under `artifacts/wp7/`.
The QA surface focuses on:

- schema completeness
- primary-key uniqueness
- no-lookahead consistency
- return validation mismatches
- CPD validity ratios
- feature completeness ratios
- clipping saturation
