import asyncio
import json
import socket
import threading
import time
from types import SimpleNamespace

import httpx2
import pytest
import uvicorn
from fastapi.testclient import TestClient

from app import assistant, db
from app.lookup import fragrantica
from app.models import Accord, FragranceDetails

KEY = {"Authorization": "Bearer test-key"}


def details(name, brand, accords, top=(), mid=(), base=()):
    return FragranceDetails(name=name, brand=brand, accords=[Accord(name=a, strength=1.0) for a in accords],
                            top_notes=list(top), middle_notes=list(mid), base_notes=list(base), source="fragrantica")


CATALOG = {
    "tobacco vanille": details("Tobacco Vanille", "Tom Ford", ["tobacco", "vanilla"], ["Tobacco Leaf"], ["Tonka Bean"], ["Vanilla"]),
    "grand soir": details("Grand Soir", "MFK", ["amber", "vanilla"], ["Labdanum"], ["Benzoin"], ["Tonka Bean", "Vanilla"]),
    "cool water": details("Cool Water", "Davidoff", ["aquatic", "fresh"], ["Sea Water", "Mint"], ["Calone"], ["Musk"]),
    "layton": details("Layton", "Parfums de Marly", ["warm spicy", "vanilla"], ["Apple"], ["Lavender"], ["Vanilla"]),
}


@pytest.fixture
def seeded(tmp_path, monkeypatch):
    monkeypatch.setenv("SILLAGE_DB", str(tmp_path / "p.db"))
    monkeypatch.setenv("SILLAGE_AI", "off")
    monkeypatch.setattr(fragrantica, "lookup", lambda q: (CATALOG[q.lower()], []) if q.lower() in CATALOG
                        else (_ for _ in ()).throw(fragrantica.FragranticaError("not found")))
    from app.main import app

    with TestClient(app, headers=KEY) as c:
        for q, status, rating in [("Tobacco Vanille", "owned", 2), ("Grand Soir", "owned", 2), ("Cool Water", "tried", -2)]:
            assert c.post("/api/fragrances/quick", json={"query": q, "status": status, "sentiment": rating}).status_code == 201
        c.post("/api/wears", json={"fragrance_id": 1, "worn_on": "2026-10-01", "weather": "Cold", "occasion": "Date", "enjoyment": 2})
        yield c


def test_auth_required(seeded):
    from app.main import app

    anon = TestClient(app)
    assert anon.get("/api/fragrances").status_code == 401
    assert anon.get("/api/fragrances", headers={"Authorization": "Bearer nope"}).status_code == 401
    assert anon.get("/api/fragrances", headers={"X-API-Key": "test-key"}).status_code == 200
    assert anon.get("/api/fragrances?key=test-key").status_code == 200
    assert anon.get("/api/health").status_code == 200
    assert anon.post("/mcp/", json={}).status_code == 401
    assert "bearer" in json.dumps(anon.get("/openapi.json").json()["components"]["securitySchemes"]).lower()


def test_tools_over_rest(seeded):
    catalog = seeded.get("/api/tools").json()
    names = {t["name"] for t in catalog}
    assert {"taste_profile", "query_database", "publish_insight", "evaluate_fragrance"} <= names
    r = seeded.post("/api/tools/search_fragrances", json={"note": "vanilla"}).json()
    assert {x["name"] for x in r["results"]} == {"Tobacco Vanille", "Grand Soir"}
    r = seeded.post("/api/tools/evaluate_fragrance", json={"name": "Layton"}).json()
    assert r["predicted_match"]["match"] > 60 and r["already_in_collection"] is None
    assert seeded.post("/api/tools/nope", json={}).status_code == 404
    bad = seeded.post("/api/tools/log_wear", json={"fragrance_id": "x"})
    assert bad.status_code == 400 and bad.json()["detail"]["error"] == "Invalid arguments"
    assert seeded.post("/api/tools/add_fragrance", json={"name": "Grand Soir"}).status_code == 400  # duplicate


def test_sql(seeded):
    r = seeded.post("/api/sql", json={"sql": "SELECT note_key(note) k, AVG(rating) r FROM v_notes GROUP BY k ORDER BY r DESC"}).json()
    assert r["columns"] == ["k", "r"] and r["rows"][0][1] == 2
    assert seeded.post("/api/sql", json={"sql": "UPDATE fragrances SET sentiment = 0"}).status_code == 400
    assert seeded.post("/api/sql", json={"sql": "SELECT 1; DROP TABLE wears"}).status_code == 400
    assert seeded.post("/api/sql", json={"sql": "SELECT * FROM v_wears"}).json()["row_count"] == 1


