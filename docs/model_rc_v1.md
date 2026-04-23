# Model RC / Promotion Decision v1

## Purpose

WP11 freezes a CPD-LSTM candidate as a Shadow-only model release candidate, or records that the
candidate should be deferred, rejected, or replaced by TSMOM-only operation.

WP11 is not research recomputation and not live-trading approval. It packages evidence and lineage
from WP10 and WP9 so a human reviewer can audit the decision.

## Inputs

- WP10 walk-forward output directory.
- WP9 CPD-LSTM candidate model artifact directory.
- `config/model_release_gates.yml`.
- Optional settings and schema snapshots.

## Decisions

- `shadow_candidate`: fixed gates passed; candidate can move to Shadow review after human approval.
- `defer_research`: evidence is incomplete or mixed.
- `reject_candidate`: structural or material evidence fails.
- `tsmom_only`: CPD-LSTM is not acceptable; fallback remains viable.

Every decision includes `human_approval_required=true`. WP11 never emits paper or live approval.

## Release Package

Release directories are written under:

```text
artifacts/releases/model_rc/<release_id>/
```

Required files:

- `manifest.json`
- `promotion_decision.json`
- `gate_results.json`
- `metrics_summary.json`
- `artifact_hashes.json`
- `input_manifest.json`
- `model_card.md`
- `release_report.md`

## CLI

```bash
python -m cpdshadow.cli model-rc package \
  --walkforward-dir data/research/walkforward/<run_id> \
  --candidate-model-dir artifacts/models/cpd_lstm/<model_id> \
  --release-id rc_<date>_<model> \
  --output-dir artifacts/releases/model_rc

python -m cpdshadow.cli model-rc qa \
  --release-dir artifacts/releases/model_rc/rc_<date>_<model>
```

## Limitations

WP11 does not train models, recompute walk-forward results, generate signals, size positions,
generate orders, connect to brokers, or approve live trading.

