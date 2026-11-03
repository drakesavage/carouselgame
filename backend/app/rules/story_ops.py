"""Validated story ops (registered into engine.CHANGE_OPS by rules/__init__.py).

The fixed points of a Carousel story: an Omen is sighted and examined; the player's own Choice action begins its story,
which seals the dossier's truth and endings; the Plot Cycle, pressure, camera and cast move only through these ops;
eligible tickets revise the screenplay as versioned commits; conclude_story picks one of four endings and pays rewards.
The GM improvises everything between them. Nothing here writes the sealed packet into a player-visible event."""
from __future__ import annotations

import json
from typing import Literal

from pydantic import BaseModel, Field

from ..carousel.refdata import require_reference
from ..carousel.sheet import sheet
from ..carousel.stories import ENDINGS, PHASES, active_story, canonical, endings_from, novelty, seal, ticket_can_revise
from ..db import one, rows
from .carousel_ops import UseTrope, _apply_use, _owned, recharge
from .engine import ChangeError, Ctx, resolve_character
from .venue_ops import lift_overlays

EndingName = Literal["full_success", "costly_success", "survive_without_victory", "defeat"]
# Role Bible dynamic_story_mutation_contract: what a generic story-altering grant may do (spawn_rescue_packet, the Damsel's
# ticket, is not built: Phase 6 rescues come from Rescue tickets via begin_story `rescue`; extraordinary_timeline_branch never
# comes from a generic grant).
REVISION_FAMILIES = ("insert_future_scene", "remove_or_delay_future_beat", "recast_npc", "introduce_plausible_support", "shift_pressure",
                     "reveal_authorized_fact", "create_obstacle", "rewrite_conditional_objective")

PLAYABLE = "Playable Omen"
HUB = "Hub / generator"
RELATED_CUE = "Related cue / route"


class SightOmen(BaseModel):
    op: Literal["sight_omen"]
    omen: str
    how: str = Field(min_length=1, max_length=200)


class ExamineOmen(BaseModel):
    op: Literal["examine_omen"]
    omen: str


def _omen(ctx: Ctx, oid: str) -> dict:
    require_reference(ctx.conn)
    o = one(ctx.conn, "SELECT * FROM ref.omens WHERE id = ?", (oid,))
    if not o:
        raise ChangeError(f"no Omen {oid!r} in the reference data")
    return o


def _omen_state(ctx: Ctx, oid: str) -> dict | None:
    return one(ctx.conn, "SELECT * FROM omen_states WHERE campaign_id = ? AND omen_id = ?", (ctx.campaign_id, oid))


def premise(ctx: Ctx, oid: str) -> str | None:
    """The dossier's player-visible premise (the public projection, never the sealed dossier)."""
    b = one(ctx.conn, "SELECT dossier_json FROM ref.omen_beats WHERE omen_id = ?", (oid,))
    return (b["dossier"] or {}).get("player_visible_premise") if b else None


def _apply_sight(ctx: Ctx, op: SightOmen) -> dict:
    o = _omen(ctx, op.omen)
    if o["record_type"] not in (PLAYABLE, HUB, RELATED_CUE):
        raise ChangeError(f"{op.omen} is a {o['record_type']!r} record, not an Omen the group can see")
    if _omen_state(ctx, op.omen):
        raise ChangeError(f"{op.omen} is already seen")
    node = one(ctx.conn, "SELECT node_id FROM campaigns WHERE id = ?", (ctx.campaign_id,))["node_id"]
    ctx.conn.execute("INSERT INTO omen_states (campaign_id, omen_id, stage, seen_at_node, seen_at_minutes) VALUES (?,?, 'visible', ?, ?)",
                     (ctx.campaign_id, op.omen, node, ctx.game_time()))
    summary = f"Something is wrong: {o['description']}"
    ctx.event("omen_sighted", summary, {"omen_id": op.omen, "description": o["description"], "node": node, "how": op.how})
    return {"summary": summary}