def test_feed_roundtrip(seeded):
    body = {"title": "Vanilla is your thing", "summary": "Every fragrance you love has vanilla.",
            "chart": {"type": "diverging", "labels": ["Vanilla", "Calone"], "values": [2, -2]}, "fragrance_ids": [1, 2]}
    item = seeded.post("/api/feed", json=body, headers={"X-Client-Name": "ChatGPT"}).json()
    assert item["source"] == "ChatGPT" and item["chart"]["type"] == "diverging"
    assert seeded.post("/api/feed", json={"title": "x", "summary": "y", "chart": {"type": "bar", "labels": ["a"], "values": []}}).status_code == 400
    seeded.post("/api/feed", json={"title": "Second", "summary": "Later one"})
    assert seeded.patch(f"/api/feed/{item['id']}", json={"pinned": True}).json()["pinned"]
    assert seeded.get("/api/feed").json()[0]["id"] == item["id"]  # pinned first
    assert seeded.delete(f"/api/feed/{item['id']}").status_code == 204
    assert len(seeded.get("/api/feed").json()) == 1


def test_connect_info(seeded):
    info = seeded.get("/api/connect").json()
    assert info["api_key"] == "test-key" and info["mcp_url"].endswith("/mcp")


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_mcp_end_to_end(seeded):
    """A real MCP client over Streamable HTTP against a live server."""
    from mcp import Client

    from app.main import app

    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    while not server.started:
        time.sleep(0.05)

    async def scenario():
        async with httpx2.AsyncClient(headers=KEY, timeout=30) as http:
            from mcp.client.streamable_http import streamable_http_client

            async with Client(streamable_http_client(f"http://127.0.0.1:{port}/mcp/", http_client=http)) as client:
                listed = await client.list_tools()
                names = {t.name for t in listed.tools}
                profile = await client.call_tool("taste_profile", {})
                pub = await client.call_tool("publish_insight", {"title": "From MCP", "summary": "Hello from an MCP client"})
                bad = await client.call_tool("get_fragrance", {"fragrance_id": 999})
                resources = await client.list_resources()
                return names, listed, profile, pub, bad, resources

    try:
        names, listed, profile, pub, bad, resources = asyncio.run(scenario())
    finally:
        server.should_exit = True
        thread.join(5)
    assert {"collection_overview", "query_database", "publish_insight"} <= names
    ro = {t.name: t.annotations.read_only_hint for t in listed.tools}
    assert ro["query_database"] is True and ro["log_wear"] is False
    text = json.dumps(profile.model_dump(), default=str)
    assert "Vanilla" in text
    assert not pub.is_error
    assert bad.is_error
    assert any(str(r.uri) == "sillage://schema" for r in resources.resources)
    feed = seeded.get("/api/feed").json()
    assert feed[0]["title"] == "From MCP" and feed[0]["source"] == "MCP client"


# ---------------------------------------------------------------- assistant loop with a fake model


class FakeStream:
    def __init__(self, final, texts=()):
        self.final, self.texts = final, texts

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def __iter__(self):
        return iter([SimpleNamespace(type="text", text=t) for t in self.texts])

    def get_final_message(self):
        return self.final


def test_assistant_runs_tools_and_streams(seeded, monkeypatch):
    calls = []
    turns = [
        FakeStream(SimpleNamespace(stop_reason="tool_use", content=[
            SimpleNamespace(type="tool_use", id="t1", name="search_fragrances", input={"note": "vanilla"}),
            SimpleNamespace(type="tool_use", id="t2", name="publish_insight", input={"title": "Vanilla", "summary": "You love it"}),
        ])),
        FakeStream(SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text="You love vanilla.")]),
                   texts=["You love ", "vanilla."]),
    ]

    def stream(**kwargs):
        calls.append(kwargs)
        return turns.pop(0)

    monkeypatch.setattr(assistant, "client", lambda: SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(stream=stream))))
    events = list(assistant.run([{"role": "user", "content": "What do I like?"}]))
    kinds = [e["type"] for e in events]
    assert kinds == ["tool", "tool_result", "tool", "tool_result", "text", "text", "done"]
    assert all(e["ok"] for e in events if e["type"] == "tool_result")
    history = calls[1]["messages"]  # the loop's live list: user, assistant(tool_use), user(tool_results), ...
    assert history[2]["role"] == "user" and {r["tool_use_id"] for r in history[2]["content"]} == {"t1", "t2"}
    assert all(t.get("eager_input_streaming") for t in calls[0]["tools"])
    assert calls[0]["fallbacks"] == "default"
    assert db.list_insights(db.connect())[0]["source"] == "Ask Sillage"


