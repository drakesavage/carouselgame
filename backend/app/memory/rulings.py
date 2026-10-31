"""GM rulings and the owner's review of them. Approved rulings (or ones the owner recorded as BOOK / CAMPAIGN canon) are
precedent in later turns; provisional ones (INTERPRETATION, ONE-SCENE, OPEN) are listed but never cited as precedent;
rejected ones are dropped."""
from __future__ import annotations

import sqlite3

from ..db import rows

PRECEDENTS = ("BOOK", "CAMPAIGN")


def ruling_status(r: dict) -> str:
    """precedent | provisional | rejected: only precedents are cited in later turns."""
    if r.get("review") == "rejected":
        return "rejected"
    if r.get("review") == "approved" or (r.get("label") in PRECEDENTS and r.get("certainty") == "canonical"):
        return "precedent"
    return "provisional"


def rulings(conn: sqlite3.Connection, cid: int) -> list[dict]:
    out = rows(conn, """SELECT id, turn_id, topic, ruling, certainty, label, sources_json, ticket_ref, review, review_note, reviewed_at, created_at
                        FROM rulings WHERE campaign_id = ? ORDER BY id DESC""", (cid,))
    for r in out:
        r["status"] = ruling_status(r)
    return out
