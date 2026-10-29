"""The Sillage tool set: one typed registry exposed three ways.

  * MCP server (`app/mcp_server.py`)  → Claude Desktop / claude.ai / ChatGPT / Cursor / any MCP client
  * REST (`POST /api/tools/{name}`)   → any app or agent framework with function calling
  * the in-app assistant (`app/assistant.py`)

Each tool is a plain function with annotated parameters; its docstring is the description the model
reads, and its signature becomes the JSON schema, so every surface stays in sync automatically.
"""

from __future__ import annotations

import contextvars
import inspect
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Annotated, Any, Callable, Literal, Optional, get_type_hints

from pydantic import BaseModel, ConfigDict, Field, ValidationError, create_model, model_validator

from . import db, lookup
from .insights import TasteModel, build_report
from .models import SENTIMENT_LABELS, Status
from .recommend import similar, suggest_wear as _suggest
from .text import fold, match_score, note_key

# Who is calling (shown as the source of published insights)
CALLER: contextvars.ContextVar[str] = contextvars.ContextVar("sillage_caller", default="AI")

Rating = Annotated[int, Field(ge=-2, le=2, description="-2 hate, -1 dislike, 0 neutral, 1 like, 2 love")]


class ToolError(Exception):
    pass


@dataclass
class ToolSpec:
    name: str
    description: str
    fn: Callable[..., Any]
    params: type[BaseModel]
    read_only: bool


REGISTRY: dict[str, ToolSpec] = {}


def _params_model(fn: Callable) -> type[BaseModel]:
    hints = get_type_hints(fn, include_extras=True)
    fields = {}
    for name, p in inspect.signature(fn).parameters.items():
        fields[name] = (hints[name], ... if p.default is inspect.Parameter.empty else p.default)
    return create_model(f"{fn.__name__}_args", __config__=ConfigDict(extra="forbid"), **fields)


def tool(read_only: bool = True):
    def register(fn: Callable) -> Callable:
        REGISTRY[fn.__name__] = ToolSpec(fn.__name__, inspect.getdoc(fn) or "", fn, _params_model(fn), read_only)
        return fn
    return register


def call_tool(name: str, arguments: Optional[dict], caller: str = "AI") -> tuple[bool, Any]:
    """Validate and run a tool. Returns (ok, result-or-error) and never raises for bad input."""
    spec = REGISTRY.get(name)
    if spec is None:
        return False, {"error": f"Unknown tool '{name}'", "available": sorted(REGISTRY)}
    try:
        params = spec.params.model_validate(arguments or {})
    except ValidationError as exc:
        return False, {"error": "Invalid arguments", "details": exc.errors(include_url=False, include_context=False)[:5]}
    token = CALLER.set(caller)
    try:
        return True, spec.fn(**{k: getattr(params, k) for k in spec.params.model_fields})
    except ToolError as exc:
        return False, {"error": str(exc)}
    finally:
        CALLER.reset(token)


def json_schema(spec: ToolSpec) -> dict:
    schema = spec.params.model_json_schema()
    schema.pop("title", None)
    return schema


def claude_tools() -> list[dict]:
    return [
        {"name": s.name, "description": s.description, "input_schema": json_schema(s), "eager_input_streaming": True}
        for s in REGISTRY.values()
    ]


def catalog() -> list[dict]:
    return [{"name": s.name, "description": s.description, "read_only": s.read_only, "input_schema": json_schema(s)}
            for s in REGISTRY.values()]


# ---------------------------------------------------------------- helpers


def _load() -> tuple[list[dict], list[dict]]:
    conn = db.connect()
    try:
        return db.list_fragrances(conn), db.list_wears(conn, limit=1_000_000)
    finally:
        conn.close()


def _brief(f: dict) -> dict:
    return {
        "id": f["id"], "name": f["name"], "brand": f.get("brand"), "year": f.get("year"), "family": f.get("family"),
        "status": f.get("status"), "rating": f.get("sentiment"),
        "rating_label": SENTIMENT_LABELS.get(f.get("sentiment"), "unrated") if f.get("sentiment") is not None else "unrated",
        "wear_count": f.get("wear_count", 0), "last_worn": f.get("last_worn"),
        "top_accords": [a["name"] for a in (f.get("accords") or [])[:4]],
    }


