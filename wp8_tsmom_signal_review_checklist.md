# WP8 Review Checklist — TSMOM Fallback / Signal Infrastructure v1

Use this checklist after Codex implements WP8.

## 1. Scope control

- [ ] WP8 did not add vendor/API/IBKR/network calls.
- [ ] WP8 did not implement CPD-LSTM training or inference.
- [ ] WP8 did not implement broker order intents or paper/live execution.
- [ ] WP8's canonical output is `signals_daily`, not `targets_daily`.

## 2. Formula correctness

- [ ] Formula is exactly `mean(sign(ret_21), sign(ret_63), sign(ret_252))`.
- [ ] `sign(0) == 0`.
- [ ] Equal weights are used: one third each.
- [ ] No CPD features, MACD features, or root-specific weights are used.
- [ ] No partial-horizon signal is produced in v1.
- [ ] Signals are clipped to `[-1, 1]`.

## 3. Validity rules

- [ ] `is_complete=false` invalidates the row.
- [ ] `warmup_status != 'ok'` invalidates the row.
- [ ] Missing/non-finite `ret_21`, `ret_63`, or `ret_252` invalidates the row.
- [ ] Valid rows carry `feature_hash` through to `signals_daily`.
- [ ] Invalid rows have `signal_raw=null`, `signal_clipped=null`, `is_valid=false`, and non-empty `invalid_reason`.
- [ ] Valid rows have `invalid_reason=null`.

## 4. Schema and storage

- [ ] Output columns match WP3 `signals_daily`.
- [ ] Primary key uniqueness is enforced: `run_id, strategy_id, as_of_date, root`.
- [ ] Output is stably sorted by `as_of_date, root`.
- [ ] Recommended partitioning is implemented or documented.
- [ ] Parquet writing supports an alternate temp output directory for tests.

## 5. Determinism and no-lookahead

- [ ] Repeated run with same input/config/run_id/created_at produces identical dataframe content.
- [ ] Input row order does not change output.
- [ ] Changing a future feature row does not change earlier signals.
- [ ] Signal generation uses only the row's existing `features_daily` values.

## 6. Formula artifact and registry

- [ ] `artifacts/strategies/tsmom_v1/formula.json` is written.
- [ ] Formula artifact JSON is canonicalized.
- [ ] `formula.sha256` is stable across repeated runs.
- [ ] If registry persistence exists, a `model_registry` row for `tsmom_v1` is produced.
- [ ] If registry persistence does not exist, a pure function returns the expected row and is tested.

## 7. CLI and QA

- [ ] `python -m cpdshadow.cli signals tsmom build ...` works.
- [ ] `python -m cpdshadow.cli signals qa ...` works.
- [ ] QA JSON and Markdown reports are generated.
- [ ] QA includes row counts, valid/invalid counts, invalid reasons, root coverage, and signal distribution.
- [ ] QA warns on abnormal invalid coverage.

## 8. Tests

- [ ] Exact formula examples are tested.
- [ ] Warmup/incomplete/nonfinite invalid cases are tested.
- [ ] Schema validation is tested.
- [ ] Formula hash stability is tested.
- [ ] CLI integration writes Parquet and QA artifacts.
- [ ] `make wp8-signal-smoke-offline` passes without local secrets.
- [ ] `make test` passes.

## 9. Integration smoke

- [ ] If Step 11 sizing API is available, a small synthetic features→signals→targets smoke exists.
- [ ] If skipped, skip reason is explicit and acceptable.
- [ ] The target smoke does not persist production `targets_daily` as part of WP8.

## 10. Final review decision

- [ ] Approve WP8 and proceed to WP9.
- [ ] Or request fixes for the listed blockers.
