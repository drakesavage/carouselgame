"""Campaign management: start a new campaign, rename, archive, and delete.

* New: a blank campaign with one player character and an opening save point. Stats, role, tickets and gear are
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
CHARACTER_CHILDREN = ("status_effects", "npc_profiles", "advanced_marks", "mark_awards")
STORY_CHILDREN = ("story_revisions", "story_cast", "story_packets")   # no campaign_id: they hang off stories


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40] or "crawler"


def summary(conn: sqlite3.Connection) -> list[dict]:
    """Every campaign with what the manager shows: size, last play, archived."""
    from .carousel.residents import unmet_clause
    return rows(conn, f"""SELECT c.id, c.name, c.is_demo, c.data_source, c.current_location, c.archived, c.created_at, c.updated_at,
                                (SELECT COUNT(*) FROM turns t WHERE t.campaign_id = c.id AND t.status = 'completed') AS turns,
                                (SELECT MAX(t.created_at) FROM turns t WHERE t.campaign_id = c.id) AS last_played,
                                (SELECT COUNT(*) FROM characters h WHERE h.campaign_id = c.id AND {unmet_clause("h")}) AS characters,
                                (SELECT name FROM characters h WHERE h.id = c.active_character_id) AS character
                         FROM campaigns c ORDER BY c.archived, c.id""")


def create(conn: sqlite3.Connection, *, name: str, character: str, location: str | None = None, premise: str | None = None,
           opening: bool = True) -> int:
    """opening=True: the new-game flow (carousel/opening.py) starts in the car at the arrival lot and `location` is ignored."""
    name, character = name.strip(), character.strip()
    if not name or not character:
        raise ValueError("a campaign name and a character name are required")
    if one(conn, "SELECT id FROM campaigns WHERE name = ?", (name,)):
        raise ValueError(f"a campaign named '{name}' already exists")
    t = now_iso()
    with transaction(conn):
        cid = next_campaign_id(conn)
        conn.execute("""INSERT INTO campaigns (id, name, is_demo, data_source, current_location, safe_room, game_time_minutes, created_at, updated_at)
                        VALUES (?,?,?,?,?,?,?,?,?)""", (cid, name, 0, "manual", (location or "").strip() or None, 0, 0, t, t))
        pc = conn.execute("""INSERT INTO characters (campaign_id, slug, name, kind, roster_category, status, in_party, party_order, sheet_json, provenance)
                             VALUES (?,?,?,?,?,?,?,?,?,?)""", (cid, slug(character), character, "pc", "player_character", "active", 1, 0, "{}", "manual")).lastrowid
        conn.execute("UPDATE campaigns SET active_character_id = ? WHERE id = ?", (pc, cid))
        from .carousel import residents
        residents.ensure(conn, cid)
        if opening:
            _start_opening(conn, cid)
        else:
            _place_on_map(conn, cid, (location or "").strip(), t)
        if (premise or "").strip():
            conn.execute("""INSERT INTO lore_facts (campaign_id, subject, fact, certainty, visibility, source, created_at)
                            VALUES (?,?,?,?,?,?,?)""", (cid, "Campaign premise", premise.strip(), "canonical", "player", "campaign setup", t))
        create_save_point(conn, cid, "Campaign started", kind="manual")
    return cid


LODGE = "CAR-047"   # where the new-game flow ends (design §9)


def _start_opening(conn: sqlite3.Connection, cid: int) -> None:
    """Stage `car`, the group at the arrival lot, knowing only that place (the Lodge is learnt when the escort starts)."""
    from .carousel import venues
    from .carousel.opening import ARRIVAL_LOT
    from .carousel.refdata import has_reference
    conn.execute("UPDATE campaigns SET opening_stage = 'car' WHERE id = ?", (cid,))
    if not has_reference(conn):
        return
    label = conn.execute("SELECT name FROM ref.places WHERE id = ?", (ARRIVAL_LOT,)).fetchone()
    conn.execute("UPDATE campaigns SET node_id = ?, current_location = ?, safe_room = 0 WHERE id = ?", (ARRIVAL_LOT, label[0] if label else None, cid))
    conn.execute("INSERT INTO known_places (campaign_id, place_id, how, known_at_minutes) VALUES (?,?,?,0)", (cid, ARRIVAL_LOT, "start"))
    venues.enter(conn, cid, ARRIVAL_LOT)


def _place_on_map(conn: sqlite3.Connection, cid: int, location: str, t: str) -> None:
    """Start the group at the place its location names, else at Dyer's Lodge; it knows that place and the Lodge."""
    from .carousel import venues
    from .carousel.import_places import place_by_name
    from .carousel.refdata import has_reference
    from .rules.travel_ops import SAFE_PLACE_IDS
    if not has_reference(conn):
        return
    named = place_by_name(conn, location) if location else None
    start = named if named and conn.execute("SELECT 1 FROM ref.graph_nodes WHERE id = ?", (named,)).fetchone() else LODGE
    label = location or conn.execute("SELECT name FROM ref.places WHERE id = ?", (LODGE,)).fetchone()[0]
    conn.execute("UPDATE campaigns SET node_id = ?, current_location = ?, safe_room = ? WHERE id = ?",
                 (start, label, int(start in SAFE_PLACE_IDS), cid))
    for pid in dict.fromkeys((start, LODGE)):
        conn.execute("INSERT INTO known_places (campaign_id, place_id, how, known_at_minutes) VALUES (?,?,?,0)", (cid, pid, "start"))
    venues.enter(conn, cid, start)


