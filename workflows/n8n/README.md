# PACER n8n workflows

Self-hosted n8n (Contabo VPS) drives long-lived schedules that don't belong
in APScheduler-inside-the-app. Each JSON file here is importable via
**n8n → Workflows → Import from File**.

## Workflows

### `monthly_partner_payouts.json`
- **Cron:** 1st of every month, 09:00 PT (cron `0 9 1 * *` — adjust for n8n TZ)
- **Pipeline:**
  1. Compute `YYYY-MM` for the *previous* month (we pay out for the month that closed)
  2. Shell → `pacer partners payout run --period <prev>` on the PACER VM
  3. Parse stdout for entry count + partner total + persisted flag
  4. Read generated CSVs (`ledger_<period>.csv`, `1099nec_<year>.csv`)
  5. Post Slack summary to `#pacer-ops`
  6. Email both CSVs to `skdev@` + `partners@`
- **Failure branch:** Slack alert to `#pacer-ops` with stderr
- **Requires credentials:**
  - `SLACK_WEBHOOK_URL_PACER_OPS` env var on the n8n instance
  - SMTP credentials configured in n8n for `emailSend`
  - SSH/shell access for n8n to run `/opt/pacer/.venv/bin/pacer` (same user as the PACER service)

### Filing 1099-NECs
The email is an **internal trigger** — it delivers the roster to finance,
but the actual IRS submission still runs through **Track1099** (or IRS FIRE)
by hand. This is intentional: crossing the $600 threshold is automatic,
but the signed filing is a human checkpoint.

## Conventions
- Every workflow is tagged `pacer` + the business domain (`partners`, `monetization`, etc.)
- Workflows never hold secrets in-JSON — use n8n credentials or `$env.*`
- Failure branches always post to `#pacer-ops` so nothing fails silently