def test_assistant_stops_on_truncated_tool_call(seeded, monkeypatch):
    final = SimpleNamespace(stop_reason="max_tokens", content=[SimpleNamespace(type="tool_use", id="t", name="log_wear", input={"fragrance_id": 1})])
    monkeypatch.setattr(assistant, "client", lambda: SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(stream=lambda **k: FakeStream(final)))))
    events = list(assistant.run([{"role": "user", "content": "log it"}]))
    assert events[-1]["type"] == "error"
    assert len(seeded.get("/api/wears").json()) == 1  # the truncated call never ran


def test_chat_endpoint_requires_ai(seeded):
    assert seeded.post("/api/chat", json={"messages": [{"role": "user", "content": "hi"}]}).status_code == 400


# ---------------------------------------------------------------- images

PNG = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
       b"\x00\x00\x00\rIDATx\x9cc\xf8\xff\xff?\x00\x05\xfe\x02\xfe\xa7\x35\x81\x84\x00\x00\x00\x00IEND\xaeB`\x82")


def test_image_from_fragrantica_id():
    assert fragrantica.image_for("https://www.fragrantica.com/perfume/Dior/Sauvage-31861.html") == \
        "https://fimgs.net/mdimg/perfume/375x500.31861.jpg"
    assert fragrantica.image_for(None) is None and fragrantica.image_for("https://example.com") is None


def test_lookup_always_sets_image(monkeypatch):
    from app import lookup as lookup_pkg

    d = details("Aventus", "Creed", ["fruity"], ["Pineapple"])
    d.source_url = "https://www.fragrantica.com/perfume/Creed/Aventus-9828.html"
    monkeypatch.setattr(fragrantica, "lookup", lambda q: (d, []))
    assert lookup_pkg.lookup("creed aventus").details.image_url.endswith("375x500.9828.jpg")


def test_photo_upload_and_restore(seeded):
    import base64

    fid = 1
    seeded.patch(f"/api/fragrances/{fid}", json={"source_url": "https://www.fragrantica.com/perfume/Tom-Ford/Tobacco-Vanille-1825.html"})
    url = "data:image/png;base64," + base64.b64encode(PNG).decode()
    item = seeded.post(f"/api/fragrances/{fid}/photo", json={"data_url": url}).json()
    assert item["image_url"].startswith("/media/")
    served = seeded.get(item["image_url"], headers={"Authorization": ""})
    assert served.status_code == 200 and served.content == PNG  # <img> works without the key
    assert seeded.get("/media/../api_key").status_code == 404
    bad = "data:image/png;base64," + base64.b64encode(b"<svg onload=alert(1)>").decode()
    assert seeded.post(f"/api/fragrances/{fid}/photo", json={"data_url": bad}).status_code == 422
    assert seeded.post(f"/api/fragrances/{fid}/photo", json={"data_url": "data:text/html;base64,PGI+"}).status_code == 422
    restored = seeded.delete(f"/api/fragrances/{fid}/photo").json()
    assert restored["image_url"].endswith("375x500.1825.jpg")  # official image comes back
    assert seeded.get(item["image_url"]).status_code == 404  # file removed


def test_backfill_images(tmp_path):
    conn = db.connect(tmp_path / "b.db")
    db.insert_fragrance(conn, {"name": "Old", "brand": "X", "source_url": "https://www.fragrantica.com/perfume/X/Old-42.html"})
    db.insert_fragrance(conn, {"name": "Manual", "brand": "Y"})
    assert db.backfill_images(conn, fragrantica.image_for) == 1
    images = {f["name"]: f["image_url"] for f in db.list_fragrances(conn)}
    assert images == {"Old": "https://fimgs.net/mdimg/perfume/375x500.42.jpg", "Manual": None}


