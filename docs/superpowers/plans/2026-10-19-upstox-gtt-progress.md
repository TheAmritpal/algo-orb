# SDD ledger — plan: docs/superpowers/plans/2026-10-19-upstox-gtt.md

Execution: inline, as explicitly requested by the user. Work remains in the shared checkout; no commits, deployment or live order submission.

Pre-flight: Task 1 produces persisted parent/leg/mutation identities consumed by Task 2; Task 3 routes by stored execution_backend. Interfaces agree. Ordinary exchange orders and GTT parent identities remain separate.

Task 1: Domain/configuration, +18/-10 rules and offline Alembic tests RED→GREEN (13 tests); parent snapshot/mutation serialization and downgrade checks pass. No database schema has been applied.

Task 2: SDK adapter tests RED→GREEN; coordinator tests RED→GREEN. Confirmed mutation uncertainty prevents repeat creation/modification/cancellation; child fill evidence precedes position updates.

Task 3: Worker wiring, CE/PE paper GTT sessions, server-triggered stop/target, backend change on restart, timing and status projection tests pass. Whole-suite verification and independent review complete.

Verification note: The baseline discovery command needs `-t .` because tests/http.py otherwise shadows Python's http package. This is a test invocation correction, not a product defect.

Ruling: GTT's documented IMMEDIATE limit-entry semantics supersede the earlier market-entry discussion; the approved design records this explicitly. Cost: entry can remain unfilled.

Ruling: A confirmed partial GTT fill is flattened after parent/child cancellation rather than increasing or resizing GTT quantity, which the API restricts for open orders. Cost: partial trades may exit early.

Ruling: No real GTT probe is possible in sandbox; use mocked SDK and deterministic paper tests and retain live enablement/account verification gates. Cost: live broker lifecycle remains unverified.


Final review: one important finding reproduced before fixing: unresolved protection modification prevented cancellation/timed exit and sibling cleanup. Regression test test_unknown_modify_still_allows_confirmed_timed_exit failed first (cancel calls zero), then passed after cancellation could atomically supersede the pending modification. Additional broker-exit cleanup and journal-atomicity tests pass. No reviewer findings deferred.

Final: fixed unresolved-modification recovery — pending modification is preserved in the journal as superseded only when cancellation is durably reserved or the parent is already terminal; creation/cancellation uncertainty still cannot be superseded by another cancellation. Cost if wrong: duplicate mutation or delayed flattening; dedicated regressions exercise both boundaries.

Final verification: `.venv/bin/python -m unittest discover -s tests -t . -q` → 185 tests run, no failures, 2 opt-in MySQL tests skipped. Ruff check passes; 15 touched/new Python files pass formatting checks; compileall and git diff --check pass. Alembic SQL compilation covered by upgrade/downgrade tests. MySQL migration has NOT been applied, settings in .env have NOT been changed, and no live orders were sent. Changes are uncommitted in the shared checkout; existing reports remain untouched.
