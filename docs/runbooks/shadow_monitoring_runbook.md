# Shadow Monitoring Runbook

- Scope: Step 15 / Shadow operations
- Audience: project owner, future operator

## 1. 毎営業日の基本フロー

1. data ingest 完了を確認する
2. feature / CPD 生成を確認する
3. target sizing を計算する
4. monitoring decision を計算する
5. decision に従って strategy / rebalance / risk scale を決める
6. Shadow 注文または paper 注文を記録する
7. EOD で fill / slippage / reconciliation を記録する
8. アラートを日報へ追記する

## 2. Monitoring decision の読み方

### `primary_strategy`

- `cpd_lstm`: champion を使用
- `tsmom`: fallback を使用
- `none`: 当日は no-trade

### `rebalance_policy`

- `full`: 通常 rebalance
- `reduce_only`: 既存リスク縮小のみ許可
- `skip`: 当日 rebalance を実施しない

### `risk_scale_multiplier`

- `1.0`: 通常サイズ
- `0.5`: 半分へ縮小
- `0.0`: 新規リスクを追加せず、必要なら flatten 方向のみ

## 3. 代表的なアラート別対応

| Alert code | 初動 |
|---|---|
| `DATA_SETTLEMENT_MISSING` | 当日 rebalance 停止。vendor raw data と settlement source を確認 |
| `DATA_CONTRACT_MAP_INCONSISTENCY` | lead map を再生成。ロール日と expiry を確認 |
| `MODEL_CPD_FAILURE` | champion を無効化し TSMOM へ降格。CPD feature job のログ確認 |
| `MODEL_FEATURE_DRIFT` | 当日 fallback 継続。直近再学習候補として issue 登録 |
| `EXEC_BROKER_AMBIGUOUS` | 新規建て禁止。position reconciliation 完了まで reduce-only |
| `EXEC_ORDER_REJECTS` | reject reason を保存し、contract mapping / trading hours / permissions を確認 |
| `RISK_REALIZED_VOL_REDUCE` | risk scale 0.5 を適用。要因分解を日報へ記録 |
| `RISK_REALIZED_VOL_HALT` | reduce-only へ。翌日まで champion/fallback に関わらず縮小優先 |
| `STRAT_CHAMPION_UNDERPERFORMANCE` | fallback 運用へ切替。次回 monthly review で継続可否判断 |

## 4. 当日中に必ず残す記録

- monitoring decision JSON
- alert list
- primary strategy / rebalance policy / risk scale
- trigger となった raw metrics
- operator acknowledgement の有無
- 当日対応と未解決項目

## 5. 人手判断が必要なケース

次は v1.0 でも operator acknowledgement を必須とする。

- data quality critical
- broker ambiguity
- order rejects
- realized vol halt
- margin halt
- model artifact mismatch

## 6. 翌営業日に持ち越してはいけない事項

- data quality critical の未調査
- broker reconciliation mismatch
- model artifact mismatch の未解消
- order reject reason の未記録
- fallback 移行の根拠未記録

## 7. Monthly review で見る項目

- alert frequency by code
- fallback days / total days
- champion vs fallback 60d net Sharpe
- reversal bucket alpha
- fill ratio と modeled/slippage ratio
- realized vol regime の推移
