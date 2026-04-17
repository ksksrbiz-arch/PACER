# Runbook — Flip `aftermarket_listings_enabled=True` in prod

**Owner:** Keith J. Skaggs Jr. (principal engineer, 1COMMERCE LLC)
**Reviewer:** Keith Skaggs Sr. (business partner) — sign off before the flip
**Last updated:** 2026-04-17
**Related:** `src/pacer/monetization/afternic.py`, `src/pacer/config.py`

## What this does

Flips the aftermarket listing pipeline from **dry-run** (log-only) to **live**
— every caught domain that hits `auction_bin` or `lease_to_own` tier will
actually POST to Afternic, Sedo MLS, and DAN.com. Before this flip, those
paths log "dry_run" and return stub `ListingResult` objects. After the flip,
we hit the real exchanges and real dollars start flowing.

## Preconditions — do not flip unless ALL are true

- [ ] `cryptography ^46.0.7` / `requests ^2.33.0` already deployed (commit
      `7290419`, ships the dependabot CVE clears).
- [ ] n8n monthly payout workflow imported and **one** successful manual
      test run has fired (Slack summary + 1099 CSV email landed).
- [ ] CTA/BOI partner cap enforced at ≤24.9% — grep for any
      `partner_default_rev_share_pct` overrides in env.
- [ ] `aftermarket_listings_enabled=False` default confirmed in
      `src/pacer/config.py` (fallback protection if .env fails to load).
- [ ] All three exchange accounts created under **1COMMERCE LLC** (not
      a personal account — 1099s route to the LLC EIN):
  - [ ] Afternic / GoDaddy API partner account active
  - [ ] Sedo MLS seller account verified
  - [ ] DAN.com seller account verified

## Secrets to seed in prod `.env`

Populate via your secrets manager (1Password → Ops vault → `PACER prod`):

```env
AFTERNIC_API_KEY=<from GoDaddy dev portal, key+secret concatenated as "key-xxxx:secret-xxxx">
AFTERNIC_PARTNER_ID=<seller ID, e.g. "1commerce-llc-seller">
SEDO_USERNAME=<sedo login>
SEDO_SIGNKEY=<sedo signkey, from account settings → API>
SEDO_PARTNERID=<sedo partner id>
DAN_API_KEY=<from dan.com → account → API tokens>

# Required for auto-301 alongside listings (Cathedral: Revenue before Scale)
CLOUDFLARE_API_TOKEN=<scoped token: Zone.Rulesets edit on redirect zones>
CLOUDFLARE_ZONE_ID=<default zone id for 301 rules>
```

**Do NOT** commit these to git. `.env` is gitignored — verify before flipping.

## Staging dry-run (mandatory baseline)

Run the full pipeline with listings ENABLED on staging, compare the
`ListingResult` set to the dry-run baseline. Any diff that isn't just
`status` changing from `dry_run` to `listed` needs root cause before prod.

```bash
# 1. On the staging VM, with live keys but staging-only DB:
export AFTERMARKET_LISTINGS_ENABLED=true
export $(cat /opt/pacer/.env.staging | xargs)

# 2. Seed 3 test candidates at each tier (auction_bin, lease_to_own, 301)
pacer pipeline run --limit 3 --source manual_test

# 3. Capture results
pacer monetization list-recent --since '1 hour ago' > /tmp/staging_listings.json

# 4. Diff against last known-good dry-run
diff <(jq -S . /opt/pacer/reports/dryrun_baseline.json) \
     <(jq -S . /tmp/staging_listings.json)
```

**Acceptance:** diff shows only `status: "listed"` (was `"dry_run"`),
`listing_id` populated (was None/stub), and `listing_url` is a real
Afternic/Sedo/DAN URL that opens in a browser. Nothing else differs.

## Prod flip

```bash
# 1. Verify you're on the prod host
hostname  # expect pacer-prod.1commerce.internal

# 2. Verify the flag is currently False
grep -E '^AFTERMARKET_LISTINGS_ENABLED' /opt/pacer/.env
# → AFTERMARKET_LISTINGS_ENABLED=false (or absent)

# 3. Flip it
sudo -u pacer sed -i \
  's/^AFTERMARKET_LISTINGS_ENABLED=.*/AFTERMARKET_LISTINGS_ENABLED=true/' \
  /opt/pacer/.env
grep -E '^AFTERMARKET_LISTINGS_ENABLED' /opt/pacer/.env
# → AFTERMARKET_LISTINGS_ENABLED=true

# 4. Bounce the app so pydantic-settings picks up the change
sudo systemctl restart pacer.service
sleep 5
systemctl status pacer.service --no-pager | head -15

# 5. Smoke test — route ONE candidate and verify it actually listed
pacer monetization route-one --domain canary-test-$(date +%s).com --tier auction_bin
# Look in logs for "afternic.post_auction_listing" with listing_id (NOT "dry_run")
journalctl -u pacer.service -n 50 --no-pager | grep -E 'afternic|sedo|dan|cloudflare'
```

## Rollback

If a listing posts to the wrong account, or API rate-limits spike, or any
exchange returns a billing error:

```bash
# Same sed, opposite direction
sudo -u pacer sed -i \
  's/^AFTERMARKET_LISTINGS_ENABLED=.*/AFTERMARKET_LISTINGS_ENABLED=false/' \
  /opt/pacer/.env
sudo systemctl restart pacer.service
```

Rollback is zero-risk because every listing call is idempotent and
gated — flipping back to false just stops new listings; it doesn't delete
ones already posted. To actually pull posted listings, use each exchange's
dashboard directly (no API path built for takedowns yet).

## Post-flip verification (within 24h)

- [ ] First real caught domain that hits auction_bin shows a live
      Afternic listing page (open in browser).
- [ ] Sedo MLS portfolio view shows the same domain.
- [ ] DAN.com seller dashboard shows the LTO entry for any
      lease_to_own-tier catch.
- [ ] Cloudflare dashboard shows the Single Redirect rule at the
      dynamic_redirect phase for any 301_redirect catch.
- [ ] `#pacer-ops` received no `:rotating_light:` failure alerts.
- [ ] Next morning: first real BIN sale or lease signing shows up in
      the `reports/monetization/<YYYY-MM>.csv` ledger.

## Owner decisions — only Keith Sr. signs off

The CTA/BOI 24.9% cap, the 1099 threshold, and the listing-enable flag
are the three levers where a coding mistake could cost real money or
trigger a compliance filing. Each one is a human checkpoint, not an
automation. Do not add auto-flip logic. The CLI stays opt-in.
