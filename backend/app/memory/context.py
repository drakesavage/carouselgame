"""GM Context Builder: a bounded, structured briefing for every GM call (never the whole transcript or rulebook).

Sections, in order:
  1 CAMPAIGN        identity, state version, floor, location, game time, dungeon day, collapse clock, cycles
  2 CURRENT SCENE   the active scene record + recent significant events
    STORY           the active story: phase, pressure, camera, cast, obligations, revisions, endings (or OMENS HERE when none)
    OMEN LEADS      GM-only: item-bound and special Omens anchored where the group stands, with their spawn condition
    VENUE           inside a venue: the zone, the ways on (blocked ones marked), zones seen, entry and exit, this story's
                    overlays; then GM-ONLY the venue's Omen cues and where they sit
    OPENING         during the new-game flow: the stage, its job, what blocks the next stage, the lure, the friends, Silas's options
  3 PARTY           every group: location, activity, members (who is present vs elsewhere)
    CAMP RETURN     the first turn back at camp: time away, stories, new posters, residents around (shown once)
    AROUND          Camp Dyer residents where the group is (carousel/residents.py): one line each, unmet ones by template id;
                    their adaptation hooks go to GM-ONLY
  4 PRESENT         full sheets for the player character and everyone present (scene participants, the PC's group,
                    anyone named); GM-ONLY companion sheets and memories for them
  5 OBJECTIVES      quests, projects, crafting, commitments, reservations (details, due times, owners)
  6 MEMORY          closed-scene summaries, transcript summaries, relevant canonical facts, the last N messages
  7 HOUSE RULES     always-apply constitution sections, house rules matching this action, recent rulings
  8 RULEBOOK        auto-retrieved rulebook passages with page refs (to verify before citing)
  9 GM-ONLY         the active story's sealed truth (never cut), relevant unrevealed secrets
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

from ..carousel.fate import GONE
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
              "ACTIVE COMBAT": "state", "STORY": "state", "OMENS HERE": "state", "OMEN LEADS": "gm_private", "VENUE": "state", "OPENING": "state", "FATE": "state", "DEATHWATCH": "state", "AROUND": "state", "CAMP RETURN": "state", "LEDGER RECORDS": "ledger", "CURRENT SCENE": "scene_memory", "MEMORY": "scene_memory", "HOUSE RULES": "contract_rules",
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
    shown = [m for m in sh["modifiers"] if not m["hidden"]]
    if shown:
        lines.append("modifiers: " + "; ".join(f"#{m['id']} {m['target']} {'x' if m['op'] == 'multiply' else '+'}{m['value']:g} "
                                              f"from {m['source']} ({m['scope']})" for m in shown))
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


def _story_section(conn: sqlite3.Connection, story: dict, packet: dict) -> str:
    """The GM's view of the running story: everything but the sealed truth (that goes in GM-ONLY)."""
    from ..carousel.stories import PHASES
    sp, endings = packet["screenplay"], packet["endings"]
    omen = one(conn, "SELECT choice_trigger FROM ref.omens WHERE id = ?", (story["omen_id"],)) or {}
    lines = [f"{story['title']} (story #{story['id']}, Omen {story['omen_id']}, lock {story['story_lock']}, "
             f"Choice action {omen.get('choice_trigger') or story['choice_action']})",
             f"Phase {story['phase']} | pressure {story['pressure']}/6 (GM-only) | camera {story['camera']} | screenplay version {story['version']}"]
    cast = rows(conn, """SELECT c.id, c.name, x.casting, x.joined_phase, x.screen_moments FROM story_cast x JOIN characters c ON c.id = x.character_id
                         WHERE x.story_id = ? ORDER BY x.screen_moments, c.id""", (story["id"],))
    lines.append("Cast (fewest screen moments first; bring quiet ones on-screen): " + "; ".join(
        f"{c['name']} (id {c['id']}) {c['casting']}, screen moments {c['screen_moments']}" + (f", joined at {c['joined_phase']}" if c["joined_phase"] != "Party" else "")
        for c in cast))
    plan = {p.get("phase"): p.get("obligation") for p in sp.get("phase_plan") or []}
    i = PHASES.index(story["phase"])
    nxt = PHASES[i + 1] if i + 1 < len(PHASES) else None
    lines.append(f"Now ({story['phase']}): {plan.get(story['phase']) or 'no scripted obligation'}")
    if nxt:
        lines.append(f"Next ({nxt}): {plan.get(nxt) or 'no scripted obligation'}")
    for n in sp.get("notes") or []:
        ops = "; ".join(f"{o['family']}: {o['detail']}" for o in n.get("operations") or [])
        lines.append(f"Revision v{n.get('version')} ({n.get('scope')}, {n.get('by')}): {ops}"
                     + (f" | costs: {', '.join(n['costs'])}" if n.get("costs") else "")
                     + (f" | new dangers: {', '.join(n['new_dangers'])}" if n.get("new_dangers") else "")
                     + (f" | keep: {', '.join(n['preserved_facts'])}" if n.get("preserved_facts") else ""))
    lines.append("Endings (conclude_story; full/costly success only from the Finale): " + " | ".join(
        f"{k}: {(v or {}).get('predicate', '')}" for k, v in endings.items()))
    return "\n".join(lines)


