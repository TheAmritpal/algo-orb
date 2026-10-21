# RELIANCE ORB: opening range caps × stop/target comparison

Period: 21 April–20 October 2026, 125 trading sessions. Two lots (1,000 units), nearest strictly ITM monthly option, expiry days skipped. Compare opening 09:15–09:30 RELIANCE share-price high minus low ≤ ₹24 or ≤ ₹20. Equality is eligible. The repeated ₹5/₹10 request is evaluated once per cap.

**Highest six-month net P&L:** ₹7 stop / ₹14 target with range ≤ ₹24: **₹86,573.26**, 105 trades, maximum closed-trade drawdown ₹46,144.02.

**Profit/drawdown comparison:** ₹5/₹10 with range ≤ ₹24 earns ₹84,354.26, just ₹2,219 less, with drawdown ₹15,661.61 lower (₹30,482.41). ₹3/₹6 with range ≤ ₹24 has the smallest drawdown (₹28,843.75) but earns substantially less (₹47,525.33). No claim is made that the historical winner is an optimal future setting.

| Opening range cap | Stop / target | Trades | Gross P&L | Charges | Estimated net P&L | Win rate | Max closed-trade drawdown | First four months net | Final two months net |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| ≤ ₹24 | ₹3 / ₹6 | 107 | ₹61,650.00 | ₹14,124.67 | ₹47,525.33 | 42.99% | ₹28,843.75 | ₹9,827.79 | ₹37,697.54 |
| ≤ ₹24 | ₹5 / ₹10 | 107 | ₹98,550.00 | ₹14,195.74 | ₹84,354.26 | 48.60% | ₹30,482.41 | ₹18,910.31 | ₹65,443.95 |
| ≤ ₹24 | ₹5 / ₹15 | 107 | ₹98,750.00 | ₹14,196.12 | ₹84,553.88 | 48.60% | ₹32,869.63 | ₹33,981.22 | ₹50,572.66 |
| ≤ ₹24 | ₹10 / ₹18 | 103 | ₹93,550.00 | ₹13,829.77 | ₹79,720.23 | 51.46% | ₹57,921.29 | ₹25,930.51 | ₹53,789.72 |
| ≤ ₹24 | ₹7 / ₹14 | 105 | ₹100,600.00 | ₹14,026.74 | ₹86,573.26 | 51.43% | ₹46,144.02 | ₹38,223.03 | ₹48,350.23 |
| ≤ ₹20 | ₹3 / ₹6 | 105 | ₹49,850.00 | ₹13,852.19 | ₹35,997.81 | 41.90% | ₹34,580.83 | ₹-1,699.73 | ₹37,697.54 |
| ≤ ₹20 | ₹5 / ₹10 | 105 | ₹86,450.00 | ₹13,922.68 | ₹72,527.32 | 47.62% | ₹40,211.78 | ₹7,083.37 | ₹65,443.95 |
| ≤ ₹20 | ₹5 / ₹15 | 105 | ₹85,000.00 | ₹13,919.88 | ₹71,080.12 | 47.62% | ₹43,029.07 | ₹20,507.46 | ₹50,572.66 |
| ≤ ₹20 | ₹10 / ₹18 | 101 | ₹79,800.00 | ₹13,553.53 | ₹66,246.47 | 50.50% | ₹62,651.30 | ₹12,456.75 | ₹53,789.72 |
| ≤ ₹20 | ₹7 / ₹14 | 103 | ₹86,850.00 | ₹13,750.50 | ₹73,099.50 | 50.49% | ₹53,458.95 | ₹24,749.27 | ₹48,350.23 |

The ₹24 cap outperformed the ₹20 cap for every listed exit setting in this sample. Wider fixed stops reject some low-premium signals because the stop would be nonpositive: two rejected signals for ₹7 stops and four for ₹10 stops under each cap. The other pairs have no entry rejections. The configured ₹10,000 planned-loss cap is retained.

The first-four-month period ends 20 August; the final-two-month period begins 21 August. The ₹7/₹14 pair has the highest full-period P&L, while ₹5/₹10 has the highest final-two-month P&L (₹65,443.95, identical under both caps). These periods were already viewed in earlier comparisons, so they are not a fresh untouched validation set for this ranking. Repeatedly selecting settings on these data risks overfitting.

## Execution-cost sensitivity for the full-period winner

| Adverse allowance per side per unit | Trades | Estimated net P&L | Max closed-trade drawdown |
|---|---:|---:|---:|
| ₹0.05 | 105 | ₹98,263.13 | ₹45,044.51 |
| ₹0.10 | 105 | ₹86,573.26 | ₹46,144.02 |
| ₹0.25 | 105 | ₹37,430.49 | ₹51,058.20 |
| ₹0.50 | 106 | ₹-9,879.04 | ₹79,444.09 |

## Method and limitations

Each combination was replayed from the same archived Upstox one-minute option candles and 15-minute underlying candles. All 147 source-response hashes were checked. All five uncapped expiry-skip references were independently reproduced, and both ₹5/₹10 capped results matched the previous reports.

Entry uses the first post-signal minute open plus ₹0.10 per unit; exit deducts ₹0.10 per unit. Stop/target levels are based on modeled entry and must fit the ₹0.05 tick. Stop gaps take a worse traded opening price; stop wins if a bar touches both thresholds. No such ambiguous bars occur in this comparison. One entry per day, entry cutoff 15:00, and timed exit at the first traded minute at/after 15:25 before session close. No capital compounding or balance cap is assumed.

Gross already includes this execution allowance. Net subtracts estimated charges: ₹30 per executed order plus the previously documented NSE transaction charges, GST, STT, stamp duty, SEBI and IPFT. Minute candles lack historical bid/ask depth and limit-order queues. Zero-volume bars are not filled; gaps, full-size execution and the live spread check cannot be recreated precisely. Closed-trade drawdown excludes intratrade losses. Displayed currency precision supports reconciliation, not exact executable results.

## Artifacts and verification

`comparison.csv` and `results.json`: all ten settings and period metrics. `trades.csv`: all 1,048 simulation rows across combinations, not distinct market trades. `monthly.csv`: monthly results per setting. `skipped.csv`: rejected entry decisions. `slippage.csv`: four execution-allowance scenarios for every combination.

Run `.venv/bin/python reports/backtests/reliance-2026-04-21_2026-10-20-cap-exit-comparison/research.py` from the repository root to reproduce offline. This uses the sibling original report’s archived market data and model. No broker or database calls, live orders, or application settings changes are involved.

Independent CSV verification checked quantities, uniqueness, cap/expiry eligibility, stop and target distances, planned-loss limits, trade arithmetic, all aggregated totals, and first/final-period reconciliation.
