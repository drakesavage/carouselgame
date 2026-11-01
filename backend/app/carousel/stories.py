"""Carousel stories: the Plot Cycle's fixed names, the sealed packet taken from a beat dossier at Choice, which tickets may
revise a screenplay, and how new a story still is to this campaign. The ops that change story state are in rules/story_ops.py."""
from __future__ import annotations

import hashlib
import json
import sqlite3

from ..db import one

PHASES: tuple[str, ...] = ("Omen", "Choice", "Party", "First Blood", "Rebirth", "Second Blood", "Finale", "The End")
ENDINGS: tuple[str, ...] = ("full_success", "costly_success", "survive_without_victory", "defeat")
DOSSIER_ENDING = {"full_success": "full_success", "costly_success": "costly_success", "survival_only": "survive_without_victory", "failure": "defeat"}
CASTINGS = ("lead", "support", "witness", "sacrifice", "outsider")
REVISING_TAGS = {"story_alteration", "story_structure", "win_condition"}
REVISING_SCOPE = "preproduction_or_live_story_mutation"


def active_story(conn: sqlite3.Connection, cid: int) -> dict | None:
    return one(conn, "SELECT * FROM stories WHERE campaign_id = ? AND status = 'active'", (cid,))


def canonical(value) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False)


def seal(dossier: dict) -> tuple[str, str]:
    """(sealed_json, sha256): the dossier as canonical JSON, so key order never changes the hash."""
    text = canonical(dossier)
    return text, hashlib.sha256(text.encode("utf-8")).hexdigest()


def endings_from(dossier: dict) -> dict[str, dict]:
    """The dossier's outcome profiles under the design's ending names."""
    return {DOSSIER_ENDING[k]: v for k, v in (dossier.get("outcomes") or {}).items() if k in DOSSIER_ENDING}


def ticket_can_revise(conn: sqlite3.Connection, cid: int, ticket_ref: str) -> bool:
    """Whether a ticket may revise a live screenplay: its definition says it alters stories (flag, tag or scope), or it is a
    trope with an owner-approved ruling tied to it."""
    if ticket_ref.startswith("trope:"):
        return one(conn, "SELECT 1 AS x FROM rulings WHERE campaign_id = ? AND ticket_ref = ? AND review = 'approved'", (cid, ticket_ref)) is not None
    r = one(conn, "SELECT raw_json FROM ref.tickets WHERE id = ?", (ticket_ref,))
    if not r:
        return False
    raw = r["raw"]
    defn = raw.get("original_ticket_definition") or raw.get("shared_pool_entry") or {}
    return defn.get("supports_story_mutation") is True or defn.get("tag") in REVISING_TAGS or defn.get("scope") == REVISING_SCOPE


def novelty(conn: sqlite3.Connection, cid: int, story_lock: str) -> int:
    """Novelty stars: 2 on the first concluded run of a story in this campaign, 1 on the second, 0 after; minus 1 per confirmed spoiler."""
    runs = one(conn, "SELECT COUNT(*) AS n FROM stories WHERE campaign_id = ? AND story_lock = ? AND status = 'concluded'", (cid, story_lock))["n"]
    spoilers = one(conn, "SELECT COUNT(*) AS n FROM story_spoilers WHERE campaign_id = ? AND story_lock = ?", (cid, story_lock))["n"]
    return max(0, max(0, 2 - runs) - spoilers)
