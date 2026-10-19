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
docker compose up -d web
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

## Optional strategy worker

Starting `web` does not start trading. For the 21 October paper session, confirm
`ORB_MODE=paper` and `ORB_LIVE_ENABLED=false` in `.env.production`, reconnect
Upstox for a valid token, then explicitly start the worker before 09:15 IST:

```bash
docker compose --profile trading up -d worker
docker compose logs --tail 100 worker
```

The worker deliberately has no automatic restart policy: a one-day paper run
must not silently resume on another exchange day. Monitor its heartbeat and
`/strategy/history`; keep it running through the 15:25 exit and final
reconciliation, then stop it:

```bash
docker compose --profile trading stop worker
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
docker compose up -d --no-deps web
```

Existing worker containers continue using their original image and environment;
coordinate worker updates outside active trading after reconciling exposure.
Changes to `.env.production` require container recreation, not just restart.
Avoid `docker compose config` without `--quiet` or `--no-env-resolution` in logs:
resolved output contains credentials. Application source is copied into the
image; no source or secret file is bind-mounted at runtime.
