# Repository Conventions

## 1. 目的

この文書は、CPD-LSTM Shadow Trading System の **実装ルール** と **Codex 運用ルール** を定義する。
以後の作業は、この文書と `docs/spec_v1.md` を優先する。

## 2. ディレクトリ構成

```text
.
├── .codex/
├── .github/workflows/
├── artifacts/
├── config/
├── data/
│   ├── raw/
│   ├── staging/
│   ├── curated/
│   ├── features/
│   ├── backtests/
│   └── shadow/
├── docs/
│   ├── decisions/
│   └── runbooks/
├── logs/
├── notebooks/
├── scripts/
├── src/cpdshadow/
└── tests/
```

### 役割

- `.codex/`: Codex project config と Codex 関連ドキュメント
- `config/`: YAML による運用設定と正本定義
- `data/raw`: vendor 取得の原データ
- `data/staging`: 契約マスタや vendor 整形途中データ
- `data/curated`: lead map / continuous などの確定中間成果物
- `data/features`: 学習・推論用特徴量
- `data/backtests`: walk-forward の出力
- `data/shadow`: shadow 日報、注文差分、reconciliation
- `artifacts/`: 学習済みモデル、推論キャッシュ、評価物
- `logs/`: JSON logs, job logs
- `src/cpdshadow`: 実装本体
- `tests/`: unit / integration / smoke

## 3. Branch 規約

- `main`: 常に再現可能な状態を維持する保護ブランチ
- `feat/<topic>`: 機能追加
- `fix/<topic>`: バグ修正
- `chore/<topic>`: 雑務・依存更新
- `docs/<topic>`: ドキュメント更新
- `exp/<topic>`: 破棄可能な研究用実験
- `release/<tag>`: リリース準備

ルール:

1. `main` へ直接 push しない
2. 仕様変更を伴う修正は、PR に decision note を含める
3. `exp/*` の成果は `main` に直接混ぜない

## 4. Commit 規約

Conventional Commits を推奨する。

- `feat:`
- `fix:`
- `chore:`
- `docs:`
- `refactor:`
- `test:`

例:

```text
feat(data): add lead map builder for cme roots
fix(risk): cap root exposure before contract rounding
docs(spec): clarify shadow fallback rules
```

## 5. PR 規約

PR の最小チェック項目:

- 何を変えたか
- なぜ必要か
- `docs/spec_v1.md` への影響有無
- 再現コマンド
- 追加 / 変更テスト
- 運用影響（risk / cost / monitoring / fallback）

## 6. Python / toolchain 規約

- Python: 3.11 系を標準とする
- package layout: `src/` layout
- dependency install: repo 内では `uv` 優先、なければ `venv + pip`
- formatter / linter: `ruff`
- typecheck: `mypy`
- tests: `pytest`

## 7. Make ターゲット規約

最低限、以下のコマンドが repo の共通窓口になる。

- `make setup`
- `make check-env`
- `make fmt`
- `make lint`
- `make typecheck`
- `make test`
- `make smoke`
- `make ci`

## 8. Codex 運用規約

### 8.1 読む順番

Codex にこの repo を触らせるときは、まず以下を読む前提にする。

1. `AGENTS.md`
2. `docs/spec_v1.md`
3. `docs/repo_conventions.md`

### 8.2 変更禁止

Codex は、明示的な指示なしに以下を変更してはいけない。

- `docs/spec_v1.md`
- `config/instruments.yml`
- `config/settings*.yml`
- cost / risk の閾値
- fallback 条件

### 8.3 作業ルール

- 1 task = 1 logical change
- config-driven に保つ
- 暗黙の global state を増やさない
- 先読み混入がありうる箇所には必ず test を付ける
- 変更後は最低 `make lint && make test` を実行する

## 9. Codex app local environment の扱い

この repo には `.codex/config.toml` を含める。
ただし、Codex app の local environment（setup scripts / actions）の**共有ファイル形式は本repoでは固定しない**。
理由は、プロジェクト共有用設定が `.codex` に置かれることは公開仕様で確認できる一方、
ファイルの安定したテキスト schema をここでは前提にしないため。

その代わり、Codex app / CLI から利用する setup scripts と action commands を `scripts/` と `Makefile` に固定する。

### 推奨 setup script

```bash
bash scripts/setup.sh
```

### 推奨 actions

- `make check-env`
- `make test`
- `make lint`
- `make smoke`
- `make ci`

## 10. テスト方針

- `tests/unit`: pure function, schema, transform, cost, risk
- `tests/integration`: dataflow, model io, broker adapter abstraction
- `tests/smoke`: daily pipeline の dry-run

v1.0 では、少なくとも以下が必須。

1. import smoke
2. config load test
3. no-lookahead test
4. roll continuity test
5. contract rounding test
6. fallback trigger test

## 11. ドキュメント方針

- 設計変更は `docs/decisions/` に decision note を追加
- 運用手順は `docs/runbooks/` に集約
- notebook は探索用途に限定し、仕様の正本にしない
