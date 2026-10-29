"""Sillage — a fragrance collection tracker that learns what you like."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Iterator, Optional

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import ai_profile, claude_client, db, lookup
from .insights import TasteModel, build_report
from .models import FragranceCreate, FragranceUpdate, LookupRequest, LookupResult, QuickAdd, WearCreate

STATIC = Path(__file__).parent / "static"

app = FastAPI(title="Sillage", version="1.0")
app.mount("/static", StaticFiles(directory=STATIC), name="static")


def get_db() -> Iterator[sqlite3.Connection]:
    conn = db.connect()
    try:
        yield conn
    finally:
        conn.close()


def _model(conn: sqlite3.Connection) -> TasteModel:
    return TasteModel(db.list_fragrances(conn), db.list_wears(conn, limit=100000))


def _get_or_404(conn: sqlite3.Connection, fid: int) -> dict:
    item = db.get_fragrance(conn, fid)
    if not item:
        raise HTTPException(404, "Fragrance not found")
    return item


def _do_lookup(query: str) -> LookupResult:
    try:
        return lookup.lookup(query)
    except lookup.LookupFailed as exc:
        raise HTTPException(502, {"message": "Couldn't find that fragrance online", "errors": exc.errors})


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/api/status")
def status():
    return {
        "ai_available": claude_client.available(),
        "model": claude_client.MODEL,
        "lookup_order": lookup._order(),
        "database": str(db.db_path()),
    }


# ---------------------------------------------------------------- lookup


@app.post("/api/lookup", response_model=LookupResult)
def lookup_fragrance(req: LookupRequest, conn: sqlite3.Connection = Depends(get_db)):
    result = _do_lookup(req.query)
    result.prediction = _model(conn).predict(result.details.model_dump())
    result.duplicate_of = db.find_duplicate(conn, result.details.brand, result.details.name)
    return result


# ---------------------------------------------------------------- fragrances


@app.get("/api/fragrances")
def list_fragrances(conn: sqlite3.Connection = Depends(get_db)):
    items = db.list_fragrances(conn)
    model = TasteModel(items, db.list_wears(conn, limit=100000))
    for item in items:
        item["score"] = model.scores.get(item["id"])
        item["prediction"] = model.predict(item) if item["status"] == "wishlist" else None
    return items


@app.post("/api/fragrances", status_code=201)
def create_fragrance(body: FragranceCreate, conn: sqlite3.Connection = Depends(get_db)):
    if not body.allow_duplicate and (dup := db.find_duplicate(conn, body.brand, body.name)):
        raise HTTPException(409, {"message": "Already in your collection", "id": dup})
    data = body.model_dump(exclude={"allow_duplicate"})
    data["source"] = data.get("source") or "manual"
    fid = db.insert_fragrance(conn, data)
    return db.get_fragrance(conn, fid)


@app.post("/api/fragrances/quick", status_code=201)
def quick_add(body: QuickAdd, conn: sqlite3.Connection = Depends(get_db)):
    """Look up + save in one step (used by bulk import)."""
    result = _do_lookup(body.query)
    d = result.details
    if dup := db.find_duplicate(conn, d.brand, d.name):
        raise HTTPException(409, {"message": f"{d.brand} {d.name} is already in your collection", "id": dup})
    fid = db.insert_fragrance(conn, {**d.model_dump(), **body.model_dump(exclude={"query"})})
    return {**db.get_fragrance(conn, fid), "lookup_message": result.message}


@app.get("/api/fragrances/{fid}")
def get_fragrance(fid: int, conn: sqlite3.Connection = Depends(get_db)):
    item = _get_or_404(conn, fid)
    model = _model(conn)
    item["score"] = model.scores.get(fid)
    item["prediction"] = model.predict(item)
    item["note_affinity"] = model.note_affinity(item)
    item["wears"] = db.list_wears(conn, fragrance_id=fid)
    return item


@app.patch("/api/fragrances/{fid}")
def update_fragrance(fid: int, body: FragranceUpdate, conn: sqlite3.Connection = Depends(get_db)):
    _get_or_404(conn, fid)
    data = body.model_dump(exclude_unset=True)
    if data.get("name") == "":
        raise HTTPException(422, "Name can't be empty")
    db.update_fragrance(conn, fid, data)
    return db.get_fragrance(conn, fid)


@app.post("/api/fragrances/{fid}/refresh")
def refresh_fragrance(fid: int, conn: sqlite3.Connection = Depends(get_db)):
    """Re-fetch notes/accords from the web, keeping your personal fields."""
    item = _get_or_404(conn, fid)
    url = item.get("source_url") or ""
    query = url if lookup.fragrantica.canonical_url(url) else f"{item['brand']} {item['name']}".strip()
    details = _do_lookup(query).details
    db.update_fragrance(conn, fid, details.model_dump())
    return db.get_fragrance(conn, fid)


@app.delete("/api/fragrances/{fid}", status_code=204)
def delete_fragrance(fid: int, conn: sqlite3.Connection = Depends(get_db)):
    if not db.delete_fragrance(conn, fid):
        raise HTTPException(404, "Fragrance not found")


# ---------------------------------------------------------------- wears


@app.get("/api/wears")
def list_wears(fragrance_id: Optional[int] = None, limit: int = 500, conn: sqlite3.Connection = Depends(get_db)):
    return db.list_wears(conn, fragrance_id=fragrance_id, limit=min(limit, 5000))


@app.post("/api/wears", status_code=201)
def create_wear(body: WearCreate, conn: sqlite3.Connection = Depends(get_db)):
    _get_or_404(conn, body.fragrance_id)
    wid = db.insert_wear(conn, body.model_dump())
    return next(w for w in db.list_wears(conn, fragrance_id=body.fragrance_id) if w["id"] == wid)


@app.delete("/api/wears/{wid}", status_code=204)
def delete_wear(wid: int, conn: sqlite3.Connection = Depends(get_db)):
    if not db.delete_wear(conn, wid):
        raise HTTPException(404, "Wear not found")


# ---------------------------------------------------------------- insights


@app.get("/api/insights")
def insights(conn: sqlite3.Connection = Depends(get_db)):
    return build_report(db.list_fragrances(conn), db.list_wears(conn, limit=100000))


@app.get("/api/insights/ai")
def latest_ai_profile(conn: sqlite3.Connection = Depends(get_db)):
    return db.latest_ai_report(conn)


@app.post("/api/insights/ai")
def generate_ai_profile(conn: sqlite3.Connection = Depends(get_db)):
    if not claude_client.available():
        raise HTTPException(400, "Set ANTHROPIC_API_KEY to enable the AI scent profile")
    fragrances = db.list_fragrances(conn)
    report = build_report(fragrances, db.list_wears(conn, limit=100000))
    if not report["ready"]:
        raise HTTPException(400, "Rate a few more fragrances first")
    try:
        payload = ai_profile.generate(report, fragrances)
    except Exception as exc:  # surfaced to the UI; the statistical report still works
        raise HTTPException(502, f"AI profile failed: {exc}")
    db.save_ai_report(conn, payload)
    return db.latest_ai_report(conn)


@app.get("/api/export")
def export(conn: sqlite3.Connection = Depends(get_db)):
    return {"fragrances": db.list_fragrances(conn), "wears": db.list_wears(conn, limit=1000000)}
