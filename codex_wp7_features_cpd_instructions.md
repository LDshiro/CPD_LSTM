# WP7 Codex Instructions — Features / CPD Builder v1

Status: **ready for local Codex implementation**  
Goal: implement the deterministic feature and CPD generation layer that consumes WP6 `continuous_daily` and produces WP3-compatible `cpd_daily` and `features_daily` datasets.

This work package is deliberately strict. The CPD-LSTM model will only be useful if features are reproducible, traceable, no-lookahead, and stable under rebuilds. Do not optimize for model performance in WP7. Optimize for correctness, determinism, schema compatibility, and debuggability.

---

## 0. Branch and scope

Create a new branch from the latest local repository state:

```bash
git checkout -b wp7-features-cpd
```

### In scope

Implement:

1. Feature configuration under `config/settings.base.yml`.
2. A CPD backend interface plus the v1 deterministic backend.
3. Feature calculations from `continuous_daily`.
4. `cpd_daily` long-form output.
5. `features_daily` wide-form output.
6. QA report generation.
7. CLI entry points.
8. Offline synthetic tests.
9. Makefile smoke target.

### Out of scope

Do not implement:

- CPD-LSTM model training.
- TSMOM fallback signal generation.
- Walk-forward evaluation.
- IBKR / broker logic.
- Any vendor/API calls.
- Order, target, or portfolio construction.
- Paper/live execution.

WP7 must be fully testable offline with synthetic data.

---

## 1. Inputs and outputs

### Required input

WP7 consumes `continuous_daily`, produced by WP6.

Expected logical columns:

```text
series_id
as_of_date
root
lead_raw_symbol
raw_settle_price
adj_settle_price
adj_factor
daily_return
settle_status
roll_flag
roll_event_id
is_usable_for_signal
quality_flags
builder_version
snapshot_id
```

Use only rows where:

```text
series_id == configured series_id
snapshot_id == requested snapshot_id, unless explicitly running in non-snapshot fixture mode
root in requested roots
as_of_date between start and end inclusive, after warmup extension has been loaded
```

### Outputs

Write partitioned Parquet datasets:

```text
data/features/cpd_daily/feature_set_id=<feature_set_id>/year=<YYYY>/part-*.parquet
data/features/features_daily/feature_set_id=<feature_set_id>/year=<YYYY>/part-*.parquet
```

Also write QA artifacts:

```text
artifacts/wp7/features_qa_<snapshot_id>_<feature_set_id>.json
artifacts/wp7/features_qa_<snapshot_id>_<feature_set_id>.md
```

The CLI must also support writing to an alternate `--output-dir` for tests.

---

## 2. Configuration

Extend `config/settings.base.yml` with a `features` section. Keep names stable; later WPs will rely on these keys.

```yaml
features:
  feature_set_id: features_v1
  builder_version: features_builder_v1
  series_id: v1_back_ratio_settle
  price_column: adj_settle_price
  return_column: daily_return
  annualization_factor: 252
  warmup_days: 252
  epsilon: 1.0e-12

  horizons:
    normalized_returns: [1, 21, 63, 126, 252]

  volatility:
    estimator: ewm_std
    span_days: 60
    min_periods: 20
    ratio_pairs:
      - [20, 60]
      - [60, 252]

  macd:
    method: log_price_ema_diff_zscore
    pairs:
      - [8, 24]
      - [16, 48]
      - [32, 96]
    zscore_span_days: 252
    zscore_min_periods: 63

  cpd:
    builder_version: cpd_builder_v1
    method: two_sample_t_v1
    windows: [21, 63]
    min_segment_days: 5
    min_segment_fraction: 0.25
    input_return: vol_scaled_daily_return
    score_transform: one_minus_exp_half_t2

  clipping:
    normalized_return_abs_max: 20.0
    macd_abs_max: 20.0
    vol_ratio_min: 0.05
    vol_ratio_max: 20.0
    cpd_score_min: 0.0
    cpd_score_max: 1.0
```

Add schema validation if the project already has settings models. Invalid configs must fail early.

---

## 3. Feature set definition

The v1 wide feature vector order is fixed:

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

