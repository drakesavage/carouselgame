"""Read models for the API.

PLAYER functions must never read gm_secrets, visibility='gm' rows, hidden rolls or GM-only source sections.
tests/test_visibility.py plants unique secret strings and asserts none of them reach any player endpoint.
GM functions are only reachable through /api/gm (token-guarded).
"""
from __future__ import annotations

import sqlite3

from ..db import one, rows

PUBLIC_CHAR_COLUMNS = """id, campaign_id, slug, name, kind, roster_category, crawler_number, species, race, class_name, level,
    status, location, in_party, party_order, hp_current, hp_max, mana_current, mana_max, ai_favor, stats_json,
    derived_json, portrait_path, provenance, assignment"""


def floor_clock(camp: dict) -> dict:
    deadline = camp["floor_deadline_minutes"]
    elapsed = camp["game_time_minutes"]
    remaining = None if deadline is None else max(0, deadline - elapsed)
    return {"elapsed_minutes": elapsed, "deadline_minutes": deadline, "remaining_minutes": remaining,
            "remaining_display": None if remaining is None else f"{remaining // 60}h{remaining % 60:02d}m"}


def _statuses(conn: sqlite3.Connection, char_id: int) -> list[dict]:
    return rows(conn, "SELECT id, name, description, polarity, expires_at_minutes FROM status_effects WHERE character_id = ? ORDER BY id", (char_id,))


def character_card(conn: sqlite3.Connection, c: dict) -> dict:
    c = dict(c)
    c["statuses"] = _statuses(conn, c["id"])
    c["equipped"] = rows(conn, "SELECT id, name, slot FROM items WHERE owner_character_id = ? AND equipped = 1 ORDER BY id", (c["id"],))
    return c


# ------------------------------------------------------------------ player
def list_campaigns(conn: sqlite3.Connection) -> list[dict]:
    return rows(conn, "SELECT id, name, is_demo, data_source, current_floor, current_location, updated_at FROM campaigns ORDER BY id")


def campaign_summary(conn: sqlite3.Connection, cid: int) -> dict | None:
    camp = one(conn, """SELECT id, name, is_demo, data_source, current_floor, current_location, safe_room, game_time_minutes,
                               floor_deadline_minutes, active_character_id, active_objective, updated_at
                        FROM campaigns WHERE id = ?""", (cid,))
    if not camp:
        return None
    party = rows(conn, f"SELECT {PUBLIC_CHAR_COLUMNS} FROM characters WHERE campaign_id = ? AND in_party = 1 ORDER BY party_order, id", (cid,))
    camp["floor_clock"] = floor_clock(camp)
    camp["party"] = [character_card(conn, c) for c in party]
    camp["quests"] = rows(conn, """SELECT id, kind, title, description, status, priority, progress FROM quests
                                   WHERE campaign_id = ? AND visibility = 'player' AND status IN ('active','paused')
                                   ORDER BY priority, id""", (cid,))
    from ..rules.combat import public_encounter
    camp["encounter"] = public_encounter(one(conn, """SELECT id, name, round, summary, combatants_json FROM encounters
                                    WHERE campaign_id = ? AND status = 'active' AND visibility = 'player' ORDER BY id DESC LIMIT 1""", (cid,)), conn)
    camp["clocks"] = rows(conn, "SELECT key, label, kind, current, maximum, unit FROM clocks WHERE campaign_id = ? AND visibility = 'player'", (cid,))
    camp["last_save"] = one(conn, "SELECT id, label, kind, created_at FROM save_points WHERE campaign_id = ? ORDER BY id DESC LIMIT 1", (cid,))
    from ..rules.state_ops import day_info
    camp["day"] = day_info(conn, cid)
    from ..art.service import current_art
    camp["art"] = current_art(conn, cid)
    camp["scene"] = one(conn, """SELECT id, title, location, group_key, situation, conditions, threats, participants_json, started_game_minutes
                                 FROM scenes WHERE campaign_id = ? AND status = 'active' ORDER BY id DESC LIMIT 1""", (cid,))
    groups = rows(conn, "SELECT key, label, location, activity FROM party_groups WHERE campaign_id = ? AND status = 'active' ORDER BY id", (cid,))
    cards = {c["id"]: c for c in (character_card(conn, x) for x in rows(conn, f"SELECT {PUBLIC_CHAR_COLUMNS} FROM characters WHERE campaign_id = ? AND status NOT IN ('deceased','dead') ORDER BY party_order, name", (cid,)))}
    for g in groups:
        g["members"] = [c for c in cards.values() if c.get("assignment") == g["key"]]
    camp["groups"] = groups
    camp["roster"] = list(cards.values())
    camp["recent_events"] = rows(conn, """SELECT id, type, summary, game_time_minutes FROM events WHERE campaign_id = ? AND visibility = 'player' AND rolled_back = 0
                                          AND type IN ('achievement','level_awarded','skill_changed','quest_completed','quest_added','item_added','character_down',
                                                       'character_death','mob_defeated','advancement_checkpoint','encounter_start','encounter_end','group_moved',
                                                       'scene_started','location_changed','floor_collapse','dungeon_day')
                                          ORDER BY id DESC LIMIT 6""", (cid,))
    camp["quests"] = rows(conn, """SELECT id, kind, title, description, status, priority, progress, details_json, due_at_minutes FROM quests
                                   WHERE campaign_id = ? AND visibility = 'player' AND status IN ('active','paused') ORDER BY priority, id""", (cid,))
    return camp