def _apply_examine(ctx: Ctx, op: ExamineOmen) -> dict:
    o = _omen(ctx, op.omen)
    if not _omen_state(ctx, op.omen):
        raise ChangeError(f"{op.omen} is not seen by the group yet")
    ctx.conn.execute("UPDATE omen_states SET stage = 'examined' WHERE campaign_id = ? AND omen_id = ?", (ctx.campaign_id, op.omen))
    text = premise(ctx, op.omen)
    summary = f"A closer look at {o['description']}" + (f": {text}" if text else "")
    ctx.event("omen_examined", summary, {"omen_id": op.omen, "premise": text})
    return {"summary": summary, "premise": text}


class CastEntry(BaseModel):
    character: str
    casting: Literal["lead", "support", "witness", "sacrifice", "outsider"]


class EndingProfile(BaseModel):
    predicate: str = Field(min_length=1, max_length=600)


class RescueEntry(BaseModel):
    """A Rescue: the rescuer, their Rescue ticket and the missing posters they carry in (rules/fate_ops.py start_rescue)."""
    character: str
    ticket: str
    posters: list[int] = Field(min_length=1, max_length=12)


class BeginStory(BaseModel):
    op: Literal["begin_story"]
    omen: str
    action: str = Field(min_length=1, max_length=200)
    cast: list[CastEntry] = Field(min_length=1, max_length=12)
    reason: str = Field(min_length=1, max_length=400)
    endings: dict[EndingName, EndingProfile] | None = None
    rescue: RescueEntry | None = None


def _norm(action: str) -> str:
    return " ".join(action.strip().lower().split())


def _resident_here(ctx: Ctx, c: dict) -> None:
    """A Camp Dyer resident joins a cast only met, not gone, and with the group or around it (carousel/residents.py)."""
    from ..carousel.residents import presence_refusal
    refused = presence_refusal(ctx.conn, ctx.campaign_id, c["id"])
    if refused:
        raise ChangeError(refused)


