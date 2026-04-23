# WP11 Codex Instructions — Model RC / Promotion Decision v1

## 0. Mission

Implement **WP11: Model RC / Promotion Decision v1** for the CPD-LSTM Shadow Trading System.

WP11 consumes WP10 walk-forward evaluation outputs and WP9 model artifacts, then produces a deterministic, auditable **Shadow model release candidate** package. WP11 must not train models, tune hyperparameters, generate new signals, place orders, connect to brokers, or declare anything live-tradable.

The goal is to answer one question in a reproducible way:

> Given the fixed WP10 evaluation evidence, should the CPD-LSTM candidate be frozen as the Shadow candidate, deferred for further research, rejected, or should the system remain TSMOM-only?

This work package is about **decision hygiene, lineage, reproducibility, and release packaging**.

---

## 1. Scope

### In scope

1. Load WP10 evaluation artifacts.
2. Load or reference WP9 CPD-LSTM model artifact metadata.
3. Load or reference WP8 TSMOM fallback artifact metadata.
4. Evaluate fixed promotion gates.
5. Produce deterministic release artifacts under `artifacts/releases/model_rc/<release_id>/`.
6. Create a machine-readable promotion decision.
7. Create a human-readable model card and release report.
8. Add CLI commands for evaluate/package/QA.
9. Add offline synthetic tests.
10. Update README/Makefile with WP11 commands.

### Out of scope

- No model training.
- No walk-forward recomputation.
- No hyperparameter search.
- No data ingest.
- No broker, IBKR, paper, or live connection.
- No order generation.
- No target-contract sizing.
- No automatic live approval.
- No mutation of WP10 source artifacts.

---

## 2. Branch and implementation discipline

Create a new branch:

```bash
git checkout -b wp11-model-rc-promotion
```

Keep all tests offline by default. Do not require Databento, IBKR, CUDA, or network access.

All outputs must be deterministic when the same inputs, config, and release ID are provided.

---

## 3. Expected new/modified files

Codex may adjust file names if the existing repo has better conventions, but keep the semantic contract intact.

```text
src/cpdshadow/model_release.py
src/cpdshadow/promotion.py
src/cpdshadow/hash_utils.py              # only if no suitable helper exists
src/cpdshadow/cli.py                     # add model-rc subcommands
config/model_release_gates.yml
docs/model_rc_v1.md
docs/decisions/0011-model-rc-promotion-v1.md
docs/wp11_model_rc_review_checklist.md
tests/unit/test_model_release.py
tests/unit/test_promotion_gates.py
tests/smoke/test_wp11_model_rc_smoke.py
Makefile
README.md
```

If the repo already has shared utilities for hashing, config loading, JSON writing, or CLI registration, reuse them rather than duplicating code.

---

## 4. Inputs

WP11 should accept paths rather than assuming one fixed local layout. Defaults may follow the repo conventions.

### Required inputs

| Input | Meaning |
|---|---|
| `--walkforward-dir` | WP10 output directory containing aggregate metrics, gates, reversal metrics, OOS results, and report files. |
| `--candidate-model-dir` | WP9 CPD-LSTM artifact directory containing `model.pt`, `config.json`, `standardizer.json`, `metrics.json`, `train_manifest.json`, etc. |
| `--feature-set-id` | Usually `features_v1`. |
| `--strategy-id` | Usually `cpd_lstm`. |
| `--baseline-strategy-id` | Usually `tsmom`. |
| `--release-id` | Optional. If omitted, create deterministic timestamp/hash-based ID. For tests, allow fixed release ID. |

### Optional inputs

| Input | Meaning |
|---|---|
| `--tsmom-artifact-dir` | WP8 TSMOM formula artifact directory, if present. |
| `--settings-path` | Usually `config/settings.base.yml`. |
| `--data-schema-path` | Usually `config/data_schema.yml`. |
| `--output-dir` | Default `artifacts/releases/model_rc`. |
| `--decision-mode` | `strict` or `advisory`. Default `strict`. |
| `--allow-warnings` | Allow warnings without failing package creation. |

---

## 5. Expected WP10 artifact contract

