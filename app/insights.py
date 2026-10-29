"""Preference analysis: turns ratings + wear history into note/accord affinities and predictions.

How a fragrance's score is built (range about -2..3):
  * your rating (Hate -2 … Love +2), blended 70/30 with the average "enjoyment" of your wears;
  * if unrated, the wear enjoyment average; if only worn, neutral (0);
  * plus a wear-frequency bonus (up to +1) for liked/unrated scents you reach for in the last year,
    since what you actually wear says as much as what you say you like.
Wishlist items are never learned from (you may not have smelled them), only predicted.

A note's affinity is a shrunk weighted mean of the scores of fragrances containing it:
    affinity = Σ(weight·score) / (Σweight + PRIOR)
so a note seen once in one loved perfume ranks below a note found across five of them.
"""

from __future__ import annotations

import math
import os
from collections import Counter, defaultdict
from datetime import date, timedelta
from statistics import mean
from typing import Iterable, Optional

from .models import SENTIMENT_LABELS
from .text import note_key

LAYER_WEIGHTS = {"top_notes": 0.7, "middle_notes": 1.0, "base_notes": 1.0, "notes": 0.9}
PRIOR = 1.5
LIKE_THRESHOLD = 0.35
MIN_SCORED = 3
WEAR_BONUS_CAP = 1.0
OWNED_STATUSES = {"owned", "decant", "sample"}

_SEASONS_NORTH = {12: "winter", 1: "winter", 2: "winter", 3: "spring", 4: "spring", 5: "spring",
                  6: "summer", 7: "summer", 8: "summer", 9: "fall", 10: "fall", 11: "fall"}
_FLIP = {"winter": "summer", "summer": "winter", "spring": "fall", "fall": "spring"}


def season_of(d: date) -> str:
    s = _SEASONS_NORTH[d.month]
    return _FLIP[s] if os.environ.get("SILLAGE_HEMISPHERE", "north").lower() == "south" else s


def _as_date(v) -> date:
    return v if isinstance(v, date) else date.fromisoformat(str(v)[:10])


# ---------------------------------------------------------------- scoring


def fragrance_score(f: dict, wears: list[dict], today: date) -> Optional[float]:
    if f.get("status") == "wishlist":
        return None
    explicit = f.get("sentiment")
    enjoy = [w["enjoyment"] for w in wears if w.get("enjoyment") is not None]
    recent = sum(1 for w in wears if (today - _as_date(w["worn_on"])).days <= 365)
    if explicit is not None:
        base = 0.7 * explicit + 0.3 * mean(enjoy) if enjoy else float(explicit)
    elif enjoy:
        base = mean(enjoy)
    elif wears:
        base = 0.0
    else:
        return None
    bonus = min(WEAR_BONUS_CAP, 0.35 * math.log2(1 + recent)) if base >= 0 else 0.0
    return round(base + bonus, 3)


def _note_items(f: dict) -> Iterable[tuple[str, str, float]]:
    """(canonical key, display label, weight) for each distinct note in a fragrance."""
    seen = set()
    for layer, weight in LAYER_WEIGHTS.items():
        for note in f.get(layer) or []:
            key = note_key(note)
            if key and key not in seen:
                seen.add(key)
                yield key, note, weight


def _accord_items(f: dict) -> Iterable[tuple[str, str, float]]:
    for a in f.get("accords") or []:
        name = a["name"].lower()
        yield name, name, max(0.2, float(a.get("strength") or 1.0))


def _affinities(scored: list[tuple[dict, float]], items) -> dict[str, dict]:
    acc: dict[str, dict] = defaultdict(lambda: {"sum": 0.0, "weight": 0.0, "labels": Counter(), "fragrances": []})
    for f, score in scored:
        for key, label, weight in items(f):
            e = acc[key]
            e["sum"] += weight * score
            e["weight"] += weight
            e["labels"][label] += 1
            e["fragrances"].append({"id": f["id"], "name": f["name"], "brand": f.get("brand"), "score": score})
    out = {}
    for key, e in acc.items():
        # most frequent spelling; ties go to the plainest ("Vanilla" over "Madagascar Vanilla")
        label = min(e["labels"].items(), key=lambda kv: (-kv[1], len(kv[0])))[0]
        out[key] = {
            "key": key,
            "label": label[:1].upper() + label[1:],
            "affinity": round(e["sum"] / (e["weight"] + PRIOR), 3),
            "confidence": round(e["weight"] / (e["weight"] + PRIOR), 3),
            "count": len(e["fragrances"]),
            "fragrances": sorted(e["fragrances"], key=lambda x: -x["score"]),
        }
    return out


