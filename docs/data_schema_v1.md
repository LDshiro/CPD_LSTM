# Data Schema v1.0

- Status: Frozen for WP4-WP6 implementation
- Effective date: 2026-04-20

## Purpose

WP4 fixes the first physical storage contract for the raw Databento ingest layer and the first curated daily layer.
WP5 extends that contract with deterministic roll outputs derived from WP4 curated data.
WP6 extends that contract with a deterministic signal-only continuous series derived from WP4/WP5 curated data.
The canonical persisted format remains partitioned Parquet datasets. The DuckDB SQL file in `sql/duckdb_schema_v1.sql`
exists as an executable schema contract, not as a requirement to persist a `.duckdb` file during WP4-WP6.

## Tables

### `run_registry`

One row per CLI invocation. Tracks command status, config hash, git commit, snapshot linkage, and the machine-readable artifact path.

### `data_snapshot_registry`

One logical snapshot per executed ingest run. Tracks requested range, requested roots, requested schemas, request-plan hash, and final manifest hash.

### `data_file_registry`

Append-only file manifest for raw and curated parquet outputs. Stores content hash, row count, trade-date range, request hash, and cache/reuse status.

### `contract_master`

Normalized point-in-time definitions for outright futures only. Logical uniqueness is `(dataset, raw_symbol, valid_from_utc)`.

### `contracts_daily`

Normalized daily contract data keyed by `(trade_date, root, raw_symbol)`. `statistics` is the primary source for settlement, cleared volume, and open interest. `ohlcv-1d` only fills missing OHLC/close/volume and can provide explicit `close_fallback`.

### `roll_events`

One row per lead-contract transition. Contains deterministic roll identifiers, trigger/effective dates, roll reason, prior-day volume context, and optional settlement-based ratio helpers for WP6 continuous construction.

### `lead_map`

One row per `(as_of_date, root)` that maps the strategy root to the actual tradable listed futures contract selected under the fixed roll policy. `snapshot_id` is inherited from the source WP4 curated snapshot.

### `continuous_daily`

One row per `(series_id, as_of_date, root)` for the canonical signal-generation series. WP6 v1 uses a backward ratio-adjusted settlement methodology driven by `lead_map` and `roll_events`, not vendor continuous prices. `continuous_daily` is signal-only and must never be used as a broker-facing contract identifier.

## Physical notes

- Raw vendor parquet is immutable under `data/raw/databento/...`.
- Registry parquet is append-only under `data/meta/...`.
- Curated parquet is written snapshot-by-snapshot under `data/curated/contract_master` and `data/curated/contracts_daily`.
- WP5 roll outputs are written snapshot-by-snapshot under `data/curated/lead_map` and `data/curated/roll_events`, partitioned by year within each snapshot.
- WP6 continuous outputs are written under `data/curated/continuous_daily/series_id=<series_id>/snapshot_id=<snapshot_id>`, partitioned by year within each snapshot.
- `quality_flags` is stored as a sorted list of strings.
