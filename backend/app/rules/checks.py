"""Checks and advancement, following the campaign's CORE_MECHANICS.md (labels in comments).

Core Check (BOOK 7.1): 1d20 + Skill Rank + Stat Mod + other modifiers vs Difficulty.
Default Difficulties (BOOK 7.2-7.4), Advantage/Disadvantage cancelling one-for-one (BOOK 7.6),
untrained = Disadvantage and no Rank (BOOK 7.5), Help dice added before the roll (BOOK 7.7),
degrees of outcome (BOOK 7.9), skill-use marks (BOOK 14.5) and the twelve-cycle correspondence
advancement checkpoint (CAMPAIGN 14.3).

Rolls are recorded on `audit` (autocommit, survives failed turns); state changes (skill marks, Levels) go
through `state` inside the turn's transaction.
"""
from __future__ import annotations

import json
import sqlite3
from typing import Any

from pydantic import BaseModel, Field

from ..db import now_iso, one, rows
from .dice import DiceRoller, RollResult, record_roll
from .engine import ChangeError, resolve_character

STAT_ALIASES = {
    "str": "strength", "strength": "strength",
    "int": "intelligence", "intelligence": "intelligence",
    "con": "constitution", "constitution": "constitution",
    "dex": "dexterity", "dexterity": "dexterity",
    "cha": "charisma", "charisma": "charisma",
}
STAT_ABBR = {"strength": "STR", "intelligence": "INT", "constitution": "CON", "dexterity": "DEX", "charisma": "CHA"}


class Help(BaseModel):
    helper: str
    dice: str = "1d6"          # 1d6 if the helper has Ranks in the Skill, else 1d2 (BOOK 7.7)


class Modifier(BaseModel):
    source: str
    value: int = Field(ge=-50, le=50)


class CheckRequest(BaseModel):
    character: str
    skill: str | None = None            # omit for a Stat Check
    stat: str                           # governing Stat
    dc: int | None = Field(default=None, ge=1, le=100)
    dc_kind: str | None = None          # "stat" | "unopposed" | "opposed" when dc is omitted
    antagonist_mod: int = 0             # for opposed default Difficulty
    advantage: int = Field(default=0, ge=0, le=5)
    disadvantage: int = Field(default=0, ge=0, le=5)
    modifiers: list[Modifier] = Field(default_factory=list)
    help: list[Help] = Field(default_factory=list)
    passive: bool = False
    reason: str
    hidden: bool = False


def degree_of(natural: int, total: int, dc: int) -> str:
    if natural == 20:
        return "Critical Hit"
    if natural == 1:
        return "Critical Fail"
    margin = total - dc
    if margin >= 10:
        return "Amazing Success"
    if margin >= 0:
        return "Success"
    if margin >= -2:
        return "Near Miss"
    if margin >= -9:
        return "Fail"
    return "Major Fail"


def is_success(degree: str) -> bool:
    return degree in {"Critical Hit", "Amazing Success", "Success"}


def default_dc(kind: str, floor: int | None, antagonist_mod: int = 0) -> int:
    if floor is None:
        raise ChangeError("current floor is unknown, so a default Difficulty cannot be computed; pass dc explicitly")
    if kind == "stat":
        return 10 + floor
    if kind == "unopposed":
        return 10 + floor * 2
    if kind == "opposed":
        return 10 + antagonist_mod + floor
    raise ChangeError(f"unknown dc_kind {kind!r}; use stat, unopposed or opposed")


def _find_skill(state: sqlite3.Connection, character_id: int, name: str) -> dict | None:
    key = name.strip().lower()
    found = rows(state, """SELECT * FROM skills WHERE character_id = ? AND kind IN ('skill','equipment_skill','spell')
                           AND (lower(name) = ? OR lower(key) = ?) ORDER BY rank DESC""", (character_id, key, key.replace(" ", "-")))
    return found[0] if found else None


