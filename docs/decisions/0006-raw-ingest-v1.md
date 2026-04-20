# Decision 0006: Fix Databento raw ingest and first curated daily layer for v1.0

- Status: accepted
- Date: 2026-04-20
- Supersedes: none

## Context

WP4 is the first point where the repository persists external market data. The project already fixed:

- the v1.0 universe in `config/instruments.yml`
- the shared risk and cost layer
- the monitoring contract

What remained under-specified was how raw vendor data should be acquired, persisted, normalized, and traced back to a reproducible snapshot.

## Decision

We fix WP4 as follows.

1. Databento historical data is acquired with parent symbology rooted in `config/instruments.yml`.
2. Raw vendor parquet is immutable and written under `data/raw/databento/...`.
3. Every ingest run writes append-only lineage registries:
   - `run_registry`
   - `data_snapshot_registry`
   - `data_file_registry`
4. The first curated outputs are limited to:
   - `contract_master`
   - `contracts_daily`
5. `statistics` is the primary source for settlement, official volume, and open interest. `ohlcv-1d` is fallback/enrichment only.
6. Re-running the same logical request must never overwrite raw files and must remain snapshot-safe.

## Consequences

### Positive

- WP5 roll logic can build from local curated inputs without calling Databento again
- raw files remain auditable and reproducible
- snapshot lineage is explicit enough for shadow ops and postmortems
- ordinary tests stay offline by using fake clients and synthetic fixtures

### Trade-offs

- the first implementation is conservative about cost and requires explicit execution flags
- raw ingest carries more metadata bookkeeping than a research-only script
- curated daily output intentionally stops before lead-map, roll, or continuous-series logic

## Follow-up

- WP5: lead map and roll engine over `contract_master` and `contracts_daily`
- WP6: continuous-series construction from the local curated layer
