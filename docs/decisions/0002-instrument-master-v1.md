# 0002: Step 4 instrument master v1.0

- Date: 2026-04-17
- Status: accepted

## Context

Step 4 では、以後の ingest / continuous / feature / portfolio / broker 実装が依存する
「銘柄正本」を固定する必要がある。

## Decision

- Universe は CME Group 系の 20 standard roots に固定する。
- Contract economics は `config/instruments.yml` に集約する。
- Shadow / benchmark / champion の正本は standard roots に固定する。
- micro roots は optional metadata として保持するが、自動代替は無効にする。
- rates には direct micro substitute を置かない。
- hard-roll override は `T-5` (financials) / `T-10` (commodities) の二段階で固定する。

## Consequences

### Positive

- 研究条件が固定され、champion vs fallback 比較が容易になる。
- Step 5 以降で root mapping と contract economics を重複管理しなくて済む。
- small-NAV live を later phase で追加しやすい。

### Negative

- 初期 universe は意図的に狭い。
- micro products を自動執行に使うには別 decision が必要。
- classic rates と Micro Treasury の不整合は温存される。

## Follow-up

- Step 5: instrument definitions ingest と raw daily ingest を実装する。
- Step 6: `lead_map` 構築ロジックに本ファイルを使う。
- Step 11: position sizing は `quote_multiplier_to_usd_notional` を使って共通化する。
