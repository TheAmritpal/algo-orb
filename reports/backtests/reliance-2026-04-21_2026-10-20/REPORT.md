# RELIANCE ORB: six-month option backtest

Research estimates generated 21 October 2026. No application, worker, risk setting, or trading mode was changed. No orders were placed.

## Result

The requested ₹5 stop / ₹10 target produced estimated net P&L of **₹75,219.35**, from 114 trades. Gross P&L after the execution allowance but before fees was ₹90,100.00; estimated charges were ₹14,880.65. Win rate was 48.25%, profit factor 1.345, and maximum drawdown from closed-trade equity ₹35,658.63. Maximum premium outlay for one trade was ₹43,500. Drawdown excludes intratrade adverse excursions; funding requirements and ROI were not estimated.

The ₹5/₹15 exit earned more over the full sample (₹80,409.34), but less in the final two months than ₹5/₹10. Skipping expiry day with ₹5/₹10 improved both periods and lowered full-period drawdown. This is a candidate for further paper validation, not proof of an optimal strategy.

## Scope and historical data

Requested period: **21 April–20 October 2026**. Actual sessions: 125, from 21 April through 19 October. October 20 was an NSE trading holiday. Training: April 21–August 20 (85 sessions); validation: August 21–October 20 (40 sessions).

Data retrieved read-only through the official Upstox Python SDK: 3,125 underlying 15-minute candles, six expired monthly contract lists, the current contract list, and 49,500 in-session option one-minute candles for 132 selected contract/day combinations. Expiry discovery succeeded for the connected account. All 125 underlying sessions had 25 contiguous 15-minute candles. The source returned an additional 560 option bars outside 09:15–15:29; these were excluded, not used to price trades. The archived raw responses retain them for audit.

Two lots equalled 1,000 units for every selected contract in this sample. Historical contract lot sizes were used rather than assuming today's lot size.

## Entry and exit assumptions

- Opening range is the 09:15–09:30 RELIANCE candle. The first subsequent completed 15-minute close strictly above/below that range triggers CE/PE. The earliest entry is 09:45; the original entry cutoff is 15:00. At most one entry per day.
- Select the nearest strictly ITM option in the nearest unexpired monthly expiry, including same-day expiry unless the tested filter excludes it. Selection uses the completed underlying candle close as the spot proxy; the live worker uses a fresh underlying quote, so strike selection may differ near strike boundaries.
- Entry proxy is the open of the option's minute beginning at the signal close, plus ₹0.10 adverse execution per unit, rounded upward to the ₹0.05 tick. Require a positive-volume entry candle. This uses no prices from before the signal, but minute OHLC cannot establish whether a real order would have filled at that price or size.
- Stop and target are measured from that modeled entry. Percentage levels are rounded outward to valid ticks. Fixed stops of ₹3–₹10 and percentage stops of 20–30% were paired with the listed targets.
- Stops execute at the stop threshold, or at a worse opening price if a later traded minute opens below it. Targets receive no favorable gap improvement. Deduct a further ₹0.10 per unit from exit prices, rounded downward. If one bar touches both stop and target, stop wins. No such ambiguous bars occurred for the main comparisons.
- Force exit at the first positive-volume minute at or after 15:25 and before 15:30. Three baseline trades exited at 15:26 because the 15:25 minute had no trades. They remain included in P&L; none of the 114 baseline signals was dropped.
- Retain the configured ₹10,000 planned-loss cap. Reject nonpositive stops. Current premium/notional caps are disabled. Historical order-book depth is unavailable, so the live ₹1 spread cap and quote freshness checks cannot be reconstructed. No balance constraint or compounding is imposed.

These estimate strategy economics. They do not recreate the worker's historical uptime, late signals, configuration discrepancies, or every live risk check. In particular, today's unexplained planned-loss rejection was not inserted into past trades.

## Fees and execution costs

Estimated charges assume ₹30 per executed order (two orders per trade, conservative Upstox Plus assumption), NSE option transaction charges 0.03553% of premium turnover, IPFT ₹0.50/lakh, SEBI ₹10/crore, GST 18% on brokerage/exchange/IPFT, sell-side STT 0.15%, and buy-side stamp duty 0.003%. Rates apply to this post-April-2026 sample. Contract-note rounding, account-specific tariffs, and incidental charges may differ. Gross already includes the modeled price allowance; net subtracts these estimated fees. Actual bid/ask spreads and two-lot depth were not available.

## Exit comparisons with original entry rules