def _sealed_truth(story: dict, packet: dict) -> str:
    hs = (packet["sealed"] or {}).get("house_story") or {}
    ab = hs.get("antagonist_behavior")
    lines = [f"Sealed truth (story #{story['id']}, sha256 {packet['sealed_sha256'][:12]}): never changes; a deduction that fits it succeeds.",
             f"- Truth: {hs.get('secret_truth') or 'not recorded'}"]
    if hs.get("actionable_counterplay"):
        lines.append(f"- Counterplay: {hs['actionable_counterplay']}")
    if ab:
        lines.append(f"- Antagonist: {ab.get('policy') if isinstance(ab, dict) else ab}")
    for c in hs.get("clue_routes") or []:
        lines.append(f"- Clue ({c.get('route') or c.get('id')}): {c.get('evidence')}")
    return "\n".join(lines)


OPENING_JOBS = {
    "car": "Choose the lure (set_lure), create the three friends to fit it (npc, in_party), and open on the drive with everyone talking. "
           "Let the player adjust or cut a friend (drop_friend) before the conversation gets going. Give each person natural moments to show who they are.",
    "silas": "Signal and radio die, the road ends in the abandoned lot, everyone walks. Silas deals each character three tickets with "
             "silas_assigns, quoting what they said or did in the car.",
    "opening_omen": "The group is pulled toward a story almost at once: sight an Omen near them. Only the player's own Choice starts it.",
    "escort": "Veterans (introduce_resident, in_party) walk the group across town to Dyer's Lodge, dodging Omens. At the Lodge, pay off "
              "the lure (pay_off_lure).",
}


MAX_LEADS, LEAD_CONDITION_CHARS = 4, 200
LEADS_HEADER = "GM only; never say these exist. Sight one with sight_omen only when play meets its condition"


def _omen_leads(conn: sqlite3.Connection, cid: int, place: str) -> str | None:
    """Item-bound and special Omens anchored at `place`, which the engine never surfaces itself: the GM sights one when
    play meets its condition. An item that travels is listed at its anchor only; following it is the GM's job. Unseen,
    not placed in a venue zone (entering the zone sights those) and not held by an active story's lock. Never the
    sealed dossier, beats or story title."""
    leads = rows(conn, """SELECT o.id, o.spawn_mode, o.description, json_extract(o.raw_json, '$."Spawn / Timing"') AS cond
                          FROM ref.omens o JOIN ref.places p ON p.id = o.place_id
                          WHERE o.record_type = 'Playable Omen' AND o.spawn_mode IN ('item','special') AND o.place_id = ?
                            AND p.north IS NOT NULL
                            AND NOT EXISTS (SELECT 1 FROM omen_states s WHERE s.campaign_id = ? AND s.omen_id = o.id)
                            AND NOT EXISTS (SELECT 1 FROM ref.venue_omens v WHERE v.omen_id = o.id AND v.placed = 1)
                            AND NOT EXISTS (SELECT 1 FROM stories st WHERE st.campaign_id = ? AND st.status = 'active'
                                            AND st.story_lock = o.story_lock)
                          ORDER BY o.id LIMIT ?""", (place, cid, cid, MAX_LEADS))
    if not leads:
        return None
    return f"({LEADS_HEADER})\n" + "\n".join(
        f"- {o['id']} [{o['spawn_mode']}] {o['description'] or ''}. Condition: {_cut(o['cond'] or 'not recorded', LEAD_CONDITION_CHARS)}"
        for o in leads)


