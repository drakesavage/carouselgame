"""Validated new-game ops (registered into engine.CHANGE_OPS by rules/__init__.py; the flow is carousel/opening.py).

`set_lure` records why the group came (the belief is the players', the truth is GM-only), `drop_friend` is the player's
veto on a GM-made friend in the car, `introduce_resident` brings a Camp Dyer resident template into the campaign (Phase 7
reuses it), `silas_assigns` is Silas dealing a character's Archetype and two starter tickets from quoted evidence,
`pay_off_lure` closes the lure at the Lodge."""
from __future__ import annotations

import json
import re
from typing import Annotated, Literal

from pydantic import BaseModel, Field, model_validator

from ..carousel import opening
from ..carousel.refdata import require_reference
from ..db import now_iso, one
from .engine import ChangeError, Ctx, resolve_character


class SetLure(BaseModel):
    op: Literal["set_lure"]
    kind: Literal["person", "event", "other"]
    belief: str = Field(min_length=1, max_length=400)
    truth: str = Field(min_length=1, max_length=1200)
    resident: str | None = None

    @model_validator(mode="after")
    def _person(self):
        if self.kind == "person" and not self.resident:
            raise ValueError("a person lure names the resident (a CAR-NPC-… template)")
        return self


class DropFriend(BaseModel):
    op: Literal["drop_friend"]
    character: str
    reason: str = Field(min_length=1, max_length=300)


class IntroduceResident(BaseModel):
    op: Literal["introduce_resident"]
    resident: str
    in_party: bool = False
    location: str | None = None


class SilasAssigns(BaseModel):
    op: Literal["silas_assigns"]
    character: str
    role: str
    reason: str = Field(min_length=1, max_length=400)
    evidence: list[Annotated[str, Field(min_length=8, max_length=300)]] = Field(min_length=1, max_length=5)
    contextual_ticket: str
    contextual_reason: str = Field(min_length=1, max_length=300)


class PayOffLure(BaseModel):
    op: Literal["pay_off_lure"]
    how: str = Field(min_length=1, max_length=600)


def _at(ctx: Ctx, *stages: str) -> str:
    s = opening.stage(ctx.conn, ctx.campaign_id)
    if s not in stages:
        raise ChangeError(f"only during the opening's {' or '.join(stages)} stage (now: {s or 'no opening'})")
    return s


def _template(ctx: Ctx, rid: str) -> dict:
    t = one(ctx.conn, "SELECT * FROM ref.npc_templates WHERE id = ?", (rid,))
    if not t:
        raise ChangeError(f"no resident template {rid!r}")
    return t


def _resident_in_campaign(ctx: Ctx, rid: str) -> dict | None:
    return one(ctx.conn, """SELECT c.* FROM characters c JOIN npc_profiles p ON p.character_id = c.id
                            WHERE c.campaign_id = ? AND p.ref_id = ?""", (ctx.campaign_id, rid))


def _apply_set_lure(ctx: Ctx, op: SetLure) -> dict:
    require_reference(ctx.conn)
    _at(ctx, "car")
    if one(ctx.conn, "SELECT lure_kind FROM campaigns WHERE id = ?", (ctx.campaign_id,))["lure_kind"]:
        raise ChangeError("the lure is already set")
    if op.resident:
        _template(ctx, op.resident)
    ctx.conn.execute("UPDATE campaigns SET lure_kind = ?, lure_belief = ?, lure_resident = ? WHERE id = ?",
                     (op.kind, op.belief, op.resident, ctx.campaign_id))
    t = now_iso()
    ctx.conn.execute("INSERT INTO gm_secrets (campaign_id, subject, kind, content, source, created_at) VALUES (?,?,?,?,?,?)",
                     (ctx.campaign_id, "lure", "lure", op.truth, "opening", t))
    ctx.conn.execute("""INSERT INTO lore_facts (campaign_id, subject, fact, certainty, visibility, source, created_at)
                        VALUES (?,?,?,?,?,?,?)""", (ctx.campaign_id, "Why we came", op.belief, "rumor", "player", "opening", t))
    summary = f"Why we came: {op.belief}"
    ctx.event("opening_lure", summary, {"kind": op.kind, "belief": op.belief})
    return {"summary": summary}


def _apply_drop_friend(ctx: Ctx, op: DropFriend) -> dict:
    _at(ctx, "car")
    c = resolve_character(ctx.conn, ctx.campaign_id, op.character)
    if c["id"] not in {f["id"] for f in opening.friends(ctx.conn, ctx.campaign_id)}:
        raise ChangeError(f"{c['name']} is not one of the friends in the car")
    ctx.conn.execute("UPDATE characters SET in_party = 0, status = 'dormant', roster_category = 'cut_from_opening' WHERE id = ?", (c["id"],))
    summary = f"{c['name']} isn't in the car after all ({op.reason})"
    ctx.event("opening_friend_dropped", summary, {"character_id": c["id"]})
    return {"summary": summary}


