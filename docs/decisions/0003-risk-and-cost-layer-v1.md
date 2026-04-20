# 0003: Risk / cost / target-contract layer を v1.0 で固定する

- Status: accepted
- Date: 2026-04-17

## Context

Step 11 では champion の CPD-LSTM と fallback の分散TSMOM を同じ変換層に通す必要がある。
データ品質やロールと同様に、position sizing / cost / margin を早い段階で固定しないと、
model 改善の効果と implementation drift が混ざる。

## Decision

以下を v1.0 の固定仕様とする。

1. base sizing は `NAV × 10% vol target / sqrt(active_count)` を annual risk budget として使う
2. single-root / asset-class cap は **NAV 比 annualized risk cap** とする
3. hard margin breach は global scale-down で閉じる
4. modeled cost は **commission + spread/slippage ticks** の one-way model を使う
5. roll day は **two-leg plus basis buffer** で近似する
6. daily PnL attribution は `gross mtm - modeled cost` を基本形とする

## Consequences

- sizing は単純で audit しやすい
- concentration guard が portfolio target vol と独立に効く
- root / asset-class / margin のどこで削られたか追跡しやすい
- 実コスト較正は monthly maintenance task として残る
- 相関を使った optimizer は v1.0 では採用しない