def _venue_section(conn: sqlite3.Connection, cid: int) -> str | None:
    """Only the zones the group may know (visited, or one link from here); the Omen cues come last, marked GM-ONLY."""
    from ..carousel import venues
    from ..carousel.stories import active_story
    venue_id, here = venues.position(conn, cid)
    v = venues.venue(conn, venue_id) if venue_id else None
    z = venues.zone(conn, here) if here else None
    if not v or not z:
        return None
    out = [f"{v['title']} — you are at {z['label']} ({z['area_type']})", "Ways on:"]
    if v["layer"] in venues.STORY_LAYERS:
        out.insert(0, f"Inside {v['title']} (story venue; travel is closed until the group leaves by "
                      f"{venues.label(conn, v['exit_zone'])} or the story ends).")
    out += [f"- {n['label']}" + (f" [blocked: {n['blocked_reason']}]" if n["blocked_reason"] else "")
            for n in venues.neighbours(conn, cid, here)] or ["- none"]
    out.append("Zones seen: " + ", ".join(venues.label(conn, i) or i for i in venues.visited(conn, cid, venue_id)))
    out.append(f"Entry: {venues.label(conn, v['entry_zone'])}. Exit: {venues.label(conn, v['exit_zone'])}.")
    story = active_story(conn, cid)
    if story:
        ov = rows(conn, "SELECT * FROM venue_overlays WHERE story_id = ? AND venue_id = ? AND lifted = 0 ORDER BY id", (story["id"], venue_id))
        out.append("Overlays from this story:")
        out += [f"- blocked: {venues.label(conn, o['a'])} – {venues.label(conn, o['b'])} ({o['reason']})" if o["kind"] == "block"
                else f"- added room: {o['label']}, off {venues.label(conn, o['b'])} ({o['reason']})" for o in ov] or ["- none"]
    cues = rows(conn, """SELECT vo.omen_id, vo.zone_id, vo.placed, o.description, o.choice_trigger FROM ref.venue_omens vo
                         LEFT JOIN ref.omens o ON o.id = vo.omen_id WHERE vo.venue_id = ? ORDER BY vo.omen_id""", (venue_id,))
    if cues:
        out.append("GM-ONLY cues placed in this venue (never tell the player where these sit):")
        out += [f"- {c['omen_id']} at {venues.label(conn, c['zone_id']) or 'unplaced'}: {c['description']} "
                f"(Choice {c['choice_trigger'] or 'not recorded'})" + ("" if c["placed"] else " (GM places it)") for c in cues]
    return "\n".join(out)


def _opening_section(conn: sqlite3.Connection, cid: int) -> str | None:
    """The new-game flow's stage and job, its blockers, the lure belief, the friends, and (in the car and at Silas) the
    starting Archetypes and contextual tickets Silas can deal, read from the reference data."""
    from ..carousel import opening
    from ..carousel.refdata import has_reference
    stage = opening.stage(conn, cid)
    if stage is None or stage == "done":
        return None
    camp = one(conn, "SELECT lure_belief FROM campaigns WHERE id = ?", (cid,))
    blocked = opening.blockers(conn, cid)
    out = [f"Stage: {stage} — {OPENING_JOBS[stage]}",
           "Next stage blocked by: " + "; ".join(blocked) if blocked else f"Next stage: ready: advance_opening to {opening.next_stage(stage)}",
           f"Why we came (the players' belief): {camp['lure_belief'] or 'not set yet'}"]
    fr = opening.friends(conn, cid)
    out.append("Friends: " + (", ".join(f"{f['name']}: {f['role_id'] or 'unassigned'}" for f in fr) if fr else "none yet"))
    if stage in ("car", "silas") and has_reference(conn):
        out.append("Silas's starting Archetypes (role id, name: play identity; stats):")
        for r in rows(conn, """SELECT r.id, r.name, r.raw_json, p.mettle, p.moxie, p.hustle, p.savvy, p.grit FROM ref.roles r
                               JOIN ref.start_profiles p ON p.role_id = r.id WHERE r.kind = 'base' ORDER BY r.id"""):
            ident = (r["raw"] or {}).get("play_identity") or ""
            out.append(f"- {r['id']}, {r['name']}: {ident}; Mettle {r['mettle']}, Moxie {r['moxie']}, Hustle {r['hustle']}, "
                       f"Savvy {r['savvy']}, Grit {r['grit']}")
        out.append("Contextual starter tickets (deal exactly one each; the Archetype's signature ticket comes with it):")
        out += [f"- {t['id']}, {t['name']}: {t['text']}" for t in rows(
            conn, "SELECT id, name, text FROM ref.tickets WHERE relationship = 'contextual_shared_starter' ORDER BY id")]
    return "\n".join(out)


