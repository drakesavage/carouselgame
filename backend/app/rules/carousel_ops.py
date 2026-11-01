"""Validated Carousel ops (registered into engine.CHANGE_OPS by rules/__init__.py).

Character creation from a starting Archetype's 11-point profile; temporary stat and Plot Armor modifiers; trope tickets:
grant, equip, unequip, use and reveal. The GM decides outcomes in the fiction; these ops keep the books (uses spent,
equip limits, recharge, who knows which trope) and write the events the chronicle shows."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

from ..carousel.refdata import require_reference, ticket_definition
from ..carousel.sheet import STATS, sheet
from ..db import one, rows
from .engine import ChangeError, Ctx, resolve_character

RULESET = "SB-0.4.1/role-0.4.2"
EQUIP_LIMIT = 4   # System Bible §4.3: provisional balance constant, not a book fact


class InitializeCharacter(BaseModel):
    op: Literal["initialize_character"]
    character: str
    role: str
    reason: str = Field(min_length=1, max_length=400)


class AddModifier(BaseModel):
    op: Literal["add_modifier"]
    character: str
    target: Literal["mettle", "moxie", "hustle", "savvy", "grit", "plot_armor"]
    op_kind: Literal["add", "multiply"]
    value: float
    source: str = Field(min_length=1, max_length=120)
    scope: Literal["scene", "story", "until_removed"]
    hidden: bool = False   # the source is secret (an NPC's hidden trope): the change shows, the source stays GM-only

    @field_validator("value")
    @classmethod
    def _nonzero(cls, v: float) -> float:
        if v == 0:
            raise ValueError("a modifier of 0 changes nothing")
        return v


class RemoveModifier(BaseModel):
    op: Literal["remove_modifier"]
    modifier_id: int
    reason: str


class GrantTicket(BaseModel):
    op: Literal["grant_ticket"]
    character: str
    ticket: str
    source: str = Field(min_length=1, max_length=120)
    hidden: bool = False


class TicketRef(BaseModel):
    op: Literal["equip_ticket", "unequip_ticket"]
    character: str
    ticket: str


class UseTrope(BaseModel):
    op: Literal["use_trope"]
    character: str
    ticket: str
    effect: str = Field(min_length=1, max_length=600)


class RevealTicket(BaseModel):
    op: Literal["reveal_ticket"]
    character: str
    ticket: str
    how: str = Field(min_length=1, max_length=200)


def _owned(ctx: Ctx, char: dict, ticket: str) -> tuple[dict, dict]:
    """(character_tickets row, definition) for a ticket the character owns, by ref or by exact name."""
    for t in rows(ctx.conn, "SELECT * FROM character_tickets WHERE character_id = ?", (char["id"],)):
        d = ticket_definition(ctx.conn, t["ticket_ref"]) or {"ref": t["ticket_ref"], "name": t["ticket_ref"]}
        if ticket in (t["ticket_ref"], d["name"]) or ticket.strip().lower() == d["name"].lower():
            return t, d
    raise ChangeError(f"{char['name']} does not own the ticket {ticket!r}")


def _vis(t: dict) -> str:
    return "gm" if t["hidden"] else "player"


def _initialize(ctx: Ctx, c: dict, role_id: str, reason: str) -> dict:
    """Set a character's stats once from a starting Archetype's profile (also used by silas_assigns)."""
    require_reference(ctx.conn)
    if c.get("mettle") is not None:
        raise ChangeError(f"{c['name']}'s stats are set once; changes go through modifiers or rewards")
    role = one(ctx.conn, "SELECT r.name, p.mettle, p.moxie, p.hustle, p.savvy, p.grit, p.base_pa FROM ref.roles r "
                         "JOIN ref.start_profiles p ON p.role_id = r.id WHERE r.id = ? AND r.kind = 'base'", (role_id,))
    if not role:
        raise ChangeError(f"{role_id!r} is not a starting Archetype with a profile")
    stats = {s: role[s.lower()] for s in STATS}
    ctx.conn.execute("UPDATE characters SET mettle = ?, moxie = ?, hustle = ?, savvy = ?, grit = ?, role_id = ?, role_reason = ?, "
                     "ruleset_version = ? WHERE id = ?", (*stats.values(), role_id, reason, RULESET, c["id"]))
    summary = f"Carousel names {c['name']} the {role['name']}: {reason}"
    ctx.event("character_stats_initialized", summary, {"character_id": c["id"], "role": role_id, "stats": stats,
                                                       "base_pa": sum(stats.values()), "ruleset": RULESET})
    return {"summary": summary, "role_name": role["name"]}


def _apply_initialize(ctx: Ctx, op: InitializeCharacter) -> dict:
    from ..carousel.opening import stage
    if stage(ctx.conn, ctx.campaign_id) in ("car", "silas"):
        raise ChangeError("during the opening, Silas assigns Archetypes (silas_assigns)")
    r = _initialize(ctx, resolve_character(ctx.conn, ctx.campaign_id, op.character), op.role, op.reason)
    return {"summary": r["summary"]}


def _apply_add_modifier(ctx: Ctx, op: AddModifier) -> dict:
    c = resolve_character(ctx.conn, ctx.campaign_id, op.character)
    if op.op_kind == "multiply" and op.target != "plot_armor":
        raise ChangeError("only Plot Armor takes a multiplier; change a stat with op_kind add")
    if op.op_kind == "multiply" and op.value < 0:
        raise ChangeError("a multiplier must be positive")
    mid = ctx.conn.execute("INSERT INTO stat_modifiers (campaign_id, character_id, target, op, value, source, scope, hidden, turn_id) VALUES (?,?,?,?,?,?,?,?,?)",
                           (ctx.campaign_id, c["id"], op.target, op.op_kind, op.value, op.source, op.scope, int(op.hidden), ctx.turn_id)).lastrowid
    pa = sheet(ctx.conn, c["id"])["plot_armor"]
    change = f"×{op.value:g}" if op.op_kind == "multiply" else f"{op.value:+g}"
    summary = f"{c['name']}: {op.target.replace('_', ' ')} {change} from {op.source} ({op.scope.replace('_', ' ')}); Plot Armor now {pa['effective']}"
    vis = "gm" if op.hidden else "player"
    ctx.event("modifier_added", summary, {"character_id": c["id"], "modifier_id": mid}, vis)
    return {"summary": summary, "modifier_id": mid, "visibility": vis}


def _apply_remove_modifier(ctx: Ctx, op: RemoveModifier) -> dict:
    m = one(ctx.conn, "SELECT m.*, c.name FROM stat_modifiers m JOIN characters c ON c.id = m.character_id WHERE m.id = ? AND m.campaign_id = ?",
            (op.modifier_id, ctx.campaign_id))
    if not m:
        raise ChangeError(f"no modifier #{op.modifier_id}")
    ctx.conn.execute("DELETE FROM stat_modifiers WHERE id = ?", (m["id"],))
    summary = f"{m['name']} is no longer affected by {m['source']} ({op.reason})"
    vis = "gm" if m["hidden"] else "player"
    ctx.event("modifier_removed", summary, {"character_id": m["character_id"], "modifier_id": m["id"]}, vis)
    return {"summary": summary, "visibility": vis}


def _grant(ctx: Ctx, c: dict, ticket_ref: str, source: str, hidden: bool = False) -> dict:
    """Give a character a reference ticket they don't own yet (also used by silas_assigns)."""
    d = ticket_definition(ctx.conn, ticket_ref)
    if not d:
        raise ChangeError(f"no such ticket {ticket_ref!r} in the reference data")
    if one(ctx.conn, "SELECT id FROM character_tickets WHERE character_id = ? AND ticket_ref = ?", (c["id"], d["ref"])):
        raise ChangeError(f"{c['name']} already owns {d['name']}")
    ctx.conn.execute("""INSERT INTO character_tickets (campaign_id, character_id, ticket_ref, uses_left, uses_max, recharge, hidden, source, acquired_turn_id)
                        VALUES (?,?,?,?,?,?,?,?,?)""", (ctx.campaign_id, c["id"], d["ref"], d["uses_max"], d["uses_max"], d["recharge"],
                                                         int(hidden), source, ctx.turn_id))
    summary = f"{c['name']} receives the ticket {d['name']}"
    ctx.event("ticket_granted", summary, {"character_id": c["id"], "ticket": d["ref"], "source": source}, "gm" if hidden else "player")
    return {"summary": summary, "visibility": "gm" if hidden else "player", "name": d["name"]}


