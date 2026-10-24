"""Turn-integrity report: heuristic flags where the narration and the validated state changes disagree.

These are warnings for the GM diagnostics panel, not hard failures: the GM's narration never changes state by
itself (only validated tools do), so a flag means "the story may claim something the ledger didn't record".
"""
from __future__ import annotations

import re
import sqlite3

from ..db import rows

RECAP = re.compile(r"\b(recap|previously on|rules question|what do i know|status check|save point|where are we)\b", re.I)


def _shingles(text: str, n: int = 8) -> set[str]:
    words = re.findall(r"[a-z0-9']+", text.lower())
    return {" ".join(words[i:i + n]) for i in range(max(0, len(words) - n + 1))}


def check(conn: sqlite3.Connection, cid: int, player_input: str, narration: str, changes: list[str], rolls: list[dict],
          errors: list[str], notices: list[dict]) -> list[dict]:
    flags: list[dict] = []
    text = narration + " " + " ".join(n.get("text", "") for n in notices)
    ch = " ".join(changes).lower()

    def flag(level: str, msg: str) -> None:
        flags.append({"level": level, "message": msg})

    for e in errors:
        flag("warn", f"A requested change was rejected: {e[:240]}")
    if re.search(r"\b\d[\d,]*\s+gold\b", text, re.I) and re.search(r"\b(pay|paid|spend|spent|cost|buy|bought|earn|loot|receive)", text, re.I) \
            and "gold" not in ch:
        flag("warn", "Narration mentions spending or gaining Gold, but no Gold change was recorded.")
    if re.search(r"\b(heal(?:s|ed)?|lose[s]?|take[s]?|took|suffer(?:s|ed)?)\s+\d+\s*(damage|hp|health|slots?)\b", text, re.I) \
            and not re.search(r"\bhp\b|health bar|slot|heals|loses", ch):
        flag("warn", "Narration describes damage or healing, but no Health change was recorded.")
    if re.search(r"\b\d+\s*(minutes?|hours?)\s+(pass|later|go by|elapse)", text, re.I) and "minutes pass" not in ch:
        flag("warn", "Narration says time passed, but the floor clock did not advance.")
    if re.search(r"\b(you|cliff)\s+(receive|obtain|acquire|pocket|pick(?:s)? up|buy|bought)\b", text, re.I) and not re.search(r"receives|gains \d|stash gains", ch):
        flag("info", "Narration may describe acquiring an item, but no item was added.")
    produced = {str(r.get("total")) for r in rolls} | {str((r.get("detail") or {}).get("natural")) for r in rolls}
    for m in re.finditer(r"\bd20\s*\(?(\d{1,2})\)?", text, re.I):
        if m.group(1) not in produced:
            flag("warn", f"Narration quotes a d20 result ({m.group(1)}) that the server did not roll this turn.")
    if RECAP.search(player_input) and "advancement cycles" in ch:
        flag("warn", "An advancement cycle was counted on what looks like a recap/status/rules turn (CORE_MECHANICS 14.3).")
    secret_text = " ".join(s["content"] for s in rows(conn, "SELECT content FROM gm_secrets WHERE campaign_id = ? AND revealed = 0", (cid,)))
    leak = _shingles(text) & _shingles(secret_text)
    if leak:
        flag("warn", f"Narration repeats wording from a GM-only record ({len(leak)} 8-word run(s)); check for an accidental reveal.")
    return flags
