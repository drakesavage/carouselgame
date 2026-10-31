"""Generate a character: when an NPC (or a companion crawler) becomes part of the story, give them a full person in
one step: a look, gender and pronouns, a personality and voice, a background, and motives of their own that make
them decide things for themselves.

Two layers, each stored where the app already keeps it:
* what the party could know (the look, the manner, the background the player character would plausibly know from
  their relationship, stated motives): the character's sheet (identity, visual reference, narrative) and NPC profile,
  shown on the character page and People;
* GM-only (the whole backstory, private motives, fears, secrets, how they decide, behavior beats with triggers): a
  private companion sheet (gm_secrets kind companion_sheet), which the GM briefing already loads whenever the
  character is present (Constitution 3.2: perform it, never expose it). It never reaches a player endpoint.

Everything already recorded about the character is given to the writer as fixed; the result may add, never
contradict. A hand-authored companion sheet (for example one imported from a ledger) is never overwritten.
"""
from __future__ import annotations

import json
import sqlite3

from ..db import now_iso, one, rows, transaction

GENERATED = "generated"  # gm_secrets.source of a companion sheet this module wrote (and may rewrite)

S, L = {"type": "string"}, {"type": "array", "items": {"type": "string"}}


def _obj(props: dict) -> dict:
    return {"type": "object", "additionalProperties": False, "required": list(props), "properties": props}


SCHEMA = _obj({
    "gender": S, "pronouns": S, "apparent_age": S,
    "appearance": _obj({"summary": S, "physique": S, "outfit": S, "signature": S, "expression": S}),
    "role": S, "personality_summary": S, "traits": L, "voice": S, "quirks": L,
    "known_background": _obj({"occupation": S, "home": S, "family": S, "history": S, "relationship_to_pc": S}),
    "known_motivation": S,
    "private": _obj({
        "backstory": S, "past_trauma": S, "loose_end": S, "regret": S,
        "wants": L, "fears": L, "secrets": L, "values_and_lines": L,
        "contradiction": S, "how_they_decide": S, "under_pressure": S,
        "view_of_pc": S, "attitude_to_pc": {"type": "integer", "minimum": -3, "maximum": 3},
        "beats": {"type": "array", "items": _obj({"trigger": S, "surface_as": S})},
        "arc_seeds": L,
    }),
})

DEVELOPER = """You create a non-player character for a Dungeon Crawler Carl tabletop RPG campaign, played by
correspondence with an AI Game Master. The character is a person, not a tool of the player: they have their own
history, wants, fears and judgment, and they will sometimes disagree, hesitate, refuse or act on their own.

Rules:
- Everything under "ESTABLISHED" is canon. Keep it exactly; add to it, never contradict it. Reuse given names, jobs,
  injuries, relationships and looks.
- The world: Earth, the Collapse and the World Dungeon of the Dungeon Crawler Carl novels and RPG. Before the Collapse
  this was an ordinary person with an ordinary life. Keep their history grounded and specific (places, jobs, people,
  small details), not epic. No superpowers, no secret agents, no chosen ones.
- Place: infer where the campaign happens from the records (towns, teams, schools, landmarks, how people talk). If
  the records do not say, assume the player character's own country, by default the United States, and that people
  met at the start live in the same area unless the record says they were visiting. Never move them abroad on a whim.
- Make them distinct from the other people listed: different temperament, voice and priorities.
- Background is a life, not a character sheet: never list Stats, Skills, Ranks, Health or Mana in any field; the app
  already tracks those.
- appearance: what anyone standing in front of them sees at once. Concrete: build, height, hair, skin, face, clothes
  and gear right now, one distinguishing detail. gender, pronouns and apparent_age as a person looking at them would
  read them (pronouns may be they/them only when that is who they are).
- known_background, known_motivation, personality_summary, traits, voice, quirks: only what the player character could
  plausibly know or have seen, given their relationship (a teammate of years knows the job, family and history; a
  stranger met today knows almost nothing: say "unknown" for what they cannot know).
- private: the whole truth, for the GM only. backstory (one rich paragraph); past_trauma, loose_end and regret as in
  the Dungeon Crawler Carl character tables; wants and fears (2-4 each); secrets (1-3, proportionate: ordinary human
  secrets, not conspiracies); values_and_lines (what they will not do); contradiction (the tension inside them);
  how_they_decide and under_pressure (how they actually make choices); view_of_pc and attitude_to_pc (-3 hostile to
  +3 devoted; most new acquaintances 0, long friends 1-2); beats (3-5 behavior triggers: when X happens they Y);
  arc_seeds (2-3 ways they could change).
- Plain, specific English. No markdown inside fields. Reply only with the JSON object."""


