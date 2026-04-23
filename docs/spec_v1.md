# CPD-LSTM Shadow Trading System v1.0 仕様書

- Status: Frozen for implementation bootstrap
- Version: 1.0.0-draft
- Scope owner: Project owner
- Effective date: 2026-04-17
- Change rule: 本書の変更は `docs/decisions/` 配下の decision note とセットで行う

## 1. 目的

本システムは、日次のグローバル先物ユニバースに対して CPD-LSTM を用いたシグナルを生成し、
**Live(Shadow) 運用**と保守までを再現可能に実装することを目的とする。

v1.0 の主目標は以下。

1. CPD-LSTM を本命戦略として Shadow で安定稼働させる。
2. 同一データ基盤・同一リスク層・同一執行層の分散TSMOMを benchmark / fallback として並走させる。
3. 再学習、推論、仮想発注、監視、障害時降格までを毎営業日自動で再実行できるようにする。
4. 同一入力スナップショットに対して同一出力を返す再現性を確保する。

## 2. v1.0 のスコープ

### In scope

- 日次頻度のシグナル生成
- グローバル先物ユニバース（詳細な銘柄正本は `config/instruments.yml` に確定）
- 外部ベンダーによる長期履歴データ取得
- 自前の lead map と continuous series の構築
- CPD-LSTM の学習 / 推論 / ウォークフォワード検証
- 同条件の分散TSMOM benchmark / fallback
- Shadow 用 target contracts 生成
- 仮想執行または paper account での約定照合
- 監視、kill switch、fallback 降格、日次レポート
- Runbook と保守手順の整備

### Out of scope

- HFT / intraday / sub-daily execution
- options / options-on-futures
- 複数ブローカー同時対応
- 現金勘定での自動 live 発注
- 最適 execution アルゴ（TWAP / POV / smart slicing 等）の高度化
- 研究目的での特徴量無制限追加
- 裁量 override を前提とした運用

## 3. システム境界

### Champion

- CPD-LSTM

### Challenger / Fallback

- 分散TSMOM

### Execution mode in v1.0

- Shadow first
- paper account は許容するが、v1.0 の成功条件は **Shadow 安定稼働** を基準にする

## 4. 設計原則

1. **単純さ優先**: 高度なモデルより先に、データ品質・ロール・コスト・再現性を固定する。
2. **比較可能性優先**: CPD-LSTM と TSMOM は universe / continuous / risk / cost / execution を共通化する。
3. **No hidden state**: 推論結果は日付・コードバージョン・設定ハッシュに紐付けて保存する。
4. **Fail closed**: データ欠損、モデル異常、ブローカー状態不明の際は新規建てを止める。
5. **Fallback first**: CPD-LSTM 障害時は自動的に TSMOM または no-trade へ降格する。

## 5. 固定仕様（v1.0）

### 5.1 時間軸

- Signal frequency: daily
- Portfolio rebalance cadence: daily close-to-next-session workflow
- Retraining cadence: quarterly
- Hyperparameter sweep cadence: yearly

### 5.2 データ原則

- 長期履歴データは外部ベンダーを正本とする
- シグナル価格は日次 settlement を第一優先とする
- continuous series は vendor synthetic をそのまま正本にせず、raw contracts から自前構築する
- execution は当日の `lead_contract` に対して行う
- continuous futures は signal / research 用であり、直接発注対象にしない
- continuous builder v1 は `v1_back_ratio_settle` / `continuous_builder_v1` の backward ratio-adjusted settlement series を canonical signal series とする
- raw vendor ingest は immutable file と snapshot registry を前提にし、最初の curated daily layer は contract definitions と per-contract daily summaries に分離する

### 5.3 continuous / roll

- lead contract は volume 主体で判定する
- `next > front` が 3 営業日連続したら volume roll
- hard roll override を併用する
- signal series は backward ratio-adjusted continuous を採用し、Databento 等の vendor continuous price を直接使わない

### 5.4 モデル

- Model family: CPD-LSTM
- Sequence length: 63 business days
- CPD windows: 21, 63
- LSTM layers: 1
- Hidden size: 64
- Dropout: 0.2
- Output head: tanh, signal range `[-1, 1]`
- Loss: ex-cost Sharpe loss

### 5.5 特徴量

v1.0 では特徴量を増やしすぎない。固定候補は以下。

- `ret_1`
- `ret_21`
- `ret_63`
- `ret_126`
- `ret_252`
- `macd_8_24`
- `macd_16_48`
- `macd_32_96`
- `cpd21_score`
- `cpd21_age`
- `cpd63_score`
- `cpd63_age`
- `vol_20_60`
- `vol_60_252`

### 5.6 ボラ / リスク / 契約数変換

- Vol estimator: 60-day EWMA
- Portfolio target vol: 10% annualized
- Active signal threshold: `abs(signal) >= 0.05`
- Single-root soft cap: `NAV × 0.12` の annualized ex-ante dollar risk
- Asset-class soft cap: `NAV × 0.35` の annualized ex-ante dollar risk
- Initial margin usage soft cap: `NAV × 0.35`
- Initial margin usage hard cap: `NAV × 0.50`
- Contract rounding: half away from zero
- Post-rounding recheck: enabled

