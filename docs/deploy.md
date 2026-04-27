# POI Lake — VPS Deployment Guide

Single-host deployment plan for production. The whole stack runs on one
VPS via docker-compose, with Caddy fronting it for TLS. Suitable up to
roughly 50–100 req/s and 5M master POIs; scale-out is a Phase 8 problem.

## 1. Provision the VPS

Recommended sizing for the full pipeline (api + worker with embedding
model + postgres + redis + admin + scraper + caddy + monitoring):

| Tier | vCPU | RAM | Disk | Use |
|---|---|---|---|---|
| **min** | 4 | 8 GB | 80 GB SSD | testing / staging |
| **prod** | 8 | 16 GB | 200 GB SSD | recommended |
| **scale** | 16 | 32 GB | 500 GB NVMe | aggressive ingestion |

Suggested providers (closest to VN traffic): Hetzner CCX22 (Singapore),
DigitalOcean SGP1, Vultr Singapore, AWS Lightsail / EC2 ap-southeast-1.

OS: **Ubuntu 22.04 LTS** or **Debian 12**. The bootstrap script is
tested on both.

## 2. DNS

Point two A records at the VPS public IP **before** running `deploy.sh`
— Caddy will try to acquire TLS certs on first start and fail if DNS
hasn't propagated.

```
api.poi.oohx.net      A   <vps-public-ip>
admin.poi.oohx.net    A   <vps-public-ip>
```

Hostnames are arbitrary — set whatever you used to `CADDY_API_HOST` /
`CADDY_ADMIN_HOST` in `.env`.

## 3. First-time bring-up

```bash
ssh root@<vps>

# 1. Bootstrap (installs docker, ufw, swap, backup cron, dedicated user).
curl -fsSL https://raw.githubusercontent.com/tuanna0703/oohx-poi/main/scripts/bootstrap_vps.sh \
  | sudo bash

# 2. Switch to the service user and configure secrets.
sudo -iu poi
cd /opt/poi-lake
cp .env.production.example .env
nano .env

# Generate values to paste:
python3 -c "import secrets; print(secrets.token_urlsafe(48))"          # APP_SECRET_KEY
python3 -c "import secrets; print(secrets.token_urlsafe(24))"          # POSTGRES_PASSWORD
python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"   # FERNET_KEY
docker run --rm caddy:2 caddy hash-password                            # ADMIN_BASIC_AUTH_HASH

# 3. First deploy — runs migrations + seeds.
./scripts/deploy.sh --first-run

# 4. Create the API client for oohx (record the printed key — shown once).
docker compose -f docker-compose.yml -f docker-compose.prod.yml \
  run --rm api python scripts/create_api_client.py oohx \
    --permissions read:master --rate-limit 1000
```

After step 4, hit the public URL:

```bash
curl -i https://api.poi.oohx.net/health
curl -i https://api.poi.oohx.net/api/v1/master-pois?per_page=5 \
  -H "X-API-Key: <printed-key>"
```

The admin UI is at `https://admin.poi.oohx.net` (Basic-auth user
`admin`, password set during bootstrap).

## 4. Subsequent deploys

```bash
sudo -iu poi
cd /opt/poi-lake
./scripts/deploy.sh
```

That pulls main, rebuilds images, runs `alembic upgrade head`, and
restarts containers with rolling restart (named volumes preserve data).

Roll back by checking out a previous tag and re-running:

```bash
git fetch --tags
git checkout v0.X.Y
./scripts/deploy.sh --no-pull   # skip the implicit `git pull`
```

## 5. Backup & restore

The bootstrap installs a daily 03:00 UTC cron entry (see `crontab -u
poi -l`). Logs go to `/var/log/poi-backup.log`. Local dumps in
`./backups/`, optionally uploaded to S3 if `BACKUP_S3_BUCKET` is set.

```bash
# manual dump
./scripts/backup_postgres.sh

# restore (DESTRUCTIVE — wipes target DB)
./scripts/restore_postgres.sh ./backups/poi_lake_20260101T030000Z.dump
```

To validate backups quarterly: spin up a throwaway compose project,
restore yesterday's dump, hit `/health/ready`, tear down.

## 6. Operational quick reference

| What | How |
|---|---|
| Watch logs | `docker compose logs -f api worker caddy` |
| Force dedupe pass | `curl -X POST -H "X-Admin-Token: $APP_SECRET_KEY" https://api.poi.oohx.net/api/v1/admin/dedupe/run` |
| List clients | `docker compose exec api python -c "from poi_lake.db.models import APIClient; ..."` |
| Disable a client | `UPDATE api_clients SET enabled=false WHERE name='X'` (then `docker compose restart api`) |
| Check rate-limit counters | `docker compose exec redis redis-cli --scan --pattern 'rl:*'` |
| Renew TLS | Caddy does it automatically; confirm via `docker compose logs caddy` |

## 7. Failure-mode runbook

| Symptom | First check | Fix |
|---|---|---|
| `502 Bad Gateway` from Caddy | `docker compose ps api` | If unhealthy, `docker compose logs api` |
| TLS cert errors | DNS resolves to this server? | wait 24h after DNS change, then `docker compose restart caddy` |
| Worker stuck | `docker compose logs --tail 200 worker` | `docker compose restart worker` (jobs in `running` get reaped if you set `error_message` and `status='failed'` manually) |
| Admin UI 502 | `docker compose ps admin` | restart admin; Streamlit holds an in-memory cache that occasionally desyncs after schema migrations |
| 429 from API | rate-limit hit (X-RateLimit-* headers) | `UPDATE api_clients SET rate_limit_per_minute = N WHERE name='oohx';` then `docker compose restart api` |
| Backup cron silently failing | `tail /var/log/poi-backup.log` | check S3 creds + `aws sts get-caller-identity` |

## 8. Security checklist

- [x] UFW: only 22, 80, 443 open
- [x] Postgres + Redis bound to docker network only (not host)
- [x] `/api/v1/admin/*` and `/metrics` blocked at Caddy on the public hostname
- [x] Admin UI behind Caddy basic-auth (defense-in-depth) + `X-Admin-Token`
- [x] API keys stored as SHA-256 hashes only
- [x] Daily security patches via `unattended-upgrades`
- [x] Weekly: rotate `APP_SECRET_KEY` if any operator leaves
- [ ] Quarterly: validate restore from latest S3 backup
- [ ] Yearly: rotate Anthropic / Google Places keys

## 9. Capacity planning

Approximate row growth with steady ingestion of HN + HCM + ĐN at 5km
cells, all OpenOOH categories on a weekly cadence:

| Table | Daily growth | After 1 year |
|---|---|---|
| `raw_pois` | ~80k | ~30M (~25 GB) |
| `processed_pois` | ~40k (after dedupe pre-filter) | ~15M |
| `master_pois` | ~5k (after merge) | ~1.8M |
| `master_poi_history` | ~15k | ~5M |

Archive `raw_pois` older than 1 year to keep working-set hot
(see runbook §6).
