"""Fate (design §8, System Bible §11): five separate axes per character, never inferred from one another.

physical_condition alive|injured|dying|dead|uncertain · wallpaper_status active|off_screen|dead|written_off|unknown ·
participation full|limited|observer|posthumous_limited|none (computed here, never set directly) ·
persistent_fate ongoing|restored|missing_postered|permanently_lost|unresolved · rescue_eligibility (GM-only)
not_applicable|possible|active|completed|failed|ineligible. The ops that change them are in rules/fate_ops.py."""
from __future__ import annotations

import sqlite3

from ..db import one
from .opening import LODGE
from .stories import active_story

GONE = ("deceased", "dead", "missing", "lost")   # characters.status values that are out of play
GONE_SQL = "(" + ",".join(f"'{s}'" for s in GONE) + ")"
DINER = "CAR-006"                                # the missing-poster board is outside the Diner
CAMP_ONLY = "You can only save at camp (Dyer's Lodge, between stories)"


def rights(physical: str, wallpaper: str, basis: str | None) -> str:
    """What a character may still do in the story, from the body and the wallpaper (and an open posthumous window)."""
    if wallpaper == "written_off":
        return "none"
    if physical == "dead":
        return "posthumous_limited" if basis else "observer"
    if physical in ("dying", "uncertain") or wallpaper in ("dead", "off_screen", "unknown"):
        return "limited"
    return "full"


def set_rights(conn: sqlite3.Connection, character_id: int) -> str:
    """Recompute and store participation. A permanently lost character takes no part, whatever the other axes say."""
    c = one(conn, "SELECT physical_condition, wallpaper_status, rights_basis, persistent_fate FROM characters WHERE id = ?", (character_id,))
    p = "none" if c["persistent_fate"] == "permanently_lost" else rights(c["physical_condition"], c["wallpaper_status"], c["rights_basis"])
    conn.execute("UPDATE characters SET participation = ? WHERE id = ?", (p, character_id))
    return p


def at_camp(conn: sqlite3.Connection, cid: int) -> bool:
    """Save points are made only here: Dyer's Lodge, between stories, after the opening. No map at all counts as camp."""
    camp = one(conn, "SELECT node_id, opening_stage FROM campaigns WHERE id = ?", (cid,))
    if not camp:
        return False
    if camp["node_id"] is None:
        return True
    return camp["node_id"] == LODGE and camp["opening_stage"] in (None, "done") and not active_story(conn, cid)


def deathwatch(conn: sqlite3.Connection, cid: int) -> dict | None:
    """{"story_id", "pace"} while the player's character is in the running story's cast but can't act."""
    s = active_story(conn, cid)
    if not s:
        return None
    c = one(conn, """SELECT c.participation FROM campaigns k JOIN characters c ON c.id = k.active_character_id
                     JOIN story_cast x ON x.character_id = c.id AND x.story_id = ? WHERE k.id = ?""", (s["id"], cid))
    if not c or c["participation"] not in ("observer", "none"):
        return None
    return {"story_id": s["id"], "pace": s["deathwatch_pace"]}


def ticket_kinds(conn: sqlite3.Connection, ticket_ref: str) -> set[str]:
    """A trope ticket's kinds: its variants' trope_type parts (split on "/") and tags. Other tickets have none."""
    if not ticket_ref.startswith("trope:"):
        return set()
    t = one(conn, "SELECT variants_json FROM ref.tropes WHERE id = ?", (ticket_ref.split(":", 1)[1],))
    out: set[str] = set()
    for v in (t or {}).get("variants") or []:
        out |= {p.strip() for p in (v.get("trope_type") or "").split("/") if p.strip()}
        out |= {str(tag).strip() for tag in v.get("tags") or []}
    return out
