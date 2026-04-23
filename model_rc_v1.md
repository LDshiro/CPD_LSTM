# Model RC / Promotion Decision v1

## Purpose

WP11 freezes a CPD-LSTM candidate as a **Shadow-only model release candidate**, or records that the candidate should be deferred, rejected, or replaced by TSMOM-only operation.

WP11 is not a research step and not a live-trading approval step. It is a controlled packaging and decision step between walk-forward evaluation and Shadow pipeline integration.

## Inputs

WP11 consumes:

1. WP10 walk-forward evaluation outputs.
2. WP9 CPD-LSTM model artifacts.
3. WP8 TSMOM fallback artifact metadata, when available.
4. Fixed release gate configuration.
5. Project configuration and schema snapshots.

## Outputs

A release candidate directory:

```text
artifacts/releases/model_rc/<release_id>/
  manifest.json
  promotion_decision.json
  gate_results.json
  metrics_summary.json
  artifact_hashes.json
  input_manifest.json
  model_card.md
  release_report.md
```

## Allowed decisions

| Decision | Meaning | Next step |
|---|---|---|
| `shadow_candidate` | CPD-LSTM passed fixed gates and can be frozen for Shadow review. | Human review, then WP12/WP14 integration. |
| `defer_research` | Evidence is incomplete or mixed. | Improve data/model/evaluation; do not freeze. |
| `reject_candidate` | Candidate materially fails. | Do not use candidate; revisit research. |
| `tsmom_only` | CPD-LSTM is not acceptable; fallback remains viable. | Continue with TSMOM-only Shadow path if appropriate. |

No WP11 decision is live-trading approval.

## Gate philosophy

The gate system separates three issues:

1. **Structural validity** — no lookahead, common universe, correct artifact lineage.
2. **Performance sufficiency** — Sharpe, drawdown, OOS stability.
3. **CPD-specific value** — reversal bucket behavior versus TSMOM.

A model that performs well overall but does not improve reversal buckets should normally be deferred rather than frozen. The CPD-LSTM thesis is specifically that change-point information helps with trend reversals.

## Default hard gates

| Gate | Default threshold |
|---|---:|
| Full OOS net Sharpe | `>= 0.90` |
| Recent 8-quarter net Sharpe | `>= 0.60` |
| Max drawdown vs TSMOM | `<= 1.50x` |
| Missing feature rate | `< 1%` |
| Common OOS universe | required |
| No-lookahead checks | required |
| Model artifact hashes | required |
| Feature set match | required |
| Signal contract match | required |

## Reversal gates

| Gate | Default |
|---|---|
| Event count | `>= 20` |
| 5-day event PnL vs TSMOM | CPD-LSTM must be `>=` TSMOM |
| 20-day event PnL vs TSMOM | CPD-LSTM must be `>=` TSMOM |

If reversal event count is too small, the outcome should usually be `defer_research` rather than `shadow_candidate`.

## Warning gates

Warning gates should be visible but not automatically fatal:

- Excess turnover relative to TSMOM.
- High modeled cost.
- Too few OOS quarters.
- Low positive-window rate.

## Human approval

All release packages must include:

```json
"human_approval_required": true
```

This prevents automated transition from research evidence to operational candidate without review.

## Reproducibility requirements

A release candidate should be reproducible from:

1. The release directory.
2. The referenced WP10 evaluation artifacts.
3. The referenced WP9 model artifacts.
4. The configuration hashes.
5. The code commit, when available.

JSON must be deterministic, and artifact hashes must use SHA-256.

## Non-goals

WP11 does not:

- Train CPD-LSTM.
- Tune hyperparameters.
- Re-run walk-forward evaluation.
- Build features.
- Generate orders.
- Connect to a broker.
- Approve paper or live trading.
