"""Validated fate ops (registered into engine.CHANGE_OPS by rules/__init__.py). Design §8, System Bible §11.

Fates change only inside a story, on a cast member; restoration happens at The End (resolve_fates, called by
conclude_story). A death freezes a snapshot in death_events whose cause stays GM-only. Permanent loss needs a warning
given on an earlier turn. A dead character may act once more only through a posthumous window on a death ticket."""
from __future__ import annotations

import json
from typing import Literal

from pydantic import BaseModel, Field

from ..carousel import fate
from ..db import one, rows
from .carousel_ops import _owned
from .engine import ChangeError, Ctx, resolve_character
from .story_ops import RescueEntry, _cast_member, _running

Physical = Literal["alive", "injured", "dying", "dead", "uncertain"]
Wallpaper = Literal["active", "off_screen", "dead", "written_off", "unknown"]
# tickets that work after death by name; any trope tagged Death/sacrifice does too
DEATH_TICKETS = ("hb.ticket.advanced.departed", "hb.ticket.aspect.final-girl.scream-queen")


class SetFate(BaseModel):
    op: Literal["set_fate"]
    character: str
    physical: Physical | None = None
    wallpaper: Wallpaper | None = None
    cause: str | None = Field(default=None, max_length=600)
    last_action: str | None = Field(default=None, max_length=300)
    earned_effects: list[str] = Field(default_factory=list, max_length=8)
    witnesses: list[str] = Field(default_factory=list, max_length=12)
    reason: str = Field(min_length=1, max_length=400)


class WarnIrreversible(BaseModel):
    op: Literal["warn_irreversible"]
    rule: str = Field(min_length=1, max_length=80)
    warning: str = Field(min_length=1, max_length=400)
    character: str | None = None


class PermanentLoss(BaseModel):
    op: Literal["permanent_loss"]
    character: str
    rule: str = Field(min_length=1, max_length=80)
    reason: str = Field(min_length=1, max_length=400)


class PosthumousWindow(BaseModel):
    op: Literal["posthumous_window"]
    character: str
    ticket: str
    effect: str = Field(min_length=1, max_length=400)


def where(ctx: Ctx, c: dict) -> dict:
    """The character's node, venue and zone: their party group's if they have one, else the campaign's."""
    g = one(ctx.conn, "SELECT node_id, venue_id, zone_id FROM party_groups WHERE campaign_id = ? AND key = ?",
            (ctx.campaign_id, c.get("assignment"))) if c.get("assignment") else None
    return g or one(ctx.conn, "SELECT node_id, venue_id, zone_id FROM campaigns WHERE id = ?", (ctx.campaign_id,))


