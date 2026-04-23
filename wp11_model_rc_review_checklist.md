# WP11 Review Checklist — Model RC / Promotion Decision v1

Use this checklist after Codex implements WP11.

## 1. Scope control

- [ ] WP11 does not train models.
- [ ] WP11 does not recompute walk-forward windows.
- [ ] WP11 does not call Databento.
- [ ] WP11 does not call IBKR or broker code.
- [ ] WP11 does not create target contracts or orders.
- [ ] WP11 does not approve paper or live trading.

## 2. Inputs

- [ ] WP10 walk-forward directory is accepted as a path.
- [ ] WP9 candidate model artifact directory is accepted as a path.
- [ ] Missing required files fail with clear error messages.
- [ ] WP10 metrics are normalized into an internal evidence object.
- [ ] Different but reasonable WP10 JSON shapes can be adapted, or fail explicitly.

## 3. Decision states

- [ ] Only these states are emitted: `shadow_candidate`, `defer_research`, `reject_candidate`, `tsmom_only`.
- [ ] `shadow_candidate` always includes `human_approval_required=true`.
- [ ] There is no `live_approved` or `paper_approved` state.
- [ ] `tsmom_only` is handled as a valid path, not as a crash.

## 4. Gate evaluation

- [ ] Full OOS net Sharpe threshold is checked.
- [ ] Recent 8-quarter net Sharpe threshold is checked.
- [ ] CPD-LSTM drawdown relative to TSMOM is checked.
- [ ] Missing feature rate is checked.
- [ ] Common OOS universe check is included when available.
- [ ] No-lookahead structural check is included when available.
- [ ] Reversal event count is checked.
- [ ] 5-day and 20-day reversal bucket comparisons vs TSMOM are checked.
- [ ] Warning gates do not automatically block package creation.
- [ ] Performance failures are represented as gate results, not unhandled exceptions.

## 5. Release artifacts

- [ ] `manifest.json` exists.
- [ ] `promotion_decision.json` exists.
- [ ] `gate_results.json` exists.
- [ ] `metrics_summary.json` exists.
- [ ] `artifact_hashes.json` exists.
- [ ] `input_manifest.json` exists.
- [ ] `model_card.md` exists.
- [ ] `release_report.md` exists.
- [ ] Release files include consistent `release_id`.
- [ ] Release files include candidate model path and hashes.
- [ ] Release files include WP10 evaluation path and hashes.
- [ ] Release files include config and feature schema hashes when available.

## 6. Determinism and lineage

- [ ] JSON output uses stable key ordering.
- [ ] Hashes are SHA-256.
- [ ] Re-running with fixed `release_id` and fixed `created_at_utc` produces identical JSON.
- [ ] Model artifact hash changes when model bytes change.
- [ ] Config hash changes when gate config changes.
- [ ] The release can be audited without opening the original notebooks.

## 7. CLI

- [ ] `python -m cpdshadow.cli model-rc package ...` works.
- [ ] `python -m cpdshadow.cli model-rc qa ...` works.
- [ ] `model-rc qa` fails malformed releases.
- [ ] CLI supports deterministic `--release-id`.
- [ ] CLI supports deterministic `--created-at-utc` or equivalent for tests.

## 8. Tests

- [ ] Passing synthetic evidence yields `shadow_candidate`.
- [ ] Low Sharpe evidence does not yield `shadow_candidate`.
- [ ] Excess drawdown evidence does not yield `shadow_candidate`.
- [ ] Insufficient reversal events yields `defer_research` when other hard gates pass.
- [ ] Reversal underperformance yields `defer_research`.
- [ ] Missing required artifact fails clearly.
- [ ] QA catches missing required release files.
- [ ] `make test` passes.
- [ ] `make wp11-model-rc-smoke-offline` passes.

## 9. Documentation

- [ ] `docs/model_rc_v1.md` explains release semantics.
- [ ] `docs/decisions/0011-model-rc-promotion-v1.md` records the decision design.
- [ ] README has WP11 commands.
- [ ] Docs explicitly say WP11 is Shadow-only and not live approval.

## 10. Final acceptance

WP11 is accepted when the release package can answer, from files alone:

1. Which model was evaluated?
2. Which data/evaluation evidence was used?
3. Which gates passed or failed?
4. Why was the decision made?
5. What is the safe next step?