def _record(conn: sqlite3.Connection, cid: int, c: dict) -> str:
    camp = one(conn, "SELECT name, current_location, game_time_minutes, active_character_id FROM campaigns WHERE id = ?", (cid,))
    pc = one(conn, "SELECT name, role_id, bio, sheet_json FROM characters WHERE id = ?", (camp["active_character_id"],)) if camp["active_character_id"] else None
    sheet = c.get("sheet") or {}
    prof = one(conn, "SELECT personality, voice, known_motivation, attitude FROM npc_profiles WHERE character_id = ?", (c["id"],)) or {}
    out = [f"CAMPAIGN: {camp['name']} | now at {camp['current_location'] or 'unknown'}"]
    premise = one(conn, "SELECT fact FROM lore_facts WHERE campaign_id = ? AND subject = 'Campaign premise' ORDER BY id LIMIT 1", (cid,))
    if premise:
        out.append(f"Premise: {premise['fact']}")
    if pc:
        out.append(f"PLAYER CHARACTER: {pc['name']} ({pc['role_id'] or 'role not assigned'})"
                   + (f" — {pc['bio']}" if pc.get("bio") else ""))
    out.append("\nESTABLISHED about the character to create (canon):")
    out.append(f"- name: {c['name']} | kind: {c['kind']} | roster: {c.get('roster_category') or '-'} | status: {c['status']}")
    for k, v in (("role", c.get("role_id")), ("location", c.get("location")), ("description", c.get("bio"))):
        if v:
            out.append(f"- {k}: {v}")
    for k, v in sheet.items():
        if k in ("identity", "narrative", "visual") and v:
            out.append(f"- {k}: {json.dumps(v, ensure_ascii=False)[:1500]}")
    for k in ("personality", "voice", "known_motivation"):
        if prof.get(k):
            out.append(f"- {k}: {prof[k]}")
    facts = rows(conn, "SELECT fact FROM lore_facts WHERE campaign_id = ? AND lower(subject) LIKE ? ORDER BY id LIMIT 12", (cid, f"%{c['name'].lower()}%"))
    mems = rows(conn, "SELECT content FROM memories WHERE campaign_id = ? AND character_id = ? ORDER BY id DESC LIMIT 8", (cid, c["id"]))
    seen = rows(conn, """SELECT summary FROM events WHERE campaign_id = ? AND rolled_back = 0 AND visibility = 'player' AND summary LIKE ?
                         ORDER BY id DESC LIMIT 12""", (cid, f"%{c['name']}%"))
    for label, items, key in (("fact", facts, "fact"), ("memory", mems, "content"), ("event", seen, "summary")):
        for x in items:
            out.append(f"- {label}: {x[key][:300]}")
    said = rows(conn, """SELECT content FROM messages WHERE campaign_id = ? AND role = 'gm' AND rolled_back = 0 AND content LIKE ?
                         ORDER BY id DESC LIMIT 4""", (cid, f"%{c['name'].split()[0]}%"))
    for m in said:
        out.append(f"- seen in play: {m['content'][:700]}")
    old = one(conn, "SELECT content FROM gm_secrets WHERE campaign_id = ? AND kind = 'companion_sheet' AND subject = ?", (cid, c["slug"]))
    if old:
        out.append(f"- previous private sheet (keep what still fits):\n{old['content'][:2500]}")
    others = rows(conn, """SELECT name, bio, sheet_json FROM characters WHERE campaign_id = ? AND id NOT IN (?, ?) AND status NOT IN ('dead','deceased')
                           ORDER BY in_party DESC, id LIMIT 12""", (cid, c["id"], camp["active_character_id"] or -1))
    if others:
        def fam(o):  # relatives already named, so a new character's family doesn't reuse them
            f = ((o.get("sheet") or {}).get("narrative") or {}).get("background") or {}
            return f" (family: {f['family'][:100]})" if isinstance(f, dict) and f.get("family") else ""
        out.append("\nOTHER PEOPLE (make this character distinct from them; do not reuse their relatives' names): "
                   + "; ".join(f"{o['name']}: {(o['bio'] or '')[:120]}{fam(o)}" for o in others))
    return "\n".join(out)


def _companion_sheet(c: dict, p: dict) -> str:
    def lst(xs):
        return "\n".join(f"  - {x}" for x in xs) or "  - (none)"
    beats = "\n".join(f"  - when: {b['trigger']}\n    they: {b['surface_as']}" for b in p["beats"]) or "  - (none)"
    return (f"character: {c['name']} ({c['slug']})\nconfidentiality: gm_only — perform it through play, never expose it\n"
            f"backstory: {p['backstory']}\npast_trauma: {p['past_trauma']}\nloose_end: {p['loose_end']}\nregret: {p['regret']}\n"
            f"wants:\n{lst(p['wants'])}\nfears:\n{lst(p['fears'])}\nsecrets:\n{lst(p['secrets'])}\n"
            f"values_and_lines:\n{lst(p['values_and_lines'])}\ncontradiction: {p['contradiction']}\n"
            f"how_they_decide: {p['how_they_decide']}\nunder_pressure: {p['under_pressure']}\nview_of_pc: {p['view_of_pc']}\n"
            f"beats:\n{beats}\narc_seeds:\n{lst(p['arc_seeds'])}")


