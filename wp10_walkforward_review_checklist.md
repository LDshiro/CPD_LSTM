# WP10 Review Checklist — Walk-forward Evaluation / OOS Validation

Use this checklist after Codex implements WP10.

## Repository hygiene

- [ ] WP4-WP9 tests still pass.
- [ ] New WP10 tests are offline and deterministic.
- [ ] No vendor/API/broker calls occur in normal tests or smoke targets.
- [ ] README has a clear WP10 section with commands.
- [ ] ADR `0010-walkforward-evaluation-v1.md` exists.
- [ ] `docs/walkforward_eval_v1.md` exists.

## CLI

- [ ] `python -m cpdshadow.cli research walkforward plan ...` works.
- [ ] `python -m cpdshadow.cli research walkforward run ...` works on synthetic smoke data.
- [ ] `python -m cpdshadow.cli research walkforward evaluate-signals ...` works when signals already exist.
- [ ] `python -m cpdshadow.cli research walkforward qa ...` catches structural problems.
- [ ] `python -m cpdshadow.cli research walkforward report ...` regenerates JSON/Markdown reports.
- [ ] `make wp10-walkforward-smoke-offline` passes.

## Split logic

- [ ] Folds are quarterly by default.
- [ ] Production defaults are 10y train / 2y validation / next quarter OOS.
- [ ] Smoke parameters can shorten train/validation requirements without changing production defaults.
- [ ] Train, validation, and OOS windows do not overlap.
- [ ] Samples with labels outside their segment are excluded.
- [ ] Fold IDs are stable and deterministic.

## No-lookahead

- [ ] Standardizer is fit only on training rows.
- [ ] Reversal thresholds are computed only from train+validation rows.
- [ ] OOS features use dates <= signal date.
- [ ] OOS PnL uses the next observed return after the signal date.
- [ ] Mutating future rows does not change earlier folds or earlier metrics.

## CPD-LSTM integration

- [ ] WP9 model training code is reused, not duplicated.
- [ ] Each fold gets its own model artifact.
- [ ] Fold artifacts include config, seed, train manifest, metrics, and model weights.
- [ ] CPD-LSTM OOS signals use `strategy_id=cpd_lstm`.
- [ ] Model IDs encode run/fold or are otherwise unique and traceable.

## TSMOM integration

- [ ] WP8 TSMOM code is reused.
- [ ] TSMOM OOS dates and roots exactly match CPD-LSTM after filtering.
- [ ] TSMOM uses `strategy_id=tsmom` and `model_id=tsmom_v1`.
- [ ] TSMOM remains available even if CPD-LSTM training fails.

## Portfolio / PnL evaluation

- [ ] Both strategies use the same NAV.
- [ ] Both strategies use the same target volatility.
- [ ] Both strategies use the same cost model.
- [ ] Costs are charged on contract turnover.
- [ ] PnL alignment is explicitly tested.
- [ ] Missing next-return rows are excluded consistently for both strategies.
- [ ] Output includes fold metrics and aggregate metrics.

## Reversal bucket

- [ ] Events are defined by `cpd21_score` or `cpd63_score` above train+val p95.
- [ ] A 5-root-observation cooldown is applied by default.
- [ ] Horizons [1, 5, 20] are computed.
- [ ] CPD-LSTM minus TSMOM event performance is reported.
- [ ] Raw and deduplicated event counts are visible.

## Gates

- [ ] `gates.json` exists.
- [ ] Performance gate failures do not crash the run.
- [ ] Structural failures still fail QA.
- [ ] Gates include full OOS Sharpe, last 8 quarters Sharpe, max DD vs TSMOM, reversal 5d/20d diff, cost-to-gross-PnL, and min folds.

## Output files

- [ ] `walkforward_windows.parquet` exists.
- [ ] `fold_metrics.parquet` exists.
- [ ] `aggregate_metrics.parquet` exists.
- [ ] `oos_signals_daily.parquet` exists.
- [ ] `oos_targets_daily.parquet` exists.
- [ ] `oos_pnl_daily.parquet` exists.
- [ ] `reversal_events.parquet` exists.
- [ ] `reversal_bucket_metrics.parquet` exists.
- [ ] `gates.json` exists.
- [ ] `manifest.json` exists.
- [ ] `reports/walkforward_report.json` exists.
- [ ] `reports/walkforward_report.md` exists.

## Idempotency and lineage

- [ ] Re-running synthetic smoke with the same inputs produces the same key outputs, ignoring timestamps.
- [ ] Outputs include `run_id`, `fold_id`, `snapshot_id`, `feature_set_id`, `series_id`, `config_hash`, and `model_id` where applicable.
- [ ] The report records warnings and skipped folds.
- [ ] The manifest records input paths and hashes where practical.
