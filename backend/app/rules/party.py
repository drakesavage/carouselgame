"""Party Skill Checks (CORE_MECHANICS 7.8; Core Rulebook p. 61, Rules for Survival p. 18) and off-screen group
resolution (CORE_MECHANICS 15.2).

A Party Skill Check: every participant rolls the Skill against the same Difficulty; the party succeeds when half or
more succeed. Participants cannot Help one another. Each Amazing Success or Critical Hit turns one teammate's failed
Check into a Success; each Major Fail or Critical Fail turns another teammate's Success into a Failure.
INTERPRETATION: both kinds of conversion are applied at once, against the original results, so neither cancels the
other's target; an upgrade goes to the failure that missed by least, a downgrade to the success that made it by least
(never the roller's own Check).

Off-screen resolution: the members of a party group the player character is not in, resolved with the same Check. The
objective and the stakes of Failure are declared before the roll (no guaranteed success because Cliff is elsewhere,
no arbitrary catastrophe), the rolls are secret by default (Cliff learns the outcome when the group reports), and the
outcome is recorded on the event log. Consequences are then applied with the ordinary validated ops.
"""
from __future__ import annotations

import json
import math
import sqlite3
from typing import Any

from pydantic import BaseModel, Field

from ..db import now_iso, one, rows
from .checks import CheckRequest, Modifier, default_dc, perform_check
from .dice import DiceRoller
from .engine import ChangeError, resolve_character

UP = {"Amazing Success", "Critical Hit"}
DOWN = {"Major Fail", "Critical Fail"}


class Member(BaseModel):
    character: str
    skill: str | None = None            # a member may use a different fitting Skill (e.g. Hide in Shadows for one, Stealth for another)
    stat: str | None = None
    advantage: int = Field(default=0, ge=0, le=5)
    disadvantage: int = Field(default=0, ge=0, le=5)
    modifiers: list[Modifier] = Field(default_factory=list)


class PartyCheckRequest(BaseModel):
    skill: str
    stat: str
    participants: list[str] = Field(default_factory=list)   # names; or give `group`
    group: str | None = None            # a party group key: its members take part
    members: list[Member] = Field(default_factory=list)      # per-member overrides
    dc: int | None = Field(default=None, ge=1, le=100)
    dc_kind: str | None = None          # unopposed (default) | opposed | stat
    antagonist_mod: int = 0
    modifiers: list[Modifier] = Field(default_factory=list)  # apply to everyone (cover, darkness, gear)
    reason: str
    hidden: bool | None = None          # default: secret for off-screen groups, shown otherwise
    # off-screen resolution (15.2)
    offscreen: bool = False
    objective: str | None = None
    stakes: str | None = None           # what Failure means, declared before the roll
    on_success: str | None = None


def _participants(conn: sqlite3.Connection, cid: int, req: PartyCheckRequest) -> tuple[list[dict], dict | None]:
    group = None
    if req.group:
        group = one(conn, "SELECT * FROM party_groups WHERE campaign_id = ? AND (key = ? OR lower(label) = lower(?)) AND status = 'active'",
                    (cid, req.group, req.group))
        if not group:
            raise ChangeError(f"no active party group {req.group!r}")
        chars = rows(conn, """SELECT * FROM characters WHERE campaign_id = ? AND assignment = ? AND status NOT IN ('deceased','dead')
                              ORDER BY party_order, id""", (cid, group["key"]))
        if req.participants:
            wanted = {resolve_character(conn, cid, p)["id"] for p in req.participants}
            outsiders = wanted - {c["id"] for c in chars}
            if outsiders:
                raise ChangeError(f"only members of {group['label']} can take part in its Check")
            chars = [c for c in chars if c["id"] in wanted]
    else:
        chars = [resolve_character(conn, cid, p) for p in req.participants]
    ids = [c["id"] for c in chars]
    if len(set(ids)) != len(ids):
        raise ChangeError("each participant rolls once")
    if len(chars) < 2:
        raise ChangeError("a Party Skill Check needs at least two participants; one character handling the task makes an ordinary skill_check (7.8)")
    return chars, group