FATE_DEFAULTS = (("physical_condition", "body", "alive"), ("wallpaper_status", "wallpaper", "active"),
                 ("participation", "participation", "full"), ("persistent_fate", "fate", "ongoing"))
DEATHWATCH_COPY = ("The player's character can't act. The player is watching. Treat what they type as requests for a cut, a pace or a summary, "
                   "never as orders to the survivors. Survivors act on their own knowledge, goals and fear. No convenient rescue, no forced tragedy; "
                   "the story runs to a real ending. Pace: {pace} (narration length only).")


def _board(conn: sqlite3.Connection, cid: int) -> list[dict]:
    return rows(conn, """SELECT p.*, c.rescue_eligibility FROM missing_posters p JOIN characters c ON c.id = p.character_id
                         WHERE p.campaign_id = ? AND p.held_by_character_id IS NULL AND p.rescued_minutes IS NULL ORDER BY p.id""", (cid,))


def _fate_section(conn: sqlite3.Connection, cid: int, story: dict | None) -> str | None:
    """Changed fate axes of the running story's cast, the warnings given in it, and the missing-poster board.
    Causes and rescue eligibility are GM-only and go to _fate_secrets instead."""
    board = _board(conn, cid)
    if not story and not board:
        return None
    out = []
    if story:
        for c in rows(conn, "SELECT c.* FROM story_cast x JOIN characters c ON c.id = x.character_id WHERE x.story_id = ? ORDER BY c.party_order, c.id",
                      (story["id"],)):
            off = [f"{label} {c[col].replace('_', ' ')}" for col, label, default in FATE_DEFAULTS if c[col] != default]
            if off:
                out.append(f"{c['name']}: {' · '.join(off)}")
        warned = rows(conn, "SELECT rule, warning FROM danger_warnings WHERE story_id = ? ORDER BY id", (story["id"],))
        if warned:
            out.append("Warnings given: " + "; ".join(f"{w['rule']}: {w['warning']}" for w in warned))
        if not out:
            out.append("Everyone in the cast is alive and on screen.")
    if board:
        out.append(f"Board outside the Diner: {len(board)} poster{'' if len(board) == 1 else 's'} ({', '.join(p['name'] for p in board)})")
    return "\n".join(out)


def _fate_secrets(conn: sqlite3.Connection, cid: int, story: dict | None) -> str:
    out = []
    if story:
        out += [f"- Death cause: {d['name']} ({d['phase']}): {d['cause']}" for d in rows(
            conn, "SELECT d.phase, d.cause, c.name FROM death_events d JOIN characters c ON c.id = d.character_id WHERE d.story_id = ? ORDER BY d.id",
            (story["id"],))]
        if story.get("rescue"):
            ids = story["rescue"]["poster_ids"]
            names = [r["name"] for r in rows(conn, f"SELECT name FROM missing_posters WHERE id IN ({','.join('?' * len(ids))}) ORDER BY id", ids)]
            out += [f"- This story is a Rescue for: {', '.join(names)}", "- Takeover: only the rescue ticket may revise this story"]
    out += [f"- Rescue eligibility: {p['name']} {p['rescue_eligibility']}" for p in _board(conn, cid)]
    return "Fate (never tell the players):\n" + "\n".join(out) if out else ""