def _resolve(fragrances: list[dict], fragrance_id: Optional[int], name: Optional[str]) -> Optional[dict]:
    if fragrance_id is not None:
        return next((f for f in fragrances if f["id"] == fragrance_id), None)
    if name:
        best = max(fragrances, key=lambda f: match_score(name, f"{f.get('brand', '')} {f['name']}"), default=None)
        if best and match_score(name, f"{best.get('brand', '')} {best['name']}") >= 0.6:
            return best
    return None


def _slim_affinity(rows: list[dict]) -> list[dict]:
    return [{"note": r["label"], "affinity": r["affinity"], "confidence": r["confidence"], "count": r["count"],
             "in": [f"{x['brand']} {x['name']}".strip() for x in r["fragrances"][:4]]} for r in rows]


def _lookup(query: str):
    try:
        return lookup.lookup(query)
    except lookup.LookupFailed as exc:
        raise ToolError("Couldn't find that fragrance online: " + "; ".join(exc.errors))


# ---------------------------------------------------------------- read tools


@tool()
def collection_overview() -> dict:
    """Start here. Summary of the whole collection: counts by status and rating, wear totals, top brands and
    families, the most recently added fragrances and recent wears. Cheap; call it to orient yourself."""
    frags, wears = _load()
    today = date.today()
    by = lambda key: dict(sorted(_count(f.get(key) for f in frags).items(), key=lambda kv: -kv[1])[:8])  # noqa: E731
    return {
        "today": today.isoformat(),
        "fragrances": len(frags),
        "by_status": _count(f["status"] for f in frags),
        "by_rating": _count(SENTIMENT_LABELS[f["sentiment"]] if f.get("sentiment") is not None else "unrated" for f in frags),
        "top_brands": by("brand"),
        "top_families": by("family"),
        "wears_total": len(wears),
        "wears_last_30_days": sum(1 for w in wears if (today - date.fromisoformat(w["worn_on"][:10])).days < 30),
        "recently_added": [_brief(f) for f in frags[:5]],
        "recent_wears": [{"date": w["worn_on"], "fragrance_id": w["fragrance_id"], "name": w["fragrance_name"],
                          "occasion": w.get("occasion"), "weather": w.get("weather")} for w in wears[:5]],
    }


def _count(values) -> dict:
    out: dict = {}
    for v in values:
        if v:
            out[v] = out.get(v, 0) + 1
    return out


@tool()
def search_fragrances(
    text: Annotated[Optional[str], Field(description="Free text matched against name, brand, family, perfumers, notes and accords")] = None,
    status: Optional[Status] = None,
    min_rating: Optional[Rating] = None,
    max_rating: Optional[Rating] = None,
    note: Annotated[Optional[str], Field(description="Only fragrances containing this note (variants match: 'vanilla' finds 'Madagascar Vanilla')")] = None,
    accord: Annotated[Optional[str], Field(description="Only fragrances with this main accord, e.g. 'woody'")] = None,
    limit: Annotated[int, Field(ge=1, le=200)] = 25,
) -> dict:
    """Find fragrances in the collection by text, status, rating range, note or accord. Returns compact rows;
    use get_fragrance for full details."""
    frags, _ = _load()
    needle = fold(text or "").strip()
    nkey = note_key(note) if note else None
    out = []
    for f in frags:
        if status and f["status"] != status:
            continue
        r = f.get("sentiment")
        if min_rating is not None and (r is None or r < min_rating):
            continue
        if max_rating is not None and (r is None or r > max_rating):
            continue
        notes = [*f["top_notes"], *f["middle_notes"], *f["base_notes"], *f["notes"]]
        if nkey and nkey not in {note_key(n) for n in notes}:
            continue
        if accord and accord.lower() not in {a["name"] for a in f["accords"]}:
            continue
        if needle:
            hay = fold(" ".join([f["name"], f.get("brand") or "", f.get("family") or "", *f["perfumers"], *notes,
                                 *(a["name"] for a in f["accords"])]))
            if needle not in hay:
                continue
        out.append(_brief(f))
    return {"count": len(out), "results": out[:limit]}


