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

## WP9 CPD-LSTM Model

WP9 では WP7 `features_daily` と WP6 `continuous_daily` の next-day label から
最小 CPD-LSTM candidate を学習し、WP8 と同じ `signals_daily` contract へ推論結果を書きます。
PyTorch は optional ML dependency です。CPU smoke には `python -m pip install -e .[dev,ml]`
を使い、CUDA 12.8 環境では `make install-torch-cu128` を使います。

```bash
python -m cpdshadow.cli models cpd-lstm train \
  --features-path data/features/features_daily \
  --continuous-path data/curated/continuous_daily \
  --snapshot-id <snapshot_id> \
  --train-start 2014-01-02 \
  --train-end 2021-12-31 \
  --val-start 2022-01-03 \
  --val-end 2023-12-29 \
  --model-id cpd_lstm_v1_candidate \
  --training-run-id train_cpd_lstm_v1_candidate \
  --output-dir artifacts/models/cpd_lstm/cpd_lstm_v1_candidate

python -m cpdshadow.cli models cpd-lstm infer \
  --features-path data/features/features_daily \
  --snapshot-id <snapshot_id> \
  --model-dir artifacts/models/cpd_lstm/cpd_lstm_v1_candidate \
  --model-id cpd_lstm_v1_candidate \
  --start 2024-01-02 \
  --end 2024-12-31 \
  --run-id infer_cpd_lstm_2024 \
  --created-at-utc 2026-04-23T00:00:00Z
```

## WP10 Walk-forward Evaluation

WP10 では CPD-LSTM と TSMOM を同じ data snapshot、root universe、risk/cost layer で
OOS 比較します。出力は model promotion ではなく、WP11 判断用の evidence と gates です。

```bash
python -m cpdshadow.cli research walkforward plan \
  --features-path data/features/features_daily \
  --continuous-path data/curated/continuous_daily \
  --snapshot-id <snapshot_id> \
  --feature-set-id features_v1 \
  --series-id v1_back_ratio_settle \
  --roots ES,NQ,ZN \
  --oos-start 2020-01-02 \
  --oos-end 2024-12-31 \
  --run-id wf_cpd_lstm_v_tsmom_2020_2024 \
  --output-dir data/research/walkforward/wf_cpd_lstm_v_tsmom_2020_2024

python -m cpdshadow.cli research walkforward run \
  --features-path data/features/features_daily \
  --continuous-path data/curated/continuous_daily \
  --snapshot-id <snapshot_id> \
  --roots ES,NQ,ZN \
  --oos-start 2020-01-02 \
  --oos-end 2024-12-31 \
  --run-id wf_cpd_lstm_v_tsmom_2020_2024 \
  --output-dir data/research/walkforward/wf_cpd_lstm_v_tsmom_2020_2024
```

## WP11 Model RC / Promotion Decision

WP11 では WP10 evidence と WP9 model artifact metadata から、Shadow-only の model release
candidate package を作ります。これは live/paper approval ではなく、必ず human review が必要です。

```bash
python -m cpdshadow.cli model-rc package \
  --walkforward-dir data/research/walkforward/<run_id> \
  --candidate-model-dir artifacts/models/cpd_lstm/<model_id> \
  --release-id rc_<date>_<model> \
  --output-dir artifacts/releases/model_rc

python -m cpdshadow.cli model-rc qa \
  --release-dir artifacts/releases/model_rc/rc_<date>_<model>
```

## WP12 Broker Boundary / Dry-Run Adapter

WP12 では `targets_daily`、`broker_positions_snapshot`、`contract_master` を入力に、
broker-neutral な `order_intents` と `journal_events` を dry-run で生成します。
この段階では broker 接続や注文送信は行わず、最終 status は `not_sent` または `rejected` のみです。

```bash
python -m cpdshadow.cli broker-boundary build-intents \
  --targets-path data/shadow/targets_daily \
  --positions-path data/shadow/broker_positions_snapshot \
  --contract-master-path data/curated/contract_master \
  --run-id shadow_run_001 \
  --execution-mode shadow \
  --output-dir data/shadow/execution_boundary/run_id=shadow_run_001

python -m cpdshadow.cli broker-boundary dry-run \
  --planned-intents-path data/shadow/execution_boundary/run_id=shadow_run_001/planned_order_intents.parquet \
  --contract-master-path data/curated/contract_master \
  --output-dir data/shadow/execution_boundary/run_id=shadow_run_001

python -m cpdshadow.cli broker-boundary qa \
  --targets-path data/shadow/targets_daily \
  --positions-path data/shadow/broker_positions_snapshot \
  --final-order-intents-path data/shadow/execution_boundary/run_id=shadow_run_001/order_intents.parquet \
  --output-dir data/shadow/execution_boundary/run_id=shadow_run_001
```

## 注意

この段階では、WP4-WP12 までのデータ基盤、roll、continuous、feature/CPD、fallback signal、CPD-LSTM candidate train/infer、walk-forward OOS evaluation、Shadow-only model RC packaging、broker-neutral dry-run order intent generation は含みますが、**IBKR アダプタ、注文送信、fills、live/paper approval** はまだ含みません。運用ゴールは引き続き **再現可能で安定した shadow execution** です。
