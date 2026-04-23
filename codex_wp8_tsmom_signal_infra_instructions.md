# WP8 Codex Instructions — TSMOM Fallback / Signal Infrastructure v1

Status: **ready for local Codex implementation**  
Goal: implement a deterministic signal layer and the first production fallback strategy: **diversified TSMOM v1**. WP8 consumes WP7 `features_daily` and writes WP3-compatible `signals_daily`. It must also establish the strategy interface that WP9 CPD-LSTM inference will reuse.

WP8 is deliberately conservative. The goal is not to optimize TSMOM performance. The goal is to create a trustworthy, schema-compatible, no-lookahead signal path that can run even when CPD-LSTM is unavailable.

---

## 0. Branch and scope

Create a new branch from the latest local repository state:

```bash
git checkout -b wp8-tsmom-signal-infra
```

### In scope

Implement:

1. A generic signal record / signal builder abstraction.
2. Formulaic TSMOM fallback strategy.
3. Stable `signals_daily` writer and reader.
4. TSMOM formula artifact metadata.
5. Optional `model_registry` row generation for formulaic strategies.
6. QA report generation for signal coverage and validity.
7. CLI commands for building and QAing signals.
8. Offline synthetic tests.
9. A Makefile smoke target.
10. A minimal integration smoke showing that TSMOM signals can feed the existing portfolio/risk sizing layer, if that layer's current API is available.

### Out of scope

Do not implement:

- CPD-LSTM model training or inference.
- Walk-forward evaluation.
- Broker / IBKR logic.
- Order intents.
- Live or paper execution.
- New feature engineering.
- CPD algorithm changes.
- Strategy performance optimization or hyperparameter search.

WP8 must be fully testable offline with synthetic `features_daily` fixtures.

---

## 1. Inputs and outputs

### Required input

WP8 consumes WP7 `features_daily`.

Expected logical columns:

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

TSMOM v1 uses only:

```text
ret_21
ret_63
ret_252
is_complete
warmup_status
feature_hash
snapshot_id
```

It must not use future returns, `continuous_daily`, raw contracts, CPD features, or any broker state.

### Output

Write WP3-compatible `signals_daily` rows:

```text
run_id
strategy_id
model_id
as_of_date
root
signal_raw
signal_clipped
is_valid
invalid_reason
feature_hash
created_at_utc
```

Recommended physical layout:

```text
data/research/signals_daily/strategy_id=tsmom/model_id=tsmom_v1/year=<YYYY>/part-*.parquet
```

Also write QA artifacts:

```text
artifacts/wp8/signals_qa_<run_id>.json
artifacts/wp8/signals_qa_<run_id>.md
```

Also write the formula artifact:

```text
artifacts/strategies/tsmom_v1/formula.json
artifacts/strategies/tsmom_v1/formula.sha256
```

If the repo already has a registry writer from WP3/WP4, also append a `model_registry` row with:

```text
model_id = tsmom_v1
strategy_id = tsmom
training_run_id = null
feature_set_id = features_v1
model_status = shadow
artifact_path = artifacts/strategies/tsmom_v1/formula.json
```

If registry writing is not implemented yet, expose a pure function that returns this row as a dict/dataframe and cover it with a unit test. Do not block WP8 on a full registry persistence layer.

---

## 2. Configuration

Extend `config/settings.base.yml` with a stable `signals` / `strategies.tsmom` section. Keep names stable; WP9 and WP14 will rely on them.

```yaml
signals:
  output_dataset: data/research/signals_daily
  qa_artifact_dir: artifacts/wp8
  default_write_mode: overwrite_partition
  stable_sort_keys: [as_of_date, root]
  clip_min: -1.0
  clip_max: 1.0

strategies:
  tsmom:
    strategy_id: tsmom
    model_id: tsmom_v1
    signal_version: tsmom_signal_v1
    formula_artifact_dir: artifacts/strategies/tsmom_v1
    feature_set_id: features_v1
    required_features: [ret_21, ret_63, ret_252]
    horizons_days: [21, 63, 252]
    weights: [0.3333333333333333, 0.3333333333333333, 0.3333333333333333]
    sign_zero_policy: zero
    allow_partial_horizons: false
    require_feature_complete: true
    require_warmup_status_ok: true
    invalid_reasons:
      missing_required_feature: missing_required_feature
      nonfinite_required_feature: nonfinite_required_feature
      feature_incomplete: feature_incomplete
      warmup_not_ok: warmup_not_ok
      root_not_requested: root_not_requested
```

