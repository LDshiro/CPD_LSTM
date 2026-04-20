# WP4 Codex Instruction — Raw Ingest and First Curated Daily Layer

Status: **ready for local Codex implementation**  
Owner: local Codex + user API credentials  
Input baseline: repository after WP3 data schema finalization  
Target branch: `wp4-raw-ingest`

## 0. Objective

Implement an idempotent Databento ingest layer that can:

1. Read the v1.0 futures universe from `config/instruments.yml`.
2. Pull Databento historical data for `definition`, `statistics`, and optionally `ohlcv-1d` for the 20 parent roots.
3. Persist immutable raw vendor Parquet files under `data/raw/databento/...`.
4. Populate lineage registries under `data/meta/...`:
   - `run_registry`
   - `data_snapshot_registry`
   - `data_file_registry`
5. Normalize the raw data into the first curated layer:
   - `curated/contract_master`
   - `curated/contracts_daily`
6. Make the ingest fully reproducible and safe to run repeatedly.

WP4 must produce data that WP5 roll engine can consume without needing to call Databento again.

## 1. Scope boundary

### In scope

- Databento historical client wrapper.
- API key loading from environment.
- Cost/size preflight before paid pulls.
- Parent-symbol request planning.
- Small smoke pulls.
- Incremental daily pulls.
- Optional large backfill support by date chunking.
- Immutable raw Parquet persistence.
- File hashing and row-count manifesting.
- Snapshot/run/file registry rows.
- Normalization to `contract_master` and `contracts_daily`.
- Unit/integration tests using fake Databento clients and fixture DataFrames.
- One optional live-vendor smoke test marked/skipped by default.

### Out of scope

- Roll-engine logic: no `lead_map`, no `roll_events` except placeholder-free tests.
- Continuous series: no `continuous_daily` builder.
- CPD/features/model training.
- IBKR connection.
- Broker order generation.
- Vendor `continuous` symbols for signals or trading.
- Manual override UI.

## 2. Non-negotiable design constraints

1. **No Databento calls in normal tests.** All tests must pass without network and without `DATABENTO_API_KEY`.
2. **Raw files are immutable.** Never edit a raw file in place. If a request is re-run, either detect identical output and reuse it, or write a new file with a new `snapshot_id`/`run_id`.
3. **Do not send continuous futures into any broker-facing layer.** WP4 does not touch broker code, but keep the separation explicit in docs and types.
4. **Use parent symbology for acquisition, then filter to outright futures.** `ES.FUT` can include related futures instruments and spreads, so filtering is mandatory.
5. **Prefer official statistics for settlement, volume, and OI.** `ohlcv-1d` is fallback/enrichment only.
6. **Use `ts_ref` for statistics session dating when available.** Daily statistics may be published on a later UTC date than the trading session they reference.
7. **Persist all request parameters and hashes.** Every raw file must be traceable back to the request that produced it.
8. **Be conservative about cost.** Large backfills require a preflight plan and an explicit execution flag.
9. **Make all writes idempotent.** Re-running the same command must not silently duplicate logical rows or corrupt registries.
10. **Keep implementation testable by dependency injection.** The normalizer must accept DataFrames; the Databento wrapper must be mockable.

## 3. External facts to respect

These are the relevant Databento facts that guided this instruction:

- The Python package is `databento`; PyPI currently shows version `0.75.0`, released 2026-04-08, with Python >=3.10 support.
- `db.Historical()` can read the API key from the `DATABENTO_API_KEY` environment variable. Do not put the key in repo files.
- `Historical.timeseries.get_range` is the primary method for retrieving historical data directly into the application; for large requests, Databento recommends considering batch jobs.
- The API supports `schema`, `stype_in`, `stype_out`, `symbols`, `start`, `end`, and `limit` parameters.
- Databento supports `raw_symbol`, `instrument_id`, `parent`, and `continuous` symbology types.
- Futures parent symbols use `[ROOT].FUT`, for example `ES.FUT`; parent futures requests can include futures spreads, so `instrument_class` filtering is required.
- `definition` records are point-in-time reference information with fields such as `raw_symbol`, `instrument_id`, expiration, tick size, and instrument class.
- `statistics` provides venue-published official daily summary statistics, including settlement price, cleared volume, and open interest. Settlement is `stat_type=3`, cleared volume `stat_type=6`, and open interest `stat_type=9`.
- `ohlcv-1d` aggregates trades into daily bars, but if no trade occurs within the interval, no record is printed.

## 4. Files to add or modify

Implement the following structure. Adjust names only if there is a strong reason, and document deviations in the completion note.

