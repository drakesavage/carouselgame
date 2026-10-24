"""GM Context Builder: a bounded, structured briefing for every GM call (never the whole transcript or rulebook).

Sections, in order:
  1 CAMPAIGN        identity, state version, floor, location, game time, dungeon day, collapse clock, cycles
  2 CURRENT SCENE   the active scene record + recent significant events
  3 PARTY           every group: location, activity, members (who is present vs elsewhere)
  4 PRESENT         full sheets for the player character and everyone present (scene participants, the PC's group,
                    anyone named); GM-ONLY companion sheets and memories for them
  5 OBJECTIVES      quests, projects, crafting, commitments, reservations (details, due times, owners)
  6 MEMORY          closed-scene summaries, transcript summaries, relevant canonical facts, the last N messages
  7 RULES           always-apply constitution sections, house rules matching this action, recent rulings,
                    auto-retrieved rulebook passages with page refs (to verify before citing)
  8 GM-ONLY         relevant unrevealed secrets
The player's exact input is sent separately as its own step.

Presence comes from stored state (scene participants, group membership), not only name matching; characters are
listed with stable ids, which every op accepts. Each section has a character budget, so the packet stays bounded.
"""
from __future__ import annotations

import json
import re
import sqlite3

from ..db import one, rows
from .views import floor_clock

BUDGET = {"scene": 3500, "party": 3000, "present_each": 2600, "companion": 2500, "objectives": 3500, "memory": 4500,
          "transcript_msg": 1500, "rules_relevant": 2400, "official": 1600, "secrets": 3500}
MAX_PRESENT_FULL = 10
MAX_COMPANIONS = 8
STOP = set("the a an and or of to in on for with by is are be as at it this that from what how does do can when which who i we my our me us "
           "you your he she they them his her their then than into onto about there here just also will would should could".split())


def _cut(text: str | None, n: int) -> str:
    text = text or ""
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9']+", (text or "").lower()) if len(w) > 2 and w not in STOP}


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
    it = rows(conn, "SELECT id, name, quantity, equipped, description, properties_json FROM items WHERE owner_character_id = ? ORDER BY equipped DESC, name", (char_id,))
    return ", ".join(f"#{i['id']} " + ("[E] " if i["equipped"] else "") + i["name"] + ("" if i["quantity"] == 1 else f" ×{i['quantity'] if i['quantity'] is not None else '?'}")
                     + (_item_note(i) if detail else "") for i in it) or "none"


def _uses(conn: sqlite3.Connection, char_id: int) -> str:
    us = rows(conn, "SELECT ability, per, uses, uses_allowed FROM ability_uses WHERE character_id = ?", (char_id,))
    return ", ".join(f"{u['ability']} {u['uses']}/{u['uses_allowed']} per {u['per'].replace('_', ' ')}" for u in us)


def _full_char(conn: sqlite3.Connection, c: dict, where: str | None = None) -> str:
    d = c.get("derived") or {}
    sheet = c.get("sheet") or {}
    lines = [f"### {c['name']} (id {c['id']}, {c['slug']}) — {c['kind']}, {c.get('roster_category') or ''}, status {c['status']}" + (f" — {where}" if where else ""),
             f"Level {c['level'] if c['level'] is not None else '?'} | {c.get('race') or c.get('species') or '?'} {c.get('class_name') or ''} | "
             f"{_res(c, 'hp', 'HP')} | {_res(c, 'mana', 'Mana')} | AI Favor {c.get('ai_favor') if c.get('ai_favor') is not None else '?'}",
             _fmt_mods(c),
             "derived: " + ", ".join(f"{k} {json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v}" for k, v in d.items() if k in
                                     ("evade", "damage_resistance", "move", "step", "size", "health_bar", "unallocated_stat_points", "level_progress")),
             "skills: " + _skills(conn, c["id"]),
             "statuses: " + _statuses(conn, c["id"]),
             "carrying: " + _items(conn, c["id"], detail=True)]
    uses = _uses(conn, c["id"])
    if uses:
        lines.append("limited uses: " + uses)
    attacks = sheet.get("attacks")
    if attacks:
        lines.append("attacks: " + "; ".join(f"{a.get('name')} +{a.get('attack_bonus', a.get('attack_bonus_before_disadvantage', '?'))} {a.get('damage', '')}"
                                             for a in (attacks.values() if isinstance(attacks, dict) else attacks) if isinstance(a, dict)))
    if sheet.get("hotlist"):
        lines.append("hotlist: " + ", ".join(map(str, sheet["hotlist"])))
    narrative = sheet.get("narrative") or {}
    if narrative.get("personality_summary") or narrative.get("current_role"):
        lines.append(f"public persona: {narrative.get('current_role', '')} — {narrative.get('personality_summary', '')}")
    p = c.get("npc_profile")
    if p:
        lines.append(f"attitude to party: {'unrecorded' if p.get('attitude') is None else format(p['attitude'], '+d')}; voice: {p.get('voice') or '-'}")
    return _cut("\n".join(lines), BUDGET["present_each"])


