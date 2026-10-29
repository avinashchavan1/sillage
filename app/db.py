"""SQLite storage. One connection per request; schema is created on first use."""

from __future__ import annotations

import json
import os
import sqlite3
import time
from pathlib import Path
from typing import Any, Iterable, Optional

from .models import FragranceDetails

DEFAULT_DB = Path(__file__).resolve().parent.parent / "data" / "sillage.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS fragrances (
    id               INTEGER PRIMARY KEY,
    name             TEXT NOT NULL,
    brand            TEXT NOT NULL DEFAULT '',
    year             INTEGER,
    concentration    TEXT,
    gender           TEXT,
    family           TEXT,
    perfumers        TEXT NOT NULL DEFAULT '[]',
    description      TEXT,
    longevity        TEXT,
    sillage          TEXT,
    seasons          TEXT NOT NULL DEFAULT '[]',
    community_rating REAL,
    rating_votes     INTEGER,
    image_url        TEXT,
    source_url       TEXT,
    source           TEXT,
    status           TEXT NOT NULL DEFAULT 'owned',
    sentiment        INTEGER CHECK (sentiment BETWEEN -2 AND 2),
    bottle_ml        REAL,
    personal_notes   TEXT,
    created_at       TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at       TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS fragrance_notes (
    fragrance_id INTEGER NOT NULL REFERENCES fragrances(id) ON DELETE CASCADE,
    layer        TEXT NOT NULL CHECK (layer IN ('top', 'middle', 'base', 'general')),
    note         TEXT NOT NULL,
    position     INTEGER NOT NULL,
    PRIMARY KEY (fragrance_id, layer, note)
);

CREATE TABLE IF NOT EXISTS fragrance_accords (
    fragrance_id INTEGER NOT NULL REFERENCES fragrances(id) ON DELETE CASCADE,
    accord       TEXT NOT NULL,
    strength     REAL NOT NULL,
    position     INTEGER NOT NULL,
    PRIMARY KEY (fragrance_id, accord)
);

