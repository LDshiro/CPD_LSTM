# WP6 Codex Instructions — Continuous Builder / Backward Ratio-Adjusted Series v1

This file mirrors the repo-root instruction `codex_wp6_continuous_builder_instructions.md` and exists so WP6 implementation guidance is available under `docs/`.

## Canonical scope

- Build `continuous_daily` from `contracts_daily`, `lead_map`, and `roll_events`
- `series_id = v1_back_ratio_settle`
- `builder_version = continuous_builder_v1`
- method = backward ratio-adjusted settlement series
- signal-only output, never broker-facing

## Hard rules

- Do not use Databento continuous symbols or vendor continuous prices.
- Use only the selected `lead_map.lead_raw_symbol` price from `contracts_daily`.
- For each date `d`, `adj_factor(d)` includes only roll ratios whose `effective_date > d`.
- The roll effective date itself is left unadjusted on the new contract scale.
- Future roll events outside the build end date must not affect earlier factors.
- Missing or nonpositive roll ratios are fatal in strict mode.
- Missing raw prices must create unusable rows with quality flags, not silent fills.

## Required interfaces

- `python -m cpdshadow.cli continuous build ...`
- `python -m cpdshadow.cli continuous qa ...`
- `make wp6-continuous-smoke-offline`

## Required outputs

- `data/curated/continuous_daily/series_id=<series_id>/snapshot_id=<snapshot_id>/year=<YYYY>/...`
- `artifacts/wp6/continuous_qa_<snapshot_id>_<series_id>.json`
- `artifacts/wp6/continuous_qa_<snapshot_id>_<series_id>.md`

## Core test example

Single-roll canonical case:

```text
D3 old=102, new=204, ratio=2.0
D4 new=206
```

Expected:

```text
adj(D3)=204
adj(D4)=206
return(D4)=206/204-1
```