@tool()
def get_fragrance(fragrance_id: int) -> dict:
    """Full details for one fragrance in the collection: notes pyramid, accords, personal fields, recent wears,
    the predicted match from the taste model, and the most similar fragrances in the collection."""
    frags, wears = _load()
    f = next((x for x in frags if x["id"] == fragrance_id), None)
    if not f:
        raise ToolError(f"No fragrance with id {fragrance_id}")
    model = TasteModel(frags, wears)
    return {
        **{k: f.get(k) for k in ("id", "name", "brand", "year", "concentration", "gender", "family", "perfumers",
                                 "accords", "top_notes", "middle_notes", "base_notes", "notes", "description",
                                 "status", "bottle_ml", "personal_notes", "source_url", "wear_count", "last_worn")},
        "rating": f.get("sentiment"),
        "taste_score": model.scores.get(fragrance_id),
        "predicted_match": model.predict(f),
        "your_note_affinity": model.note_affinity(f),
        "recent_wears": [w for w in wears if w["fragrance_id"] == fragrance_id][:20],
        "similar_in_collection": similar(f, frags, 4),
    }


@tool()
def taste_profile() -> dict:
    """The learned taste model: notes and accords the person loves and avoids (affinity about -2..+3, with
    confidence and example fragrances), polarizing notes, best families/perfumers/brands, monthly wear counts,
    most worn, neglected bottles, seasonal accords, occasion go-tos and wishlist predictions."""
    frags, wears = _load()
    r = build_report(frags, wears)
    return {
        "ready": r["ready"], "rated_or_worn": r["scored_count"], "summary": r["summary"], "totals": r["totals"],
        "loved_notes": _slim_affinity(r["liked_notes"]), "avoided_notes": _slim_affinity(r["disliked_notes"]),
        "loved_accords": _slim_affinity(r["liked_accords"]), "avoided_accords": _slim_affinity(r["disliked_accords"]),
        "polarizing_notes": [p["label"] for p in r["polarizing_notes"]],
        "families": r["families"], "perfumers": r["perfumers"], "brands": r["brands"][:8],
        "monthly_wears": r["monthly_wears"], "most_worn": r["most_worn"], "most_complimented": r["most_complimented"],
        "neglected": r["neglected"], "seasons": r["seasons"], "occasions": r["occasions"],
        "wishlist_predictions": [{k: p[k] for k in ("id", "name", "brand", "match", "verdict")} for p in r["wishlist_predictions"]],
        "how_affinity_works": "Shrunk weighted mean of the scores of fragrances containing the note; score = rating "
                              "blended with wear enjoyment, plus a bonus for frequent wear. Positive = loved.",
    }


@tool()
def wear_history(
    days: Annotated[int, Field(ge=1, le=3650, description="How far back to look")] = 90,
    fragrance_id: Optional[int] = None,
    limit: Annotated[int, Field(ge=1, le=1000)] = 100,
) -> dict:
    """Wear log entries (newest first) with date, occasion, weather, sprays, enjoyment and compliments."""
    conn = db.connect()
    try:
        wears = db.list_wears(conn, fragrance_id=fragrance_id, limit=100_000)
    finally:
        conn.close()
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    rows = [w for w in wears if w["worn_on"] >= cutoff]
    return {"count": len(rows), "wears": rows[:limit]}


@tool()
def query_database(
    sql: Annotated[str, Field(description="One read-only SQLite SELECT. " + db.SQL_GUIDE)],
) -> dict:
    """Run a read-only SQL query for custom analysis (aggregations, trends, cross-tabs) the other tools don't
    cover. Returns columns and up to 500 rows. Example: SELECT note_key(note) k, AVG(rating) r, COUNT(*) n
    FROM v_notes WHERE rating IS NOT NULL GROUP BY k HAVING n >= 2 ORDER BY r DESC"""
    try:
        return db.run_readonly_query(sql)
    except db.QueryError as exc:
        raise ToolError(f"{exc}. Schema: {db.SQL_GUIDE}")


