# Order status checks and premium exits

Approved in conversation: check known live order IDs every 30 seconds using the official Upstox orders API, including runs awaiting reconciliation. Persist verified statuses and fills, record sanitized check outcomes, and compare broker positions to recorded exposure. A missing or inconsistent response must never authorize another order. A possible manual exit remains explicitly unresolved; do not invent exit fills or realized P&L.

Use the shared premium calculation for SL = entry premium - 5 and TP = entry premium + 8, initially from the entry request and then from actual average fills. Existing completed trades are not rewritten.

Approach: retain the worker and its existing execution safeguards; add an independent read-only polling step before execution management and exact-ID reads at broker boundaries. A separate thread would complicate execution ownership; changing the whole worker to 30 seconds would delay protection and entry processing.

Scope: code, documentation and mocked tests only. No production database corrections, order mutations, deployment or worker restart.
