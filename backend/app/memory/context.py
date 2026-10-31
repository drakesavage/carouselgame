"""GM Context Builder: a bounded, structured briefing for every GM call (never the whole transcript or rulebook).

Sections, in order:
  1 CAMPAIGN        identity, state version, floor, location, game time, dungeon day, collapse clock, cycles
  2 CURRENT SCENE   the active scene record + recent significant events
  3 PARTY           every group: location, activity, members (who is present vs elsewhere)
  4 PRESENT         full sheets for the player character and everyone present (scene participants, the PC's group,
                    anyone named); GM-ONLY companion sheets and memories for them
  5 OBJECTIVES      quests, projects, crafting, commitments, reservations (details, due times, owners)
  6 MEMORY          closed-scene summaries, transcript summaries, relevant canonical facts, the last N messages
  7 HOUSE RULES     always-apply constitution sections, house rules matching this action, recent rulings
  8 RULEBOOK        auto-retrieved rulebook passages with page refs (to verify before citing)
  9 GM-ONLY         relevant unrevealed secrets
The player's exact input is sent separately as its own step.

Every section maps to one category (CATEGORIES) so diagnostics can report what was loaded. Budgets are characters
(DEFAULT_BUDGET, overridable per install with CONTEXT_BUDGETS); anything cut or left out is listed in `omissions`
instead of disappearing silently.

Presence comes from stored state (scene participants, group membership), not only name matching; characters are
listed with stable ids, which every op accepts. Each section has a character budget, so the packet stays bounded.
"""
from __future__ import annotations

import json
import re
import sqlite3

from ..db import one, rows

DEFAULT_BUDGET = {"scene": 3500, "party": 3000, "present_each": 2600, "companion": 3800, "objectives": 3500, "memory": 4500,
                  "transcript_msg": 1500, "rules_always_each": 2600, "rules_relevant": 2400, "official": 1600, "secrets": 3500,
                  "max_present_full": 10, "max_companions": 8, "max_named_absent": 4, "max_facts": 8, "max_secrets": 10,
                  "official_passages": 2, "max_house_rules": 3, "house_rule_each": 700, "include_absent_party": 0}
BUDGET = DEFAULT_BUDGET  # backwards-compatible name
# Profiles for models with very large context windows (e.g. Gemini, 1M tokens): front-load the whole party, every
# private sheet, every house rule, a longer transcript and the ledger's current-state documents verbatim, instead of
# relying on the GM to look things up. About 55k tokens per call for the Denton campaign (character YAMLs are
# left out: their content is already in the PRESENT sheets).
PROFILES: dict[str, dict] = {
    "standard": {},
    "large": {"scene": 12000, "party": 12000, "present_each": 12000, "companion": 12000, "objectives": 30000, "memory": 40000,
              "transcript_msg": 8000, "rules_always_each": 12000, "rules_relevant": 80000, "house_rule_each": 8000, "official": 9000,
              "secrets": 40000, "max_present_full": 40, "max_companions": 40, "max_named_absent": 40, "max_facts": 200,
              "max_secrets": 200, "official_passages": 6, "max_house_rules": 500, "recent_messages": 40, "include_absent_party": 1,
},
}
CATEGORIES = {"CAMPAIGN": "state", "PARTY": "state", "PRESENT": "state", "OBJECTIVES": "state", "SHARED STASH": "state",
              "ACTIVE COMBAT": "state", "LEDGER RECORDS": "ledger", "CURRENT SCENE": "scene_memory", "MEMORY": "scene_memory", "HOUSE RULES": "contract_rules",
              "RULEBOOK": "rulebook", "RECENT TRANSCRIPT": "narrative", "RECENT ROLLS": "state", "GM-ONLY SECRETS": "gm_private"}
STOP = set("the a an and or of to in on for with by is are be as at it this that from what how does do can when which who i we my our me us "
           "you your he she they them his her their then than into onto about there here just also will would should could".split())


def _cut(text: str | None, n: int) -> str:
    text = text or ""
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9']+", (text or "").lower()) if len(w) > 2 and w not in STOP}


