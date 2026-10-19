# Upstox GTT Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** Execute RELIANCE ORB through broker-managed GTT with TP +18 and SL -10 premium points.

**Architecture:** Keep candle confirmation in StrategyEngine. Route new entries and recovery by persisted execution backend to a GTT coordinator, using a dedicated SDK adapter and transactional repository. Persist GTT mutations and generated exchange orders separately; reuse the existing fill ledger and bounded emergency exits.

**Tech Stack:** Python, official Upstox SDK, MySQL, Alembic, uv, unittest.

**Spec:** docs/superpowers/specs/2026-10-19-upstox-gtt-design.md

## Global Constraints

- TP +18 and SL -10 premium points from confirmed average fills.
- Two lots, one reserved attempt per day, 15:00 entry cutoff, 15:25 timed exit.
- No live orders during development; GTT sandbox unsupported.
- Persist before mutation; never retry uncertain creation; secrets never logged.
- Existing ordinary-order runs recover by their stored backend.

## Review Focus

- GTT zero-position parents with active triggers remain unresolved.
- Quantity is immutable for open GTT; partial entry requires confirmed cancellation and flattening.
- Exchange orders generated during cancellation must be reconciled before flattening.
- Lost creation acknowledgement must never produce another entry.
- Historical runs keep their stored backend and premium levels during recovery.

### Task 1: Domain, persistence and configuration

**Files:** app/strategies/models.py, app/strategies/rules.py, app/core/trading.py, app/repositories/strategy.py, new app/repositories/gtt.py, new migrations/versions/0005_gtt_execution.py; tests/test_gtt_repository.py and existing settings/rules tests.

**Interfaces:** Produce GttRecord, GttLeg, GttSnapshot; GttRepository.reserve(run, request, stop, target), get(run_id), acknowledge(run_id, gtt_id), mutation(run_id, kind, fields), mutation_result(id, status), observe(run_id, snapshot), attach_child(run_id, leg, request). Store execution_backend on StrategyRun; default old rows to orders.

- [x] Write failing tests for +18/-10 levels, backend parsing, persisted round trips and offline migration SQL.
- [x] Run `.venv/bin/python -m unittest tests.test_gtt_repository tests.test_strategy_rules tests.test_trading_settings -q`; expect missing domain/schema or old target failures.
- [x] Implement domain validation, backend configuration and Alembic schema. Reuse strategy_orders/fills for generated orders; parent mapping owns both exit intents.
- [x] Run the same tests; expect PASS. Preserve active historical levels.

### Task 2: SDK adapter and execution coordination

**Files:** new app/services/gtt_broker.py, new app/strategies/gtt.py; tests/test_gtt_broker.py, tests/test_gtt_engine.py and shared test helpers.

**Interfaces:** GttBroker.place(record), snapshot(record), modify(record, stop, target), cancel(record), child_order(record, leg, request). GttExecution.enter(run, request, now), manage(run, now) uses persisted records, underlying engine ownership and bounded exit.

- [x] Write failing tests for SDK payloads and child identity checks, exact fills, deduplicated trades and sanitized uncertainty.
- [x] Run `.venv/bin/python -m unittest tests.test_gtt_broker tests.test_gtt_engine -q`; expect missing implementation failures.
- [x] Implement MULTIPLE/IMMEDIATE entry and both exits; market_protection=-1. Read back modifications and cancellations, inspect child fills, keep uncertain mutations durable and never repeat create.
- [x] Test full fill/reanchor, server-triggered target, partial fill cancellation/flattening, sibling cancellation races, stale pricing, timed exit, restart and zero-position active parent. Expect PASS without any broker network calls.

### Task 3: Worker integration, simulation and regression verification

**Files:** app/strategies/engine.py, app/cli/run_strategy.py, new app/services/paper_gtt.py, app/services/strategy_status.py, .env.example, README.md; strategy worker/session/status and GTT regression tests.

**Interfaces:** Wire execution adapter via StrategyEngine optional gtt_broker parameter. Route recovery by run.execution_backend. PaperGttBroker shares the existing PaperBroker ledger and simulates GTT lifecycle; never submit real mutations in paper mode.

- [x] Write failing tests for configured GTT entry, backend recovery across setting changes, paper GTT, GTT status and unchanged live enablement gates.
- [x] Run the targeted tests; expect routing/setup failures.
- [x] Integrate backend selection, paper simulator, documentation and TP +18 presentation. Keep existing plain-order tests explicitly on orders backend.
- [x] Run `.venv/bin/python -m unittest discover -s tests -t . -q`; expect PASS (opt-in MySQL tests may skip). Compile Alembic SQL and run available lint checks.
- [x] Review the whole diff independently, fix material findings with regression tests, report GTT broker validation unavailable and live trading unchanged.

Execution: user explicitly requested implementation in this session. Implement inline, leave changes reviewable without committing or enabling live trading. Maintain progress in docs/superpowers/plans/2026-10-19-upstox-gtt-progress.md.
