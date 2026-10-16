# FastAPI project setup

## Goal

Create a runnable Python project managed with uv, using FastAPI and Uvicorn,
Jinja2 templates, MySQL, and the official Upstox Python SDK. This is the
foundation for the real-money algorithmic trading application described in
AGENTS.md.

## Scope and dependencies

Use the available Python 3.13 interpreter and uv to create pyproject.toml,
uv.lock, and a local .venv. Install fastapi, uvicorn[standard], jinja2,
mysql-connector-python, upstox-python-sdk, and python-dotenv. Resolve compatible
versions through uv and record the result in the lockfile.

Use the MySQL connector directly for this initial scaffold; an ORM is outside
this setup's scope. Install the Upstox SDK without implementing broker calls or
order execution.

## Files and behavior

- app/__init__.py: application package.
- app/main.py: FastAPI application with GET / rendering a Jinja2 starter page
  and GET /health returning a simple application liveness response.
- app/templates/index.html: minimal project welcome page.
- app/config.py: environment-based MySQL configuration loaded from .env.
- app/database.py: an explicit connection helper using mysql.connector and
  environment configuration. Connections are opened only when requested;
  callers are responsible for closing them.
- .env.example: placeholder MySQL settings and Upstox credentials/token names.
- .gitignore: exclude .venv, .env, Python caches, and local tool caches.
- README.md: environment setup, MySQL prerequisites, and the command
  `uv run uvicorn app.main:app --reload`.

Template paths resolve relative to the application module. The web application
starts without broker credentials or an active database connection. Liveness
does not imply database or broker connectivity. The connection helper reports
missing required database configuration clearly and propagates connector
errors without logging secrets.

## Credentials and live orders

Keep credentials in local environment variables or an ignored .env file.
Never include actual credentials in examples or output. This scaffold does
not send broker requests or place, modify, or cancel orders.

## Verification

Confirm uv can sync the locked dependencies and that all requested packages
import successfully. Smoke-test the ASGI application's home page and health
route, including template rendering. Verify that importing the application
does not open a database connection or contact Upstox. Actual database
connectivity requires the user's MySQL instance and credentials and is outside
the scaffold smoke test.
