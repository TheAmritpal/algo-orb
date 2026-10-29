# Production deployment

Host Nginx terminates HTTPS and proxies to `127.0.0.1:3000`. Compose runs the
application only; MySQL and Nginx stay on the host. Requires Docker Engine and
Docker Compose 2.30+ (raw env-file support).

This foundation has **no strategy worker**. Only the `web` service is defined.

## 1. Database

Create the database and an application user with a newly rotated password.
Grant schema privileges for the first migration, then revoke them:

```sql
CREATE DATABASE algo_orb CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER 'algo_orb'@'%' IDENTIFIED BY '<rotated-password>';
GRANT SELECT, INSERT, UPDATE, DELETE, CREATE, ALTER, INDEX, DROP, REFERENCES
  ON algo_orb.* TO 'algo_orb'@'%';
```

Run the migration (step 3), then reduce the runtime account to DML only:

```sql
REVOKE CREATE, ALTER, INDEX, DROP, REFERENCES ON algo_orb.* FROM 'algo_orb'@'%';
```

A future migration must temporarily re-grant those privileges, or use a separate
migration account.

## 2. Environment

```bash
cp deploy/production.env.example .env.production
chmod 600 .env.production
```

Fill in every placeholder. Keep `APP_COOKIE_SECURE=true` and
`APP_ENV=production`. Register the exact HTTPS callback URL
(`https://<domain>/auth/upstox/callback`) in the Upstox developer console.
Never commit `.env.production`; it is gitignored.

Compose maps `host.docker.internal` to the Docker host gateway. Ensure MySQL
accepts connections from the Compose subnet `172.30.21.0/24`; keep port 3306
closed to the internet and unpublished in Docker.

## 3. Build, migrate, seed, start

```bash
docker compose build web
docker compose run --rm --no-deps web alembic upgrade head
docker compose run --rm --no-deps web python -m app.cli.sync_admin
docker compose up -d web
docker compose ps
curl --fail http://127.0.0.1:3000/health
```

`sync_admin` is idempotent. Run it for initial setup or when rotating the admin
password; a password change revokes the administrator's sessions. Do not run it
on every deploy.

## 4. Nginx and HTTPS

Edit `deploy/nginx/algo-orb.conf`: replace `trading.example.com` and the
certificate paths, then install it:

```bash
sudo cp deploy/nginx/algo-orb.conf /etc/nginx/sites-available/algo-orb
sudo ln -s /etc/nginx/sites-available/algo-orb /etc/nginx/sites-enabled/algo-orb
sudo nginx -t
sudo systemctl reload nginx
```

Port 3000 is bound to host loopback only. Do not expose it publicly. Access
logs are disabled in Nginx and Uvicorn so OAuth codes do not reach logs.

## Safety notes

- `TRADING_MODE=paper` and `TRADING_ENABLED=false` in the example. Changing
  them does not create an order route; a strategy must add one deliberately.
- `/health` proves process liveness only, not database readiness, broker login,
  or strategy safety.
- Order audit rows and Upstox tokens contain sensitive data. Restrict database
  access and backups.
- Rotate every credential that was ever committed to this repository. History
  was rewritten, but rotation is the actual protection.
