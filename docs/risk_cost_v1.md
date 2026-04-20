# Risk / Cost / Portfolio Layer v1.0

- Status: Frozen for Step 11 implementation
- Effective date: 2026-04-17
- Scope: CPD-LSTM champion / distributed TSMOM fallback

## 1. Position sizing

v1.0 の sizing は **annualized risk budget** ベースで固定する。

### 1.1 Inputs

各 root に対して最低限必要な入力は以下。

- `signal ∈ [-1, 1]`
- `annualized_vol`
- `lead_price`
- `quote_multiplier_to_usd_notional`
- `initial_margin_per_contract_usd`
- `tick_value_usd`

### 1.2 Active root 判定

以下を満たす root のみを active とする。

- `abs(signal) >= 0.05`
- `annualized_vol > 0`
- `lead_price > 0`

### 1.3 Raw target contracts

v1.0 では、units を揃えるため annualized risk のまま計算する。

```text
Total annualized risk budget = NAV × 0.10
Per-active-root raw risk budget = Total annualized risk budget / sqrt(N_active)
Annualized dollar risk per contract = annualized_vol × lead_price × quote_multiplier
Raw contracts = signal × Per-active-root raw risk budget / Annualized dollar risk per contract
```

この定義により、signal が 1 に近いほど raw risk budget を多く使い、
vol が高い契約ほど契約数は小さくなる。

## 2. Absolute risk caps

v1.0 の root / asset-class cap は **gross risk share** ではなく、
**NAV に対する annualized ex-ante dollar risk の上限** として固定する。

### 2.1 Root soft cap

```text
|position annualized dollar risk| <= NAV × 0.12
```

### 2.2 Asset-class soft cap

```text
sum(|position annualized dollar risk| within asset class) <= NAV × 0.35
```

注記: 現行の target portfolio vol = 10% では root cap が routine に効くとは限らない。
この cap は **平常時の主要 sizing ルール** というより、モデル異常や将来の leverage 拡張に備えた backstop として使う。

## 3. Margin caps

margin 制約は risk cap と独立に評価する。

### 3.1 Soft cap

```text
Total initial margin required <= NAV × 0.35
```

raw/capped targets が soft cap を超える場合は、全 root を同率で縮小する。

### 3.2 Hard cap

```text
Total initial margin required <= NAV × 0.50
```

hard cap breach は警報扱いであり、v1.0 では sizing layer が soft cap に押し戻しても、
**pre-scale で hard cap を超えた事実** は diagnostic flag として保持する。

## 4. Integer conversion

### 4.1 Rounding

契約数の丸めは **half away from zero** を採用する。

- `+1.50 -> +2`
- `+1.49 -> +1`
- `-1.50 -> -2`
- `-1.49 -> -1`

### 4.2 Post-rounding recheck

丸め後に制約を再評価し、margin / root / asset-class cap を超える場合は
**1 contract ずつゼロ方向に縮小**して制約を満たす。

## 5. Cost model

v1.0 のコストは、live broker exact fee table ではなく **research default** を使う。
これは Shadow 運用の conservative baseline であり、Step 14 以降で paper 実測に合わせて校正する。

### 5.1 One-way rebalance cost

```text
One-way cost per contract
= commission_per_contract_side_usd + slippage_ticks_per_side × tick_value_usd
```

### 5.2 Roll cost

roll は「旧限月を閉じて新限月を建てる」2-leg イベントとして扱う。

```text
Roll cost per carried contract
= 2 × (
    commission_per_contract_side_usd
    + slippage_ticks_per_side × tick_value_usd
    + roll_extra_ticks_per_contract_side × tick_value_usd
  )
```

### 5.3 Default research assumptions

| Asset class | Commission / side (USD) | Slippage / side (ticks) | Roll extra / side (ticks) |
|---|---:|---:|---:|
| equity_index | 1.50 | 0.50 | 0.25 |
| rates | 1.35 | 0.50 | 0.25 |
| fx | 1.35 | 0.75 | 0.25 |
| metals | 1.85 | 0.75 | 0.50 |
| energy | 2.35 | 1.00 | 0.75 |
| agriculture | 1.85 | 1.00 | 0.50 |

## 6. PnL attribution

v1.0 の日次 PnL attribution は以下の最小形で固定する。

```text
Gross mark-to-market PnL
= previous_contracts × quote_multiplier × (current_price - previous_price)

Net PnL
= Gross mark-to-market PnL - rebalance_cost - roll_cost
```

複雑な roll yield / carry 分解は v1.0 の外に置く。

## 7. Implementation boundary

Step 11 の成果物は以下。

- `config/settings.base.yml`
- `src/cpdshadow/config.py`
- `src/cpdshadow/costs.py`
- `src/cpdshadow/risk.py`
- `src/cpdshadow/portfolio.py`
- `tests/unit/test_portfolio_risk_costs.py`
