import pytest
from fastapi.testclient import TestClient

from app import lookup as lookup_pkg
from app.lookup import claude as claude_provider
from app.lookup import fragrantica
from app.models import Accord, Alternative, FragranceDetails


def make(name, brand, accords, top=(), mid=(), base=()):
    return FragranceDetails(
        name=name, brand=brand, accords=[Accord(name=a, strength=1.0) for a in accords],
        top_notes=list(top), middle_notes=list(mid), base_notes=list(base),
        source="fragrantica", source_url=f"https://www.fragrantica.com/perfume/{brand}/{name}-1.html",
    )


CATALOG = {
    "dior sauvage": make("Sauvage", "Dior", ["fresh spicy", "amber"], ["Bergamot"], ["Lavender"], ["Ambroxan"]),
    "tobacco vanille": make("Tobacco Vanille", "Tom Ford", ["tobacco", "vanilla"], ["Tobacco Leaf"], ["Tonka Bean"], ["Vanilla"]),
    "acqua di gio": make("Acqua di Gio", "Giorgio Armani", ["aquatic", "citrus"], ["Sea Notes"], ["Calone"], ["Musk"]),
    "vanilla wish": make("Vanilla Wish", "Indie", ["vanilla"], [], ["Tonka Bean"], ["Vanilla"]),
}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("SILLAGE_DB", str(tmp_path / "test.db"))
    monkeypatch.setenv("SILLAGE_AI", "off")

    def fake_lookup(query):
        if query.lower() in CATALOG:
            return CATALOG[query.lower()], [Alternative(label="Other", url="https://www.fragrantica.com/perfume/X/Y-2.html")]
        raise fragrantica.FragranticaError("No Fragrantica page found")

    monkeypatch.setattr(fragrantica, "lookup", fake_lookup)
    from app.main import app

    return TestClient(app)


def test_lookup_preview_and_save_flow(client):
    r = client.post("/api/lookup", json={"query": "Dior Sauvage"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["details"]["name"] == "Sauvage" and body["duplicate_of"] is None
    assert body["alternatives"][0]["label"] == "Other"

    r = client.post("/api/fragrances", json={**body["details"], "status": "owned", "sentiment": 2})
    assert r.status_code == 201
    saved = r.json()
    assert saved["base_notes"] == ["Ambroxan"] and saved["accords"][0]["name"] == "fresh spicy"

    # duplicate detection
    assert client.post("/api/fragrances", json={**body["details"]}).status_code == 409
    assert client.post("/api/lookup", json={"query": "Dior Sauvage"}).json()["duplicate_of"] == saved["id"]


def test_lookup_failure_reports_providers(client):
    r = client.post("/api/lookup", json={"query": "nonexistent perfume"})
    assert r.status_code == 502
    errors = r.json()["detail"]["errors"]
    assert any(e.startswith("Fragrantica") for e in errors) and any(e.startswith("Claude") for e in errors)


def test_claude_fallback(client, monkeypatch):
    monkeypatch.setenv("SILLAGE_AI", "on")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    monkeypatch.setattr(
        claude_provider, "lookup", lambda q: (make("Aventus", "Creed", ["fruity"], ["Pineapple"]), [], "Interpreted as Creed Aventus")
    )
    r = client.post("/api/lookup", json={"query": "aventus creed"})
    assert r.status_code == 200
    assert r.json()["details"]["brand"] == "Creed"
    assert r.json()["message"] == "Interpreted as Creed Aventus"


def test_weak_fragrantica_match_defers_to_claude(client, monkeypatch):
    monkeypatch.setenv("SILLAGE_AI", "on")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    monkeypatch.setattr(fragrantica, "lookup", lambda q: (CATALOG["dior sauvage"], []))
    monkeypatch.setattr(claude_provider, "lookup", lambda q: (make("Aventus", "Creed", ["fruity"], ["Pineapple"]), [], None))
    assert lookup_pkg.lookup("creed aventus").details.name == "Aventus"
    assert lookup_pkg.lookup("dior sauvage").details.name == "Sauvage"


def test_wears_insights_and_predictions(client):
    ids = {}
    for q, status, sentiment in [
        ("Dior Sauvage", "owned", 1), ("Tobacco Vanille", "owned", 2),
        ("Acqua di Gio", "tried", -2), ("Vanilla Wish", "wishlist", None),
    ]:
        r = client.post("/api/fragrances/quick", json={"query": q, "status": status, "sentiment": sentiment})
        assert r.status_code == 201, r.text
        ids[q] = r.json()["id"]
    assert client.post("/api/fragrances/quick", json={"query": "Dior Sauvage"}).status_code == 409

    for day in ("2026-10-01", "2026-10-05", "2026-10-09"):
        r = client.post("/api/wears", json={"fragrance_id": ids["Tobacco Vanille"], "worn_on": day,
                                            "occasion": "Date", "enjoyment": 2, "compliments": 1})
        assert r.status_code == 201
    wear_id = r.json()["id"]

    item = client.get(f"/api/fragrances/{ids['Tobacco Vanille']}").json()
    assert item["wear_count"] == 3 and len(item["wears"]) == 3 and item["last_worn"] == "2026-10-09"

    listing = client.get("/api/fragrances").json()
    wish = next(f for f in listing if f["status"] == "wishlist")
    assert wish["prediction"]["match"] > 60

    report = client.get("/api/insights").json()
    assert report["ready"]
    assert report["liked_notes"][0]["label"] in {"Tonka Bean", "Vanilla", "Tobacco Leaf"}
    assert report["disliked_accords"][0]["label"] in {"Aquatic", "Citrus"}

    assert client.delete(f"/api/wears/{wear_id}").status_code == 204
    assert client.get(f"/api/fragrances/{ids['Tobacco Vanille']}").json()["wear_count"] == 2


def test_update_and_delete(client):
    fid = client.post("/api/fragrances/quick", json={"query": "Dior Sauvage"}).json()["id"]
    r = client.patch(f"/api/fragrances/{fid}", json={"sentiment": -1, "base_notes": ["Ambroxan", "Cedar"]})
    assert r.json()["sentiment"] == -1 and r.json()["base_notes"] == ["Ambroxan", "Cedar"]
    assert r.json()["top_notes"] == ["Bergamot"]  # untouched layers stay
    r = client.patch(f"/api/fragrances/{fid}", json={"sentiment": None})
    assert r.json()["sentiment"] is None
    assert client.delete(f"/api/fragrances/{fid}").status_code == 204
    assert client.get(f"/api/fragrances/{fid}").status_code == 404


def test_manual_entry(client):
    r = client.post("/api/fragrances", json={"name": "Grandma's Rose", "brand": "", "middle_notes": ["Rose"], "sentiment": 1})
    assert r.status_code == 201 and r.json()["source"] == "manual"
