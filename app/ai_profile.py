"""Claude-written scent profile + recommendations, built on top of the statistical report."""

from __future__ import annotations

import json

from pydantic import BaseModel

from .claude_client import FALLBACK_BETA, MODEL, client
from .models import SENTIMENT_LABELS


class Recommendation(BaseModel):
    name: str
    brand: str
    why: str
    key_notes: list[str]


class Avoid(BaseModel):
    name: str
    brand: str
    why: str


class AIProfile(BaseModel):
    headline: str
    narrative: str
    signature_traits: list[str]
    recommendations: list[Recommendation]
    avoid: list[Avoid]
    collection_gaps: list[str]


SYSTEM = """You are a perfumery expert helping a collector understand their own taste.
You get statistics computed from their ratings and wear log, plus their collection.
Write in second person, warm but precise, like a knowledgeable friend at a perfume counter.
Ground every claim in the data provided; name specific notes, accords and fragrances from it.
Recommend real, currently obtainable perfumes that are NOT already in the collection."""


def _collection_lines(fragrances: list[dict]) -> str:
    lines = []
    for f in fragrances:
        rating = SENTIMENT_LABELS.get(f.get("sentiment"), "unrated") if f.get("sentiment") is not None else "unrated"
        accords = ", ".join(a["name"] for a in (f.get("accords") or [])[:4])
        lines.append(
            f"- {f.get('brand') or '?'} – {f['name']} [{f.get('status')}, {rating}, worn {f.get('wear_count', 0)}x]"
            + (f" accords: {accords}" if accords else "")
        )
    return "\n".join(lines)


def generate(report: dict, fragrances: list[dict]) -> dict:
    slim = {k: report[k] for k in (
        "summary", "liked_notes", "disliked_notes", "liked_accords", "disliked_accords",
        "polarizing_notes", "families", "perfumers", "seasons", "occasions", "most_worn", "neglected", "totals",
    )}
    for key in ("liked_notes", "disliked_notes", "liked_accords", "disliked_accords", "polarizing_notes"):
        slim[key] = [{**a, "fragrances": [f"{x['brand']} {x['name']}" for x in a["fragrances"][:4]]} for a in slim[key]]

    prompt = (
        "<taste_statistics>\n" + json.dumps(slim, ensure_ascii=False) + "\n</taste_statistics>\n\n"
        "<collection>\n" + _collection_lines(fragrances) + "\n</collection>\n\n"
        "Affinity ranges roughly from -2 (hated) to +3 (loved and worn a lot); confidence is 0-1.\n"
        "Write: a one-line headline for their scent identity; a narrative of 2 short paragraphs on what "
        "they love, what they avoid and any surprising patterns (polarizing notes, seasonal habits); "
        "4-6 signature traits; 6 recommendations with why each fits and its key notes; 3 popular "
        "fragrances they'd likely dislike and why; and 2-4 gaps in the collection (seasons, occasions "
        "or styles it doesn't cover well)."
    )
    resp = client().beta.messages.parse(
        model=MODEL,
        max_tokens=16000,
        system=SYSTEM,
        messages=[{"role": "user", "content": prompt}],
        output_format=AIProfile,
        output_config={"effort": "medium"},
        betas=[FALLBACK_BETA],
        fallbacks="default",
    )
    if resp.stop_reason == "refusal" or resp.parsed_output is None:
        raise RuntimeError("Claude could not produce a profile for this collection")
    return resp.parsed_output.model_dump()