def _sheet_lines(conn: sqlite3.Connection, c: dict) -> list[str]:
    """Role, five stats (base -> current when they differ), Plot Armor base/current/effective, and active modifiers."""
    from ..carousel.sheet import STATS, sheet
    sh = sheet(conn, c["id"])
    role = c.get("role_id") or "no Archetype assigned"
    lines = [f"role: {role}" + (f" ({c['role_reason']})" if c.get("role_reason") else "")]
    if sh["stats"]["Mettle"] is None:
        lines.append("stats: not assigned yet")
        return lines
    pa = sh["plot_armor"]
    lines.append("stats: " + ", ".join(f"{s} {sh['stats'][s]}" + (f"->{sh['current'][s]}" if sh["current"][s] != sh["stats"][s] else "") for s in STATS)
                 + f" | Plot Armor {pa['base']}/{pa['current']}/{pa['effective']} (base/current/effective)")
    if sh["modifiers"]:
        lines.append("modifiers: " + "; ".join(f"#{m['id']} {m['target']} {'x' if m['op'] == 'multiply' else '+'}{m['value']:g} "
                                              f"from {m['source']} ({m['scope']})" for m in sh["modifiers"]))
    return lines


def _tickets(conn: sqlite3.Connection, char_id: int, *, hidden: bool) -> list[tuple[dict, dict | None]]:
    """(owned row, reference definition or None when reference data is missing) for a character's tickets."""
    from ..carousel.refdata import has_reference, ticket_definition
    ref = has_reference(conn)
    return [(t, ticket_definition(conn, t["ticket_ref"]) if ref else None)
            for t in rows(conn, "SELECT * FROM character_tickets WHERE character_id = ? AND hidden = ? ORDER BY equipped DESC, id", (char_id, int(hidden)))]


def _ticket_rulings(conn: sqlite3.Connection, cid: int, ref: str) -> list[str]:
    from .rulings import ruling_status
    out = []
    for r in rows(conn, "SELECT topic, ruling, label, certainty, review FROM rulings WHERE campaign_id = ? AND ticket_ref = ? ORDER BY id DESC", (cid, ref)):
        st = ruling_status(r)
        if st != "rejected":
            out.append(f"    ruling ({'precedent' if st == 'precedent' else 'provisional'}): {r['topic']}: {r['ruling']}")
    return out


def _ticket_line(t: dict, d: dict | None) -> str:
    if d is None:
        return f"- {t['ticket_ref']} (ticket text unavailable: reference data not imported)"
    state = "[EQUIPPED]" if t["equipped"] else "[owned, not equipped]"
    uses = (f"uses left {t['uses_left']}/{t['uses_max']}" + (f" (recharges per {t['recharge']})" if t["recharge"] else "")
            if t["uses_max"] is not None else "GM-adjudicated uses")
    return f"- {d['name']} ({t['ticket_ref']}) {state}, {uses}: {d['text']}"


def _tickets_block(conn: sqlite3.Connection, cid: int, c: dict) -> str:
    """Every owned, non-hidden ticket with its full rule text and the rulings on it. Never cut by the sheet budget."""
    lines = []
    for t, d in _tickets(conn, c["id"], hidden=False):
        lines.append(_ticket_line(t, d))
        lines += _ticket_rulings(conn, cid, t["ticket_ref"])
    return f"{c['name']}'s tickets:\n" + "\n".join(lines) if lines else f"{c['name']}'s tickets: none"


def _pa(conn: sqlite3.Connection, c: dict) -> str:
    from ..carousel.sheet import sheet
    pa = sheet(conn, c["id"])["plot_armor"]
    return "PA ?" if pa["effective"] is None else f"PA {pa['effective']}"


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


