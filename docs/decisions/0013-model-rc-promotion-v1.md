# 0013 - Model RC / Promotion Decision v1

## Status

Accepted for WP11 implementation.

## Context

WP10 creates walk-forward evidence comparing CPD-LSTM against TSMOM. Before any Shadow integration,
the project needs a deterministic way to freeze, defer, reject, or route around a candidate without
post-hoc interpretation of backtest results.

The historical WP11 draft used decision number `0011`, but `0011` is already the WP9 CPD-LSTM model
decision and `0012` is WP10. This canonical decision note therefore uses `0013`.

## Decision

Implement a Shadow-only model release candidate package that:

- consumes fixed WP10 evidence and WP9 model artifact metadata,
- evaluates gates from `config/model_release_gates.yml`,
- emits exactly one of `shadow_candidate`, `defer_research`, `reject_candidate`, `tsmom_only`,
- writes deterministic JSON/Markdown under `artifacts/releases/model_rc/<release_id>/`,
- always requires human approval for `shadow_candidate`,
- never approves paper or live trading.

## Rationale

CPD-LSTM is more complex than TSMOM, so it must pass full-period performance, recent performance,
drawdown, data quality, artifact integrity, and reversal-bucket gates before it can be considered
for Shadow review. The package makes the decision auditable and reproducible.

## Consequences

Positive:

- Promotion decisions become deterministic and reviewable.
- TSMOM-only remains an explicit safe path.
- Release packages preserve model/evaluation lineage.

Tradeoffs:

- Promising models can be deferred when reversal evidence is sparse.
- Gate thresholds may need later revision, but WP11 freezes v1 defaults to avoid moving targets.

