# Bounded GTT entry retries

Approved policy: at most two additional attempts (three total) for an unfilled
GTT entry, after 30 seconds per attempt. Same contract and original signal;
refresh option quote and repeat session, account/protection, funds, position and
configured risk checks. Stop at 15:00 and never carry retry eligibility into
another exchange date. Entry minus 5 / plus 10 remains unchanged.

Retry only a known timeout with no fills, after parent triggers and generated
orders are confirmed cancelled/terminal and broker exposure is zero. Broker
rejections, partial fills, uncertain mutations, stopped entries, and previously
reconciliating runs cannot authorize a retry. Partial fills retain the existing
flattening path. Once any entry fills, no further entry is allowed that day.

Keep an active GTT per run for compatibility; add an attempt number and durable
timeout authorization. Archive each resolved previous parent transactionally
when reserving its replacement. Scope pending mutations and child mappings by
attempt, keeping every old mutation, child, order and fill. Reserve the new
attempt and its create journal atomically before the single broker submission.
Never replay an uncertain creation. Worker restarts use persisted attempt count,
timeout authorization and journals. No live orders, production migrations,
database-mode changes or deployments are part of implementation.

Alternatives: unlimited retries risk chasing the market; a new-candle-only retry
changes signal selection. User accepted the recommended two-timeout-retry policy.
