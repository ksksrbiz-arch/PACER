@echo off
setlocal
cd /d "C:\Users\keith\Documents\Claude\Projects\PACER"

echo === status ===
git status --short

echo === add ===
git add -A

echo === commit ===
git -c user.email=240277128+ksksrbiz-arch@users.noreply.github.com -c user.name=ksksrbiz-arch commit -m "feat(pacer): TM screener wiring + auction/LTO yield tiers + payout ledger" -m "scoring/engine: call USPTOTrademarkScreener after spam filter, before Ahrefs/LLM. Hard-discard on conflict (saves API credits + UDRP exposure). Stores tm_conflict/tm_reason on candidate." -m "monetization/router: new auction_bin tier (yield>=85) + lease_to_own tier (yield>=70 AND commercial>=50). Composite yield_score = 0.40*authority + 0.60*commercial (configurable via epmv_*_weight). LTO price = est_bin/36 with $9.99 floor. Backward-compatible choose_strategy() — single-arg form keeps old behavior; yield_s explicit activates premium tiers." -m "partners/ledger: PayoutEntry SQLAlchemy model + PayoutLedger wrapper. record_batch / mark_paid / void lifecycle. CTA 24.9%% cap enforced at DB level. FK to domain_candidates (SET NULL) for 1099-NEC auditability." -m "migrations: 0004_payout_ledger (partners + domain_candidates deps)." -m "tests: router yield tiers, payout ledger (sqlite+aiosqlite), engine × TM integration with Ahrefs/LLM call suppression."

echo === pull --rebase ===
git -c core.editor=true pull --rebase origin main

echo === push ===
git push origin main

echo === final status ===
git status --short
git log --oneline -5

timeout /t 8 /nobreak > nul && del "%~f0"