def _named(chars: list[dict], text: str) -> list[int]:
    low = text.lower()
    hits = []
    for c in chars:
        names = {c["name"].lower(), c["slug"].replace("-", " "), c["name"].split()[0].lower()}
        pos = max((low.rfind(n) for n in names if len(n) > 2 and re.search(rf"\b{re.escape(n)}\b", low)), default=-1)
        if pos >= 0:
            hits.append((pos, c["id"]))
    return [cid for _, cid in sorted(hits, key=lambda x: -x[0])]


def _due(q: dict, now: int) -> str:
    if q.get("due_at_minutes") is None:
        return ""
    left = q["due_at_minutes"] - now
    return f" [due in {left // 60}h{left % 60:02d}m]" if left >= 0 else f" [OVERDUE by {-left // 60}h{-left % 60:02d}m]"


def build_context(conn: sqlite3.Connection, cid: int, player_input: str, recent_window: int = 12, rule_conn: sqlite3.Connection | None = None) -> dict:
    from ..rules.state_ops import day_info
    camp = one(conn, "SELECT * FROM campaigns WHERE id = ?", (cid,))
    clock = floor_clock(camp)
    now = camp["game_time_minutes"]
    chars = rows(conn, "SELECT * FROM characters WHERE campaign_id = ? ORDER BY party_order, id", (cid,))
    by_id = {c["id"]: c for c in chars}
    pc = by_id.get(camp["active_character_id"])
    scene = one(conn, "SELECT * FROM scenes WHERE campaign_id = ? AND status = 'active' ORDER BY id DESC LIMIT 1", (cid,))
    groups = rows(conn, "SELECT * FROM party_groups WHERE campaign_id = ? AND status = 'active' ORDER BY id", (cid,))
    recent = rows(conn, """SELECT role, content FROM messages WHERE campaign_id = ? AND rolled_back = 0 AND visibility = 'player'
                           ORDER BY id DESC LIMIT ?""", (cid, recent_window))[::-1]
    focus_text = " ".join(m["content"][-800:] for m in recent[-3:]) + " " + player_input
    sections: dict[str, str] = {}

    # 1 CAMPAIGN
    d = day_info(conn, cid)
    last_event = one(conn, "SELECT MAX(id) AS m FROM events WHERE campaign_id = ?", (cid,))["m"] or 0
    last_save = one(conn, "SELECT id, label FROM save_points WHERE campaign_id = ? ORDER BY id DESC LIMIT 1", (cid,))
    ident = [f"Campaign #{cid}: {camp['name']}{' (DEMO DATA)' if camp['is_demo'] else ''} | state version: event {last_event}"
             + (f", save #{last_save['id']}" if last_save else ""),
             f"Floor {camp['current_floor'] if camp['current_floor'] is not None else '?'} — {camp['current_location'] or 'unknown location'}"
             f"{' [SAFEROOM]' if camp['safe_room'] else ' [NOT a saferoom]'}",
             f"Game time: {now} min elapsed on this floor; dungeon day {d['day']} ({d['minutes_into_day'] // 60}h{d['minutes_into_day'] % 60:02d}m in, "
             f"daily reset in {d['minutes_to_reset'] // 60}h{d['minutes_to_reset'] % 60:02d}m); "
             + (f"FLOOR COLLAPSE in {clock['remaining_display']} ({clock['remaining_minutes']} min)" if clock["remaining_minutes"] is not None else "floor clock unknown")]
    if scene and scene.get("location") and camp["current_location"] and             scene["location"].split(",")[0].split(" — ")[0].strip().lower() != camp["current_location"].split(",")[0].split(" — ")[0].strip().lower():
        ident.append(f"NOTE: the campaign record places the party at {camp['current_location']}"
                     f"{' (saferoom)' if camp['safe_room'] else ''}, but the current scene is at {scene['location']}. Records disagree: do not assume "
                     "saferoom protection in the scene; settle it in play and record it with op set_location.")
    if camp["active_objective"]:
        ident.append("Immediate objective: " + camp["active_objective"])
    for k in rows(conn, "SELECT key, label, current, maximum, visibility FROM clocks WHERE campaign_id = ?", (cid,)):
        ident.append(f"Clock {k['key']} ({k['label']}): {k['current']}/{k['maximum'] or '-'}{' [GM-ONLY]' if k['visibility'] == 'gm' else ''}")
    sections["CAMPAIGN"] = "\n".join(ident)

    # 2 CURRENT SCENE
    sc = []
    if scene:
        sc.append(f"{scene['title']}" + (f" @ {scene['location']}" if scene["location"] else "") + (f" (group: {scene['group_key']})" if scene["group_key"] else "")
                  + f" — since t={scene['started_game_minutes']}" + (f" [source: {scene['source']}]" if scene.get("source") and scene["source"] != "play" else ""))
        for label, key in (("Situation", "situation"), ("Conditions", "conditions"), ("Threats", "threats")):
            if scene.get(key):
                sc.append(f"{label}: {scene[key]}")
        if scene.get("participants"):
            sc.append("Participants: " + ", ".join(map(str, scene["participants"])))
    else:
        sc.append("No scene recorded. Start one with apply_changes op scene (title, location, group, situation, participants).")
    ev = rows(conn, """SELECT summary FROM events WHERE campaign_id = ? AND visibility = 'player' AND rolled_back = 0
                       AND type NOT IN ('time_advanced','memory_recorded','fact_recorded') ORDER BY id DESC LIMIT 8""", (cid,))[::-1]
    if ev:
        sc.append("Recent events: " + " | ".join(e["summary"][:160] for e in ev))
    sections["CURRENT SCENE"] = _cut("\n".join(sc), BUDGET["scene"])

    # 3 PARTY ORGANIZATION
    pc_group = pc.get("assignment") if pc else None
    present: list[int] = []
    if pc:
        present.append(pc["id"])
    if scene:
        for ref in scene.get("participants") or []:
            for c in chars:
                if str(ref).lower() in (c["name"].lower(), c["slug"], str(c["id"])) and c["id"] not in present:
                    present.append(c["id"])
    for gkey in {pc_group, scene["group_key"] if scene else None} - {None}:
        present += [c["id"] for c in chars if c.get("assignment") == gkey and c["id"] not in present]
    if not groups and not scene:  # no organization recorded: treat the in-party roster as together
        present += [c["id"] for c in chars if c["in_party"] and c["id"] not in present]
    named = [i for i in _named(chars, focus_text) if i != (pc or {}).get("id")]
    po = []
    for g in groups:
        members = [c for c in chars if c.get("assignment") == g["key"]]
        tag = " ← PLAYER CHARACTER IS HERE" if g["key"] == pc_group else ""
        po.append(f"- [{g['key']}] {g['label']} @ {g['location'] or 'location not recorded'}{tag}" + (f" — {g['activity']}" if g["activity"] else ""))
        po.append("  members: " + ", ".join(f"{c['name']} (id {c['id']}, {_res(c, 'hp', 'HP')}, {c['status']})" for c in members))
    loose = [c for c in chars if not c.get("assignment") and c["id"] != (pc or {}).get("id") and c["status"] not in ("deceased", "dead")]
    if loose and groups:
        po.append("- Not in a group: " + ", ".join(f"{c['name']} (id {c['id']}, {c['location'] or 'location unknown'})" for c in loose))
    if not groups:
        po.append("No party groups recorded: everyone in the active party is assumed together. Record splits with op group / move_group.")
    po.append("Only characters PRESENT below can act in this scene; others are elsewhere doing their activity (resolve offscreen per §15).")
    sections["PARTY"] = _cut("\n".join(po), BUDGET["party"])

    # 4 PRESENT CHARACTERS (+ named elsewhere)
    full_ids = present[:MAX_PRESENT_FULL] + [i for i in named if i not in present][:4]
    blocks, companions = [], 0
    for i in full_ids:
        c = dict(by_id[i])
        c["npc_profile"] = one(conn, "SELECT * FROM npc_profiles WHERE character_id = ?", (i,))
        where = None if i in present else f"NOT PRESENT (group {c.get('assignment') or '-'}, {c.get('location') or 'location unknown'})"
        role = "PLAYER CHARACTER — controlled only by the player" if pc and i == pc["id"] else where
        blocks.append(_full_char(conn, c, role))
        if pc and i == pc["id"]:
            continue
        mem = rows(conn, "SELECT content, visibility FROM memories WHERE campaign_id = ? AND character_id = ? ORDER BY importance, id DESC LIMIT 6", (cid, i))
        if mem:
            blocks.append("memories: " + " | ".join(("[GM-ONLY] " if m["visibility"] == "gm" else "[party saw] ") + m["content"][:220] for m in mem))
        comp = one(conn, "SELECT id, content FROM gm_secrets WHERE campaign_id = ? AND kind = 'companion_sheet' AND subject = ? AND revealed = 0", (cid, c["slug"]))
        if comp and companions < MAX_COMPANIONS:
            companions += 1
            blocks.append(f"GM-ONLY private companion sheet (secret id {comp['id']}; perform it, never expose it):\n" + _cut(comp["content"], BUDGET["companion"]))
    absent_named = [by_id[i]["name"] for i in named if i not in full_ids]
    if len(present) > MAX_PRESENT_FULL:
        blocks.append("Also present (compact): " + ", ".join(f"{by_id[i]['name']} (id {i}): L{by_id[i]['level']}, {_res(by_id[i], 'hp', 'HP')}; top skills {_skills(conn, i, 5)}"
                                                          for i in present[MAX_PRESENT_FULL:]))
    if absent_named:
        blocks.append("Also mentioned: " + ", ".join(absent_named))
    sections["PRESENT"] = "\n".join(blocks) or "No characters recorded."

    # 5 OBJECTIVES & COMMITMENTS
    ob = []
    for q in rows(conn, "SELECT * FROM quests WHERE campaign_id = ? AND visibility = 'player' AND status IN ('active','paused') ORDER BY priority, id", (cid,)):
        det = {k: v for k, v in (q.get("details") or {}).items() if k != "ledger_source"}
        owner = by_id.get(q.get("owner_character_id"))
        ob.append(f"- (id {q['id']}) [{q['kind']}, P{q['priority']}{', ' + q['status'] if q['status'] != 'active' else ''}] {q['title']}{_due(q, now)}"
                  + (f" — holder: {owner['name']}" if owner else "") + (f" — {q['progress']}" if q["progress"] else "")
                  + (f"\n  details: {_cut(json.dumps(det, ensure_ascii=False, default=str), 500)}" if det else ""))
    ob.append("Reservations are held, not owned: never treat a reserved item as in anyone's inventory until it is actually acquired.")
    sections["OBJECTIVES"] = _cut("\n".join(ob), BUDGET["objectives"])

    # shared stash (resources) belongs with objectives/resources
    shared = rows(conn, "SELECT id, name, quantity, deployed, description, properties_json FROM items WHERE campaign_id = ? AND owner_character_id IS NULL ORDER BY deployed, name", (cid,))
    if shared:
        sections["SHARED STASH"] = ("(qty ? = count not established; set_quantity before spending)\n"
                                    + ", ".join(f"#{s['id']} {s['name']} ×{s['quantity'] if s['quantity'] is not None else '?'}{' (deployed)' if s['deployed'] else ''}{_item_note(s)}" for s in shared))

    enc = one(conn, "SELECT * FROM encounters WHERE campaign_id = ? AND status = 'active' ORDER BY id DESC LIMIT 1", (cid,))
    if enc:
        sections["ACTIVE COMBAT"] = _combat_context(conn, enc)

    # 6 MEMORY
    mem = []
    eps = rows(conn, "SELECT subject, content FROM memories WHERE campaign_id = ? AND layer = 'episodic' AND visibility = 'player' ORDER BY id DESC LIMIT 4", (cid,))[::-1]
    if eps:
        mem.append("Earlier scenes: " + " | ".join(f"{e['subject']}: {e['content'][:400]}" for e in eps))
    sums = rows(conn, "SELECT summary FROM memory_summaries WHERE campaign_id = ? ORDER BY id DESC LIMIT 3", (cid,))[::-1]
    if sums:
        mem.append("Transcript summaries: " + " | ".join(s["summary"][:600] for s in sums))
    want = _words(player_input + " " + (scene["title"] + " " + (scene["situation"] or "") if scene else ""))
    facts = rows(conn, "SELECT subject, fact, certainty FROM lore_facts WHERE campaign_id = ? AND visibility = 'player'", (cid,))
    scored = sorted(((len(want & _words(f["subject"] + " " + f["fact"])), f) for f in facts), key=lambda x: -x[0])
    rel = [f for s, f in scored if s >= 2][:8]
    if rel:
        mem.append("Relevant established facts: " + " | ".join(f"{f['fact'][:260]}" + ("" if f["certainty"] == "canonical" else f" [{f['certainty']}]") for f in rel))
    mem.append("History beyond this briefing: lookup_records (imported ledger) and the event log. If a needed memory can't be found, "
               "say so and make an explicit provisional ruling; never invent continuity.")
    sections["MEMORY"] = _cut("\n".join(mem), BUDGET["memory"])

    # 7 RULES
    ru = []
    for r in rows(conn, "SELECT section, title, text FROM house_rules WHERE campaign_id = ? AND always_include = 1 ORDER BY id", (cid,)):
        ru.append(f"### {r['section'] or ''} {r['title']}\n{r['text'][:1600]}")
    hr = rows(conn, "SELECT section, title, text, label FROM house_rules WHERE campaign_id = ? AND always_include = 0", (cid,))
    act = _words(player_input)
    hits = sorted(((len(act & _words(h["title"] + " " + h["text"][:1500])), h) for h in hr), key=lambda x: -x[0])
    rel_rules = [h for s, h in hits if s >= 2][:3]
    if rel_rules:
        ru.append("Possibly relevant house rules for this action:\n" + _cut("\n".join(f"- §{h['section'] or ''} {h['title']} ({h['label'] or 'campaign'}): {h['text'][:700]}" for h in rel_rules), BUDGET["rules_relevant"]))
    rl = rows(conn, "SELECT topic, ruling, label FROM rulings WHERE campaign_id = ? ORDER BY id DESC LIMIT 8", (cid,))
    if rl:
        ru.append("Recent rulings: " + " | ".join(f"({r['label']}) {r['topic']}: {r['ruling'][:240]}" for r in rl))
    if rule_conn is not None and len(act) >= 2:
        from ..reference import rulebooks
        ps = rulebooks.search(rule_conn, player_input, limit=2)
        if ps:
            ru.append("Auto-retrieved rulebook passages (keyword match; read_rule_page before citing):\n"
                      + _cut("\n".join(f"- {p.ref()}: {p.text[:600]}" for p in ps), BUDGET["official"]))
    ru.append("Other constitution sections and the rulebooks: search_rules. Unsupported mechanics: retrieve, then record_ruling a provisional label.")
    sections["RULES"] = "\n".join(ru)

    # 8 GM-ONLY
    pres_words = _words(" ".join(by_id[i]["name"] + " " + by_id[i]["slug"] for i in present + named) + " " + (scene["title"] if scene else "") + " " + player_input)
    secrets = rows(conn, "SELECT id, subject, kind, content FROM gm_secrets WHERE campaign_id = ? AND revealed = 0 AND kind != 'companion_sheet' ORDER BY id DESC", (cid,))
    ranked = sorted(((len(pres_words & _words(s["subject"] + " " + s["content"][:400])), -s["id"], s) for s in secrets), key=lambda x: (-x[0], x[1]))
    chosen = [s for score, _, s in ranked if score > 0][:6] + [s for score, _, s in ranked if score == 0][:4]
    if chosen:
        sections["GM-ONLY SECRETS"] = _cut("Never reveal directly; reveal only through play, then reveal_secret.\n"
                                           + "\n".join(f"- #{s['id']} [{s['kind']}] {s['subject']}: {s['content'][:500]}" for s in chosen), BUDGET["secrets"])

    # transcript
    if recent:
        sections["RECENT TRANSCRIPT"] = "\n".join(f"[{m['role'].upper()}] {m['content'][:BUDGET['transcript_msg']]}" for m in recent)
    else:
        sections["RECENT TRANSCRIPT"] = "(No play has happened in this app yet. Use lookup_records for where the campaign left off.)"

    text = "\n\n".join(f"## {name}\n{body}" for name, body in sections.items())
    return {"kind": "context", "text": text, "player_character": pc["name"] if pc else "player",
            "sizes": {k: len(v) for k, v in sections.items()}, "present": [by_id[i]["name"] for i in present], "named": [by_id[i]["name"] for i in named]}