`feature_hash` must be calculated from this exact ordered vector plus `feature_set_id`, `series_id`, `root`, and `as_of_date`. The hash must be stable across rebuilds.

Recommended payload before hashing:

```json
{
  "feature_set_id": "features_v1",
  "series_id": "v1_back_ratio_settle",
  "root": "ES",
  "as_of_date": "2024-01-10",
  "features": [ ... ordered numeric values or null ... ]
}
```

Use a stable JSON serializer, sorted keys, fixed date format, and no platform-dependent float formatting beyond normal JSON numeric output.

---

## 4. Core calculations

All calculations are per-root, sorted by `as_of_date`. The root trading calendar is simply the observed `continuous_daily` date sequence for that root. Do not assume a global calendar.

### 4.1 Usable row policy

A row is usable if all are true:

```text
is_usable_for_signal == true
adj_settle_price is finite
adj_settle_price > 0
```

If a row is not usable, downstream feature values for that as-of date should be null and `warmup_status` should be `blocked_quality`, unless the reason is insufficient history, in which case use `warmup`.

Do not forward-fill adjusted prices across missing or blocked sessions in WP7.

### 4.2 Daily return

Prefer `continuous_daily.daily_return` if present and finite. Validate it against the adjusted price implied return:

```text
price_return_t = adj_settle_price_t / adj_settle_price_{t-1} - 1
```

If both exist and the absolute difference exceeds `1e-8`, add a QA warning. For actual feature calculation, recompute from `adj_settle_price` so the feature layer is self-contained and auditable.

First valid row per root has null daily return.

### 4.3 Volatility estimator

Define simple daily returns as `r_t`.

For annualized 60-day volatility:

```text
sigma60_daily_t = EWM_STD(r, span=60, adjust=false, min_periods=20)_t
annualized_vol_60_t = sigma60_daily_t * sqrt(252)
```

Use pandas-compatible `ewm(...).std(bias=False)` unless the project has an existing helper. Be explicit in the code and tests.

For vol ratios:

```text
vol_20_60_t  = sigma20_daily_t / sigma60_daily_t
vol_60_252_t = sigma60_daily_t / sigma252_daily_t
```

Then clip:

```text
vol_ratio_min <= vol_ratio <= vol_ratio_max
```

If denominator is missing or below epsilon, output null.

### 4.4 Normalized returns

For horizon `h`:

```text
raw_ret_h_t = adj_settle_price_t / adj_settle_price_{t-h} - 1
ret_h_t = raw_ret_h_t / (sigma60_daily_t * sqrt(h) + epsilon)
```

Then clip to:

```text
[-normalized_return_abs_max, +normalized_return_abs_max]
```

For `ret_1`, this is the daily return normalized by one-day sigma.

A horizon is invalid if any price needed for `t-h` or `t` is missing/nonpositive, or if `sigma60_daily_t` is missing.

### 4.5 MACD features

For each pair `(fast, slow)`:

```text
log_price_t = log(adj_settle_price_t)
macd_raw_t = EMA_fast(log_price)_t - EMA_slow(log_price)_t
macd_feature_t = macd_raw_t / (EWM_STD(macd_raw, span=252, min_periods=63)_t + epsilon)
```

Then clip to:

```text
[-macd_abs_max, +macd_abs_max]
```

Use `adjust=false` for all EMAs. If price is not usable, output null.

### 4.6 CPD input series

CPD uses volatility-scaled daily returns:

```text
z_t = r_t / (sigma60_daily_t + epsilon)
```

Clip `z_t` to `[-10, +10]` before CPD to prevent one bad settlement from dominating an entire window. This clipping is for CPD only; do not overwrite `daily_return`.

### 4.7 CPD backend v1: `two_sample_t_v1`

This is a deterministic, lightweight CPD proxy with the same output shape as the later CPD-LSTM model expects: location and severity. It is intentionally pluggable. A later paper-compatible GP/OCPD backend may replace it without changing `cpd_daily` or `features_daily`.

For each root, date `t`, and lookback window `L in {21, 63}`:

1. Take the last `L` valid `z_t` observations ending at `t`.
2. If fewer than `L` observations exist or any are non-finite, set `cpd_is_valid=false`.
3. Define:

