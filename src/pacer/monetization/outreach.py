"""AI-generated competitor outreach — offer the caught domain to the top
organic competitor of the defunct company.
"""
from __future__ import annotations

from loguru import logger

from pacer.config import get_settings
from pacer.models.domain_candidate import DomainCandidate
from pacer.scoring.relevance import _get_client  # re-use OpenAI client

settings = get_settings()


OUTREACH_PROMPT = """
You are writing a short, respectful outbound email from 1COMMERCE LLC.

We now own the expired domain {domain} (previously owned by {prior_company}).
The recipient is a competitor in the same vertical and would benefit from
redirecting the domain's existing SEO equity (DR {dr}, {refdomains} referring
domains) to their own site.

Write a 4–6 sentence pitch offering the domain. Tone: direct, no fluff, drop
one concrete SEO figure. Include a single CTA: reply to discuss terms.
"""


async def send_competitor_outreach(
    candidate: DomainCandidate,
    competitor_email: str,
    competitor_name: str,
) -> str:
    client = _get_client()
    if client is None:
        return ""
    prompt = OUTREACH_PROMPT.format(
        domain=candidate.domain,
        prior_company=candidate.company_name or "a recently defunct company",
        dr=candidate.domain_rating or 0,
        refdomains=candidate.referring_domains or 0,
    )
    resp = await client.chat.completions.create(
        model=settings.openai_model,
        messages=[
            {"role": "system", "content": "You are a concise sales writer."},
            {"role": "user", "content": prompt},
        ],
        max_tokens=350,
        temperature=0.4,
    )
    body = resp.choices[0].message.content or ""
    # Hook: wire to MailerLite / SMTP / SendGrid here.
    logger.info(
        "outreach_drafted domain={} to={} chars={}",
        candidate.domain,
        competitor_email,
        len(body),
    )
    return body
