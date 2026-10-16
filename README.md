# Algo ORB

FastAPI application with Jinja2 templates, PyMySQL, and the
official Upstox Python SDK. Uses Python 3.13 and uv.

## Setup

```bash
uv sync --frozen
cp .env.example .env
uv run uvicorn app.main:app --reload
```

Open http://127.0.0.1:8000 for the home page and http://127.0.0.1:8000/docs
for API documentation. `/health` reports application liveness only.

`uv.lock` pins compatible dependency versions for reproducible installs.
Package installation requires access to PyPI or a populated uv cache.

## MySQL

Install or provision MySQL separately, create the `algo_orb` database and an
application user, then update the `MYSQL_*` values in `.env`. The Python driver
does not install a MySQL server or create databases.

For authentication that requires RSA support, install the optional dependencies
with `uv add 'pymysql[rsa]'` when network access is available.

`app.database.get_connection()` opens a connection only when called. Close it
after use. Missing settings raise a configuration error; connector failures
propagate to the caller. The starter routes do not require a running database.

## Upstox

The SDK imports as `upstox_client`. The `UPSTOX_*` settings in `.env.example`
reserve names for later authentication work; this scaffold does not authenticate
or submit broker requests. Keep `.env` and real credentials out of source control.

Live trading requires explicit implementation and authorization under AGENTS.md.
