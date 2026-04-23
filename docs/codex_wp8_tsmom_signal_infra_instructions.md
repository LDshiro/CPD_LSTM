# WP8 Codex Instructions — TSMOM Fallback / Signal Infrastructure v1

This file is the canonical in-repo copy of the WP8 instruction. The root-level
`codex_wp8_tsmom_signal_infra_instructions.md` remains as planning context, but
the implementation contract lives here for future maintenance.

## Goal

Consume WP7 `features_daily` and produce deterministic, no-lookahead
`signals_daily` for the formulaic TSMOM fallback strategy. WP8 also establishes
the reusable signal interface that WP9 model-backed strategies will implement.

## Hard constraints

- No Databento, IBKR, broker, or network calls.
- Use WP7 `features_daily` only.
- TSMOM v1 formula is exactly:
  - `mean(sign(ret_21), sign(ret_63), sign(ret_252))`
- Required validity:
  - `is_complete == true`
  - `warmup_status == "ok"`
  - `ret_21`, `ret_63`, `ret_252` finite
  - `feature_hash` present
- No partial horizon handling in v1.
- No CPD gating in v1.
- `signals_daily` is the only canonical persisted output of WP8.

## Output contract

`signals_daily` columns:

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

Physical layout:

```text
data/research/signals_daily/strategy_id=<strategy_id>/model_id=<model_id>/run_id=<run_id>/year=<YYYY>/
```

QA artifacts:

```text
artifacts/wp8/signals_qa_<run_id>.json
artifacts/wp8/signals_qa_<run_id>.md
```

Formula artifact:

```text
artifacts/strategies/tsmom_v1/formula.json
artifacts/strategies/tsmom_v1/formula.sha256
```

## CLI

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

```bash
python -m cpdshadow.cli signals qa \
  --signals-path data/research/signals_daily \
  --run-id infer_tsmom_2024
```

## Acceptance

- `make test` passes without network.
- `make wp8-signal-smoke-offline` passes without network.
- Signal generation is deterministic.
- No-lookahead tests pass.
- Formula artifact hash is stable.
- QA JSON and Markdown reports are generated.
