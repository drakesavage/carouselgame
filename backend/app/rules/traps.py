"""Traps (CORE_MECHANICS 13.7, CAMPAIGN). The published books give no general procedure for crawler-built traps (Trap
Engineer only covers crafting them, Core Rulebook p. 203), so this enforces the campaign rule's record-keeping and
timing rather than inventing statistics:

* A trap is an item whose `properties.trap` record states its components and ammunition, deployment time and Action
  cost, trigger, target area or lane, Check, Difficulty, damage and effects, reset/reload procedure, and bulk. Its
  current condition lives in `properties.trap_state`. A trap with no complete record cannot be deployed: the GM or the
  owner records it first (a ruling, labelled), never at the moment of resolution.
* The record is locked while the trap is deployed: "a trap cannot acquire a new capability between deployment and
  resolution".
* deploy -> armed; trigger -> its Check (a fixed attack bonus, an operator's Skill, or a target's save) and damage,
  ammunition spent per trigger, then triggered (or spent, if single-use); reset -> armed again after its reset cost;
  stow -> ready. Costs are Actions in an active combat, otherwise game time.
"""
from __future__ import annotations

import json
import sqlite3
from typing import Any, Literal

from pydantic import BaseModel, Field

from ..db import one
from .checks import STAT_ALIASES, degree_of, is_success
from .dice import DiceRoller, record_roll
from .engine import ChangeError, Ctx, _find_item, apply_change, resolve_character


class TrapAmmo(BaseModel):
    item: str                              # the inventory item each trigger consumes (e.g. "Paralytic Dart")
    per_trigger: int = Field(default=1, ge=1, le=100)


class Cost(BaseModel):
    actions: int = Field(default=0, ge=0, le=10)
    minutes: int = Field(default=0, ge=0, le=600)


class TrapCheck(BaseModel):
    kind: Literal["attack", "operator", "save", "none"]
    attack_bonus: int | None = Field(default=None, ge=-10, le=60)   # attack: d20 + this vs the target's Evade
    operator_skill: str | None = None                               # operator: the operator's Skill Rank + Stat Mod
    operator_stat: str | None = None
    save_stat: str | None = None                                    # save: the target's Stat Check vs dc
    dc: int | None = Field(default=None, ge=1, le=100)              # save Difficulty, or the target number outside combat


class TrapSpec(BaseModel):
    components: str = Field(min_length=3)
    ammunition: TrapAmmo | None = None
    deploy: Cost
    trigger: str = Field(min_length=3)
    area: str = Field(min_length=3)
    check: TrapCheck
    damage: str | None = None             # dice, e.g. "2d6+2 Piercing"; None for obstacles and alarms
    effects: str | None = None            # e.g. "Held until it escapes (Escape Artist vs 14)"
    reset: Cost
    reset_notes: str | None = None
    single_use: bool = False
    bulk: str = Field(min_length=2)
    label: Literal["BOOK", "CAMPAIGN", "INTERPRETATION"] = "CAMPAIGN"
    source: str = Field(min_length=3)     # who established it and where (a ruling, a build record)


def _trap(conn: sqlite3.Connection, cid: int, ref: str) -> dict:
    it = _find_item(conn, cid, None, ref)
    if not it:
        raise ChangeError(f"no trap {ref!r} in the shared stash")
    return it


def _save(conn: sqlite3.Connection, it: dict, props: dict) -> None:
    conn.execute("UPDATE items SET properties_json = ? WHERE id = ?", (json.dumps(props, ensure_ascii=False), it["id"]))


def _spend(conn: sqlite3.Connection, cid: int, turn_id: int | None, roller: DiceRoller, who: str | None, cost: Cost, what: str) -> list[str]:
    from .combat import Combat, active_encounter
    lines = []
    if active_encounter(conn, cid) and cost.actions:
        if not who:
            raise ChangeError(f"{what} costs {cost.actions} Action(s) in combat: name who does it")
        cb = Combat(conn, cid, turn_id, roller)
        for _ in range(cost.actions):
            cb.spend_action(who, what)
        lines.append(f"{who} spends {cost.actions} Action(s): {what}")
    elif cost.minutes:
        apply_change(conn, cid, turn_id, {"op": "advance_time", "minutes": cost.minutes, "reason": what})
        lines.append(f"{cost.minutes} minutes: {what}")
    return lines