```text
min_seg = max(min_segment_days, floor(min_segment_fraction * L))
```

4. For every split `k` satisfying:

```text
min_seg <= k <= L - min_seg
```

split the window into `z[0:k]` and `z[k:L]`.

5. Compute Welch-style two-sample statistic:

```text
mu1 = mean(z[0:k])
mu2 = mean(z[k:L])
var1 = variance(z[0:k], ddof=1)
var2 = variance(z[k:L], ddof=1)
t_stat_k = abs(mu2 - mu1) / sqrt(var1/k + var2/(L-k) + epsilon)
```

6. Choose `k_star` with maximum `t_stat_k`. On ties, choose the latest split, i.e. the largest `k`, so the model treats recent changepoints as more relevant.

7. Store:

```text
cpd_score = clip(1 - exp(-0.5 * t_stat_k_star^2), 0, 1)
cpd_location_index = k_star
cpd_age_days = L - k_star
cpd_is_valid = true
cpd_method = two_sample_t_v1
```

The age convention is important:

```text
cpd_age_days == 0 means the split is at the end of the window, but this should not happen because min_seg prevents it.
Lower age means more recent estimated change.
Higher age means older estimated change.
```

In `features_daily`, store normalized ages:

```text
cpd21_age = cpd_age_days / 21
cpd63_age = cpd_age_days / 63
```

Raw integer ages remain in `cpd_daily`.

---

## 5. Output schemas

### 5.1 `cpd_daily`

Must match WP3 schema:

```text
feature_set_id
as_of_date
root
cpd_window_days
cpd_score
cpd_age_days
cpd_location_index
cpd_is_valid
cpd_method
builder_version
snapshot_id
```

Primary key:

```text
feature_set_id, as_of_date, root, cpd_window_days
```

Allowed `cpd_window_days`: `21`, `63`.

Allowed `cpd_method`: `two_sample_t_v1` for WP7.

### 5.2 `features_daily`

Must match WP3 schema:

```text
feature_set_id
as_of_date
root
series_id
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
annualized_vol_60
is_complete
warmup_status
feature_hash
builder_version
snapshot_id
```

Primary key:

```text
feature_set_id, as_of_date, root
```

`is_complete=true` only if all 14 model features plus `annualized_vol_60` are finite and the source row is usable.

Allowed `warmup_status`:

```text
ok
warmup
missing_input
blocked_quality
```

Priority if multiple apply:

```text
blocked_quality > missing_input > warmup > ok
```

---

## 6. CLI design

Add commands under the existing `cpdshadow.cli` structure. Names may be adapted to the existing CLI style, but behavior should match this interface.

### Build

```bash
python -m cpdshadow.cli features build \
  --start 2024-01-02 \
  --end 2024-03-29 \
  --snapshot-id <snapshot_id> \
  --series-id v1_back_ratio_settle \
  --feature-set-id features_v1 \
  --roots ES,NQ \
  --input-dir data/curated/continuous_daily \
  --output-dir data/features \
  --artifact-dir artifacts/wp7
```

Build must load enough pre-start warmup history. If `--start 2024-01-02` and `warmup_days=252`, load at least 252 observed sessions before start when available.

### QA

```bash
python -m cpdshadow.cli features qa \
  --snapshot-id <snapshot_id> \
  --feature-set-id features_v1 \
  --input-dir data/features \
  --artifact-dir artifacts/wp7
```

QA should produce JSON and Markdown reports.

### Optional inspect command

If easy, add:

```bash
python -m cpdshadow.cli features inspect \
  --feature-set-id features_v1 \
  --root ES \
  --as-of-date 2024-03-29
```

This is optional and should not delay WP7.

---

## 7. Suggested implementation files

Add or update:

```text
src/cpdshadow/features.py
src/cpdshadow/cpd.py
src/cpdshadow/features_io.py        # optional if storage helpers are cleaner this way
src/cpdshadow/cli.py
config/settings.base.yml
Makefile
docs/decisions/0007-features-cpd-builder-v1.md
docs/features_cpd_v1.md             # optional, but preferred
tests/unit/test_features_builder.py
tests/unit/test_cpd.py
tests/integration/test_wp7_features_cli.py
```

