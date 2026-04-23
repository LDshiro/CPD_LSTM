# Features / CPD v1 Specification

This document summarizes the WP7 feature contract.

## Inputs

`continuous_daily` rows from `series_id=v1_back_ratio_settle`.

## Outputs

- `cpd_daily`: long-form changepoint features by root/date/window.
- `features_daily`: wide model input features by root/date.

## Feature vector order

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

## Key conventions

- All features are computed independently per root.
- `as_of_date` uses only observations through that root's observed session date.
- `adj_settle_price` is the authoritative price.
- Daily returns are recomputed from adjusted prices.
- CPD ages are raw in `cpd_daily` and normalized in `features_daily`.
- A feature row is complete only when all model features plus `annualized_vol_60` are finite.
