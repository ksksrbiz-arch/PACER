"""LLM abstraction layer for PACER relevance scoring.

Provider priority chain (configured via LLM_PROVIDER env var):
    1. claude  — Anthropic Claude (primary, best quality)
    2. groq    — Groq free-tier Llama (fallback when Claude hits rate limits)
    3. openai  — OpenAI GPT-4o (legacy / secondary fallback)

On a rate-limit / overload response (HTTP 429 or 529) the client
transparently retries the next provider in the chain so the scoring
pipeline never stalls during Claude usage spikes.
"""
from __future__ import annotations

import json
from typing import Any

import httpx
from loguru import logger

from pacer.config import get_settings

settings = get_settings()

SYSTEM_PROMPT = (
    "You are a domain-portfolio analyst. Return a JSON object with: "
    '{"relevance": 0..100, "vertical": str, "commercial_intent": 0..100, "notes": str}. '
    "Score relevance for SaaS/B2B tech repurposing potential."
)

# ─────────────────────── provider implementations ──────────────────────────


async def _call_claude(domain: str, company: str | None) -> dict[str, Any]:
    """Call Anthropic Claude.  Returns parsed JSON dict or raises."""
    import anthropic  # lazy import — optional dependency

    key = settings.anthropic_api_key.get_secret_value()
    if not key:
        raise ValueError("ANTHROPIC_API_KEY not set")

    client = anthropic.AsyncAnthropic(api_key=key)
    user_msg = f"Domain: {domain}\nCompany: {company or 'unknown'}"
    msg = await client.messages.create(
        model=settings.anthropic_model,
        max_tokens=300,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_msg}],
        temperature=0.2,  # type: ignore[arg-type]
    )
    text = msg.content[0].text if msg.content else "{}"
    # Claude sometimes wraps JSON in a markdown fence
    text = text.strip()
    if text.startswith("```"):
        text = "\n".join(text.splitlines()[1:])
        text = text.rstrip("`").strip()
    return json.loads(text)


async def _call_groq(domain: str, company: str | None) -> dict[str, Any]:
    """Call Groq (OpenAI-compatible API, free tier).  Returns parsed JSON dict or raises."""
    from openai import AsyncOpenAI  # Groq is OpenAI wire-compatible

    key = settings.groq_api_key.get_secret_value()
    if not key:
        raise ValueError("GROQ_API_KEY not set")

    client = AsyncOpenAI(
        api_key=key,
        base_url="https://api.groq.com/openai/v1",
    )
    user_msg = f"Domain: {domain}\nCompany: {company or 'unknown'}"
    resp = await client.chat.completions.create(
        model=settings.groq_model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_msg},
        ],
        response_format={"type": "json_object"},
        temperature=0.2,
        max_tokens=300,
    )
    return json.loads(resp.choices[0].message.content or "{}")


async def _call_openai(domain: str, company: str | None) -> dict[str, Any]:
    """Call OpenAI GPT-4o (legacy path).  Returns parsed JSON dict or raises."""
    from openai import AsyncOpenAI

    key = settings.openai_api_key.get_secret_value()
    if not key:
        raise ValueError("OPENAI_API_KEY not set")

    client = AsyncOpenAI(api_key=key)
    user_msg = f"Domain: {domain}\nCompany: {company or 'unknown'}"
    resp = await client.chat.completions.create(
        model=settings.openai_model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_msg},
        ],
        response_format={"type": "json_object"},
        temperature=0.2,
        max_tokens=250,
    )
    return json.loads(resp.choices[0].message.content or "{}")


# ───────────────── ordered fallback chain ──────────────────────────────────

_PROVIDERS: dict[str, Any] = {
    "claude": _call_claude,
    "groq": _call_groq,
    "openai": _call_openai,
}

_FALLBACK_CHAIN: dict[str, list[str]] = {
    "claude": ["claude", "groq", "openai"],
    "groq": ["groq", "openai"],
    "openai": ["openai"],
}

# HTTP status codes that indicate rate-limit / overload → try next provider
_RATE_LIMIT_STATUSES = {429, 503, 529}


