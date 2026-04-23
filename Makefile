SHELL := /usr/bin/env bash
.DEFAULT_GOAL := help

PYTHON ?= python3
VENV ?= .venv
ACTIVATE = source $(VENV)/bin/activate

help:
	@echo "Available targets:"
	@echo "  make setup         - create venv and install deps"
	@echo "  make check-env     - verify local prerequisites"
	@echo "  make init-dirs     - create data/artifact/log dirs"
	@echo "  make fmt           - format code with ruff"
	@echo "  make lint          - run ruff checks"
	@echo "  make typecheck     - run mypy"
	@echo "  make test          - run pytest"
	@echo "  make smoke         - run smoke tests"
	@echo "  make ci            - run lint + typecheck + tests"
	@echo "  make wp4-plan-smoke    - build a deterministic WP4 plan artifact"
	@echo "  make wp4-smoke-offline - run WP4 offline tests"
	@echo "  make wp4-smoke-vendor  - run optional WP4 live vendor smoke test"
	@echo "  make wp5-roll-smoke-offline - run WP5 offline roll-engine tests"
	@echo "  make wp6-continuous-smoke-offline - run WP6 offline continuous-builder tests"
	@echo "  make wp7-features-smoke-offline - run WP7 offline features/CPD tests"
	@echo "  make wp8-signal-smoke-offline - run WP8 offline signal tests"
	@echo "  make wp9-cpd-lstm-smoke-offline - run WP9 offline CPD-LSTM tests"
	@echo "  make wp10-walkforward-smoke-offline - run WP10 offline walk-forward tests"
	@echo "  make wp11-model-rc-smoke-offline - run WP11 offline model RC tests"
	@echo "  make install-torch-cu128 - optional PyTorch CUDA 12.8 install"

setup:
	bash scripts/setup.sh

check-env:
	bash scripts/check_env.sh

init-dirs:
	mkdir -p data/raw data/staging data/curated data/features data/research data/backtests data/shadow data/meta artifacts logs

fmt:
	$(ACTIVATE) && ruff format .

lint:
	$(ACTIVATE) && ruff check .

typecheck:
	$(ACTIVATE) && mypy src

test:
	$(ACTIVATE) && pytest -q tests/unit tests/integration

smoke:
	$(ACTIVATE) && pytest -q tests/smoke

ci: lint typecheck test smoke

wp4-plan-smoke:
	$(ACTIVATE) && python -m cpdshadow.cli ingest databento plan --start 2024-01-02 --end 2024-01-05 --roots ES,NQ --schemas definition,statistics --output artifacts/wp4/plan_smoke.json

wp4-smoke-offline:
	$(ACTIVATE) && pytest -q tests/unit/test_databento_request_plan.py tests/unit/test_databento_normalize.py tests/unit/test_registry_storage.py tests/integration/test_wp4_ingest_offline.py

wp4-smoke-vendor:
	$(ACTIVATE) && CPDSHADOW_RUN_VENDOR_TESTS=1 pytest -q tests/integration/test_wp4_vendor_smoke.py

wp5-roll-smoke-offline:
	$(ACTIVATE) && pytest -q tests/unit/test_roll_engine.py tests/integration/test_roll_engine_io.py

wp6-continuous-smoke-offline:
	$(ACTIVATE) && pytest -q tests/unit/test_continuous_builder.py tests/smoke/test_wp6_continuous_smoke.py

wp7-features-smoke-offline:
	$(ACTIVATE) && pytest -q tests/unit/test_cpd.py tests/unit/test_features_builder.py tests/integration/test_wp7_features_cli.py

wp8-signal-smoke-offline:
	$(ACTIVATE) && pytest -q tests/unit/test_tsmom_signal_formula.py tests/unit/test_signal_schema_validation.py tests/unit/test_signal_artifact.py tests/integration/test_wp8_signals_cli.py

wp9-cpd-lstm-smoke-offline:
	$(ACTIVATE) && pytest -q tests/unit/test_cpd_lstm_dataset.py tests/unit/test_cpd_lstm_model.py tests/integration/test_wp9_cpd_lstm_cli.py

wp10-walkforward-smoke-offline:
	$(ACTIVATE) && pytest -q tests/unit/test_walkforward_windows.py tests/unit/test_research_metrics.py tests/unit/test_reversal_bucket.py tests/unit/test_walkforward_pnl_alignment.py tests/integration/test_wp10_walkforward_smoke.py

wp11-model-rc-smoke-offline:
	$(ACTIVATE) && pytest -q tests/unit/test_model_release.py tests/unit/test_promotion_gates.py tests/smoke/test_wp11_model_rc_smoke.py

install-torch-cu128:
	$(ACTIVATE) && bash scripts/install_torch_cuda128.sh
