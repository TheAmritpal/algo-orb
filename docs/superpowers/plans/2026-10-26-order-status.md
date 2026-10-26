# Order Status and Premium Exit Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Keep live order evidence current every 30 seconds and use -5/+8 premium exits.
**Architecture:** Use exact order-ID reads in the official SDK adapters. A worker-owned, read-only polling component updates known intents and audits position mismatches independently of GTT snapshot management.
**Tech Stack:** Python, official Upstox SDK, existing MySQL repositories and unittest.
**Spec:** docs/superpowers/specs/2026-10-26-order-status-design.md

## Global Constraints

No live orders or production writes during implementation/testing. Preserve uncertain state, identity validation, durable fills and blocked retries. Never log secrets.

## Review Focus

- Wrong order/account/instrument must not be accepted.
- API failures must not silently clear reconciliation or cause submissions.
- Completed orders must have complete trade evidence.
- Manual position changes must not produce duplicate sells or invented fills.
- Restart must trigger immediate checks; faster worker ticks must not overpoll.

### Task 1: Shared premium target

Files: app/strategies/rules.py; tests/test_strategy_rules.py; tests/test_gtt_repository.py; existing engine/session tests; README.md.
- [x] Change literal test expectations to average +8 and unchanged -5; run unittest and observe failures.
- [x] Change shared target constant to 8; update active documentation and confirm rule and execution tests pass.

### Task 2: Exact-ID status reads

Files: app/services/trading_broker.py; app/services/gtt_broker.py; tests/test_trading_broker.py; tests/test_gtt_broker.py.
Interfaces: Broker.reconcile(intent, *, verify_tag=True); GttBroker.child_order(record, leg, request).
- [x] Test exact-ID response acceptance and rejection of wrong IDs/identity, API failure and incomplete trade evidence.
- [x] Use SDK get_order_status(order_id=...) when ID is known; retain tag-based discovery only for missing IDs. Permit synthetic GTT tags only with an exact saved child ID.
- [x] Run broker adapter tests and inspect results.

### Task 3: Independent 30-second polling

Files: app/strategies/order_status.py; app/strategies/engine.py; tests/test_order_status.py; README.md.
Interfaces: OrderStatusPoller(repository, broker).poll(run, now) -> (updated_run, safe_to_manage).
- [x] Add tests for checks at 0/30/60 seconds, persistence of statuses/fills, completed orders, continued checks in reconciliation, API failures, position mismatches and immediate checks after restart.
- [x] Implement polling for live runs before execution management, with sanitized events and fail-closed results until the next check. Keep normal worker cadence and execution safeguards.
- [x] Run full unittest suite, lint if available, inspect diff and obtain independent review.

## Execution notes

User explicitly approved proceeding; execute inline without another approval round. Work in the current checkout to leave the requested fixes directly reviewable; no branch or production operations. Baseline pytest is unavailable; use the project's unittest suite.

Progress: Tasks 1–3 complete. Full suite: 323 tests, 11 skipped. Targeted Ruff checks pass. Independent review found two Important issues; both were reproduced with failing tests, fixed, and verified by the reviewer.
Ruling: Position polling records a mismatch but lets existing GTT synchronization discover newly generated exit children before enforcing the position guard. Blocking before discovery would strand legitimate GTT closes. All execution paths still compare reconciled exposure to broker positions before submitting an exit.
Ruling: No new UI design is involved; corrected only the existing dashboard's numeric target copy.

Review fixes: controlled GTT exits keep broker-tag validation; ordinary entries compare positions before partial-entry protective sells. All production access during this implementation was avoided. Changes remain uncommitted for user review.
