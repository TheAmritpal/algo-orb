# GTT Entry Retries Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans inline; test-first implementation with a final independent review.

**Goal:** Retry confirmed unfilled GTT timeouts twice without duplicate exposure.

**Architecture:** Retain the active-parent interface, archive prior attempts,
scope child/mutation journals by attempt, and reprice only after cancellation.

**Tech Stack:** Python, official Upstox SDK, MySQL, Alembic, unittest.

**Spec:** docs/superpowers/specs/2026-10-23-gtt-entry-retries-design.md

## Global Constraints

- Three attempts maximum; 30 seconds per attempt; entry cutoff 15:00 IST.
- Zero fills and exposure required; unknown outcomes never authorize re-entry.
- No live orders or changes to the connected production database.
- Preserve user configuration and credentials; do not commit private env files.

## Review Focus

- A fill racing cancellation must flatten rather than start another entry.
- Crash after timeout authorization/cancellation must retain the attempt budget.
- Old parents/children must not be mistaken for the active attempt.
- Time passing during risk checks/persistence must prevent stale submissions.
- Concurrency/stale run versions must not reserve two replacements.

### Task 1: Durable attempts and atomic replacement

Files: migrations/versions/0009_gtt_entry_retries.py,
app/strategies/gtt_models.py, app/repositories/gtt.py,
tests/test_gtt_repository.py, tests/test_mysql_strategy_history.py.

- [x] Write failing persistence/migration and race-guard tests.
- [x] Add attempt and retry authorization fields, history archive, and
      attempt-scoped mutation/child mappings with historical defaults of 1.
- [x] Implement authorize_retry(run, record) and reserve_retry(run, record,
      request, stop, target), with row lock/version/zero-fill/terminal checks.
- [x] Verify repository tests and migrations against disposable MySQL only.

### Task 2: Retry orchestration and recovery

Files: app/strategies/gtt.py, tests/gtt_helpers.py,
tests/test_gtt_engine.py, tests/test_gtt_retries.py.

- [x] Write failing tests for fresh-price second/third attempts and no fourth.
- [x] Persist timeout authorization before cancellation; drain parent and
      children; repeat session/risk/funds/protection/exposure checks.
- [x] Reserve replacement before broker submission; preserve existing unknown
      mutation and partial-fill handling. Recheck cutoff and quote age.
- [x] Verify cancel-fill races, rejected/stale quotes, restart budget, pending
      creation/cancellation, cutoff/date rollover, stopped entries and no reentry.

### Task 3: Deployment guidance and verification

Files: deploy/README.md, README.md.

- [x] Document policy and required 0009 migration with worker stopped.
- [x] Run complete unittest suite and isolated MySQL integration suite.
- [x] Run formatting/lint/diff checks and independent review; fix findings.
- [x] Transfer reviewed changes to original checkout without user config edits.

Progress is recorded here; no automatic commits or live deployment.

## Verification ledger

- Baseline: 272 unittest tests, OK (5 MySQL tests skipped).
- RED: new timeout retry test observed old request price 100 rather than fresh
  102; migration test observed missing attempt archive.
- GREEN: three-attempt budget, repricing/levels, recovery after confirmed cancel,
  uncertainty, partial fills, late fills and fresh risk checks pass.
- Timing regressions RED then GREEN: quote expires during reservation; cutoff
  reached exactly at 15:00 during reservation. Retries use quote timestamp and
  recheck the strict cutoff immediately before submission.
- Independent review: two important findings, both fixed with RED-to-GREEN
  tests: any-product same-contract exposure blocks retry; uncertainty is a
  persisted retry disqualification across run-state recovery and restarts.
- Disposable MySQL 8.4: fresh schema upgrade; 0008 parent/journal retained through
  0009 upgrade and unused downgrade; used attempt history refuses downgrade.
- Ruling: work in isolated feature worktree, transfer reviewed files to the
  user's checkout without commits/deployment or touching .codex/config.toml.
  This preserves the requested editable diff; no automatic branch integration.

- Final verification in both isolated workspace and original checkout: 299 tests
  passed with ORB_MYSQL_TESTS=1 against disposable MySQL, no skips.
- Changed Python files: Ruff clean and formatted; git diff --check clean.
  Repository-wide Ruff reports 7 pre-existing findings in broker_readiness.py,
  strategy_status.py, rules.py, 0001_create_upstox_tokens.py,
  test_broker_readiness.py and test_strategy_engine.py; those files are untouched.
