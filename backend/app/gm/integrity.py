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
          errors: list[str], notices: list[dict], turn_id: int | None = None) -> list[dict]:
    flags: list[dict] = []
    text = narration + " " + " ".join(n.get("text", "") for n in notices)
    ch = " ".join(changes).lower()

    recap = bool(RECAP.search(player_input))

    def flag(level: str, msg: str, recounting: bool = False) -> None:
        # On a recap/status turn the narration retells past events, so "mentioned but not recorded" is expected.
        if recounting and recap and level == "warn":
            level, msg = "info", msg + " (recap turn: probably retelling earlier play)"
        flags.append({"level": level, "message": msg})

    for e in errors:
        flag("warn", f"A requested change was rejected: {e[:240]}")
    statements = " ".join(x for x in re.split(r"(?<=[.!?])\s+", text) if not x.rstrip("*_ ").endswith("?"))  # an offer is a question
    if re.search(r"\b(?:you |cliff |we )?(?:pay|pays|paid|spend|spends|spent|buy|buys|bought|earn|earned|loot|looted|receive|received)\b[^.]{0,40}?\b\d[\d,]*\s+gold\b",
                 statements, re.I) and "gold" not in ch:   # a quoted price or an offer is not a transaction (playtests 1-2, L3)
        flag("warn", "Narration mentions spending or gaining Gold, but no Gold change was recorded.", recounting=True)
    if re.search(r"\b(heal(?:s|ed)?|lose[s]?|take[s]?|took|suffer(?:s|ed)?)\s+\d+\s*(damage|hp|health|slots?)\b", text, re.I) \
            and not re.search(r"\bhp\b|health bar|slot|heals|loses", ch):
        flag("warn", "Narration describes damage or healing, but no Health change was recorded.", recounting=True)
    if re.search(r"\b\d+\s*(minutes?|hours?)\s+(pass|later|go by|elapse)", text, re.I) and "minutes pass" not in ch:
        flag("warn", "Narration says time passed, but the floor clock did not advance.", recounting=True)
    if re.search(r"\b(you|cliff)\s+(receive|obtain|acquire|pocket|pick(?:s)? up|buy|bought)\b", text, re.I) and not re.search(r"receives|gains \d|stash gains", ch):
        flag("info", "Narration may describe acquiring an item, but no item was added.", recounting=True)
    produced = {str(r.get("total")) for r in rolls} | {str((r.get("detail") or {}).get("natural")) for r in rolls}
    for m in re.finditer(r"\bd20\s*\(?(\d{1,2})\)?", text, re.I):
        if m.group(1) not in produced:
            flag("warn", f"Narration quotes a d20 result ({m.group(1)}) that the server did not roll this turn.")
    if RECAP.search(player_input) and "advancement cycles" in ch:
        flag("warn", "An advancement cycle was counted on what looks like a recap/status/rules turn (CORE_MECHANICS 14.3).")
    # An absent character speaking or reporting in the narration needs an off-screen resolution this turn (CORE 15.2;
    # playtest 1, H3: an invented recon report and return)
    if turn_id is not None:
        from .orchestrator import absent_names
        resolved = rows(conn, "SELECT 1 AS x FROM events WHERE campaign_id = ? AND turn_id = ? AND type = 'offscreen_resolved'", (cid, turn_id))
        speakers = {m.group(1).strip() for m in re.finditer(r"\*\*\[?(?:Party Chat\]\s*)?([A-Z][\w' -]{1,30}?)(?:\s*\([^)]*\))?:\*\*", text)}
        absent = {n for n in absent_names(conn, cid) if " " not in n or n[0].isupper()}
        # people who left during this turn were present when they spoke (playtest 2: Marcus and Selina heading out)
        for e in rows(conn, "SELECT payload_json FROM events WHERE campaign_id = ? AND turn_id = ? AND type IN ('group_moved', 'group_updated', 'character_moved')",
                      (cid, turn_id)):
            key = (e.get("payload") or {}).get("key")
            for c in rows(conn, "SELECT name FROM characters WHERE campaign_id = ? AND assignment = ?", (cid, key)) if key else []:
                absent -= {c["name"], c["name"].split()[0]}
            if (e.get("payload") or {}).get("character_id"):
                c = rows(conn, "SELECT name FROM characters WHERE id = ?", ((e.get("payload") or {}).get("character_id"),))
                absent -= {c[0]["name"], c[0]["name"].split()[0]} if c else set()
        quoted = sorted(s for s in speakers if s in absent or s.split()[0] in absent)
        if quoted and not resolved:
            flag("warn", f"Narration has {', '.join(quoted)} (not with the player character) speaking or reporting, but nothing was "
                         "resolved off-screen this turn (CORE 15.2): check that the report is from the records, not invented.")
    secret_text = " ".join(s["content"] for s in rows(conn, "SELECT content FROM gm_secrets WHERE campaign_id = ? AND revealed = 0", (cid,)))
    leak = _shingles(text) & _shingles(secret_text)
    if leak:
        flag("warn", f"Narration repeats wording from a GM-only record ({len(leak)} 8-word run(s)); check for an accidental reveal.")
    return flags


