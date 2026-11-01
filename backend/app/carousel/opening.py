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


def blockers(conn: sqlite3.Connection, cid: int) -> list[str]:
    """What stops the next stage, one plain line per problem (empty: ready)."""
    return []
