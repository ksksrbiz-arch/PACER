# PACER · provision a fresh VPS in 5 minutes

Everything below assumes you've already run the local prep (SSH key generated, secrets staged). If `deploy/.secrets-staging/secrets.env` doesn't exist yet on this machine, run `python3 deploy/gen_secrets.py` first (or have the agent do it).

## 1 · Pick a provider

Any will do. Cheapest sane spec:

| Provider     | Plan                | Spec                  | Price   |
| ------------ | ------------------- | --------------------- | ------- |
| Hostinger    | KVM 2               | 2 vCPU / 8GB / 100GB  | ~$8/mo  |
| Contabo      | VPS S               | 4 vCPU / 8GB / 200GB  | ~$5/mo  |
| Hetzner      | CPX21               | 3 vCPU / 4GB / 80GB   | ~$8/mo  |
| DigitalOcean | Basic 2GB           | 1 vCPU / 2GB / 50GB   | ~$12/mo |

Required: Ubuntu 22.04 or 24.04. KVM virtualization (avoid OpenVZ — Caddy needs unrestricted UFW + Docker).

## 2 · Paste this public key into the provider's "SSH keys" panel BEFORE provisioning

```
ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIJMMBJxu++jVRzoBCqAz6gvkeES/rA4kooC0+VBxst94 pacer-vps@1commerce
```

Fingerprint (verify): `SHA256:qrByE8e9iAcuo1/zbYLyhtainu5qh8F60cYkLKCCa7s`

The matching private key lives at `C:\Users\keith\.ssh\pacer_ed25519` (Windows) / `/mnt/c/Users/keith/.ssh/pacer_ed25519` (WSL).

## 3 · Point DNS

Cloudflare → DNS → add an `A` record:

| Type | Name  | Content    | Proxy    | TTL  |
| ---- | ----- | ---------- | -------- | ---- |
| A    | pacer | <VPS IPv4> | DNS only | Auto |

Wait until `Resolve-DnsName pacer.1commercesolutions.com` (PowerShell) or `dig +short` returns the VPS IP. Caddy needs DNS live to issue the cert.

## 4 · Run the one-shot bootstrap

From WSL:

```bash
cd ~/repos/pacer
bash deploy/remote_bootstrap.sh <VPS-IP>
```

That's it. The driver:

1. SSH preflights to confirm key auth works
2. Builds `/opt/pacer/.env` locally from your dev `.env` plus the new VPS-only secrets
3. scp's `install.sh` to the VPS and runs it (Docker + UFW + 2GB swap)
4. Clones `https://github.com/ksksrbiz-arch/PACER.git@main` into `/opt/pacer`
5. scp's the production `.env`
6. `docker compose up -d --build` with the Caddy overlay
7. `alembic upgrade head`
8. Smoke-tests `https://pacer.1commercesolutions.com/`

Total wall time: ~6-8 minutes (the first `docker compose build` for `pacer` is the slowest step).

## 5 · After it's up

```
URL:   https://pacer.1commercesolutions.com/
User:  keith
Pass:  see deploy/.secrets-staging/secrets.env -> PACER_BASIC_PASS
SSH:   ssh -i ~/.ssh/pacer_ed25519 root@<VPS-IP>
```

Tail logs:

```bash
ssh -i ~/.ssh/pacer_ed25519 root@<VPS-IP> 'cd /opt/pacer && docker compose logs -f --tail 100 pacer pacer-web caddy'
```

Trigger a manual run from the dashboard, or directly via the API if you've also configured `pacer-web`'s tier1 endpoints.

## Common breakage

| Symptom                                    | Likely cause                                       | Fix                                                     |
| ------------------------------------------ | -------------------------------------------------- | ------------------------------------------------------- |
| `Permission denied (publickey)`            | Public key wasn't installed at provision time      | Most providers have "Re-deploy SSH key" — push it again |
| `dig +short pacer.<domain>` returns empty | DNS not propagated yet                             | Wait 60s; verify A record TTL set to "Auto" or 120      |
| `caddy` logs `obtain certificate` looping | DNS pointing wrong / port 80 blocked at provider FW | Check provider firewall (separate from UFW)             |
| Bootstrap fails at step 6 with build OOM   | <2GB RAM with no swap                              | install.sh added 2GB swap; reboot the VPS and rerun     |

## Re-running

`remote_bootstrap.sh` is idempotent. Re-running it will:
- Skip Docker / UFW / swap setup if already in place
- `git pull` instead of clone
- Recreate `.env` (overwrites — make sure no manual edits happened on the VPS)
- `up -d --build` (rebuilds changed images)
- Re-run migrations (no-op if at head)

So if a step fails, just rerun.
