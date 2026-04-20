#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

PYTHON_BIN="${PYTHON_BIN:-python3.11}"
if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  PYTHON_BIN="${PYTHON_BIN_FALLBACK:-python3}"
fi

if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  echo "[setup] Python 3.11+ not found" >&2
  exit 1
fi

mkdir -p data/raw data/staging data/curated data/features data/backtests data/shadow data/meta artifacts logs

if command -v uv >/dev/null 2>&1; then
  echo "[setup] using uv"
  uv venv .venv --python "$PYTHON_BIN"
  . .venv/bin/activate
  uv pip install --upgrade pip
  uv pip install -e ".[dev]"
else
  echo "[setup] using venv + pip"
  "$PYTHON_BIN" -m venv .venv
  . .venv/bin/activate
  python -m pip install --upgrade pip
  pip install -e ".[dev]"
fi

if [[ "${INSTALL_TORCH_CU128:-0}" == "1" ]]; then
  echo "[setup] installing torch for CUDA 12.8"
  . .venv/bin/activate
  pip install --index-url https://download.pytorch.org/whl/cu128 torch torchvision torchaudio
fi

if [[ -f .pre-commit-config.yaml ]]; then
  . .venv/bin/activate
  pre-commit install || true
fi

echo "[setup] done"
