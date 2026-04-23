# Decision 0009 — Features / CPD Builder v1

Status: accepted for WP7 implementation  
Date: 2026-04-21

## Context

WP4 fixed raw ingest and the first curated daily layer. WP5 added a
deterministic roll engine, and WP6 added the signal-only continuous series.
The next stable boundary is a reproducible model-input layer that downstream
research and model code can consume without recomputing features ad hoc.

The main risks at this stage are not model quality but data leakage, schema
drift, silent price substitution, and rebuild non-determinism.

## Decision

Implement WP7 with:

- `cpd_daily` as the long-form CPD output table
- `features_daily` as the wide model-input table
- `continuous_daily.adj_settle_price` as the authoritative source price
- adjusted-price return recomputation inside the feature layer
- fixed CPD windows `21` and `63`
- default CPD backend `two_sample_t_v1`
- deterministic `feature_hash` from the fixed ordered feature vector
- offline-only tests and QA artifacts

Use snapshot-partitioned feature storage:

```text
data/features/cpd_daily/feature_set_id=<feature_set_id>/snapshot_id=<snapshot_id>/year=<YYYY>/
data/features/features_daily/feature_set_id=<feature_set_id>/snapshot_id=<snapshot_id>/year=<YYYY>/
```

This intentionally adds `snapshot_id` to the physical layout relative to the
earlier draft instruction so lineage and overwrite behavior stay aligned with
WP4-WP6.

## Rationale

1. Reproducibility is more important than sophistication in WP7.
2. The CPD backend should be swappable later without changing the table schema.
3. The model stack should consume persisted features, not rebuild them inline.
4. Snapshot-partitioned outputs reduce accidental overwrite risk and simplify
   registry lineage.
5. No-lookahead needs explicit test coverage at the feature boundary.

## Consequences

- WP8 and later can consume stable feature tables with consistent hashes.
- A future paper-compatible CPD backend can be added behind the same
  `cpd_daily` schema by introducing a new `cpd_method`.
- Feature builds gain an explicit QA/reporting surface for completeness,
  validity, and clipping.

## Non-goals

- No model training
- No TSMOM signal generation
- No portfolio construction
- No broker integration
- No vendor/API calls
