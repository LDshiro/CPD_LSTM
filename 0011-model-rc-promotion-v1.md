# Decision 0011 — Model RC / Promotion Decision v1

## Status

Proposed for WP11 implementation.

## Context

WP10 creates walk-forward evaluation evidence comparing CPD-LSTM against TSMOM. Before any Shadow integration, the project needs a deterministic way to freeze or reject a CPD-LSTM candidate without allowing discretionary, post-hoc interpretation of backtest results.

The release step must preserve lineage back to the model artifact, feature set, data snapshot, configuration, and walk-forward evaluation. It must also make clear that a model release candidate is not paper or live trading approval.

## Decision

Create a WP11 release process that:

1. Consumes WP10 walk-forward outputs.
2. Consumes WP9 model artifact metadata.
3. Evaluates fixed gates from `config/model_release_gates.yml`.
4. Emits exactly one decision state:
   - `shadow_candidate`
   - `defer_research`
   - `reject_candidate`
   - `tsmom_only`
5. Writes a release directory under `artifacts/releases/model_rc/<release_id>/`.
6. Requires human approval for any `shadow_candidate`.
7. Never approves live or paper trading.

## Rationale

CPD-LSTM is more complex than TSMOM. It should not be admitted into Shadow operation merely because one backtest headline metric looks attractive. The model must show:

- adequate full-period OOS performance,
- adequate recent performance,
- drawdown not materially worse than TSMOM,
- valid data and no-lookahead checks,
- and CPD-specific value in reversal buckets.

The release directory makes the decision auditable and repeatable.

## Consequences

Positive:

- Avoids ad hoc promotion decisions.
- Creates stable handoff to Shadow integration.
- Preserves CPD-LSTM and TSMOM comparison evidence.
- Makes fallback behavior explicit.

Trade-offs:

- A promising model may be deferred if reversal event evidence is sparse.
- Release packaging adds implementation work before Shadow integration.
- Gate thresholds may need later revision, but v1 freezes them to avoid moving targets.

## Non-decisions

- WP11 does not choose final live trading capital.
- WP11 does not implement broker integration.
- WP11 does not change the CPD-LSTM architecture.
- WP11 does not replace WP15 monitoring.

## Acceptance criteria

- `make test` passes.
- `make wp11-model-rc-smoke-offline` passes.
- Passing synthetic evidence creates a `shadow_candidate` release.
- Failing synthetic evidence does not create a `shadow_candidate` release.
- Release QA catches malformed packages.
- Every release includes `human_approval_required=true`.
