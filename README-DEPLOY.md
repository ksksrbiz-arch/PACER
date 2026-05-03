# PACER · VPS deploy runbook

Single-VPS production deploy. Stack:

- `postgres` — primary store
- `redis` — circuit-breaker + rate-limit state
- `pacer` — APScheduler worker (daily + 6h fast cycles)
- `pacer-web` — FastAPI + Jinja2 ops dashboard, binds `127.0.0.1:8080`
- `pacer-backup` — postgres dump loop into `./backups/`
- `caddy` — reverse proxy with auto-TLS + HTTP basic auth (added by overlay)

The Caddy service lives in `deploy/docker-compose.caddy.yml` as an overlay so the base `docker-compose.yml` stays runnable as-is for local dev.

## 0 · Prereqs

- Ubuntu 22.04 / 24.04 VPS, 2 vCPU / 4 GB RAM minimum.
- Public IPv4.
- Domain you control. Cloudflare DNS is fine — leave grey-cloud (DNS only) so Caddy can do its own TLS.
- SSH key access set up. Don't run anything below over a password login.

## 1 · DNS

Cloudflare → DNS → add an `A` record:

| Type | Name  | Content    | Proxy    | TTL  |
| ---- | ----- | ---------- | -------- | ---- |
| A    | pacer | <VPS IPv4> | DNS only | Auto |

Wait until `dig +short pacer.1commercesolutions.com` returns the VPS IP before continuing — Caddy's ACME challenge needs DNS to be live.

## 2 · Bootstrap the box

```bash
ssh root@<VPS-IP>
curl -fsSL -o /tmp/install.sh \
  https://raw.githubusercontent.com/ksksrbiz-arch/PACER/main/deploy/install.sh
bash /tmp/install.sh pacer.1commercesolutions.com keith@1commercesolutions.com
```

Installs Docker, sets up UFW (22/80/443), adds 2GB swap, scaffolds `/opt/pacer/.env`.

## 3 · Pull the repo

```bash
cd /opt/pacer
git clone https://github.com/ksksrbiz-arch/PACER.git .
git checkout main
```

## 4 · `.env`

The bootstrap stub set `PACER_DOMAIN`, `LETSENCRYPT_EMAIL`, and a placeholder `PACER_BASIC_USER`. Paste your real PACER secrets on top, then generate the basic-auth hash:

```bash
docker run --rm caddy:2-alpine caddy hash-password --plaintext '<your-password>'
# Paste the $2a$14$.... output into PACER_BASIC_HASH= in /opt/pacer/.env
```

Make sure these are present (most carry over from your dev `.env`):

```bash
PACER_DOMAIN=pacer.1commercesolutions.com
LETSENCRYPT_EMAIL=keith@1commercesolutions.com
PACER_BASIC_USER=keith
PACER_BASIC_HASH=$2a$14$...

DATABASE_URL=postgresql+asyncpg://pacer:pacer@postgres:5432/pacer
SYNC_DATABASE_URL=postgresql://pacer:pacer@postgres:5432/pacer
REDIS_URL=redis://redis:6379/0

# Plus your existing ANTHROPIC_API_KEY, OPENAI_API_KEY, registrar keys,
# DOMA_*, SECURITIZE_*, SLACK_WEBHOOK_URL, LLC_*, etc.
```

`chmod 600 .env` if `install.sh` didn't already.

## 5 · Bring it up

```bash
cd /opt/pacer
docker compose -f docker-compose.yml -f deploy/docker-compose.caddy.yml up -d --build
docker compose exec pacer alembic upgrade head
docker compose ps
```

You should see:

```
postgres     running (healthy)
redis        running
pacer        running          # APScheduler — daily + 6h fast
pacer-web    running          # FastAPI ops dashboard on :8080 (private)
pacer-backup running          # nightly pg_dump loop
caddy        running          # 80/443 public
```

Caddy will obtain a Let's Encrypt cert on first request to the hostname. Watch logs:

```bash
docker compose logs -f caddy
```

Look for `certificate obtained successfully`.

## 6 · Smoke test

```bash
curl -fsS https://pacer.1commercesolutions.com/
# -> 401 (basic auth challenge, that's correct)

curl -fsS -u "$PACER_BASIC_USER:<password>" https://pacer.1commercesolutions.com/
# -> HTML home page from pacer-web
```

Open the URL in a browser. You'll get the basic-auth prompt; enter the credentials you set, then you're in `pacer-web`'s dashboard (home / candidates / runs / events / spend).

## 7 · Day-2 ops

**Logs**

```bash
docker compose logs -f --tail=200 pacer pacer-web caddy
```

`pacer` is the scheduler, `pacer-web` is uvicorn access logs, `caddy` is reverse-proxy + ACME activity.

**Migrations after pulling new code**

```bash
git pull
docker compose -f docker-compose.yml -f deploy/docker-compose.caddy.yml up -d --build
docker compose exec pacer alembic upgrade head
```

**Backups**

`pacer-backup` writes nightly `pg_dump` files to `./backups/`. To push them off-box on a cron, point an rclone or aws-cli sync at that directory.

**Rotate the basic-auth password**

```bash
NEW_HASH=$(docker run --rm caddy:2-alpine caddy hash-password --plaintext '<new-pw>')
sed -i "s|^PACER_BASIC_HASH=.*|PACER_BASIC_HASH=$NEW_HASH|" /opt/pacer/.env
docker compose restart caddy
```

The hash never appears in logs and bcrypt makes brute-force unattractive.

## 8 · Common breakage

| Symptom                                  | Likely cause                                       | Fix                                                                                  |
| ---------------------------------------- | -------------------------------------------------- | ------------------------------------------------------------------------------------ |
| Caddy can't get a cert                   | DNS not propagated, or 80/443 blocked              | `dig +short` your hostname; `ufw status`; check VPS provider firewall (not UFW)      |
| 502 on every request                     | `pacer-web` not healthy                            | `docker compose logs pacer-web`; check DATABASE_URL points at `postgres` (compose hostname) |
| Caddy starts then crashes                | Missing `PACER_BASIC_HASH` or wrong format         | Regenerate via `caddy hash-password --plaintext '<pw>'`; the full string starts with `$2a$14$` |
| Browser 401s with correct password       | `PACER_BASIC_USER` mismatch                        | Confirm the username you typed matches `.env`                                        |
| `alembic upgrade head` says "DB not exist" | Postgres not healthy yet                          | `docker compose ps` — wait for `postgres` to be `(healthy)`                          |

## 9 · Hardening checklist before going public

- [ ] Cloudflare in front (orange-cloud) once you're stable, with a Page Rule limiting access to your IPs.
- [ ] `fail2ban` for SSH if you haven't disabled password login (you should).
- [ ] Off-box backup target wired up (R2/S3 + rclone).
- [ ] Watchtower or weekly `apt-get update && unattended-upgrades` for the host OS.
- [ ] Sentry or similar for `pacer` + `pacer-web` containers.