def _combat_context(conn: sqlite3.Connection, enc: dict) -> str:
    st = enc["combatants"]
    if not (isinstance(st, dict) and "combatants" in st):
        return (f"{enc['name']} (round {enc['round']})\n{enc['summary'] or ''}\n"
                f"Combatants: {json.dumps(enc['combatants'], ensure_ascii=False)[:1500]}")
    lines = [f"{enc['name']} — round {st['round']}, phase {st['phase']} (stat blocks are GM-ONLY)", st.get("setup") or ""]
    for c in st["combatants"]:
        if c["side"] == "party":
            ch = one(conn, "SELECT hp_current, hp_max, status FROM characters WHERE id = ?", (c["character_id"],))
            lines.append(f"- [party {c['id']}] {c['name']}: HP {ch['hp_current']}/{ch['hp_max']} ({ch['status']}); Actions left {c['actions_left']}"
                         + ("; EVADING" if c["evading"] else "") + ("; Advantage vs " + ",".join(c["advantage_vs"]) if c["advantage_vs"] else ""))
        else:
            atks = "; ".join(f"{x['name']} {x['to_hit']} to hit, {x['damage']}" + (" AREA" if x.get("area") else "") for x in c["attacks"])
            lines.append(f"- [foe {c['id']}] {c['name']} ({c['kind']}): HB {sum(c['slots'])}/{c['max_hb']} {c['slots']}"
                         + (" DEFEATED" if c["defeated"] else "") + f"; Evade {c['evade']}, DR {c['dr']}, Actions {c['actions_left']}/{c['actions']}; attacks: {atks}")
    if st.get("declared"):
        lines.append("Declared this round: " + "; ".join(f"{d['mob']} {d['attack']} -> {d['target']}" for d in st["declared"]))
    lines.append("Procedure: declare Mob Attacks -> (player decides Interrupts) -> resolve -> crawler Actions -> end round. "
                 "Never choose the player character's Actions; ask.")
    return "\n".join(lines)