| Setting | Trades | Gross P&L | Estimated net P&L | Win rate | Max closed-trade drawdown | Training net | Validation net |
|---|---:|---:|---:|---:|---:|---:|---:|
| Stop ₹3 / target ₹6 | 114 | ₹43,250.00 | ₹28,459.57 | 41.23% | ₹32,023.83 | ₹-2,869.84 | ₹31,329.41 |
| Stop ₹4 / target ₹8 | 114 | ₹55,300.00 | ₹40,486.40 | 42.98% | ₹37,290.89 | ₹-8,808.33 | ₹49,294.73 |
| Stop ₹5 / target ₹10 | 114 | ₹90,100.00 | ₹75,219.35 | 48.25% | ₹35,658.63 | ₹13,199.21 | ₹62,020.14 |
| Stop ₹5 / target ₹15 | 114 | ₹95,300.00 | ₹80,409.34 | 48.25% | ₹36,829.10 | ₹33,260.49 | ₹47,148.85 |
| Stop ₹7 / target ₹14 | 110 | ₹93,400.00 | ₹78,854.82 | 50.91% | ₹52,399.12 | ₹37,688.16 | ₹41,166.66 |
| Stop ₹10 / target ₹18 | 107 | ₹92,850.00 | ₹78,579.13 | 51.40% | ₹58,179.28 | ₹34,967.20 | ₹43,611.93 |
| Stop 20% / target 40% | 114 | ₹61,650.00 | ₹46,824.22 | 46.49% | ₹45,644.97 | ₹7,659.93 | ₹39,164.29 |
| Stop 25% / target 50% | 112 | ₹83,250.00 | ₹68,726.99 | 49.11% | ₹51,184.30 | ₹13,843.06 | ₹54,883.93 |
| Stop 30% / target 60% | 100 | ₹64,150.00 | ₹51,525.61 | 48.00% | ₹43,163.00 | ₹5,719.38 | ₹45,806.23 |

Trade counts differ where the stop would be nonpositive or planned loss would exceed ₹10,000. Those are pre-entry rejections, listed in skipped.csv. The ₹10/₹18 configuration rejected seven low-premium signals; the ₹5/₹10 and ₹5/₹15 configurations each traded all 114.

## Entry-variable comparisons using ₹5/₹10

| Setting | Trades | Gross P&L | Estimated net P&L | Win rate | Max closed-trade drawdown | Training net | Validation net |
|---|---:|---:|---:|---:|---:|---:|---:|
| Baseline ₹5/₹10 | 114 | ₹90,100.00 | ₹75,219.35 | 48.25% | ₹35,658.63 | ₹13,199.21 | ₹62,020.14 |
| Skip expiry day; ₹5/₹10 | 108 | ₹98,750.00 | ₹84,411.61 | 49.07% | ₹30,482.41 | ₹18,967.66 | ₹65,443.95 |
| Entry cutoff 11 AM; ₹5/₹10 | 85 | ₹52,600.00 | ₹41,595.88 | 48.24% | ₹34,636.21 | ₹-1,098.68 | ₹42,694.56 |
| Entry cutoff noon; ₹5/₹10 | 102 | ₹75,950.00 | ₹62,686.74 | 47.06% | ₹36,405.58 | ₹5,206.82 | ₹57,479.92 |
| Entry cutoff 1 PM; ₹5/₹10 | 107 | ₹88,300.00 | ₹74,367.95 | 48.60% | ₹34,700.99 | ₹14,171.38 | ₹60,196.57 |
| Range at most 1.5%; ₹5/₹10 | 109 | ₹84,650.00 | ₹70,392.91 | 47.71% | ₹35,456.27 | ₹8,372.77 | ₹62,020.14 |
| Range at most 2%; ₹5/₹10 | 114 | ₹90,100.00 | ₹75,219.35 | 48.25% | ₹35,658.63 | ₹13,199.21 | ₹62,020.14 |
| Breakout buffer 0.05%; ₹5/₹10 | 112 | ₹71,200.00 | ₹56,590.62 | 49.11% | ₹39,747.40 | ₹1,057.47 | ₹55,533.15 |
| Breakout buffer 0.1%; ₹5/₹10 | 108 | ₹75,700.00 | ₹61,597.98 | 50.00% | ₹35,590.18 | ₹5,511.13 | ₹56,086.85 |

Opening-range width is (opening high − opening low) divided by opening price. Breakout buffers are fractions of the underlying boundary, not of option premium. Skipping expiry removes six trades, four losing and two winning; its apparent improvement rests on this small sample.

## Selection and validation

81 predefined combinations were evaluated: nine exit settings × nine entry filters. The selection rule was highest net training P&L with at least 20 training trades. It selected **₹7 stop / ₹14 target, entry cutoff 13:00**:

| Setting | Trades | Gross P&L | Estimated net P&L | Win rate | Max closed-trade drawdown | Training net | Validation net |
|---|---:|---:|---:|---:|---:|---:|---:|
| Training-selected configuration | 103 | ₹93,600.00 | ₹79,999.57 | 51.46% | ₹50,702.59 | ₹40,656.48 | ₹39,343.09 |
| Requested baseline | 114 | ₹90,100.00 | ₹75,219.35 | 48.25% | ₹35,658.63 | ₹13,199.21 | ₹62,020.14 |
| Expiry filter candidate | 108 | ₹98,750.00 | ₹84,411.61 | 49.07% | ₹30,482.41 | ₹18,967.66 | ₹65,443.95 |

