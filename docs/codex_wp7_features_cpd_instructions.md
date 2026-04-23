# WP7 Codex Instructions — Features / CPD Builder v1

This file is the canonical in-repo copy of the WP7 instruction. The root-level
`codex_wp7_features_cpd_instructions.md` remains as planning context, but the
implementation contract lives here for future maintenance.

## Goal

Consume WP6 `continuous_daily` and produce deterministic, no-lookahead:

- `cpd_daily`
- `features_daily`
- QA JSON/Markdown reports
- CLI commands
- offline synthetic tests

## Hard constraints

- No Databento, IBKR, broker, or network calls.
- `continuous_daily.adj_settle_price` is the authoritative feature price.
- Recompute returns from adjusted prices; use existing `daily_return` only for QA warnings.
- Store CPD outputs long-form in `cpd_daily`.
- Store model inputs wide-form in `features_daily`.
- CPD windows are fixed at `21` and `63`.
- WP7 default CPD method is `two_sample_t_v1`.
- `features_daily.cpd21_age` and `cpd63_age` are normalized to `[0, 1]`.
- `cpd_daily.cpd_age_days` remains raw integer days.
- Do not implement model training, TSMOM, portfolio targets, or broker logic.

## Physical storage

The repo implementation uses snapshot-partitioned feature storage for lineage
consistency with WP4-WP6:

```text
data/features/cpd_daily/feature_set_id=<feature_set_id>/snapshot_id=<snapshot_id>/year=<YYYY>/
data/features/features_daily/feature_set_id=<feature_set_id>/snapshot_id=<snapshot_id>/year=<YYYY>/
```

QA artifacts live under:

```text
artifacts/wp7/features_qa_<snapshot_id>_<feature_set_id>.json
artifacts/wp7/features_qa_<snapshot_id>_<feature_set_id>.md
```

## Feature vector order

```text
ret_1
ret_21
ret_63
ret_126
ret_252
macd_8_24
macd_16_48
macd_32_96
cpd21_score
cpd21_age
cpd63_score
cpd63_age
vol_20_60
vol_60_252
```

## Core calculations

- Usable source row:
  - `is_usable_for_signal == true`
  - `adj_settle_price` finite
  - `adj_settle_price > 0`
- Daily return:
  - `adj_settle_price_t / adj_settle_price_{t-1} - 1`
- Annualized volatility:
  - `sigma60_daily = EWM_STD(r, span=60, adjust=false, min_periods=20)`
  - `annualized_vol_60 = sigma60_daily * sqrt(252)`
- Normalized return horizon `h`:
  - `ret_h = ((P_t / P_{t-h}) - 1) / (sigma60_daily_t * sqrt(h) + epsilon)`
- MACD pair `(fast, slow)`:
  - `log_price = log(adj_settle_price)`
  - `macd_raw = EMA_fast(log_price) - EMA_slow(log_price)`
  - `macd_feature = macd_raw / (EWM_STD(macd_raw, span=252, min_periods=63) + epsilon)`
- CPD input:
  - `z_t = daily_return / (sigma60_daily + epsilon)`
  - clip `z_t` to `[-10, 10]`

## CPD backend

For each root, as-of date, and lookback window `L in {21, 63}`:

1. Use the last `L` contiguous `z_t` rows ending at `t`.
2. If any of those rows are non-finite, mark `cpd_is_valid=false`.
3. Compute the Welch-style two-sample statistic across all valid splits.
4. Pick the maximum `t_stat`; on ties choose the latest split.
5. Store:
   - `cpd_score = clip(1 - exp(-0.5 * t_stat^2), 0, 1)`
   - `cpd_location_index = k_star`
   - `cpd_age_days = L - k_star`

## CLI

```bash
python -m cpdshadow.cli features build \
  --start 2024-01-02 \
  --end 2024-03-29 \
  --snapshot-id <snapshot_id> \
  --series-id v1_back_ratio_settle \
  --feature-set-id features_v1 \
  --roots ES,NQ
```

```bash
python -m cpdshadow.cli features qa \
  --snapshot-id <snapshot_id> \
  --feature-set-id features_v1
```

## Acceptance

- `make test` passes without network.
- `make wp7-features-smoke-offline` passes without network.
- Builds are deterministic across repeated runs.
- No-lookahead tests pass.
- QA JSON and Markdown reports are generated.