def _apply_begin(ctx: Ctx, op: BeginStory) -> dict:
    from ..carousel.opening import stage
    if stage(ctx.conn, ctx.campaign_id) in ("car", "silas"):
        raise ChangeError("the opening Omen comes after Silas")
    o = _omen(ctx, op.omen)
    if o["record_type"] != PLAYABLE:
        raise ChangeError(f"{op.omen} is not a Playable Omen ({o['record_type']}); a story starts only from a playable one")
    if not _omen_state(ctx, op.omen):
        raise ChangeError(f"{op.omen} is not seen by the group; it must be sighted before its story can start")
    t = one(ctx.conn, "SELECT kind FROM turns WHERE id = ? AND campaign_id = ?", (ctx.turn_id, ctx.campaign_id)) if ctx.turn_id else None
    if not t or t["kind"] is not None:
        raise ChangeError("a story starts only on the player's own Choice (a turn the player typed), never on an arrival or a server turn")
    if o["choice_trigger"] and _norm(op.action) != _norm(o["choice_trigger"]):
        raise ChangeError(f"{op.omen}'s Choice action is {o['choice_trigger']}; {op.action!r} does not commit the group")
    running = active_story(ctx.conn, ctx.campaign_id)
    if running:
        raise ChangeError(f"another story is running: {running['title']}")
    lock = o["story_lock"] or op.omen
    pc_id = one(ctx.conn, "SELECT active_character_id FROM campaigns WHERE id = ?", (ctx.campaign_id,))["active_character_id"]
    cast = [(resolve_character(ctx.conn, ctx.campaign_id, c.character), c.casting) for c in op.cast]
    if pc_id not in {c["id"] for c, _ in cast}:
        raise ChangeError("the cast must include the player character")
    if len({c["id"] for c, _ in cast}) != len(cast):
        raise ChangeError("a character appears twice in the cast")
    for c, _ in cast:
        _resident_here(ctx, c)
    b = one(ctx.conn, "SELECT sealed_json FROM ref.omen_beats WHERE omen_id = ?", (op.omen,))
    dossier = (b or {}).get("sealed") or {}
    from ..carousel.continuity import predecessor_context
    inherited = predecessor_context(ctx.conn, ctx.campaign_id, dossier)
    if inherited:
        dossier = {**dossier, "continuity_context": inherited}
    endings = endings_from(dossier)
    if endings and op.endings:
        raise ChangeError(f"{op.omen}'s endings are sealed in its dossier; leave `endings` out")
    if not endings:
        if not op.endings or set(op.endings) != set(ENDINGS):
            raise ChangeError(f"{op.omen} has no ending profiles: give `endings` with a predicate for each of {', '.join(ENDINGS)}")
        endings = {k: v.model_dump() for k, v in op.endings.items()}
    sealed_json, sha = seal(dossier)
    title = o["playable_title"] or dossier.get("title") or op.omen
    now = ctx.game_time()
    sid = ctx.conn.execute("""INSERT INTO stories (campaign_id, omen_id, story_lock, title, status, phase, pressure, camera, version,
                                                   choice_action, started_minutes, started_turn_id, group_no)
                              VALUES (?,?,?,?, 'active', 'Party', 1, 'on', 0, ?,?,?, (SELECT group_no FROM campaigns WHERE id = ?))""",
                           (ctx.campaign_id, op.omen, lock, title, op.action, now, ctx.turn_id, ctx.campaign_id)).lastrowid
    ctx.conn.execute("INSERT INTO story_packets (story_id, sealed_json, sealed_sha256, screenplay_json, endings_json) VALUES (?,?,?,?,?)",
                     (sid, sealed_json, sha, canonical({"version": 0, "phase_plan": dossier.get("phase_plan") or [], "notes": []}), canonical(endings)))
    for c, casting in cast:
        ctx.conn.execute("INSERT INTO story_cast (story_id, character_id, casting, joined_phase) VALUES (?,?,?, 'Party')", (sid, c["id"], casting))
    names = ", ".join(f"{c['name']} ({casting})" for c, casting in cast)
    ctx.event("story_omen", f"The Omen: {o['description']}", {"story_id": sid, "omen_id": op.omen, "title": title})
    ctx.event("story_choice", f"Choice: {op.action}. {title} begins.", {"story_id": sid, "action": op.action, "title": title,
                                                                        "cast": [c["id"] for c, _ in cast]})
    ctx.event("story_phase", "Plot Cycle: Choice → Party", {"story_id": sid, "from": "Choice", "to": "Party"})
    if op.rescue:
        from .fate_ops import start_rescue
        start_rescue(ctx, sid, lock, {c["id"] for c, _ in cast}, op.rescue)
    return {"summary": f"{title} begins with {names}", "story_id": sid, "phase": "Party"}


PRESSURE_FLOOR = {"Party": 1, "First Blood": 2, "Rebirth": 3, "Second Blood": 4, "Finale": 5}


class AdvancePhase(BaseModel):
    op: Literal["advance_phase"]
    to: Literal["Omen", "Choice", "Party", "First Blood", "Rebirth", "Second Blood", "Finale", "The End"]
    trigger: str = Field(min_length=1, max_length=400)


class Pressure(BaseModel):
    op: Literal["pressure"]
    delta: int
    reason: str = Field(min_length=1, max_length=300)


class Camera(BaseModel):
    op: Literal["camera"]
    on: bool
    focus: list[str] = Field(default_factory=list, max_length=12)
    reason: str = Field(min_length=1, max_length=300)


class CastCharacter(BaseModel):
    op: Literal["cast_character"]
    character: str
    casting: Literal["lead", "support", "witness", "sacrifice", "outsider"]


def _running(ctx: Ctx) -> dict:
    s = active_story(ctx.conn, ctx.campaign_id)
    if not s:
        raise ChangeError("no story is active")
    return s


def _cast_member(ctx: Ctx, story: dict, ref: str) -> dict:
    c = resolve_character(ctx.conn, ctx.campaign_id, ref)
    if not one(ctx.conn, "SELECT 1 AS x FROM story_cast WHERE story_id = ? AND character_id = ?", (story["id"], c["id"])):
        raise ChangeError(f"{c['name']} is not in the cast of {story['title']}")
    return c