def _apply_grant(ctx: Ctx, op: GrantTicket) -> dict:
    r = _grant(ctx, resolve_character(ctx.conn, ctx.campaign_id, op.character), op.ticket, op.source, op.hidden)
    return {"summary": r["summary"], "visibility": r["visibility"]}


def _apply_equip(ctx: Ctx, op: TicketRef) -> dict:
    c = resolve_character(ctx.conn, ctx.campaign_id, op.character)
    t, d = _owned(ctx, c, op.ticket)
    equip = op.op == "equip_ticket"
    if equip and not t["equipped"]:
        n = one(ctx.conn, "SELECT COUNT(*) AS n FROM character_tickets WHERE character_id = ? AND equipped = 1", (c["id"],))["n"]
        if n >= EQUIP_LIMIT:
            raise ChangeError(f"equip limit {EQUIP_LIMIT}; unequip one first")
    ctx.conn.execute("UPDATE character_tickets SET equipped = ? WHERE id = ?", (int(equip), t["id"]))
    summary = f"{c['name']} {'equips' if equip else 'puts away'} {d['name']}"
    ctx.event("ticket_equipped" if equip else "ticket_unequipped", summary, {"character_id": c["id"], "ticket": t["ticket_ref"]}, _vis(t))
    return {"summary": summary, "visibility": _vis(t)}


