"""Sillage — a fragrance collection platform that learns what you like.

Surfaces: the web app (/), the REST API (/api, OpenAPI at /docs), and an MCP server (/mcp) so any AI app can
read the collection, run analyses and publish results back into the app. All /api and /mcp calls need the key.
"""

from __future__ import annotations

import contextlib
import json
import os
import sqlite3
from pathlib import Path
from typing import Any, Iterator, Optional

from fastapi import APIRouter, Body, Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.security import HTTPBearer
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import ai_profile, assistant, auth, claude_client, db, lookup, mcp_server, tools
from .insights import TasteModel, build_report
from .models import FragranceCreate, FragranceUpdate, LookupRequest, LookupResult, QuickAdd, WearCreate
from .recommend import similar

STATIC = Path(__file__).parent / "static"
PUBLIC_URL = os.environ.get("SILLAGE_PUBLIC_URL", "").rstrip("/")

class _MCPMount:
    """ASGI slot for the MCP app; a fresh server is built per app lifespan (its session manager runs once)."""

    app = None

    async def __call__(self, scope, receive, send):
        if self.app is None:
            from starlette.responses import PlainTextResponse

            await PlainTextResponse("MCP server not started", status_code=503)(scope, receive, send)
            return
        await self.app(scope, receive, send)


mcp_mount = _MCPMount()


@contextlib.asynccontextmanager
async def lifespan(_app: FastAPI):
    print(f"Sillage API key: {auth.api_key()}  (MCP endpoint: /mcp, API docs: /docs)", flush=True)
    server = mcp_server.build_server()
    mcp_mount.app = mcp_server.http_app(server)
    async with server.session_manager.run():
        yield
    mcp_mount.app = None


app = FastAPI(
    title="Sillage",
    version="2.0",
    description="Your fragrance collection as an API. Authenticate with `Authorization: Bearer <key>`. "
                "The same tools are available over MCP at /mcp.",
    servers=[{"url": PUBLIC_URL}] if PUBLIC_URL else None,
    lifespan=lifespan,
)
app.mount("/static", StaticFiles(directory=STATIC), name="static")
app.mount("/mcp", auth.KeyMiddleware(mcp_mount))
_bearer = HTTPBearer(auto_error=False, description="Your Sillage API key")


def _require_key(request: Request, _token=Depends(_bearer)) -> None:
    auth.require_key(request)


api = APIRouter(prefix="/api", dependencies=[Depends(_require_key)])


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


@app.get("/bootstrap", include_in_schema=False)
def bootstrap(request: Request):
    """Hands the web app its key automatically, but only to a browser on this same machine.

    Loopback client + loopback Host header: a remote visitor (or a DNS-rebinding page, whose Host header
    would be its own domain) gets nothing and must type the key instead."""
    if PUBLIC_URL or os.environ.get("SILLAGE_BOOTSTRAP", "on").lower() in ("0", "off", "false", "no"):
        return {"api_key": None}  # deployed: proxies can forge loopback addresses, so always type the key
    host = (request.headers.get("host") or "").rsplit(":", 1)[0].strip("[]")
    client = request.client.host if request.client else ""
    if client in ("127.0.0.1", "::1") and host in ("127.0.0.1", "localhost", "::1"):
        return {"api_key": auth.api_key()}
    return {"api_key": None}


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(STATIC / "index.html")


@api.get("/status")
def status():
    return {
        "ai_available": claude_client.available(),
        "model": claude_client.MODEL,
        "lookup_order": lookup._order(),
        "database": str(db.db_path()),
    }


# ---------------------------------------------------------------- lookup


@api.post("/lookup", response_model=LookupResult)
def lookup_fragrance(req: LookupRequest, conn: sqlite3.Connection = Depends(get_db)):
    result = _do_lookup(req.query)
    result.prediction = _model(conn).predict(result.details.model_dump())
    result.duplicate_of = db.find_duplicate(conn, result.details.brand, result.details.name)
    return result


# ---------------------------------------------------------------- fragrances


@api.get("/fragrances")
def list_fragrances(conn: sqlite3.Connection = Depends(get_db)):
    items = db.list_fragrances(conn)
    model = TasteModel(items, db.list_wears(conn, limit=100000))
    for item in items:
        item["score"] = model.scores.get(item["id"])
        item["prediction"] = model.predict(item) if item["status"] == "wishlist" else None
    return items


