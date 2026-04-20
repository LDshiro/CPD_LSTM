#!/usr/bin/env bash
set -euo pipefail

check_cmd() {
  local cmd="$1"
  if command -v "$cmd" >/dev/null 2>&1; then
    echo "[ok] $cmd"
  else
    echo "[missing] $cmd"
  fi
}

echo "== core =="
check_cmd git
check_cmd python3
check_cmd python3.11
check_cmd uv
check_cmd bash

echo "== gpu =="
if command -v nvidia-smi >/dev/null 2>&1; then
  nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader || true
else
  echo "[info] nvidia-smi not found"
fi

echo "== repo dirs =="
for d in data/raw data/staging data/curated data/features data/backtests data/shadow data/meta artifacts logs; do
  [[ -d "$d" ]] && echo "[ok] $d" || echo "[missing] $d"
done

echo "== python =="
python3 - <<'PY'
import sys
print(sys.version)
PY
