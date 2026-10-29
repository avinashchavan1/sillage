"""Fragrance lookup: tries providers in order (default: direct Fragrantica, then Claude web research).

Override with SILLAGE_LOOKUP_ORDER, e.g. "claude,fragrantica" or "fragrantica".
"""

from __future__ import annotations

import os

from .. import claude_client
from ..models import LookupResult
from ..text import match_score
from . import claude, fragrantica

WEAK_MATCH = 0.5


class LookupFailed(Exception):
    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("; ".join(errors))


def _order() -> list[str]:
    raw = os.environ.get("SILLAGE_LOOKUP_ORDER", "fragrantica,claude")
    return [p.strip().lower() for p in raw.split(",") if p.strip()]


def lookup(query: str) -> LookupResult:
    result = _lookup(query.strip())
    d = result.details
    if not d.image_url:  # e.g. a Claude lookup that read the Fragrantica page but returned no image
        d.image_url = fragrantica.image_for(d.source_url)
    return result


def _lookup(query: str) -> LookupResult:
    is_url = fragrantica.canonical_url(query) is not None
    order = _order()
    errors: list[str] = []
    weak: LookupResult | None = None

    for i, provider in enumerate(order):
        if provider == "fragrantica":
            try:
                details, alts = fragrantica.lookup(query)
            except fragrantica.FragranticaError as exc:
                errors.append(f"Fragrantica: {exc}")
                continue
            result = LookupResult(details=details, alternatives=alts)
            score = match_score(query, f"{details.brand} {details.name}")
            claude_next = "claude" in order[i + 1 :] and claude_client.available()
            if not is_url and score < WEAK_MATCH and claude_next:
                weak = result  # search engine may have picked the wrong perfume; let Claude try
                continue
            return result

        if provider == "claude":
            if not claude_client.available():
                errors.append("Claude: not configured (set ANTHROPIC_API_KEY for smart lookups)")
                continue
            try:
                details, alts, note = claude.lookup(query)
            except (claude.ClaudeLookupError, claude_client.ClaudeUnavailable) as exc:
                errors.append(f"Claude: {exc}")
                continue
            return LookupResult(details=details, alternatives=alts, message=note)

    if weak is not None:
        weak.message = "Closest Fragrantica match — double-check it's the one you meant."
        return weak
    raise LookupFailed(errors or ["No lookup providers configured"])