```text
config/
  databento.ingest.yml                 # new: ingest defaults and schema mapping

src/cpdshadow/
  cli.py                               # new or extend: Typer entrypoint
  ids.py                               # new: deterministic IDs/hashes helpers if not already present
  storage/
    __init__.py
    parquet_io.py                      # new: partitioned parquet read/write helpers
    registry.py                        # new: run/snapshot/file registry helpers
  vendor/
    __init__.py
    databento_client.py                # new: Databento wrapper + protocol/fakeable interface
  ingest/
    __init__.py
    databento_raw.py                   # new: request planning, pulls, raw persistence
    normalize_databento.py             # new: definition/statistics/ohlcv normalizers
    quality.py                         # new: QA checks for WP4 data

tests/
  fixtures/databento/
    definition_sample.parquet           # small synthetic or sanitized fixture
    statistics_sample.parquet
    ohlcv_1d_sample.parquet
  unit/test_databento_request_plan.py
  unit/test_databento_normalize.py
  unit/test_registry_storage.py
  integration/test_wp4_ingest_offline.py
  integration/test_wp4_vendor_smoke.py  # skipped unless env flag is set
```

Modify:

```text
pyproject.toml
Makefile
.env.example
docs/spec_v1.md
docs/data_schema_v1.md                 # only add WP4 implementation notes, do not alter schema contract
docs/decisions/0006-raw-ingest-v1.md   # new decision memo
```

## 5. Dependency changes

Update `pyproject.toml`:

```toml
[project]
dependencies = [
  # existing deps ...
  "databento>=0.75,<1.0",
  "tenacity>=8.3",
]
```

Rationale:

- `databento>=0.75,<1.0` aligns with the current PyPI package and avoids uncontrolled major-version changes.
- `tenacity` is for bounded retry on transient vendor/API errors.

Do not add heavy frameworks. Avoid Dask/Spark. Pandas + PyArrow + DuckDB are enough.

## 6. Config file specification

Create `config/databento.ingest.yml`:

```yaml
version: 1.0.0
vendor: databento
client:
  dataset: GLBX.MDP3
  key_env: DATABENTO_API_KEY
  default_stype_in: parent
  default_stype_out: raw_symbol
  request_timeout_seconds: 300
  max_retries: 3
  retry_backoff_seconds: 5
  max_symbols_per_request: 20
  price_type: float
  pretty_ts: true
  map_symbols: true

requests:
  default_start_date: "2010-01-01"
  chunking:
    bootstrap: year
    incremental: month
  schemas:
    definition:
      enabled: true
      required: true
    statistics:
      enabled: true
      required: true
    ohlcv-1d:
      enabled: true
      required: false
      purpose: fallback_close_and_ohlc

cost_control:
  require_preflight: true
  require_execute_flag_for_paid_pull: true
  default_max_cost_usd: 5.00
  smoke_max_days: 10

raw_storage:
  root: data/raw/databento
  write_mode: immutable
  compression: zstd

curated_storage:
  contract_master: data/curated/contract_master
  contracts_daily: data/curated/contracts_daily

normalization:
  keep_only_roots_from_instruments_yml: true
  keep_only_outright_futures: true
  settlement_priority:
    - final_settlement
    - preliminary_settlement
    - close_fallback
  statistics_trade_date_field: ts_ref
  ohlcv_trade_date_field: ts_event
  unavailable_price_policy: mark_missing

quality:
  fail_on_empty_required_schema: true
  fail_on_unknown_root: true
  warn_on_missing_settlement: true
  warn_on_zero_volume_lead_candidates: true
```

## 7. CLI contract

Add a Typer CLI. Preferred commands:

```bash
python -m cpdshadow.cli ingest databento plan \
  --start 2024-01-01 \
  --end 2024-02-01 \
  --roots ES,NQ,ZN,CL \
  --schemas definition,statistics,ohlcv-1d \
  --output artifacts/wp4/plan_2024_01.json
```

```bash
python -m cpdshadow.cli ingest databento smoke \
  --start 2024-01-01 \
  --end 2024-01-08 \
  --roots ES,NQ \
  --max-cost-usd 1.00 \
  --execute
```

```bash
python -m cpdshadow.cli ingest databento run \
  --start 2010-01-01 \
  --end 2026-04-01 \
  --roots-from-config \
  --schemas definition,statistics,ohlcv-1d \
  --max-cost-usd 100.00 \
  --execute
```

```bash
python -m cpdshadow.cli ingest databento normalize \
  --snapshot-id <snapshot_id>
```

