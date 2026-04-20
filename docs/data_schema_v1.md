# Data Schema v1.0

- Status: Frozen for WP4 implementation
- Effective date: 2026-04-20

## Purpose

WP4 fixes the first physical storage contract for the raw Databento ingest layer and the first curated daily layer.
The canonical persisted format remains partitioned Parquet datasets. The DuckDB SQL file in `sql/duckdb_schema_v1.sql`
exists as an executable schema contract, not as a requirement to persist a `.duckdb` file during WP4.

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

## Physical notes

- Raw vendor parquet is immutable under `data/raw/databento/...`.
- Registry parquet is append-only under `data/meta/...`.
- Curated parquet is written snapshot-by-snapshot under `data/curated/contract_master` and `data/curated/contracts_daily`.
- `quality_flags` is stored as a sorted list of strings.
