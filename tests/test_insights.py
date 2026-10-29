from datetime import date

from app.insights import TasteModel, build_report, fragrance_score

TODAY = date(2026, 10, 29)


def frag(fid, name, sentiment, accords=(), top=(), mid=(), base=(), status="owned", family=None, last_worn=None):
    return {
        "id": fid, "name": name, "brand": "Brand", "status": status, "sentiment": sentiment, "family": family,
        "accords": [{"name": a, "strength": 1.0} for a in accords], "top_notes": list(top),
        "middle_notes": list(mid), "base_notes": list(base), "notes": [], "perfumers": [],
        "last_worn": last_worn, "wear_count": 0,
    }


def collection():
    return [
        frag(1, "Vanilla Bomb", 2, ["vanilla", "sweet"], ["Bergamot"], ["Tonka Bean"], ["Madagascar Vanilla"], family="Amber Vanilla"),
        frag(2, "Tonka Dream", 2, ["sweet", "warm spicy"], ["Cardamom"], ["Tonka Bean"], ["Vanilla", "Amber"], family="Amber Vanilla"),
        frag(3, "Ocean Spray", -2, ["aquatic", "marine"], ["Sea Notes"], ["Calone"], ["Musk"], family="Aquatic"),
        frag(4, "Salt Breeze", -1, ["aquatic", "fresh"], ["Sea Salt", "Calabrian bergamot"], ["Calone"], ["Ambroxan"]),
        frag(5, "Wish Gourmand", None, ["vanilla", "sweet"], [], ["Tonka Bean"], ["Vanilla"], status="wishlist"),
        frag(6, "Wish Marine", None, ["aquatic"], ["Sea Notes"], ["Calone"], [], status="wishlist"),
    ]


def test_fragrance_score_blends_rating_and_wears():
    f = {"status": "owned", "sentiment": 1}
    assert fragrance_score(f, [], TODAY) == 1.0
    wears = [{"worn_on": "2026-10-01", "enjoyment": 2}] * 3
    s = fragrance_score(f, wears, TODAY)
    assert s > 1.3 + 0.6  # blended rating + wear bonus
    assert fragrance_score({"status": "wishlist", "sentiment": 2}, [], TODAY) is None
    assert fragrance_score({"status": "owned", "sentiment": None}, [], TODAY) is None
    assert fragrance_score({"status": "owned", "sentiment": -2}, wears[:1], TODAY) < 0  # no bonus for hated


def test_affinities_find_liked_and_disliked_notes():
    model = TasteModel(collection(), [], TODAY)
    assert model.ready
    assert model.notes["tonka bean"]["affinity"] > model.notes["bergamot"]["affinity"]
    # "Madagascar Vanilla" and "Vanilla" are grouped
    assert model.notes["vanilla"]["count"] == 2
    assert model.notes["calone"]["affinity"] < -0.5
    assert model.accords["aquatic"]["affinity"] < 0 < model.accords["vanilla"]["affinity"]


def test_predictions_rank_wishlist():
    model = TasteModel(collection(), [], TODAY)
    good = model.predict(collection()[4])
    bad = model.predict(collection()[5])
    assert good["match"] > 70 and bad["match"] < 40
    assert any(p["label"].lower() == "tonka bean" for p in good["pros"])
    assert any(c["label"].lower() == "calone" for c in bad["cons"])
    assert model.predict(frag(9, "Unknown", None, ["leather"], ["Birch Tar"])) is None


def test_build_report_shapes():
    wears = [
        {"id": 1, "fragrance_id": 1, "worn_on": "2026-10-20", "enjoyment": 2, "compliments": 2, "occasion": "Date"},
        {"id": 2, "fragrance_id": 1, "worn_on": "2026-01-10", "enjoyment": None, "compliments": 0, "occasion": "Work"},
        {"id": 3, "fragrance_id": 2, "worn_on": "2026-07-04", "enjoyment": 1, "compliments": 1, "occasion": "Work"},
    ]
    items = collection()
    items[0]["last_worn"] = "2026-10-20"
    report = build_report(items, wears, TODAY)
    assert report["ready"]
    assert report["liked_notes"][0]["label"].lower() in {"tonka bean", "vanilla"}
    assert {n["key"] for n in report["disliked_notes"]} >= {"calone"}
    assert report["most_worn"][0]["id"] == 1
    assert report["most_complimented"][0]["compliments"] == 2
    assert report["monthly_wears"][-1] == {"month": "2026-10", "count": 1}
    assert len(report["monthly_wears"]) == 12
    assert {s["season"] for s in report["seasons"]} == {"fall", "winter", "summer"}
    assert report["occasions"][0]["occasion"] == "Work"
    assert [w["name"] for w in report["wishlist_predictions"]] == ["Wish Gourmand", "Wish Marine"]
    assert any(n["id"] == 3 for n in report["neglected"])  # never worn
    assert all(n["id"] != 1 for n in report["neglected"])  # worn recently
    assert any("aquatic" in line for line in report["summary"])


def test_report_not_ready():
    report = build_report(collection()[:1], [], TODAY)
    assert not report["ready"]
    assert "2 more" in report["summary"][0]


def test_suggest_wear_respects_weather():
    from app.recommend import similar, suggest_wear

    frags = [
        frag(1, "Gourmand Love", 2, ["vanilla", "sweet", "amber"], ["Cinnamon"], ["Tonka Bean"], ["Vanilla"]),
        frag(2, "Citrus Like", 1, ["citrus", "fresh", "aromatic"], ["Bergamot", "Lemon"], ["Neroli"], ["Musk"]),
        frag(3, "Hated", -2, ["aquatic"], ["Calone"]),
        frag(4, "Wish", None, ["citrus"], status="wishlist"),
    ]
    hot = [p["name"] for p in suggest_wear(frags, [], weather="hot", today=TODAY)]
    cold = [p["name"] for p in suggest_wear(frags, [], weather="cold", today=TODAY)]
    assert hot[0] == "Citrus Like" and cold[0] == "Gourmand Love"
    assert "Hated" not in hot and "Wish" not in hot  # disliked and unowned never suggested
    sims = similar(frags[3], frags)  # a citrus wishlist item is closest to the citrus fragrance
    assert sims[0]["name"] == "Citrus Like" and sims[0]["shared"] == ["citrus"]
    assert all(s["name"] != "Wish" for s in sims)