def record(conn: sqlite3.Connection, cid: int, trap: str, spec: dict) -> dict:
    it = _trap(conn, cid, trap)
    props = it.get("properties") or {}
    if (props.get("trap_state") or {}).get("condition") in ("armed", "triggered"):
        raise ChangeError(f"{it['name']} is deployed: its record is locked until it is stowed (13.7)")
    s = TrapSpec.model_validate(spec)
    c = s.check
    if c.kind == "attack" and c.attack_bonus is None:
        raise ChangeError("an attack trap needs attack_bonus")
    if c.kind == "operator" and not (c.operator_skill and c.operator_stat):
        raise ChangeError("an operated trap needs operator_skill and operator_stat")
    if c.kind == "save" and not (c.save_stat and c.dc):
        raise ChangeError("a save trap needs save_stat and dc")
    props["trap"] = s.model_dump()
    props.setdefault("trap_state", {"condition": "ready"})
    _save(conn, it, props)
    Ctx(conn, cid, None).event("trap_recorded", f"Trap record set for {it['name']} ({s.label}; {s.source})", {"item_id": it["id"]})
    return {"summary": f"{it['name']}: trap record set ({s.label})"}


def _spec(it: dict) -> tuple[TrapSpec, dict]:
    props = it.get("properties") or {}
    if not props.get("trap"):
        raise ChangeError(f"{it['name']} has no trap record (components, trigger, area, Check, damage, reset, bulk); "
                          "record it with trap_record before it is deployed (13.7)")
    return TrapSpec.model_validate(props["trap"]), props


def deploy(conn, roller, cid, turn_id, trap: str, location: str, by: str | None = None) -> dict:
    it = _trap(conn, cid, trap)
    spec, props = _spec(it)
    st = props.get("trap_state") or {}
    if st.get("condition") not in (None, "ready"):
        raise ChangeError(f"{it['name']} is {st.get('condition')}, not ready to deploy")
    if spec.ammunition:
        ammo = _find_item(conn, cid, None, spec.ammunition.item)
        if not ammo or ammo["quantity"] is None or ammo["quantity"] < spec.ammunition.per_trigger:
            raise ChangeError(f"{it['name']} needs {spec.ammunition.per_trigger}x {spec.ammunition.item} loaded; not enough on hand")
    lines = _spend(conn, cid, turn_id, roller, by, spec.deploy, f"Deploy {it['name']}")
    props["trap_state"] = {"condition": "armed", "location": location, "deployed_by": by, "triggers": 0}
    _save(conn, it, props)
    Ctx(conn, cid, turn_id).event("trap_deployed", f"{it['name']} armed at {location}", {"item_id": it["id"]})
    return {"summary": "; ".join(lines + [f"{it['name']} armed at {location} (trigger: {spec.trigger}; area: {spec.area})"]), "lines": lines}


