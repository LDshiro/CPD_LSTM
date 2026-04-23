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

## WP6 Continuous Builder

WP6 では WP4/WP5 curated snapshot から signal-only の `continuous_daily` を構築します。

```bash
python -m cpdshadow.cli continuous build \
  --snapshot-id <snapshot_id> \
  --start 2024-01-02 \
  --end 2024-03-29 \
  --roots ES,NQ

python -m cpdshadow.cli continuous qa \
  --snapshot-id <snapshot_id>
```

## WP7 Features / CPD Builder

WP7 では WP6 `continuous_daily` から `cpd_daily` と `features_daily` を構築します。

```bash
python -m cpdshadow.cli features build \
  --snapshot-id <snapshot_id> \
  --start 2024-01-02 \
  --end 2024-03-29 \
  --series-id v1_back_ratio_settle \
  --feature-set-id features_v1 \
  --roots ES,NQ

python -m cpdshadow.cli features qa \
  --snapshot-id <snapshot_id> \
  --feature-set-id features_v1
```

## WP8 TSMOM Signals

WP8 では WP7 `features_daily` から fallback 用の `signals_daily` を構築します。

```bash
python -m cpdshadow.cli signals tsmom build \
  --features-path data/features/features_daily \
  --snapshot-id <snapshot_id> \
  --feature-set-id features_v1 \
  --start 2024-01-02 \
  --end 2024-12-31 \
  --roots ES,NQ,ZN \
  --run-id infer_tsmom_2024 \
  --created-at-utc 2026-04-20T00:00:00Z

python -m cpdshadow.cli signals qa \
  --signals-path data/research/signals_daily \
  --run-id infer_tsmom_2024
```

## 注意

この段階では、WP4-WP8 までのデータ基盤、roll、continuous、feature/CPD、fallback signal 生成は含みますが、**CPD-LSTM 推論、IBKR アダプタ、注文執行** はまだ含みません。運用ゴールは引き続き **再現可能で安定した shadow execution** です。
