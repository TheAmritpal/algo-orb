# Algo ORB

Clean trading foundation for Upstox. There is **no strategy yet** and no route
or CLI can submit a live order. The application provides login, Upstox OAuth,
MySQL persistence, a safe order gateway, and a paper broker.

Stack: Python 3.13, uv, FastAPI, Jinja2, MySQL, Alembic, the official Upstox
Python SDK, pytest, and ruff.

## Setup

```bash
uv sync
test -e .env || cp .env.example .env
# Fill in real values in .env. Never commit it.
uv run alembic upgrade head
uv run python -m app.cli.sync_admin
uv run uvicorn app.main:create_app --factory --reload --no-access-log
```

Open http://localhost:8000/login. `/health` is a liveness check only.

## Safety

- `TRADING_MODE=paper` and `TRADING_ENABLED=false` by default.
- Every order must pass through `OrderGateway`, which validates limits, writes
  an audit row, and calls the broker once.
- `MARKET` orders are rejected until a reference-price rule exists.
- No live order endpoint exists in this foundation.

## Database

Create the `algo_orb` database and an application user separately. Alembic
owns the schema; handlers never create or alter tables.

```bash
uv run alembic upgrade head
uv run alembic current
```

Optional MySQL integration tests use a separate `algo_orb_test` database and
skip when it is unavailable.

## Tests

```bash
uv run ruff check .
uv run pytest
```

Tests use fakes and never contact Upstox.

## Production

See [deploy/README.md](deploy/README.md) for Docker Compose, host Nginx/MySQL,
migrations, and the admin sync command. There is no strategy worker.