@tool()
def evaluate_fragrance(
    name: Annotated[str, Field(description="Any fragrance name, e.g. 'Parfums de Marly Layton' or a Fragrantica URL")],
) -> dict:
    """Look up a fragrance online (Fragrantica, falling back to web research) WITHOUT saving it, and predict how
    much this person would like it from their taste model. Also says whether it's already in the collection
    and which owned fragrances smell most similar. Use for 'should I buy X?' or 'would I like X?'."""
    result = _lookup(name)
    d = result.details.model_dump()
    frags, wears = _load()
    model = TasteModel(frags, wears)
    dup = next((f for f in frags if fold(f.get("brand") or "") == fold(d["brand"]) and fold(f["name"]) == fold(d["name"])), None)
    owned = [f for f in frags if f["status"] in ("owned", "decant", "sample")]
    return {
        "fragrance": {k: d[k] for k in ("name", "brand", "year", "concentration", "gender", "family", "perfumers",
                                        "accords", "top_notes", "middle_notes", "base_notes", "notes", "source_url")},
        "lookup_note": result.message,
        "predicted_match": model.predict(d),
        "already_in_collection": _brief(dup) if dup else None,
        "similar_owned": similar(d, owned, 3),
    }


@tool()
def similar_fragrances(
    fragrance_id: Annotated[Optional[int], Field(description="A fragrance in the collection")] = None,
    name: Annotated[Optional[str], Field(description="Or any fragrance by name (looked up online if not owned)")] = None,
    limit: Annotated[int, Field(ge=1, le=20)] = 5,
) -> dict:
    """Fragrances in the collection that smell most like the given one (cosine similarity over notes and
    accords), with the notes/accords they share. Handy for dupes, layering and 'I already have something like it'."""
    frags, _ = _load()
    target = _resolve(frags, fragrance_id, name)
    if target is None:
        if not name:
            raise ToolError("Give fragrance_id or name")
        target = _lookup(name).details.model_dump()
    return {"target": {"name": target["name"], "brand": target.get("brand")}, "similar": similar(target, frags, limit)}


@tool()
def suggest_what_to_wear(
    weather: Annotated[Optional[str], Field(description="hot, warm, mild, cool, cold, rainy or humid")] = None,
    occasion: Annotated[Optional[str], Field(description="e.g. Work, Date, Night out, Casual, Gym / sport")] = None,
    limit: Annotated[int, Field(ge=1, le=10)] = 3,
) -> dict:
    """Pick fragrances to wear now from what the person owns: favours their favourites, bottles they haven't worn
    lately, scents that fit the weather (learned from their own wear log) and their go-tos for the occasion.
    Returns ranked picks with plain-language reasons."""
    frags, wears = _load()
    return {"weather": weather, "occasion": occasion, "picks": _suggest(frags, wears, weather, occasion, limit)}


@tool()
def list_insights(limit: Annotated[int, Field(ge=1, le=100)] = 10) -> dict:
    """Analyses previously published to the app's Insights feed (newest and pinned first)."""
    conn = db.connect()
    try:
        return {"insights": db.list_insights(conn, limit)}
    finally:
        conn.close()


# ---------------------------------------------------------------- write tools


@tool(read_only=False)
def add_fragrance(
    name: Annotated[str, Field(description="Fragrance name as the person said it (typos fine) or a Fragrantica URL")],
    status: Status = "owned",
    rating: Optional[Rating] = None,
    personal_notes: Optional[str] = None,
) -> dict:
    """Look the fragrance up online and save it to the collection with its notes and accords. Refuses duplicates."""
    result = _lookup(name)
    d = result.details
    conn = db.connect()
    try:
        if dup := db.find_duplicate(conn, d.brand, d.name):
            raise ToolError(f"{d.brand} {d.name} is already in the collection (id {dup})")
        fid = db.insert_fragrance(conn, {**d.model_dump(), "status": status, "sentiment": rating, "personal_notes": personal_notes})
        return {"added": _brief(db.get_fragrance(conn, fid)), "lookup_note": result.message}
    finally:
        conn.close()


@tool(read_only=False)
def update_fragrance(
    fragrance_id: int,
    status: Optional[Status] = None,
    rating: Optional[Rating] = None,
    clear_rating: bool = False,
    personal_notes: Optional[str] = None,
    bottle_ml: Annotated[Optional[float], Field(ge=0)] = None,
) -> dict:
    """Change a fragrance's status, rating, personal notes or bottle size. Only the fields you pass change."""
    conn = db.connect()
    try:
        if not db.get_fragrance(conn, fragrance_id):
            raise ToolError(f"No fragrance with id {fragrance_id}")
        patch = {k: v for k, v in {"status": status, "sentiment": rating, "personal_notes": personal_notes,
                                    "bottle_ml": bottle_ml}.items() if v is not None}
        if clear_rating:
            patch["sentiment"] = None
        if not patch:
            raise ToolError("Nothing to change")
        db.update_fragrance(conn, fragrance_id, patch)
        return {"updated": _brief(db.get_fragrance(conn, fragrance_id))}
    finally:
        conn.close()


