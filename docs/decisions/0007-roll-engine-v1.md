# 0007: Roll Engine v1

- Status: accepted
- Date: 2026-04-20

## Context

WP4 fixed raw ingest and the first curated daily layer, but v1 still needed a deterministic lead-contract mapping
before continuous series construction can be implemented. The roll engine must be auditable, reproducible, and must
never select vendor continuous symbols as tradable contracts.

## Decision

We adopt `volume3_hardroll_v1` as the fixed WP5 policy:

- lead contracts are actual outright listed futures
- the root trading calendar is derived from observed `contracts_daily.trade_date`
- volume roll requires `next > front` for 3 consecutive completed root trading dates
- the new lead becomes effective on the next root trading date after confirmation
- hard roll uses per-root settings from `config/instruments.yml`
- hard roll overrides a pending volume roll if it would occur earlier
- rolled-away contracts do not become lead again

The implementation writes:

- `data/curated/lead_map/snapshot_id=<snapshot_id>/year=<YYYY>/...`
- `data/curated/roll_events/snapshot_id=<snapshot_id>/year=<YYYY>/...`

This path layout intentionally follows the WP4 snapshot-scoped convention instead of a flat year-only layout so that
reruns and `--overwrite` remain bounded to one source snapshot.

## Consequences

- WP6 can build continuous series from deterministic `lead_map` and `roll_events`.
- QA failures stop promotion of invalid roll outputs.
- Commodity roots currently fall back to `last_trade_date` or `expiration_date` when first-notice data is absent from
  the curated schema.
- The decision note number is `0007` because `0006-raw-ingest-v1.md` already exists and remains authoritative for WP4.