@api.post("/fragrances", status_code=201)
def create_fragrance(body: FragranceCreate, conn: sqlite3.Connection = Depends(get_db)):
    if not body.allow_duplicate and (dup := db.find_duplicate(conn, body.brand, body.name)):
        raise HTTPException(409, {"message": "Already in your collection", "id": dup})
    data = body.model_dump(exclude={"allow_duplicate"})
    data["source"] = data.get("source") or "manual"
    fid = db.insert_fragrance(conn, data)
    return db.get_fragrance(conn, fid)


@api.post("/fragrances/quick", status_code=201)
def quick_add(body: QuickAdd, conn: sqlite3.Connection = Depends(get_db)):
    """Look up + save in one step (used by bulk import)."""
    result = _do_lookup(body.query)
    d = result.details
    if dup := db.find_duplicate(conn, d.brand, d.name):
        raise HTTPException(409, {"message": f"{d.brand} {d.name} is already in your collection", "id": dup})
    fid = db.insert_fragrance(conn, {**d.model_dump(), **body.model_dump(exclude={"query"})})
    return {**db.get_fragrance(conn, fid), "lookup_message": result.message}


@api.get("/fragrances/{fid}")
def get_fragrance(fid: int, conn: sqlite3.Connection = Depends(get_db)):
    item = _get_or_404(conn, fid)
    model = _model(conn)
    item["score"] = model.scores.get(fid)
    item["prediction"] = model.predict(item)
    item["note_affinity"] = model.note_affinity(item)
    item["similar"] = similar(item, db.list_fragrances(conn), 4)
    item["wears"] = db.list_wears(conn, fragrance_id=fid)
    return item


@api.patch("/fragrances/{fid}")
def update_fragrance(fid: int, body: FragranceUpdate, conn: sqlite3.Connection = Depends(get_db)):
    _get_or_404(conn, fid)
    data = body.model_dump(exclude_unset=True)
    if data.get("name") == "":
        raise HTTPException(422, "Name can't be empty")
    db.update_fragrance(conn, fid, data)
    return db.get_fragrance(conn, fid)


@api.post("/fragrances/{fid}/refresh")
def refresh_fragrance(fid: int, conn: sqlite3.Connection = Depends(get_db)):
    """Re-fetch notes/accords from the web, keeping your personal fields."""
    item = _get_or_404(conn, fid)
    url = item.get("source_url") or ""
    query = url if lookup.fragrantica.canonical_url(url) else f"{item['brand']} {item['name']}".strip()
    details = _do_lookup(query).details
    db.update_fragrance(conn, fid, details.model_dump())
    return db.get_fragrance(conn, fid)


@api.delete("/fragrances/{fid}", status_code=204)
def delete_fragrance(fid: int, conn: sqlite3.Connection = Depends(get_db)):
    if not db.delete_fragrance(conn, fid):
        raise HTTPException(404, "Fragrance not found")


# ---------------------------------------------------------------- wears


@api.get("/wears")
def list_wears(fragrance_id: Optional[int] = None, limit: int = 500, conn: sqlite3.Connection = Depends(get_db)):
    return db.list_wears(conn, fragrance_id=fragrance_id, limit=min(limit, 5000))


@api.post("/wears", status_code=201)
def create_wear(body: WearCreate, conn: sqlite3.Connection = Depends(get_db)):
    _get_or_404(conn, body.fragrance_id)
    wid = db.insert_wear(conn, body.model_dump())
    return next(w for w in db.list_wears(conn, fragrance_id=body.fragrance_id) if w["id"] == wid)


@api.delete("/wears/{wid}", status_code=204)
def delete_wear(wid: int, conn: sqlite3.Connection = Depends(get_db)):
    if not db.delete_wear(conn, wid):
        raise HTTPException(404, "Wear not found")


# ---------------------------------------------------------------- insights


@api.get("/insights")
def insights(conn: sqlite3.Connection = Depends(get_db)):
    return build_report(db.list_fragrances(conn), db.list_wears(conn, limit=100000))


@api.get("/insights/ai")
def latest_ai_profile(conn: sqlite3.Connection = Depends(get_db)):
    return db.latest_ai_report(conn)


@api.post("/insights/ai")
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


@api.get("/export")
def export(conn: sqlite3.Connection = Depends(get_db)):
    return {"fragrances": db.list_fragrances(conn), "wears": db.list_wears(conn, limit=1000000)}


