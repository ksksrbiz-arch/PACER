"""GPT-4o topical relevance check for candidate domains."""
from __future__ import annotations

import json

from loguru import logger
from openai import AsyncOpenAI

from pacer.config import get_settings

settings = get_settings()

_client: AsyncOpenAI | None = None


def _get_client() -> AsyncOpenAI | None:
    global _client
    key = settings.openai_api_key.get_secret_value()
    if not key:
        return None
    if _client is None:
        _client = AsyncOpenAI(api_key=key)
    return _client


SYSTEM = (
    "You are a domain-portfolio analyst. Return a JSON object with: "
    '{"relevance": 0..100, "vertical": str, "commercial_intent": 0..100, "notes": str}. '
    "Score relevance for SaaS/B2B tech repurposing potential."
)


async def llm_relevance(domain: str, company_name: str | None = None) -> dict:
    """Ask GPT-4o for a structured relevance rating. Returns {} on failure."""
    client = _get_client()
    if client is None:
        return {}

    prompt = f"Domain: {domain}\nCompany: {company_name or 'unknown'}"
    try:
        resp = await client.chat.completions.create(
            model=settings.openai_model,
            messages=[
                {"role": "system", "content": SYSTEM},
                {"role": "user", "content": prompt},
            ],
            response_format={"type": "json_object"},
            temperature=0.2,
            max_tokens=250,
        )
        return json.loads(resp.choices[0].message.content or "{}")
    except Exception as exc:
        logger.warning("llm_relevance_failed domain={} err={}", domain, exc)
        return {}
