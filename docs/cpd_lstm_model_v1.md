# CPD-LSTM Model v1 Specification

Status: **candidate design for WP9 implementation**

## Purpose

CPD-LSTM v1 is the first neural strategy model for the CPD-LSTM Shadow Trading System. It consumes WP7 `features_daily`, uses CPD severity/age features alongside normalized trend features, and emits raw strategy signals in the same `signals_daily` contract as the WP8 TSMOM fallback.

The model is intended to learn a blend of slow trend-following and faster reversal response. It is not yet a release candidate. WP10 will evaluate it through walk-forward testing, and WP11 will decide whether any trained artifact can be promoted.

## Inputs

Feature set: `features_v1`

Sequence length: 63 root-observed trading days

Feature order:

```text
ret_1
ret_21
ret_63
ret_126
ret_252
macd_8_24
macd_16_48
macd_32_96
cpd21_score
cpd21_age
cpd63_score
cpd63_age
vol_20_60
vol_60_252
```

The model does not consume raw contracts, broker state, positions, margin, order fills, or future returns during inference.

## Labels for training

For a sequence ending at `as_of_date=t`, the label return is the next root-observed adjusted daily return from WP6 `continuous_daily`:

```text
r_next[root,t] = continuous_daily.daily_return[root,t_next]
```

The label is normalized by the 60-day annualized volatility known at `t`:

```text
sigma_daily[root,t] = max(annualized_vol_60[root,t] / sqrt(252), min_vol / sqrt(252))
y_norm[root,t] = clip(r_next[root,t] / sigma_daily[root,t], -10, +10)
```

## Model

```text
LSTM(input_size=14, hidden_size=64, num_layers=1, batch_first=True)
Dropout(p=0.20)
Linear(64 -> 32)
ReLU
Linear(32 -> 1)
Tanh
```

Output range: `[-1, 1]`

The output is a dimensionless conviction signal. Step 11 converts it to contract targets using the shared portfolio/risk layer.

## Loss

The v1 loss is a portfolio-style ex-cost Sharpe proxy:

```text
portfolio_pnl[t] = mean_root(signal[root,t] * next_vol_scaled_return[root,t] - turnover_cost[root,t])
loss = -mean(portfolio_pnl) / (std(portfolio_pnl) + epsilon) + small regularizers
```

The cost model is a training proxy only. Step 11 remains the source of truth for USD sizing and target construction.

## Outputs

CPD-LSTM inference writes `signals_daily` rows:

```text
run_id
strategy_id = cpd_lstm
model_id
as_of_date
root
signal_raw
signal_clipped
is_valid
invalid_reason
feature_hash
created_at_utc
```

## Artifact requirements

A trained model artifact must include:

```text
model.pt
config.json
feature_order.json
standardizer.json
metrics.json
train_manifest.json
model_card.md
sha256sums.txt
```

## Known limitations in v1

- Single-root sequences only; no cross-asset attention or graph layer.
- No root embeddings.
- No hyperparameter search.
- CPD backend is whatever WP7 produced; WP9 does not improve CPD calculation.
- Cost treatment is approximate inside loss.
- Not approved for shadow selection until WP10/WP11 evaluation.