def _apply_advance(ctx: Ctx, op: AdvancePhase) -> dict:
    s = _running(ctx)
    if op.to == "The End":
        raise ChangeError("conclude the story to end it (conclude_story)")
    if PHASES.index(op.to) <= PHASES.index(s["phase"]):
        raise ChangeError(f"the Plot Cycle only moves forward: {s['phase']} → {op.to} is not allowed")
    pressure = max(s["pressure"], PRESSURE_FLOOR.get(op.to, 0))
    ctx.conn.execute("UPDATE stories SET phase = ?, pressure = ? WHERE id = ?", (op.to, pressure, s["id"]))
    summary = f"Plot Cycle: {s['phase']} → {op.to}"
    ctx.event("story_phase", summary, {"story_id": s["id"], "from": s["phase"], "to": op.to})
    ctx.event("story_phase_trigger", f"{summary} (trigger: {op.trigger})", {"story_id": s["id"], "trigger": op.trigger, "pressure": pressure}, "gm")
    return {"summary": summary, "phase": op.to}


def _apply_pressure(ctx: Ctx, op: Pressure) -> dict:
    s = _running(ctx)
    pressure = min(6, max(0, s["pressure"] + op.delta))
    ctx.conn.execute("UPDATE stories SET pressure = ? WHERE id = ?", (pressure, s["id"]))
    summary = f"Pressure {s['pressure']} → {pressure}: {op.reason}"
    ctx.event("story_pressure", summary, {"story_id": s["id"], "from": s["pressure"], "to": pressure, "reason": op.reason}, "gm")
    return {"summary": summary, "pressure": pressure, "visibility": "gm"}


def _apply_camera(ctx: Ctx, op: Camera) -> dict:
    s = _running(ctx)
    focus = [_cast_member(ctx, s, f) for f in op.focus]
    ctx.conn.execute("UPDATE stories SET camera = ? WHERE id = ?", ("on" if op.on else "off", s["id"]))
    for c in focus:
        ctx.conn.execute("UPDATE story_cast SET screen_moments = screen_moments + 1 WHERE story_id = ? AND character_id = ?", (s["id"], c["id"]))
    names = ", ".join(c["name"].split()[0] for c in focus)
    summary = f"Camera on: {names}" if op.on and names else "Camera on" if op.on else "Camera off"
    ctx.event("story_camera", summary, {"story_id": s["id"], "on": op.on, "focus": [c["id"] for c in focus]})
    return {"summary": summary}


def _apply_cast(ctx: Ctx, op: CastCharacter) -> dict:
    s = _running(ctx)
    c = resolve_character(ctx.conn, ctx.campaign_id, op.character)
    had = one(ctx.conn, "SELECT casting FROM story_cast WHERE story_id = ? AND character_id = ?", (s["id"], c["id"]))
    if had:
        ctx.conn.execute("UPDATE story_cast SET casting = ? WHERE story_id = ? AND character_id = ?", (op.casting, s["id"], c["id"]))
        summary = f"{c['name']} is now cast as {op.casting} (was {had['casting']})"
    else:
        _resident_here(ctx, c)
        ctx.conn.execute("INSERT INTO story_cast (story_id, character_id, casting, joined_phase) VALUES (?,?,?,?)",
                         (s["id"], c["id"], op.casting, s["phase"]))
        summary = f"{c['name']} joins {s['title']} as {op.casting}"
    ctx.event("story_cast", summary, {"story_id": s["id"], "character_id": c["id"], "casting": op.casting})
    return {"summary": summary}


class RevisionOperation(BaseModel):
    family: str = Field(min_length=1, max_length=60)
    detail: str = Field(min_length=1, max_length=600)


