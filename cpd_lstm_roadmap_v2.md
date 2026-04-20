# CPD-LSTM Shadow Trading System Roadmap v2

Status date: 2026-04-20
Goal: Live(Shadow) operation and maintainable production-like workflow. Funded live auto-trading is out of scope for v1.0.

## Current completed work

The following work packages have already been drafted and implemented as project artifacts in this chat:

| Original step | Status | Output class |
|---|---:|---|
| Step 1: v1.0 specification | Complete | `docs/spec_v1.md` |
| Step 2: repository and Codex scaffold | Complete | repo skeleton, `.codex/`, `Makefile`, tests |
| Step 4: instrument master | Complete | `config/instruments.yml`, instrument docs/tests |
| Step 11: risk / cost / portfolio layer | Complete | sizing, caps, cost model, diagnostics/tests |
| Step 15: monitoring / kill-switch layer | Complete | monitoring config, control actions, runbook/tests |

Important note: Step 15 is logically downstream of data/model/broker integration. Its standalone rules are fixed, but final integration testing must still occur after Steps 5-14.

## Roadmap v2 overview

| Milestone | Objective | Exit gate |
|---|---|---|
| M0: Frozen design baseline | Keep current Step 1/2/4/11/15 artifacts as the implementation baseline | Latest Step 15 zip is merged locally and tests pass |
| M1: Local build green | Make the project runnable in the user's local environment | `make setup`, `make test`, `scripts/check_env.sh` pass |
| M2: Data green | Build reliable raw -> lead map -> continuous -> features data pipeline | 20-root panel is reproducible with QA reports |
| M3: Research green | Implement TSMOM baseline, CPD features, CPD-LSTM training/inference | Walk-forward reports are reproducible |
| M4: Model release candidate | Freeze a shadow candidate model with go/no-go metrics | model artifact, config hash, evaluation report are versioned |
| M5: Shadow pipeline green | Run daily shadow batch end-to-end without funded trading | daily reports, monitoring decisions, journal snapshots are generated |
| M6: Maintenance-ready | Make the workflow operable and recoverable | runbooks, calendars, retraining/release procedure, incident drills are complete |

## Detailed remaining work packages