def _firewall_line(conn: sqlite3.Connection, pc: dict | None) -> str | None:
    """A restored player character died in the last story: they know nothing of what came after their death."""
    if not pc or pc["persistent_fate"] != "restored":
        return None
    d = one(conn, """SELECT d.phase, s.title FROM death_events d JOIN stories s ON s.id = d.story_id WHERE d.character_id = ?
                     AND s.id = (SELECT MAX(id) FROM stories WHERE campaign_id = s.campaign_id AND status = 'concluded') ORDER BY d.id DESC LIMIT 1""",
            (pc["id"],))
    if not d:
        return None
    ident = (pc.get("sheet") or {}).get("identity")
    pronoun = str((ident or {}).get("pronouns") or "they").split("/")[0].strip().lower() if isinstance(ident, dict) else "they"
    verb = "don't" if pronoun in ("they", "") else "doesn't"
    return f"{pc['name']} died at {d['phase']} in {d['title']}; {pronoun or 'they'} {verb} know what happened after that unless told"


CAMP_RETURN_COPY = ("The group is back at camp after {h}h{mm:02d}. Since they left: {stories}; new posters: {posters}. Residents around now: "
                    "{around}. You may add colour (who noticed, rumours, who asks about the story), recorded with record_fact or remember. "
                    "Nothing happened that the records don't show.")


def _camp_return_section(back: dict) -> str:
    stories = "; ".join(f"{s['title']} ({s['ending']}; cast: {', '.join(s['cast_names'])})" for s in back["stories"]) or "no stories"
    return CAMP_RETURN_COPY.format(h=back["away_minutes"] // 60, mm=back["away_minutes"] % 60, stories=stories,
                                   posters=", ".join(back["posters"]) or "none", around=", ".join(back["around"]) or "none")


MAX_AROUND = 12


def _around_section(conn: sqlite3.Connection, near: list[dict]) -> str:
    """One line per resident around the group (met first); past MAX_AROUND, the rest as a count."""
    out = []
    for a in near[:MAX_AROUND]:
        t = one(conn, """SELECT t.id, t.camp_role, c.bio FROM characters c JOIN npc_profiles p ON p.character_id = c.id
                         JOIN ref.npc_templates t ON t.id = p.ref_id WHERE c.id = ?""", (a["id"],)) or {}
        role = t.get("camp_role") or "no camp role recorded"
        out.append(f"{a['name']} (met) — {role}" if a["met"] else
                   f"Not yet met: {t.get('id')} — {t.get('bio') or 'no description recorded'}; {role}. "
                   "Introduce with introduce_resident if they come into the scene.")
    if len(near) > MAX_AROUND:
        out.append(f"…and {len(near) - MAX_AROUND} more around (not listed)")
    return "\n".join(out)


