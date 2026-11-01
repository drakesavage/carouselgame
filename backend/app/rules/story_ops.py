"""Validated story ops (registered into engine.CHANGE_OPS by rules/__init__.py).

The fixed points of a Carousel story: an Omen is sighted and examined; the player's own Choice action begins its story,
which seals the dossier's truth and endings; the Plot Cycle, pressure, camera and cast move only through these ops;
eligible tickets revise the screenplay as versioned commits; conclude_story picks one of four endings and pays rewards.
The GM improvises everything between them. Nothing here writes the sealed packet into a player-visible event."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from ..carousel.refdata import require_reference
from ..carousel.stories import ENDINGS, PHASES, active_story, canonical, endings_from, seal
from ..db import one
from .engine import ChangeError, Ctx, resolve_character

PLAYABLE = "Playable Omen"
HUB = "Hub / generator"


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
    if o["record_type"] not in (PLAYABLE, HUB):
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


class BeginStory(BaseModel):
    op: Literal["begin_story"]
    omen: str
    action: str = Field(min_length=1, max_length=200)
    cast: list[CastEntry] = Field(min_length=1, max_length=12)
    reason: str = Field(min_length=1, max_length=400)
    endings: dict[Literal["full_success", "costly_success", "survive_without_victory", "defeat"], EndingProfile] | None = None


def _norm(action: str) -> str:
    return " ".join(action.strip().lower().split())


def _apply_begin(ctx: Ctx, op: BeginStory) -> dict:
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
    b = one(ctx.conn, "SELECT sealed_json FROM ref.omen_beats WHERE omen_id = ?", (op.omen,))
    dossier = (b or {}).get("sealed") or {}
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
                                                   choice_action, started_minutes, started_turn_id) VALUES (?,?,?,?, 'active', 'Party', 1, 'on', 0, ?,?,?)""",
                           (ctx.campaign_id, op.omen, lock, title, op.action, now, ctx.turn_id)).lastrowid
    ctx.conn.execute("INSERT INTO story_packets (story_id, sealed_json, sealed_sha256, screenplay_json, endings_json) VALUES (?,?,?,?,?)",
                     (sid, sealed_json, sha, canonical({"version": 0, "phase_plan": dossier.get("phase_plan") or [], "notes": []}), canonical(endings)))
    for c, casting in cast:
        ctx.conn.execute("INSERT INTO story_cast (story_id, character_id, casting, joined_phase) VALUES (?,?,?, 'Party')", (sid, c["id"], casting))
    names = ", ".join(f"{c['name']} ({casting})" for c, casting in cast)
    ctx.event("story_omen", f"The Omen: {o['description']}", {"story_id": sid, "omen_id": op.omen, "title": title})
    ctx.event("story_choice", f"Choice: {op.action}. {title} begins.", {"story_id": sid, "action": op.action, "title": title,
                                                                        "cast": [c["id"] for c, _ in cast]})
    ctx.event("story_phase", "Plot Cycle: Choice → Party", {"story_id": sid, "from": "Choice", "to": "Party"})
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
        ctx.conn.execute("INSERT INTO story_cast (story_id, character_id, casting, joined_phase) VALUES (?,?,?,?)",
                         (s["id"], c["id"], op.casting, s["phase"]))
        summary = f"{c['name']} joins {s['title']} as {op.casting}"
    ctx.event("story_cast", summary, {"story_id": s["id"], "character_id": c["id"], "casting": op.casting})
    return {"summary": summary}


def register(ops: dict) -> None:
    ops.update({
        "sight_omen": (SightOmen, _apply_sight),
        "examine_omen": (ExamineOmen, _apply_examine),
        "begin_story": (BeginStory, _apply_begin),
        "advance_phase": (AdvancePhase, _apply_advance),
        "pressure": (Pressure, _apply_pressure),
        "camera": (Camera, _apply_camera),
        "cast_character": (CastCharacter, _apply_cast),
    })
