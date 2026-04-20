# Decision 0007 — Features / CPD Builder v1

Status: proposed for WP7 implementation  
Date: 2026-04-20

## Context

The project needs a deterministic feature layer before CPD-LSTM training and shadow operation. Earlier WPs fixed the futures universe, raw/curated data schemas, roll engine, continuous series construction, risk/cost sizing, and monitoring. WP7 connects the research series to model-ready features.

The source research implementation for Momentum Transformer / Slow Momentum with Fast Reversion uses feature files and optional CPD modules with multiple lookback windows. The CPD module provides changepoint location and severity features that downstream models can learn from. This project keeps the same output concept but implements a small deterministic v1 backend first.

## Decision

Implement WP7 with:

- `features_daily` as the wide model-input table.
- `cpd_daily` as the long-form CPD output table.
- Fixed v1 feature vector:
  - `ret_1`, `ret_21`, `ret_63`, `ret_126`, `ret_252`
  - `macd_8_24`, `macd_16_48`, `macd_32_96`
  - `cpd21_score`, `cpd21_age`, `cpd63_score`, `cpd63_age`
  - `vol_20_60`, `vol_60_252`
- `adj_settle_price` from `continuous_daily` as the authoritative input price.
- Recomputed adjusted-price returns for feature math.
- 60-day EWM volatility for normalization and `annualized_vol_60`.
- CPD windows fixed at 21 and 63.
- CPD backend `two_sample_t_v1` for WP7.
- Normalized CPD ages in `features_daily` and raw integer CPD ages in `cpd_daily`.
- Deterministic `feature_hash` from the exact ordered feature vector.

## Rationale

1. **Schema stability matters more than CPD sophistication in WP7.** The downstream model can only be trusted if feature inputs are auditable and reproducible.
2. **CPD should be pluggable.** A paper-compatible GP/OCPD backend can be added later without changing the data contract.
3. **Long CPD + wide features is the right split.** Long CPD supports adding windows later; wide features keeps PyTorch tensor construction deterministic.
4. **No-lookahead must be tested directly.** This is a common failure mode in daily futures pipelines.
5. **Adjusted settlement series should be authoritative.** Direct per-contract data belongs upstream; model inputs should consume `continuous_daily`.

## Consequences

- WP8/WP9 can consume stable model-ready features without recomputing indicators ad hoc.
- CPD-LSTM performance from WP9 may differ from the original paper until a paper-compatible CPD backend is added.
- The project gains an explicit QA surface for data completeness, feature range, CPD validity, and hash determinism.

## Non-goals

- No model training.
- No signal generation.
- No portfolio construction.
- No broker integration.
- No vendor calls.
