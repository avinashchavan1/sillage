"""Moving data in and out: JSON export/import (also from the hosted claude.ai app) and SQLite backups.

Export format (what /api/export returns and /api/import accepts):
    {"format": "sillage-export", "version": 1, "exported_at": ..., "fragrances": [...], "wears": [...], "insights": [...]}
Fragrance ids in the file are only used to connect wears and insights to their fragrance; they may be numbers
(this server) or strings (the hosted app). Import merges: fragrances already in the collection (same brand and
name) are matched rather than duplicated, and repeated wears or insights are skipped, so importing the same
file twice changes nothing.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from pydantic import ValidationError

from . import db
from .lookup.fragrantica import image_for
from .models import FragranceDetails, PersonalFields

FORMAT = "sillage-export"
BACKUP_KEEP = 14
BACKUP_EVERY_SECONDS = 24 * 3600


def export_all(conn: sqlite3.Connection) -> dict:
    return {
        "format": FORMAT,
        "version": 1,
        "exported_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "fragrances": db.list_fragrances(conn),
        "wears": db.list_wears(conn, limit=10_000_000),
        "insights": db.list_insights(conn, limit=100_000),
    }


def _sql_time(value: Any) -> Optional[str]:
    """'2026-10-29T14:46:25.381Z' -> '2026-10-29 14:46:25' (the format SQLite's datetime('now') uses)."""
    if not value:
        return None
    text = str(value).replace("T", " ").rstrip("Z")
    return text[:19] if len(text) >= 19 else None


def _fragrance(raw: dict) -> dict:
    data = dict(raw)
    if "sentiment" not in data and "rating" in data:
        data["sentiment"] = data["rating"]
    if data.get("status") not in ("owned", "sample", "decant", "wishlist", "tried", "sold"):
        data["status"] = "owned"
    details = FragranceDetails.model_validate({k: v for k, v in data.items() if k in FragranceDetails.model_fields and v is not None})
    personal = PersonalFields.model_validate({k: data.get(k) for k in PersonalFields.model_fields if data.get(k) is not None})
    out = {**details.model_dump(), **personal.model_dump()}
    # Hosted-app photos live in that app's own storage and can't move; Fragrantica's photo is rebuilt instead.
    if not out.get("image_url") or str(out["image_url"]).startswith(("/_blob/", "/media/")):
        out["image_url"] = image_for(out.get("source_url"))
    return out


def import_all(conn: sqlite3.Connection, payload: dict) -> dict:
    if not isinstance(payload, dict) or not isinstance(payload.get("fragrances"), list):
        raise ValueError("Not a Sillage export: expected an object with a 'fragrances' list")
    report = {"fragrances_added": 0, "fragrances_matched": 0, "wears_added": 0, "wears_skipped": 0,
              "insights_added": 0, "insights_skipped": 0, "errors": []}
    id_map: dict[str, int] = {}

    for raw in payload["fragrances"]:
        try:
            data = _fragrance(raw)
        except ValidationError as exc:
            report["errors"].append(f"Fragrance {raw.get('name', '?')!r}: {exc.errors()[0]['msg']}")
            continue
        if not data.get("name"):
            report["errors"].append("Skipped a fragrance with no name")
            continue
        existing = db.find_duplicate(conn, data["brand"], data["name"])
        if existing:
            fid = existing
            report["fragrances_matched"] += 1
        else:
            fid = db.insert_fragrance(conn, data)
            if created := _sql_time(raw.get("created_at")):
                conn.execute("UPDATE fragrances SET created_at = ? WHERE id = ?", (created, fid))
            report["fragrances_added"] += 1
        if raw.get("id") is not None:
            id_map[str(raw["id"])] = fid

    for raw in payload.get("wears") or []:
        fid = id_map.get(str(raw.get("fragrance_id")))
        worn = str(raw.get("worn_on") or "")[:10]
        if not fid or len(worn) != 10:
            report["wears_skipped"] += 1
            continue
        dup = conn.execute(
            "SELECT 1 FROM wears WHERE fragrance_id = ? AND worn_on = ? AND IFNULL(occasion, '') = ? AND IFNULL(notes, '') = ?",
            (fid, worn, raw.get("occasion") or "", raw.get("notes") or ""),
        ).fetchone()
        if dup:
            report["wears_skipped"] += 1
            continue
        enjoyment = raw.get("enjoyment")
        db.insert_wear(conn, {
            "fragrance_id": fid, "worn_on": worn, "occasion": raw.get("occasion"), "weather": raw.get("weather"),
            "sprays": raw.get("sprays"), "enjoyment": enjoyment if enjoyment in (-2, -1, 0, 1, 2) else None,
            "compliments": int(raw.get("compliments") or 0), "notes": raw.get("notes"),
        })
        report["wears_added"] += 1

    for raw in payload.get("insights") or []:
        title, summary = str(raw.get("title") or "")[:120], str(raw.get("summary") or "")[:600]
        if not title or not summary or conn.execute(
            "SELECT 1 FROM insights WHERE title = ? AND summary = ?", (title, summary)
        ).fetchone():
            report["insights_skipped"] += 1
            continue
        item = db.insert_insight(conn, {
            "title": title, "summary": summary, "body": raw.get("body"), "chart": raw.get("chart"),
            "fragrance_ids": [id_map[str(i)] for i in raw.get("fragrance_ids") or [] if str(i) in id_map],
            "source": raw.get("source") or "Import", "pinned": bool(raw.get("pinned")),
        })
        if created := _sql_time(raw.get("created_at")):
            conn.execute("UPDATE insights SET created_at = ? WHERE id = ?", (created, item["id"]))
        report["insights_added"] += 1

    conn.commit()
    return report


# ---------------------------------------------------------------- backups


def backup_dir() -> Path:
    return Path(db.db_path()).parent / "backups"


def backup_now(dest_dir: Optional[Path] = None, keep: int = BACKUP_KEEP, stamp: Optional[str] = None) -> Path:
    """Consistent copy of the live database (SQLite online backup), keeping the newest `keep` files."""
    dest_dir = dest_dir or backup_dir()
    dest_dir.mkdir(parents=True, exist_ok=True)
    stamp = stamp or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    target = dest_dir / f"sillage-{stamp}.db"
    tmp = target.with_suffix(".db.tmp")
    src = db.connect()
    dst = sqlite3.connect(tmp)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    tmp.replace(target)
    for old in sorted(dest_dir.glob("sillage-*.db"))[:-keep] if keep > 0 else []:
        old.unlink(missing_ok=True)
    return target


async def backup_loop() -> None:
    while True:
        try:
            path = await asyncio.to_thread(backup_now)
            print(f"Backup written: {path}", flush=True)
        except Exception as exc:  # never take the server down over a backup
            print(f"Backup failed: {exc}", flush=True)
        await asyncio.sleep(BACKUP_EVERY_SECONDS)


def load_file(path: str) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))
