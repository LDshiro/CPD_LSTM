# Decision 0004: Fix monitoring and kill-switch layer for v1.0

- Status: accepted
- Date: 2026-04-17
- Supersedes: none

## Context

Step 15 requires a deterministic control plane so that Shadow operations do not depend on ad hoc human judgement.
The repository already fixed:

- champion / fallback roles
- risk and cost layer
- instrument master
- shadow-first deployment mode

What remained under-specified was how monitoring outputs should be converted into an operational action.

## Decision

We fix v1.0 monitoring as follows.

1. Monitoring emits one of four control actions: `run_cpd_lstm`, `fallback_tsmom`, `hold`, `reduce_only`.
2. Action precedence is fixed as `reduce_only > hold > fallback_tsmom > run_cpd_lstm`.
3. Data-quality failures that compromise raw tradability force `hold`.
4. Champion-specific failures force `fallback_tsmom`.
5. Broker ambiguity and hard risk breaches force `reduce_only`.
6. Thresholds are config-driven and stored in `config/settings.base.yml`.

## Consequences

### Positive

- Shadow operations become deterministic
- fallback trigger tests can be automated
- risk / broker conditions override model quality by construction
- the control plane is simple enough to serialize to JSON or dashboards

### Trade-offs

- `hold` and `reduce_only` are conservative and may skip profitable rebalances
- strategy-health thresholds are intentionally simple in v1.0
- alert thresholds will need paper/live recalibration later

## Follow-up

- Step 16: emit monitoring decisions in the daily shadow batch
- Step 17: add operator runbook and incident review cadence