```bash
python -m cpdshadow.cli ingest databento qa \
  --snapshot-id <snapshot_id>
```

Alternative command names are acceptable if all functions exist.

### CLI behavior rules

- Without `--execute`, never call `timeseries.get_range`; produce a plan only.
- With `--execute`, require `DATABENTO_API_KEY`.
- Always run preflight cost/size if Databento metadata methods are available.
- If preflight cannot be performed, require explicit `--skip-preflight` and record this in `run_registry.notes`.
- `--force` may create a new snapshot, but must not overwrite raw files.
- Every command must write a machine-readable artifact under `artifacts/wp4/`.

## 8. Request planning

Input roots come from `config/instruments.yml` unless explicitly overridden. Convert:

```text
ES -> ES.FUT
NQ -> NQ.FUT
...
```

Use:

```python
client.timeseries.get_range(
    dataset="GLBX.MDP3",
    symbols=["ES.FUT", "NQ.FUT", ...],
    stype_in="parent",
    stype_out="raw_symbol",
    schema=schema,
    start=start,
    end=end,
)
```

For a large bootstrap, chunk by year per schema. Keep the 20 parent symbols in one request unless the API or cost estimate suggests splitting.

Recommended chunk key:

```text
request_id = sha256(dataset|schema|stype_in|stype_out|symbols_sorted|start|end|schema_version|config_hash)
```

Raw output filename:

```text
data/raw/databento/dataset=GLBX.MDP3/schema=<schema>/year=<YYYY>/<request_id>.parquet
```

## 9. Registry semantics

### `run_registry`

Create one `run_registry` row per command invocation.

- `run_type = ingest`
- `status = started/succeeded/failed`
- `data_snapshot_id = snapshot_id` after snapshot creation
- `config_hash = hash(config/instruments.yml + config/databento.ingest.yml + config/data_schema.yml)`
- `git_commit = git rev-parse HEAD`, fallback `unknown-local`

### `data_snapshot_registry`

Create one logical snapshot per executed ingest run.

- `vendor = databento`
- `dataset = GLBX.MDP3`
- `start_date`, `end_date` are full requested range
- `manifest_hash` = hash of request plan + file hashes + row counts

### `data_file_registry`

Create one row per raw Parquet file and one row per generated curated Parquet dataset part if implemented with file-level manifests.

Required:

- `file_id`
- `snapshot_id`
- `logical_table`
- `source_schema`
- `path`
- `content_sha256`
- `row_count`
- `min_trade_date`
- `max_trade_date`
- `request_params_hash`

## 10. Raw persistence rules

When a `DBNStore` is returned:

```python
data.to_parquet(
    path,
    price_type="float",
    pretty_ts=True,
    map_symbols=True,
    schema=schema,
)
```

After writing:

1. Read the Parquet back with PyArrow or pandas.
2. Compute `row_count`.
3. Determine trade-date range:
   - `definition`: from `ts_event`, with fallback to `ts_recv`.
   - `statistics`: from `ts_ref` where present, else `ts_event`.
   - `ohlcv-1d`: from `ts_event`.
4. Compute SHA-256 over file bytes.
5. Add file registry row.

Never use CSV as the canonical raw format.

## 11. Normalization: `contract_master`

Source: raw `definition` rows.

Transformation:

1. Convert timestamps to UTC-aware pandas timestamps, then store UTC timestamps.
2. Filter to roots in `config/instruments.yml`.
3. Filter to outright futures only:
   - accept Databento enum/string corresponding to FUTURE;
   - reject spreads/options/combos;
   - when ambiguous, reject and record quality warning.
4. Map root using `asset` when present; fallback to parsed configured root only if unambiguous.
5. Map fields into `contract_master` schema:
   - `dataset`
   - `instrument_id`
   - `raw_symbol`
   - `root`
   - `exchange`
   - `currency`
   - `expiration_date`
   - `last_trade_date`
   - `first_trade_date`
   - `multiplier`
   - `tick_size`
   - `instrument_class`
   - `valid_from_utc`
   - `valid_to_utc`
   - `definition_hash`
   - `ingested_at_utc`
6. Use v1.0 instrument config as fallback for `multiplier` and `tick_size` only when Databento fields are missing; flag the row.

Deduplication:

- Primary logical key is `(dataset, raw_symbol, valid_from_utc)`.
- If multiple identical definition messages appear, keep the most recent by `ts_event`/`ts_recv` and stable hash.
- Do not collapse genuine point-in-time definition changes.

## 12. Normalization: `contracts_daily`

