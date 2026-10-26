"""Which turns earn a correspondence-play advancement cycle (CORE_MECHANICS 14.3; state/advancement.yaml tracking rules).

A cycle needs a meaningful player decision AND a resolution that changed something. The GM proposes; the server
decides, so the count cannot drift (playtest 1, H2: lending bolas, walking home and every instruction inside one
fight each counted, and twelve such cycles triggered a premature Level checkpoint). A cycle is refused when:

* the player's message is a recap, status check, rules question, save or image request (do_not_increment_for);
* nothing consequential was committed this turn yet: only bookkeeping (time passing, moving, scene changes, rests,
  transfers, equipping, memories, notes) and no Check, purchase, discovery, damage, deal or other consequence
  ("count the cycle after resolving the decision");
* the turn already earned a cycle (one player message is at most one decision);
* the combat in progress, or one that ended this turn, already earned its cycle ("multiple rolls or Actions that
  merely resolve the same player decision").

Each counted cycle records the decision, the resolution, the player's words and the events that qualified it.
"""
from __future__ import annotations

import re
import sqlite3

from ..db import one, rows

CLOCK = "advancement_cycles"
NOT_A_DECISION = re.compile(r"\b(recap|previously on|status check|status\?|rules? question|how does .* work|save (here|the game|now)|"
                            r"let'?s save|draw|paint|picture of|portrait)\b", re.I)
ROUTINE = {"time_advanced", "group_moved", "group_updated", "character_moved", "location_changed", "scene_started", "scene_closed",
           "scene_updated", "rest", "mending", "hp_changed", "mana_changed", "item_transferred", "item_equipped", "item_unequipped",
           "memory_recorded", "gm_note", "status_expired", "dungeon_day", "clock_changed", "advancement_checkpoint_pending",
           "trap_recorded", "art_suggested"}


def eligibility(conn: sqlite3.Connection, cid: int, turn_id: int | None) -> tuple[bool, str, list[dict]]:
    """(eligible, reason, qualifying events) for counting one cycle on this turn."""
    if turn_id is None:
        return False, "cycles are counted only during play turns", []
    turn = one(conn, "SELECT player_input FROM turns WHERE id = ?", (turn_id,)) or {}
    words = turn.get("player_input") or ""
    if NOT_A_DECISION.search(words):
        return False, "the player's message is a recap, status, rules, save or image request, not a decision (CORE 14.3)", []
    events = rows(conn, "SELECT id, type, summary, payload_json FROM events WHERE campaign_id = ? AND turn_id = ? AND rolled_back = 0 ORDER BY id",
                  (cid, turn_id))
    if any(e["type"] == "clock_changed" and (e.get("payload") or {}).get("key") == CLOCK and (e.get("payload") or {}).get("after", 0) >
           (e.get("payload") or {}).get("before", 0) for e in events):
        return False, "this turn already earned its cycle; one player message is at most one decision", []
    enc = one(conn, "SELECT id FROM encounters WHERE campaign_id = ? AND status = 'active' ORDER BY id DESC LIMIT 1", (cid,))
    ended = next(((e.get("payload") or {}).get("encounter_id") for e in events if e["type"] == "encounter_end"), None)
    for eid in [x for x in ((enc or {}).get("id"), ended) if x]:
        if one(conn, "SELECT 1 AS x FROM events WHERE campaign_id = ? AND type = 'clock_changed' AND rolled_back = 0 "
                     "AND json_extract(payload_json, '$.key') = ? AND json_extract(payload_json, '$.encounter_id') = ?", (cid, CLOCK, eid)):
            return False, "this combat already earned its cycle; further instructions resolve the same decision", []
    rolled = one(conn, "SELECT count(*) AS n FROM dice_rolls WHERE campaign_id = ? AND turn_id = ? AND rolled_back = 0 AND hidden = 0", (cid, turn_id))["n"]
    consequential = [e for e in events if e["type"] not in ROUTINE]
    if not consequential and not rolled:
        return False, ("nothing consequential has been resolved this turn (only time, movement, scenes, rests, transfers or notes); "
                       "routine bookkeeping does not earn a cycle, and a cycle is counted after its resolution"), []
    return True, "decision with a consequential resolution", consequential[:8]


def encounter_for_cycle(conn: sqlite3.Connection, cid: int, turn_id: int | None) -> int | None:
    enc = one(conn, "SELECT id FROM encounters WHERE campaign_id = ? AND status = 'active' ORDER BY id DESC LIMIT 1", (cid,))
    if enc:
        return enc["id"]
    if turn_id is None:
        return None
    e = one(conn, "SELECT payload_json FROM events WHERE campaign_id = ? AND turn_id = ? AND type = 'encounter_end' ORDER BY id DESC", (cid, turn_id))
    return (e.get("payload") or {}).get("encounter_id") if e else None