Prefer pure functions for calculations. The CLI should be thin.

---

## 8. QA report requirements

The QA report must include:

```text
snapshot_id
feature_set_id
series_id
builder_version
cpd_builder_version
start_date
end_date
roots
feature_rows
cpd_rows
complete_feature_ratio_by_root
cpd_valid_ratio_by_root_and_window
warmup_status_counts
missing_input_counts
blocked_quality_counts
max_abs_by_feature
annualized_vol_60_summary_by_root
return_validation_warning_count
feature_hash_duplicate_count
primary_key_duplicate_count
fatal_error_count
warning_count
```

Fatal errors:

- Duplicate primary keys in `features_daily`.
- Duplicate primary keys in `cpd_daily`.
- Missing required columns.
- Non-finite values where `is_complete=true`.
- CPD score outside `[0, 1]`.
- CPD age outside `[0, 1]` in `features_daily`.
- CPD age days outside `[0, window]` in `cpd_daily`.
- `feature_hash` missing or duplicated for different feature payloads.

Warnings:

- Return validation mismatch count > 0.
- Feature complete ratio below 95% after warmup.
- A root has zero valid CPD rows for a configured window.
- Absolute feature values hit clipping caps frequently.

---

## 9. Makefile targets

Add:

```makefile
wp7-features-smoke-offline:
	$(ACTIVATE) && pytest -q tests/unit/test_features_builder.py tests/unit/test_cpd.py tests/integration/test_wp7_features_cli.py
```

If the repo uses a different test layout, adapt the target, but keep the target name.

`make test` must still pass.

---

## 10. Offline synthetic tests

At minimum, implement these tests.

### CPD tests

1. **Stationary low score**  
   A constant or IID low-noise return window should produce a low score.

2. **Step-change high score**  
   A synthetic window with a clear mean shift should produce a higher score and a location near the true split.

3. **Recent change lower age**  
   A split near the end of the window should produce a lower `cpd_age_days` than a split near the start.

4. **Invalid warmup**  
   Fewer than `L` observations should produce `cpd_is_valid=false`.

### Feature tests

1. **No future leakage**  
   Build features for dates through `T`. Then alter prices strictly after `T` and rebuild. Features at `T` must be byte-identical or hash-identical.

2. **Daily return validation**  
   If `continuous_daily.daily_return` disagrees with adjusted price returns, QA emits a warning but feature calculation uses adjusted prices.

3. **Warmup classification**  
   Early rows before 252-session history are `warmup` and not complete.

4. **Blocked quality**  
   A row with `is_usable_for_signal=false` becomes `blocked_quality` and `is_complete=false`.

5. **Feature hash determinism**  
   Two builds from identical inputs produce identical `feature_hash` and identical Parquet logical rows.

6. **Schema columns**  
   Output columns exactly match WP3 logical schema for `cpd_daily` and `features_daily`.

### CLI integration test

Create a tiny synthetic `continuous_daily` fixture for two roots, run build+qa to a temp directory, and assert that both Parquet outputs and QA artifacts exist.

No test should require Databento, IBKR, or internet access.

---

## 11. Acceptance criteria

WP7 is complete only when:

1. `make test` passes.
2. `make wp7-features-smoke-offline` passes.
3. Feature and CPD outputs match WP3 schema.
4. The build is deterministic across two identical runs.
5. QA JSON/Markdown are produced.
6. `features_daily` has stable `feature_hash` values.
7. No-lookahead test passes.
8. CPD 21 and CPD 63 both appear in `cpd_daily`.
9. `features_daily` contains normalized CPD ages, while `cpd_daily` contains raw integer ages.
10. Existing WP4/WP5/WP6 commands are not broken.

---

## 12. Notes for later WPs

- WP8 will consume `features_daily` for TSMOM fallback and model-ready signal infrastructure.
- WP9 will consume the exact ordered feature vector for CPD-LSTM datasets.
- If a paper-compatible GP/OCPD backend is added later, keep `cpd_daily` output schema unchanged and add a new `cpd_method`, e.g. `gp_ocpd_v2`.
- Do not let future model code recompute features ad hoc. Models must consume `features_daily`.
