# CPD-LSTM Shadow Trading System

Step 1 と Step 2 の初版成果物です。目的は、CPD-LSTM を本命、分散TSMOMを fallback / benchmark とする日次グローバル先物戦略の **Shadow 運用基盤**を、再現可能な形で立ち上げることです。

## 収録物

- `docs/spec_v1.md` — v1.0 運用仕様の凍結文書
- `docs/repo_conventions.md` — リポジトリ運用規約と Codex 利用方針
- `.codex/config.toml` — プロジェクトスコープの Codex 設定
- `.codex/README.md` — Codex app / CLI での使い方と local environment 登録手順
- `AGENTS.md` — Codex 向けの repo-level 作業規約
- `Makefile` / `pyproject.toml` / `.pre-commit-config.yaml` — 初期開発基盤
- `scripts/setup.sh` / `scripts/check_env.sh` — ローカルセットアップと環境診断
- `.github/workflows/ci.yml` — lint / typecheck / test を回す最小CI

## 使い始め

```bash
cd /path/to/this/repo
make setup
make check-env
make test
```

## WP5 Roll Engine

WP5 では WP4 curated snapshot から `lead_map` と `roll_events` を構築します。

```bash
python -m cpdshadow.cli roll-engine build \
  --start 2024-01-02 \
  --end 2024-01-08 \
  --snapshot-id <snapshot_id> \
  --roots ES

python -m cpdshadow.cli roll-engine qa \
  --snapshot-id <snapshot_id>
```

## 注意

この段階では Step 4 以降の詳細実装（銘柄正本、特徴量、CPD、モデル学習、IBKR アダプタ等）はまだ含みません。今回は **仕様凍結と repo skeleton の固定** がゴールです。