The training-selected configuration earned ₹39,343.09 in validation versus the baseline's ₹62,020.14. Tuning did not beat the baseline on unseen-period profit. The expiry filter improved its corresponding baseline in both periods but was not the overall training winner, so highlighting it now is exploratory and needs new forward data. The highest full-sample net was ₹86,630.61 for skipping expiry with ₹7/₹14, but that ranking uses the validation period and must not be called an independently validated optimum.

No formal multiple-testing correction or statistical significance claim is made. Only 36 baseline trades lie in the validation period. Six months cannot establish performance across market regimes.

## Baseline monthly net P&L

| Month | Trades | Estimated net P&L |
|---|---:|---:|
| 2026-04 | 8 | ₹9,844.36 |
| 2026-05 | 19 | ₹19,716.65 |
| 2026-06 | 19 | ₹-23,722.29 |
| 2026-07 | 21 | ₹-171.81 |
| 2026-08 | 17 | ₹15,101.10 |
| 2026-09 | 20 | ₹8,759.48 |
| 2026-10 | 10 | ₹45,691.86 |

April and October are partial months. The baseline included 44 CE trades (₹35,125.60 net) and 70 PE trades (₹40,093.75 net). Exits: 16 targets, 28 stops, five adverse opening-gap stops, and 65 timed exits. Worst modeled net trade: −₹5,369.05. A ₹5,000 planned stop is not a guaranteed maximum loss.

## Execution-cost sensitivity: baseline

| Adverse allowance per side per unit | Trades | Estimated net P&L | Max closed-trade drawdown |
|---|---:|---:|---:|
| ₹0.00 | 113 | ₹93,444.16 | ₹33,309.37 |
| ₹0.05 | 113 | ₹84,448.00 | ₹34,459.08 |
| ₹0.10 | 114 | ₹75,219.35 | ₹35,658.63 |
| ₹0.25 | 114 | ₹41,343.92 | ₹43,648.53 |
| ₹0.50 | 114 | ₹-26,942.41 | ₹80,799.54 |

Stop and target prices are recalculated from each modeled entry, so different allowances can change the exit path and entry eligibility; these are complete resimulations, not a simple subtraction. The ₹0.50-per-side case loses money. This sensitivity is a practical reason to forward-test executable quotes before changing the running strategy.

## Data-quality limits

All selected option days contained a full sequence of in-session timestamp bars, but some were zero-volume carried-forward prices. The simulation never fills on zero-volume candles. 74 of 114 baseline trades encountered at least one zero-volume minute before exit; the largest cumulative count was 62. Price gaps, unavailable depth, partial fills, limit-order queues, and asynchronous signal/order timing make exact realized P&L unknowable from these candles. The displayed precision supports CSV reconciliation, not confidence in execution to the paise. Closed-trade drawdown may understate intraday mark-to-market risk.

## Files and reproduction

- baseline-trades.csv: all 114 baseline trades with symbols, strikes, entry/exit times, levels, charges and P&L.
- skip-expiry-trades.csv and training-selected-trades.csv: the two highlighted comparisons.
- comparison.csv: all 81 combinations, including separate training and validation metrics.
- trades.csv: all 8,403 simulated trade rows across combinations, not 8,403 distinct historical trades.
- signals.csv, skipped.csv, monthly.csv, slippage.csv and results.json: audit details.
- market-data.tar.gz and data-manifest.json: cached public-market responses and SHA-256 hashes. No tokens or account identifiers are archived.
- research.py: standalone research script, outside the application; no order APIs are called.
- validation.json: arithmetic, timing, quantity, risk-cap, uniqueness and split checks passed for all simulated rows. Synthetic stop/target/gap/fee cases were also checked.

From the repository root, extract market-data.tar.gz into a temporary directory, set ORB_RESEARCH_CACHE to its market-data subdirectory, and run `.venv/bin/python reports/backtests/reliance-2026-04-21_2026-10-20/research.py --offline`. Offline mode does not read broker tokens or connect to MySQL/Upstox. Outputs are regenerated in this report directory.

Sources: [Upstox expired candles](https://upstox.com/developer/api-documentation/get-expired-historical-candle-data/), [historical candles V3](https://upstox.com/developer/api-documentation/v3/get-historical-candle-data/), [Upstox fee schedule](https://upstox.com/brokerage-charges/), [NSE 2026 F&O holiday circular](https://nsearchives.nseindia.com/content/circulars/FAOP71777.pdf).
