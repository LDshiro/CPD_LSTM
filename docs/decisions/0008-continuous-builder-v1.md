# 0008: Continuous Builder v1

- Status: accepted
- Date: 2026-04-20

## Context

WP6 needs a deterministic `continuous_daily` dataset for signal generation and research. The repository already stores WP4/WP5 curated data snapshot-by-snapshot, and WP6 must stay aligned with that lineage model.

The project also requires a hard separation between:

- broker-facing raw tradable contracts; and
- signal-only continuous prices.

## Decision

WP6 adopts:

- `series_id = v1_back_ratio_settle`
- `builder_version = continuous_builder_v1`
- method = backward ratio-adjusted settlement series
- roll source = WP5 `lead_map` + `roll_events`
- price source = WP4 `contracts_daily.settle_price`

The physical storage layout is:

```text
data/curated/continuous_daily/series_id=<series_id>/snapshot_id=<snapshot_id>/year=<YYYY>/
```

This adds a snapshot partition beneath the recommended series/year path so that WP6 stays consistent with WP4/WP5 overwrite safety and lineage semantics.

## Consequences

- Continuous prices remain research/signal-only and are never broker-facing.
- Future roll events outside the requested build end date do not affect earlier adjustment factors.
- Missing or nonpositive roll ratios are fatal in strict mode.
- Missing raw prices are preserved as explicit unusable rows with quality flags instead of silent fills.
- QA artifacts live under `artifacts/wp6/`.