Source priority:

1. `statistics` for settlement, volume, open interest, official high/low/open/close if available.
2. `ohlcv-1d` for OHLC and close fallback.
3. Missing rows are allowed only if `settle_status = missing` and `quality_flags` explains why.

Daily key:

```text
(trade_date, root, raw_symbol)
```

### Statistics handling

Use `ts_ref` as the trading session date when it is not null. If `ts_ref` is null/invalid, fallback to `ts_event` date and set quality flag `stats_missing_ts_ref`.

Map `stat_type`:

| stat_type | Meaning | Target field |
|---:|---|---|
| 1 | Opening price | `open_price`, if better official source exists |
| 3 | Settlement price | `settle_price` |
| 4 | Trading session low | `low_price` |
| 5 | Trading session high | `high_price` |
| 6 | Cleared volume | `volume` |
| 9 | Open interest | `open_interest` |
| 11 | Close price | `close_price` |

If multiple rows exist for the same `stat_type`, keep the most recent by `ts_event`/`ts_recv`/`sequence`. For settlement, prefer final over preliminary if flags can be decoded. If flags cannot be decoded, set `settle_status = preliminary` unless there is clear final information.

### OHLCV handling

`ohlcv-1d` is used to fill missing `open/high/low/close/volume` when statistics are absent. It must not override an official statistics value unless the official value is null.

If `settle_price` is missing but `close_price` is present, set:

```text
settle_price = close_price
settle_status = close_fallback
price_source = ohlcv
quality_flags includes close_fallback_used
```

If neither settlement nor close is available:

```text
settle_status = missing
price_source = missing
quality_flags includes missing_price
```

### Output fields

Populate exactly the columns defined in `sql/duckdb_schema_v1.sql`:

```text
trade_date, root, raw_symbol, dataset, instrument_id,
open_price, high_price, low_price, close_price,
settle_price, settle_status,
volume, open_interest,
price_source, volume_source,
available_at_utc, ingested_at_utc,
quality_flags, override_id, snapshot_id
```

`available_at_utc` should be the latest vendor timestamp among the rows used for that contract/date, not the local ingest time. If uncertain, use the max of `ts_recv`/`ts_event` available and set a quality flag.

## 13. Quality checks

Implement `src/cpdshadow/ingest/quality.py` with checks that return structured warnings/errors.

Minimum checks:

| Check | Severity | Rule |
|---|---|---|
| required schema empty | error | `definition` or `statistics` returns zero rows for requested period |
| unknown root | error | normalized row root not in instruments config |
| spread leakage | error | `contract_master` contains futures spreads/options |
| duplicate daily key | error | duplicate `(trade_date, root, raw_symbol)` in `contracts_daily` |
| negative volume/OI | error | volume/open_interest < 0 |
| missing settlement | warning | `settle_status in ('missing','close_fallback')` |
| no active contract candidates | warning | root/date has no non-expired contracts with price or volume |
| price outlier | warning | daily move > 25% for rates/equity/fx, > 50% for commodities before roll logic |
| stale raw file | warning | file exists but registry missing or hash mismatch |

The CLI must print a concise table and write JSON to `artifacts/wp4/qa_<snapshot_id>.json`.

## 14. Idempotency rules

Before writing a raw file:

1. Build `request_params_hash`.
2. Check if a registry row exists for same request hash and same content hash.
3. If identical, reuse and mark as `cached` in run artifact.
4. If same request hash but different content, write a new file and mark `vendor_changed_same_request` warning.
5. Never delete old raw files automatically.

Before writing curated datasets:

- Write to a staging directory first.
- Validate row counts and primary-key uniqueness.
- Atomically replace the snapshot-specific curated partition if validation passes.
- Keep old snapshot partitions unless a user explicitly runs cleanup.

## 15. Tests

### Unit tests

Add tests for:

- Parent-symbol planning from `config/instruments.yml`.
- Request hash stability.
- Raw file hash computation.
- Registry row creation.
- Definition filtering excludes spreads/options.
- Definition normalizer maps tick/multiplier dates correctly.
- Statistics pivot maps `stat_type` correctly.
- `ts_ref` date is used for statistics trade date.
- Multiple settlement rows choose final/latest.
- OHLCV fallback does not override official settlement.
- Duplicate primary keys raise.
- Missing settlement produces warning, not crash.

### Offline integration test

Create synthetic fixture DataFrames for `ES` and `NQ` over a few days. Use fake client to simulate `get_range`. End-to-end command should:

