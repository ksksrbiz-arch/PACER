# PACER Setup — Hostinger / OpenClaw VPS

## 1. Provision

```bash
# As root on fresh Ubuntu 22.04 VPS
apt update && apt upgrade -y
apt install -y docker.io docker-compose-plugin git ufw fail2ban
systemctl enable --now docker
ufw allow OpenSSH && ufw --force enable

adduser --disabled-password --gecos "" pacer
usermod -aG docker pacer
```

## 2. Clone + configure

```bash
su - pacer
git clone https://github.com/ksksrbiz-arch/PACER.git pacer
cd pacer
cp .env.example .env
# Fill in every key. Use `openssl rand -hex 32` for anything that needs a secret.
```

## 3. Database migrations

```bash
make deploy-prep    # builds containers, runs alembic upgrade head
```

## 4. First launch

```bash
make docker-up
make docker-logs    # verify scheduler picks up at configured cron (default 03:00 UTC)
```

## 5. Monitoring

- `docker compose logs -f pacer` — live tail
- Loguru writes to `./logs/pacer.log` (rotated daily, 30-day retention)
- Slack webhook (`SLACK_WEBHOOK_URL`) receives critical alerts + daily summary
- Optional: add Prometheus scrape target to port 9090 (metrics exposed by `prometheus_client`)

## 6. DFR exemption (Oregon Money Transmitter)

Before executing the first fractional RWA sale under 1COMMERCE LLC:

1. Engage an Oregon-admitted fintech attorney (Portland recommended).
2. Commission an opinion letter confirming the Securitize hybrid settlement flow keeps PACER inside the DFR exemption.
3. File the letter with DFR and retain a signed PDF in `compliance/dfr-opinion-letter.pdf` (gitignored).
4. Flip `RWA_FRACTIONAL_SALES_ENABLED=true` in `.env` and restart.

Template outreach and opinion-letter skeleton are in `docs/dfr-exemption-opinion.md`.

## 7. Backups

```bash
# Nightly pg_dump via cron on VPS host
0 4 * * * docker exec pacer_postgres_1 pg_dump -U pacer pacer | gzip > /backups/pacer-$(date +\%F).sql.gz
```

Retain 30 days rolling, encrypt with `age` before offsite rsync.

## 8. Updates

```bash
git pull
make docker-down && make deploy-prep && make docker-up
```
