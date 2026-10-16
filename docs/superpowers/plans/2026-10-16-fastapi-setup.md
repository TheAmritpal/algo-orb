# FastAPI Setup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Create and verify a runnable FastAPI starter with the requested dependencies installed through uv.

**Architecture:** A small application package serves a Jinja2 home page and a liveness route. Environment configuration and an on-demand MySQL connection helper stay separate from HTTP routes. Broker integration is limited to installing the SDK.

**Tech Stack:** Python 3.13, uv, FastAPI, Uvicorn, Jinja2, mysql-connector-python, upstox-python-sdk, python-dotenv.

**Spec:** `docs/superpowers/specs/2026-10-16-fastapi-setup-design.md`

## Global Constraints

- Use the available Python 3.13 interpreter and uv.
- Record compatible dependency versions in uv.lock and install into .venv.
- Keep credentials in environment variables or an ignored .env file.
- Start without database connectivity or broker credentials.
- Do not send broker requests or place, modify, or cancel orders.
- Git operations are unavailable in the current workspace; work in place without commits.

## Review Focus

- Missing MySQL settings: fail clearly when a connection is requested, not during application startup.
- Invalid MySQL port: report a configuration error without disclosing credentials.
- Alternate working directory: resolve templates relative to the application module.
- Unavailable database: propagate connector errors only from an explicit connection request.
- Missing Upstox credentials: application startup and liveness still work.

### Task 1: Runnable project and dependencies

**Files:** Create `pyproject.toml`, `uv.lock`, `.python-version`, `.gitignore`, `.env.example`, `README.md`, `app/__init__.py`, `app/main.py`, `app/templates/index.html`.

**Interfaces:** Produce `app.main:app`, `GET /` (HTML welcome page), and `GET /health` (`{"status": "ok"}`).

- [x] Initialize an application project with uv, Python 3.13, and no generated Git repository.
- [x] Install fastapi, uvicorn[standard], jinja2, mysql-connector-python, upstox-python-sdk, and python-dotenv.
- [x] Implement the starter routes and module-relative template path.
- [x] Document `uv sync --frozen` and `uv run uvicorn app.main:app --reload`; ignore secrets and local caches.
- [x] Smoke-test routes without credentials or external connections, including template rendering from another working directory. Use a temporary verification script rather than permanent tests for this low-impact scaffold.

### Task 2: MySQL configuration helper and final checks

**Files:** Create `app/config.py`, `app/database.py`; complete `.env.example` and `README.md`.

**Interfaces:** `get_mysql_config() -> dict[str, str | int]` reads MYSQL_HOST, MYSQL_PORT (default 3306), MYSQL_USER, MYSQL_PASSWORD, MYSQL_DATABASE. `get_connection()` returns a MySQL connection; the caller closes it.

- [x] Load .env relative to the project root without overriding existing environment values. Validate required settings and port range before connecting.
- [x] Implement a connection helper that calls mysql.connector.connect only when explicitly requested; preserve connector exceptions.
- [x] Add placeholder MySQL settings and UPSTOX_API_KEY, UPSTOX_API_SECRET, UPSTOX_ACCESS_TOKEN to .env.example.
- [x] Verify missing settings and invalid ports raise clear errors; use isolated environment values so no local secrets or external services are touched.
- [x] Confirm imports do not connect to MySQL or Upstox, and the helper propagates a simulated connector error without logging credentials.
- [x] Run `uv sync --frozen`, package import checks, and Uvicorn startup and route smoke checks. Report that actual MySQL connectivity requires a configured server.

## Execution result

Completed using cached packages, with PyMySQL replacing MySQL Connector/Python and core Uvicorn replacing its standard extras. Frozen sync, imports, ASGI routes, configuration checks, and fresh code review passed. Uvicorn application startup completed; the sandbox denies socket binding, so HTTP serving could not be verified here. No actual MySQL or Upstox connections were attempted. Git metadata is unavailable, so files remain in the supplied workspace without commits.
