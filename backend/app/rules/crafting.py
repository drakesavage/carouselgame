"""Crafting (CORE_MECHANICS 13.1, 13.2, 13.5, 13.6; Core Rulebook pp. 223-227; page numbers are the rulebook index's).

One call is one crafting attempt, declared in full before anything is spent (13.1): the item, the crafting Skill, the
materials, the time and the Difficulty, from Table 43 by size or from a declared project's design (a blueprint may set
its own). Every material must be on hand with an established count, or nothing happens. Then, in order:

  materials are consumed (whether or not the roll succeeds, BOOK p. 227);
  the floor clock advances size x size hours (or the design's build time);
  the lead crafter makes an Unopposed Fabricate Check, or Engineering for moving parts, with Help from qualified
  assistants (13.6, BOOK 7.7);
  Success adds the finished item; Failure salvages by the margin missed (Table 47: 1-2 all, 3-9 half rounded down,
  10+ nothing; non-salvageable materials such as mixed liquids are lost).

Common household items cost 1 Misc. Junk and no Check. Magical crafting needs Floor 4+ and a crafting table (13.3); it
is refused below Floor 4 and is not a tool yet above it.
"""
from __future__ import annotations

import re
import sqlite3
from typing import Any, Literal

from pydantic import BaseModel, Field

from ..db import one
from .checks import CheckRequest, Help, Modifier, perform_check
from .dice import DiceRoller
from .engine import ChangeError, _find_item, apply_change, resolve_character

JUNK = "Miscellaneous Junk"
# Table 43: size -> (size number for size x size hours, Misc. Junk needed)
SIZES = {"tiny": (1, 1), "small": (2, 4), "petite": (3, 16), "medium": (4, 32), "large": (5, 64), "huge": (6, 128),
         "colossal": (7, 256), "gargantuan": (8, 512)}
CRAFT_STAT = {"fabricate": "intelligence", "engineering": "intelligence"}  # both INT (Core Rulebook pp. 107, 193)