def perform_check(state: sqlite3.Connection, audit: sqlite3.Connection, roller: DiceRoller, campaign_id: int,
                  turn_id: int | None, req: CheckRequest) -> dict[str, Any]:
    char = resolve_character(state, campaign_id, req.character)
    stat = STAT_ALIASES.get(req.stat.strip().lower())
    if not stat:
        raise ChangeError(f"unknown stat {req.stat!r}")
    mods = (char.get("stats") or {}).get("modifiers") or {}
    if stat not in mods or mods[stat] is None:
        raise ChangeError(f"{char['name']} has no recorded {stat} modifier; the sheet must establish it first")
    stat_mod = int(mods[stat])
    camp = one(state, "SELECT current_floor FROM campaigns WHERE id = ?", (campaign_id,))
    if req.dc is not None:
        dc = req.dc
    else:
        dc = default_dc(req.dc_kind or ("unopposed" if req.skill else "stat"), camp["current_floor"], req.antagonist_mod)

    rank, untrained, skill_row = 0, False, None
    if req.skill:
        skill_row = _find_skill(state, char["id"], req.skill)
        if skill_row and (skill_row["rank"] or 0) > 0:
            rank = int(skill_row["rank"])
        else:
            if req.passive or (skill_row and skill_row["passive"]):
                raise ChangeError("Passive Skills and Spells cannot normally be attempted untrained (BOOK 7.5)")
            untrained = True
    elif req.help:
        raise ChangeError("Stat Checks cannot receive Help (BOOK 7.7)")

    adv = req.advantage
    dis = req.disadvantage + (1 if untrained else 0)
    net = adv - dis
    d20 = "2d20kh1" if net > 0 else "2d20kl1" if net < 0 else "1d20"
    extra = sum(m.value for m in req.modifiers)
    expr = f"{d20}+{rank}+{stat_mod}" + (f"{'+' if extra >= 0 else ''}{extra}" if extra else "")
    for h in req.help:
        d = h.dice.replace(" ", "").lower().lstrip("1")
        if d not in {"d6", "d2"}:
            raise ChangeError(f"Help adds 1d6 (helper has Ranks) or 1d2 (no Ranks); got {h.dice!r}")
    # Roll the core expression, then each helper's die separately so the breakdown can name helpers.
    result: RollResult = roller.roll(expr, target=dc, reason=req.reason)
    help_rolls = []
    for h in req.help:
        hr = roller.roll("1" + h.dice.replace(" ", "").lower().lstrip("1"))
        help_rolls.append({"helper": h.helper, "dice": h.dice, "value": hr.total})
        result.dice.extend(hr.dice)
        result.total += hr.total
    result.target = dc
    d20s = [d for d in result.dice if d.sides == 20]
    natural = next(d.value for d in d20s if d.kept)
    degree = degree_of(natural, result.total, dc)
    result.degree = degree
    result.outcome = "success" if is_success(degree) else "failure"
    result.detail = {
        "kind": "skill_check" if req.skill else "stat_check",
        "character": char["name"], "skill": req.skill, "rank": rank, "untrained": untrained,
        "stat": stat, "stat_mod": stat_mod, "modifiers": [m.model_dump() for m in req.modifiers],
        "help": help_rolls, "advantage": "advantage" if net > 0 else "disadvantage" if net < 0 else "none",
        "d20s": [d.value for d in d20s], "natural": natural, "dc": dc,
    }
    record_roll(audit, campaign_id, result, turn_id=turn_id, character_id=char["id"], hidden=req.hidden)

    marked = False
    if skill_row and not untrained and not (req.passive or skill_row["passive"]) and skill_row["kind"] == "skill" and not skill_row["marked"]:
        state.execute("UPDATE skills SET marked = 1 WHERE id = ?", (skill_row["id"],))
        marked = True
    summary = compact(result)
    return {"roll_id": result.id, "summary": summary, "total": result.total, "dc": dc, "natural": natural,
            "degree": degree, "success": is_success(degree), "skill_marked": marked, "hidden": req.hidden}


def compact(r: RollResult) -> str:
    """`Engineering: d20 17 + Rank 8 + INT 5 + 1 = 31 vs DC 18 → Amazing Success` (CORE_MECHANICS 6.3)."""
    d = r.detail
    if d.get("d20s") and len(d["d20s"]) > 1:
        die = f"d20 [{', '.join(map(str, d['d20s']))}] keep {d['natural']}"
    else:
        die = f"d20 {d.get('natural')}"
    parts = [die]
    if d.get("skill"):
        parts.append(f"Rank {d['rank']}" + (" (untrained)" if d["untrained"] else ""))
    parts.append(f"{STAT_ABBR.get(d.get('stat', ''), d.get('stat'))} {d.get('stat_mod'):+d}".replace("+-", "-"))
    for m in d.get("modifiers", []):
        parts.append(f"{m['value']:+d} {m['source']}")
    for h in d.get("help", []):
        parts.append(f"{h['helper']}'s Help {h['value']}")
    label = d.get("skill") or f"{STAT_ABBR.get(d.get('stat', ''), '')} Check"
    return f"{d.get('character')} — {label}: {' + '.join(parts)} = {r.total} vs DC {d.get('dc')} → {r.degree}"