WP10 implementations may name files slightly differently. WP11 should support the canonical names below and fail clearly if required evidence is absent.

Canonical expected files:

```text
<walkforward-dir>/aggregate_metrics.json
<walkforward-dir>/gates.json
<walkforward-dir>/reversal_bucket_metrics.json
<walkforward-dir>/window_metrics.parquet or .json
<walkforward-dir>/walkforward_report.md
<walkforward-dir>/oos_pnl.parquet or .json
<walkforward-dir>/manifest.json              # if available
```

The following fields should be read when present:

```text
cpd_lstm.net_sharpe
cpd_lstm.recent_8q_net_sharpe
cpd_lstm.max_drawdown
cpd_lstm.annualized_return
cpd_lstm.realized_vol
cpd_lstm.turnover
cpd_lstm.avg_modeled_cost_bps

tsmom.net_sharpe
tsmom.recent_8q_net_sharpe
tsmom.max_drawdown
tsmom.annualized_return
tsmom.realized_vol
tsmom.turnover

reversal_bucket.cpd_lstm.event_pnl_5d
reversal_bucket.tsmom.event_pnl_5d
reversal_bucket.cpd_lstm.event_pnl_20d
reversal_bucket.tsmom.event_pnl_20d
reversal_bucket.event_count

data_quality.missing_feature_rate
data_quality.oos_root_date_coverage
structural.no_lookahead_checks_passed
structural.common_universe_check_passed
```

If the existing WP10 output has a different shape, implement a small adapter that normalizes it into an internal `PromotionEvidence` dataclass.

---

## 6. Promotion states

WP11 must output exactly one of these states:

| State | Meaning |
|---|---|
| `shadow_candidate` | CPD-LSTM passed hard gates and can be frozen for Shadow candidate use after human review. |
| `defer_research` | Evidence is insufficient or mixed; do not freeze CPD-LSTM yet. |
| `reject_candidate` | Evidence structurally or materially fails. |
| `tsmom_only` | CPD-LSTM is not acceptable; proceed with TSMOM fallback only for later Shadow work. |

Do not output `paper_approved`, `live_approved`, or similar states in WP11.

Even when `shadow_candidate` is produced, include:

```json
"human_approval_required": true
```

---

## 7. Promotion gates

Create `config/model_release_gates.yml` with the following defaults.

```yaml
model_release:
  version: model_release_gates_v1
  status_outputs:
    - shadow_candidate
    - defer_research
    - reject_candidate
    - tsmom_only

  hard_gates:
    min_full_oos_net_sharpe: 0.90
    min_recent_8q_net_sharpe: 0.60
    max_drawdown_vs_tsmom_multiple: 1.50
    max_missing_feature_rate: 0.01
    require_common_oos_universe: true
    require_no_lookahead_checks: true
    require_model_artifact_hashes: true
    require_feature_set_match: true
    require_signal_contract_match: true

  reversal_gates:
    min_event_count: 20
    require_5d_pnl_ge_tsmom: true
    require_20d_pnl_ge_tsmom: true

  warning_gates:
    max_turnover_vs_tsmom_multiple: 2.50
    max_avg_modeled_cost_bps: 3.00
    min_oos_quarters: 8
    min_positive_oos_window_rate: 0.50

  decision_policy:
    any_hard_fail: reject_candidate
    insufficient_reversal_events: defer_research
    hard_pass_with_reversal_pass: shadow_candidate
    hard_pass_reversal_mixed: defer_research
    hard_fail_but_tsmom_valid: tsmom_only
```

The gate evaluator must produce individual gate results:

```json
{
  "gate_id": "min_full_oos_net_sharpe",
  "severity": "hard",
  "status": "pass|fail|warn|na",
  "observed": 0.93,
  "threshold": 0.90,
  "message": "..."
}
```

Avoid throwing exceptions for performance gate failures. Performance failures are valid outcomes. Throw exceptions only for malformed inputs, impossible schemas, unreadable artifacts, or programming errors.

---

## 8. Release package layout

Write the release under:

```text
artifacts/releases/model_rc/<release_id>/
```

Required files:

