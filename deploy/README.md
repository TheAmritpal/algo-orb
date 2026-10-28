# Production deployment on Linux

Host Nginx terminates HTTPS and proxies to `127.0.0.1:3000`. Compose runs
the application only; MySQL and Nginx remain installed on the host. Requires
Docker Engine and Docker Compose 2.30+ (raw environment file support).

## Server configuration

1. Copy `deploy/production.env.example` to `.env.production` in the project root,
   set permissions with `chmod 600 .env.production`, and fill in real secrets,
   the domain, and the pinned broker account. This file is excluded from Git and
   the image build. Values are literal: do not wrap them in quotes or use variable
   expansion. Keep the existing local `.env` out of production credentials setup.
2. Register the exact HTTPS callback URL in the Upstox developer app. Keep
   `APP_COOKIE_SECURE=true`. Paper execution is the production template default;
   it does not automatically change to live on 22 October or any other date.
3. Ensure `172.30.21.0/24` does not overlap your server, VPN or Docker networks.
   If changing it in `compose.yaml`, also change Uvicorn's trusted gateway IP
   in the Dockerfile and rebuild. Only one Uvicorn worker is used because the
   login limiter is in-process. Do not scale web replicas without a shared limiter.

### Connect to host MySQL

Compose maps `host.docker.internal` to Docker's host gateway. On Linux this is
normally the default bridge address, not the Compose network gateway. Inspect:

```bash
docker network inspect bridge --format '{{(index .IPAM.Config 0).Gateway}}'
```

MySQL listening only on `127.0.0.1` cannot accept this container connection.
Configure its listener to include the host gateway interface (or an appropriate
private interface/listener configuration for your MySQL version). If binding to
all interfaces, firewall TCP 3306 so only approved local/container sources can
reach it; do not expose it to the internet. Allow the Compose subnet
`172.30.21.0/24` through the host INPUT firewall to that listener, and grant the
application MySQL user access from that subnet rather than only `localhost`.
Confirm the actual source address on your host, especially with customized
Docker NAT rules. Keep port 3306 unpublished in Docker.

The database must already exist. Use a schema-capable migration account for
Alembic and a runtime account with application CRUD permissions afterward.
Token tables, sessions and backups contain sensitive data.

## Build, migrate and start

From the project root:

```bash
docker compose build web
# One-off commands do not start the HTTP server or strategy worker.
docker compose run --rm --no-deps web alembic upgrade head
docker compose run --rm --no-deps web python -m app.cli.sync_admin
docker compose up -d
curl --fail http://127.0.0.1:3000/health
docker compose ps
```

For separate migration credentials, use a private temporary copy of the
production environment file with the migration user, then run
`ORB_ENV_FILE=/path/to/migration.env docker compose run --rm --no-deps web alembic upgrade head`.
The normal commands continue using `.env.production` for runtime credentials.
Never place credentials in shell arguments or logs.

`sync_admin` revokes the administrator's existing sessions. Run it for initial
setup or intentional credential rotation, not every deployment. Migrations are
explicit; startup never changes the schema. Back up MySQL before upgrades and
review migration rollback implications before reverting an image.

The health check proves HTTP liveness only. It does not prove MySQL availability,
broker authentication, a running strategy worker or safe trading. Docker restart
policies restart exited web processes, not merely unhealthy containers.

## Host Nginx and HTTPS

Obtain a certificate for your real domain using your existing certificate
workflow. The sample expects certificates to exist before `nginx -t` can pass.
Replace `trading.example.com` and the certificate paths in
`deploy/nginx/algo-orb.conf`, then install it in your host's Nginx site directory:

```bash
sudo cp deploy/nginx/algo-orb.conf /etc/nginx/sites-available/algo-orb
sudo ln -s /etc/nginx/sites-available/algo-orb /etc/nginx/sites-enabled/algo-orb
sudo nginx -t
sudo systemctl reload nginx
```

Use your distribution's `conf.d` directory instead if it does not use
sites-available/sites-enabled. Configure DNS and allow host ports 80/443. Port
3000 is bound only to host loopback and maps to container port 8000; Nginx
forwards to host port 3000. Do not expose 3000 publicly. Nginx overwrites
forwarded headers, and Uvicorn trusts only the
configured Docker gateway. Verify HTTPS redirects, secure login cookies and
broker callback behaviour through the actual production domain.

Access logs are disabled in both Nginx and Uvicorn to avoid storing OAuth codes.
Nginx critical error logs may still contain request URIs; restrict log access
and ensure any outer proxy/monitor also redacts callback query strings.

## Strategy worker

An unqualified `docker compose up -d` starts web, token-scheduler and worker.
The worker uses the database Trading settings for paper or live mode and requires
a connected account and valid token. Confirm the intended mode before startup;
live mode still requires explicit enablement and configured trading limits.
Starting only `web` does not start the worker. To start or inspect it separately:

```bash
docker compose up -d worker
docker compose logs --tail 100 worker
```

The worker uses `restart: unless-stopped` in both paper and live mode. Docker
restarts it after process exits or a daemon/server restart unless it was manually
stopped. An explicit `docker compose up -d` starts stopped services again.
Monitor its heartbeat and `/strategy/history`; container uptime alone does not
confirm successful strategy initialization. To stop it intentionally:

```bash
docker compose stop worker
```

There is no date-based stop or live switch. Do not leave the paper worker
running overnight if only one session is intended. Stopping a live worker does
not close real NRML exposure. Paper success does not verify live GTT protection.
Review the operational readiness documents before explicitly configuring live
execution. Never start a second worker during unresolved exposure.

## Live trading activation

This version requires Alembic **0009**. Stop the worker before migration and
keep it stopped until the new image and schema are both installed. GTT entry
timeouts now allow two additional attempts, 30 seconds per attempt, on the same
contract and original signal. Each retry requires confirmed zero-fill
cancellation and zero exposure, a fresh quote and repeated risk checks.
No new entry follows partial fills, rejection or an uncertain broker response;
no retries start at/after 15:00 IST. Attempt budgets and audit history persist
across restarts. The protection-verification and live-mode gates still apply.
After retries have been recorded, schema downgrade is refused rather than
discarding the attempt history; plan rollbacks with this constraint in mind.

The strategy uses two contract lots, a stop five premium points below entry,
and a target ten points above entry. With a 500-unit lot, this is 1000 units:
5000 planned loss and 10000 target profit, before charges and execution losses.
Lot sizes come from broker metadata; confirm them for the selected contract.

Use this sequence on the production server, outside an active position:

1. Back up the server's private `.env.production` outside the checkout before
   updating files. Preserve its credentials, account pin and database settings.
   Do not replace it with local development credentials or the example file.
2. Stop the worker with `docker compose stop worker`. Check Upstox positions,
   pending orders and GTTs, plus application reconciliation state. Resolve any
   exposure before proceeding; stopping a worker does not close positions.
3. Verify the selected stock-option contract is eligible for fresh orders under
   Upstox's expiry-period rules, including product D and any delivery margin.
   The selector excludes expiries on or before the next NSE trading session,
   using Upstox's actual expiries and exchange calendar. On 26 and 27 October
   2026 it rolls past the restricted 27 October contract to the next eligible
   month. Existing exposure is managed on its original contract. Review future
   broker notices for changes to the restriction window and other trading bans.
