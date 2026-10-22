"""Builds the GM's per-turn context: a compact, relevant slice of campaign memory instead of the whole history.

Always: campaign position + floor clock, the player character's full mechanics, compact party lines, active
quests/encounter, shared resources, always-include house rules, recent rulings, rolling summaries, the last
N messages. On demand: full sheets + GM-ONLY companion sheets for characters named in the input or the
recent transcript (max 4), and unrevealed secrets. Everything else is reachable through lookup tools.
"""
from __future__ import annotations

import json
import re
import sqlite3

from ..db import one, rows
from .views import floor_clock

MAX_FOCUS = 4
COMPANION_CHARS = 3500


def _fmt_mods(c: dict) -> str:
    st = (c.get("stats") or {})
    stats, mods = st.get("stats") or {}, st.get("modifiers") or {}
    if not stats and not mods:
        return "stats: not established"
    return "stats: " + ", ".join(f"{k[:3].upper()} {stats.get(k, '?')} ({mods.get(k, 0):+d})" for k in
                                 ["strength", "intelligence", "constitution", "dexterity", "charisma"] if k in stats or k in mods)


def _res(c: dict, key: str, label: str) -> str:
    cur, mx = c.get(f"{key}_current"), c.get(f"{key}_max")
    return f"{label} {cur if cur is not None else '?'}/{mx if mx is not None else '?'}"


def _skills(conn: sqlite3.Connection, char_id: int, limit: int | None = None) -> str:
    sk = rows(conn, "SELECT kind, name, rank, passive, marked, mana_cost FROM skills WHERE character_id = ? ORDER BY kind, rank DESC, name", (char_id,))
    if limit:
        sk = sk[:limit]
    parts = []
    for s in sk:
        tag = "" if s["kind"] == "skill" else f" [{s['kind']}{', ' + str(s['mana_cost']) + ' mana' if s['mana_cost'] else ''}]"
        parts.append(f"{s['name']} {s['rank'] if s['rank'] is not None else '?'}{tag}{' (P)' if s['passive'] else ''}{' *marked' if s['marked'] else ''}")
    return ", ".join(parts) or "none recorded"


def _statuses(conn: sqlite3.Connection, char_id: int) -> str:
    st = rows(conn, "SELECT name, polarity, expires_at_minutes FROM status_effects WHERE character_id = ?", (char_id,))
    return ", ".join(f"{s['name']}" + (f" (until t={s['expires_at_minutes']})" if s["expires_at_minutes"] else "") for s in st) or "none"


def _item_note(i: dict) -> str:
    props = i.get("properties") or {}
    bits = [i.get("description") or ""] + [str(x) for x in (props.get("effects") or [])][:3]
    note = "; ".join(b for b in bits if b)
    return f" ({note[:160]})" if note else ""


def _items(conn: sqlite3.Connection, char_id: int, detail: bool = False) -> str:
    it = rows(conn, "SELECT name, quantity, equipped, description, properties_json FROM items WHERE owner_character_id = ? ORDER BY equipped DESC, name", (char_id,))
    return ", ".join(("[E] " if i["equipped"] else "") + i["name"] + ("" if i["quantity"] == 1 else f" ×{i['quantity'] if i['quantity'] is not None else '?'}")
                     + (_item_note(i) if detail else "") for i in it) or "none"


def _full_char(conn: sqlite3.Connection, c: dict) -> str:
    d = c.get("derived") or {}
    sheet = c.get("sheet") or {}
    lines = [f"### {c['name']} (id {c['id']}, {c['slug']}) — {c['kind']}, {c.get('roster_category') or ''}, status {c['status']}",
             f"Level {c['level'] if c['level'] is not None else '?'} | {c.get('race') or c.get('species') or '?'} {c.get('class_name') or ''} | "
             f"{_res(c, 'hp', 'HP')} | {_res(c, 'mana', 'Mana')} | AI Favor {c.get('ai_favor') if c.get('ai_favor') is not None else '?'}",
             _fmt_mods(c),
             "derived: " + ", ".join(f"{k} {json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v}" for k, v in d.items() if k in
                                     ("evade", "damage_resistance", "move", "step", "size", "health_bar", "unallocated_stat_points")),
             "skills: " + _skills(conn, c["id"]),
             "statuses: " + _statuses(conn, c["id"]),
             "carrying: " + _items(conn, c["id"], detail=True)]
    attacks = sheet.get("attacks")
    if attacks:
        lines.append("attacks: " + "; ".join(f"{a.get('name')} +{a.get('attack_bonus', a.get('attack_bonus_before_disadvantage', '?'))} {a.get('damage', '')}"
                                             for a in (attacks.values() if isinstance(attacks, dict) else attacks) if isinstance(a, dict)))
    narrative = sheet.get("narrative") or {}
    if narrative.get("personality_summary") or narrative.get("current_role"):
        lines.append(f"public persona: {narrative.get('current_role', '')} — {narrative.get('personality_summary', '')}")
    if c.get("npc_profile"):
        p = c["npc_profile"]
        lines.append(f"attitude to party: {'unrecorded' if p.get('attitude') is None else format(p['attitude'], '+d')}; voice: {p.get('voice') or '-'}")
    return "\n".join(lines)