def _full_char(conn: sqlite3.Connection, c: dict, where: str | None = None, budget: int = DEFAULT_BUDGET["present_each"]) -> str:
    sheet = c.get("sheet") or {}
    lines = [f"### {c['name']} (id {c['id']}, {c['slug']}) — {c['kind']}, {c.get('roster_category') or ''}, status {c['status']}" + (f" — {where}" if where else ""),
             *_sheet_lines(conn, c),
             "statuses: " + _statuses(conn, c["id"]),
             "carrying: " + _items(conn, c["id"], detail=True)]
    ident = sheet.get("identity") if isinstance(sheet.get("identity"), dict) else {}
    if c["kind"] == "npc":  # a person in front of the crawler has a visible gender; never leave it to "they" by default
        lines.append("identity: " + ", ".join(f"{k.replace('_', ' ')} {v}" for k, v in ident.items()) if ident.get("pronouns")
                     else "identity: gender and pronouns NOT RECORDED — decide them now as anyone looking at this person would, "
                          "record with op npc (gender, pronouns, apparent_age) and narrate with them")
    narrative = sheet.get("narrative") or {}
    from ..importer.visual_reference import reference_line
    looks = reference_line(sheet)
    if looks:
        lines.append(looks)
    if narrative.get("personality_summary") or narrative.get("current_role"):
        lines.append(f"public persona: {narrative.get('current_role', '')} — {narrative.get('personality_summary', '')}")
    p = c.get("npc_profile")
    if p:
        lines.append(f"attitude to party: {'unrecorded' if p.get('attitude') is None else format(p['attitude'], '+d')}; voice: {p.get('voice') or '-'}")
    return _cut("\n".join(lines), budget)


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


