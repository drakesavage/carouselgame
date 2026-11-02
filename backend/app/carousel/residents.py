"""Camp Dyer's residents in a campaign (design §10, NPC readme controls 1–8). Every campaign holds every resident template
from the start, unmet: an unmet resident is GM-side only (no player view lists their name, looks or role) until
introduce_resident meets them. What a met resident shows the players is `known_to_new_arrival.after_introduction` plus
what play has recorded. Provenance stays on every row; gm_only goes to the companion sheet, never to a player view."""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
from pathlib import Path

from ..db import now_iso, one, rows
from . import clock
from .fate import GONE
from .refdata import has_reference
from .stories import active_story

HANGOUTS_FILE = Path(__file__).with_name("resident_hangouts.json")


def unmet_clause(alias: str = "c") -> str:
    """SQL: the character `alias` is not an unmet resident. Every player view that lists characters adds it."""
    return f"NOT EXISTS (SELECT 1 FROM npc_profiles u WHERE u.character_id = {alias}.id AND u.met = 0)"


def _slug(conn: sqlite3.Connection, cid: int, name: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "resident"
    slug, n = base, 2
    while one(conn, "SELECT id FROM characters WHERE campaign_id = ? AND slug = ?", (cid, slug)):
        slug, n = f"{base}-{n}", n + 1
    return slug


def by_ref(conn: sqlite3.Connection, cid: int) -> dict[str, int]:
    """{template id: character id} for the residents this campaign holds."""
    return {r["ref_id"]: r["id"] for r in rows(conn, """SELECT c.id, p.ref_id FROM characters c JOIN npc_profiles p ON p.character_id = c.id
                                                       WHERE c.campaign_id = ? AND p.ref_id IS NOT NULL""", (cid,))}


def ensure(conn: sqlite3.Connection, cid: int) -> int:
    """Insert every resident template the campaign lacks, unmet, and the relationships between residents (GM-only).
    Returns how many residents it added. Call inside a transaction."""
    if not has_reference(conn):
        return 0
    have = by_ref(conn, cid)
    t = now_iso()
    added = 0
    for tpl in rows(conn, "SELECT * FROM ref.npc_templates ORDER BY id"):
        if tpl["id"] in have:
            continue
        public = tpl["player"] or {}
        slug = _slug(conn, cid, tpl["name"])
        sheet = {"narrative": {"current_role": tpl["camp_role"]}} if tpl["camp_role"] else {}
        char_id = conn.execute(
            """INSERT INTO characters (campaign_id, slug, name, kind, roster_category, status, in_party, bio, sheet_json, provenance)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (cid, slug, tpl["name"], "npc", "allied_npcs", "active", 0, public.get("appearance"), json.dumps(sheet), f"resident {tpl['id']}")).lastrowid
        conn.execute("INSERT INTO npc_profiles (character_id, personality, ref_id, met) VALUES (?,?,?,0)", (char_id, public.get("personality"), tpl["id"]))
        conn.execute("INSERT INTO gm_secrets (campaign_id, subject, kind, content, source, created_at) VALUES (?,?,?,?,?,?)",
                     (cid, slug, "companion_sheet", json.dumps(tpl["gm"], ensure_ascii=False), "resident template", t))
        known = (public.get("known_to_new_arrival") or {}).get("after_introduction") or []
        conn.execute("INSERT INTO gm_secrets (campaign_id, subject, kind, content, source, created_at) VALUES (?,?,?,?,?,?)",
                     (cid, slug, "resident_public", json.dumps(known, ensure_ascii=False), "resident template", t))
        have[tpl["id"]] = char_id
        added += 1
    if added:
        _relationships(conn, cid, have)
    return added


def _relationships(conn: sqlite3.Connection, cid: int, have: dict[str, int]) -> None:
    """Template relationships between residents both in the campaign, GM-only; meeting a resident doesn't reveal them."""
    existing = {(r["a_character_id"], r["b_character_id"], r["kind"]) for r in rows(
        conn, "SELECT a_character_id, b_character_id, kind FROM relationships WHERE campaign_id = ?", (cid,))}
    for tpl in rows(conn, "SELECT id, player_json FROM ref.npc_templates ORDER BY id"):
        a = have.get(tpl["id"])
        for rel in (tpl["player"] or {}).get("relationships") or []:
            b = have.get(rel.get("with"))
            kind = rel.get("type") or "acquaintance"
            if not a or not b or (a, b, kind) in existing:
                continue
            conn.execute("INSERT INTO relationships (campaign_id, a_character_id, b_character_id, kind, description, visibility) VALUES (?,?,?,?,?,?)",
                         (cid, a, b, kind, rel.get("basis"), "gm"))
            existing.add((a, b, kind))


def known_lines(conn: sqlite3.Connection, cid: int, slug: str) -> list[str]:
    """`after_introduction` for one resident (stored GM-side; the player sees it once met, as lore facts)."""
    r = one(conn, "SELECT content FROM gm_secrets WHERE campaign_id = ? AND kind = 'resident_public' AND subject = ?", (cid, slug))
    return json.loads(r["content"]) if r else []


def unmet_ids(conn: sqlite3.Connection, cid: int) -> set[int]:
    return {r["id"] for r in rows(conn, """SELECT c.id FROM characters c JOIN npc_profiles p ON p.character_id = c.id
                                          WHERE c.campaign_id = ? AND p.met = 0""", (cid,))}


def in_briefing(conn: sqlite3.Connection, cid: int, chars: list[dict]) -> list[dict]:
    """The characters the briefing may list: unmet residents only when they are around the group."""
    unmet = unmet_ids(conn, cid)
    if not unmet:
        return chars
    near: set[int] = set()
    return [c for c in chars if c["id"] not in unmet or c["id"] in near]


def unmeet_all(conn: sqlite3.Connection, cid: int) -> None:
    """A new group (design §8.1) knows nobody: every resident is unmet again; memories and relationships stay."""
    conn.execute("""UPDATE npc_profiles SET met = 0 WHERE ref_id IS NOT NULL
                    AND character_id IN (SELECT id FROM characters WHERE campaign_id = ?)""", (cid,))
    conn.execute("""UPDATE characters SET in_party = 0 WHERE campaign_id = ?
                    AND id IN (SELECT character_id FROM npc_profiles WHERE ref_id IS NOT NULL)""", (cid,))


# ---------------------------------------------------------------- schedules
# Where a resident is comes from overrides first (party, story, group, a GM note), then the daily schedule: home at night,
# in the morning and in the evening, an afternoon hangout among the safer places. Nothing moves off-screen (design §10).
_TABLES: dict[tuple, dict] = {}


def _norm(text: str | None) -> str:
    return (text or "").lower().replace("’", "'")


def _ref_path(conn: sqlite3.Connection) -> str:
    dbs = {r[1]: r[2] for r in conn.execute("PRAGMA database_list")}
    return dbs.get("ref") or dbs.get("main") or ""


def hangout_table(conn: sqlite3.Connection) -> dict:
    """resident_hangouts.json checked against the reference data (a campaign connection, or reference.db itself):
    {"default_home", "homes", "usual_hangouts", "affiliations", "report"}. An entry whose place is missing is dropped; one
    whose zone is missing (or isn't at that place) keeps its place without a zone. Each is a line in "report".
    Loaded once per reference DB and file version."""
    path = _ref_path(conn)
    key = (path, os.path.getmtime(path) if path and os.path.exists(path) else 0, HANGOUTS_FILE.stat().st_mtime_ns)
    if key in _TABLES:
        return _TABLES[key]
    from .import_places import resolve_place
    raw = json.loads(HANGOUTS_FILE.read_text(encoding="utf-8"))
    report: list[str] = []

    def check(entry: dict, where: str) -> dict | None:
        place = resolve_place(conn, entry["place"])
        if not place:
            report.append(f"{where}: place {entry['place']} is not in the reference data; dropped")
            return None
        zone = entry.get("zone")
        if zone:
            z = conn.execute("SELECT venue_id FROM venue_zones WHERE id = ?", (zone,)).fetchone()
            v = conn.execute("SELECT venue_id FROM venue_places WHERE place_id = ?", (place,)).fetchone()
            if not z or not v or z[0] != v[0]:
                report.append(f"{where}: zone {zone} is not at {place} in the reference data; kept the place without a zone")
                zone = None
        return {"place": place, "zone": zone}

    def matched(section: str) -> list[dict]:
        out = []
        for e in raw.get(section) or []:
            c = check(e, f"{section} {e['match']!r}")
            if c:
                out.append({"match": _norm(e["match"]), **c})
        return out

    table = {"default_home": check(raw["default_home"], "default_home"), "homes": matched("homes"), "usual_hangouts": matched("usual_hangouts"),
             "affiliations": {_norm(k): [c for c in (check(e, f"affiliation {k}") for e in v) if c] for k, v in (raw.get("affiliations") or {}).items()},
             "report": report}
    _TABLES.clear()
    _TABLES[key] = table
    return table


def _player(tpl: dict) -> dict:
    return tpl["player"] if isinstance(tpl.get("player"), dict) else json.loads(tpl.get("player_json") or "{}")


def home(conn: sqlite3.Connection, tpl: dict) -> dict:
    """{"place", "zone", "why": "home" | "default_home"} for a resident template (its usual_location against `homes`)."""
    table = hangout_table(conn)
    loc = _norm(tpl.get("usual_location"))
    for h in table["homes"]:
        if h["match"] in loc:
            return {"place": h["place"], "zone": h["zone"], "why": "home"}
    return {**(table["default_home"] or {"place": None, "zone": None}), "why": "default_home"}


def hangouts(conn: sqlite3.Connection, tpl: dict) -> list[dict]:
    """[{"place", "zone"}]: the template's usual_location matches, then its affiliation's list, duplicates removed."""
    table = hangout_table(conn)
    loc = _norm(tpl.get("usual_location"))
    out: list[dict] = []
    for h in [h for h in table["usual_hangouts"] if h["match"] in loc] + table["affiliations"].get(_norm(_player(tpl).get("affiliation")), []):
        e = {"place": h["place"], "zone": h["zone"]}
        if e not in out:
            out.append(e)
    return out


def _group_position(conn: sqlite3.Connection, cid: int) -> tuple[str | None, str | None]:
    """Where the player character's group stands: its party_groups row when it has one with a node, else the campaign's."""
    camp = one(conn, "SELECT node_id, zone_id, active_character_id FROM campaigns WHERE id = ?", (cid,))
    pc = one(conn, "SELECT assignment FROM characters WHERE id = ?", (camp["active_character_id"],)) if camp["active_character_id"] else None
    g = one(conn, "SELECT node_id, zone_id FROM party_groups WHERE campaign_id = ? AND key = ? AND status = 'active'",
            (cid, pc["assignment"])) if pc and pc["assignment"] else None
    return (g["node_id"], g["zone_id"]) if g and g["node_id"] else (camp["node_id"], camp["zone_id"])


def _at(place: str | None, zone: str | None, why: str) -> dict:
    return {"place_id": place, "zone_id": zone, "why": why}


def where(conn: sqlite3.Connection, cid: int, character_id: int) -> dict | None:
    """{"place_id", "zone_id" | None, "why": party|story|group|note|home|hangout}; None when the character is GONE (or is
    not a resident and nothing places them). First match wins: party, the running story's cast, a group assignment, an
    unexpired resident_whereabouts note, then the schedule."""
    c = one(conn, """SELECT c.id, c.status, c.in_party, c.assignment, p.ref_id FROM characters c LEFT JOIN npc_profiles p ON p.character_id = c.id
                     WHERE c.campaign_id = ? AND c.id = ?""", (cid, character_id))
    if not c or c["status"] in GONE:
        return None
    if c["in_party"]:
        return _at(*_group_position(conn, cid), "party")
    s = active_story(conn, cid)
    if s and one(conn, "SELECT 1 AS x FROM story_cast WHERE story_id = ? AND character_id = ?", (s["id"], character_id)):
        return _at(*_group_position(conn, cid), "story")
    if c["assignment"]:
        g = one(conn, "SELECT node_id, zone_id FROM party_groups WHERE campaign_id = ? AND key = ? AND status = 'active'", (cid, c["assignment"]))
        if g and g["node_id"]:
            return _at(g["node_id"], g["zone_id"], "group")
    now = one(conn, "SELECT game_time_minutes FROM campaigns WHERE id = ?", (cid,))["game_time_minutes"]
    note = one(conn, "SELECT place_id, zone_id FROM resident_whereabouts WHERE campaign_id = ? AND character_id = ? AND until_minutes > ?",
               (cid, character_id, now))
    if note:
        return _at(note["place_id"], note["zone_id"], "note")
    tpl = one(conn, "SELECT id, usual_location, player_json FROM ref.npc_templates WHERE id = ?", (c["ref_id"],)) \
        if c["ref_id"] and has_reference(conn) else None
    if not tpl:
        return None
    if clock.slot(now) == "afternoon":
        spots = hangouts(conn, tpl)
        if spots:
            pick = spots[int(hashlib.sha256(f"{c['ref_id']}:{clock.day(now)}".encode()).hexdigest(), 16) % len(spots)]
            return _at(pick["place"], pick["zone"], "hangout")
    h = home(conn, tpl)
    return _at(h["place"], h["zone"], "home")