def _ranked(aff: dict[str, dict], positive: bool, limit: int) -> list[dict]:
    if positive:
        rows = [a for a in aff.values() if a["affinity"] >= LIKE_THRESHOLD]
        rows.sort(key=lambda a: (-a["affinity"], -a["count"]))
    else:
        rows = [a for a in aff.values() if a["affinity"] <= -LIKE_THRESHOLD]
        rows.sort(key=lambda a: (a["affinity"], -a["count"]))
    return [_public(a) for a in rows[:limit]]


def _public(a: dict) -> dict:
    return {**{k: a[k] for k in ("key", "label", "affinity", "confidence", "count")},
            "fragrances": a["fragrances"][:6]}


# ---------------------------------------------------------------- model


class TasteModel:
    """Fitted note/accord affinities for one collection."""

    def __init__(self, fragrances: list[dict], wears: list[dict], today: Optional[date] = None):
        self.today = today or date.today()
        by_f: dict[int, list[dict]] = defaultdict(list)
        for w in wears:
            by_f[w["fragrance_id"]].append(w)
        self.wears_by_fragrance = by_f
        self.scores: dict[int, float] = {}
        scored = []
        for f in fragrances:
            s = fragrance_score(f, by_f.get(f["id"], []), self.today)
            if s is not None:
                self.scores[f["id"]] = s
                scored.append((f, s))
        self.scored = scored
        self.notes = _affinities(scored, _note_items)
        self.accords = _affinities(scored, _accord_items)

    @property
    def ready(self) -> bool:
        return len(self.scored) >= MIN_SCORED

    def note_affinity(self, f: dict) -> dict[str, float]:
        """Your affinity for each note in `f`, for notes seen in at least two fragrances."""
        out = {}
        for key, label, _ in _note_items(f):
            a = self.notes.get(key)
            if a and a["count"] >= 2:
                out[label] = a["affinity"]
        return out

    def predict(self, f: dict) -> Optional[dict]:
        """Estimate how much you'd like `f` from its notes/accords. None if nothing overlaps."""
        if not self.scored:
            return None
        parts = []  # (label, kind, affinity, weight)
        for key, label, w in _note_items(f):
            if key in self.notes and self.notes[key].get("count"):
                a = self.notes[key]
                # don't let the fragrance vouch for itself
                if a["count"] == 1 and a["fragrances"][0]["id"] == f.get("id"):
                    continue
                parts.append((a["label"], "note", a["affinity"], w * a["confidence"]))
        note_total = sum(1 for _ in _note_items(f))
        for key, label, w in _accord_items(f):
            if key in self.accords:
                a = self.accords[key]
                if a["count"] == 1 and a["fragrances"][0]["id"] == f.get("id"):
                    continue
                parts.append((a["label"], "accord", a["affinity"], 0.8 * w * a["confidence"]))
        if not parts:
            return None
        total_w = sum(p[3] for p in parts)
        pred = sum(p[2] * p[3] for p in parts) / total_w
        known_notes = sum(1 for p in parts if p[1] == "note")
        coverage = known_notes / note_total if note_total else 0.0
        # Little overlap -> pull toward "no idea" (50%)
        certainty = min(1.0, total_w / 2.0)
        match = round(50 + 50 * max(-1.0, min(1.0, pred / 1.5)) * certainty)
        pos = sorted((p for p in parts if p[2] > 0.15), key=lambda p: -p[2] * p[3])
        neg = sorted((p for p in parts if p[2] < -0.15), key=lambda p: p[2] * p[3])
        return {
            "match": match,
            "score": round(pred, 3),
            "coverage": round(coverage, 2),
            "certainty": round(certainty, 2),
            "verdict": _verdict(match, certainty),
            "pros": [{"label": p[0], "kind": p[1], "affinity": p[2]} for p in pos[:4]],
            "cons": [{"label": p[0], "kind": p[1], "affinity": p[2]} for p in neg[:4]],
        }