def _mentioned(conn: sqlite3.Connection, cid: int, text: str, exclude: int | None) -> list[dict]:
    chars = rows(conn, "SELECT * FROM characters WHERE campaign_id = ?", (cid,))
    low = text.lower()
    hits = []
    for c in chars:
        if c["id"] == exclude:
            continue
        names = {c["name"].lower(), c["slug"].replace("-", " ")} | {c["name"].split()[0].lower()}
        pos = min((low.rfind(n) for n in names if len(n) > 2 and re.search(rf"\b{re.escape(n)}\b", low)), default=-1)
        if pos >= 0:
            hits.append((pos, c))
    hits.sort(key=lambda x: -x[0])  # most recently mentioned first
    return [c for _, c in hits[:MAX_FOCUS]]


def build_context(conn: sqlite3.Connection, cid: int, player_input: str, recent_window: int = 12) -> dict:
    camp = one(conn, "SELECT * FROM campaigns WHERE id = ?", (cid,))
    clock = floor_clock(camp)
    out: list[str] = []
    out.append(f"Campaign: {camp['name']}{' (DEMO DATA)' if camp['is_demo'] else ''}")
    out.append(f"Floor {camp['current_floor'] if camp['current_floor'] is not None else '?'} — {camp['current_location'] or 'unknown location'}"
               f"{' [SAFEROOM]' if camp['safe_room'] else ''}; game time elapsed on floor {clock['elapsed_minutes']} min; "
               + (f"floor collapses in {clock['remaining_minutes']} min ({clock['remaining_display']})" if clock["remaining_minutes"] is not None else "floor clock unknown"))
    if camp["active_objective"]:
        out.append("Immediate objective: " + camp["active_objective"])
    for k in rows(conn, "SELECT key, label, current, maximum, visibility FROM clocks WHERE campaign_id = ?", (cid,)):
        out.append(f"Clock {k['key']} ({k['label']}): {k['current']}/{k['maximum'] or '-'}{' [GM-ONLY]' if k['visibility'] == 'gm' else ''}")

    pc = one(conn, "SELECT * FROM characters WHERE id = ?", (camp["active_character_id"],)) if camp["active_character_id"] else None
    out.append("\n## PLAYER CHARACTER (controlled only by the player)")
    out.append(_full_char(conn, pc) if pc else "No player character set.")

    party = rows(conn, "SELECT * FROM characters WHERE campaign_id = ? AND in_party = 1 AND id != ? ORDER BY party_order", (cid, pc["id"] if pc else -1))
    if party:
        out.append("\n## ACTIVE PARTY (compact)")
        for c in party:
            out.append(f"- {c['name']} (id {c['id']}): L{c['level'] if c['level'] is not None else '?'} {c.get('race') or c.get('species') or ''} {c.get('class_name') or ''}; "
                       f"{_res(c, 'hp', 'HP')}, {_res(c, 'mana', 'Mana')}; statuses {_statuses(conn, c['id'])}; top skills {_skills(conn, c['id'], 6)}")

    recent = rows(conn, """SELECT role, content FROM messages WHERE campaign_id = ? AND rolled_back = 0 AND visibility = 'player'
                           ORDER BY id DESC LIMIT ?""", (cid, recent_window))[::-1]
    focus = _mentioned(conn, cid, " ".join(m["content"][-600:] for m in recent[-4:]) + " " + player_input, pc["id"] if pc else None)
    if focus:
        out.append("\n## CHARACTERS IN FOCUS (named in the input or recent scene)")
        for c in focus:
            c["npc_profile"] = one(conn, "SELECT * FROM npc_profiles WHERE character_id = ?", (c["id"],))
            out.append(_full_char(conn, c))
            comp = one(conn, "SELECT id, content FROM gm_secrets WHERE campaign_id = ? AND kind = 'companion_sheet' AND subject = ? AND revealed = 0",
                       (cid, c["slug"]))
            if comp:
                out.append(f"GM-ONLY private companion sheet (secret id {comp['id']}; perform, never expose):\n" + comp["content"][:COMPANION_CHARS])

    q = rows(conn, "SELECT title, kind, status, priority, progress FROM quests WHERE campaign_id = ? AND status IN ('active','paused') ORDER BY priority, id", (cid,))
    if q:
        out.append("\n## ACTIVE QUESTS & PROJECTS\n" + "\n".join(f"- [{x['kind']}, P{x['priority']}] {x['title']}" + (f" — {x['progress']}" if x["progress"] else "") for x in q))
    enc = one(conn, "SELECT * FROM encounters WHERE campaign_id = ? AND status = 'active' ORDER BY id DESC LIMIT 1", (cid,))
    if enc:
        out.append(f"\n## ACTIVE ENCOUNTER: {enc['name']} (round {enc['round']})\n{enc['summary'] or ''}\nCombatants: {json.dumps(enc['combatants'], ensure_ascii=False)[:1500]}")
    shared = rows(conn, "SELECT name, quantity, deployed, description, properties_json FROM items WHERE campaign_id = ? AND owner_character_id IS NULL ORDER BY deployed, name", (cid,))
    if shared:
        out.append("\n## SHARED STASH (qty ? = count not established; establish it with set_quantity before spending)\n"
                   + ", ".join(f"{s['name']} ×{s['quantity'] if s['quantity'] is not None else '?'}{' (deployed)' if s['deployed'] else ''}{_item_note(s)}" for s in shared))

    rules = rows(conn, "SELECT section, title, text, label FROM house_rules WHERE campaign_id = ? AND always_include = 1 ORDER BY id", (cid,))
    if rules:
        out.append("\n## CAMPAIGN PROCEDURE (always applies; more via search_rules)")
        out.extend(f"### {r['section'] or ''} {r['title']}\n{r['text'][:1600]}" for r in rules)
    others = rows(conn, "SELECT section, title FROM house_rules WHERE campaign_id = ? AND always_include = 0 ORDER BY id", (cid,))
    if others:
        out.append("Other house-rule sections (search_rules to read): " + "; ".join(f"{r['section'] or ''} {r['title']}".strip() for r in others)[:2500])
    rl = rows(conn, "SELECT topic, ruling, label FROM rulings WHERE campaign_id = ? ORDER BY id DESC LIMIT 8", (cid,))
    if rl:
        out.append("\n## RECENT RULINGS\n" + "\n".join(f"- ({r['label']}) {r['topic']}: {r['ruling'][:300]}" for r in rl))
    secrets = rows(conn, "SELECT id, subject, kind, content FROM gm_secrets WHERE campaign_id = ? AND revealed = 0 AND kind != 'companion_sheet' ORDER BY id DESC LIMIT 10", (cid,))
    if secrets:
        out.append("\n## GM-ONLY SECRETS (never reveal directly)\n" + "\n".join(f"- #{s['id']} [{s['kind']}] {s['subject']}: {s['content'][:500]}" for s in secrets))
    sums = rows(conn, "SELECT summary FROM memory_summaries WHERE campaign_id = ? ORDER BY id DESC LIMIT 3", (cid,))[::-1]
    if sums:
        out.append("\n## EARLIER IN THIS SESSION (summaries)\n" + "\n".join("- " + s["summary"] for s in sums))
    if recent:
        out.append("\n## RECENT TRANSCRIPT")
        out.extend(f"[{m['role'].upper()}] {m['content'][:1500]}" for m in recent)
    else:
        out.append("\n(No play has happened in this app yet. Use lookup_records for where the campaign left off.)")
    return {"kind": "context", "text": "\n".join(out), "player_character": pc["name"] if pc else "player"}
