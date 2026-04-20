# Monitoring and Kill-Switch v1.0

## 1. 目的

Step 15 では、Shadow 運用で毎日同じ判断を返せるように、
監視結果を **control action** に変換する仕様を固定する。

## 2. Control actions

| Action | 意味 | 実行内容 |
|---|---|---|
| `run_cpd_lstm` | champion 正常 | CPD-LSTM target を採用 |
| `fallback_tsmom` | champion 停止、fallback 採用 | TSMOM target を採用 |
| `hold` | 当日 rebalance 停止 | ポジション維持、注文なし |
| `reduce_only` | 緊急リスク抑制 | 新規建て禁止、縮小方向のみ |

## 3. 優先順位

複数アラートが同時に出た場合は、次の優先順位で final action を決める。

```text
reduce_only > hold > fallback_tsmom > run_cpd_lstm
```

この順序により、execution / broker / hard risk の問題が model 系アラートより優先される。

## 4. Alert categories

### 4.1 Data quality

- missing settlement roots > 0 -> `hold`
- missing volume roots > 0 -> `hold`
- contract map conflicts > 0 -> `hold`
- duplicate feature rows > 0 -> `hold`
- stale feature roots > 0 -> `fallback_tsmom`
- feature missing fraction > 1% -> `fallback_tsmom`

### 4.2 Model health

- artifact hash mismatch -> `fallback_tsmom`
- CPD service failure -> `fallback_tsmom`
- inference failure -> `fallback_tsmom`
- non-finite signals -> `fallback_tsmom`
- signal saturation fraction > 35% -> `fallback_tsmom`
- cross-sectional signal std < 0.02 -> warning / `fallback_tsmom`
- feature PSI > 0.20 -> warning / `fallback_tsmom`

### 4.3 Risk health

- margin hard cap pre-scale breach -> `reduce_only`
- position reconciliation mismatch > 0 -> `reduce_only`
- realized vol >= 14% -> `reduce_only`
- margin usage >= 50% -> `reduce_only`
- realized vol >= 12% -> warning
- margin usage >= 35% -> warning

### 4.4 Execution health

- broker state unknown or disconnected -> `reduce_only`
- order rejects > 0 -> `reduce_only`
- slippage ratio >= 1.50 -> `fallback_tsmom`
- slippage ratio >= 1.25 -> warning
- fill ratio < 80% -> warning

### 4.5 Strategy health

- CPD 60d net Sharpe < -0.25 かつ TSMOM 未満 -> warning / `fallback_tsmom`
- 連続 2 window 以上で reversal edge <= 0 -> `fallback_tsmom`

## 5. 出力

監視レイヤは、最低でも次の項目を JSON 化できるようにする。

- `final_action`
- `highest_severity`
- `kill_switch_engaged`
- `fallback_active`
- `can_rebalance`
- `reduce_only`
- `alerts[]`

`alerts[]` の各要素は以下を持つ。

- `code`
- `category`
- `severity`
- `recommended_action`
- `message`
- `details`

## 6. 実務上の読み方

- `run_cpd_lstm`: そのまま champion を採用
- `fallback_tsmom`: champion を止め、同じ risk / execution 層で TSMOM を使う
- `hold`: その日だけ新規 target を作らない
- `reduce_only`: broker / risk 状態が危険なので、縮小方向しか許可しない

## 7. 実装境界

v1.0 の monitoring layer は、**意思決定の正規化** までを責務とする。

- Slack / email 通知の配送
- 実際の broker order gating
- dashboard の描画

これらは次段の job / ops 層で扱う。