# ------------------------------------------------------------------ advancement (CORE_MECHANICS 14.3 / 14.5)
ADVANCEMENT_CLOCK = "advancement_cycles"


def resolve_advancement_checkpoint(state: sqlite3.Connection, audit: sqlite3.Connection, roller: DiceRoller,
                                   campaign_id: int, turn_id: int | None, occasion: str) -> dict[str, Any]:
    """On twelve cycles: every eligible HUD-party crawler gains 1 Level; each marked Rank 0-4 Skill rolls
    1d20 vs current Rank (meet or exceed: +1 Rank); resolved marks clear; Rank 5+ marks stay banked for
    floor end; twelve cycles are removed and any excess carries forward."""
    clock = one(state, "SELECT * FROM clocks WHERE campaign_id = ? AND key = ?", (campaign_id, ADVANCEMENT_CLOCK))
    if not clock:
        raise ChangeError("this campaign has no advancement_cycles clock")
    required = clock["maximum"] or 12
    if clock["current"] < required:
        raise ChangeError(f"only {clock['current']}/{required} cycles; the checkpoint is not due")
    camp = one(state, "SELECT current_floor FROM campaigns WHERE id = ?", (campaign_id,))
    party = rows(state, """SELECT * FROM characters WHERE campaign_id = ? AND in_party = 1 AND kind = 'crawler'
                           AND status = 'active' ORDER BY party_order""", (campaign_id,))
    lines: list[str] = []
    for c in party:
        before = c["level"]
        if before is None:
            lines.append(f"{c['name']}: Level unknown — Level award needs manual review")
        else:
            derived = c.get("derived") or {}
            if (camp["current_floor"] or 0) >= 3:
                derived["unallocated_stat_points"] = (derived.get("unallocated_stat_points") or 0) + 3
            state.execute("UPDATE characters SET level = ?, derived_json = ? WHERE id = ?", (before + 1, json.dumps(derived), c["id"]))
            lines.append(f"{c['name']}: Level {before} → {before + 1}")
        for s in rows(state, "SELECT * FROM skills WHERE character_id = ? AND marked = 1 ORDER BY name", (c["id"],)):
            r = s["rank"] or 0
            if r >= 5:
                continue  # floor-end timing
            res = roller.roll("1d20", target=r, reason=f"{s['name']} advancement (Rank {r})")
            res.detail = {"kind": "skill_advancement", "character": c["name"], "skill": s["name"], "rank": r}
            res.degree = "Success" if res.outcome == "success" else "Fail"
            record_roll(audit, campaign_id, res, turn_id=turn_id, character_id=c["id"])
            new = r + 1 if res.outcome == "success" else r
            state.execute("UPDATE skills SET rank = ?, marked = 0 WHERE id = ?", (new, s["id"]))
            lines.append(f"  {s['name']}: d20 {res.total} vs Rank {r} → " + (f"{r} → {new}" if new != r else "no change"))
    state.execute("UPDATE clocks SET current = current - ? WHERE id = ?", (required, clock["id"]))
    summary = f"Advancement checkpoint resolved ({occasion})"
    state.execute(
        """INSERT INTO events (campaign_id, turn_id, type, summary, payload_json, visibility, game_time_minutes, created_at)
           VALUES (?,?,?,?,?,?,(SELECT game_time_minutes FROM campaigns WHERE id = ?),?)""",
        (campaign_id, turn_id, "advancement_checkpoint", summary, json.dumps({"lines": lines}), "player", campaign_id, now_iso()),
    )
    lines.append("Race/Class level-up benefits (e.g. Human AI Favor) and Stat allocation still need GM/player handling.")
    return {"summary": summary, "lines": lines, "cycles_remaining": clock["current"] - required}
