# Decision 0003: Fix risk / cost / portfolio layer for v1.0

- Status: accepted
- Date: 2026-04-17
- Supersedes: none

## Context

Step 11 requires a concrete and testable portfolio transformation layer so that
Step 12 walk-forward evaluation and Step 14 broker integration can share the same sizing logic.

The repository already fixed the following at spec level:

- daily signal frequency
- CPD-LSTM champion / distributed TSMOM fallback
- 10% annualized target portfolio vol
- settlement-first data policy
- ex-cost Sharpe loss

What remained under-specified was:

- the exact units used in contract sizing
- how root / asset-class / margin caps are expressed
- how integer contract conversion is performed
- how modeled trade costs are attached to target contracts

## Decision

We fix v1.0 as follows.

1. Position sizing is based on **annualized dollar risk**, not mixed daily/annual units.
2. Root and asset-class caps are expressed as **absolute annualized ex-ante dollar risk relative to NAV**.
3. Margin soft/hard caps are evaluated on total initial margin requirement.
4. Integer contracts use half-away-from-zero rounding.
5. Post-rounding constraint checks reduce positions one contract at a time toward zero.
6. The cost model remains a conservative research default until paper/live measurements are available.

## Consequences

### Positive

- sizing is deterministic and unit-consistent
- CPD-LSTM and TSMOM can share the exact same target layer
- the risk layer can emit explicit diagnostics for shadow monitoring
- the modeled cost layer is simple enough to calibrate later

### Trade-offs

- root soft cap is mostly a backstop at 10% portfolio vol rather than a routine clamp
- cost assumptions are provisional and may be pessimistic or optimistic by contract
- integer post-processing is greedy rather than globally optimal

## Follow-up

- Step 12: wire the same layer into walk-forward evaluation
- Step 14: calibrate cost assumptions against paper fills and broker statements
- Step 15: alert on repeated pre-scale hard-cap breaches and realized cost drift
