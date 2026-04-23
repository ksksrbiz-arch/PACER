"""Topical relevance check for candidate domains.

Thin shim that delegates to llm_client.llm_relevance_with_fallback.
The ``llm_relevance`` name is kept for backward compatibility with
existing engine.py call sites and test monkeypatches.
"""

from __future__ import annotations

from pacer.scoring.llm_client import llm_relevance_with_fallback

# Public alias used by engine.py and tests
llm_relevance = llm_relevance_with_fallback

__all__ = ["llm_relevance"]
