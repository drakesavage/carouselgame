"""The new-game opening (design §9): a forward-only stage on the campaign row, car → silas → opening_omen → escort → done.
NULL means the campaign has no opening (seeded and legacy campaigns) and every opening guard is off. Only the
`advance_opening` op (rules/opening_ops.py) moves the stage, and only when blockers() is empty."""
from __future__ import annotations

import re
import sqlite3

from ..db import one, rows

STAGES = ("car", "silas", "opening_omen", "escort", "done")
ARRIVAL_LOT = "CAR-082"   # the Eastern arrival parking lot: the vast lot of long-abandoned cars
LODGE = "CAR-047"


def stage(conn: sqlite3.Connection, cid: int) -> str | None:
    r = one(conn, "SELECT opening_stage FROM campaigns WHERE id = ?", (cid,))
    return r["opening_stage"] if r else None


def friends(conn: sqlite3.Connection, cid: int) -> list[dict]:
    """The GM-made friends in the car: active in-party NPCs that are not imported residents."""
    return rows(conn, """SELECT c.* FROM characters c LEFT JOIN npc_profiles p ON p.character_id = c.id
                         WHERE c.campaign_id = ? AND c.kind = 'npc' AND c.in_party = 1 AND c.status = 'active' AND p.ref_id IS NULL
                         ORDER BY c.id""", (cid,))


_QUOTES = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"'})


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s.translate(_QUOTES)).strip().lower()


def quote_found(conn: sqlite3.Connection, cid: int, quote: str) -> bool:
    """The quote was said: it appears in a player-visible player or GM message of this campaign."""
    q = _norm(quote)
    return bool(q) and any(q in _norm(r["content"]) for r in rows(
        conn, "SELECT content FROM messages WHERE campaign_id = ? AND role IN ('player', 'gm') AND visibility = 'player' AND rolled_back = 0",
        (cid,)))


def next_stage(current: str | None) -> str | None:
    if current not in STAGES or current == STAGES[-1]:
        return None
    return STAGES[STAGES.index(current) + 1]


def blockers(conn: sqlite3.Connection, cid: int, reason: str | None = None) -> list[str]:
    """What stops the next stage, one plain line per problem (empty: ready). `reason` is the GM's word that the player
    refused every Omen, which lets the opening Omen pass without a story."""
    s = stage(conn, cid)
    if s is None:
        return ["this campaign has no opening"]
    camp = one(conn, "SELECT * FROM campaigns WHERE id = ?", (cid,))
    out: list[str] = []
    if s == "car":
        if not camp["lure_kind"]:
            out.append("the lure isn't set (set_lure)")
        n = len(friends(conn, cid))
        if n != 3:
            out.append(f"{n} friend{'' if n == 1 else 's'}; the car holds 3")
    elif s == "silas":
        group = [one(conn, "SELECT * FROM characters WHERE id = ?", (camp["active_character_id"],)), *friends(conn, cid)]
        out += [f"{c['name']} has no Archetype yet" for c in group if c and not c["role_id"]]
    elif s == "opening_omen":
        from .stories import active_story
        running = active_story(conn, cid)
        if running:
            out.append(f"a story is still running: {running['title']}")
        elif not one(conn, "SELECT id FROM stories WHERE campaign_id = ?", (cid,)) and not (reason or "").strip():
            # begin_story is refused before this stage, so any story in the campaign started during the opening
            out.append("no story has started; give a reason if the player refused every Omen")
    elif s == "escort":
        if camp["node_id"] != LODGE:
            out.append("the group isn't at Dyer's Lodge yet")
        if not camp["lure_paid_off"]:
            out.append("the lure isn't paid off (pay_off_lure)")
        if not one(conn, """SELECT c.id FROM characters c JOIN npc_profiles p ON p.character_id = c.id
                            WHERE c.campaign_id = ? AND c.in_party = 1 AND c.status = 'active' AND p.ref_id IS NOT NULL""", (cid,)):
            out.append("no veteran walks with the group (introduce_resident, in_party)")
    else:
        out.append("nothing comes after done")
    return out
