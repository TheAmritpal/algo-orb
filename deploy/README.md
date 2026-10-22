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

## Updating

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