# ---------------------------------------------------------------- export / import / backups

HOSTED_EXPORT = {  # shape produced by the hosted claude.ai app's Export button
    "format": "sillage-export", "version": 1,
    "fragrances": [
        {"id": "insvpj2dnpgtrfm2vdb8", "name": "Hawas for Him", "brand": "Rasasi", "year": 2015, "status": "owned", "sentiment": 1,
         "accords": [{"name": "fresh spicy", "strength": 1}], "top_notes": ["Apple"], "photo_id": "c27da4edafe67d5b9344315aed151e31",
         "source_url": "https://www.fragrantica.com/perfume/Rasasi/Hawas-for-Him-46890.html", "created_at": "2026-10-29T14:46:25.381Z"},
        {"id": "ex-grand", "name": "Grand Soir", "brand": "MFK", "status": "owned", "sentiment": 2, "accords": [{"name": "amber", "strength": 1}]},
        {"id": "bad", "name": "Broken", "brand": "X", "sentiment": 7},
    ],
    "wears": [
        {"fragrance_id": "insvpj2dnpgtrfm2vdb8", "worn_on": "2026-10-30", "occasion": "Work", "enjoyment": 2, "compliments": 1},
        {"fragrance_id": "missing", "worn_on": "2026-10-30"},
    ],
    "insights": [{"title": "Fresh picks", "summary": "Hawas suits hot days.", "chart": {"type": "bar", "labels": ["Hawas"], "values": [1]},
                  "fragrance_ids": ["insvpj2dnpgtrfm2vdb8"], "source": "Claude Code", "created_at": "2026-10-29T14:30:00Z"}],
}


def test_import_merges_and_is_idempotent(seeded):
    r = seeded.post("/api/import", json=HOSTED_EXPORT).json()
    assert (r["fragrances_added"], r["fragrances_matched"], r["wears_added"], r["wears_skipped"], r["insights_added"]) == (1, 1, 1, 1, 1)
    assert len(r["errors"]) == 1 and "Broken" in r["errors"][0]
    hawas = next(f for f in seeded.get("/api/fragrances").json() if f["name"] == "Hawas for Him")
    assert hawas["image_url"].endswith("375x500.46890.jpg")  # hosted photo can't move; Fragrantica's is rebuilt
    assert hawas["created_at"] == "2026-10-29 14:46:25" and hawas["wear_count"] == 1
    feed = seeded.get("/api/feed").json()
    assert feed[0]["fragrance_ids"] == [hawas["id"]] and feed[0]["source"] == "Claude Code"
    again = seeded.post("/api/import", json=HOSTED_EXPORT).json()
    assert (again["fragrances_added"], again["wears_added"], again["insights_added"]) == (0, 0, 0)
    assert seeded.post("/api/import", json={"nope": 1}).status_code == 422


def test_export_roundtrip(seeded, tmp_path, monkeypatch):
    exported = seeded.get("/api/export").json()
    assert exported["format"] == "sillage-export" and len(exported["fragrances"]) == 3 and len(exported["wears"]) == 1
    conn = db.connect(tmp_path / "fresh.db")
    from app import portability

    report = portability.import_all(conn, exported)
    assert report["fragrances_added"] == 3 and report["wears_added"] == 1 and not report["errors"]
    assert {f["name"] for f in db.list_fragrances(conn)} == {f["name"] for f in exported["fragrances"]}


def test_backups(seeded, tmp_path):
    from app import portability

    for _ in range(50):  # the server backs up on startup (in a background task)
        if list((tmp_path / "backups").glob("sillage-*.db")):
            break
        time.sleep(0.05)
    assert list((tmp_path / "backups").glob("sillage-*.db"))
    dest = tmp_path / "manual"
    for day in ("2026-10-01", "2026-10-02", "2026-10-03"):
        portability.backup_now(dest, keep=2, stamp=day)
    files = sorted(p.name for p in dest.glob("*.db"))
    assert files == ["sillage-2026-10-02.db", "sillage-2026-10-03.db"]
    copy = db.connect(dest / files[-1])
    assert len(db.list_fragrances(copy)) == 3  # a real, consistent database
    resp = seeded.get("/api/backup")
    assert resp.status_code == 200 and resp.content[:15] == b"SQLite format 3"