def continuity(conn: sqlite3.Connection, cid: int, ctx: dict, flags: list[dict], time_before: int | None, time_after: int | None,
               changes: list[str]) -> dict:
    """Per-turn continuity report: did the briefing carry what the GM needed, and does the outcome add up?
    Each check is pass / warn with a short reason; `categories` and `omissions` come from the context builder."""
    text = ctx.get("text", "")
    checks: list[dict] = []

    def add(name: str, ok: bool, detail: str) -> None:
        checks.append({"check": name, "status": "pass" if ok else "warn", "detail": detail})

    camp = rows(conn, "SELECT active_character_id FROM campaigns WHERE id = ?", (cid,))[0]
    pc = rows(conn, "SELECT id, name FROM characters WHERE id = ?", (camp["active_character_id"],)) if camp["active_character_id"] else []
    add("player character sheet", bool(pc) and f"(id {pc[0]['id']}," in text, pc[0]["name"] if pc else "no player character set")
    # Compare against what existed when the briefing was built (this turn may have created groups or quests since).
    keys = ctx.get("group_keys")
    if keys is None:
        keys = [g["key"] for g in rows(conn, "SELECT key FROM party_groups WHERE campaign_id = ? AND status = 'active'", (cid,))]
    missing = [k for k in keys if f"[{k}]" not in text]
    add("party groups", not missing, f"{len(keys)} groups listed" if not missing else "missing: " + ", ".join(missing))
    open_q = rows(conn, "SELECT id, kind, title FROM quests WHERE campaign_id = ? AND visibility = 'player'", (cid,))
    wanted = set(ctx.get("open_quest_ids") or [])
    open_q = [q for q in open_q if q["id"] in wanted] if "open_quest_ids" in ctx else         rows(conn, "SELECT id, kind, title FROM quests WHERE campaign_id = ? AND visibility = 'player' AND status IN ('active','paused')", (cid,))
    lost = [q for q in open_q if f"(id {q['id']})" not in text]
    kinds = sorted({q["kind"] for q in open_q})
    add("objectives, projects, promises, reservations", not lost,
        f"{len(open_q)} open ({', '.join(kinds) or 'none'})" if not lost else f"cut from the briefing: {', '.join(q['title'] for q in lost[:5])}")
    present = ctx.get("present") or []
    npc_rows = rows(conn, """SELECT c.name, c.slug FROM characters c JOIN gm_secrets g ON g.subject = c.slug AND g.kind = 'companion_sheet'
                             AND g.revealed = 0 AND g.campaign_id = c.campaign_id WHERE c.campaign_id = ?""", (cid,))
    want = [n["name"] for n in npc_rows if n["name"] in present]
    lacking = [n for n in want if f"### {n} " in text and "private companion sheet" not in text.split(f"### {n} ", 1)[1].split("### ", 1)[0]]
    add("private personalities for present NPCs", not lacking,
        f"{len(want)} present NPCs with private sheets" if not lacking else "sheet left out for: " + ", ".join(lacking))
    add("current scene", "## CURRENT SCENE" in text and "No scene recorded" not in text, "scene loaded" if "No scene recorded" not in text else "no scene recorded")
    elapsed = (time_after or 0) - (time_before or 0)
    add("dungeon time", elapsed >= 0 and not any("floor clock did not advance" in f["message"] for f in flags),
        f"{elapsed} game minutes elapsed")
    add("advancement cycles", not any("cycle" in f["message"].lower() for f in flags),
        "cycle counted" if any("cycle" in c.lower() for c in changes) else "no cycle counted")
    add("GM secrets stayed hidden", not any("GM-only" in f["message"] or "secret" in f["message"].lower() for f in flags), "no verbatim leak detected")
    add("mechanics recorded", not any(f["level"] in ("warn", "error") and ("recorded" in f["message"] or "advance" in f["message"]) for f in flags),
        f"{len(changes)} validated changes")
    return {"categories": ctx.get("categories", {}), "omissions": ctx.get("omissions", []), "checks": checks,
            "passed": all(c["status"] == "pass" for c in checks)}
