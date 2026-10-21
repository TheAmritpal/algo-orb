# RELIANCE ORB: opening range at most ₹24

Period: 21 April–20 October 2026, the same six-month sample as the previous comparison (125 sessions; last trading session 19 October).

Rules: ₹5 stop, ₹10 target, two historical lots (1,000 units), skip expiry days, skip a session when the opening 09:15–09:30 RELIANCE equity candle high minus low is strictly greater than ₹24. Exactly ₹24 remains eligible. This is an absolute high–low range, not the candle body, a percentage, or the option candle range.

Estimated P&L: **₹84,354.26 net**, from 107 trades. Gross after modeled execution allowance: ₹98,550.00. Estimated charges: ₹14,195.74. Net win rate: 48.60%. Maximum closed-trade drawdown: ₹30,482.41. Maximum modeled premium outlay: ₹43,500.

Without the ₹24 range filter, but retaining the same ₹5/₹10 exits and expiry skip, the result was ₹84,411.61 net from 108 trades. The new filter reduces net P&L by ₹57.35 and leaves maximum drawdown unchanged.

## Monthly net P&L

| Month | Trades | Estimated net P&L |
|---|---:|---:|
| 2026-04 | 7 | ₹64.25 |
| 2026-05 | 18 | ₹24,892.87 |
| 2026-06 | 18 | ₹-18,529.99 |
| 2026-07 | 19 | ₹4,950.88 |
| 2026-08 | 16 | ₹20,288.52 |
| 2026-09 | 19 | ₹6,995.87 |
| 2026-10 | 10 | ₹45,691.86 |

April and October are partial months.

## Excluded wide-range sessions

| Date | Opening high–low range | Previous trade result |
|---|---:|---:|
| 2026-06-22 | ₹30.8 | No signal |
| 2026-07-08 | ₹24.3 | ₹57.35 net |
| 2026-07-20 | ₹31.0 | No signal |

Six expiry sessions were also skipped; nine other sessions had no signal. The three wide-range sessions include two that would not have traded anyway. Only the 8 July trade is removed by this new filter.

## Method and limits

This rerun recomputes signals and option outcomes from the previously archived Upstox data using the same model. All 147 archived-response SHA-256 hashes were verified, and the previous no-range-filter result was reproduced before comparing. No broker/database access or orders were needed.

Entry is the signal-time option minute open plus ₹0.10 per unit; exits deduct ₹0.10 per unit. Levels fit ₹0.05 ticks. One entry per day, nearest strictly ITM monthly CE/PE, entry cutoff 15:00, scheduled exit at the first traded minute at/after 15:25 before session end. A same-bar stop/target tie favors the stop. The ₹10,000 planned-loss cap remains enforced.

Fees use the previous conservative estimate: ₹30 per executed order, plus NSE transaction charges, GST, STT, stamp duty, SEBI and IPFT charges. Gross already includes the execution allowance; net then subtracts fees. These are historical-candle estimates, not actual bid/ask fills. Seventy trades encountered zero-volume minutes before exit. Partial fills, depth, the live spread check, and intratrade drawdown cannot be recreated.

The original split remains: training net ₹18,910.31 (73 trades), validation net ₹65,443.95 (34 trades). Since this new threshold was requested after examining prior results, the previously viewed validation period is no longer an untouched test for selecting this new filter.

Files: `trades.csv` contains all 107 simulated trades; `daily-decisions.csv` records all 125 sessions; `excluded-days.csv` records the three wide-range sessions; `monthly.csv` and `results.json` contain summaries. Run `.venv/bin/python reports/backtests/reliance-2026-04-21_2026-10-20-opening-max24/research.py` from the repository root to reproduce offline. It uses the sibling original report’s archived dataset and model.

No application strategy settings were changed.