def _apply_introduce_resident(ctx: Ctx, op: IntroduceResident) -> dict:
    require_reference(ctx.conn)
    t = _template(ctx, op.resident)
    if _resident_in_campaign(ctx, op.resident):
        raise ChangeError(f"{t['name']} is already in this campaign")
    public = t["player"]   # the template's player-visible part; gm_only is t["gm"]
    base = re.sub(r"[^a-z0-9]+", "-", t["name"].lower()).strip("-") or "resident"
    slug, n = base, 2
    while one(ctx.conn, "SELECT id FROM characters WHERE campaign_id = ? AND slug = ?", (ctx.campaign_id, slug)):
        slug, n = f"{base}-{n}", n + 1
    sheet = {"narrative": {"current_role": t["camp_role"]}} if t["camp_role"] else {}
    char_id = ctx.conn.execute(
        """INSERT INTO characters (campaign_id, slug, name, kind, roster_category, status, location, in_party, bio, sheet_json, provenance)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (ctx.campaign_id, slug, t["name"], "npc", "core_party" if op.in_party else "allied_npcs", "active", op.location,
         int(op.in_party), public.get("appearance"), json.dumps(sheet), f"resident {op.resident}")).lastrowid
    ctx.conn.execute("INSERT INTO npc_profiles (character_id, personality, last_seen, ref_id) VALUES (?,?,?,?)",
                     (char_id, public.get("personality"), op.location, op.resident))
    ctx.conn.execute("INSERT INTO gm_secrets (campaign_id, subject, kind, content, source, created_at) VALUES (?,?,?,?,?,?)",
                     (ctx.campaign_id, slug, "companion_sheet", json.dumps(t["gm"], ensure_ascii=False), "resident template", now_iso()))
    summary = f"Met {t['name']}" + (f", {t['camp_role']}" if t["camp_role"] else "")
    ctx.event("npc_met", summary, {"character_id": char_id, "role": t["camp_role"]})
    return {"summary": summary, "character_id": char_id}


def _apply_silas_assigns(ctx: Ctx, op: SilasAssigns) -> dict:
    """Every check runs before any write, so a refusal leaves no stats, tickets or evidence behind."""
    from .carousel_ops import _grant, _initialize
    require_reference(ctx.conn)
    _at(ctx, "silas")
    c = resolve_character(ctx.conn, ctx.campaign_id, op.character)
    pc = one(ctx.conn, "SELECT active_character_id FROM campaigns WHERE id = ?", (ctx.campaign_id,))["active_character_id"]
    if c["id"] != pc and c["id"] not in {f["id"] for f in opening.friends(ctx.conn, ctx.campaign_id)}:
        raise ChangeError(f"Silas deals only to the group from the car; {c['name']} isn't one of them")
    if c.get("role_id") or c.get("mettle") is not None:
        raise ChangeError(f"Silas has already dealt {c['name']} their tickets")
    if not one(ctx.conn, "SELECT 1 AS x FROM ref.roles r JOIN ref.start_profiles p ON p.role_id = r.id WHERE r.id = ? AND r.kind = 'base'", (op.role,)):
        raise ChangeError(f"{op.role!r} is not a starting Archetype with a profile")
    for q in op.evidence:
        if not opening.quote_found(ctx.conn, ctx.campaign_id, q):
            raise ChangeError(f"Silas can only cite what happened: {q!r} was never said")
    if not one(ctx.conn, "SELECT 1 AS x FROM ref.tickets WHERE id = ? AND relationship = 'contextual_shared_starter'", (op.contextual_ticket,)):
        raise ChangeError(f"{op.contextual_ticket!r} is not a contextual starter ticket")
    sig = one(ctx.conn, "SELECT id FROM ref.tickets WHERE relationship = 'starting_signature' AND source_role_id = ?", (op.role,))
    if not sig:
        raise ChangeError(f"{op.role} has no starting signature ticket in the reference data")
    role_name = _initialize(ctx, c, op.role, op.reason)["role_name"]
    names = [_grant(ctx, c, t, "Silas")["name"] for t in (sig["id"], op.contextual_ticket)]
    ctx.conn.execute("UPDATE characters SET role_evidence_json = ? WHERE id = ?",
                     (json.dumps({"quotes": op.evidence, "contextual_reason": op.contextual_reason}, ensure_ascii=False), c["id"]))
    summary = f"Silas deals {c['name']} three tickets: {role_name} ({op.reason}), {names[0]}, {names[1]}"
    ctx.event("silas_deals", summary, {"character_id": c["id"], "role": op.role, "tickets": [sig["id"], op.contextual_ticket],
                                       "evidence": op.evidence, "contextual_reason": op.contextual_reason})
    return {"summary": summary}


def _apply_pay_off_lure(ctx: Ctx, op: PayOffLure) -> dict:
    _at(ctx, "escort")
    camp = one(ctx.conn, "SELECT node_id, lure_kind, lure_resident, lure_paid_off FROM campaigns WHERE id = ?", (ctx.campaign_id,))
    if not camp["lure_kind"]:
        raise ChangeError("there is no lure to pay off")
    if camp["lure_paid_off"]:
        raise ChangeError("the lure is already paid off")
    if camp["node_id"] != opening.LODGE:
        raise ChangeError("the lure pays off at Dyer's Lodge; the group isn't there yet")
    if camp["lure_resident"] and not _resident_in_campaign(ctx, camp["lure_resident"]):
        raise ChangeError(f"introduce {_template(ctx, camp['lure_resident'])['name']} (introduce_resident) before paying off the lure")
    ctx.conn.execute("UPDATE campaigns SET lure_paid_off = ? WHERE id = ?", (op.how, ctx.campaign_id))
    summary = f"Why we came, answered: {op.how}"
    ctx.event("opening_lure_paid_off", summary, {"how": op.how})
    return {"summary": summary}


def register(ops: dict) -> None:
    ops.update({
        "set_lure": (SetLure, _apply_set_lure),
        "drop_friend": (DropFriend, _apply_drop_friend),
        "introduce_resident": (IntroduceResident, _apply_introduce_resident),
        "silas_assigns": (SilasAssigns, _apply_silas_assigns),
        "pay_off_lure": (PayOffLure, _apply_pay_off_lure),
    })