4. Confirm the server's outbound static IP is registered in Upstox. Read-only
   history access does not validate mutation access. This deployment uses a
   regular API app and submits GTT orders without `X-Algo-Name`. Upstox's
   [GTT placement documentation](https://upstox.com/developer/api-documentation/place-gtt-order/)
   says that header is only required for an exchange-approved algo strategy;
   it is not a launch requirement for this app.
5. Fund the account for premium times quantity, charges and applicable broker
   margin. Retain a loss reserve. The planned-loss cap is not the entry funding
   requirement. Verify the account balance rather than relying on a past quote.
6. Confirm these values in the server's `.env.production`:

   ```dotenv
   ORB_EXECUTION_BACKEND=gtt
   ORB_MAX_PLANNED_LOSS=5000
   ORB_MAX_SPREAD=1
   ORB_MAX_SLIPPAGE=1
   ORB_QUOTE_MAX_AGE_SECONDS=5
   ORB_POLL_SECONDS=2
   ```

   `ORB_BROKER_USER_ID` must pin the intended live account. Set premium and
   notional caps explicitly; `none` intentionally disables those two caps.
   Existing private files retain `none`. Keep
   `ORB_PROTECTIVE_STOP_VERIFIED=false` until account-specific live GTT entry,
   stop/target protection, sibling cancellation and recovery have been verified.
   Only after that verification set it to `true` on the server. Successful
   paper runs, profile checks and GTT read access do not establish protection.
7. Build and apply migrations, then recreate only web and token-scheduler:

   ```bash
   docker compose build web
   docker compose run --rm --no-deps web alembic upgrade head
   docker compose up -d --no-deps --force-recreate web token-scheduler
   curl --fail http://127.0.0.1:3000/health
   ```

   Do not run an unqualified `docker compose up -d` at this stage; that also
   starts the worker. No admin synchronization is needed for a routine update.
8. Log in to the dashboard. Under Trading settings select **Live**, check
   **Enable live trading with real money**, and save. These settings are in MySQL;
   editing an environment file does not change them. Verify the saved values.
9. Before the session verify token renewal (scheduled 08:30 IST), account,
   funds and broker access. Start exactly one worker only when all checks above
   pass:

   ```bash
   docker compose up -d --no-deps --force-recreate worker
   docker compose logs --tail 100 worker
   ```

10. Supervise fresh quotes and the 09:15 opening-range data, then the single
    product `D` GTT. Monitor through the 15:14 cleanup; confirm the GTT is
    cancelled, the position is flat, and no working child order remains. Do not
    retry an uncertain order manually before checking its broker status.

For an emergency, stop the worker to halt further strategy submissions, then
manage and reconcile positions/orders/GTTs directly in Upstox. Stopping the
worker or disabling live settings does not close an existing position. To
return to paper, stop the worker, reconcile live exposure, save Paper with live
enablement unchecked in the dashboard, and recreate the worker.

Do not deploy confidential `.env` changes through a public repository. These
files are currently tracked in this checkout despite the older setup text;
keep credentials out of commits and arrange repository-history remediation
and credential rotation separately if they have already been shared.

## Routine updates

```bash
docker compose build web
docker compose run --rm --no-deps web alembic upgrade head
docker compose up -d --no-deps
```

This also recreates the worker when its image or configuration changes;
coordinate updates outside active trading after reconciling exposure.
Changes to `.env.production` require container recreation, not just restart.
Avoid `docker compose config` without `--quiet` or `--no-env-resolution` in logs:
resolved output contains credentials. Application source is copied into the
image; no source or secret file is bind-mounted at runtime.


## Optional daily PIN/TOTP token retrieval

Fill `UPSTOX_TOTP_SECRET` (Base32 enrolment key), `UPSTOX_TOTP_PIN`, the live API
key and `ORB_BROKER_USER_ID` in the private `.env.production`. Never paste these
values into cron entries, shell arguments, or logs. Rebuild the image after
adding this command, then run an intentional initial connection:

```bash
docker compose run --rm --no-deps web python -m app.cli.upstox_token --connect
```

Compose includes a `token-scheduler` service. It verifies saved connections at
**08:30 IST every day**, including weekends, regardless of the host timezone.
Expired tokens are replaced through PIN/TOTP. If started late, it catches up
until 15:30 IST; after that it waits for the next day. Exchange holidays are not
excluded.

Build and deploy the updated code once:

```bash
docker compose up -d --build
docker compose logs --tail 50 token-scheduler worker
```

Compose reads `.env.production` by default. Put the TOTP values in that file,
or select a different private file with `ORB_ENV_FILE`. Editing the local `.env`
alone does not update production containers. Credential environment changes
require container recreation; ordinary daily token renewal does not.

No host cron entry is needed. Disable old renewal cron jobs to avoid duplicate
scheduling. Renewal and worker iterations share an account-wide MySQL advisory
lock. The scheduler waits briefly for a running iteration, then retries failures
with backoff from 30 seconds to five minutes during the renewal window. Before
issuing a token on any retry it verifies the saved token and requires a confirmed
broker `401`. Network failures alone do not authorize rotation. Successful
verification or renewal completes that date's scheduled work.

The worker keeps its duplicate-worker lock continuously, but releases the
credential lock between iterations. It adopts verified replacement credentials
in the existing SDK client before its next iteration, retaining simulated orders
and GTT state. On startup it waits for credentials to become available. No daily
worker or container restart is needed. Renewal never places orders or changes
trading mode or limits.

The scheduler never uses `--connect`: intentional first connection or reconnection
still uses the command above. A UI disconnect therefore remains disconnected.
Disconnection requires workers to be stopped and also excludes concurrent token
renewal. Invalid PIN/TOTP settings, broker outages or database outages can still
prevent renewal; failures are logged without exposing credentials.

To disable only automatic token renewal:

```bash
docker compose stop token-scheduler
```

## Place-once GTT and 15:14 cleanup

The worker places one product `D` GTT at the ORB breakout, records the GTT ID,
and does not poll order status. At **15:14 IST** it cancels the GTT and working
children, reads the product `D` position, and either closes the run (flat) or
submits one bounded sell in the same product and closes. Upstox does not allow
intraday (MIS) BUY orders for options, so delivery is required; there is no MIS
auto-square-off backstop. The worker must be running at 15:14. If the worker is
down, cancel the GTT manually in Upstox and manage the delivery position because
its exit legs remain valid for 365 days. Genuine cancellation or exit uncertainty
becomes `reconciliation_required` and never submits a second sell.

## One-time repair of run 160103

Run 160103 blocks new entries until it is closed. With the worker stopped:

```bash
docker compose stop worker
PYTHONPATH=. uv run python -m app.cli.repair_manual_exit
PYTHONPATH=. uv run python -m app.cli.repair_manual_exit --apply
```

The command is dry-run by default, refuses a non-flat position or a running
worker, and makes no broker mutations. Verify the run is `closed` with 1000
filled, 0 remaining and entry average 45.775. Then deploy the new code and
restart the worker after 15:30 IST:

```bash
docker compose build web
docker compose run --rm --no-deps web alembic upgrade head
docker compose up -d --no-deps --force-recreate web token-scheduler worker
```

Do not run an unqualified `docker compose up -d` while reconciling exposure.