@tool(read_only=False)
def log_wear(
    fragrance_id: int,
    worn_on: Annotated[Optional[date], Field(description="YYYY-MM-DD; defaults to today")] = None,
    occasion: Optional[str] = None,
    weather: Optional[str] = None,
    sprays: Annotated[Optional[int], Field(ge=0, le=50)] = None,
    enjoyment: Optional[Rating] = None,
    compliments: Annotated[int, Field(ge=0)] = 0,
    notes: Optional[str] = None,
) -> dict:
    """Record that the person wore a fragrance (it must be in the collection)."""
    conn = db.connect()
    try:
        f = db.get_fragrance(conn, fragrance_id)
        if not f:
            raise ToolError(f"No fragrance with id {fragrance_id}")
        wid = db.insert_wear(conn, {"fragrance_id": fragrance_id, "worn_on": worn_on or date.today(), "occasion": occasion,
                                    "weather": weather, "sprays": sprays, "enjoyment": enjoyment,
                                    "compliments": compliments, "notes": notes})
        return {"logged": {"wear_id": wid, "fragrance": f"{f['brand']} {f['name']}".strip(), "worn_on": str(worn_on or date.today())}}
    finally:
        conn.close()


class Chart(BaseModel):
    """A small chart the app renders under the insight."""

    model_config = ConfigDict(extra="forbid")
    type: Literal["bar", "diverging", "line", "table"] = Field(
        description="bar: ranked magnitudes; diverging: values that can be negative (e.g. affinity); "
                    "line: a trend over ordered labels (e.g. months); table: rows and columns")
    title: Optional[str] = None
    labels: list[str] = Field(default_factory=list, description="Category labels (bar, diverging, line)")
    values: list[float] = Field(default_factory=list, description="One number per label (bar, diverging, line)")
    unit: Optional[str] = Field(None, description="Shown after values, e.g. 'wears' or '%'")
    columns: list[str] = Field(default_factory=list, description="Table header (table)")
    rows: list[list[Any]] = Field(default_factory=list, description="Table rows (table)")

    @model_validator(mode="after")
    def _shape(self):
        if self.type == "table":
            if not self.columns or not self.rows:
                raise ValueError("table charts need columns and rows")
            self.rows = self.rows[:50]
        else:
            if not self.labels or len(self.labels) != len(self.values):
                raise ValueError("labels and values must be non-empty and the same length")
            self.labels, self.values = self.labels[:40], self.values[:40]
        return self


@tool(read_only=False)
def publish_insight(
    title: Annotated[str, Field(max_length=120)],
    summary: Annotated[str, Field(max_length=600, description="One to three sentences: the finding")],
    body_markdown: Annotated[Optional[str], Field(max_length=20000, description="Optional detail in Markdown")] = None,
    chart: Optional[Chart] = None,
    fragrance_ids: Annotated[list[int], Field(description="Fragrances this is about (linked in the app)")] = [],  # noqa: B006
    source: Annotated[Optional[str], Field(description="Your app's name, e.g. 'Claude', 'ChatGPT'")] = None,
    pinned: bool = False,
) -> dict:
    """Publish an analysis to the Insights feed in the Sillage app so the person can see it there, with an
    optional chart. Use it when the person asks to save, pin or share a finding, or after a substantial analysis
    they asked for. Don't publish trivia."""
    conn = db.connect()
    try:
        item = db.insert_insight(conn, {"title": title, "summary": summary, "body": body_markdown,
                                        "chart": chart.model_dump() if chart else None, "fragrance_ids": fragrance_ids,
                                        "source": source or CALLER.get(), "pinned": pinned})
        return {"published": {"id": item["id"], "title": item["title"]}, "view_in_app": "/#ai"}
    finally:
        conn.close()