def generate(conn: sqlite3.Connection, cid: int, char_id: int, settings, *, provider=None) -> dict:
    """Write the character (Codex, the user's ChatGPT plan) and store both layers. Returns only player-visible summary."""
    c = one(conn, "SELECT * FROM characters WHERE campaign_id = ? AND id = ?", (cid, char_id))
    if not c:
        raise LookupError("character not found")
    camp = one(conn, "SELECT active_character_id FROM campaigns WHERE id = ?", (cid,))
    if c["id"] == camp["active_character_id"]:
        raise ValueError("the player character is yours to write; Generate is for the people around them")
    old = one(conn, "SELECT id, source FROM gm_secrets WHERE campaign_id = ? AND kind = 'companion_sheet' AND subject = ?", (cid, c["slug"]))
    if old and old["source"] != GENERATED:
        raise ValueError(f"{c['name']} already has a hand-written private sheet; Generate never replaces it")
    if provider is None:
        from ..gm.providers import CodexCliProvider
        provider = CodexCliProvider(settings, effort="medium")
    from ..gm.providers import AGENTS_NOTICE
    raw, _ = provider._run(_record(conn, cid, c), SCHEMA, f"generate-{c['slug']}", DEVELOPER + "\n\n" + AGENTS_NOTICE)
    g = json.loads(raw)
    p = g["private"]

    c = one(conn, "SELECT * FROM characters WHERE id = ?", (char_id,))  # re-read: play may have moved on during the call
    sheet = c.get("sheet") or {}
    sheet["identity"] = {**{k: g[k] for k in ("gender", "pronouns", "apparent_age") if g.get(k)}, **(sheet.get("identity") or {})}
    ap = g["appearance"]
    vis = sheet.setdefault("visual", {})
    vis["reference"] = {**{"demographic": ", ".join(x for x in (g["gender"], c.get("species") or "Human") if x), "age": g["apparent_age"],
                           "physique": ap["physique"], "outfit": ap["outfit"], "signature": ap["signature"], "expression": ap["expression"]},
                        **(vis.get("reference") or {})}
    vis.setdefault("summary", ap["summary"])
    nar = sheet.setdefault("narrative", {})
    nar.setdefault("current_role", g["role"])
    nar["personality_summary"] = nar.get("personality_summary") or g["personality_summary"]
    nar["traits"] = nar.get("traits") or g["traits"]
    nar["quirks"] = nar.get("quirks") or g["quirks"]
    known = {k: v for k, v in g["known_background"].items() if v and v.strip().lower() not in ("unknown", "n/a", "none")}
    nar["background"] = {**known, **(nar.get("background") if isinstance(nar.get("background"), dict) else {})}
    with transaction(conn):
        conn.execute("UPDATE characters SET sheet_json = ?, bio = COALESCE(NULLIF(bio, ''), ?) WHERE id = ?",
                     (json.dumps(sheet, ensure_ascii=False), ap["summary"], char_id))
        prof = one(conn, "SELECT * FROM npc_profiles WHERE character_id = ?", (char_id,))
        if prof is None:
            conn.execute("INSERT INTO npc_profiles (character_id) VALUES (?)", (char_id,))
            prof = {}
        conn.execute("""UPDATE npc_profiles SET personality = COALESCE(personality, ?), voice = COALESCE(voice, ?),
                        known_motivation = COALESCE(known_motivation, ?), attitude = COALESCE(attitude, ?) WHERE character_id = ?""",
                     (g["personality_summary"], g["voice"], g["known_motivation"] if g["known_motivation"].lower() != "unknown" else None,
                      p["attitude_to_pc"], char_id))
        content = _companion_sheet(c, p)
        if old:
            conn.execute("UPDATE gm_secrets SET content = ?, created_at = ? WHERE id = ?", (content, now_iso(), old["id"]))
        else:
            conn.execute("INSERT INTO gm_secrets (campaign_id, subject, kind, content, source, created_at) VALUES (?,?,?,?,?,?)",
                         (cid, c["slug"], "companion_sheet", content, GENERATED, now_iso()))
        conn.execute("INSERT INTO events (campaign_id, type, summary, payload_json, visibility, created_at) VALUES (?,?,?,?,?,?)",
                     (cid, "npc_updated", f"{c['name']} fleshed out (look, manner, background)", json.dumps({"character_id": char_id}), "player", now_iso()))
    return {"name": c["name"], "pronouns": sheet["identity"].get("pronouns"), "appearance": ap["summary"]}