Add config schema validation if the project already has Pydantic/dataclass settings. Invalid configs must fail early.

Do not create root-specific weights in WP8. Do not add CPD gating to TSMOM. TSMOM must remain a transparent benchmark and fallback.

---

## 3. TSMOM v1 formula

For each `(as_of_date, root)` with a valid feature row:

```text
signal_raw = (
    sign(ret_21) * 1/3
  + sign(ret_63) * 1/3
  + sign(ret_252) * 1/3
)

signal_clipped = clamp(signal_raw, -1.0, 1.0)
```

Where:

```text
sign(x) = +1 if x > 0
sign(x) =  0 if x == 0
sign(x) = -1 if x < 0
```

Required validity rules:

```text
is_complete == true
warmup_status == 'ok'
ret_21, ret_63, ret_252 are present and finite
feature_hash is present and non-empty
```

If any required validity rule fails:

```text
signal_raw = null
signal_clipped = null
is_valid = false
invalid_reason = one stable enum string
```

Do not silently impute missing returns. Do not allow partial-horizon TSMOM in v1.

### Examples

| ret_21 | ret_63 | ret_252 | signal_raw |
|---:|---:|---:|---:|
| 1.2 | 0.4 | 2.0 | 1.0 |
| 1.2 | -0.4 | 2.0 | 0.3333333333 |
| -1.2 | -0.4 | 2.0 | -0.3333333333 |
| -1.2 | -0.4 | -2.0 | -1.0 |
| 0.0 | 1.0 | -1.0 | 0.0 |

---

## 4. Signal infrastructure design

Create a small strategy interface that can be reused by WP9 CPD-LSTM.

Suggested files:

```text
src/cpdshadow/signals.py
src/cpdshadow/signal_io.py
src/cpdshadow/strategies/__init__.py
src/cpdshadow/strategies/base.py
src/cpdshadow/strategies/tsmom.py
```

If the repo already has a different organization, adapt to it, but preserve the same public behavior and tests.

### Suggested core objects

```python
@dataclass(frozen=True)
class SignalBuildRequest:
    run_id: str
    strategy_id: str
    model_id: str
    feature_set_id: str
    snapshot_id: str | None
    start_date: date | None
    end_date: date | None
    roots: tuple[str, ...] | None
    created_at_utc: datetime

@dataclass(frozen=True)
class SignalBuildResult:
    signals: pd.DataFrame
    qa: dict[str, Any]
    formula_artifact_path: Path | None = None

class SignalStrategy(Protocol):
    strategy_id: str
    model_id: str
    def build_signals(self, features: pd.DataFrame, request: SignalBuildRequest) -> SignalBuildResult: ...
```

Do not over-engineer the interface. Keep it explicit enough for WP9's model-backed strategy to implement later.

### Signal schema enforcement

Add a validation function that enforces:

```text
required columns exist
primary key uniqueness: run_id, strategy_id, as_of_date, root
signal_clipped is null or between -1 and 1
is_valid false implies invalid_reason is non-empty
is_valid true implies invalid_reason is null
feature_hash is carried through when feature row is valid
as_of_date is date-like and stable
rows sorted by as_of_date, root before writing
```

### Determinism

Repeated runs with the same input, same config, same `run_id`, and same `created_at_utc` must produce byte-equivalent dataframes after stable sorting, ignoring Parquet metadata. Unit tests should compare dataframe content.

Input row order must not affect output order or signal values.

---

## 5. Formula artifact

Write a small artifact that records exactly what formula was used.

Suggested `formula.json`:

```json
{
  "strategy_id": "tsmom",
  "model_id": "tsmom_v1",
  "signal_version": "tsmom_signal_v1",
  "feature_set_id": "features_v1",
  "required_features": ["ret_21", "ret_63", "ret_252"],
  "horizons_days": [21, 63, 252],
  "weights": [0.3333333333333333, 0.3333333333333333, 0.3333333333333333],
  "formula": "mean(sign(ret_21), sign(ret_63), sign(ret_252))",
  "clip": [-1.0, 1.0],
  "allow_partial_horizons": false,
  "sign_zero_policy": "zero"
}
```

Calculate SHA-256 over stable canonical JSON and write it to `formula.sha256`. Use this hash as `artifact_sha256` if writing a registry row.

---

## 6. CLI commands

