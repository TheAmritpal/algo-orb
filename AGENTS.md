# Instructions for LLM Agents

## Project purpose

This project is an algorithmic trading application that places real, live-money
orders through Upstox. The current foundation contains **no strategy** and cannot
place a live order; a future strategy will be added deliberately.

## Required technology stack

- Python with FastAPI for the backend and HTTP routes.
- uv for project and dependency management; run with Uvicorn.
- Jinja2 for server-rendered pages.
- MySQL as the database.
- Alembic for schema changes; never create or alter tables in request handlers.
- The official Upstox Python SDK for broker integration and trade execution.

## Architecture

- `app/core` — settings, logging/redaction, security helpers, typed errors.
- `app/repositories` — PyMySQL data access.
- `app/services` — auth, Upstox OAuth, risk, readiness, order gateway.
- `app/brokers` — `OrderBroker` protocol, paper broker, Upstox SDK adapter.
- `app/routers` — public pages and auth/Upstox routes.
- `migrations/` — Alembic schema.
- `tests/` — fakes only; no network or broker calls.

## Development instructions

- Keep credentials and database passwords in environment variables or `.env*`;
  never commit, print, or log them. `.env*` is gitignored.
- Validate order inputs and configured limits before any broker call.
- Route all order writes through `OrderGateway`; it audits `INTENT` before the
  broker call and records `ACCEPTED`/`REJECTED`/`UNKNOWN` after it.
- On an ambiguous broker response, record `UNKNOWN` and never retry
  automatically. Query order status first.
- Treat order placement, modification, and cancellation as real-money actions.
- Use mocks or sandbox facilities for tests. Do not submit live orders while
  developing unless the user explicitly authorizes the specific activity.
- Record order requests, broker responses, and status changes without secrets.
- Run `uv run ruff check .` and `uv run pytest` before claiming completion.
