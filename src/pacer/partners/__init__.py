"""Partner / profit-share module.

Partners operate under 1COMMERCE LLC as 1099-NEC contractors. They do NOT
form new LLCs and do NOT hold equity in 1COMMERCE LLC. Revenue share is
capped at 24.9% to stay below the CTA/BOI beneficial-ownership threshold
(see legal/partner_profit_share_agreement.md).
"""
from pacer.partners.models.partner import Partner, PartnerStatus

__all__ = ["Partner", "PartnerStatus"]