Extend the existing CLI style. If the repo already uses argparse, keep argparse. If it uses Typer, keep Typer. Do not add a heavy CLI dependency just for WP8.

Required command shape:

```bash
python -m cpdshadow.cli signals tsmom build \
  --features-path data/features/features_daily \
  --snapshot-id <snapshot_id> \
  --feature-set-id features_v1 \
  --start 2024-01-02 \
  --end 2024-12-31 \
  --roots ES,NQ,ZN \
  --run-id infer_tsmom_2024 \
  --created-at-utc 2026-04-20T00:00:00Z \
  --output-dir data/research/signals_daily
```

Required QA command:

```bash
python -m cpdshadow.cli signals qa \
  --signals-path data/research/signals_daily \
  --run-id infer_tsmom_2024
```

Required offline smoke command target:

```bash
make wp8-signal-smoke-offline
```

The smoke target must create synthetic features in a temp directory, build TSMOM signals, run signal QA, and exit without network/API/broker calls.

---

## 7. QA report

Generate JSON and Markdown reports with at least:

```text
run_id
strategy_id
model_id
feature_set_id
snapshot_id
start_date
end_date
root_count
row_count
valid_row_count
invalid_row_count
coverage_by_root
invalid_reason_counts
signal_distribution: min, p05, median, p95, max
long_count / short_count / flat_count
missing_required_feature_count
created_at_utc
```

For TSMOM, `long_count` means `signal_clipped > 0`, `short_count` means `< 0`, `flat_count` means `== 0` among valid rows.

If invalid rows exceed 5% of requested rows outside expected warmup dates, the QA command should return a non-zero exit code or at least surface a `severity = warning` in the report. In WP8, prefer report warnings over hard failure unless primary key or schema validity is broken.

---

## 8. Tests

Add offline tests only.

Suggested tests:

```text
tests/unit/test_tsmom_signal_formula.py
tests/unit/test_signal_schema_validation.py
tests/unit/test_signal_artifact.py
tests/integration/test_wp8_signals_cli.py
```

Required coverage:

1. Exact formula examples.
2. `sign(0) == 0`.
3. Missing `ret_252` invalidates row when `allow_partial_horizons=false`.
4. `warmup_status != ok` invalidates row.
5. `is_complete=false` invalidates row.
6. Non-finite required features invalidate row.
7. Valid rows carry through `feature_hash`.
8. `signal_clipped` is always in `[-1, 1]` or null.
9. Duplicate primary keys are rejected.
10. Input order does not affect output content.
11. Changing a future feature row does not change earlier signals.
12. Formula artifact hash is stable.
13. CLI writes Parquet and QA artifacts.
14. The smoke target does not require Databento, IBKR, network, or local secrets.

If the existing project has a strict type/lint target, keep it passing. Do not weaken existing tests.

---

## 9. Minimal integration with sizing layer

If the Step 11 portfolio/risk API is already available in the current repo, add a small offline smoke that demonstrates:

```text
synthetic features_daily
  -> tsmom signals_daily
  -> existing target-contract conversion using synthetic lead_map/current positions
  -> target contracts dataframe
```

Do not persist `targets_daily` as WP8's main output. WP8's canonical output is `signals_daily`. The target smoke is only to prove that TSMOM fallback can later power the shadow pipeline.

If the current portfolio API has drifted or is not yet wired, skip this integration smoke with a clear pytest skip reason and keep the signal layer complete.

---

## 10. Acceptance criteria

WP8 is complete when all are true:

```bash
make test
make wp8-signal-smoke-offline
```

and:

- `signals_daily` rows conform to WP3 schema.
- `tsmom_v1` formula artifact is written and hash-stable.
- Signal generation is deterministic.
- No-lookahead signal test passes.
- No vendor/API/IBKR/network calls occur in normal tests.
- TSMOM can be used as a fallback signal source even if CPD-LSTM is absent.
- Documentation and decision record are committed.

---

## 11. Implementation order for Codex

Recommended order:

1. Add config section and schema validation.
2. Add signal dataclasses / validation helpers.
3. Add TSMOM formula implementation.
4. Add formula artifact writer.
5. Add Parquet writer/reader helpers.
6. Add CLI build and QA commands.
7. Add QA JSON/Markdown reports.
8. Add unit tests.
9. Add integration smoke.
10. Add Makefile target.
11. Run full tests and fix stale interfaces without changing previous WP contracts.

Do not broaden WP8 to include model training or broker logic.