def trigger(conn, roller, cid, turn_id, trap: str, target: str, operator: str | None = None, advantage: int = 0,
            target_number: int | None = None) -> dict:
    from .combat import Combat, _find, _state, active_encounter
    it = _trap(conn, cid, trap)
    spec, props = _spec(it)
    st = props.get("trap_state") or {}
    if st.get("condition") != "armed":
        raise ChangeError(f"{it['name']} is {st.get('condition') or 'not deployed'}; only an armed trap triggers")
    enc = active_encounter(conn, cid)
    mob = _find(_state(enc), target, "foe") if enc else None
    lines, hit = [], True
    if spec.ammunition:
        apply_change(conn, cid, turn_id, {"op": "remove_item", "name": spec.ammunition.item, "character": "shared",
                                          "quantity": spec.ammunition.per_trigger, "cause": f"{it['name']} triggered"})
        lines.append(f"Spent {spec.ammunition.per_trigger}x {spec.ammunition.item}")
    c = spec.check
    if c.kind in ("attack", "operator"):
        if c.kind == "attack":
            bonus, who = c.attack_bonus, it["name"]
        else:
            if not operator:
                raise ChangeError(f"{it['name']} is operated: name the operator")
            op = resolve_character(conn, cid, operator)
            stat = STAT_ALIASES.get(c.operator_stat.lower(), c.operator_stat.lower())
            rank = (one(conn, "SELECT rank FROM skills WHERE character_id = ? AND lower(name) = lower(?)", (op["id"], c.operator_skill)) or {}).get("rank") or 0
            mod = ((op.get("stats") or {}).get("modifiers") or {}).get(stat)
            if mod is None:
                raise ChangeError(f"{op['name']} has no recorded {stat} modifier")
            bonus, who = int(rank) + int(mod), op["name"]
        target_no = mob["evade"] if mob else (target_number if target_number is not None else c.dc)
        if target_no is None:
            raise ChangeError("outside combat, give target_number: the target's Evade (a Mob's default is 10 + DEX Mod + Floor)")
        d20 = "2d20kh1" if advantage > 0 else "1d20"
        r = roller.roll(f"{d20}+{bonus}", target=target_no, reason=f"{it['name']} triggers on {target}")
        nat = next(d.value for d in r.dice if d.kept)
        r.degree = degree_of(nat, r.total, target_no)
        r.outcome = "success" if is_success(r.degree) else "failure"
        r.detail = {"kind": "trap", "trap": it["name"], "operator": who, "natural": nat, "dc": target_no,
                    "summary": f"{it['name']} ({who}): d20 {nat} {bonus:+d} = {r.total} vs {'Evade ' if mob else ''}{target_no} → {r.degree}"}
        record_roll(conn, cid, r, turn_id=turn_id, hidden=False)
        hit = is_success(r.degree)
        lines.append(r.detail["summary"])
    elif c.kind == "save":
        if mob:
            raise ChangeError("Mobs do not roll saves (the GM does not roll for Mobs): give this trap an attack Check against Evade")
        lines.append(f"{target} must make a {c.save_stat} Stat Check vs {c.dc} (skill_check); on a Failure: {spec.effects or spec.damage}")
        hit = None
    if hit and spec.damage:
        if mob:
            lines.append(Combat(conn, cid, turn_id, roller).damage(mob["name"], expression=spec.damage.split()[0],
                                                                    damage_type=(spec.damage.split()[1:] or [None])[0], cause=it["name"])["summary"])
        else:
            d = roller.roll(spec.damage.split()[0], reason=f"{it['name']} damage to {target}")
            record_roll(conn, cid, d, turn_id=turn_id)
            lines.append(f"Damage {spec.damage} = {d.total} (apply it to {target})")
    if hit and spec.effects:
        lines.append(f"Effect: {spec.effects}")
    if hit is False:
        lines.append("No effect.")
    st["triggers"] = int(st.get("triggers") or 0) + 1
    st["condition"] = "spent" if spec.single_use else "triggered"
    props["trap_state"] = st
    _save(conn, it, props)
    Ctx(conn, cid, turn_id).event("trap_triggered", f"{it['name']} triggered on {target}", {"item_id": it["id"], "hit": hit})
    lines.append(f"{it['name']} is now {st['condition']}" + ("" if spec.single_use else f"; reset: {spec.reset_notes or 'see its record'}"))
    return {"summary": " ".join(lines), "lines": lines, "hit": hit, "condition": st["condition"]}


def reset(conn, roller, cid, turn_id, trap: str, by: str | None = None) -> dict:
    """Triggered -> armed again where it stands, or ready if it was stowed first."""
    it = _trap(conn, cid, trap)
    spec, props = _spec(it)
    st = props.get("trap_state") or {}
    if st.get("condition") != "triggered":
        raise ChangeError(f"{it['name']} is {st.get('condition') or 'not deployed'}; only a triggered trap is reset")
    if spec.ammunition:
        ammo = _find_item(conn, cid, None, spec.ammunition.item)
        if not ammo or ammo["quantity"] is None or ammo["quantity"] < spec.ammunition.per_trigger:
            raise ChangeError(f"no {spec.ammunition.item} left to reload {it['name']}")
    lines = _spend(conn, cid, turn_id, roller, by, spec.reset, f"Reset {it['name']}")
    st["condition"] = "armed" if st.get("location") else "ready"
    props["trap_state"] = st
    _save(conn, it, props)
    return {"summary": "; ".join(lines + [f"{it['name']} " + (f"re-armed at {st['location']}" if st.get("location") else "reset and ready")]),
            "lines": lines}


def stow(conn, cid, trap: str) -> dict:
    it = _trap(conn, cid, trap)
    _, props = _spec(it)
    st = props.get("trap_state") or {}
    if st.get("condition") == "spent":
        raise ChangeError(f"{it['name']} is spent (single-use); remove it or rebuild it")
    cond = "triggered" if st.get("condition") == "triggered" else "ready"   # a fired trap still needs its reset
    props["trap_state"] = {"condition": cond, "triggers": st.get("triggers", 0)}
    _save(conn, it, props)
    return {"summary": f"{it['name']} recovered and stowed ({'needs its reset' if cond == 'triggered' else 'ready'})"}


def status(conn, cid) -> list[dict[str, Any]]:
    """Every trap with its condition, and every likely trap still without a record."""
    from ..db import rows
    out = []
    for it in rows(conn, "SELECT id, name, quantity, properties_json FROM items WHERE campaign_id = ? AND owner_character_id IS NULL", (cid,)):
        p = it.get("properties") or {}
        if p.get("trap") or "trap" in it["name"].lower() or p.get("trap_state"):
            out.append({"id": it["id"], "name": it["name"], "recorded": bool(p.get("trap")), "condition": (p.get("trap_state") or {}).get("condition")})
    return out
