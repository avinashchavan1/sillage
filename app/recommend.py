"""Similarity and wear suggestions built on the taste model.

Each fragrance becomes a sparse scent vector: canonical notes (weighted by pyramid layer) plus accords
(weighted by strength). Cosine similarity between vectors answers "what in my collection smells like
this?"; the wear suggester blends taste score, time since last worn, and how well a fragrance's accords
match what you historically wear in a given weather / occasion.
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from datetime import date
from typing import Optional

from .insights import LAYER_WEIGHTS, TasteModel, _as_date
from .text import note_key

WEARABLE = {"owned", "sample", "decant"}
WEATHER_WEIGHT = 3.5  # strong enough that a loved gourmand loses to a liked citrus on a hot day

# Prior knowledge for weather fit when the wear log doesn't say enough yet.
_WARM = {"citrus": 1.0, "aquatic": 1.0, "marine": 0.9, "fresh": 0.9, "green": 0.7, "ozonic": 0.8, "aromatic": 0.5,
         "fresh spicy": 0.5, "fruity": 0.4, "white floral": 0.3, "salty": 0.6,
         "vanilla": -0.6, "amber": -0.7, "oud": -0.8, "tobacco": -0.7, "leather": -0.6, "sweet": -0.5,
         "balsamic": -0.6, "warm spicy": -0.6, "smoky": -0.6, "honey": -0.5, "chocolate": -0.6}
_COLD = {k: -v for k, v in _WARM.items()}
WEATHER_PRIOR = {"hot": _WARM, "warm": {k: v * 0.6 for k, v in _WARM.items()}, "humid": _WARM,
                 "cold": _COLD, "cool": {k: v * 0.6 for k, v in _COLD.items()}, "rainy": {k: v * 0.4 for k, v in _COLD.items()}}


def scent_vector(f: dict) -> dict[str, float]:
    vec: dict[str, float] = {}
    for layer, w in LAYER_WEIGHTS.items():
        for note in f.get(layer) or []:
            key = "n:" + note_key(note)
            vec[key] = max(vec.get(key, 0.0), w)
    for a in f.get("accords") or []:
        key = "a:" + a["name"].lower()
        vec[key] = max(vec.get(key, 0.0), 1.2 * float(a.get("strength") or 1.0))
    return vec


def cosine(a: dict[str, float], b: dict[str, float]) -> float:
    if not a or not b:
        return 0.0
    dot = sum(v * b[k] for k, v in a.items() if k in b)
    na = math.sqrt(sum(v * v for v in a.values()))
    nb = math.sqrt(sum(v * v for v in b.values()))
    return dot / (na * nb) if na and nb else 0.0


def similar(target: dict, fragrances: list[dict], limit: int = 5) -> list[dict]:
    tv = scent_vector(target)
    rows = []
    for f in fragrances:
        if f.get("id") is not None and f.get("id") == target.get("id"):
            continue
        s = cosine(tv, scent_vector(f))
        if s <= 0:
            continue
        fv = scent_vector(f)
        shared = list(dict.fromkeys(k[2:] for k in tv if k in fv))
        rows.append({"id": f["id"], "name": f["name"], "brand": f.get("brand"), "status": f.get("status"),
                     "rating": f.get("sentiment"), "similarity": round(s, 3), "shared": shared[:8]})
    rows.sort(key=lambda r: -r["similarity"])
    return rows[:limit]


def _weather_profile(fragrances: list[dict], wears: list[dict], weather: str) -> dict[str, float]:
    """Which accords you over-index on in this weather, from your own log (falls back to a prior)."""
    by_id = {f["id"]: f for f in fragrances}
    here, overall = Counter(), Counter()
    n_here = 0
    for w in wears:
        f = by_id.get(w["fragrance_id"])
        if not f:
            continue
        for a in f.get("accords") or []:
            overall[a["name"]] += 1
            if (w.get("weather") or "").lower() == weather:
                here[a["name"]] += 1
        n_here += (w.get("weather") or "").lower() == weather
    prior = WEATHER_PRIOR.get(weather, {})
    if n_here < 5:
        return prior
    total_here, total = sum(here.values()) or 1, sum(overall.values()) or 1
    learned = {k: (here[k] / total_here) / (overall[k] / total) - 1 for k in overall}  # lift vs your average
    return {k: 0.6 * learned.get(k, 0) + 0.4 * prior.get(k, 0) for k in set(learned) | set(prior)}


def suggest_wear(fragrances: list[dict], wears: list[dict], weather: Optional[str] = None,
                 occasion: Optional[str] = None, limit: int = 3, today: Optional[date] = None) -> list[dict]:
    today = today or date.today()
    model = TasteModel(fragrances, wears, today)
    weather = (weather or "").strip().lower() or None
    occasion = (occasion or "").strip().lower() or None
    wprof = _weather_profile(fragrances, wears, weather) if weather else {}
    occ_counts: dict[int, int] = defaultdict(int)
    for w in wears:
        if occasion and (w.get("occasion") or "").lower() == occasion:
            occ_counts[w["fragrance_id"]] += 1
    last: dict[int, date] = {}
    for w in wears:
        d = _as_date(w["worn_on"])
        if w["fragrance_id"] not in last or d > last[w["fragrance_id"]]:
            last[w["fragrance_id"]] = d

    out = []
    for f in fragrances:
        if f.get("status") not in WEARABLE or (f.get("sentiment") is not None and f["sentiment"] < 0):
            continue
        reasons = []
        taste = model.scores.get(f["id"], 0.5)
        days = (today - last[f["id"]]).days if f["id"] in last else None
        freshness = min((days if days is not None else 60) / 21, 2.0)
        if days is None:
            reasons.append("you haven't logged wearing it yet")
        elif days >= 21:
            reasons.append(f"not worn in {days} days")
        fit = 0.0
        if wprof:
            accs = f.get("accords") or []
            total = sum(float(a.get("strength") or 1) for a in accs) or 1
            fit = sum(wprof.get(a["name"], 0) * float(a.get("strength") or 1) for a in accs) / total
            if fit > 0.25:
                reasons.append(f"suits {weather} weather")
            elif fit < -0.3:
                reasons.append(f"heavy for {weather} weather")
        occ = min(occ_counts.get(f["id"], 0), 4) * 0.25
        if occ:
            reasons.append(f"a go-to for {occasion}")
        if taste >= 1.5:
            reasons.append("one of your favourites")
        score = taste + 0.6 * freshness + WEATHER_WEIGHT * fit + occ
        out.append({"id": f["id"], "name": f["name"], "brand": f.get("brand"), "score": round(score, 3),
                    "days_since_worn": days, "reasons": reasons or ["a solid all-rounder for you"]})
    out.sort(key=lambda r: -r["score"])
    return out[:limit]
