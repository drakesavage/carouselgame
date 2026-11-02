"""Save points and rollback.

A save point stores a JSON copy of every campaign-scoped *state* table plus the high-water ids of the
append-only logs. Restoring replaces the state tables from the copy and flags log rows newer than the
save point as rolled_back (they are kept for audit, hidden from the transcript and timeline).
A 'pre_restore' save point is taken automatically first, so a restore can itself be undone.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3

from ..db import now_iso, one, rows, transaction

# table -> SQL selecting that campaign's rows (param: campaign_id)
SNAPSHOT_TABLES: dict[str, str] = {
    "campaigns": "SELECT * FROM campaigns WHERE id = ?",
    "characters": "SELECT * FROM characters WHERE campaign_id = ?",
    "items": "SELECT * FROM items WHERE campaign_id = ?",
    "status_effects": "SELECT s.* FROM status_effects s JOIN characters c ON c.id = s.character_id WHERE c.campaign_id = ?",
    "npc_profiles": "SELECT n.* FROM npc_profiles n JOIN characters c ON c.id = n.character_id WHERE c.campaign_id = ?",
    "relationships": "SELECT * FROM relationships WHERE campaign_id = ?",
    "locations": "SELECT * FROM locations WHERE campaign_id = ?",
    "quests": "SELECT * FROM quests WHERE campaign_id = ?",
    "clocks": "SELECT * FROM clocks WHERE campaign_id = ?",
    "house_rules": "SELECT * FROM house_rules WHERE campaign_id = ?",
    "rulings": "SELECT * FROM rulings WHERE campaign_id = ?",
    "lore_facts": "SELECT * FROM lore_facts WHERE campaign_id = ?",
    "gm_secrets": "SELECT * FROM gm_secrets WHERE campaign_id = ?",
    "memory_summaries": "SELECT * FROM memory_summaries WHERE campaign_id = ?",
    "party_groups": "SELECT * FROM party_groups WHERE campaign_id = ?",
    "scenes": "SELECT * FROM scenes WHERE campaign_id = ?",
    "memories": "SELECT * FROM memories WHERE campaign_id = ?",
    "character_tickets": "SELECT * FROM character_tickets WHERE campaign_id = ?",
    "stat_modifiers": "SELECT * FROM stat_modifiers WHERE campaign_id = ?",
    "known_places": "SELECT * FROM known_places WHERE campaign_id = ?",
    "campaign_flags": "SELECT * FROM campaign_flags WHERE campaign_id = ?",
    "omen_states": "SELECT * FROM omen_states WHERE campaign_id = ?",
    "zone_visits": "SELECT * FROM zone_visits WHERE campaign_id = ?",
    "venue_overlays": "SELECT * FROM venue_overlays WHERE campaign_id = ?",
    "stories": "SELECT * FROM stories WHERE campaign_id = ?",
    "story_packets": "SELECT p.* FROM story_packets p JOIN stories s ON s.id = p.story_id WHERE s.campaign_id = ?",
    "story_cast": "SELECT x.* FROM story_cast x JOIN stories s ON s.id = x.story_id WHERE s.campaign_id = ?",
    "story_revisions": "SELECT r.* FROM story_revisions r JOIN stories s ON s.id = r.story_id WHERE s.campaign_id = ?",
    "story_spoilers": "SELECT * FROM story_spoilers WHERE campaign_id = ?",
    "advanced_marks": "SELECT m.* FROM advanced_marks m JOIN characters c ON c.id = m.character_id WHERE c.campaign_id = ?",
    "mark_awards": "SELECT a.* FROM mark_awards a JOIN characters c ON c.id = a.character_id WHERE c.campaign_id = ?",
    "death_events": "SELECT * FROM death_events WHERE campaign_id = ?",
    "missing_posters": "SELECT * FROM missing_posters WHERE campaign_id = ?",
    "danger_warnings": "SELECT * FROM danger_warnings WHERE campaign_id = ?",
    "resident_whereabouts": "SELECT * FROM resident_whereabouts WHERE campaign_id = ?",
}
# Tables without a campaign_id column: which rows belong to the campaign (param: campaign_id).
_BY_CHARACTER = "character_id IN (SELECT id FROM characters WHERE campaign_id = ?)"
_BY_STORY = "story_id IN (SELECT id FROM stories WHERE campaign_id = ?)"
_SCOPED_DELETE = {"status_effects": _BY_CHARACTER, "npc_profiles": _BY_CHARACTER, "advanced_marks": _BY_CHARACTER, "mark_awards": _BY_CHARACTER,
                  "story_packets": _BY_STORY, "story_cast": _BY_STORY, "story_revisions": _BY_STORY}
# Delete children before parents.
_DELETE_ORDER = ["death_events", "missing_posters", "danger_warnings", "story_revisions", "story_cast", "story_packets", "mark_awards", "advanced_marks", "stories", "story_spoilers", "known_places", "campaign_flags", "omen_states", "zone_visits", "venue_overlays", "resident_whereabouts", "character_tickets", "stat_modifiers", "status_effects", "npc_profiles", "relationships", "items", "locations",
                 "quests", "clocks", "house_rules", "rulings", "lore_facts", "gm_secrets",
                 "memory_summaries", "party_groups", "scenes", "memories", "characters"]
_LOGS = {"messages": "max_message_id", "events": "max_event_id", "turns": "max_turn_id"}


def _raw_rows(conn: sqlite3.Connection, sql: str, cid: int) -> list[dict]:
    return [dict(r) for r in conn.execute(sql, (cid,)).fetchall()]


def _high_water(conn: sqlite3.Connection, cid: int) -> dict[str, int]:
    return {col: conn.execute(f"SELECT COALESCE(MAX(id), 0) FROM {t} WHERE campaign_id = ?", (cid,)).fetchone()[0]
            for t, col in _LOGS.items()}


def state_fingerprint(conn: sqlite3.Connection, cid: int) -> str:
    """Hash of everything a save point captures plus the log high-water marks: equal fingerprints mean the
    authoritative state did not change in between (used to stage a turn without holding a write transaction)."""
    h = hashlib.sha256()
    for t, sql in SNAPSHOT_TABLES.items():
        h.update(t.encode())
        for r in conn.execute(sql, (cid,)).fetchall():
            h.update(json.dumps(tuple(r), default=str).encode())
    hw = _high_water(conn, cid)
    h.update(json.dumps({k: v for k, v in hw.items() if k != "max_turn_id"}).encode())
    return h.hexdigest()


def create_save_point(conn: sqlite3.Connection, cid: int, label: str, kind: str = "manual", turn_id: int | None = None,
                      keep_auto: int | None = None) -> int:
    """Call inside an open transaction (or standalone: it is a handful of reads plus one insert)."""
    snapshot = {t: _raw_rows(conn, sql, cid) for t, sql in SNAPSHOT_TABLES.items()}
    hw = _high_water(conn, cid)
    sp_id = conn.execute(
        """INSERT INTO save_points (campaign_id, label, kind, turn_id, max_message_id, max_event_id,
                                    max_turn_id, snapshot_json, created_at) VALUES (?,?,?,?,?,?,?,?,?)""",
        (cid, label, kind, turn_id, hw["max_message_id"], hw["max_event_id"], hw["max_turn_id"],
         json.dumps(snapshot), now_iso()),
    ).lastrowid
    if keep_auto is not None and kind == "auto":
        conn.execute(
            """DELETE FROM save_points WHERE campaign_id = ? AND kind = 'auto' AND id NOT IN
               (SELECT id FROM save_points WHERE campaign_id = ? AND kind = 'auto' ORDER BY id DESC LIMIT ?)""",
            (cid, cid, keep_auto),
        )
    return sp_id


def list_save_points(conn: sqlite3.Connection, cid: int) -> list[dict]:
    return rows(conn, """SELECT id, label, kind, turn_id, created_at, max_message_id FROM save_points
                         WHERE campaign_id = ? ORDER BY id DESC""", (cid,))


def restore_save_point(conn: sqlite3.Connection, cid: int, save_point_id: int) -> dict:
    sp = one(conn, "SELECT * FROM save_points WHERE id = ? AND campaign_id = ?", (save_point_id, cid))
    if not sp:
        raise KeyError(f"save point {save_point_id} not found for campaign {cid}")
    snapshot = sp["snapshot"]
    with transaction(conn):
        backup_id = create_save_point(conn, cid, f"Before restoring “{sp['label']}”", kind="pre_restore")
        conn.execute("PRAGMA defer_foreign_keys = ON")
        # Tables a save point predates (added in a later schema) are left as they are.
        for table in [t for t in _DELETE_ORDER if t in snapshot]:
            conn.execute(f"DELETE FROM {table} WHERE {_SCOPED_DELETE.get(table, 'campaign_id = ?')}", (cid,))
        camp = snapshot["campaigns"][0]
        conn.execute(f"UPDATE campaigns SET {', '.join(f'{k} = ?' for k in camp if k != 'id')} WHERE id = ?",
                     (*[v for k, v in camp.items() if k != "id"], cid))
        for table in [t for t in reversed(_DELETE_ORDER) if t in snapshot]:
            for r in snapshot.get(table, []):
                cols = list(r)
                conn.execute(f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                             [r[c] for c in cols])
        for table, col in _LOGS.items():
            conn.execute(f"UPDATE {table} SET rolled_back = 1 WHERE campaign_id = ? AND id > ?", (cid, sp[col]))
        conn.execute("INSERT INTO restore_log (campaign_id, save_point_id, backup_save_point_id, created_at) VALUES (?,?,?,?)",
                     (cid, save_point_id, backup_id, now_iso()))
    return {"restored": save_point_id, "backup_save_point_id": backup_id, "label": sp["label"]}
