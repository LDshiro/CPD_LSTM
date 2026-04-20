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
	@echo "  make install-torch-cu128 - optional PyTorch CUDA 12.8 install"

setup:
	bash scripts/setup.sh

check-env:
	bash scripts/check_env.sh

init-dirs:
	mkdir -p data/raw data/staging data/curated data/features data/backtests data/shadow data/meta artifacts logs

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

install-torch-cu128:
	$(ACTIVATE) && pip install --index-url https://download.pytorch.org/whl/cu128 torch torchvision torchaudio
