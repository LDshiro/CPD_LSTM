# Codex Project Notes

## 1. Project trust

この repo では `.codex/config.toml` を使う前提なので、プロジェクトは trusted として開く。

## 2. Codex CLI

```bash
codex
```

または review 専用 profile:

```bash
codex --profile review
```

## 3. Codex app / Worktree 用 setup

Codex app の local environment では Linux / WSL2 の setup script として以下を登録する。

```bash
bash scripts/setup.sh
```

## 4. 推奨 actions

以下を project actions として登録する。

### Check env

```bash
make check-env
```

### Lint

```bash
make lint
```

### Test

```bash
make test
```

### Smoke

```bash
make smoke
```

### CI local

```bash
make ci
```