CREATE TABLE IF NOT EXISTS wears (
    id           INTEGER PRIMARY KEY,
    fragrance_id INTEGER NOT NULL REFERENCES fragrances(id) ON DELETE CASCADE,
    worn_on      TEXT NOT NULL,
    occasion     TEXT,
    weather      TEXT,
    sprays       INTEGER,
    enjoyment    INTEGER CHECK (enjoyment BETWEEN -2 AND 2),
    compliments  INTEGER NOT NULL DEFAULT 0,
    notes        TEXT,
    created_at   TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS wears_by_date ON wears (worn_on);
CREATE INDEX IF NOT EXISTS wears_by_fragrance ON wears (fragrance_id);

CREATE TABLE IF NOT EXISTS ai_reports (
    id         INTEGER PRIMARY KEY,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    payload    TEXT NOT NULL
);

-- Analyses published by AI clients (MCP, REST, the in-app assistant) for the app to display
CREATE TABLE IF NOT EXISTS insights (
    id             INTEGER PRIMARY KEY,
    title          TEXT NOT NULL,
    summary        TEXT NOT NULL,
    body           TEXT,
    chart          TEXT,
    fragrance_ids  TEXT NOT NULL DEFAULT '[]',
    source         TEXT NOT NULL DEFAULT 'AI',
    pinned         INTEGER NOT NULL DEFAULT 0,
    created_at     TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Convenience views for ad-hoc analysis (documented in SQL_GUIDE)
CREATE VIEW IF NOT EXISTS v_fragrances AS
    SELECT f.id, f.name, f.brand, f.year, f.concentration, f.gender, f.family, f.status,
           f.sentiment AS rating, f.bottle_ml, f.community_rating, f.created_at,
           (SELECT COUNT(*) FROM wears w WHERE w.fragrance_id = f.id) AS wear_count,
           (SELECT MAX(worn_on) FROM wears w WHERE w.fragrance_id = f.id) AS last_worn,
           (SELECT COALESCE(SUM(compliments), 0) FROM wears w WHERE w.fragrance_id = f.id) AS compliments
    FROM fragrances f;
CREATE VIEW IF NOT EXISTS v_notes AS
    SELECT n.fragrance_id, f.name, f.brand, f.status, f.sentiment AS rating, n.layer, n.note
    FROM fragrance_notes n JOIN fragrances f ON f.id = n.fragrance_id;
CREATE VIEW IF NOT EXISTS v_accords AS
    SELECT a.fragrance_id, f.name, f.brand, f.status, f.sentiment AS rating, a.accord, a.strength
    FROM fragrance_accords a JOIN fragrances f ON f.id = a.fragrance_id;
CREATE VIEW IF NOT EXISTS v_wears AS
    SELECT w.id, w.fragrance_id, f.name, f.brand, w.worn_on, w.occasion, w.weather, w.sprays,
           w.enjoyment, w.compliments, w.notes
    FROM wears w JOIN fragrances f ON f.id = w.fragrance_id;
"""

SQL_GUIDE = """SQLite database of one person's fragrance collection. Read-only; one SELECT (or WITH) statement.
Ratings everywhere are -2 hate, -1 dislike, 0 neutral, 1 like, 2 love (NULL = unrated).
Views (prefer these):
  v_fragrances(id, name, brand, year, concentration, gender, family, status, rating, bottle_ml,
               community_rating, created_at, wear_count, last_worn, compliments)
      status is one of owned, sample, decant, wishlist, tried, sold.
  v_notes(fragrance_id, name, brand, status, rating, layer, note)   layer: top|middle|base|general
  v_accords(fragrance_id, name, brand, status, rating, accord, strength)   strength 0-1, accord lowercase
  v_wears(id, fragrance_id, name, brand, worn_on, occasion, weather, sprays, enjoyment, compliments, notes)
      worn_on is 'YYYY-MM-DD'; enjoyment uses the rating scale.
Function note_key(note) groups variants ('Calabrian bergamot' -> 'bergamot'); use it to GROUP BY notes.
Base tables: fragrances, fragrance_notes, fragrance_accords, wears, insights."""

LAYERS = (("top", "top_notes"), ("middle", "middle_notes"), ("base", "base_notes"), ("general", "notes"))
JSON_COLUMNS = ("perfumers", "seasons")
DETAIL_COLUMNS = (
    "name", "brand", "year", "concentration", "gender", "family", "perfumers", "description",
    "longevity", "sillage", "seasons", "community_rating", "rating_votes", "image_url",
    "source_url", "source",
)
PERSONAL_COLUMNS = ("status", "sentiment", "bottle_ml", "personal_notes")


def db_path() -> Path:
    return Path(os.environ.get("SILLAGE_DB", DEFAULT_DB))


def connect(path: Optional[Path] = None) -> sqlite3.Connection:
    path = path or db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    return conn


def _encode(column: str, value: Any) -> Any:
    return json.dumps(value or []) if column in JSON_COLUMNS else value


def _clean_list(items: Iterable[str]) -> list[str]:
    seen, out = set(), []
    for item in items:
        item = " ".join(str(item).split())
        if item and item.lower() not in seen:
            seen.add(item.lower())
            out.append(item)
    return out


def _write_notes_and_accords(conn: sqlite3.Connection, fid: int, data: dict) -> None:
    for layer, key in LAYERS:
        if key in data and data[key] is not None:
            conn.execute("DELETE FROM fragrance_notes WHERE fragrance_id = ? AND layer = ?", (fid, layer))
            conn.executemany(
                "INSERT INTO fragrance_notes (fragrance_id, layer, note, position) VALUES (?, ?, ?, ?)",
                [(fid, layer, note, i) for i, note in enumerate(_clean_list(data[key]))],
            )
    if data.get("accords") is not None:
        conn.execute("DELETE FROM fragrance_accords WHERE fragrance_id = ?", (fid,))
        seen = set()
        rows = []
        for i, acc in enumerate(data["accords"]):
            name = " ".join(acc["name"].split()).lower()
            if name and name not in seen:
                seen.add(name)
                rows.append((fid, name, float(acc.get("strength", 1.0)), i))
        conn.executemany(
            "INSERT INTO fragrance_accords (fragrance_id, accord, strength, position) VALUES (?, ?, ?, ?)", rows
        )


def insert_fragrance(conn: sqlite3.Connection, data: dict) -> int:
    columns = [c for c in (*DETAIL_COLUMNS, *PERSONAL_COLUMNS) if c in data]
    cur = conn.execute(
        f"INSERT INTO fragrances ({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)})",
        [_encode(c, data[c]) for c in columns],
    )
    fid = cur.lastrowid
    _write_notes_and_accords(conn, fid, data)
    conn.commit()
    return fid


def update_fragrance(conn: sqlite3.Connection, fid: int, data: dict) -> None:
    columns = [c for c in (*DETAIL_COLUMNS, *PERSONAL_COLUMNS) if c in data]
    if columns:
        assignments = ", ".join(f"{c} = ?" for c in columns)
        conn.execute(
            f"UPDATE fragrances SET {assignments}, updated_at = datetime('now') WHERE id = ?",
            [*(_encode(c, data[c]) for c in columns), fid],
        )
    _write_notes_and_accords(conn, fid, data)
    conn.commit()


def find_duplicate(conn: sqlite3.Connection, brand: str, name: str) -> Optional[int]:
    row = conn.execute(
        "SELECT id FROM fragrances WHERE lower(trim(brand)) = lower(trim(?)) AND lower(trim(name)) = lower(trim(?))",
        (brand or "", name),
    ).fetchone()
    return row["id"] if row else None


def _hydrate(conn: sqlite3.Connection, rows: list[sqlite3.Row]) -> list[dict]:
    if not rows:
        return []
    items = {}
    for row in rows:
        item = dict(row)
        for c in JSON_COLUMNS:
            item[c] = json.loads(item[c] or "[]")
        for _, key in LAYERS:
            item[key] = []
        item["accords"] = []
        items[item["id"]] = item
    ids = list(items)
    marks = ",".join("?" for _ in ids)
    layer_key = dict(LAYERS)
    for r in conn.execute(
        f"SELECT fragrance_id, layer, note FROM fragrance_notes WHERE fragrance_id IN ({marks}) ORDER BY position", ids
    ):
        items[r["fragrance_id"]][layer_key[r["layer"]]].append(r["note"])
    for r in conn.execute(
        f"SELECT fragrance_id, accord, strength FROM fragrance_accords WHERE fragrance_id IN ({marks}) ORDER BY position",
        ids,
    ):
        items[r["fragrance_id"]]["accords"].append({"name": r["accord"], "strength": r["strength"]})
    for r in conn.execute(
        f"""SELECT fragrance_id, COUNT(*) AS wear_count, MAX(worn_on) AS last_worn
            FROM wears WHERE fragrance_id IN ({marks}) GROUP BY fragrance_id""",
        ids,
    ):
        items[r["fragrance_id"]].update(wear_count=r["wear_count"], last_worn=r["last_worn"])
    for item in items.values():
        item.setdefault("wear_count", 0)
        item.setdefault("last_worn", None)
    return [items[r["id"]] for r in rows]


def list_fragrances(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute("SELECT * FROM fragrances ORDER BY created_at DESC, id DESC").fetchall()
    return _hydrate(conn, rows)


def get_fragrance(conn: sqlite3.Connection, fid: int) -> Optional[dict]:
    rows = conn.execute("SELECT * FROM fragrances WHERE id = ?", (fid,)).fetchall()
    found = _hydrate(conn, rows)
    return found[0] if found else None


def delete_fragrance(conn: sqlite3.Connection, fid: int) -> bool:
    cur = conn.execute("DELETE FROM fragrances WHERE id = ?", (fid,))
    conn.commit()
    return cur.rowcount > 0


def details_from_row(item: dict) -> FragranceDetails:
    return FragranceDetails.model_validate({k: v for k, v in item.items() if k in FragranceDetails.model_fields})


def insert_wear(conn: sqlite3.Connection, data: dict) -> int:
    columns = ["fragrance_id", "worn_on", "occasion", "weather", "sprays", "enjoyment", "compliments", "notes"]
    cur = conn.execute(
        f"INSERT INTO wears ({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)})",
        [str(data[c]) if c == "worn_on" else data.get(c) for c in columns],
    )
    conn.commit()
    return cur.lastrowid


def list_wears(conn: sqlite3.Connection, fragrance_id: Optional[int] = None, limit: int = 1000) -> list[dict]:
    sql = """SELECT w.*, f.name AS fragrance_name, f.brand AS fragrance_brand
             FROM wears w JOIN fragrances f ON f.id = w.fragrance_id"""
    params: list[Any] = []
    if fragrance_id is not None:
        sql += " WHERE w.fragrance_id = ?"
        params.append(fragrance_id)
    sql += " ORDER BY w.worn_on DESC, w.id DESC LIMIT ?"
    params.append(limit)
    return [dict(r) for r in conn.execute(sql, params)]


def delete_wear(conn: sqlite3.Connection, wid: int) -> bool:
    cur = conn.execute("DELETE FROM wears WHERE id = ?", (wid,))
    conn.commit()
    return cur.rowcount > 0


def save_ai_report(conn: sqlite3.Connection, payload: dict) -> None:
    conn.execute("INSERT INTO ai_reports (payload) VALUES (?)", (json.dumps(payload),))
    conn.commit()


MAX_SQL_ROWS = 500


class QueryError(Exception):
    pass


def run_readonly_query(sql: str, path: Optional[Path] = None, max_rows: int = MAX_SQL_ROWS) -> dict:
    """Run one SELECT on a read-only connection with a time budget. Used by AI clients for analysis."""
    from .text import note_key

    statement = sql.strip().rstrip(";").strip()
    head = statement.split(None, 1)[0].lower() if statement else ""
    if head not in ("select", "with"):
        raise QueryError("Only a single SELECT (or WITH ... SELECT) statement is allowed")
    if ";" in statement:
        raise QueryError("Only one statement per query")
    path = path or db_path()
    connect(path).close()  # make sure the schema exists
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        conn.execute("PRAGMA query_only = ON")
        conn.create_function("note_key", 1, lambda s: note_key(s) if s else s, deterministic=True)
        deadline = time.monotonic() + 3.0
        conn.set_progress_handler(lambda: 1 if time.monotonic() > deadline else 0, 10_000)
        try:
            cur = conn.execute(statement)
            rows = cur.fetchmany(max_rows + 1)
        except sqlite3.OperationalError as exc:
            msg = "Query took too long (3s limit)" if "interrupted" in str(exc) else str(exc)
            raise QueryError(msg) from exc
        except sqlite3.DatabaseError as exc:
            raise QueryError(str(exc)) from exc
        columns = [d[0] for d in cur.description or []]
        return {
            "columns": columns,
            "rows": [list(r) for r in rows[:max_rows]],
            "row_count": min(len(rows), max_rows),
            "truncated": len(rows) > max_rows,
        }
    finally:
        conn.close()


def _insight_row(row: sqlite3.Row) -> dict:
    item = dict(row)
    item["chart"] = json.loads(item["chart"]) if item["chart"] else None
    item["fragrance_ids"] = json.loads(item["fragrance_ids"] or "[]")
    item["pinned"] = bool(item["pinned"])
    return item


def insert_insight(conn: sqlite3.Connection, data: dict) -> dict:
    cur = conn.execute(
        "INSERT INTO insights (title, summary, body, chart, fragrance_ids, source, pinned) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            data["title"], data["summary"], data.get("body"),
            json.dumps(data["chart"]) if data.get("chart") else None,
            json.dumps(data.get("fragrance_ids") or []), data.get("source") or "AI", int(bool(data.get("pinned"))),
        ),
    )
    conn.commit()
    return get_insight(conn, cur.lastrowid)


def get_insight(conn: sqlite3.Connection, iid: int) -> Optional[dict]:
    row = conn.execute("SELECT * FROM insights WHERE id = ?", (iid,)).fetchone()
    return _insight_row(row) if row else None


def list_insights(conn: sqlite3.Connection, limit: int = 50) -> list[dict]:
    rows = conn.execute("SELECT * FROM insights ORDER BY pinned DESC, id DESC LIMIT ?", (limit,)).fetchall()
    return [_insight_row(r) for r in rows]


def set_insight_pinned(conn: sqlite3.Connection, iid: int, pinned: bool) -> bool:
    cur = conn.execute("UPDATE insights SET pinned = ? WHERE id = ?", (int(pinned), iid))
    conn.commit()
    return cur.rowcount > 0


def delete_insight(conn: sqlite3.Connection, iid: int) -> bool:
    cur = conn.execute("DELETE FROM insights WHERE id = ?", (iid,))
    conn.commit()
    return cur.rowcount > 0


def latest_ai_report(conn: sqlite3.Connection) -> Optional[dict]:
    row = conn.execute("SELECT created_at, payload FROM ai_reports ORDER BY id DESC LIMIT 1").fetchone()
    if not row:
        return None
    return {"created_at": row["created_at"], **json.loads(row["payload"])}