def new_group(conn: sqlite3.Connection, cid: int, character: str) -> int:
    """After a wipe (design §8.1): a new player character arrives in the same town, in the car at the arrival lot, knowing
    nothing. The world stays: the clock, the posters, story history, residents and their memories, secrets and lore."""
    from .carousel.stories import active_story
    character = character.strip()
    if not character:
        raise ValueError("the new character needs a name")
    camp = one(conn, "SELECT * FROM campaigns WHERE id = ?", (cid,))
    if not camp:
        raise LookupError("campaign not found")
    pc = one(conn, "SELECT persistent_fate FROM characters WHERE id = ?", (camp["active_character_id"],))
    if not pc or pc["persistent_fate"] not in ("missing_postered", "permanently_lost"):
        raise ValueError("a new group starts only when your group is on the missing-poster board or lost")
    if active_story(conn, cid):
        raise ValueError("a story is still running")
    # a running turn: the API holds the write lock, and a turn staged meanwhile fails its state fingerprint on commit
    base, n = slug(character), 1
    while one(conn, "SELECT id FROM characters WHERE campaign_id = ? AND slug = ?", (cid, base if n == 1 else f"{base}-{n}")):
        n += 1
    with transaction(conn):
        conn.execute("UPDATE campaigns SET group_no = group_no + 1, lure_kind = NULL, lure_belief = NULL, lure_resident = NULL, "
                     "lure_paid_off = NULL, updated_at = ? WHERE id = ?", (now_iso(), cid))
        new = conn.execute("""INSERT INTO characters (campaign_id, slug, name, kind, roster_category, status, in_party, party_order, sheet_json, provenance)
                              VALUES (?,?,?,?,?,?,?,?,?,?)""", (cid, base if n == 1 else f"{base}-{n}", character, "pc", "player_character", "active",
                                                                1, 0, "{}", "manual")).lastrowid
        conn.execute("UPDATE campaigns SET active_character_id = ? WHERE id = ?", (new, cid))
        conn.execute("UPDATE characters SET in_party = 0 WHERE campaign_id = ? AND in_party = 1 AND id != ?", (cid, new))
        from .carousel import residents
        residents.unmeet_all(conn, cid)
        for t in ("known_places", "omen_states", "zone_visits"):   # what the group knows; the town itself is unchanged
            conn.execute(f"DELETE FROM {t} WHERE campaign_id = ?", (cid,))
        _start_opening(conn, cid)
        create_save_point(conn, cid, "A new group arrives", kind="manual")
    return new


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
        for t in STORY_CHILDREN:
            counts[t] = conn.execute(f"DELETE FROM {t} WHERE story_id IN (SELECT id FROM stories WHERE campaign_id = ?)", (cid,)).rowcount
        for t in _campaign_tables(conn):
            counts[t] = conn.execute(f"DELETE FROM {t} WHERE campaign_id = ?", (cid,)).rowcount
        counts["characters"] = conn.execute("DELETE FROM characters WHERE campaign_id = ?", (cid,)).rowcount
        conn.execute("DELETE FROM campaigns WHERE id = ?", (cid,))
    return {"deleted": camp["name"], "backup": dest.name, "pictures_moved": moved + len(pics),
            "rows": {k: v for k, v in counts.items() if v}}
