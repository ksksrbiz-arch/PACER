"""Partner payout math + CTA/BOI guardrails."""
from __future__ import annotations

import pytest

from pacer.partners.payout import compute_payout


def test_default_rev_share_splits_correctly():
    line = compute_payout(partner_id=1, domain="widget.com", gross_revenue_cents=10_000)
    # Default is 20% (from settings)
    assert line.partner_cents == 2_000
    assert line.llc_cents == 8_000
    assert line.partner_cents + line.llc_cents == 10_000


def test_explicit_rev_share_overrides_default():
    line = compute_payout(
        partner_id=1,
        domain="widget.com",
        gross_revenue_cents=10_000,
        rev_share_pct=15.0,
    )
    assert line.partner_cents == 1_500
    assert line.llc_cents == 8_500


def test_cta_boi_cap_enforced():
    # 25.0% would make them a beneficial owner — must raise
    with pytest.raises(ValueError):
        compute_payout(
            partner_id=1,
            domain="widget.com",
            gross_revenue_cents=10_000,
            rev_share_pct=25.0,
        )


def test_max_allowed_is_24_9():
    line = compute_payout(
        partner_id=1,
        domain="widget.com",
        gross_revenue_cents=10_000,
        rev_share_pct=24.9,
    )
    assert line.partner_cents == 2_490


def test_negative_revenue_rejected():
    with pytest.raises(ValueError):
        compute_payout(partner_id=1, domain="x.com", gross_revenue_cents=-1)


def test_zero_revenue_produces_zero_payout():
    line = compute_payout(partner_id=1, domain="x.com", gross_revenue_cents=0)
    assert line.partner_cents == 0
    assert line.llc_cents == 0


def test_rounding_does_not_overpay_partner():
    # 33 cents * 20% = 6.6 → 7 partner + 26 llc = 33 (partner rounds up, LLC absorbs)
    line = compute_payout(partner_id=1, domain="x.com", gross_revenue_cents=33)
    assert line.partner_cents + line.llc_cents == 33