def tally(results: list[dict]) -> tuple[int, int, bool]:
    """Apply Amazing/Critical upgrades and Major/Critical downgrades against the original results (see the module
    docstring), set each result's `converted` and `final`, and return (successes, needed, party success)."""
    for x in results:
        x["converted"] = None
    ups = [x for x in results if x["degree"] in UP]
    downs = [x for x in results if x["degree"] in DOWN]
    failed = sorted((x for x in results if not x["success"]), key=lambda x: -x["margin"])
    for src, tgt in zip(ups, failed):
        tgt["converted"] = f"Success (raised by {src['character']}'s {src['degree']})"
    passed = sorted((x for x in results if x["success"]), key=lambda x: x["margin"])
    for src in downs:
        tgt = next((x for x in passed if x["converted"] is None and x["id"] != src["id"]), None)
        if tgt:
            tgt["converted"] = f"Failure (dragged down by {src['character']}'s {src['degree']})"
    for x in results:
        x["final"] = x["converted"].startswith("Success") if x["converted"] else x["success"]
    succeeded = sum(x["final"] for x in results)
    need = math.ceil(len(results) / 2)
    return succeeded, need, succeeded >= need


def party_check(conn: sqlite3.Connection, roller: DiceRoller, cid: int, turn_id: int | None, req: PartyCheckRequest) -> dict[str, Any]:
    chars, group = _participants(conn, cid, req)
    camp = one(conn, "SELECT current_floor, active_character_id, game_time_minutes FROM campaigns WHERE id = ?", (cid,))
    if req.offscreen:
        if not group:
            raise ChangeError("off-screen resolution is for a party group: give `group`")
        if camp["active_character_id"] in {c["id"] for c in chars}:
            raise ChangeError(f"{group['label']} includes the player character, so it is on-screen: the player decides what they do")
        if not (req.objective or "").strip() or not (req.stakes or "").strip():
            raise ChangeError("declare the group's objective and the stakes of Failure before rolling (15.2)")
    hidden = req.offscreen if req.hidden is None else req.hidden
    dc = req.dc if req.dc is not None else default_dc(req.dc_kind or "unopposed", camp["current_floor"], req.antagonist_mod)
    overrides = {resolve_character(conn, cid, m.character)["id"]: m for m in req.members}

    results = []
    for c in chars:
        m = overrides.get(c["id"])
        r = perform_check(conn, conn, roller, cid, turn_id, CheckRequest(
            character=str(c["id"]), skill=(m and m.skill) or req.skill, stat=(m and m.stat) or req.stat, dc=dc,
            advantage=m.advantage if m else 0, disadvantage=m.disadvantage if m else 0,
            modifiers=list(req.modifiers) + (list(m.modifiers) if m else []), reason=f"{req.reason} (party check: {c['name']})",
            hidden=hidden))
        results.append({"character": c["name"], "id": c["id"], "degree": r["degree"], "total": r["total"], "success": r["success"],
                        "margin": r["total"] - dc, "summary": r["summary"], "roll_id": r["roll_id"], "converted": None})

    succeeded, need, ok = tally(results)

    lines = [f"{x['summary']}" + (f" → {x['converted']}" if x["converted"] else "") for x in results]
    verdict = f"Party {'Success' if ok else 'Failure'}: {succeeded}/{len(results)} succeeded (needed {need}) vs Difficulty {dc}"
    lines.append(verdict)
    out = {"success": ok, "succeeded": succeeded, "needed": need, "participants": len(results), "dc": dc, "results": results,
           "hidden": hidden, "lines": lines, "summary": verdict}
    if req.offscreen:
        outcome = (req.on_success or "the objective is achieved") if ok else req.stakes
        payload = {"group": group["key"], "objective": req.objective, "stakes": req.stakes, "success": ok, "succeeded": succeeded,
                   "needed": need, "dc": dc, "outcome": outcome, "roll_ids": [x["roll_id"] for x in results]}
        conn.execute("""INSERT INTO events (campaign_id, turn_id, type, summary, payload_json, visibility, game_time_minutes, created_at)
                        VALUES (?,?,?,?,?,?,?,?)""",
                     (cid, turn_id, "offscreen_resolved",
                      f"{group['label']} (off-screen): {req.objective} — {'Success' if ok else 'Failure'}: {outcome}"[:500],
                      json.dumps(payload, ensure_ascii=False), "gm" if hidden else "player", camp["game_time_minutes"], now_iso()))
        out["outcome"] = outcome
        lines.append(f"Off-screen outcome for {group['label']}: {outcome}. Apply its consequences with apply_changes; "
                     + ("the player learns it when the group reports." if hidden else "the player sees it now."))
    return out
