"""Read models for the API.

PLAYER functions must never read gm_secrets, visibility='gm' rows, hidden rolls or GM-only source sections.
tests/test_visibility.py plants unique secret strings and asserts none of them reach any player endpoint.
GM functions are only reachable through /api/gm (token-guarded).
"""
from __future__ import annotations

import re
import sqlite3

from ..db import one, rows

PUBLIC_CHAR_COLUMNS = """id, campaign_id, slug, name, kind, roster_category, species, status, location, in_party, party_order,
    role_id, aspect_id, advanced_role_id, role_reason, portrait_path, portrait_crop_json, provenance, assignment, stars, pending_stat_points"""


def _statuses(conn: sqlite3.Connection, char_id: int) -> list[dict]:
    return rows(conn, "SELECT id, name, description, polarity, expires_at_minutes FROM status_effects WHERE character_id = ? ORDER BY id", (char_id,))


LIFE_STATES = (  # (pattern on status / roster category, state); first match wins
    (r"dead|deceas|died|killed", "deceased"),
    (r"missing|lost", "missing"),
    (r"retir|dormant|inactive", "retired"),
    (r"contact|guest|external", "contact"),
)


def life_state(c: dict) -> dict:
    """Whether someone is around at all."""
    hay = f"{c.get('status') or ''} {c.get('roster_category') or ''} {c.get('kind') or ''}".lower()
    state = next((s for pat, s in LIFE_STATES if re.search(pat, hay)), "active")
    status = (c.get("status") or "").strip()
    label = "Deceased" if state == "deceased" else status.replace("_", " ").replace("-", " ").capitalize() if status and status != "active" else state.capitalize()
    return {"state": state, "label": label, "note": None}


def visible_tickets(conn: sqlite3.Connection, char_id: int) -> list[dict]:
    """Owned tickets the party knows about. Hidden ones (an NPC's unrevealed tropes) are left out entirely: no entry,
    no count, no placeholder."""
    from ..carousel.refdata import has_reference, ticket_definition
    out = []
    for t in rows(conn, "SELECT * FROM character_tickets WHERE character_id = ? AND hidden = 0 ORDER BY equipped DESC, id", (char_id,)):
        d = (ticket_definition(conn, t["ticket_ref"]) if has_reference(conn) else None) or {"name": t["ticket_ref"], "text": ""}
        out.append({"ref": t["ticket_ref"], "name": d["name"], "text": d["text"], "equipped": bool(t["equipped"]),
                    "uses_left": t["uses_left"], "uses_max": t["uses_max"], "recharge": t["recharge"]})
    return out


def character_card(conn: sqlite3.Connection, c: dict) -> dict:
    c = dict(c)
    c["life"] = life_state(c)
    c["statuses"] = _statuses(conn, c["id"])
    from ..carousel.sheet import sheet
    c["stats"] = sheet(conn, c["id"], show_hidden=False)   # Carousel stats and Plot Armor; c["sheet"] is the decoded sheet_json
    c["tickets"] = visible_tickets(conn, c["id"])
    c["equipped"] = rows(conn, "SELECT id, name, slot FROM items WHERE owner_character_id = ? AND equipped = 1 ORDER BY id", (c["id"],))
    return c


# ------------------------------------------------------------------ player
def list_campaigns(conn: sqlite3.Connection) -> list[dict]:
    return rows(conn, "SELECT id, name, is_demo, data_source, current_location, archived, updated_at FROM campaigns ORDER BY id")


def campaign_summary(conn: sqlite3.Connection, cid: int) -> dict | None:
    camp = one(conn, """SELECT id, name, is_demo, data_source, current_location, safe_room, game_time_minutes,
                               active_character_id, active_objective, objective_quests_json, updated_at
                        FROM campaigns WHERE id = ?""", (cid,))
    if not camp:
        return None
    party = rows(conn, f"SELECT {PUBLIC_CHAR_COLUMNS} FROM characters WHERE campaign_id = ? AND in_party = 1 ORDER BY party_order, id", (cid,))
    camp["party"] = [character_card(conn, c) for c in party]
    order = {q: n for n, q in enumerate(camp.pop("objective_quests") or [])}
    camp["clocks"] = rows(conn, "SELECT key, label, kind, current, maximum, unit FROM clocks WHERE campaign_id = ? AND visibility = 'player'", (cid,))
    camp["last_save"] = one(conn, "SELECT id, label, kind, created_at FROM save_points WHERE campaign_id = ? ORDER BY id DESC LIMIT 1", (cid,))
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
    quests = rows(conn, """SELECT id, kind, title, description, status, priority, progress, details_json, due_at_minutes FROM quests
                           WHERE campaign_id = ? AND visibility = 'player' AND status IN ('active','paused') ORDER BY priority, id""", (cid,))
    for q in quests:
        q["immediate"] = q["id"] in order and q["status"] == "active"
    # the immediate objective is its linked quests, first and in order; a finished quest drops out by itself
    camp["quests"] = sorted(quests, key=lambda q: (0, order[q["id"]]) if q["immediate"] else (1, 0))
    return camp


def list_characters(conn: sqlite3.Connection, cid: int) -> list[dict]:
    return [character_card(conn, c) for c in rows(conn, f"SELECT {PUBLIC_CHAR_COLUMNS} FROM characters WHERE campaign_id = ? ORDER BY in_party DESC, party_order, name", (cid,))]


