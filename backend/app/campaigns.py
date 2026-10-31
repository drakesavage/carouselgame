"""Campaign management: start a new campaign, rename, archive, and delete.

* New: a blank campaign with one player character and an opening save point. Stats, skills and gear are
  left empty for character creation in play; nothing is invented.
* Archive: hidden from the campaign picker, nothing removed. Unarchive brings it back.
* Delete: game PC only, the exact name must be typed, and never while a turn is running. A database backup is taken
  first and the campaign's art and portraits are moved into that backup folder, so a delete can always be undone by
  restoring that backup (docs/OPERATIONS.md).
"""
from __future__ import annotations

import re
import shutil
import sqlite3
from pathlib import Path

from .config import Settings
from .db import next_campaign_id, now_iso, one, remember_campaign_id, rows, transaction
from .memory.snapshots import create_save_point

# tables that hang off characters instead of carrying campaign_id
CHARACTER_CHILDREN = ("skills", "status_effects", "npc_profiles")


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40] or "crawler"


def summary(conn: sqlite3.Connection) -> list[dict]:
    """Every campaign with what the manager shows: size, last play, archived."""
    return rows(conn, """SELECT c.id, c.name, c.is_demo, c.data_source, c.current_floor, c.current_location, c.archived, c.created_at, c.updated_at,
                                (SELECT COUNT(*) FROM turns t WHERE t.campaign_id = c.id AND t.status = 'completed') AS turns,
                                (SELECT MAX(t.created_at) FROM turns t WHERE t.campaign_id = c.id) AS last_played,
                                (SELECT COUNT(*) FROM characters h WHERE h.campaign_id = c.id) AS characters,
                                (SELECT name FROM characters h WHERE h.id = c.active_character_id) AS crawler
                         FROM campaigns c ORDER BY c.archived, c.id""")


def create(conn: sqlite3.Connection, *, name: str, crawler: str, race: str | None = None, class_name: str | None = None,
           floor: int = 1, location: str | None = None, floor_hours: int | None = None, premise: str | None = None,
           clock_on_entry: bool = False) -> int:
    """clock_on_entry: the floor clock waits (floor_clock_pending_minutes) until the GM starts it as the crawler enters the Dungeon."""
    name, crawler = name.strip(), crawler.strip()
    if not name or not crawler:
        raise ValueError("a campaign name and a crawler name are required")
    if one(conn, "SELECT id FROM campaigns WHERE name = ?", (name,)):
        raise ValueError(f"a campaign named '{name}' already exists")
    t = now_iso()
    with transaction(conn):
        cid = next_campaign_id(conn)
        conn.execute(
            """INSERT INTO campaigns (id, name, is_demo, data_source, current_floor, current_location, safe_room, game_time_minutes,
                                      floor_deadline_minutes, floor_clock_pending_minutes, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (cid, name, 0, "manual", floor, (location or "").strip() or None, 0, 0,
             floor_hours * 60 if floor_hours and not clock_on_entry else None, floor_hours * 60 if floor_hours and clock_on_entry else None, t, t))
        pc = conn.execute(
            """INSERT INTO characters (campaign_id, slug, name, kind, roster_category, species, race, class_name, level, status, in_party,
                                       party_order, ai_favor, stats_json, derived_json, sheet_json, bio, provenance)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (cid, slug(crawler), crawler, "crawler", "player_character", (race or "").strip() or None, (race or "").strip() or None,
             (class_name or "").strip() or None, None, "active", 1, 0, None, "{}", "{}", "{}", None, "manual")).lastrowid
        conn.execute("UPDATE campaigns SET active_character_id = ? WHERE id = ?", (pc, cid))
        if (premise or "").strip():
            conn.execute("""INSERT INTO lore_facts (campaign_id, subject, fact, certainty, visibility, source, created_at)
                            VALUES (?,?,?,?,?,?,?)""", (cid, "Campaign premise", premise.strip(), "canonical", "player", "campaign setup", t))
        create_save_point(conn, cid, "Campaign started", kind="manual")
    return cid


def rename(conn: sqlite3.Connection, cid: int, name: str) -> None:
    name = name.strip()
    if not name:
        raise ValueError("the name can't be empty")
    if one(conn, "SELECT id FROM campaigns WHERE name = ? AND id != ?", (name, cid)):
        raise ValueError(f"a campaign named '{name}' already exists")
    conn.execute("UPDATE campaigns SET name = ?, updated_at = ? WHERE id = ?", (name, now_iso(), cid))


def set_archived(conn: sqlite3.Connection, cid: int, archived: bool) -> None:
    conn.execute("UPDATE campaigns SET archived = ? WHERE id = ?", (1 if archived else 0, cid))


def _campaign_tables(conn: sqlite3.Connection) -> list[str]:
    out = []
    for (t,) in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' AND sql NOT LIKE 'CREATE VIRTUAL%'"):
        if t not in ("campaigns", "characters") and any(r[1] == "campaign_id" for r in conn.execute(f"PRAGMA table_info({t})")):
            out.append(t)
    return out


def delete(conn: sqlite3.Connection, settings: Settings, cid: int, confirm_name: str) -> dict:
    """Back up, move the campaign's pictures into the backup, then remove every row the campaign owns."""
    from . import backup
    camp = one(conn, "SELECT id, name FROM campaigns WHERE id = ?", (cid,))
    if not camp:
        raise LookupError("campaign not found")
    if confirm_name.strip() != camp["name"]:
        raise ValueError("type the campaign's exact name to delete it")
    if one(conn, "SELECT id FROM turns WHERE campaign_id = ? AND status = 'processing'", (cid,)):
        raise RuntimeError("a turn is running in this campaign; wait for it to finish")
    dest = backup.create(settings, f"before-delete-{slug(camp['name'])}", media=False)
    moved = 0
    art = settings.data_dir / "art" / f"c{cid}"
    if art.is_dir():
        shutil.move(str(art), str(dest / "art" / f"c{cid}"))
        moved += 1
    pics = [p for p in settings.portraits_dir.glob(f"c{cid}-*") if p.is_file()]
    if pics:
        (dest / "portraits").mkdir(exist_ok=True)
        for p in pics:
            shutil.move(str(p), str(dest / "portraits" / p.name))
    counts: dict[str, int] = {}
    with transaction(conn):
        remember_campaign_id(conn, cid)
        # the full-text index mirrors source_sections by rowid; remove its entries before the rows
        conn.execute("""INSERT INTO source_sections_fts(source_sections_fts, rowid, section, content)
                        SELECT 'delete', id, document_path || ' ' || section, content FROM source_sections WHERE campaign_id = ?""", (cid,))
        for t in CHARACTER_CHILDREN:
            counts[t] = conn.execute(f"DELETE FROM {t} WHERE character_id IN (SELECT id FROM characters WHERE campaign_id = ?)", (cid,)).rowcount
        for t in _campaign_tables(conn):
            counts[t] = conn.execute(f"DELETE FROM {t} WHERE campaign_id = ?", (cid,)).rowcount
        counts["characters"] = conn.execute("DELETE FROM characters WHERE campaign_id = ?", (cid,)).rowcount
        conn.execute("DELETE FROM campaigns WHERE id = ?", (cid,))
    return {"deleted": camp["name"], "backup": dest.name, "pictures_moved": moved + len(pics),
            "rows": {k: v for k, v in counts.items() if v}}
