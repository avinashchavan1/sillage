"""Shared Anthropic client setup. Claude features are optional: without credentials the app still
works using the direct Fragrantica scraper and the local (statistical) insights."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

MODEL = os.environ.get("SILLAGE_MODEL", "claude-opus-5-5")
# Server-side refusal fallback: if the model declines, the API re-runs on a recommended fallback model.
FALLBACK_BETA = "server-side-fallback-2026-07-01"


class ClaudeUnavailable(Exception):
    pass


def available() -> bool:
    if os.environ.get("SILLAGE_AI", "on").lower() in ("0", "off", "false", "no"):
        return False
    if any(os.environ.get(k) for k in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_PROFILE")):
        return True
    return (Path.home() / ".config" / "anthropic").exists()  # `ant auth login` profile


@lru_cache(maxsize=1)
def client():
    if not available():
        raise ClaudeUnavailable("Claude is not configured (set ANTHROPIC_API_KEY to enable AI lookups)")
    import anthropic

    return anthropic.Anthropic(max_retries=3, timeout=300)
