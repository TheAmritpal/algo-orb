# Clean Foundation Verification — 2026-10-29

## Scope

Verified the rebuilt foundation described in
`docs/superpowers/specs/2026-10-29-clean-foundation-design.md` and
`docs/superpowers/plans/2026-10-29-clean-foundation.md`.

## Commands and results

| Check | Command | Result |
| --- | --- | --- |
| Lint | `uv run ruff check .` | All checks passed |
| Tests | `uv run pytest -q` | 55 passed, 3 skipped |
| Migration | `uv run alembic upgrade head` / `alembic current` | `0001 (head)` |
| Tables | `SHOW TABLES FROM algo_orb` | `alembic_version`, `order_audit`, `sessions`, `upstox_tokens`, `users` |
| Admin seed | `uv run python -m app.cli.sync_admin` | `admin user ready` |
| Health | `GET /health` | `200 {"status":"ok"}` |
| Login page | `GET /login` | `200` |
| Login | `POST /login` with CSRF | `303 /`, HttpOnly session cookie set |
| Dashboard | `GET /` with session | `200`, signed-in username rendered |
| Logout | `POST /logout` | `303 /login`; dashboard redirects again |
| Health without DB | Unit test | `200`; DB failure on dashboard returns `503` |
| History purge | `git log --all --diff-filter=A` | no `.env`, `.env.production`, `.setting/`, old MCP configs |
| Tracked secrets | value scan against preserved env files | zero hits |
| Compose | `docker compose config` | valid |
| Image | `docker build -t algo-orb:foundation .` | built |

## Skipped or manual

- **Upstox OAuth round-trip:** not completed. It needs a browser and the rotated
  Upstox credentials, and the user must register the redirect URL. Unit and route
  tests use a fake SDK; no live call was made.
- **MySQL integration tests:** skipped. `algo_orb_test` does not exist and the
  application user cannot create databases.
- **Live trading:** intentionally unavailable. `TRADING_ENABLED=false`, no route
  or CLI calls `OrderGateway`, and the gateway's live path is tested only with
  fakes.

## Outstanding operator actions

1. **Rotate every credential** that was committed: Upstox sandbox/live keys,
   MySQL password, `APP_PASSWORD`, `APP_SESSION_SECRET`, `UPSTOX_TOTP_SECRET`,
   and `UPSTOX_TOTP_PIN`. History was rewritten and force-pushed, but rotation is
   the actual protection.
2. After rotating `APP_PASSWORD`, run `uv run python -m app.cli.sync_admin` to
   update the hash and revoke old sessions.
3. Complete the Upstox OAuth flow in a browser and confirm the dashboard shows
   **Connected**.
4. Delete `backups/` and the private copies in `~/.config/algo-orb/` once the
   rotated values are stored safely. Both contain the old secrets.
5. Review `deploy/README.md` before any production deployment.