```text
manifest.json
promotion_decision.json
gate_results.json
metrics_summary.json
artifact_hashes.json
model_card.md
release_report.md
input_manifest.json
```

Optional but useful:

```text
config_snapshot.yml
feature_schema_snapshot.yml
walkforward_report.md             # copied or referenced with hash
aliases/shadow_candidate.json      # only if status == shadow_candidate and --write-alias is set
```

Do not copy large model files by default unless explicitly requested. Prefer storing their paths and hashes. If copying is implemented, make it optional with `--copy-artifacts`.

---

## 9. Manifest schema

`manifest.json` must include at least:

```json
{
  "schema_version": "model_rc_manifest_v1",
  "release_id": "rc_20260423_example",
  "created_at_utc": "2026-04-23T00:00:00Z",
  "status": "shadow_candidate",
  "human_approval_required": true,
  "strategy_id": "cpd_lstm",
  "model_family": "cpd_lstm_v1",
  "model_id": "...",
  "feature_set_id": "features_v1",
  "signal_contract_version": "signals_daily_v1",
  "walkforward_eval_id": "...",
  "candidate_model_dir": "...",
  "walkforward_dir": "...",
  "config_hash": "...",
  "feature_schema_hash": "...",
  "artifact_hashes_file": "artifact_hashes.json",
  "gate_results_file": "gate_results.json",
  "promotion_decision_file": "promotion_decision.json"
}
```

---

## 10. Promotion decision schema

`promotion_decision.json` must include:

```json
{
  "schema_version": "promotion_decision_v1",
  "release_id": "...",
  "decision": "shadow_candidate|defer_research|reject_candidate|tsmom_only",
  "human_approval_required": true,
  "summary": "...",
  "hard_gate_status": "pass|fail",
  "reversal_gate_status": "pass|fail|insufficient|mixed",
  "warning_count": 0,
  "failure_count": 0,
  "recommended_next_step": "...",
  "fallback_strategy_id": "tsmom",
  "created_at_utc": "..."
}
```

---

## 11. Model card requirements

`model_card.md` should be readable without opening JSON.

Include:

1. Release ID.
2. Decision.
3. Intended use: Shadow candidate only.
4. Non-use: not approved for live trading.
5. Candidate model path and hash.
6. Feature set.
7. Training/evaluation summary from WP10.
8. CPD-LSTM vs TSMOM summary.
9. Reversal bucket summary.
10. Known limitations.
11. Required fallback: TSMOM.
12. Required monitoring before any future paper/live step.
13. Human approval status.

Do not present backtest performance as guaranteed future performance.

---

## 12. CLI

Add subcommands under the existing CLI style.

Suggested commands:

```bash
python -m cpdshadow.cli model-rc evaluate \
  --walkforward-dir artifacts/walkforward/<eval_id> \
  --candidate-model-dir artifacts/models/<model_id> \
  --settings-path config/settings.base.yml \
  --gates-path config/model_release_gates.yml \
  --release-id rc_test \
  --output-dir artifacts/releases/model_rc
```

```bash
python -m cpdshadow.cli model-rc package \
  --walkforward-dir artifacts/walkforward/<eval_id> \
  --candidate-model-dir artifacts/models/<model_id> \
  --release-id rc_test \
  --output-dir artifacts/releases/model_rc
```

```bash
python -m cpdshadow.cli model-rc qa \
  --release-dir artifacts/releases/model_rc/rc_test
```

If evaluate and package are redundant in the existing architecture, it is acceptable to implement one `build` command and one `qa` command, as long as the outputs above are produced.

---

## 13. Makefile target

Add:

```makefile
wp11-model-rc-smoke-offline:
	python -m cpdshadow.cli model-rc package \
		--walkforward-dir tests/fixtures/wp10_walkforward_pass \
		--candidate-model-dir tests/fixtures/wp9_model_candidate \
		--release-id rc_smoke \
		--output-dir artifacts/test_releases/model_rc
	python -m cpdshadow.cli model-rc qa \
		--release-dir artifacts/test_releases/model_rc/rc_smoke
```

Use synthetic fixtures. Do not require real market data.

---

## 14. Tests

Add tests for at least these cases:

### Unit tests

