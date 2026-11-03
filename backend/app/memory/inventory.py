"""Reservations: an item held for someone but not owned yet. The rules engine refuses to add or equip it until the
reservation quest is resolved (rules/engine.py)."""
from __future__ import annotations

import re
import sqlite3

from ..db import rows
from .dossier import _norm


def pending_reservation(conn: sqlite3.Connection, cid: int, name: str) -> dict | None:
    """An active reservation for this item that hasn't been claimed: the item is not anyone's yet."""
    for q in rows(conn, "SELECT id, title, details_json FROM quests WHERE campaign_id = ? AND kind = 'reservation' AND status = 'active'", (cid,)):
        if _norm(re.sub(r"^Reserved:\s*", "", q["title"])) == _norm(name) and not (q.get("details") or {}).get("owned"):
            return q
    return None
