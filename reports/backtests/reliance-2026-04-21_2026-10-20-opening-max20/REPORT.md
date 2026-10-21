# RELIANCE ORB: opening range ≤ ₹20

Period: 21 April–20 October 2026; 125 trading sessions. The 09:15–09:30 RELIANCE equity candle high minus low must be at most ₹20. Exactly ₹20 remains eligible. Other rules remain ₹5 stop, ₹10 target, two lots, nearest ITM monthly option, expiry days skipped, one entry per day and scheduled 15:25 exit.

Estimated net P&L: **₹72,527.32**. Gross after modeled slippage: ₹86,450.00. Estimated charges: ₹13,922.68. Trades: 105. Win rate: 47.62%. Maximum closed-trade drawdown: ₹40,211.78.

| Opening range filter | Trades | Estimated net P&L | Maximum closed-trade drawdown |
|---|---:|---:|---:|
| None | 108 | ₹84,411.61 | ₹30,482.41 |
| ≤ ₹24 | 107 | ₹84,354.26 | ₹30,482.41 |
| ≤ ₹20 | 105 | ₹72,527.32 | ₹40,211.78 |

The ₹20 filter removes three previously profitable trades totalling ₹11,884.29 net. Five sessions have ranges above ₹20; two of those already had no signal. Six expiry days and nine other no-signal sessions are also excluded. Compared with the ₹24 cap, the ₹20 cap reduces net P&L by ₹11,826.94 and increases drawdown.

## Monthly results

| Month | Trades | Estimated net P&L |
|---|---:|---:|
| 2026-04 | 7 | ₹64.25 |
| 2026-05 | 17 | ₹15,163.50 |
| 2026-06 | 18 | ₹-18,529.99 |
| 2026-07 | 18 | ₹2,853.31 |
| 2026-08 | 16 | ₹20,288.52 |
| 2026-09 | 19 | ₹6,995.87 |
| 2026-10 | 10 | ₹45,691.86 |

April and October are partial months.

## Removed trades

| Date | Opening range | Previous net P&L |
|---|---:|---:|
| 2026-05-29 | ₹20.3 | ₹9,729.37 |
| 2026-07-08 | ₹24.3 | ₹57.35 |
| 2026-07-24 | ₹21.2 | ₹2,097.57 |

Recomputed offline from the same archived Upstox candles and model as the previous runs. All 147 source-response hashes verified; the no-range-filter result reproduced before comparison. CSV net, gross, charges, monthly totals, eligibility and session counts reconciled independently.

Assumptions remain ₹0.10 adverse execution per unit per side plus estimated charges including ₹30 per order. Historical minute candles cannot recreate bid/ask depth, partial fills, quote freshness or the live spread filter. Sixty-eight modeled trades encountered zero-volume minutes. Drawdown uses closed trades, excluding intratrade losses. These are estimates, not actual trading P&L.

The previously viewed training/validation split is retained for reference: ₹7,083.37 training net and ₹65,443.95 validation net. This threshold comparison is exploratory because the validation outcomes were already viewed.

Reproduce from repository root: `.venv/bin/python reports/backtests/reliance-2026-04-21_2026-10-20-opening-max20/research.py`. No network, database access or orders are involved. Application strategy settings were not changed.
