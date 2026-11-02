"""Camp Dyer's residents in a campaign (design §10, NPC readme controls 1–8). Every campaign holds every resident template
from the start, unmet: an unmet resident is GM-side only (no player view lists their name, looks or role) until
introduce_resident meets them. What a met resident shows the players is `known_to_new_arrival.after_introduction` plus
what play has recorded. Provenance stays on every row; gm_only goes to the companion sheet, never to a player view."""
from __future__ import annotations

import json
import re
import sqlite3

from ..db import now_iso, one, rows
from .refdata import has_reference


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
