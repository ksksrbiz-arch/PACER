"""Partner payout computation.

Pure functions — no DB. Given a DomainCandidate's revenue and a Partner's
terms, compute the 1099-NEC payout amount in cents. Caller writes to the
ledger.
"""
from __future__ import annotations

from dataclasses import dataclass

from pacer.config import get_settings


@dataclass(frozen=True)
class PayoutLine:
    partner_id: int
    domain: str
    gross_revenue_cents: int
    partner_cents: int
    llc_cents: int
    rev_share_pct: float


def compute_payout(
    partner_id: int,
    domain: str,
    gross_revenue_cents: int,
    rev_share_pct: float | None = None,
) -> PayoutLine:
    """Compute a single payout line.

    Raises ValueError if rev_share_pct exceeds the CTA/BOI cap.
    """
    settings = get_settings()
    pct = rev_share_pct if rev_share_pct is not None else settings.partner_default_rev_share_pct

    if pct < 0 or pct > settings.partner_max_rev_share_pct:
        raise ValueError(
            f"rev_share_pct {pct} out of bounds [0, {settings.partner_max_rev_share_pct}]"
        )
    if gross_revenue_cents < 0:
        raise ValueError("gross_revenue_cents must be non-negative")

    partner_cents = int(round(gross_revenue_cents * (pct / 100.0)))
    llc_cents = gross_revenue_cents - partner_cents

    return PayoutLine(
        partner_id=partner_id,
        domain=domain,
        gross_revenue_cents=gross_revenue_cents,
        partner_cents=partner_cents,
        llc_cents=llc_cents,
        rev_share_pct=pct,
    )
