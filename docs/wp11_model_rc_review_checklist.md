# WP11 Review Checklist - Model RC / Promotion Decision v1

## Scope

- [ ] WP11 does not train models.
- [ ] WP11 does not recompute walk-forward outputs.
- [ ] WP11 does not call Databento, IBKR, broker, or network services.
- [ ] WP11 does not create target contracts or orders.
- [ ] WP11 does not approve paper or live trading.

## Decisions

- [ ] Only `shadow_candidate`, `defer_research`, `reject_candidate`, or `tsmom_only` is emitted.
- [ ] `shadow_candidate` includes `human_approval_required=true`.
- [ ] TSMOM-only is handled as a valid decision path.

## Release Package

- [ ] Required JSON and Markdown files exist.
- [ ] Manifest and decision release IDs match.
- [ ] SHA-256 hashes are deterministic.
- [ ] Gate results include all configured hard gates.
- [ ] Non-candidate release reports explain the next step.

## Tests

- [ ] Passing synthetic evidence yields `shadow_candidate`.
- [ ] Failing evidence does not yield `shadow_candidate`.
- [ ] QA catches malformed packages.
- [ ] `make test` and `make wp11-model-rc-smoke-offline` pass in a prepared environment.