def list_characters(conn: sqlite3.Connection, cid: int) -> list[dict]:
    return [character_card(conn, c) for c in rows(conn, f"SELECT {PUBLIC_CHAR_COLUMNS} FROM characters WHERE campaign_id = ? ORDER BY in_party DESC, party_order, name", (cid,))]


def character_sheet(conn: sqlite3.Connection, cid: int, char_id: int) -> dict | None:
    c = one(conn, f"SELECT {PUBLIC_CHAR_COLUMNS}, bio, sheet_json FROM characters WHERE campaign_id = ? AND id = ?", (cid, char_id))
    if not c:
        return None
    c = character_card(conn, c)
    c["skills"] = rows(conn, "SELECT id, kind, name, rank, mana_cost, passive, marked, description, properties_json FROM skills WHERE character_id = ? ORDER BY kind, rank DESC, name", (char_id,))
    c["items"] = rows(conn, "SELECT id, name, quantity, equipped, slot, rarity, description, properties_json FROM items WHERE owner_character_id = ? ORDER BY equipped DESC, name", (char_id,))
    c["achievements"] = rows(conn, "SELECT title, description, game_time_minutes FROM achievements WHERE character_id = ? ORDER BY id DESC", (char_id,))
    c["npc_profile"] = one(conn, "SELECT personality, voice, known_motivation, attitude, last_seen FROM npc_profiles WHERE character_id = ?", (char_id,))
    c["limited_uses"] = rows(conn, "SELECT ability, per, uses, uses_allowed, cooldown_minutes FROM ability_uses WHERE character_id = ? ORDER BY ability", (char_id,))
    c["relationships"] = rows(conn, """SELECT r.kind, r.description, a.name AS a_name, b.name AS b_name FROM relationships r
                                       JOIN characters a ON a.id = r.a_character_id JOIN characters b ON b.id = r.b_character_id
                                       WHERE r.visibility = 'player' AND (r.a_character_id = ? OR r.b_character_id = ?)""", (char_id, char_id))
    return c


def inventory(conn: sqlite3.Connection, cid: int) -> dict:
    items = rows(conn, """SELECT i.id, i.owner_character_id, c.name AS owner_name, i.name, i.quantity, i.equipped, i.deployed, i.slot,
                                 i.rarity, i.description, i.properties_json
                          FROM items i LEFT JOIN characters c ON c.id = i.owner_character_id
                          WHERE i.campaign_id = ? ORDER BY i.owner_character_id IS NOT NULL, c.name, i.equipped DESC, i.name""", (cid,))
    return {"shared": [i for i in items if i["owner_character_id"] is None],
            "by_character": [i for i in items if i["owner_character_id"] is not None]}


def quests(conn: sqlite3.Connection, cid: int) -> list[dict]:
    return rows(conn, "SELECT id, kind, title, description, status, priority, progress, giver, updated_at FROM quests WHERE campaign_id = ? AND visibility = 'player' ORDER BY status = 'active' DESC, priority, id", (cid,))


def npcs(conn: sqlite3.Connection, cid: int) -> list[dict]:
    out = rows(conn, f"""SELECT {PUBLIC_CHAR_COLUMNS}, n.personality, n.voice, n.known_motivation, n.attitude, n.last_seen
                         FROM characters c LEFT JOIN npc_profiles n ON n.character_id = c.id
                         WHERE c.campaign_id = ? AND c.id != COALESCE((SELECT active_character_id FROM campaigns WHERE id = ?), -1)
                         ORDER BY c.roster_category, c.name""", (cid, cid))
    return out


def timeline(conn: sqlite3.Connection, cid: int, limit: int = 300) -> list[dict]:
    return rows(conn, """SELECT id, turn_id, type, summary, game_time_minutes, created_at FROM events
                         WHERE campaign_id = ? AND visibility = 'player' AND rolled_back = 0 ORDER BY id DESC LIMIT ?""", (cid, limit))


