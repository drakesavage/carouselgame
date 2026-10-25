"""One canonical value everywhere (roadmap M1 exit gate).

Each check reads one fact the way every surface actually serves it (the database, the HUD summary, the GM briefing)
and reports whether they agree. Read-only: it never fixes anything; a mismatch is for an owner-approved correction.
Only player-visible values are compared, so the report is safe to show players.
"""
from __future__ import annotations

import re
import sqlite3

from .db import one, rows


def _check(name: str, values: dict, ok: bool, note: str = "") -> dict:
    return {"check": name, "ok": bool(ok), "values": values, **({"note": note} if note else {})}


def _place(text: str | None) -> str:
    """Comparable place name: 'The Molting Pigeon, Heavensworth, Over City' and 'The Molting Pigeon — Over City' agree."""
    return re.split(r"[,—]", text or "")[0].strip().lower()


def run(conn: sqlite3.Connection, cid: int) -> list[dict]:
    from .memory.context import build_context
    from .memory.views import campaign_summary
    camp = one(conn, "SELECT * FROM campaigns WHERE id = ?", (cid,))
    hud = campaign_summary(conn, cid)
    ctx = build_context(conn, cid, "status check")["text"]
    out: list[dict] = []

    # Gold: the shared stash row, and the GM briefing's stash listing when it shows Gold
    gold = one(conn, "SELECT quantity FROM items WHERE campaign_id = ? AND owner_character_id IS NULL AND lower(name) = 'gold'", (cid,))
    m = re.search(r"#\d+ Gold ×(\d+)", ctx)
    vals = {"inventory": gold and gold["quantity"], **({"gm_context": int(m.group(1))} if m else {})}
    out.append(_check("Gold", vals, len(set(vals.values())) == 1))

    # Floor clock
    fc = hud["floor_clock"]
    m = re.search(r"FLOOR COLLAPSE in \S+ \((\d+) min\)", ctx)
    want = None if camp["floor_deadline_minutes"] is None else camp["floor_deadline_minutes"] - camp["game_time_minutes"]
    vals = {"campaign": want, "hud": fc.get("remaining_minutes"), "gm_context": int(m.group(1)) if m else None}
    out.append(_check("Floor clock (minutes remaining)", vals, len(set(vals.values())) == 1))

    # Advancement and other player clocks
    for c in hud["clocks"]:
        m = re.search(rf"Clock {re.escape(c['key'])} \([^)]*\): (\d+)/", ctx)
        vals = {"clock": c["current"], "gm_context": int(m.group(1)) if m else None}
        out.append(_check(f"Clock: {c['label']}", vals, len(set(vals.values())) == 1))

    # Location and saferoom
    m = re.search(r"^Floor \S+ — (.+?) \[(SAFEROOM|NOT a saferoom)\]", ctx, re.M)
    vals = {"campaign": f"{camp['current_location']} ({'saferoom' if camp['safe_room'] else 'not a saferoom'})",
            "hud": f"{hud['current_location']} ({'saferoom' if hud['safe_room'] else 'not a saferoom'})",
            "gm_context": f"{m.group(1)} ({'saferoom' if m.group(2) == 'SAFEROOM' else 'not a saferoom'})" if m else None}
    out.append(_check("Location and saferoom", vals, len(set(vals.values())) == 1))
    groups = {g["key"]: g for g in hud["groups"]}
    pc = one(conn, "SELECT name, assignment FROM characters WHERE id = ?", (camp["active_character_id"],)) if camp["active_character_id"] else None
    if pc and pc["assignment"] in groups:
        g = groups[pc["assignment"]]
        out.append(_check(f"{pc['name']}'s group is where the campaign is", {"campaign": camp["current_location"], f"group {g['key']}": g["location"]},
                          _place(g["location"]) == _place(camp["current_location"])))

    # Physical positions: a character's own location must agree with its group's (unknown stays unknown)
    for c in rows(conn, """SELECT name, assignment, location FROM characters WHERE campaign_id = ? AND assignment IS NOT NULL
                           AND status NOT IN ('deceased','dead') ORDER BY party_order, name""", (cid,)):
        g = groups.get(c["assignment"])
        if not g:
            out.append(_check(f"{c['name']}: group", {"assignment": c["assignment"]}, False, "assigned to a group that does not exist"))
            continue
        m = re.search(rf"### {re.escape(c['name'])} \(.*?NOT PRESENT \(group [^,]+, ([^)]+)\)", ctx)
        vals = {f"group {g['key']}": g["location"] or "unknown", "character record": c["location"] or "unknown",
                **({"gm_context": m.group(1).replace("location unknown", "unknown")} if m else {})}
        ok = (not g["location"] and not c["location"]) or _place(g["location"]) == _place(c["location"])
        ok = ok and (not m or _place(vals["gm_context"]) == _place(vals[f"group {g['key']}"]))
        out.append(_check(f"{c['name']}: position", vals, ok))

    # Reservations: a paid reservation must not still say unpaid, and is never owned
    for q in rows(conn, "SELECT title, progress, details_json FROM quests WHERE campaign_id = ? AND kind = 'reservation' AND status = 'active'", (cid,)):
        d = q.get("details") or {}
        unpaid_text = bool(re.search(r"\bno (30-Gold )?payment\b|\bnot (been )?paid\b", f"{q['progress'] or ''}", re.I))
        vals = {"payment": d.get("payment"), "owned": d.get("owned"), "progress says unpaid": unpaid_text, "records conflict": d.get("conflict")}
        out.append(_check(q["title"], vals, not (d.get("payment") == "paid" and unpaid_text) and d.get("owned") is not True and not d.get("conflict")))
    return out


def summary(conn: sqlite3.Connection, cid: int) -> dict:
    checks = run(conn, cid)
    return {"ok": all(c["ok"] for c in checks), "failed": sum(not c["ok"] for c in checks), "checks": checks}