class ReviseScreenplay(BaseModel):
    op: Literal["revise_screenplay"]
    character: str
    ticket: str
    base_version: int
    scope: Literal["scene", "arc", "premise", "ending_conditions"]
    operations: list[RevisionOperation] = Field(min_length=1, max_length=5)
    costs: list[str] = Field(default_factory=list, max_length=8)
    new_dangers: list[str] = Field(default_factory=list, max_length=8)
    preserved_facts: list[str] = Field(default_factory=list, max_length=12)
    public_summary: str = Field(min_length=1, max_length=300)
    reason: str = Field(min_length=1, max_length=600)
    new_endings: dict[EndingName, EndingProfile] | None = None


def _packet(ctx: Ctx, sid: int) -> dict:
    p = one(ctx.conn, "SELECT screenplay_json, endings_json FROM story_packets WHERE story_id = ?", (sid,))
    return {"screenplay": p["screenplay"], "endings": p["endings"]}


def _apply_revise(ctx: Ctx, op: ReviseScreenplay) -> dict:
    require_reference(ctx.conn)
    s = _running(ctx)
    c = _cast_member(ctx, s, op.character)
    t, d = _owned(ctx, c, op.ticket)
    if s["rescue"] and t["ticket_ref"] != s["rescue"]["ticket_ref"]:
        raise ChangeError("Takeover: in a Rescue, only the rescue ticket may revise this story")
    if not t["equipped"]:
        raise ChangeError(f"{d['name']} is not equipped")
    if t["uses_left"] is not None and t["uses_left"] <= 0:
        raise ChangeError(f"{d['name']} has no uses left")
    if not ticket_can_revise(ctx.conn, ctx.campaign_id, t["ticket_ref"]):
        raise ChangeError(f"{d['name']} cannot revise a screenplay (it is not a story-altering ticket, and no approved ruling says it may)")
    bad = [o.family for o in op.operations if o.family not in REVISION_FAMILIES]
    if bad:
        raise ChangeError(f"operation family {', '.join(bad)} is not allowed; use one of {', '.join(REVISION_FAMILIES)}")
    if op.base_version != s["version"]:
        raise ChangeError(f"the screenplay changed (now version {s['version']}); propose against it")
    if op.scope == "ending_conditions":
        missing = [f for f in ("new_endings", "costs", "new_dangers") if not getattr(op, f)]
        if missing:
            raise ChangeError(f"changing ending conditions needs {', '.join(missing)}: no revision is an automatic victory")
    before = _packet(ctx, s["id"])
    version = s["version"] + 1
    note = {"version": version, "scope": op.scope, "by": c["name"], "ticket": t["ticket_ref"],
            "operations": [o.model_dump() for o in op.operations], "costs": op.costs, "new_dangers": op.new_dangers,
            "preserved_facts": op.preserved_facts}
    screenplay = {**before["screenplay"], "version": version, "notes": [*(before["screenplay"].get("notes") or []), note]}
    endings = {**before["endings"], **{k: v.model_dump() for k, v in (op.new_endings or {}).items()}}
    after = {"screenplay": screenplay, "endings": endings}
    ctx.conn.execute("UPDATE story_packets SET screenplay_json = ?, endings_json = ? WHERE story_id = ?",
                     (canonical(screenplay), canonical(endings), s["id"]))
    ctx.conn.execute("UPDATE stories SET version = ? WHERE id = ?", (version, s["id"]))
    ctx.conn.execute("""INSERT INTO story_revisions (story_id, version, character_id, ticket_ref, scope, operations_json, before_json, after_json,
                                                     public_summary, reason, turn_id) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                     (s["id"], version, c["id"], t["ticket_ref"], op.scope, json.dumps(note["operations"], ensure_ascii=False),
                      canonical(before), canonical(after), op.public_summary, op.reason, ctx.turn_id))
    _apply_use(ctx, UseTrope(op="use_trope", character=str(c["id"]), ticket=t["ticket_ref"], effect=f"revises the screenplay: {op.public_summary}"))
    ctx.event("screenplay_revised", f"The story shifts: {op.public_summary}", {"story_id": s["id"], "public_summary": op.public_summary})
    ctx.event("screenplay_revised_detail", f"Screenplay v{version} ({op.scope}) by {c['name']}: {op.reason}",
              {"story_id": s["id"], "version": version, "note": note, "reason": op.reason}, "gm")
    return {"summary": f"Screenplay revised to version {version}: {op.public_summary}", "version": version}


STARS_PER_POINT = 5
DECAY_AFTER = 3          # concluded stories in a row without a star
MARKS_TO_UNLOCK = 10     # System Bible §4.4 [DECISION]
ASPECT_MIN_PA = 21


class Award(BaseModel):
    character: str
    performance: int = Field(ge=0, le=2)
    difficulty: int = Field(ge=0, le=2)


class ConcludeStory(BaseModel):
    op: Literal["conclude_story"]
    ending: EndingName
    reason: str = Field(min_length=1, max_length=600)
    awards: list[Award] = Field(default_factory=list, max_length=12)


class SpendStatPoint(BaseModel):
    op: Literal["spend_stat_point"]
    character: str
    stat: Literal["mettle", "moxie", "hustle", "savvy", "grit"]


class AwardMark(BaseModel):
    op: Literal["award_mark"]
    character: str
    track: str
    feat: str = Field(min_length=1, max_length=300)


class SelectAspect(BaseModel):
    op: Literal["select_aspect"]
    character: str
    aspect: str
    feat: str = Field(min_length=1, max_length=300)


class ConfirmSpoiler(BaseModel):
    op: Literal["confirm_spoiler"]
    story_lock: str = Field(min_length=1, max_length=120)
    element: str = Field(min_length=1, max_length=200)
    source: str = Field(min_length=1, max_length=300)


def _pay_stars(ctx: Ctx, c: dict, gain: int) -> None:
    stars, points, streak = c["stars"] + gain, c["pending_stat_points"], c["starless_streak"]
    while stars >= STARS_PER_POINT:
        stars, points = stars - STARS_PER_POINT, points + 1
        ctx.event("stat_point_earned", f"{c['name']} earns a stat point", {"character_id": c["id"]})
    if gain:
        streak = 0
    else:
        streak += 1
        if streak >= DECAY_AFTER:
            if stars > 0:
                stars -= 1
                ctx.event("stars_decayed", f"{c['name']} loses an unspent star after {DECAY_AFTER} stories without one", {"character_id": c["id"]})
            streak = 0
    ctx.conn.execute("UPDATE characters SET stars = ?, pending_stat_points = ?, starless_streak = ? WHERE id = ?", (stars, points, streak, c["id"]))


def _apply_conclude(ctx: Ctx, op: ConcludeStory) -> dict:
    """System Bible order: objective (the ending), fates (restoration or posters), rescue, rewards; then overlays lift."""
    from .fate_ops import resolve_fates, resolve_rescue
    s = _running(ctx)
    if op.ending in ("full_success", "costly_success") and s["phase"] != "Finale":
        raise ChangeError(f"{op.ending} is reached only from the Finale (the story is at {s['phase']}); "
                          "survive_without_victory and defeat can end it from any phase")
    cast = rows(ctx.conn, "SELECT c.* FROM story_cast x JOIN characters c ON c.id = x.character_id WHERE x.story_id = ?", (s["id"],))
    unsure = ", ".join(c["name"] + "'s" for c in cast if c["physical_condition"] == "uncertain")
    if unsure:
        raise ChangeError(f"decide {unsure} fate first (set_fate): The End needs every body known")
    by_id = {c["id"]: c for c in cast}
    awards: dict[int, Award] = {}
    for a in op.awards:
        c = _cast_member(ctx, s, a.character)
        if c["kind"] != "pc":
            raise ChangeError(f"{c['name']} is not a player character; NPC cast earn no stars")
        if c["id"] in awards:
            raise ChangeError(f"{c['name']} has two awards")
        awards[c["id"]] = a
    missing = [c["name"] for c in cast if c["kind"] == "pc" and c["id"] not in awards]
    if missing:
        raise ChangeError(f"every player character in the cast needs an award (performance and difficulty 0-2): {', '.join(missing)}")
    nov = novelty(ctx.conn, ctx.campaign_id, s["story_lock"])
    ctx.conn.execute("""UPDATE stories SET status = 'concluded', phase = 'The End', ending = ?, ending_reason = ?, ended_minutes = ?, novelty = ?,
                        ended_turn_id = ? WHERE id = ?""", (op.ending, op.reason, ctx.game_time(), nov, ctx.turn_id, s["id"]))
    recharge(ctx, "story")
    ctx.conn.execute(f"DELETE FROM stat_modifiers WHERE campaign_id = ? AND scope = 'story' AND character_id IN ({','.join('?' * len(by_id))})",
                     (ctx.campaign_id, *by_id))
    stars = {str(cid_): nov + a.performance + a.difficulty for cid_, a in awards.items()}
    ending = op.ending.replace("_", " ")
    ctx.event("story_phase", f"Plot Cycle: {s['phase']} → The End", {"story_id": s["id"], "from": s["phase"], "to": "The End"})
    ctx.event("story_concluded", f"{s['title']} ends: {ending}", {"story_id": s["id"], "ending": op.ending, "cast": list(by_id), "stars": stars})
    fates = resolve_fates(ctx, s, cast)
    resolve_rescue(ctx, s, op.ending)
    for cid_ in awards:   # re-read: resolve_fates changed the rows; a dead PC still earns its award
        _pay_stars(ctx, one(ctx.conn, "SELECT * FROM characters WHERE id = ?", (cid_,)), stars[str(cid_)])
    lift_overlays(ctx, s["id"])
    from ..carousel.venues import in_story_venue
    from .story_venue_ops import leave
    inside = in_story_venue(ctx.conn, ctx.campaign_id)
    if inside:   # whatever the ending, the story never strands the group
        leave(ctx, inside)
    return {"summary": f"{s['title']} ends: {ending}", "ending": op.ending, "stars": stars, "novelty": nov, "fates": fates["outcome"]}


def _apply_spend(ctx: Ctx, op: SpendStatPoint) -> dict:
    c = resolve_character(ctx.conn, ctx.campaign_id, op.character)
    if c["pending_stat_points"] <= 0:
        raise ChangeError(f"{c['name']} has no stat points to spend")
    if c[op.stat] is None:
        raise ChangeError(f"{c['name']} has no stats yet")
    ctx.conn.execute(f"UPDATE characters SET {op.stat} = {op.stat} + 1, pending_stat_points = pending_stat_points - 1 WHERE id = ?", (c["id"],))
    summary = f"{c['name']} spends a stat point: {op.stat.title()} {c[op.stat]} → {c[op.stat] + 1}"
    ctx.event("stat_point_spent", summary, {"character_id": c["id"], "stat": op.stat})
    return {"summary": summary}


def _apply_mark(ctx: Ctx, op: AwardMark) -> dict:
    require_reference(ctx.conn)
    c = resolve_character(ctx.conn, ctx.campaign_id, op.character)
    role = one(ctx.conn, "SELECT id, name FROM ref.roles WHERE id = ? AND kind = 'advanced'", (op.track,))
    if not role:
        raise ChangeError(f"{op.track!r} is not an advanced role track")
    s = active_story(ctx.conn, ctx.campaign_id) or one(ctx.conn, "SELECT * FROM stories WHERE campaign_id = ? AND status = 'concluded' "
                                                                 "ORDER BY id DESC LIMIT 1", (ctx.campaign_id,))
    if not s:
        raise ChangeError("marks are earned in a story; there is none yet")
    if one(ctx.conn, "SELECT 1 AS x FROM mark_awards WHERE story_id = ? AND character_id = ? AND track_role_id = ?", (s["id"], c["id"], role["id"])):
        raise ChangeError(f"{c['name']} already has a {role['name']} mark from {s['title']}")
    ctx.conn.execute("INSERT INTO mark_awards (story_id, character_id, track_role_id, feat) VALUES (?,?,?,?)", (s["id"], c["id"], role["id"], op.feat))
    ctx.conn.execute("""INSERT INTO advanced_marks (character_id, track_role_id, marks) VALUES (?,?,1)
                        ON CONFLICT(character_id, track_role_id) DO UPDATE SET marks = marks + 1""", (c["id"], role["id"]))
    m = one(ctx.conn, "SELECT marks, unlocked FROM advanced_marks WHERE character_id = ? AND track_role_id = ?", (c["id"], role["id"]))
    summary = f"{c['name']} earns a {role['name']} mark ({m['marks']}/{MARKS_TO_UNLOCK}): {op.feat}"
    ctx.event("mark_awarded", summary, {"character_id": c["id"], "track": role["id"], "marks": m["marks"], "story_id": s["id"]})
    if m["marks"] >= MARKS_TO_UNLOCK and not m["unlocked"]:
        ctx.conn.execute("UPDATE advanced_marks SET unlocked = 1 WHERE character_id = ? AND track_role_id = ?", (c["id"], role["id"]))
        ctx.event("advanced_role_unlocked", f"{c['name']} unlocks the {role['name']} Archetype", {"character_id": c["id"], "track": role["id"]})
    return {"summary": summary, "marks": m["marks"]}


def _apply_aspect(ctx: Ctx, op: SelectAspect) -> dict:
    require_reference(ctx.conn)
    c = resolve_character(ctx.conn, ctx.campaign_id, op.character)
    a = one(ctx.conn, "SELECT r.id, r.name, r.parent, p.name AS parent_name FROM ref.roles r LEFT JOIN ref.roles p ON p.id = r.parent "
                      "WHERE r.id = ? AND r.kind = 'aspect'", (op.aspect,))
    if not a:
        raise ChangeError(f"{op.aspect!r} is not an Aspect")
    if c["aspect_id"]:
        raise ChangeError(f"{c['name']} already has an Aspect")
    if a["parent"] != c["role_id"]:
        raise ChangeError(f"{a['name']} is an Aspect of the {a['parent_name'] or a['parent']}; {c['name']} is not one")
    pa = sheet(ctx.conn, c["id"])["plot_armor"]["base"]
    if pa is None or pa < ASPECT_MIN_PA:
        raise ChangeError(f"an Aspect needs base Plot Armor {ASPECT_MIN_PA}; {c['name']} has {pa}")
    ctx.conn.execute("UPDATE characters SET aspect_id = ? WHERE id = ?", (a["id"], c["id"]))
    summary = f"{c['name']} takes the Aspect {a['name']}: {op.feat}"
    ctx.event("aspect_selected", summary, {"character_id": c["id"], "aspect": a["id"], "feat": op.feat})
    return {"summary": summary}


def _apply_spoiler(ctx: Ctx, op: ConfirmSpoiler) -> dict:
    new = ctx.conn.execute("INSERT OR IGNORE INTO story_spoilers (campaign_id, story_lock, element, source) VALUES (?,?,?,?)",
                           (ctx.campaign_id, op.story_lock, op.element, op.source)).rowcount > 0
    summary = f"Spoiler {'confirmed' if new else 'already recorded'} for {op.story_lock}: {op.element}"
    if new:
        ctx.event("spoiler_confirmed", summary, {"story_lock": op.story_lock, "element": op.element, "source": op.source}, "gm")
    return {"summary": summary, "visibility": "gm"}


def register(ops: dict) -> None:
    ops.update({
        "sight_omen": (SightOmen, _apply_sight),
        "examine_omen": (ExamineOmen, _apply_examine),
        "begin_story": (BeginStory, _apply_begin),
        "advance_phase": (AdvancePhase, _apply_advance),
        "pressure": (Pressure, _apply_pressure),
        "camera": (Camera, _apply_camera),
        "cast_character": (CastCharacter, _apply_cast),
        "revise_screenplay": (ReviseScreenplay, _apply_revise),
        "conclude_story": (ConcludeStory, _apply_conclude),
        "spend_stat_point": (SpendStatPoint, _apply_spend),
        "award_mark": (AwardMark, _apply_mark),
        "select_aspect": (SelectAspect, _apply_aspect),
        "confirm_spoiler": (ConfirmSpoiler, _apply_spoiler),
    })