# ---------------------------------------------------------------- AI platform


@api.get("/health")
def health():
    return {"ok": True}


@api.get("/connect")
def connect_info(request: Request):
    """Everything an AI client needs to connect (shown on the app's Connect page)."""
    base = PUBLIC_URL or str(request.base_url).rstrip("/")
    return {
        "base_url": base,
        "api_key": auth.api_key(),
        "mcp_url": f"{base}/mcp",
        "openapi_url": f"{base}/openapi.json",
        "docs_url": f"{base}/docs",
        "public": bool(PUBLIC_URL),
        "project_dir": str(Path(__file__).resolve().parent.parent),
        "python": _python_path(),
        "tools": [{"name": t["name"], "read_only": t["read_only"], "summary": t["description"].split(". ")[0]} for t in tools.catalog()],
    }


def _python_path() -> str:
    import sys

    venv = Path(__file__).resolve().parent.parent / ".venv" / "bin" / "python"
    return str(venv) if venv.exists() else sys.executable


@api.get("/tools")
def list_tools():
    """The tool catalog with JSON schemas (the same tools the MCP server exposes)."""
    return tools.catalog()


@api.post("/tools/{name}")
def run_tool(name: str, request: Request, arguments: dict[str, Any] = Body(default_factory=dict)):
    """Call any Sillage tool by name with JSON arguments. Errors come back as 400 with a reason the caller can fix."""
    ok, result = tools.call_tool(name, arguments, caller=request.headers.get("x-client-name") or "API client")
    if not ok:
        raise HTTPException(404 if "Unknown tool" in str(result.get("error")) else 400, result)
    return result


class SQLRequest(BaseModel):
    sql: str = Field(..., description=db.SQL_GUIDE)


@api.post("/sql")
def run_sql(body: SQLRequest):
    """Read-only SQL over the collection for custom analysis (one SELECT, 500 rows, 3s)."""
    try:
        return db.run_readonly_query(body.sql)
    except db.QueryError as exc:
        raise HTTPException(400, {"error": str(exc), "schema": db.SQL_GUIDE})


@api.get("/feed")
def feed(limit: int = 50, conn: sqlite3.Connection = Depends(get_db)):
    """Insights published by AI clients, pinned first."""
    return db.list_insights(conn, min(limit, 200))


@api.post("/feed", status_code=201)
def publish(request: Request, body: dict[str, Any] = Body(...), conn: sqlite3.Connection = Depends(get_db)):
    """Publish an insight (same fields as the publish_insight tool)."""
    ok, result = tools.call_tool("publish_insight", body, caller=request.headers.get("x-client-name") or "API client")
    if not ok:
        raise HTTPException(400, result)
    return db.get_insight(conn, result["published"]["id"])


class PinRequest(BaseModel):
    pinned: bool


@api.patch("/feed/{iid}")
def pin(iid: int, body: PinRequest, conn: sqlite3.Connection = Depends(get_db)):
    if not db.set_insight_pinned(conn, iid, body.pinned):
        raise HTTPException(404, "Insight not found")
    return db.get_insight(conn, iid)


@api.delete("/feed/{iid}", status_code=204)
def remove_insight(iid: int, conn: sqlite3.Connection = Depends(get_db)):
    if not db.delete_insight(conn, iid):
        raise HTTPException(404, "Insight not found")


class ChatMessage(BaseModel):
    role: str = Field(..., pattern="^(user|assistant)$")
    content: str = Field(..., max_length=20000)


class ChatRequest(BaseModel):
    messages: list[ChatMessage] = Field(..., min_length=1, max_length=40)


@api.post("/chat")
def chat(body: ChatRequest):
    """Ask Sillage: streams Server-Sent Events from the in-app assistant (needs ANTHROPIC_API_KEY)."""
    if not claude_client.available():
        raise HTTPException(400, "Set ANTHROPIC_API_KEY to use the assistant")
    if body.messages[-1].role != "user":
        raise HTTPException(422, "The last message must be from the user")

    def events():
        try:
            for event in assistant.run([m.model_dump() for m in body.messages]):
                yield f"data: {json.dumps(event, default=str)}\n\n"
        except Exception as exc:  # keep the stream well-formed for the UI
            yield f"data: {json.dumps({'type': 'error', 'message': f'Assistant failed: {exc}'})}\n\n"

    return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


app.include_router(api)