def _apply_use(ctx: Ctx, op: UseTrope) -> dict:
    require_reference(ctx.conn)
    c = resolve_character(ctx.conn, ctx.campaign_id, op.character)
    t, d = _owned(ctx, c, op.ticket)
    if c["participation"] in ("observer", "none"):
        raise ChangeError(f"{c['name']} can't act in the story now (participation {c['participation']}); tropes need a living hand")
    if c["participation"] == "posthumous_limited" and t["ticket_ref"] != c["rights_basis"]:
        raise ChangeError(f"{c['name']} is dead and may play only {c['rights_basis']} through the posthumous window")
    if not t["equipped"]:
        raise ChangeError(f"{d['name']} is not equipped")
    if t["uses_left"] is not None and t["uses_left"] <= 0:
        raise ChangeError(f"{d['name']} has no uses left" + (f" (recharges per {t['recharge']})" if t["recharge"] else ""))
    left = None if t["uses_left"] is None else t["uses_left"] - 1
    ctx.conn.execute("UPDATE character_tickets SET uses_left = ? WHERE id = ?", (left, t["id"]))
    if c["participation"] == "posthumous_limited":   # the window closes after its one use
        from ..carousel.fate import set_rights
        ctx.conn.execute("UPDATE characters SET rights_basis = NULL WHERE id = ?", (c["id"],))
        set_rights(ctx.conn, c["id"])
    summary = f"{c['name']} plays {d['name']}: {op.effect}"
    ctx.event("trope_used", summary, {"character_id": c["id"], "ticket": t["ticket_ref"], "effect": op.effect, "uses_left": left}, _vis(t))
    return {"summary": summary, "uses_left": left, "visibility": _vis(t)}


def _apply_reveal(ctx: Ctx, op: RevealTicket) -> dict:
    c = resolve_character(ctx.conn, ctx.campaign_id, op.character)
    t, d = _owned(ctx, c, op.ticket)
    ctx.conn.execute("UPDATE character_tickets SET hidden = 0 WHERE id = ?", (t["id"],))
    summary = f"{c['name']}'s trope {d['name']} is revealed ({op.how})"
    ctx.event("ticket_revealed", summary, {"character_id": c["id"], "ticket": t["ticket_ref"]})
    return {"summary": summary}


def recharge(ctx: Ctx, scope: Literal["scene", "story"]) -> int:
    """Refill every ticket that recharges per `scope`; returns how many were refilled. Scene close calls "scene";
    the story runner calls "story" at The End."""
    return ctx.conn.execute("UPDATE character_tickets SET uses_left = uses_max WHERE campaign_id = ? AND recharge = ? "
                            "AND uses_max IS NOT NULL AND uses_left IS NOT uses_max", (ctx.campaign_id, scope)).rowcount


def end_scene_modifiers(ctx: Ctx) -> int:
    return ctx.conn.execute("DELETE FROM stat_modifiers WHERE campaign_id = ? AND scope = 'scene'", (ctx.campaign_id,)).rowcount


def register(ops: dict) -> None:
    ops.update({
        "initialize_character": (InitializeCharacter, _apply_initialize),
        "add_modifier": (AddModifier, _apply_add_modifier),
        "remove_modifier": (RemoveModifier, _apply_remove_modifier),
        "grant_ticket": (GrantTicket, _apply_grant),
        "equip_ticket": (TicketRef, _apply_equip),
        "unequip_ticket": (TicketRef, _apply_equip),
        "use_trope": (UseTrope, _apply_use),
        "reveal_ticket": (RevealTicket, _apply_reveal),
    })
