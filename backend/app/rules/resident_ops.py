"""Validated resident ops (registered into engine.CHANGE_OPS by rules/__init__.py; the residents are carousel/residents.py).

`resident_whereabouts` is the GM placing a resident somewhere until a game minute ("Adeline is at the Diner until tonight");
the schedule resumes after it. The note and its reason are GM-only. `recruit_resident` brings a met, present, willing
resident into the group; `dismiss_resident` sends them off (home by default) until the next morning."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from ..carousel import clock, residents
from ..carousel.fate import GONE
from ..carousel.stories import active_story
from ..carousel.import_places import resolve_place
from ..carousel.refdata import require_reference
from ..db import one
from .engine import ChangeError, Ctx, resolve_character


class ResidentWhereabouts(BaseModel):
    op: Literal["resident_whereabouts"]
    resident: str
    place: str
    zone: str | None = None
    until_minutes: int
    reason: str = Field(min_length=1, max_length=300)


def resident(ctx: Ctx, ref: str) -> dict:
    """A resident by template id (CAR-NPC-…) or by any character reference; refused for anyone else."""
    if ref.strip().upper().startswith("CAR-NPC-"):
        c = one(ctx.conn, """SELECT c.* FROM characters c JOIN npc_profiles p ON p.character_id = c.id
                             WHERE c.campaign_id = ? AND p.ref_id = ?""", (ctx.campaign_id, ref.strip().upper()))
        if not c:
            raise ChangeError(f"no resident {ref!r} in this campaign")
    else:
        c = resolve_character(ctx.conn, ctx.campaign_id, ref)
    p = one(ctx.conn, "SELECT ref_id, met, attitude FROM npc_profiles WHERE character_id = ?", (c["id"],))
    if not p or not p["ref_id"]:
        raise ChangeError(f"{c['name']} is not a resident of Camp Dyer")
    return {**c, "ref_id": p["ref_id"], "met": p["met"], "attitude": p["attitude"]}


def when(minutes: int) -> str:
    return f"day {clock.day(minutes)} {clock.hour(minutes):02d}:{(clock.CLOCK_START + minutes) % 60:02d}"


def _apply_whereabouts(ctx: Ctx, op: ResidentWhereabouts) -> dict:
    require_reference(ctx.conn)
    c = resident(ctx, op.resident)
    if c["status"] in GONE:
        raise ChangeError(f"{c['name']} is gone ({c['status']})")
    place = resolve_place(ctx.conn, op.place)
    if not place:
        raise ChangeError(f"no place {op.place!r} in the reference data")
    if op.zone:
        z = one(ctx.conn, "SELECT venue_id FROM ref.venue_zones WHERE id = ?", (op.zone,))
        v = one(ctx.conn, "SELECT venue_id FROM ref.venue_places WHERE place_id = ?", (place,))
        if not z or not v or z["venue_id"] != v["venue_id"]:
            raise ChangeError(f"zone {op.zone} is not at {place}")
    now = ctx.game_time()
    if op.until_minutes <= now:
        raise ChangeError(f"until_minutes {op.until_minutes} is already past (now {now})")
    ctx.conn.execute("""INSERT OR REPLACE INTO resident_whereabouts (campaign_id, character_id, place_id, zone_id, until_minutes, reason)
                        VALUES (?,?,?,?,?,?)""", (ctx.campaign_id, c["id"], place, op.zone, op.until_minutes, op.reason))
    name = one(ctx.conn, "SELECT name FROM ref.places WHERE id = ?", (place,))["name"]
    summary = f"{c['name']} is at {name} until {when(op.until_minutes)} ({op.reason})"
    ctx.event("resident_whereabouts", summary, {"character_id": c["id"], "place_id": place, "zone_id": op.zone,
                                                "until_minutes": op.until_minutes}, visibility="gm")
    return {"summary": summary}


class RecruitResident(BaseModel):
    op: Literal["recruit_resident"]
    resident: str
    reason: str = Field(min_length=1, max_length=300)


class DismissResident(BaseModel):
    op: Literal["dismiss_resident"]
    resident: str
    where: str | None = None
    reason: str = Field(min_length=1, max_length=300)


def _pc_group(ctx: Ctx) -> str | None:
    pc = one(ctx.conn, """SELECT c.assignment FROM campaigns k JOIN characters c ON c.id = k.active_character_id WHERE k.id = ?""",
             (ctx.campaign_id,))
    return pc["assignment"] if pc else None


def _apply_recruit(ctx: Ctx, op: RecruitResident) -> dict:
    require_reference(ctx.conn)
    c = resident(ctx, op.resident)
    if c["in_party"]:
        raise ChangeError(f"{c['name']} is already with the group")
    refused = residents.presence_refusal(ctx.conn, ctx.campaign_id, c["id"])
    if refused:
        raise ChangeError(refused)
    if (c["attitude"] or 0) < 0:
        raise ChangeError(f"{c['name']} won't come: attitude {c['attitude']}")
    ctx.conn.execute("UPDATE characters SET in_party = 1, roster_category = 'core_party', assignment = ? WHERE id = ?", (_pc_group(ctx), c["id"]))
    ctx.conn.execute("DELETE FROM resident_whereabouts WHERE campaign_id = ? AND character_id = ?", (ctx.campaign_id, c["id"]))
    summary = f"{c['name']} joins the group"
    ctx.event("resident_recruited", summary, {"character_id": c["id"], "reason": op.reason})
    return {"summary": summary}


def _apply_dismiss(ctx: Ctx, op: DismissResident) -> dict:
    require_reference(ctx.conn)
    c = resident(ctx, op.resident)
    if not c["in_party"]:
        raise ChangeError(f"{c['name']} isn't with the group")
    s = active_story(ctx.conn, ctx.campaign_id)
    if s and one(ctx.conn, "SELECT 1 AS x FROM story_cast WHERE story_id = ? AND character_id = ?", (s["id"], c["id"])):
        raise ChangeError(f"{c['name']} is in the cast of {s['title']}; they leave only through the story")
    if op.where:
        place, zone = resolve_place(ctx.conn, op.where), None
        if not place:
            raise ChangeError(f"no place {op.where!r} in the reference data")
    else:
        h = residents.home(ctx.conn, one(ctx.conn, "SELECT id, usual_location, player_json FROM ref.npc_templates WHERE id = ?", (c["ref_id"],)))
        place, zone = h["place"], h["zone"]
    until = clock.next_morning(ctx.game_time())
    ctx.conn.execute("UPDATE characters SET in_party = 0, roster_category = 'allied_npcs', assignment = NULL WHERE id = ?", (c["id"],))
    ctx.conn.execute("""INSERT OR REPLACE INTO resident_whereabouts (campaign_id, character_id, place_id, zone_id, until_minutes, reason)
                        VALUES (?,?,?,?,?,?)""", (ctx.campaign_id, c["id"], place, zone, until, op.reason))
    name = one(ctx.conn, "SELECT name FROM ref.places WHERE id = ?", (place,))["name"]
    summary = f"{c['name']} heads back to camp" if not op.where else f"{c['name']} heads to {name}"
    ctx.event("resident_dismissed", summary, {"character_id": c["id"], "place_id": place})
    return {"summary": summary}


def register(ops: dict) -> None:
    ops.update({
        "resident_whereabouts": (ResidentWhereabouts, _apply_whereabouts),
        "recruit_resident": (RecruitResident, _apply_recruit),
        "dismiss_resident": (DismissResident, _apply_dismiss),
    })