def _verdict(match: int, certainty: float) -> str:
    if certainty < 0.35:
        return "Not enough overlap with your collection to tell"
    if match >= 78:
        return "Very likely your style"
    if match >= 62:
        return "Probably a good fit"
    if match >= 45:
        return "Could go either way"
    if match >= 30:
        return "Probably not for you"
    return "Has a lot of what you dislike"


# ---------------------------------------------------------------- report


def _group_stats(scored: list[tuple[dict, float]], fragrances: list[dict], field: str, min_count: int = 1):
    counts = Counter()
    sums: dict[str, list[float]] = defaultdict(list)
    for f in fragrances:
        values = f.get(field)
        for v in values if isinstance(values, list) else [values]:
            if v:
                counts[v] += 1
    for f, s in scored:
        values = f.get(field)
        for v in values if isinstance(values, list) else [values]:
            if v:
                sums[v].append(s)
    rows = [
        {"label": k, "count": c, "rated": len(sums[k]),
         "avg_score": round(mean(sums[k]), 2) if sums[k] else None}
        for k, c in counts.items() if c >= min_count
    ]
    rows.sort(key=lambda r: (-(r["avg_score"] if r["avg_score"] is not None else -9), -r["count"]))
    return rows


def _join(words: list[str]) -> str:
    words = [w.lower() for w in words]
    if len(words) <= 1:
        return "".join(words)
    return ", ".join(words[:-1]) + " and " + words[-1]


def _summary(model: TasteModel, liked_acc, disliked_acc, liked_notes, disliked_notes, families) -> list[str]:
    if not model.ready:
        need = MIN_SCORED - len(model.scored)
        return [f"Rate or log wears for {need} more fragrance{'s' if need != 1 else ''} to unlock your scent profile."]
    lines = []
    if liked_acc:
        lines.append(f"Your taste leans {_join([a['label'] for a in liked_acc[:3]])}.")
    if liked_notes:
        lines.append(f"The notes you keep coming back to: {_join([n['label'] for n in liked_notes[:5]])}.")
    loved_families = [f for f in families if f["avg_score"] is not None and f["avg_score"] >= 1 and f["rated"] >= 2]
    if loved_families:
        lines.append(f"{loved_families[0]['label']} fragrances are your safest bet.")
    dislikes = [a["label"] for a in disliked_acc[:2]] + [n["label"] for n in disliked_notes[:3]]
    if dislikes:
        lines.append(f"You tend to steer away from {_join(dislikes)}.")
    if not lines:
        lines.append("No strong patterns yet — your ratings are fairly even across notes. Keep rating!")
    return lines