def _is_rate_limited(exc: Exception) -> bool:
    """Return True if the exception is a rate-limit or overload error."""
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in _RATE_LIMIT_STATUSES
    # anthropic / openai SDK wrap HTTP errors in their own types
    msg = str(exc).lower()
    return any(kw in msg for kw in ("rate limit", "overloaded", "529", "too many requests"))


async def llm_relevance_with_fallback(domain: str, company: str | None = None) -> dict[str, Any]:
    """Call the configured LLM provider, falling back on rate-limit errors.

    Returns a relevance dict or {} if all providers fail / are unconfigured.
    """
    chain = _FALLBACK_CHAIN.get(settings.llm_provider, ["claude", "groq", "openai"])

    for provider_name in chain:
        fn = _PROVIDERS[provider_name]
        try:
            result = await fn(domain, company)
            if provider_name != settings.llm_provider:
                logger.info(
                    "llm_fallback_used primary={} actual={} domain={}",
                    settings.llm_provider,
                    provider_name,
                    domain,
                )
            return result
        except ValueError as exc:
            # Key not configured — skip silently and try next
            logger.debug("llm_provider_skipped provider={} reason={}", provider_name, exc)
            continue
        except Exception as exc:
            if _is_rate_limited(exc):
                logger.warning(
                    "llm_rate_limited provider={} domain={} err={} — trying next",
                    provider_name,
                    domain,
                    exc,
                )
                continue
            logger.warning("llm_call_failed provider={} domain={} err={}", provider_name, domain, exc)
            return {}

    logger.warning("llm_all_providers_failed domain={}", domain)
    return {}


# ───────────────── free-form text generation ───────────────────────────────


async def _gen_claude(system: str, user: str) -> str:
    import anthropic  # lazy import

    key = settings.anthropic_api_key.get_secret_value()
    if not key:
        raise ValueError("ANTHROPIC_API_KEY not set")
    client = anthropic.AsyncAnthropic(api_key=key)
    msg = await client.messages.create(
        model=settings.anthropic_model,
        max_tokens=500,
        system=system,
        messages=[{"role": "user", "content": user}],
        temperature=0.4,  # type: ignore[arg-type]
    )
    return msg.content[0].text if msg.content else ""


async def _gen_groq(system: str, user: str) -> str:
    from openai import AsyncOpenAI

    key = settings.groq_api_key.get_secret_value()
    if not key:
        raise ValueError("GROQ_API_KEY not set")
    client = AsyncOpenAI(api_key=key, base_url="https://api.groq.com/openai/v1")
    resp = await client.chat.completions.create(
        model=settings.groq_model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        max_tokens=500,
        temperature=0.4,
    )
    return resp.choices[0].message.content or ""


async def _gen_openai(system: str, user: str) -> str:
    from openai import AsyncOpenAI

    key = settings.openai_api_key.get_secret_value()
    if not key:
        raise ValueError("OPENAI_API_KEY not set")
    client = AsyncOpenAI(api_key=key)
    resp = await client.chat.completions.create(
        model=settings.openai_model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        max_tokens=500,
        temperature=0.4,
    )
    return resp.choices[0].message.content or ""


_GEN_PROVIDERS: dict[str, Any] = {
    "claude": _gen_claude,
    "groq": _gen_groq,
    "openai": _gen_openai,
}


async def llm_generate_text(system: str, user: str) -> str:
    """Generate free-form text via the configured LLM with auto-fallback.

    Returns the generated string, or "" if all providers fail.
    """
    chain = _FALLBACK_CHAIN.get(settings.llm_provider, ["claude", "groq", "openai"])

    for provider_name in chain:
        fn = _GEN_PROVIDERS[provider_name]
        try:
            result = await fn(system, user)
            if provider_name != settings.llm_provider:
                logger.info(
                    "llm_gen_fallback_used primary={} actual={}",
                    settings.llm_provider,
                    provider_name,
                )
            return result
        except ValueError as exc:
            logger.debug("llm_gen_provider_skipped provider={} reason={}", provider_name, exc)
            continue
        except Exception as exc:
            if _is_rate_limited(exc):
                logger.warning(
                    "llm_gen_rate_limited provider={} err={} — trying next",
                    provider_name,
                    exc,
                )
                continue
            logger.warning("llm_gen_failed provider={} err={}", provider_name, exc)
            return ""

    logger.warning("llm_gen_all_providers_failed")
    return ""
