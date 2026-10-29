"""Small text helpers: name matching, note normalization, list splitting."""

from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher

_WORD = re.compile(r"[a-z0-9]+")

# Origin/qualifier words that make two notes look different when, for taste purposes,
# they're the same thing ("Calabrian bergamot" and "Bergamot" -> "bergamot").
_NOTE_QUALIFIERS = {
    "african", "amalfi", "atlas", "australian", "bourbon", "brazilian", "bulgarian", "calabrian",
    "cambodian", "chinese", "egyptian", "french", "guatemalan", "haitian", "himalayan", "indian",
    "indonesian", "italian", "jamaican", "japanese", "javanese", "laotian", "madagascan", "madagascar",
    "mediterranean", "mexican", "moroccan", "omani", "peruvian", "provencal", "sicilian", "somalian",
    "spanish", "sri", "lankan", "tahitian", "texas", "tunisian", "turkish", "tuscan", "virginia",
    "virginian", "damask", "taif", "may",
}
_NOTE_SUFFIXES = {"absolute", "absolue", "essence", "oil", "extract", "co2", "infusion", "tincture"}

CONCENTRATIONS = [
    ("extrait de parfum", "Extrait de Parfum"),
    ("extrait", "Extrait de Parfum"),
    ("eau de parfum", "Eau de Parfum"),
    ("edp", "Eau de Parfum"),
    ("eau de toilette", "Eau de Toilette"),
    ("edt", "Eau de Toilette"),
    ("eau de cologne", "Eau de Cologne"),
    ("edc", "Eau de Cologne"),
    ("cologne", "Cologne"),
    ("elixir", "Elixir"),
    ("parfum", "Parfum"),
    ("perfume oil", "Perfume Oil"),
    ("attar", "Attar"),
]


def fold(text: str) -> str:
    """Lowercase and strip accents: 'François' -> 'francois'."""
    text = unicodedata.normalize("NFKD", text or "")
    return "".join(c for c in text if not unicodedata.combining(c)).lower()


def tokens(text: str) -> list[str]:
    return _WORD.findall(fold(text).replace("'", ""))


def _token_in(token: str, pool: list[str]) -> bool:
    return any(token == p or (len(token) > 3 and SequenceMatcher(None, token, p).ratio() >= 0.8) for p in pool)


def match_score(query: str, candidate: str) -> float:
    """How well `candidate` (e.g. 'Dior Sauvage Elixir') answers `query` (e.g. 'sauvage dior').

    1.0 = every query word present and nothing extra; extra words cost a little, so the
    original beats its flankers when the user didn't name a flanker.
    """
    q = [t for t in tokens(query) if t not in {"by", "the", "de", "and"}]
    c = [t for t in tokens(candidate) if t not in {"by", "the", "de", "and"}]
    if not q or not c:
        return 0.0
    found = sum(1 for t in q if _token_in(t, c))
    extra = sum(1 for t in c if not _token_in(t, q))
    return round(found / len(q) - 0.08 * extra, 3)


def note_key(note: str) -> str:
    """Canonical key used to group notes for preference analysis."""
    words = tokens(note)
    stripped = [w for w in words if w not in _NOTE_QUALIFIERS]
    while len(stripped) > 1 and stripped[-1] in _NOTE_SUFFIXES:
        stripped.pop()
    return " ".join(stripped or words)


def split_list(text: str) -> list[str]:
    """'Bergamot, Pepper and Lemon' -> ['Bergamot', 'Pepper', 'Lemon']."""
    text = text.strip().rstrip(".")
    parts = re.split(r",\s*|\s+and\s+|\s*;\s*", text)
    return [p.strip() for p in parts if p.strip()]


def infer_concentration(name: str) -> str | None:
    folded = " " + " ".join(tokens(name)) + " "
    for needle, label in CONCENTRATIONS:
        if f" {needle} " in folded:
            return label
    return None