1. Plan requests.
2. Write raw Parquet files.
3. Create registry files.
4. Normalize contract master and contracts daily.
5. Run QA.

No network.

### Optional live vendor smoke test

Add `tests/integration/test_wp4_vendor_smoke.py`, skipped unless:

```bash
export CPDSHADOW_RUN_VENDOR_TESTS=1
export DATABENTO_API_KEY=...
```

Smoke range:

```text
roots: ES,NQ
start: 2024-01-02
end: 2024-01-05
schemas: definition,statistics
```

It should not run by default in CI.

## 16. Makefile targets

Add or update:

```makefile
wp4-plan-smoke:
	python -m cpdshadow.cli ingest databento plan --start 2024-01-02 --end 2024-01-05 --roots ES,NQ --schemas definition,statistics --output artifacts/wp4/plan_smoke.json

wp4-smoke-offline:
	pytest -q tests/unit/test_databento_request_plan.py tests/unit/test_databento_normalize.py tests/integration/test_wp4_ingest_offline.py

wp4-smoke-vendor:
	CPDSHADOW_RUN_VENDOR_TESTS=1 pytest -q tests/integration/test_wp4_vendor_smoke.py
```

Do not make vendor smoke part of default `make test`.

## 17. Completion criteria

WP4 is complete only when all of the following pass:

1. `make test` passes without network.
2. `make wp4-smoke-offline` passes without network.
3. `python -m cpdshadow.cli ingest databento plan ...` produces a deterministic JSON plan.
4. With user-provided credentials, a tiny `--execute` smoke run succeeds for ES/NQ.
5. Raw Parquet files are written to `data/raw/databento/...`.
6. `data_file_registry` contains hashes, row counts, and date ranges.
7. `data_snapshot_registry` contains a manifest hash.
8. `contract_master` contains outright futures only.
9. `contracts_daily` has unique `(trade_date, root, raw_symbol)` keys.
10. Missing settlements are flagged, not silently imputed except explicit close fallback.
11. The same command can be re-run without corrupting output.
12. `docs/decisions/0006-raw-ingest-v1.md` documents actual deviations and lessons.

## 18. Suggested implementation order for Codex

1. Create branch `wp4-raw-ingest`.
2. Run baseline tests.
3. Add dependencies and config skeleton.
4. Implement pure helper utilities: hashing, request IDs, partition paths.
5. Implement storage registry helpers using Parquet and pandas/PyArrow.
6. Implement Databento protocol/wrapper, with fake client test double.
7. Implement request planner.
8. Implement raw writer and file registry creation.
9. Implement definition normalizer.
10. Implement statistics/OHLCV normalizer.
11. Implement QA module.
12. Implement Typer CLI.
13. Add offline tests and fixtures.
14. Add optional vendor smoke test.
15. Update docs and decision memo.
16. Run full tests.
17. Run live smoke only when user has set credentials and explicitly requested it.

## 19. Codex prompt to paste

Use this exact prompt in local Codex after checking out the latest repo:

```text
You are working in the CPD-LSTM shadow trading repository. Implement WP4: Databento raw ingest and first curated daily layer.

Start from the current repository that already includes WP3 data_schema_v1. Create branch wp4-raw-ingest. Read docs/spec_v1.md, docs/data_schema_v1.md, config/instruments.yml, config/data_schema.yml, and sql/duckdb_schema_v1.sql before editing.

Implement the instruction in docs/codex_wp4_raw_ingest_instructions.md. Do not call Databento in ordinary tests. Use fake clients and small synthetic fixtures. Add Databento as a dependency, create config/databento.ingest.yml, implement request planning, raw immutable Parquet storage, registry writes, definition/statistics/ohlcv normalization into contract_master and contracts_daily, QA checks, CLI commands, Makefile targets, and docs/decisions/0006-raw-ingest-v1.md.

Keep WP4 scope strict: no lead_map, no roll_events builder, no continuous_daily, no CPD/features/model, no IBKR. The only curated outputs are contract_master and contracts_daily plus meta registries.

Acceptance: make test and make wp4-smoke-offline must pass without network. The optional live vendor smoke test must be skipped by default and only run when CPDSHADOW_RUN_VENDOR_TESTS=1 and DATABENTO_API_KEY are present. All writes must be idempotent and raw files immutable.
```

## 20. Human checklist before running live vendor smoke

The user should confirm:

- `DATABENTO_API_KEY` exists in local environment, not committed.
- Databento account has access to `GLBX.MDP3` historical data.
- Cost limit is acceptable for the smoke request.
- The smoke date range is small.
- No default command performs a large paid pull.