| WP | Phase | Task | Best venue | Primary outputs | Definition of done |
|---:|---|---|---|---|---|
| 0 | Baseline | Merge the latest Step 15 artifact into the local repo | Local Codex | local git repo | Tests pass locally; duplicates/stale files are cleaned |
| 1 | Local environment | Verify OS/GPU/Python/CUDA/IBKR prerequisites | Local Codex + user | environment report | `torch.cuda.is_available()` works; project tests pass |
| 2 | External dependency readiness | Prepare Databento/IBKR credentials and market data permissions | User + Local Codex | `.env`, connection checklist | Read-only data and paper account connectivity are confirmed |
| 3 | Data schema finalization | Freeze physical Parquet/DuckDB schemas for raw, lead, continuous, features, journal | Chat | schema docs, dataclass/Pydantic templates | Schema is implementable and no-lookahead fields are explicit |
| 4 | Raw data ingestion | Implement incremental daily data fetch and local caching | Local Codex | `data/raw`, ingestion CLI | Re-running ingestion is idempotent |
| 5 | Contract map and roll engine | Implement lead contract selection and roll QA | Local Codex, chat for edge cases | `lead_map.parquet`, QA report | Volume roll and hard roll are deterministic |
| 6 | Continuous series builder | Build backward ratio-adjusted signal series and execution lead series | Local Codex | `continuous_daily.parquet` | Roll gaps are explainable; no direct trading against synthetic symbols |
| 7 | Feature and CPD engine | Implement returns, MACD, vol ratios, CPD 21/63 features | Chat for reference design, Local Codex to run | `features_daily.parquet`, tests | Feature panel is reproducible and passes no-lookahead checks |
| 8 | TSMOM fallback | Implement the baseline strategy on the same data/risk/cost layer | Chat + Local Codex | TSMOM reports, fallback target generator | TSMOM can produce daily targets independently of CPD-LSTM |
| 9 | CPD-LSTM implementation | Build PyTorch dataset, model, ex-cost Sharpe loss, training/inference CLI | Chat for code skeleton, Local Codex for training | checkpoints, predictions, inference artifacts | Same seed/config/input produces same output |
| 10 | Walk-forward evaluation | Implement quarterly retrain walk-forward, reversal bucket evaluation, champion/challenger comparison | Chat + Local Codex | evaluation reports | One command regenerates OOS reports |
| 11 | Model RC freeze | Decide whether CPD-LSTM passes go/no-go and freeze model artifact | Chat + user + Local Codex | `releases/model-rc1/` | artifact hash, config hash, metrics, release note are fixed |
| 12 | Broker interface design | Implement broker-neutral order intent, dry-run adapter, IBKR adapter boundary | Chat for interface, Local Codex for adapter | `broker/`, dry-run logs | Dry-run validates target -> order intent -> journal |
| 13 | IBKR paper/shadow integration | Resolve actual contracts, pull account/positions, optionally submit paper orders | Local Codex + user | broker state snapshots, paper fills | Paper account or simulated fills reconcile with target journal |
| 14 | Daily shadow batch | Wire ingestion -> features -> signals -> targets -> monitoring -> journal -> report | Chat for orchestration design, Local Codex for scheduling | `jobs/daily_shadow.py`, daily report | One command runs the daily shadow batch end-to-end |
| 15 | Observability integration | Connect Step 15 monitoring to the actual daily batch, alerts, and reports | Local Codex + chat for thresholds | monitoring JSON, alert log | Control action is emitted every run and gates order intents |
| 16 | Shadow acceptance run | Run the daily process for a defined observation window | Local Codex + user | daily reports, incident log | Stable daily reproducibility; failures are classified and handled |
| 17 | Maintenance system | Finalize daily/weekly/monthly/quarterly runbooks, retraining, release, dependency, data-vendor, roll calendar procedures | Chat | `ops/runbooks/`, maintenance calendar | A third party could operate or recover the system from docs |

## Suggested next chat-first tasks

1. WP3: physical data schema and local storage contract.
2. WP7: exact feature/CPD computation spec and no-lookahead test cases.
3. WP8: TSMOM fallback implementation spec and target output schema.
4. WP9: CPD-LSTM PyTorch skeleton and training/inference CLI design.
5. WP14: daily shadow job contract after the above outputs exist.

## Suggested next local Codex tasks

1. Merge the latest Step 15 zip and create a real git repository.
2. Run the existing test suite.
3. Execute `scripts/check_env.sh` and fix local environment issues.
4. Configure secrets in `.env` but do not commit them.
5. Confirm read-only connectivity to the data vendor and IBKR paper environment.

## Shadow acceptance criteria

The v1.0 goal is met when the following are true:

- The daily shadow job can be run from a clean local checkout.
- The job records raw data snapshot IDs, feature hashes, model artifact hashes, target contracts, monitoring action, and journal outputs.
- CPD-LSTM and TSMOM targets are generated on the same data/risk/cost layer.
- Monitoring emits one of `run_cpd_lstm`, `fallback_tsmom`, `hold`, or `reduce_only` on every run.
- Fallback and reduce-only drills have been tested with synthetic failures.
- Daily reports and incident logs are produced consistently.
- Maintenance runbooks cover ordinary operations, retraining, roll calendar checks, vendor/API failures, dependency updates, and postmortems.

## Non-goals for v1.0

- Funded live auto-trading.
- Intraday or HFT execution.
- Complex execution algorithms.
- Adding new asset classes or large feature sets before the baseline shadow process is stable.
- Performance chasing before data, roll, costs, and monitoring are reliable.