def messages(conn: sqlite3.Connection, cid: int, before: int | None = None, limit: int = 80) -> list[dict]:
    sql = """SELECT id, turn_id, role, content, meta_json, created_at FROM messages
             WHERE campaign_id = ? AND visibility = 'player' AND rolled_back = 0"""
    params: list = [cid]
    if before:
        sql += " AND id < ?"
        params.append(before)
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(limit)
    return list(reversed(rows(conn, sql, params)))


def player_rolls(conn: sqlite3.Connection, cid: int, limit: int = 100) -> list[dict]:
    return rows(conn, """SELECT id, turn_id, character_id, expression, dice_json, modifier, total, target, outcome, degree, reason,
                                detail_json, rng, created_at FROM dice_rolls
                         WHERE campaign_id = ? AND hidden = 0 AND rolled_back = 0 ORDER BY id DESC LIMIT ?""", (cid, limit))


def house_rules(conn: sqlite3.Connection, cid: int) -> list[dict]:
    return rows(conn, "SELECT id, section, title, text, label, status, mechanical_status, source FROM house_rules WHERE campaign_id = ? ORDER BY id", (cid,))


def rulings(conn: sqlite3.Connection, cid: int) -> list[dict]:
    return rows(conn, "SELECT id, turn_id, topic, ruling, certainty, label, sources_json, created_at FROM rulings WHERE campaign_id = ? ORDER BY id DESC", (cid,))


def lore(conn: sqlite3.Connection, cid: int) -> list[dict]:
    return rows(conn, "SELECT id, subject, fact, certainty, source FROM lore_facts WHERE campaign_id = ? AND visibility = 'player' ORDER BY id", (cid,))


def player_records(conn: sqlite3.Connection, cid: int, q: str | None = None, limit: int = 30) -> list[dict]:
    if q:
        return rows(conn, """SELECT s.id, s.document_path, s.section, snippet(source_sections_fts, 1, '[', ']', ' … ', 40) AS snippet
                             FROM source_sections_fts JOIN source_sections s ON s.id = source_sections_fts.rowid
                             WHERE source_sections_fts MATCH ? AND s.campaign_id = ? AND s.visibility = 'player'
                             ORDER BY rank LIMIT ?""", (fts_query(q), cid, limit))
    return rows(conn, "SELECT id, document_path, section FROM source_sections WHERE campaign_id = ? AND visibility = 'player' ORDER BY id", (cid,))


def player_record(conn: sqlite3.Connection, cid: int, section_id: int) -> dict | None:
    return one(conn, "SELECT id, document_path, section, content FROM source_sections WHERE campaign_id = ? AND id = ? AND visibility = 'player'", (cid, section_id))


def fts_query(text: str) -> str:
    """Turn free text into a safe FTS5 OR-query of quoted terms."""
    terms = [t for t in "".join(ch if ch.isalnum() else " " for ch in text.lower()).split() if len(t) > 1]
    return " OR ".join(f'"{t}"' for t in terms[:24]) or '""'


# ------------------------------------------------------------------ GM only
def gm_secrets(conn: sqlite3.Connection, cid: int) -> list[dict]:
    return rows(conn, "SELECT id, subject, kind, content, revealed, source, created_at FROM gm_secrets WHERE campaign_id = ? ORDER BY revealed, id", (cid,))


def gm_messages(conn: sqlite3.Connection, cid: int, limit: int = 200) -> list[dict]:
    return list(reversed(rows(conn, "SELECT * FROM messages WHERE campaign_id = ? ORDER BY id DESC LIMIT ?", (cid, limit))))


def gm_events(conn: sqlite3.Connection, cid: int, limit: int = 300) -> list[dict]:
    return rows(conn, "SELECT * FROM events WHERE campaign_id = ? ORDER BY id DESC LIMIT ?", (cid, limit))


def gm_rolls(conn: sqlite3.Connection, cid: int, limit: int = 200) -> list[dict]:
    return rows(conn, "SELECT * FROM dice_rolls WHERE campaign_id = ? ORDER BY id DESC LIMIT ?", (cid, limit))


def gm_lore(conn: sqlite3.Connection, cid: int) -> list[dict]:
    return rows(conn, "SELECT * FROM lore_facts WHERE campaign_id = ? ORDER BY id", (cid,))


def gm_turns(conn: sqlite3.Connection, cid: int, limit: int = 50) -> list[dict]:
    return rows(conn, "SELECT id, idempotency_key, status, player_input, provider, error, rolled_back, created_at, completed_at FROM turns WHERE campaign_id = ? ORDER BY id DESC LIMIT ?", (cid, limit))
