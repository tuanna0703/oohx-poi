# CI/CD

`.github/workflows/ci.yml`, two jobs.

| job | runs on | does |
|---|---|---|
| `build-test` | every push to `main`, every PR | builds the api, admin and postgres images; runs `ruff`, `mypy` (informational) and the full pytest suite **inside the api image** against the real PostGIS + pgvector image |
| `deploy` | push to `main` (or manual run) after `build-test` passes | SSHes to the VPS as `poi`; the key can only deploy a commit already on `origin/main` |

Code reaches GitHub through the subtree split (`scripts/push-to-oohx-poi.ps1`
/ `.sh`), so **pushing poi-lake to `main` is deploying it**. Docs-only pushes
(`*.md`, `docs/**`) skip the workflow.

## What a deploy does (`scripts/ci_deploy.sh`)

1. `git checkout --detach -f <sha>` in `/opt/poi-lake`.
2. Build `api` + `admin` while the old containers keep serving.
3. Wait (≤10 min) until no ingest job is `pending`/`running` — restarting the
   worker drops in-flight dramatiq messages and strands those rows.
4. `alembic upgrade head`, then `docker compose up -d --remove-orphans`.
5. If `docker/caddy/` changed: `caddy validate`, then `caddy reload`.
6. Poll `/health/ready`. On failure, check out the previous commit and run the
   same steps again, then fail the job. **Migrations are not rolled back** —
   keep them backward compatible.

The deploy key's forced command (`scripts/deploy_entry.sh`, installed root-owned
at `/usr/local/bin/poi-lake-deploy`) accepts only a 40-char sha that is an
ancestor of `origin/main`, then runs `ci_deploy.sh` **from that commit**.

## VPS-only configuration

The repo is public, so host-specific config stays untracked on the VPS:

| file | purpose |
|---|---|
| `.env` | secrets |
| `docker-compose.local.yml` | extra compose override, picked up automatically when present (e.g. attach `caddy` to another product's network) |
| `docker/caddy/sites/*.caddy` | extra Caddy sites, pulled in by `import` at the end of the Caddyfile |

Caddy mounts the whole `docker/caddy/` directory, not the single Caddyfile:
git replaces files on checkout, and a single-file bind mount would keep
serving the old inode.

## One-time setup

1. **VPS** (as root): move host-local Caddy sites into `docker/caddy/sites/`,
   write `docker-compose.local.yml`, check `/opt/poi-lake` out at the commit
   currently running, install the forced command:
   `install -o root -g root -m 755 scripts/deploy_entry.sh /usr/local/bin/poi-lake-deploy`.
2. **Deploy key**: `ssh-keygen -t ed25519 -N "" -C github-actions -f poi_deploy`;
   append to `/home/poi/.ssh/authorized_keys`:
   `restrict,command="/usr/local/bin/poi-lake-deploy" <contents of poi_deploy.pub>`.
3. **GitHub** — environment `production` with secrets:
   `DEPLOY_SSH_KEY` (private key), `DEPLOY_HOST`, `DEPLOY_KNOWN_HOSTS`
   (`ssh-keyscan -t ed25519 <host>`). Add required reviewers to the
   environment if every deploy should wait for a click.

## Deploy by hand

```bash
ssh poi@<host>                # a normal login key, not the CI one
cd /opt/poi-lake && git fetch origin main
bash scripts/ci_deploy.sh "$(git rev-parse origin/main)"
```