1. Passing evidence produces `shadow_candidate`.
2. Low full OOS Sharpe produces `reject_candidate` or `tsmom_only` depending on TSMOM validity.
3. Low recent 8Q Sharpe blocks `shadow_candidate`.
4. Excess drawdown relative to TSMOM blocks `shadow_candidate`.
5. Missing feature rate above threshold blocks `shadow_candidate`.
6. Insufficient reversal event count produces `defer_research` if other hard gates pass.
7. Reversal 5d/20d underperformance produces `defer_research`.
8. Warnings do not block release package creation.
9. Missing required input file fails clearly.
10. Hash computation is deterministic.
11. Re-running package with same inputs/release ID produces identical JSON except for explicitly allowed timestamp fields. For tests, allow fixed `created_at_utc`.

### Smoke tests

1. Synthetic WP10 pass fixture creates full release directory.
2. `qa` passes on the synthetic release.
3. No network or broker imports are required.

---

## 15. QA rules

`model-rc qa` should verify:

1. Required files exist.
2. JSON files parse.
3. Manifest and decision release IDs match.
4. Referenced hash file exists.
5. `human_approval_required` is true.
6. Decision is one of allowed states.
7. No live/paper approval field is true.
8. Gate results contain all configured hard gates.
9. If `status == shadow_candidate`, all hard gates are pass and reversal status is pass.
10. If `status != shadow_candidate`, release report explains why.

---

## 16. Implementation notes

### Dataclasses

Suggested dataclasses:

```python
@dataclass(frozen=True)
class PromotionEvidence:
    cpd_lstm: dict[str, float | int | str | bool]
    tsmom: dict[str, float | int | str | bool]
    reversal_bucket: dict[str, float | int | str | bool]
    data_quality: dict[str, float | int | str | bool]
    structural: dict[str, float | int | str | bool]
    source_paths: dict[str, str]

@dataclass(frozen=True)
class GateResult:
    gate_id: str
    severity: str
    status: str
    observed: object
    threshold: object
    message: str

@dataclass(frozen=True)
class PromotionDecision:
    decision: str
    hard_gate_status: str
    reversal_gate_status: str
    warning_count: int
    failure_count: int
    summary: str
    recommended_next_step: str
```

### Deterministic JSON

Write JSON with sorted keys and stable indentation:

```python
json.dumps(obj, ensure_ascii=False, sort_keys=True, indent=2)
```

### Hashing

Hash binary files by bytes. Hash normalized config dictionaries via sorted JSON. Use SHA-256.

### Time handling

Allow `--created-at-utc` for deterministic tests. Otherwise use current UTC.

---

## 17. README update

Add a README section:

```markdown
## WP11 Model RC / Promotion Decision

WP11 packages a CPD-LSTM candidate for Shadow-only review using WP10 walk-forward evidence.

Example:

```bash
python -m cpdshadow.cli model-rc package \
  --walkforward-dir artifacts/walkforward/<eval_id> \
  --candidate-model-dir artifacts/models/<model_id> \
  --release-id rc_<date>_<model> \
  --output-dir artifacts/releases/model_rc

python -m cpdshadow.cli model-rc qa \
  --release-dir artifacts/releases/model_rc/rc_<date>_<model>
```

WP11 never approves live trading. It produces a Shadow candidate decision that still requires human approval.
```

---

## 18. Completion criteria

WP11 is complete when:

1. `make test` passes.
2. `make wp11-model-rc-smoke-offline` passes.
3. A synthetic passing fixture produces `shadow_candidate`.
4. A synthetic failing fixture does not produce `shadow_candidate`.
5. Required release files are generated.
6. QA command catches malformed releases.
7. README and docs are updated.
8. The implementation does not require Databento, IBKR, CUDA, or network access.

---

## 19. Review focus

After Codex implements WP11, review especially:

1. Are performance gate failures treated as outcomes, not exceptions?
2. Does the release package preserve lineage back to WP10 and WP9?
3. Is `shadow_candidate` clearly not live approval?
4. Does `tsmom_only` remain a valid path?
5. Are hashes deterministic?
6. Can a third party reproduce the decision from the release directory alone?
