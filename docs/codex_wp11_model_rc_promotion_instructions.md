# WP11 Codex Instructions - Model RC / Promotion Decision v1

WP11 consumes fixed WP10 walk-forward evidence and WP9 model artifact metadata, evaluates fixed
release gates, and writes an auditable Shadow-only release candidate package under
`artifacts/releases/model_rc/<release_id>/`.

WP11 must not train models, recompute walk-forward outputs, generate signals, create target
contracts, connect to Databento or IBKR, generate orders, or approve paper/live trading.

Allowed decisions are exactly:

- `shadow_candidate`
- `defer_research`
- `reject_candidate`
- `tsmom_only`

`shadow_candidate` is still Shadow-only and always requires human approval.

Required release files:

- `manifest.json`
- `promotion_decision.json`
- `gate_results.json`
- `metrics_summary.json`
- `artifact_hashes.json`
- `input_manifest.json`
- `model_card.md`
- `release_report.md`

Use deterministic JSON and SHA-256 hashes. Performance gate failures are valid decision outcomes,
not program errors. Structural failures, malformed inputs, missing required artifacts, and invalid
release packages should fail clearly.