def build_report(fragrances: list[dict], wears: list[dict], today: Optional[date] = None) -> dict:
    model = TasteModel(fragrances, wears, today)
    today = model.today
    by_id = {f["id"]: f for f in fragrances}

    liked_notes = _ranked(model.notes, True, 15)
    disliked_notes = _ranked(model.notes, False, 15)
    liked_acc = _ranked(model.accords, True, 10)
    disliked_acc = _ranked(model.accords, False, 10)

    polarizing = []
    for a in model.notes.values():
        scores = [x["score"] for x in a["fragrances"]]
        if len(scores) >= 2 and max(scores) >= 1 and min(scores) <= -1:
            polarizing.append(_public(a))
    polarizing.sort(key=lambda a: -a["count"])

    families = _group_stats(model.scored, fragrances, "family")
    brands = _group_stats(model.scored, fragrances, "brand")
    perfumers = _group_stats(model.scored, fragrances, "perfumers", min_count=2)

    # --- wear analytics
    month_keys = []
    y, m = today.year, today.month
    for _ in range(12):
        month_keys.append(f"{y:04d}-{m:02d}")
        y, m = (y, m - 1) if m > 1 else (y - 1, 12)
    month_keys.reverse()
    monthly = Counter(str(w["worn_on"])[:7] for w in wears)
    wear_counts = Counter(w["fragrance_id"] for w in wears)
    compliments = Counter()
    for w in wears:
        compliments[w["fragrance_id"]] += w.get("compliments") or 0

    def _brief(fid: int) -> dict:
        f = by_id[fid]
        return {"id": fid, "name": f["name"], "brand": f.get("brand")}

    most_worn = [{**_brief(fid), "count": c, "last_worn": by_id[fid].get("last_worn")}
                 for fid, c in wear_counts.most_common(10) if fid in by_id]
    complimented = [{**_brief(fid), "compliments": c} for fid, c in compliments.most_common(8) if c > 0 and fid in by_id]

    cutoff = today - timedelta(days=90)
    neglected = []
    for f in fragrances:
        if f.get("status") not in OWNED_STATUSES:
            continue
        last = f.get("last_worn")
        if last is None or _as_date(last) < cutoff:
            neglected.append({"id": f["id"], "name": f["name"], "brand": f.get("brand"), "last_worn": last,
                              "sentiment": f.get("sentiment")})
    neglected.sort(key=lambda n: (n["last_worn"] is not None, n["last_worn"] or "", -(n["sentiment"] or 0)))

    seasonal: dict[str, Counter] = defaultdict(Counter)
    season_wears = Counter()
    for w in wears:
        f = by_id.get(w["fragrance_id"])
        if not f:
            continue
        s = season_of(_as_date(w["worn_on"]))
        season_wears[s] += 1
        for a in f.get("accords") or []:
            seasonal[s][a["name"]] += float(a.get("strength") or 1.0)
    seasons = [
        {"season": s, "wears": season_wears[s],
         "accords": [{"label": k, "weight": round(v / season_wears[s], 2)} for k, v in seasonal[s].most_common(5)]}
        for s in ("spring", "summer", "fall", "winter") if season_wears[s]
    ]

    occasions: dict[str, Counter] = defaultdict(Counter)
    for w in wears:
        if w.get("occasion") and w["fragrance_id"] in by_id:
            occasions[w["occasion"]][w["fragrance_id"]] += 1
    occasion_rows = [
        {"occasion": occ, "wears": sum(c.values()),
         "top": [{**_brief(fid), "count": n} for fid, n in c.most_common(3)]}
        for occ, c in sorted(occasions.items(), key=lambda kv: -sum(kv[1].values()))
    ]

    status_counts = Counter(f.get("status") for f in fragrances)
    sentiment_counts = Counter(SENTIMENT_LABELS[f["sentiment"]] for f in fragrances if f.get("sentiment") is not None)
    last30 = sum(1 for w in wears if (today - _as_date(w["worn_on"])).days < 30)
    unique90 = len({w["fragrance_id"] for w in wears if (today - _as_date(w["worn_on"])).days < 90})

    wishlist_predictions = []
    for f in fragrances:
        if f.get("status") == "wishlist":
            p = model.predict(f)
            if p:
                wishlist_predictions.append({"id": f["id"], "name": f["name"], "brand": f.get("brand"), **p})
    wishlist_predictions.sort(key=lambda p: -p["match"])

    return {
        "ready": model.ready,
        "scored_count": len(model.scored),
        "summary": _summary(model, liked_acc, disliked_acc, liked_notes, disliked_notes, families),
        "totals": {
            "fragrances": len(fragrances),
            "by_status": dict(status_counts),
            "by_sentiment": dict(sentiment_counts),
            "wears": len(wears),
            "wears_last_30_days": last30,
            "unique_worn_last_90_days": unique90,
        },
        "liked_notes": liked_notes,
        "disliked_notes": disliked_notes,
        "liked_accords": liked_acc,
        "disliked_accords": disliked_acc,
        "polarizing_notes": polarizing[:10],
        "families": families[:12],
        "brands": brands[:12],
        "perfumers": perfumers[:10],
        "monthly_wears": [{"month": k, "count": monthly.get(k, 0)} for k in month_keys],
        "most_worn": most_worn,
        "most_complimented": complimented,
        "neglected": neglected[:12],
        "seasons": seasons,
        "occasions": occasion_rows,
        "wishlist_predictions": wishlist_predictions,
    }