def character_sheet(conn: sqlite3.Connection, cid: int, char_id: int) -> dict | None:
    c = one(conn, f"SELECT {PUBLIC_CHAR_COLUMNS}, bio, sheet_json FROM characters WHERE campaign_id = ? AND id = ?", (cid, char_id))
    if not c:
        return None
    c = character_card(conn, c)
    c["items"] = rows(conn, "SELECT id, name, quantity, equipped, slot, rarity, description, properties_json FROM items WHERE owner_character_id = ? ORDER BY equipped DESC, name", (char_id,))
    c["npc_profile"] = one(conn, "SELECT personality, voice, known_motivation, attitude, last_seen FROM npc_profiles WHERE character_id = ?", (char_id,))
    c["relationships"] = rows(conn, """SELECT r.kind, r.description, a.name AS a_name, b.name AS b_name FROM relationships r
                                       JOIN characters a ON a.id = r.a_character_id JOIN characters b ON b.id = r.b_character_id
                                       WHERE r.visibility = 'player' AND (r.a_character_id = ? OR r.b_character_id = ?)""", (char_id, char_id))
    return c


def inventory(conn: sqlite3.Connection, cid: int) -> dict:
    """Items with category, honest quantity and stage, plus Gold, reservations, projects and materials (memory/inventory.py)."""
    from .inventory import build
    return build(conn, cid)


def quests(conn: sqlite3.Connection, cid: int) -> list[dict]:
    return rows(conn, "SELECT id, kind, title, description, status, priority, progress, giver, updated_at FROM quests WHERE campaign_id = ? AND visibility = 'player' ORDER BY status = 'active' DESC, priority, id", (cid,))


# record-keeping, not something that happened between the party and a person
BOOKKEEPING_EVENTS = ("time_advanced", "correction", "trap_recorded", "item_added", "item_removed", "item_transferred",
                      "item_count_set", "quest_updated", "clock_changed", "visual_reference", "ledger_synced")


def npcs(conn: sqlite3.Connection, cid: int) -> list[dict]:
    """People as the party knows them: life state, whereabouts, promises and the last thing seen of them.
    Only player-visible quests, memories and events are read; GM secrets and private beats never enter."""
    from .dossier import _flat, _mentions
    out = rows(conn, f"""SELECT {PUBLIC_CHAR_COLUMNS}, c.sheet_json, n.personality, n.voice, n.known_motivation, n.attitude, n.last_seen
                         FROM characters c LEFT JOIN npc_profiles n ON n.character_id = c.id
                         WHERE c.campaign_id = ? AND c.id != COALESCE((SELECT active_character_id FROM campaigns WHERE id = ?), -1)
                         ORDER BY c.roster_category, c.name""", (cid, cid))
    groups = {g["key"]: g for g in rows(conn, "SELECT key, label, location, activity FROM party_groups WHERE campaign_id = ? AND status = 'active'", (cid,))}
    quests = rows(conn, """SELECT id, kind, title, description, progress, giver, details_json FROM quests
                           WHERE campaign_id = ? AND visibility = 'player' AND status IN ('active','paused') ORDER BY priority, id""", (cid,))
    events = rows(conn, f"""SELECT summary, game_time_minutes FROM events WHERE campaign_id = ? AND visibility = 'player' AND rolled_back = 0
                            AND type NOT IN ({','.join('?' * len(BOOKKEEPING_EVENTS))}) ORDER BY id DESC LIMIT 400""", (cid, *BOOKKEEPING_EVENTS))
    for p in out:
        sheet = p.pop("sheet", None) or {}
        narrative = sheet.get("narrative") if isinstance(sheet.get("narrative"), dict) else {}
        p["life"] = life_state(p)
        p["identity"] = sheet.get("identity") if isinstance(sheet.get("identity"), dict) else None
        # allegiance from the record: clan membership first, then the stated relationship, else the roster role (shown by the client)
        clan = sheet.get("clan_member")
        p["allegiance"] = (f"Denton clan{' (' + str(sheet['clan_role']) + ')' if sheet.get('clan_role') else ''}" if clan is True
                           else str(sheet["relationship_status"]).replace("-", " ").capitalize() if sheet.get("relationship_status") else None)
        notes = narrative.get("relationship_notes")
        p["relationship_notes"] = [str(n) for n in notes] if isinstance(notes, list) else [str(notes)] if notes else []
        g = groups.get(p.get("assignment") or "")
        p["group"] = {"label": g["label"], "location": g["location"], "activity": g["activity"]} if g else None
        p["where"] = (g or {}).get("location") or p.get("location")
        p["commitments"] = [{"id": q["id"], "kind": q["kind"], "title": q["title"]} for q in quests
                            if _mentions(" ".join(str(q.get(k) or "") for k in ("title", "description", "progress", "giver")) + " " + _flat(q.get("details")), p["name"])]
        mem = one(conn, """SELECT content, game_time_minutes FROM memories WHERE campaign_id = ? AND character_id = ? AND visibility = 'player'
                           ORDER BY id DESC LIMIT 1""", (cid, p["id"]))
        ev = next((e for e in events if _mentions(e["summary"] or "", p["name"])), None)
        last = mem and {"text": mem["content"], "game_time_minutes": mem["game_time_minutes"], "source": "memory"} \
            or ev and {"text": ev["summary"], "game_time_minutes": ev["game_time_minutes"], "source": "event"}
        p["last_interaction"] = last or ({"text": p["last_seen"], "game_time_minutes": None, "source": "profile"} if p.get("last_seen") else None)
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


def gm_lore(conn: sqlite3.Connection, cid: int) -> list[dict]:
    return rows(conn, "SELECT * FROM lore_facts WHERE campaign_id = ? ORDER BY id", (cid,))


def gm_turns(conn: sqlite3.Connection, cid: int, limit: int = 50) -> list[dict]:
    return rows(conn, "SELECT id, idempotency_key, status, player_input, provider, error, rolled_back, created_at, completed_at FROM turns WHERE campaign_id = ? ORDER BY id DESC LIMIT ?", (cid, limit))