Sizing は annualized units で固定する。

```text
Total annualized risk budget = NAV × 0.10
Per-active-root raw risk budget = Total annualized risk budget / sqrt(N_active)
Annualized dollar risk per contract = annualized_vol × lead_price × quote_multiplier
Raw contracts = signal × Per-active-root raw risk budget / Annualized dollar risk per contract
```

### 5.7 Cost model

v1.0 の cost model は research default を用いる。

```text
One-way rebalance cost per contract
= commission_per_contract_side_usd + slippage_ticks_per_side × tick_value_usd

Roll cost per carried contract
= 2 × (
    commission_per_contract_side_usd
    + slippage_ticks_per_side × tick_value_usd
    + roll_extra_ticks_per_contract_side × tick_value_usd
  )
```

Paper/live に進んだ後、実測値で再校正するまではこの conservative baseline を正本とする。

### 5.8 Benchmark / fallback

分散TSMOMを常時並走させる。

- Same universe
- Same continuous series
- Same cost model
- Same risk layer
- Same execution abstraction

Naive signal:

```text
s_tsmom_i,t = ( sign(ret_21) + sign(ret_63) + sign(ret_252) ) / 3
```

### 5.9 運用モード

- v1.0 のゴールは Live(Shadow) と保守
- funded live auto-trading は v1.x 以降の別判断とする
- Shadow では target, expected fills, realized paper fills, hypothetical broker constraints を毎日記録する

### 5.10 Monitoring / control plane

v1.0 では監視の出力を **4つの control actions** に正規化する。

1. `run_cpd_lstm`: champion をそのまま採用
2. `fallback_tsmom`: champion を止めて fallback を採用
3. `hold`: 当日 rebalance を停止し、ポジション維持のみ
4. `reduce_only`: 新規建て禁止、リスク削減方向の注文のみ許可

各カテゴリーの主要しきい値は次で固定する。

- Data quality
  - missing settlement roots: 0
  - missing volume roots: 0
  - contract map conflicts: 0
  - duplicate feature rows: 0
  - feature missing fraction: 1% 以下
  - stale feature roots: 0
- Model health
  - artifact hash mismatch: 不許容
  - CPD service failure: 不許容
  - non-finite signal roots: 0
  - signal saturation fraction: 35% 以下
  - cross-sectional signal std: 0.02 以上
  - feature PSI: 0.20 以下
- Risk health
  - realized vol warning: 12%
  - realized vol reduce-only: 14%
  - margin usage warning: 35%
  - margin usage reduce-only: 50%
  - position reconciliation mismatches: 0
- Execution health
  - broker state known: 必須
  - order rejects: 0
  - fill ratio warning: 80% 以上
  - slippage ratio warning: 1.25 以下
  - slippage ratio fallback: 1.50 以下
- Strategy health
  - CPD 60d net Sharpe review floor: -0.25
  - max consecutive CPD underperformance windows: 2
  - reversal edge floor: 0 USD

Action precedence は `reduce_only > hold > fallback_tsmom > run_cpd_lstm` とする。

## 6. 再現性要件

以下は必須要件。

1. すべての実験は config-driven とする。
2. seed を固定する。
3. モデル artifact に config hash を付ける。
4. 同一データ snapshot に対して同一推論結果を返す。
5. raw / staging / curated / features / outputs を分離する。

## 7. 成功指標

### 7.1 研究面

CPD-LSTM は live shadow champion として採用する前に、最低でも以下を満たすこと。

- 全 OOS net Sharpe >= 0.90
- 直近 8 四半期 net Sharpe >= 0.60
- max drawdown <= 1.5 x TSMOM max drawdown
- modeled cost error <= 1.25 x realized paper cost proxy
- reversal bucket で CPD-LSTM が TSMOM を上回る

### 7.2 運用面

- 日次バッチ成功率 >= 99%
- 欠損データによる silent failure = 0
- position reconciliation mismatch = 0 継続
- 監視アラートが記録され、未解決で放置されない

## 8. Fallback / kill switch

以下のいずれかで CPD-LSTM は停止または降格する。

- feature completeness failure
- CPD computation failure
- model artifact mismatch
- broker state ambiguity
- realized vol breach
- margin hard cap breach
- abnormal signal saturation
- slippage regime break

降格または停止の優先順位は次。

1. `reduce_only`
2. `hold`
3. `fallback_tsmom`
4. `run_cpd_lstm`

代表的なルール:

- raw settlement / volume / contract map 異常 -> `hold`
- champion feature freshness / CPD / inference 異常 -> `fallback_tsmom`
- broker ambiguity / position mismatch / hard risk breach -> `reduce_only`
- 短期的な fill deterioration や高 vol 警告のみ -> alert を出しつつ継続可

## 9. 変更管理

v1.0 では次の変更を禁止する。

- 学習頻度の ad hoc 変更
- 特徴量の逐次追加
- benchmark と champion の別 universe 化
- continuous / roll ルールの task 単位変更
- cost model の後付け修正
- 監視 action precedence の ad hoc 変更
