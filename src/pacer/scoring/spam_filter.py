"""Heuristic spam / toxicity filter run before LLM scoring to save tokens."""

from __future__ import annotations

import re

_BAD_TLDS = {".tk", ".ml", ".ga", ".cf", ".cn", ".ru", ".top", ".xyz"}
_BAD_PATTERNS = (
    re.compile(r"\d{4,}"),  # long digit runs
    re.compile(r"-.{0,3}-.{0,3}-"),  # heavy hyphenation
    re.compile(r"(porn|casino|loan|viagra|cbd)", re.I),
)


def is_likely_spam(domain: str) -> bool:
    low = domain.lower()
    for tld in _BAD_TLDS:
        if low.endswith(tld):
            return True
    for pat in _BAD_PATTERNS:
        if pat.search(low):
            return True
    return False


def spam_score(domain: str) -> float:
    """0 (clean) … 1 (junk)."""
    score = 0.0
    low = domain.lower()
    if any(low.endswith(t) for t in _BAD_TLDS):
        score += 0.5
    if re.search(r"\d{4,}", low):
        score += 0.2
    if low.count("-") >= 3:
        score += 0.2
    if any(p.search(low) for p in _BAD_PATTERNS):
        score += 0.3
    return min(score, 1.0)
