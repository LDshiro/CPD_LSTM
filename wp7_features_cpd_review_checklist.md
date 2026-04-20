# WP7 Review Checklist — Features / CPD Builder v1

Use this after Codex implements WP7.

## 1. Scope control

- [ ] No Databento, IBKR, broker, or internet dependency in normal tests.
- [ ] No CPD-LSTM model training code added in WP7.
- [ ] No TSMOM signal generation added in WP7.
- [ ] Existing WP4/WP5/WP6 commands still work or are not regressed.

## 2. Schema compatibility

- [ ] `cpd_daily` contains all WP3 columns with correct names.
- [ ] `features_daily` contains all WP3 columns with correct names.
- [ ] `cpd_daily` primary key is unique: `feature_set_id, as_of_date, root, cpd_window_days`.
- [ ] `features_daily` primary key is unique: `feature_set_id, as_of_date, root`.
- [ ] `feature_set_id`, `series_id`, `builder_version`, and `snapshot_id` are populated.

## 3. No-lookahead controls

- [ ] Features at date `T` do not change when prices after `T` are modified.
- [ ] CPD at date `T` uses only observations through `T`.
- [ ] Build CLI loads warmup history before `--start` but does not emit pre-start rows unless explicitly requested.
- [ ] `available_at_utc` is not required for feature math, but the feature layer does not create later decision timestamps or execution dates.

## 4. Price and return handling

- [ ] `adj_settle_price` is the authoritative input price.
- [ ] Daily returns are recomputed from adjusted prices.
- [ ] Existing `continuous_daily.daily_return` is used only for validation warnings.
- [ ] Missing/nonpositive adjusted prices mark rows incomplete.
- [ ] No forward-fill is applied across missing or blocked sessions.

## 5. Normalized return features

- [ ] `ret_1`, `ret_21`, `ret_63`, `ret_126`, `ret_252` use the fixed formula.
- [ ] 60-day EWM daily volatility is used for normalization.
- [ ] Feature values are clipped to configured bounds.
- [ ] Early rows are classified as `warmup` and incomplete.

## 6. MACD features

- [ ] MACD uses log-price EMA differences.
- [ ] EMA calculations use `adjust=false`.
- [ ] MACD features are z-scored using trailing EWM std.
- [ ] MACD features are clipped to configured bounds.

## 7. CPD calculations

- [ ] CPD method is `two_sample_t_v1` for WP7.
- [ ] CPD windows are exactly 21 and 63.
- [ ] `cpd_score` is always in `[0,1]` when valid.
- [ ] `cpd_age_days` is raw integer age in `cpd_daily`.
- [ ] `cpd21_age` and `cpd63_age` are normalized ages in `features_daily`.
- [ ] Tie-breaking chooses the latest split.
- [ ] Synthetic stationary data produces low CPD scores.
- [ ] Synthetic step-change data produces higher CPD scores near the true split.

## 8. Completion and warmup status

- [ ] `is_complete=true` only when all required model features and `annualized_vol_60` are finite.
- [ ] `blocked_quality` outranks `missing_input`, `warmup`, and `ok`.
- [ ] `missing_input` is used for gaps or insufficient valid data after warmup.
- [ ] `warmup` is used for early rows with insufficient history.

## 9. Determinism and hashing

- [ ] Repeated builds on identical inputs produce identical logical rows.
- [ ] `feature_hash` is stable across repeated builds.
- [ ] `feature_hash` is based on the fixed ordered vector.
- [ ] Hashing handles null values deterministically.

## 10. CLI and QA

- [ ] `features build` command exists and works on synthetic fixture data.
- [ ] `features qa` command exists and writes JSON and Markdown reports.
- [ ] `make wp7-features-smoke-offline` exists.
- [ ] `make test` passes.
- [ ] QA report includes complete ratios, CPD valid ratios, warmup status counts, warnings, and fatal error counts.

## 11. Red flags

Stop and fix before merging if any are true:

- [ ] A test or build requires live vendor data.
- [ ] Future rows alter past features.
- [ ] CPD uses globally standardized returns fitted on the full sample.
- [ ] Feature builder silently fills missing prices.
- [ ] Feature builder silently treats invalid CPD as zero severity while marking row complete.
- [ ] Continuous symbols are used for broker-facing fields.
- [ ] `features_daily` is generated from `contracts_daily` instead of `continuous_daily`.
