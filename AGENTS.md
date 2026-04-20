# AGENTS.md

## Repo intent

This repository implements a daily global futures trading system for CPD-LSTM shadow operations.
The production goal is **stable and reproducible shadow execution**, not aggressive feature growth.

## Read first

Before any code changes, read:

1. `docs/spec_v1.md`
2. `docs/repo_conventions.md`

## Hard rules

- Do not change `docs/spec_v1.md` unless the task explicitly asks for a spec update.
- Do not change `config/instruments.yml` or risk / cost thresholds unless explicitly instructed.
- Keep champion and fallback strategies comparable by sharing universe, data, cost, risk, and execution layers.
- Prefer config-driven design over hard-coded constants.
- Add or update tests for any logic that can introduce lookahead, roll errors, sizing errors, or fallback misfires.
- After code changes, run at least `make lint && make test`.

## Code style

- Small modules
- Typed public interfaces
- Explicit schemas for IO boundaries
- Deterministic behavior under fixed inputs and seeds

## Operational bias

- Fail closed
- Log first, then optimize
- Prefer simpler changes that improve reliability over clever changes that increase model complexity