def _around_hooks(conn: sqlite3.Connection, near: list[dict]) -> str:
    """The companion sheet's adaptation_hook for each resident listed in AROUND, nothing else from it."""
    out = []
    for a in near[:MAX_AROUND]:
        sheet = one(conn, """SELECT g.content, p.ref_id FROM gm_secrets g JOIN characters c ON c.slug = g.subject AND c.campaign_id = g.campaign_id
                             JOIN npc_profiles p ON p.character_id = c.id WHERE c.id = ? AND g.kind = 'companion_sheet'""", (a["id"],))
        try:
            hook = ((json.loads(sheet["content"]) if sheet else {}).get("gm_only") or {}).get("adaptation_hook")
        except (ValueError, AttributeError):
            hook = None
        if hook:
            out.append(f"- {a['name']}: {hook}" if a["met"] else f"- {sheet['ref_id']} ({a['name']}, not yet met): {hook}")
    return "Residents around (what they want; play it, never tell the players):\n" + "\n".join(out) if out else ""


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
    from ..carousel import residents
    chars = residents.in_briefing(conn, cid, rows(conn, "SELECT * FROM characters WHERE campaign_id = ? ORDER BY party_order, id", (cid,)))
    by_id = {c["id"]: c for c in chars}
    unmet = residents.unmet_ids(conn, cid)
    listable = [c for c in chars if c["id"] not in unmet]   # unmet residents appear only in AROUND, one line each
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
    firewall = _firewall_line(conn, pc)
    if firewall:
        ident.append(firewall)
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

    # STORY (or the Omens at hand when none is running)
    from ..carousel.refdata import has_reference
    from ..carousel.stories import active_story
    story = active_story(conn, cid)
    packet = one(conn, "SELECT * FROM story_packets WHERE story_id = ?", (story["id"],)) if story else None
    if story and packet:
        sections["STORY"] = _story_section(conn, story, packet)
    elif has_reference(conn) and camp.get("node_id"):
        here_omens = rows(conn, """SELECT o.id, o.description, o.choice_trigger, o.record_type, s.stage FROM omen_states s
                                   JOIN ref.omens o ON o.id = s.omen_id WHERE s.campaign_id = ? AND o.place_id = ? ORDER BY o.id""",
                          (cid, camp["node_id"]))
        if here_omens:
            sections["OMENS HERE"] = ("Seen here. Looking is safe; only the player's own Choice action commits (then begin_story):\n"
                                      + "\n".join(f"- {o['id']} [{o['stage']}] {o['description']} — "
                                                  + ("a hub: it never starts a story itself" if o["record_type"] != "Playable Omen"
                                                     else f"Choice action: {o['choice_trigger'] or 'not recorded (record the action with begin_story reason)'}")
                                                  for o in here_omens))

    leads = _omen_leads(conn, cid, camp["node_id"]) if has_reference(conn) and camp.get("node_id") else None
    if leads:
        sections["OMEN LEADS"] = leads

    venue_text = _venue_section(conn, cid) if has_reference(conn) else None
    if venue_text:
        sections["VENUE"] = venue_text

    opening_text = _opening_section(conn, cid)
    if opening_text:
        sections["OPENING"] = opening_text

    from ..carousel.fate import deathwatch
    fate_text = _fate_section(conn, cid, story)
    if fate_text:
        sections["FATE"] = fate_text
    watching = deathwatch(conn, cid)
    if watching:
        sections["DEATHWATCH"] = DEATHWATCH_COPY.format(pace=watching["pace"])
    back = residents.camp_return(conn, cid) if has_reference(conn) else None
    if back:
        sections["CAMP RETURN"] = _camp_return_section(back)

    # 3 PARTY ORGANIZATION
    pc_group = pc.get("assignment") if pc else None
    present: list[int] = []
    if pc:
        present.append(pc["id"])
    if scene:
        for ref in scene.get("participants") or []:
            for c in listable:
                if str(ref).lower() in (c["name"].lower(), c["slug"], str(c["id"])) and c["id"] not in present:
                    present.append(c["id"])
    for gkey in {pc_group, scene["group_key"] if scene else None} - {None}:
        present += [c["id"] for c in listable if c.get("assignment") == gkey and c["id"] not in present]
    if not groups and not scene:  # no organization recorded: treat the in-party roster as together
        present += [c["id"] for c in chars if c["in_party"] and c["id"] not in present]
    here = (camp["current_location"] or "").strip().lower()
    if here and not pc_group:  # an NPC recorded at the PC's location (op npc) is in the room even if no scene lists them
        present += [c["id"] for c in listable if not c["in_party"] and (c.get("location") or "").strip().lower() == here
                    and c["status"] not in GONE and c["id"] not in present]
    named = [i for i in _named(listable, focus_text) if i != (pc or {}).get("id")]
    po = []
    for g in groups:
        members = [c for c in listable if c.get("assignment") == g["key"]]
        tag = " ← PLAYER CHARACTER IS HERE" if g["key"] == pc_group else ""
        po.append(f"- [{g['key']}] {g['label']} @ {g['location'] or 'location not recorded'}{tag}" + (f" — {g['activity']}" if g["activity"] else ""))
        po.append("  members: " + ", ".join(f"{c['name']} (id {c['id']}, {_pa(conn, c)}, {c['status']})" for c in members))
    loose = [c for c in listable if not c.get("assignment") and c["id"] != (pc or {}).get("id") and c["status"] not in GONE]
    if loose and groups:
        po.append("- Not in a group: " + ", ".join(f"{c['name']} (id {c['id']}, {c['location'] or 'location unknown'})" for c in loose))
    if not groups:
        po.append("No party groups recorded: everyone in the active party is assumed together. Record splits with op group / move_group.")
    po.append("Only characters PRESENT below can act in this scene; others are elsewhere doing their activity (resolve offscreen per §15).")
    sections["PARTY"] = cut("PARTY", "\n".join(po), B["party"])

    near = residents.around(conn, cid) if has_reference(conn) else []
    if near:
        sections["AROUND"] = _around_section(conn, near)

    # 4 PRESENT CHARACTERS (+ named elsewhere)
    full_ids = present[:B["max_present_full"]] + [i for i in named if i not in present][:B["max_named_absent"]]
    if B["include_absent_party"]:  # large profile: everyone in the party, labelled NOT PRESENT, so offscreen play stays in character
        full_ids += [c["id"] for c in chars if c["in_party"] and c["id"] not in full_ids and c["status"] not in GONE]
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
    secrets = rows(conn, """SELECT id, subject, kind, content FROM gm_secrets WHERE campaign_id = ? AND revealed = 0
                            AND kind NOT IN ('companion_sheet', 'resident_public') ORDER BY id DESC""", (cid,))
    ranked = sorted(((len(pres_words & _words(s["subject"] + " " + s["content"][:400])), -s["id"], s) for s in secrets), key=lambda x: (-x[0], x[1]))
    hi = [s for score, _, s in ranked if score > 0]
    chosen = hi[:max(B["max_secrets"] - 4, 0)] + [s for score, _, s in ranked if score == 0][:min(4, B["max_secrets"])]
    if len(secrets) > len(chosen):
        omissions.append(f"GM-ONLY SECRETS: {len(secrets) - len(chosen)} lower-ranked unrevealed secrets not included "
                         f"({max(len(hi) - len(chosen), 0)} of them matched this scene)")
    hidden_tickets = [f"- {by_id[i]['name']} secretly holds {d['name'] if d else t['ticket_ref']}"
                      + (f" ({'equipped' if t['equipped'] else 'not equipped'}): {d['text']}" if d else "")
                      for i in full_ids for t, d in _tickets(conn, i, hidden=True)]
    hidden_tickets += [f"- {by_id[i]['name']}: secret modifier #{m['id']} {m['target']} {'x' if m['op'] == 'multiply' else '+'}{m['value']:g} "
                       f"from {m['source']} ({m['scope']})"
                       for i in full_ids for m in rows(conn, "SELECT * FROM stat_modifiers WHERE character_id = ? AND hidden = 1", (i,))]
    sealed = _sealed_truth(story, packet) if story and packet else ""   # never cut by the secrets budget
    if sealed:
        sections["GM-ONLY SECRETS"] = sealed
    if hidden_tickets:
        sections["GM-ONLY SECRETS"] = (sections["GM-ONLY SECRETS"] + "\n" if sealed else "") + ("Hidden tropes (reveal only through play or a trope such as Trope Master, then op reveal_ticket):\n"
                                       + "\n".join(hidden_tickets))
    if chosen:
        sections["GM-ONLY SECRETS"] = (sections["GM-ONLY SECRETS"] + "\n" if sealed or hidden_tickets else "") + cut("GM-ONLY SECRETS", "Never reveal directly; reveal only through play, then reveal_secret.\n"
                                          + "\n".join(f"- #{s['id']} [{s['kind']}] {s['subject']}: {s['content'][:500]}" for s in chosen), B["secrets"])
    fate_secrets = _fate_secrets(conn, cid, story)
    if fate_secrets:
        sections["GM-ONLY SECRETS"] = (sections["GM-ONLY SECRETS"] + "\n" if "GM-ONLY SECRETS" in sections else "") + fate_secrets
    hooks = _around_hooks(conn, near)
    if hooks:
        sections["GM-ONLY SECRETS"] = (sections["GM-ONLY SECRETS"] + "\n" if "GM-ONLY SECRETS" in sections else "") + hooks

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
