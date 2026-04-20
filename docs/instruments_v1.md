# Step 4: instrument master v1.0

## 目的

`config/instruments.yml` は v1.0 の取引ユニバース正本です。以下を一元化します。

- Universe membership
- 契約経済性（tick / tick value / notional multiplier）
- hard-roll override
- 小口執行に使える micro 代替の可否

このファイルは **research / backtest / shadow / broker adapter** の共通入力にします。

## Universe

| Root | Name | Exchange | Asset class | Quote | Multiplier to USD notional | Tick | Tick value USD | Hard roll |
|---|---|---:|---|---|---:|---:|---:|---|
| ES | E-mini S&P 500 | CME | equity_index | index points | 50.0 | 0.25 | 12.50 | T-5 last trade |
| NQ | E-mini Nasdaq-100 | CME | equity_index | index points | 20.0 | 0.25 | 5.00 | T-5 last trade |
| RTY | E-mini Russell 2000 | CME | equity_index | index points | 50.0 | 0.10 | 5.00 | T-5 last trade |
| YM | E-mini Dow | CBOT | equity_index | index points | 5.0 | 1.0 | 5.00 | T-5 last trade |
| ZT | 2-Year Treasury Note | CBOT | rates | points of par | 2000.0 | 0.00390625 | 7.8125 | T-5 last trade |
| ZF | 5-Year Treasury Note | CBOT | rates | points of par | 1000.0 | 0.0078125 | 7.8125 | T-5 last trade |
| ZN | 10-Year Treasury Note | CBOT | rates | points of par | 1000.0 | 0.015625 | 15.625 | T-5 last trade |
| ZB | 30-Year Treasury Bond | CBOT | rates | points of par | 1000.0 | 0.03125 | 31.25 | T-5 last trade |
| 6E | Euro FX | CME | fx | USD per EUR | 125000.0 | 0.00005 | 6.25 | T-5 last trade |
| 6J | Japanese Yen | CME | fx | USD per JPY | 12500000.0 | 0.0000005 | 6.25 | T-5 last trade |
| 6B | British Pound | CME | fx | USD per GBP | 62500.0 | 0.0001 | 6.25 | T-5 last trade |
| 6A | Australian Dollar | CME | fx | USD per AUD | 100000.0 | 0.00005 | 5.00 | T-5 last trade |
| GC | Gold | COMEX | metals | USD per oz | 100.0 | 0.10 | 10.00 | T-10 FND/LTD |
| SI | Silver | COMEX | metals | USD per oz | 5000.0 | 0.005 | 25.00 | T-10 FND/LTD |
| HG | Copper | COMEX | metals | USD per lb | 25000.0 | 0.0005 | 12.50 | T-10 FND/LTD |
| CL | WTI Crude Oil | NYMEX | energy | USD per barrel | 1000.0 | 0.01 | 10.00 | T-10 FND/LTD |
| NG | Henry Hub Natural Gas | NYMEX | energy | USD per MMBtu | 10000.0 | 0.001 | 10.00 | T-10 FND/LTD |
| ZC | Corn | CBOT | agriculture | cents per bushel | 50.0 | 0.25 | 12.50 | T-10 FND/LTD |
| ZW | Chicago SRW Wheat | CBOT | agriculture | cents per bushel | 50.0 | 0.25 | 12.50 | T-10 FND/LTD |
| ZS | Soybeans | CBOT | agriculture | cents per bushel | 50.0 | 0.25 | 12.50 | T-10 FND/LTD |

## 重要な実装メモ

### 1. `quote_multiplier_to_usd_notional` の意味

この値は、**保存済み価格をそのまま掛けるだけで概算契約金額が出る** ように設計しています。

- ES: `notional ≈ price * 50`
- ZN: `notional ≈ price * 1000`
- ZC: `notional ≈ price * 50`  
  (`5,000 bushels x 0.01 USD` なので、価格が 460.25 cents なら notional は `460.25 x 50 = 23,012.5 USD`)

この設計にすると、Step 11 のポジション枚数計算を asset-class 横断で共通化できます。

### 2. grains の `min_price_increment`

grains は **0.25** で保持します。これは「0.25 cent per bushel」を意味します。  
`0.0025 USD` ではありません。prices も cents 表示系で扱う前提です。

### 3. hard roll の固定方針

v1.0 では次で固定します。

- equity / rates / fx: `T-5` before last trade date
- metals / energy / agriculture: `T-10` before first notice or last trade date

最終的な実ロールは `lead_map` 側で volume 3日判定が優先され、hard roll は安全側 override として使います。

## Micro substitution policy

### Approved

このグループは **標準契約の研究シグナルを micro に縮小して執行しやすい** 候補です。ただし v1.0 では自動切替を無効にしています。

- ES → MES
- NQ → MNQ
- RTY → M2K
- YM → MYM
- 6E → M6E
- 6B → M6B
- 6A → M6A
- GC → MGC

### Restricted

このグループは **tick 構造や settlement が標準契約と完全一致しない** ため、v1.0 では自動代替を禁止します。

- 6J → MJY
- SI → SIL
- HG → MHG
- CL → MCL
- NG → MNG
- ZC → MZC
- ZW → MZW
- ZS → MZS

### None

rates は classic contract に対する直接の micro 代替を置きません。

- ZT
- ZF
- ZN
- ZB

## 運用方針

1. **研究・検証・shadow の正本は standard roots** に固定する。  
2. micro は小口 funded live の将来オプションであり、v1.0 の champion/challenger 判定には使わない。  
3. `config/instruments.yml` を変える場合は `docs/decisions/` に decision note を追加する。  

## ソース整理

公式仕様は主に CME Group / CBOT / COMEX / NYMEX の contract specs, overview, FAQ, product guide を基準に確認した。
特に確認した観点は次の4つ。

- standard contract size / tick / tick value
- classic Treasury tick schedule
- micro product code and size ratio
- micro product の settlement 差異（financial/cash settled かどうか）