def build_context(conn: sqlite3.Connection, cid: int, player_input: str, recent_window: int = 12, rule_conn: sqlite3.Connection | None = None,
                  budgets: dict | None = None) -> dict:
    B = {**DEFAULT_BUDGET, **(budgets or {})}
    omissions: list[str] = []
    recent_window = int(B.get("recent_messages") or recent_window)

    def cut(section: str, text: str, n: int) -> str:
        if len(text) > n:
            omissions.append(f"{section}: {len(text) - n} characters cut (budget {n})")
        return _cut(text, n)

    camp = one(conn, "SELECT * FROM campaigns WHERE id = ?", (cid,))
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
    last_event = one(conn, "SELECT MAX(id) AS m FROM events WHERE campaign_id = ?", (cid,))["m"] or 0
    last_save = one(conn, "SELECT id, label FROM save_points WHERE campaign_id = ? ORDER BY id DESC LIMIT 1", (cid,))
    ident = [f"Campaign #{cid}: {camp['name']}{' (DEMO DATA)' if camp['is_demo'] else ''} | state version: event {last_event}"
             + (f", save #{last_save['id']}" if last_save else ""),
             f"Location: {camp['current_location'] or 'unknown location'}{' [SAFE PLACE]' if camp['safe_room'] else ' [NOT a safe place]'}",
             f"Game time: {now // 60}h{now % 60:02d}m elapsed"]
    if scene and scene.get("location") and camp["current_location"] and             scene["location"].split(",")[0].split(" — ")[0].strip().lower() != camp["current_location"].split(",")[0].split(" — ")[0].strip().lower():
        ident.append(f"NOTE: the campaign record places the party at {camp['current_location']}"
                     f"{' (saferoom)' if camp['safe_room'] else ''}, but the current scene is at {scene['location']}. Records disagree: do not assume "
                     "saferoom protection in the scene; settle it in play and record it with op set_location.")
    linked = immediate_objective(conn, cid)
    if linked:
        ident.append("Immediate objective (quest records; set with op quest immediate=true/false): "
                     + "; ".join(f"{q['title']} [quest #{q['id']}]" for q in linked))
    elif camp["active_objective"]:
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
    sections["CURRENT SCENE"] = cut("CURRENT SCENE", "\n".join(sc), B["scene"])

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
    here = (camp["current_location"] or "").strip().lower()
    if here and not pc_group:  # an NPC recorded at the PC's location (op npc) is in the room even if no scene lists them
        present += [c["id"] for c in chars if not c["in_party"] and (c.get("location") or "").strip().lower() == here
                    and c["status"] not in ("deceased", "dead") and c["id"] not in present]
    named = [i for i in _named(chars, focus_text) if i != (pc or {}).get("id")]
    po = []
    for g in groups:
        members = [c for c in chars if c.get("assignment") == g["key"]]
        tag = " ← PLAYER CHARACTER IS HERE" if g["key"] == pc_group else ""
        po.append(f"- [{g['key']}] {g['label']} @ {g['location'] or 'location not recorded'}{tag}" + (f" — {g['activity']}" if g["activity"] else ""))
        po.append("  members: " + ", ".join(f"{c['name']} (id {c['id']}, {_pa(conn, c)}, {c['status']})" for c in members))
    loose = [c for c in chars if not c.get("assignment") and c["id"] != (pc or {}).get("id") and c["status"] not in ("deceased", "dead")]
    if loose and groups:
        po.append("- Not in a group: " + ", ".join(f"{c['name']} (id {c['id']}, {c['location'] or 'location unknown'})" for c in loose))
    if not groups:
        po.append("No party groups recorded: everyone in the active party is assumed together. Record splits with op group / move_group.")
    po.append("Only characters PRESENT below can act in this scene; others are elsewhere doing their activity (resolve offscreen per §15).")
    sections["PARTY"] = cut("PARTY", "\n".join(po), B["party"])

    # 4 PRESENT CHARACTERS (+ named elsewhere)
    full_ids = present[:B["max_present_full"]] + [i for i in named if i not in present][:B["max_named_absent"]]
    if B["include_absent_party"]:  # large profile: everyone in the party, labelled NOT PRESENT, so offscreen play stays in character
        full_ids += [c["id"] for c in chars if c["in_party"] and c["id"] not in full_ids and c["status"] not in ("deceased", "dead")]
    blocks, companions = [], 0
    for i in full_ids:
        c = dict(by_id[i])
        c["npc_profile"] = one(conn, "SELECT * FROM npc_profiles WHERE character_id = ?", (i,))
        where = None if i in present else f"NOT PRESENT (group {c.get('assignment') or '-'}, {c.get('location') or 'location unknown'})"
        role = "PLAYER CHARACTER — controlled only by the player" if pc and i == pc["id"] else where
        sheet = _full_char(conn, c, role, budget=10**6)
        if len(sheet) > B["present_each"]:
            omissions.append(f"PRESENT: {c['name']}'s sheet cut by {len(sheet) - B['present_each']} characters")
        blocks.append(_cut(sheet, B["present_each"]))
        blocks.append(_tickets_block(conn, cid, c))
        if pc and i == pc["id"]:
            continue
        mem = rows(conn, "SELECT content, visibility FROM memories WHERE campaign_id = ? AND character_id = ? ORDER BY importance, id DESC LIMIT 6", (cid, i))
        if mem:
            blocks.append("memories: " + " | ".join(("[GM-ONLY] " if m["visibility"] == "gm" else "[party saw] ") + m["content"][:220] for m in mem))
        comp = one(conn, "SELECT id, content FROM gm_secrets WHERE campaign_id = ? AND kind = 'companion_sheet' AND subject = ? AND revealed = 0", (cid, c["slug"]))
        if comp and companions < B["max_companions"]:
            companions += 1
            blocks.append(f"GM-ONLY private companion sheet (secret id {comp['id']}; perform it, never expose it):\n"
                          + cut(f"PRESENT: {c['name']}'s companion sheet", comp["content"], B["companion"]))
        elif comp:
            omissions.append(f"PRESENT: {c['name']}'s companion sheet left out (max_companions {B['max_companions']})")
    absent_named = [by_id[i]["name"] for i in named if i not in full_ids]
    if len(present) > B["max_present_full"]:
        omissions.append(f"PRESENT: {len(present) - B['max_present_full']} present characters shown compact (max_present_full {B['max_present_full']})")
        blocks.append("Also present (compact): " + ", ".join(f"{by_id[i]['name']} (id {i}): {by_id[i].get('role_id') or 'no role'}, {_pa(conn, by_id[i])}"
                                                          for i in present[B["max_present_full"]:]))
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
    sections["OBJECTIVES"] = cut("OBJECTIVES", "\n".join(ob), B["objectives"])

    # shared stash (resources) belongs with objectives/resources
    shared = rows(conn, "SELECT id, name, quantity, deployed, description, properties_json FROM items WHERE campaign_id = ? AND owner_character_id IS NULL ORDER BY deployed, name", (cid,))
    if shared:
        sections["SHARED STASH"] = ("(qty ? = count not established; set_quantity before spending)\n"
                                    + ", ".join(f"#{s['id']} {s['name']} ×{s['quantity'] if s['quantity'] is not None else '?'}{' (deployed)' if s['deployed'] else ''}{_item_note(s)}" for s in shared))

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
    rel_all = [f for s, f in scored if s >= 2]
    rel = rel_all[:B["max_facts"]]
    if len(rel_all) > len(rel):
        omissions.append(f"MEMORY: {len(rel_all) - len(rel)} more matching facts not included (max_facts {B['max_facts']})")
    if rel:
        mem.append("Relevant established facts: " + " | ".join(f"{f['fact'][:260]}" + ("" if f["certainty"] == "canonical" else f" [{f['certainty']}]") for f in rel))
    mem.append("History beyond this briefing: lookup_records (imported ledger) and the event log. If a needed memory can't be found, "
               "say so and make an explicit provisional ruling; never invent continuity.")
    sections["MEMORY"] = cut("MEMORY", "\n".join(mem), B["memory"])

    # 7 RULES
    ru = []
    for r in rows(conn, "SELECT section, title, text FROM house_rules WHERE campaign_id = ? AND always_include = 1 ORDER BY id", (cid,)):
        ru.append(f"### {r['section'] or ''} {r['title']}\n" + cut(f"HOUSE RULES: §{r['section'] or ''} {r['title']}", r["text"], B["rules_always_each"]))
    hr = rows(conn, "SELECT section, title, text, label FROM house_rules WHERE campaign_id = ? AND always_include = 0", (cid,))
    act = _words(player_input)
    hits = sorted(((len(act & _words(h["title"] + " " + h["text"][:1500])), h) for h in hr), key=lambda x: -x[0])
    rel_rules = [h for s, h in hits if s >= 2 or B["max_house_rules"] > 50][:B["max_house_rules"]]
    if rel_rules:
        label = "Possibly relevant house rules for this action" if B["max_house_rules"] <= 50 else "All other house rules (most relevant first)"
        ru.append(label + ":\n" + cut("HOUSE RULES: relevant", "\n".join(f"- §{h['section'] or ''} {h['title']} ({h['label'] or 'campaign'}): {h['text'][:B['house_rule_each']]}" for h in rel_rules), B["rules_relevant"]))
    # rulings: only precedents carry forward (approved, or the owner's CAMPAIGN/BOOK rulings); provisional ones only for the
    # last few turns and labelled as such; rejected ones never (memory/rulings.py)
    from .rulings import ruling_status
    rl = rows(conn, "SELECT topic, ruling, label, certainty, review, turn_id FROM rulings WHERE campaign_id = ? ORDER BY id DESC LIMIT 300", (cid,))
    prec = [r for r in rl if ruling_status(r) == "precedent"]
    prec = sorted(prec, key=lambda r: -len(act & _words(r["topic"] + " " + r["ruling"])))[:8]
    if prec:
        ru.append("Precedents (approved rulings; apply them): " + " | ".join(f"({r['label']}) {r['topic']}: {r['ruling'][:240]}" for r in prec))
    recent_turns = {t["id"] for t in rows(conn, "SELECT id FROM turns WHERE campaign_id = ? ORDER BY id DESC LIMIT 10", (cid,))}
    prov = [r for r in rl if ruling_status(r) == "provisional" and r["turn_id"] in recent_turns][:5]
    if prov:
        ru.append("Provisional rulings from recent turns (keep consistent within the scene; NOT precedent, awaiting the owner's review): "
                  + " | ".join(f"({r['label']}) {r['topic']}: {r['ruling'][:200]}" for r in prov))
    ru.append("Anything else: search_rules. Unsupported mechanics: retrieve, then record_ruling a provisional label.")
    sections["HOUSE RULES"] = "\n".join(ru)
    if rule_conn is not None and len(act) >= 2 and B["official_passages"] > 0:
        from ..reference import rulebooks
        ps = rulebooks.search(rule_conn, player_input, limit=B["official_passages"])
        if ps:
            sections["RULEBOOK"] = ("Auto-retrieved rulebook passages (keyword match; read_rule_page before citing):\n"
                                    + cut("RULEBOOK", "\n".join(f"- {p.ref()}: {p.text[:600]}" for p in ps), B["official"]))

    # 8 GM-ONLY
    pres_words = _words(" ".join(by_id[i]["name"] + " " + by_id[i]["slug"] for i in present + named) + " " + (scene["title"] if scene else "") + " " + player_input)
    secrets = rows(conn, "SELECT id, subject, kind, content FROM gm_secrets WHERE campaign_id = ? AND revealed = 0 AND kind != 'companion_sheet' ORDER BY id DESC", (cid,))
    ranked = sorted(((len(pres_words & _words(s["subject"] + " " + s["content"][:400])), -s["id"], s) for s in secrets), key=lambda x: (-x[0], x[1]))
    hi = [s for score, _, s in ranked if score > 0]
    chosen = hi[:max(B["max_secrets"] - 4, 0)] + [s for score, _, s in ranked if score == 0][:min(4, B["max_secrets"])]
    if len(secrets) > len(chosen):
        omissions.append(f"GM-ONLY SECRETS: {len(secrets) - len(chosen)} lower-ranked unrevealed secrets not included "
                         f"({max(len(hi) - len(chosen), 0)} of them matched this scene)")
    hidden_tickets = [f"- {by_id[i]['name']} secretly holds {d['name'] if d else t['ticket_ref']}"
                      + (f" ({'equipped' if t['equipped'] else 'not equipped'}): {d['text']}" if d else "")
                      for i in full_ids for t, d in _tickets(conn, i, hidden=True)]
    if hidden_tickets:
        sections["GM-ONLY SECRETS"] = ("Hidden tropes (reveal only through play or a trope such as Trope Master, then op reveal_ticket):\n"
                                       + "\n".join(hidden_tickets))
    if chosen:
        sections["GM-ONLY SECRETS"] = (sections.get("GM-ONLY SECRETS", "") + "\n" if hidden_tickets else "") + cut("GM-ONLY SECRETS", "Never reveal directly; reveal only through play, then reveal_secret.\n"
                                          + "\n".join(f"- #{s['id']} [{s['kind']}] {s['subject']}: {s['content'][:500]}" for s in chosen), B["secrets"])

    # transcript
    if recent:
        long_msgs = sum(1 for m in recent if len(m["content"]) > B["transcript_msg"])
        if long_msgs:
            omissions.append(f"RECENT TRANSCRIPT: {long_msgs} long messages cut to {B['transcript_msg']} characters")
        sections["RECENT TRANSCRIPT"] = "\n".join(f"[{m['role'].upper()}] {m['content'][:B['transcript_msg']]}" for m in recent)
    else:
        sections["RECENT TRANSCRIPT"] = "(No play has happened in this app yet. Use lookup_records for where the campaign left off.)"

    text = "\n\n".join(f"## {name}\n{body}" for name, body in sections.items())
    categories: dict[str, int] = {}
    for k, v in sections.items():
        categories[CATEGORIES.get(k, "other")] = categories.get(CATEGORIES.get(k, "other"), 0) + len(v)
    return {"kind": "context", "text": text, "player_character": pc["name"] if pc else "player",
            "sizes": {k: len(v) for k, v in sections.items()}, "categories": categories, "omissions": omissions,
            "group_keys": [g["key"] for g in groups], "open_quest_ids": [q["id"] for q in rows(conn, "SELECT id FROM quests WHERE campaign_id = ? AND visibility = 'player' AND status IN ('active','paused')", (cid,))],
            "present": [by_id[i]["name"] for i in present], "named": [by_id[i]["name"] for i in named]}


def immediate_objective(conn, cid: int) -> list[dict]:
    """The active quests linked as the immediate objective, in order (campaigns.objective_quests)."""
    ids = json.loads(one(conn, "SELECT objective_quests_json AS j FROM campaigns WHERE id = ?", (cid,))["j"] or "[]")
    by_id = {q["id"]: q for q in rows(conn, "SELECT id, title FROM quests WHERE campaign_id = ? AND status = 'active' AND visibility = 'player'", (cid,))}
    return [by_id[i] for i in ids if i in by_id]