class Material(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    quantity: int = Field(default=1, ge=1, le=100000)
    owner: str | None = None            # None / "shared" = shared stash
    salvageable: bool = True            # mixed liquid ingredients are typically not


class CraftRequest(BaseModel):
    crafter: str                        # the lead crafter who makes the Check
    item: str = Field(min_length=1, max_length=120)
    kind: Literal["common", "mundane", "magical"] = "mundane"
    size: Literal["tiny", "small", "petite", "medium", "large", "huge", "colossal", "gargantuan"] | None = None
    moving_parts: bool = False          # Engineering instead of Fabricate
    project: str | None = None          # a declared project/crafting quest: its design supplies the defaults
    skill: str | None = None
    stat: str | None = None
    dc: int | None = Field(default=None, ge=1, le=100)
    hours: int | None = Field(default=None, ge=0, le=300)
    junk: int | None = Field(default=None, ge=0, le=100000)
    materials: list[Material] = Field(default_factory=list)   # components beyond Misc. Junk
    help: list[Help] = Field(default_factory=list)
    advantage: int = Field(default=0, ge=0, le=5)
    disadvantage: int = Field(default=0, ge=0, le=5)
    modifiers: list[Modifier] = Field(default_factory=list)
    owner: str | None = None            # who receives the item (default shared stash)
    description: str | None = None
    advance_clock: bool = True          # false when the time is already covered by a parallel task (CORE 8.4)
    reason: str | None = None


def _project(conn: sqlite3.Connection, cid: int, title: str) -> dict:
    q = one(conn, "SELECT * FROM quests WHERE campaign_id = ? AND lower(title) = lower(?)", (cid, title.strip())) or \
        one(conn, "SELECT * FROM quests WHERE campaign_id = ? AND lower(title) LIKE lower(?) AND kind IN ('project','crafting') "
                  "ORDER BY status = 'active' DESC, id DESC", (cid, f"%{title.strip()}%"))
    if not q:
        raise ChangeError(f"no declared project {title!r}: record its design (quest kind project) before building it (13.1)")
    if q["status"] != "active" or str((q.get("details") or {}).get("status", "")).lower() in ("built", "complete", "completed"):
        raise ChangeError(f"project {q['title']!r} is already {q['status'] if q['status'] != 'active' else 'built'}")
    return q


def _design(q: dict) -> dict[str, Any]:
    """Defaults from a project's recorded design: '<skill>_difficulty', 'build_time' ('8h'), 'misc_junk_cost', lead, Advantage."""
    d = q.get("details") or {}
    out: dict[str, Any] = {}
    for k, v in d.items():
        m = re.fullmatch(r"([a-z_]+)_difficulty", k)
        if m and isinstance(v, int):
            out["skill"], out["dc"] = m.group(1).replace("_", " ").title(), v
    m = re.match(r"\s*(\d+)\s*h", str(d.get("build_time") or ""))
    if m:
        out["hours"] = int(m.group(1))
    if isinstance(d.get("misc_junk_cost"), int):
        out["junk"] = d["misc_junk_cost"]
    if d.get("lead"):
        out["lead"] = str(d["lead"])
    if d.get("build_advantage"):
        out["advantage"] = 1
    return out


def _salvaged(quantity: int, margin: int) -> int:
    """Table 47: missed by 1-2 salvages everything, 3-9 half (rounded down), 10+ nothing."""
    return quantity if margin <= 2 else quantity // 2 if margin <= 9 else 0


def craft(conn: sqlite3.Connection, roller: DiceRoller, cid: int, turn_id: int | None, req: CraftRequest) -> dict[str, Any]:
    crafter = resolve_character(conn, cid, req.crafter)
    floor = (one(conn, "SELECT current_floor FROM campaigns WHERE id = ?", (cid,)) or {}).get("current_floor") or 0
    if req.kind == "magical":
        if int(re.sub(r"\D", "", str(floor)) or 0) < 4:
            raise ChangeError("magical crafting needs the Fourth Floor or later and an appropriate crafting table (13.3)")
        raise ChangeError("magical crafting is not a tool yet: resolve it with skill_check and apply_changes")

    # ---- 13.1 declaration: everything settled before anything is spent
    q = _project(conn, cid, req.project) if req.project else None
    design = _design(q) if q else {}
    if design.get("lead") and design["lead"].split()[0].lower() not in crafter["name"].lower():
        raise ChangeError(f"{q['title']!r} is led by {design['lead']}; the lead crafter makes the Check (13.6)")
    if req.kind == "common":
        junk, hours, skill = 1, 0, None
    else:
        size = SIZES.get(req.size or "")
        junk = req.junk if req.junk is not None else design.get("junk", size[1] if size else None)
        hours = req.hours if req.hours is not None else design.get("hours", size[0] ** 2 if size else None)
        if junk is None or hours is None:
            raise ChangeError("declare the item's size (Table 43), or give junk and hours from its blueprint or project design")
        skill = req.skill or design.get("skill") or ("Engineering" if req.moving_parts else "Fabricate")
    need = ([Material(name=JUNK, quantity=junk)] if junk else []) + list(req.materials)
    spend = []
    for m in need:
        owner = resolve_character(conn, cid, m.owner, allow_shared=True) if m.owner and m.owner != "shared" else None
        it = _find_item(conn, cid, owner["id"] if owner else None, m.name)
        label = owner["name"] if owner else "the shared stash"
        if not it:
            raise ChangeError(f"{label} has no {m.name!r}; nothing was spent")
        if it["quantity"] is None:
            raise ChangeError(f"the count of {it['name']} in {label} is not established; establish it with set_quantity first")
        if it["quantity"] < m.quantity:
            raise ChangeError(f"{label} has {it['quantity']}x {it['name']}, the attempt needs {m.quantity}; nothing was spent")
        spend.append((m, it, owner))

    # ---- materials are consumed on the attempt (BOOK p. 227), then the time passes
    lines = []
    for m, it, owner in spend:
        apply_change(conn, cid, turn_id, {"op": "remove_item", "item_id": it["id"], "name": it["name"],
                                          "character": owner["name"] if owner else "shared", "quantity": m.quantity,
                                          "cause": f"crafting: {req.item}"})
    if spend:
        lines.append("Spent " + ", ".join(f"{m.quantity}x {it['name']}" for m, it, _ in spend))
    if hours and req.advance_clock:
        apply_change(conn, cid, turn_id, {"op": "advance_time", "minutes": hours * 60, "reason": f"Crafting {req.item} ({hours}h)"})
        lines.append(f"{hours}h of work")

    to = req.owner if req.owner and req.owner != "shared" else "shared"
    if req.kind == "common":
        apply_change(conn, cid, turn_id, {"op": "add_item", "character": to, "name": req.item, "quantity": 1, "description": req.description})
        lines.append(f"Recovered {req.item} from Misc. Junk (common household item; no Check)")
        return {"success": True, "item": req.item, "lines": lines, "summary": "; ".join(lines)}

    # ---- the Check (13.2; Help 13.6)
    stat = req.stat or CRAFT_STAT.get(skill.lower(), "intelligence")
    adv = max(req.advantage, design.get("advantage", 0))
    check = perform_check(conn, conn, roller, cid, turn_id, CheckRequest(
        character=crafter["name"], skill=skill, stat=stat, dc=req.dc if req.dc is not None else design.get("dc"),
        dc_kind="unopposed", advantage=adv, disadvantage=req.disadvantage, modifiers=req.modifiers, help=req.help,
        reason=req.reason or f"Craft {req.item} ({skill})"))
    lines.append(check["summary"])
    out: dict[str, Any] = {"success": check["success"], "item": req.item, "check": check, "hours": hours, "skill": skill}
    if check["success"]:
        apply_change(conn, cid, turn_id, {"op": "add_item", "character": to, "name": req.item, "quantity": 1,
                                          "description": req.description or (f"Built from the {q['title']} design." if q else None)})
        lines.append(f"Crafted {req.item}")
        if q:
            apply_change(conn, cid, turn_id, {"op": "quest", "title": q["title"], "status": "completed",
                                              "progress": f"Built: {req.item}.", "details": {"status": "built"}})
    else:
        margin = check["dc"] - check["total"]
        back = []
        for m, it, owner in spend:
            n = _salvaged(m.quantity, margin) if m.salvageable else 0
            if n:
                apply_change(conn, cid, turn_id, {"op": "add_item", "character": owner["name"] if owner else "shared", "name": it["name"],
                                                  "quantity": n})
                back.append(f"{n}x {it['name']}")
        lines.append(f"Failed by {margin}: " + (f"salvaged {', '.join(back)}" if back else "nothing salvaged") + " (Table 47)")
        out["salvaged"] = back
        if q:
            apply_change(conn, cid, turn_id, {"op": "quest", "title": q["title"],
                                              "progress": f"Attempt failed by {margin}; " + (f"salvaged {', '.join(back)}." if back else "nothing salvaged."),
                                              "details": {"last_attempt": {"missed_by": margin, "salvaged": back}}})
    out["lines"], out["summary"] = lines, "; ".join(lines)
    return out