def _die(ctx: Ctx, s: dict, c: dict, op: SetFate) -> int:
    missing = [f for f in ("cause", "last_action") if not (getattr(op, f) or "").strip()]
    if missing:
        raise ChangeError(f"a death needs {' and '.join(missing)}")
    witnesses = [resolve_character(ctx.conn, ctx.campaign_id, w)["id"] for w in op.witnesses]
    items = [r["name"] for r in rows(ctx.conn, "SELECT name FROM items WHERE campaign_id = ? AND owner_character_id = ? "
                                               "AND (quantity IS NULL OR quantity > 0) ORDER BY id", (ctx.campaign_id, c["id"]))]
    pos = where(ctx, c)
    did = ctx.conn.execute("""INSERT INTO death_events (campaign_id, character_id, story_id, phase, cause, last_action, earned_effects_json,
                                                        witnesses_json, node_id, venue_id, zone_id, items_json, minutes, turn_id)
                              VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                           (ctx.campaign_id, c["id"], s["id"], s["phase"], op.cause, op.last_action, json.dumps(op.earned_effects),
                            json.dumps(witnesses), pos["node_id"], pos["venue_id"], pos["zone_id"], json.dumps(items), ctx.game_time(),
                            ctx.turn_id)).lastrowid
    ctx.event("character_died", f"{c['name']} dies ({s['phase']})", {"character_id": c["id"], "story_id": s["id"], "phase": s["phase"],
                                                                     "last_action": op.last_action})
    ctx.event("death_record", f"{c['name']} died at {s['phase']}: {op.cause}", {"death_event_id": did, "character_id": c["id"], "cause": op.cause,
                                                                                "earned_effects": op.earned_effects, "reason": op.reason}, "gm")
    return did


def _apply_set_fate(ctx: Ctx, op: SetFate) -> dict:
    s = _running(ctx)
    c = _cast_member(ctx, s, op.character)
    if op.physical is None and op.wallpaper is None:
        raise ChangeError("set_fate needs physical or wallpaper")
    if op.physical == "dead" and c["physical_condition"] == "dead":
        raise ChangeError(f"{c['name']} is already dead")
    if op.physical and op.physical != "dead" and c["physical_condition"] == "dead":
        raise ChangeError(f"{c['name']} is dead: no resurrection mid-story; The End restores, or a rescue")
    if op.physical:
        ctx.conn.execute("UPDATE characters SET physical_condition = ? WHERE id = ?", (op.physical, c["id"]))
    if op.wallpaper:
        ctx.conn.execute("UPDATE characters SET wallpaper_status = ? WHERE id = ?", (op.wallpaper, c["id"]))
    if op.physical == "dead":
        _die(ctx, s, c, op)
        summary = f"{c['name']} dies ({s['phase']})"
    else:
        parts = [f"body {op.physical}" if op.physical else "", f"wallpaper {op.wallpaper.replace('_', ' ')}" if op.wallpaper else ""]
        summary = f"{c['name']}: {' · '.join(p for p in parts if p)}"
        ctx.event("wallpaper_changed" if op.wallpaper and not op.physical else "fate_changed", summary,
                  {"character_id": c["id"], "physical": op.physical, "wallpaper": op.wallpaper})
    participation = fate.set_rights(ctx.conn, c["id"])
    return {"summary": summary, "participation": participation}


def _apply_warn(ctx: Ctx, op: WarnIrreversible) -> dict:
    from ..carousel.stories import active_story
    c = resolve_character(ctx.conn, ctx.campaign_id, op.character) if op.character else None
    s = active_story(ctx.conn, ctx.campaign_id)
    ctx.conn.execute("INSERT INTO danger_warnings (campaign_id, story_id, character_id, rule, warning, turn_id, minutes) VALUES (?,?,?,?,?,?,?)",
                     (ctx.campaign_id, s["id"] if s else None, c["id"] if c else None, op.rule, op.warning, ctx.turn_id, ctx.game_time()))
    summary = f"Warning: {op.warning}"
    ctx.event("danger_warning", summary, {"rule": op.rule, "character_id": c["id"] if c else None, "story_id": s["id"] if s else None})
    return {"summary": summary}


def _apply_permanent_loss(ctx: Ctx, op: PermanentLoss) -> dict:
    c = resolve_character(ctx.conn, ctx.campaign_id, op.character)
    if not ctx.turn_id:
        raise ChangeError("permanent loss happens on a turn, after a warning given on an earlier turn")
    if c["persistent_fate"] == "permanently_lost":
        raise ChangeError(f"{c['name']} is already lost")
    warned = rows(ctx.conn, "SELECT turn_id FROM danger_warnings WHERE campaign_id = ? AND rule = ? AND (character_id IS NULL OR character_id = ?)",
                  (ctx.campaign_id, op.rule, c["id"]))
    if not warned:
        raise ChangeError(f"no warning was given for {op.rule!r}; warn_irreversible first")
    if not any(w["turn_id"] is not None and w["turn_id"] < ctx.turn_id for w in warned):
        raise ChangeError(f"the warning for {op.rule!r} must come on an earlier turn, so the player can choose")
    ctx.conn.execute("""UPDATE characters SET persistent_fate = 'permanently_lost', rescue_eligibility = 'ineligible', status = 'lost', in_party = 0
                        WHERE id = ?""", (c["id"],))
    fate.set_rights(ctx.conn, c["id"])
    summary = f"{c['name']} is lost for good"
    ctx.event("permanent_loss", summary, {"character_id": c["id"], "rule": op.rule})
    ctx.event("permanent_loss_detail", f"{summary} ({op.rule}): {op.reason}", {"character_id": c["id"], "rule": op.rule, "reason": op.reason}, "gm")
    return {"summary": summary}


def _apply_window(ctx: Ctx, op: PosthumousWindow) -> dict:
    c = resolve_character(ctx.conn, ctx.campaign_id, op.character)
    if c["physical_condition"] != "dead":
        raise ChangeError(f"{c['name']} is not dead; a posthumous window opens only after death")
    t, d = _owned(ctx, c, op.ticket)
    if not t["equipped"]:
        raise ChangeError(f"{d['name']} is not equipped")
    if t["uses_left"] is not None and t["uses_left"] <= 0:
        raise ChangeError(f"{d['name']} has no uses left")
    if t["ticket_ref"] not in DEATH_TICKETS and "Death/sacrifice" not in fate.ticket_kinds(ctx.conn, t["ticket_ref"]):
        raise ChangeError(f"{d['name']} doesn't work after death (it needs a Death/sacrifice ticket)")
    ctx.conn.execute("UPDATE characters SET rights_basis = ? WHERE id = ?", (t["ticket_ref"], c["id"]))
    fate.set_rights(ctx.conn, c["id"])
    summary = f"{c['name']} reaches back once: {op.effect}"
    vis = "gm" if t["hidden"] else "player"
    ctx.event("posthumous_window", summary, {"character_id": c["id"], "ticket": t["ticket_ref"], "effect": op.effect}, vis)
    return {"summary": summary, "visibility": vis}


class TakePoster(BaseModel):
    op: Literal["take_poster"]
    poster: int
    character: str


def _apply_take_poster(ctx: Ctx, op: TakePoster) -> dict:
    c = resolve_character(ctx.conn, ctx.campaign_id, op.character)
    if c["status"] in fate.GONE:
        raise ChangeError(f"{c['name']} can't take a poster ({c['status']})")
    if where(ctx, c)["node_id"] != fate.DINER:
        raise ChangeError("the missing-poster board is outside the Diner; go there first")
    p = one(ctx.conn, "SELECT * FROM missing_posters WHERE id = ? AND campaign_id = ?", (op.poster, ctx.campaign_id))
    if not p:
        raise ChangeError(f"no missing poster #{op.poster}")
    if p["held_by_character_id"] is not None or p["rescued_minutes"] is not None:
        raise ChangeError(f"{p['name']}'s poster is not on the board")
    ctx.conn.execute("UPDATE missing_posters SET held_by_character_id = ? WHERE id = ?", (c["id"], p["id"]))
    summary = f"{c['name']} takes down {p['name']}'s poster"
    ctx.event("poster_taken", summary, {"character_id": c["id"], "poster_id": p["id"]})
    return {"summary": summary}


def start_rescue(ctx: Ctx, story_id: int, story_lock: str, cast_ids: set[int], entry: RescueEntry) -> None:
    """begin_story with `rescue`: the rescuer carries posters of this story's lost group in on a Rescue ticket (one use)."""
    from .carousel_ops import UseTrope, _apply_use
    c = resolve_character(ctx.conn, ctx.campaign_id, entry.character)
    if c["id"] not in cast_ids:
        raise ChangeError(f"{c['name']} must be in the cast to lead a rescue")
    t, d = _owned(ctx, c, entry.ticket)
    if "Rescue" not in fate.ticket_kinds(ctx.conn, t["ticket_ref"]):
        raise ChangeError(f"{d['name']} is not a Rescue ticket")
    names = []
    for pid in dict.fromkeys(entry.posters):
        p = one(ctx.conn, "SELECT p.*, c.rescue_eligibility FROM missing_posters p JOIN characters c ON c.id = p.character_id "
                          "WHERE p.id = ? AND p.campaign_id = ?", (pid, ctx.campaign_id))
        if not p or p["held_by_character_id"] != c["id"]:
            raise ChangeError(f"{c['name']} isn't holding poster #{pid}; take it from the board at the Diner")
        if p["story_lock"] != story_lock:
            raise ChangeError(f"{p['name']} was lost in another story; a rescue enters the story that took them")
        if p["rescue_eligibility"] != "possible":
            raise ChangeError(f"{p['name']} can't be rescued (rescue {p['rescue_eligibility']})")
        names.append(p["name"])
    _apply_use(ctx, UseTrope(op="use_trope", character=str(c["id"]), ticket=t["ticket_ref"], effect="enters the story to bring the lost back"))
    ids = list(dict.fromkeys(entry.posters))
    ctx.conn.execute("UPDATE stories SET rescue_json = ? WHERE id = ?",
                     (json.dumps({"character_id": c["id"], "ticket_ref": t["ticket_ref"], "poster_ids": ids}), story_id))
    ctx.conn.execute(f"UPDATE characters SET rescue_eligibility = 'active' WHERE id IN "
                     f"(SELECT character_id FROM missing_posters WHERE id IN ({','.join('?' * len(ids))}))", ids)
    ctx.event("story_rescue", "This story is a Rescue", {"story_id": story_id})
    ctx.event("story_rescue_detail", f"{c['name']} carries the posters of {', '.join(names)}", {"story_id": story_id, "poster_ids": ids,
                                                                                               "ticket": t["ticket_ref"]}, "gm")


def resolve_rescue(ctx: Ctx, story: dict, ending: str) -> dict | None:
    """After the fates: a victory brings every carried poster's character back; anything else fails them for good."""
    r = story.get("rescue")   # stories.rescue_json, decoded by row_to_dict
    if not r:
        return None
    won = ending in ("full_success", "costly_success")
    found = []
    for pid in r["poster_ids"]:
        p = one(ctx.conn, "SELECT * FROM missing_posters WHERE id = ?", (pid,))
        if won:
            ctx.conn.execute("""UPDATE characters SET rescue_eligibility = 'completed', persistent_fate = 'restored', physical_condition = 'alive',
                                wallpaper_status = 'active', rights_basis = NULL, status = 'active' WHERE id = ?""", (p["character_id"],))
            fate.set_rights(ctx.conn, p["character_id"])
            ctx.conn.execute("UPDATE missing_posters SET held_by_character_id = NULL, rescued_minutes = ? WHERE id = ?", (ctx.game_time(), pid))
            ctx.event("character_found", f"{p['name']} is found", {"character_id": p["character_id"], "poster_id": pid, "story_id": story["id"]})
            found.append(p["character_id"])
        else:
            ctx.conn.execute("UPDATE characters SET rescue_eligibility = 'failed' WHERE id = ?", (p["character_id"],))
            ctx.conn.execute("UPDATE missing_posters SET held_by_character_id = NULL WHERE id = ?", (pid,))
            ctx.event("rescue_failed", f"The rescue of {p['name']} failed", {"poster_id": pid, "story_id": story["id"]}, "gm")
    return {"found": found, "failed": [] if won else r["poster_ids"]}


def _last_seen(ctx: Ctx, pos: dict) -> str | None:
    from ..carousel.venues import label
    place = one(ctx.conn, "SELECT name FROM ref.places WHERE id = ?", (pos.get("node_id"),)) if pos.get("node_id") else None
    zone = label(ctx.conn, pos.get("zone_id"))
    return " — ".join(p for p in ((place or {}).get("name"), zone) if p) or None


def resolve_fates(ctx: Ctx, story: dict, cast: list[dict]) -> dict:
    """The End (Bible §11.6): if anyone in the group is neither dead nor permanently lost, everyone not lost is restored
    where they stand; otherwise everyone not lost goes up on the missing-poster board. The death records stay."""
    from ..carousel.sheet import sheet
    alive = [c for c in cast if c["persistent_fate"] != "permanently_lost"]
    if any(c["physical_condition"] != "dead" for c in alive):
        restored = []
        for c in alive:
            hurt = c["physical_condition"] in ("dead", "dying", "injured")
            debuffs = [r["name"] for r in rows(ctx.conn, "SELECT name FROM status_effects WHERE character_id = ? AND polarity = 'debuff' ORDER BY id",
                                               (c["id"],))]
            ctx.conn.execute("DELETE FROM status_effects WHERE character_id = ? AND polarity = 'debuff'", (c["id"],))
            ctx.conn.execute("UPDATE characters SET physical_condition = 'alive', wallpaper_status = 'active', rights_basis = NULL, "
                             "persistent_fate = CASE WHEN ? THEN 'restored' ELSE persistent_fate END WHERE id = ?", (int(hurt), c["id"]))
            fate.set_rights(ctx.conn, c["id"])
            restored.append(c["id"])
            if hurt or debuffs or c["wallpaper_status"] != "active":   # nothing to say about someone who came through untouched
                ctx.event("fate_restored", f"{c['name']} walks out of the story whole", {"character_id": c["id"], "story_id": story["id"],
                                                                                          "was": c["physical_condition"], "cleared": debuffs})
        return {"outcome": "restored", "restored": restored, "posters": []}
    group_no = one(ctx.conn, "SELECT group_no FROM campaigns WHERE id = ?", (ctx.campaign_id,))["group_no"]
    posters = []
    for c in alive:
        d = one(ctx.conn, "SELECT * FROM death_events WHERE character_id = ? AND story_id = ? ORDER BY id DESC LIMIT 1", (c["id"], story["id"]))
        role = one(ctx.conn, "SELECT name FROM ref.roles WHERE id = ?", (c["role_id"],)) if c["role_id"] else None
        pid = ctx.conn.execute("""INSERT INTO missing_posters (campaign_id, character_id, group_no, name, plot_armor, archetype, last_seen, story_id,
                                                               story_lock, death_event_id, posted_minutes) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                               (ctx.campaign_id, c["id"], group_no, c["name"], sheet(ctx.conn, c["id"])["plot_armor"]["effective"],
                                (role or {}).get("name"), _last_seen(ctx, d or where(ctx, c)), story["id"], story["story_lock"],
                                d["id"] if d else None, ctx.game_time())).lastrowid
        ctx.conn.execute("""UPDATE characters SET persistent_fate = 'missing_postered', rescue_eligibility = 'possible', status = 'missing',
                            in_party = 0, assignment = NULL WHERE id = ?""", (c["id"],))
        posters.append(pid)
        ctx.event("poster_created", f"A missing poster goes up: {c['name']}", {"character_id": c["id"], "poster_id": pid, "story_id": story["id"]})
    return {"outcome": "postered", "restored": [], "posters": posters}


def register(ops: dict) -> None:
    ops.update({
        "set_fate": (SetFate, _apply_set_fate),
        "warn_irreversible": (WarnIrreversible, _apply_warn),
        "permanent_loss": (PermanentLoss, _apply_permanent_loss),
        "posthumous_window": (PosthumousWindow, _apply_window),
        "take_poster": (TakePoster, _apply_take_poster),
    })
