"""Validated state ops added in schema v2 (registered into engine.CHANGE_OPS at import).

Training and awards (CORE_MECHANICS §14): skill ranks/new skills, Level awards, AI Favor.
Party organization (§15): groups with a location and activity; only a group's own members move with it.
Scene and memory (§3.2, §16): the active scene, episodic/NPC memories.
Limited uses (§11, race/class features): once per scene/day/floor/rest or cooldown minutes.
Rest (§11.4): short/long/full-day in a saferoom, healing by Health Bar slots.
"""
from __future__ import annotations

import json
import re
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from ..db import now_iso, one, rows
from .engine import ChangeError, Ctx, resolve_character

SKILL_CAP_FLOORS_1_TO_5 = 15   # BOOK 14.5: normal advancement caps at Rank 15 on Floors 1-5


def _slug(s: str) -> str:
    return "".join(ch if ch.isalnum() else "-" for ch in s.lower()).strip("-")


def _floor(ctx: Ctx) -> int | None:
    return one(ctx.conn, "SELECT current_floor FROM campaigns WHERE id = ?", (ctx.campaign_id,))["current_floor"]


# ------------------------------------------------------------------ training & awards
class SkillChange(BaseModel):
    op: Literal["skill"]
    character: str
    name: str = Field(min_length=1, max_length=80)
    kind: Literal["skill", "spell"] = "skill"
    rank: int | None = Field(default=None, ge=0, le=30)        # set to this Rank
    delta: int | None = Field(default=None, ge=-5, le=10)      # or change by this much
    passive: bool | None = None
    mana_cost: int | None = Field(default=None, ge=0, le=500)
    source: Literal["training", "book", "class", "race", "item", "grind", "advancement", "correction", "other"]
    reason: str = Field(min_length=3)
    cap_override: str | None = None                            # cite the rule that allows > Rank 15 (e.g. a Class benefit)


def _apply_skill(ctx: Ctx, op: SkillChange) -> dict:
    if (op.rank is None) == (op.delta is None):
        raise ChangeError("give exactly one of rank or delta")
    c = resolve_character(ctx.conn, ctx.campaign_id, op.character)
    row = one(ctx.conn, "SELECT * FROM skills WHERE character_id = ? AND kind = ? AND (lower(name) = lower(?) OR key = ?)",
              (c["id"], op.kind, op.name, _slug(op.name)))
    before = (row["rank"] or 0) if row else 0
    after = op.rank if op.rank is not None else before + op.delta
    if after < 0:
        raise ChangeError("a Rank cannot go below 0")
    if after < before and op.source != "correction":
        raise ChangeError("lowering a Rank needs source 'correction'")
    floor = _floor(ctx)
    if floor is not None and floor <= 5 and after > SKILL_CAP_FLOORS_1_TO_5 and not op.cap_override:
        raise ChangeError(f"Rank {after} exceeds the Rank {SKILL_CAP_FLOORS_1_TO_5} cap on Floors 1-5 (BOOK 14.5); cite cap_override if a Class/Race allows it")
    props = {"provenance": f"{op.source}: {op.reason}"}
    if row:
        merged = {**(row.get("properties") or {}), "last_change": props["provenance"]}
        ctx.conn.execute("UPDATE skills SET rank = ?, passive = COALESCE(?, passive), mana_cost = COALESCE(?, mana_cost), properties_json = ? WHERE id = ?",
                         (after, None if op.passive is None else int(op.passive), op.mana_cost, json.dumps(merged), row["id"]))
        summary = f"{c['name']}: {row['name']} Rank {before} → {after} ({op.source})"
    else:
        ctx.conn.execute("INSERT INTO skills (character_id, kind, key, name, rank, mana_cost, passive, properties_json) VALUES (?,?,?,?,?,?,?,?)",
                         (c["id"], op.kind, _slug(op.name), op.name, after, op.mana_cost, int(bool(op.passive)), json.dumps(props)))
        summary = f"{c['name']} learns {op.kind} {op.name} at Rank {after} ({op.source})"
    ctx.event("skill_changed", summary, {"character_id": c["id"], "skill": op.name, "before": before, "after": after, "source": op.source})
    return {"summary": summary}


class LevelAward(BaseModel):
    op: Literal["level"]
    character: str
    delta: int = Field(ge=1, le=6)
    source: Literal["quest", "boss", "grind", "crawler_kill", "correction", "other"]
    reason: str = Field(min_length=3)


def _apply_level(ctx: Ctx, op: LevelAward) -> dict:
    c = resolve_character(ctx.conn, ctx.campaign_id, op.character)
    if c["level"] is None:
        raise ChangeError(f"{c['name']} has no recorded Level")
    derived = c.get("derived") or {}
    floor = _floor(ctx) or 0
    if floor >= 3:
        derived["unallocated_stat_points"] = (derived.get("unallocated_stat_points") or 0) + 3 * op.delta
    lp = derived.get("level_progress")
    if isinstance(lp, dict) and "current" in lp:
        lp["current"] = 0   # PLAY_PROTOCOL: a Level from any source resets running Level-grind progress
    ctx.conn.execute("UPDATE characters SET level = ?, derived_json = ? WHERE id = ?", (c["level"] + op.delta, json.dumps(derived), c["id"]))
    summary = f"{c['name']} Level {c['level']} → {c['level'] + op.delta} ({op.source}: {op.reason})"
    if floor >= 3:
        summary += f"; +{3 * op.delta} Stat points banked"
    ctx.event("level_awarded", summary, {"character_id": c["id"], "delta": op.delta, "source": op.source})
    return {"summary": summary, "note": "Apply Race/Class level-up benefits (e.g. Human +1 AI Favor) with their own ops."}


class AiFavor(BaseModel):
    op: Literal["ai_favor"]
    character: str
    delta: int = Field(ge=-10, le=10)
    reason: str = Field(min_length=3)


def _apply_ai_favor(ctx: Ctx, op: AiFavor) -> dict:
    c = resolve_character(ctx.conn, ctx.campaign_id, op.character)
    before = c["ai_favor"] or 0
    if before + op.delta < 0:
        raise ChangeError(f"{c['name']} has only {before} AI Favor")
    ctx.conn.execute("UPDATE characters SET ai_favor = ? WHERE id = ?", (before + op.delta, c["id"]))
    summary = f"{c['name']} AI Favor {before} → {before + op.delta} — {op.reason}"
    ctx.event("ai_favor_changed", summary, {"character_id": c["id"], "before": before, "after": before + op.delta})
    return {"summary": summary}


# ------------------------------------------------------------------ party organization
# ------------------------------------------------------------------ one location model (playtest 1, H4/H5)
# The campaign's location is where the player character's group is; a group's location is where its members are.
def _place(text: str | None) -> str:
    return re.split(r"[,—]", text or "")[0].strip().lower()


def pc_group(ctx: Ctx) -> dict | None:
    camp = one(ctx.conn, "SELECT active_character_id FROM campaigns WHERE id = ?", (ctx.campaign_id,))
    pc = one(ctx.conn, "SELECT assignment FROM characters WHERE id = ?", (camp["active_character_id"],)) if camp and camp["active_character_id"] else None
    if not pc or not pc["assignment"]:
        return None
    return one(ctx.conn, "SELECT * FROM party_groups WHERE campaign_id = ? AND key = ? AND status = 'active'", (ctx.campaign_id, pc["assignment"]))


def known_saferoom(ctx: Ctx, location: str) -> bool:
    """A place is a saferoom only if it was recorded as one: the campaign is there in a saferoom now, set_location marked
    it (meta saferooms:<cid>), a party group named for the saferoom is stationed there, or a past location change said so."""
    camp = one(ctx.conn, "SELECT current_location, safe_room FROM campaigns WHERE id = ?", (ctx.campaign_id,))
    if camp["safe_room"] and _place(camp["current_location"]) == _place(location):
        return True
    m = one(ctx.conn, "SELECT value FROM meta WHERE key = ?", (f"saferooms:{ctx.campaign_id}",))
    if m and _place(location) in json.loads(m["value"]):
        return True
    for g in rows(ctx.conn, "SELECT key, label, location FROM party_groups WHERE campaign_id = ? AND status = 'active'", (ctx.campaign_id,)):
        if "saferoom" in f"{g['key']} {g['label']}".lower() and _place(g["location"]) == _place(location):
            return True
    for e in rows(ctx.conn, "SELECT payload_json FROM events WHERE campaign_id = ? AND type = 'location_changed' AND rolled_back = 0 ORDER BY id DESC", (ctx.campaign_id,)):
        p = e.get("payload") or {}
        if _place(p.get("location")) == _place(location) and p.get("safe_room") is not None:
            return bool(p["safe_room"])
    return False


def remember_saferoom(ctx: Ctx, location: str) -> None:
    key = f"saferooms:{ctx.campaign_id}"
    m = one(ctx.conn, "SELECT value FROM meta WHERE key = ?", (key,))
    places = set(json.loads(m["value"])) if m else set()
    if _place(location) not in places:
        places.add(_place(location))
        ctx.conn.execute("INSERT INTO meta(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, json.dumps(sorted(places))))


def campaign_follows_group(ctx: Ctx, g: dict, location: str | None) -> None:
    """The player character's group moved: the campaign's location (and saferoom status) moves with it."""
    pg = pc_group(ctx)
    if not pg or pg["key"] != g["key"] or not location:
        return
    camp = one(ctx.conn, "SELECT current_location FROM campaigns WHERE id = ?", (ctx.campaign_id,))
    if camp["current_location"] == location:
        return
    safe = known_saferoom(ctx, location)
    ctx.conn.execute("UPDATE campaigns SET current_location = ?, safe_room = ?, updated_at = ? WHERE id = ?", (location, int(safe), now_iso(), ctx.campaign_id))
    ctx.event("location_changed", f"Party moves to {location} (with {g['label']})" + ("" if safe else " — not a saferoom"),
              {"location": location, "safe_room": None, "followed_group": g["key"]})


class GroupUpsert(BaseModel):
    op: Literal["group"]
    key: str = Field(min_length=1, max_length=60)
    label: str | None = None
    location: str | None = None
    activity: str | None = None
    members: list[str] | None = None          # replaces the membership when given
    status: Literal["active", "disbanded"] | None = None


def _apply_group(ctx: Ctx, op: GroupUpsert) -> dict:
    g = one(ctx.conn, "SELECT * FROM party_groups WHERE campaign_id = ? AND key = ?", (ctx.campaign_id, op.key))
    if not g:
        ctx.conn.execute("INSERT INTO party_groups (campaign_id, key, label, location, activity, status, source) VALUES (?,?,?,?,?,?,?)",
                         (ctx.campaign_id, op.key, op.label or op.key.replace("-", " ").title(), op.location, op.activity, op.status or "active", "play"))
    else:
        ctx.conn.execute("UPDATE party_groups SET label = COALESCE(?, label), location = COALESCE(?, location), activity = COALESCE(?, activity), "
                         "status = COALESCE(?, status) WHERE id = ?", (op.label, op.location, op.activity, op.status, g["id"]))
    g = one(ctx.conn, "SELECT * FROM party_groups WHERE campaign_id = ? AND key = ?", (ctx.campaign_id, op.key))
    if op.location:
        ctx.conn.execute("UPDATE characters SET location = ? WHERE campaign_id = ? AND assignment = ?", (g["location"], ctx.campaign_id, op.key))
        campaign_follows_group(ctx, g, g["location"])
    names = []
    if op.members is not None:
        ctx.conn.execute("UPDATE characters SET assignment = NULL WHERE campaign_id = ? AND assignment = ?", (ctx.campaign_id, op.key))
        for m in op.members:
            ch = resolve_character(ctx.conn, ctx.campaign_id, m)
            # a member is where the group is; a group with no recorded position leaves its members' position unknown too
            ctx.conn.execute("UPDATE characters SET assignment = ?, location = ? WHERE id = ?", (op.key, g["location"], ch["id"]))
            names.append(ch["name"])
    summary = f"Group {g['label']}" + (f" @ {g['location']}" if g["location"] else "") + (f": {g['activity']}" if g["activity"] else "") + \
        (f" — members: {', '.join(names)}" if names else "") + (" (disbanded)" if g["status"] == "disbanded" else "")
    ctx.event("group_updated", summary, {"key": op.key, "members": names})
    return {"summary": summary}


class MoveGroup(BaseModel):
    op: Literal["move_group"]
    key: str
    destination: str = Field(min_length=1)
    activity: str | None = None
    reason: str = Field(min_length=3)


def _apply_move_group(ctx: Ctx, op: MoveGroup) -> dict:
    g = one(ctx.conn, "SELECT * FROM party_groups WHERE campaign_id = ? AND key = ?", (ctx.campaign_id, op.key))
    if not g:
        raise ChangeError(f"no group {op.key!r}; create it with the group op")
    members = rows(ctx.conn, "SELECT id, name FROM characters WHERE campaign_id = ? AND assignment = ?", (ctx.campaign_id, op.key))
    if not members:
        raise ChangeError(f"group {op.key!r} has no members")
    ctx.conn.execute("UPDATE party_groups SET location = ?, activity = COALESCE(?, activity) WHERE id = ?", (op.destination, op.activity, g["id"]))
    ctx.conn.execute("UPDATE characters SET location = ? WHERE campaign_id = ? AND assignment = ?", (op.destination, ctx.campaign_id, op.key))
    summary = f"{g['label']} ({', '.join(m['name'].split(' ')[0] for m in members)}) → {op.destination} — {op.reason}"
    ctx.event("group_moved", summary, {"key": op.key, "from": g["location"], "to": op.destination})
    campaign_follows_group(ctx, g, op.destination)
    return {"summary": summary}


class MoveCharacter(BaseModel):
    op: Literal["move_character"]
    character: str
    destination: str = Field(min_length=1)
    group: str | None = None                  # join this group (its location wins if it has one)
    reason: str = Field(min_length=3)


def _apply_move_character(ctx: Ctx, op: MoveCharacter) -> dict:
    c = resolve_character(ctx.conn, ctx.campaign_id, op.character)
    dest = op.destination
    if op.group:
        g = one(ctx.conn, "SELECT * FROM party_groups WHERE campaign_id = ? AND key = ?", (ctx.campaign_id, op.group))
        if not g:
            raise ChangeError(f"no group {op.group!r}")
        dest = g["location"] or dest
    ctx.conn.execute("UPDATE characters SET location = ?, assignment = COALESCE(?, assignment) WHERE id = ?", (dest, op.group, c["id"]))
    summary = f"{c['name']}: {c['location'] or 'unknown'} → {dest}" + (f" (joins {op.group})" if op.group else "") + f" — {op.reason}"
    ctx.event("character_moved", summary, {"character_id": c["id"], "from": c["location"], "to": dest})
    return {"summary": summary}


# ------------------------------------------------------------------ scene & memory
SCENE_ALIASES = {"end": "close", "finish": "close", "stop": "close", "begin": "start", "open": "start", "new": "start",
                 "set": "update", "change": "update", "edit": "update"}


class SceneChange(BaseModel):
    op: Literal["scene"]
    action: Literal["start", "update", "close"]

    @field_validator("action", mode="before")
    @classmethod
    def _alias(cls, v):  # the GM kept sending "end" and "set" (playtest 1, M4)
        return SCENE_ALIASES.get(str(v).lower(), v)
    title: str | None = None
    location: str | None = None
    group: str | None = None
    situation: str | None = None
    conditions: str | None = None
    threats: str | None = None
    participants: list[str] | None = None
    summary: str | None = None                # required on close: becomes episodic memory


def _apply_scene(ctx: Ctx, op: SceneChange) -> dict:
    cur = one(ctx.conn, "SELECT * FROM scenes WHERE campaign_id = ? AND status = 'active' ORDER BY id DESC LIMIT 1", (ctx.campaign_id,))
    now = ctx.game_time()
    if op.action == "start":
        if not op.title:
            raise ChangeError("a new scene needs a title")
        if cur:
            if not op.summary:
                raise ChangeError("close the current scene with a summary first (or pass summary to close it automatically)")
            _close(ctx, cur, op.summary, now)
        ctx.conn.execute("""INSERT INTO scenes (campaign_id, title, location, group_key, situation, conditions, threats, participants_json,
                                                status, started_game_minutes, source, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                         (ctx.campaign_id, op.title, op.location, op.group, op.situation, op.conditions, op.threats, json.dumps(op.participants or []),
                          "active", now, "play", now_iso()))
        ctx.conn.execute("UPDATE ability_uses SET uses = 0 WHERE campaign_id = ? AND per = 'scene'", (ctx.campaign_id,))
        summary = f"Scene: {op.title}" + (f" @ {op.location}" if op.location else "")
        ctx.event("scene_started", summary, {"title": op.title})
        return {"summary": summary}
    if not cur:
        raise ChangeError("there is no active scene")
    if op.action == "close":
        if not op.summary:
            raise ChangeError("closing a scene needs a summary (it becomes episodic memory)")
        _close(ctx, cur, op.summary, now)
        return {"summary": f"Scene closed: {cur['title']}"}
    fields = {k: v for k, v in {"title": op.title, "location": op.location, "group_key": op.group, "situation": op.situation,
                                "conditions": op.conditions, "threats": op.threats}.items() if v is not None}
    if op.participants is not None:
        fields["participants_json"] = json.dumps(op.participants)
    if not fields:
        raise ChangeError("scene update has no changes")
    ctx.conn.execute(f"UPDATE scenes SET {', '.join(f'{k} = ?' for k in fields)} WHERE id = ?", (*fields.values(), cur["id"]))
    return {"summary": f"Scene updated: {op.title or cur['title']}"}


def _close(ctx: Ctx, cur: dict, summary: str, now: int) -> None:
    ctx.conn.execute("UPDATE scenes SET status = 'closed', summary = ?, ended_game_minutes = ? WHERE id = ?", (summary, now, cur["id"]))
    ctx.conn.execute("INSERT INTO memories (campaign_id, layer, subject, content, visibility, importance, turn_id, game_time_minutes, created_at) "
                     "VALUES (?,?,?,?,?,?,?,?,?)", (ctx.campaign_id, "episodic", cur["title"], summary, "player", 2, ctx.turn_id, now, now_iso()))
    ctx.conn.execute("UPDATE ability_uses SET uses = 0 WHERE campaign_id = ? AND per = 'scene'", (ctx.campaign_id,))
    ctx.event("scene_closed", f"Scene ended: {cur['title']}", {"scene_id": cur["id"]})


class Remember(BaseModel):
    op: Literal["remember"]
    subject: str = Field(min_length=1)        # a character (name/slug/id) or a topic
    content: str = Field(min_length=3, max_length=2000)
    visibility: Literal["player", "gm"] = "player"   # gm = private beat / hidden reaction
    importance: int = Field(default=2, ge=1, le=3)

    @field_validator("importance", mode="before")
    @classmethod
    def _clamp(cls, v):  # out-of-range importance (not a gameplay value) once rejected a whole turn (playtest 1, M5)
        try:
            return min(3, max(1, int(v)))
        except (TypeError, ValueError):
            return 2


def _apply_remember(ctx: Ctx, op: Remember) -> dict:
    try:
        ch = resolve_character(ctx.conn, ctx.campaign_id, op.subject)
    except ChangeError:
        ch = None
    ctx.conn.execute("INSERT INTO memories (campaign_id, layer, subject, character_id, content, visibility, importance, turn_id, game_time_minutes, created_at) "
                     "VALUES (?,?,?,?,?,?,?,?,?,?)",
                     (ctx.campaign_id, "npc" if ch else "episodic", ch["slug"] if ch else op.subject, ch["id"] if ch else None, op.content,
                      op.visibility, op.importance, ctx.turn_id, ctx.game_time(), now_iso()))
    who = ch["name"] if ch else op.subject
    ctx.event("memory_recorded", f"Memory recorded about {who}", {"subject": who}, visibility=op.visibility)
    return {"summary": f"Remembered about {who}", "visibility": op.visibility}


# ------------------------------------------------------------------ limited uses
class UseAbility(BaseModel):
    op: Literal["use_ability"]
    character: str
    ability: str = Field(min_length=1, max_length=80)
    per: Literal["scene", "day", "floor", "short_rest", "long_rest", "minutes"]
    uses_allowed: int = Field(default=1, ge=1, le=20)
    cooldown_minutes: int | None = Field(default=None, ge=1)


def _apply_use_ability(ctx: Ctx, op: UseAbility) -> dict:
    c = resolve_character(ctx.conn, ctx.campaign_id, op.character)
    now = ctx.game_time()
    row = one(ctx.conn, "SELECT * FROM ability_uses WHERE character_id = ? AND lower(ability) = lower(?)", (c["id"], op.ability))
    if op.per == "minutes" and not op.cooldown_minutes:
        raise ChangeError("per 'minutes' needs cooldown_minutes")
    if row:
        uses = row["uses"]
        if row["per"] == "minutes" and row["last_used_minutes"] is not None and now - row["last_used_minutes"] >= (row["cooldown_minutes"] or 0):
            uses = 0
        if uses >= row["uses_allowed"]:
            wait = ""
            if row["per"] == "minutes":
                wait = f"; ready in {row['cooldown_minutes'] - (now - row['last_used_minutes'])} min"
            raise ChangeError(f"{c['name']} has already used {row['ability']} ({uses}/{row['uses_allowed']} per {row['per']}){wait}")
        ctx.conn.execute("UPDATE ability_uses SET uses = ?, last_used_minutes = ? WHERE id = ?", (uses + 1, now, row["id"]))
        left = row["uses_allowed"] - uses - 1
    else:
        ctx.conn.execute("INSERT INTO ability_uses (campaign_id, character_id, ability, per, uses_allowed, uses, cooldown_minutes, last_used_minutes) VALUES (?,?,?,?,?,?,?,?)",
                         (ctx.campaign_id, c["id"], op.ability, op.per, op.uses_allowed, 1, op.cooldown_minutes, now))
        left = op.uses_allowed - 1
    summary = f"{c['name']} uses {op.ability} ({left} left this {op.per.replace('_', ' ')})"
    ctx.event("ability_used", summary, {"character_id": c["id"], "ability": op.ability})
    return {"summary": summary}


def reset_uses(ctx: Ctx, per: tuple[str, ...]) -> None:
    marks = ",".join("?" * len(per))
    ctx.conn.execute(f"UPDATE ability_uses SET uses = 0 WHERE campaign_id = ? AND per IN ({marks})", (ctx.campaign_id, *per))


# ------------------------------------------------------------------ rest (BOOK 11.4) and mending
class Rest(BaseModel):
    op: Literal["rest"]
    kind: Literal["short", "long", "full_day"]
    characters: list[str] | None = None       # default: the active scene's participants, else active in-party crawlers
    reason: str = Field(min_length=3)


REST_MINUTES = {"short": 120, "long": 480, "full_day": 1800}


def _rest_targets(ctx: Ctx, refs: list[str] | None) -> list[dict]:
    if refs:
        return [resolve_character(ctx.conn, ctx.campaign_id, r) for r in refs]
    return rows(ctx.conn, "SELECT * FROM characters WHERE campaign_id = ? AND in_party = 1 AND kind = 'crawler' AND status = 'active'", (ctx.campaign_id,))


def _apply_rest(ctx: Ctx, op: Rest) -> dict:
    from .combat import heal_character_slots
    from .engine import AdvanceTime, _apply_advance_time
    camp = one(ctx.conn, "SELECT safe_room FROM campaigns WHERE id = ?", (ctx.campaign_id,))
    if not camp["safe_room"]:
        raise ChangeError("Rest must occur in a saferoom with no strenuous work (BOOK 11.4); the party is not in one")
    lines = []
    for c in _rest_targets(ctx, op.characters):
        if c["hp_max"] is not None:
            slots = 5 if op.kind == "short" else 1000
            try:
                lines.append(heal_character_slots(ctx.conn, ctx.campaign_id, ctx.turn_id, c, slots, f"{op.kind} rest"))
            except ChangeError as exc:
                lines.append(f"{c['name']}: {exc}")
        if c["mana_max"] is not None:
            m = one(ctx.conn, "SELECT mana_current FROM characters WHERE id = ?", (c["id"],))["mana_current"] or 0
            new = min(c["mana_max"], m + c["mana_max"] // 2) if op.kind == "short" else c["mana_max"]
            ctx.conn.execute("UPDATE characters SET mana_current = ? WHERE id = ?", (new, c["id"]))
        removed = ["Minor Injury"] if op.kind == "short" else ["Minor Injury", "Major Injury", "Fatigued"]
        for name in removed:
            if ctx.conn.execute("DELETE FROM status_effects WHERE character_id = ? AND name = ?", (c["id"], name)).rowcount:
                lines.append(f"{c['name']}: {name} removed")
    reset_uses(ctx, ("short_rest",) if op.kind == "short" else ("short_rest", "long_rest"))
    # the rest is the clock for its own hours: if this turn already advanced time for the rest, do not count it twice
    covered = rest_time_already_advanced(ctx, REST_MINUTES[op.kind])
    t = {"summary": f"the clock already advanced {covered} minutes for this rest this turn"} if covered else         _apply_advance_time(ctx, AdvanceTime(op="advance_time", minutes=REST_MINUTES[op.kind], reason=f"{op.kind.replace('_', ' ')} rest — {op.reason}"))
    summary = f"{op.kind.replace('_', ' ').title()} rest ({REST_MINUTES[op.kind] // 60}h)"
    ctx.event("rest", summary, {"kind": op.kind, "lines": lines})
    return {"summary": summary, "lines": lines, "time": t["summary"]}


REST_WORDS = re.compile(r"\b(rest|rests|resting|sleep|sleeps|sleeping|overnight)\b", re.I)


def rest_time_already_advanced(ctx: Ctx, minutes: int) -> int:
    """Minutes this turn's advance_time calls already spent on a rest (reason mentions rest/sleep), when they cover it."""
    if ctx.turn_id is None:
        return 0
    spent = 0
    for e in rows(ctx.conn, "SELECT summary, payload_json FROM events WHERE campaign_id = ? AND turn_id = ? AND type = 'time_advanced' AND rolled_back = 0",
                  (ctx.campaign_id, ctx.turn_id)):
        p = e.get("payload") or {}
        reason = re.sub(r"^\d+ minutes pass\W+", "", e["summary"]).lower()
        if REST_WORDS.search(reason) and not reason.startswith(("short rest", "long rest", "full day rest")):  # not the rest op's own advance
            spent += int(p.get("after", 0)) - int(p.get("before", 0))
    return spent if spent >= minutes else 0


def apply_mending(ctx: Ctx, refs: list[str] | str, hours: int) -> list[str]:
    """CAMPAIGN mending ruling: every full hour outside combat or stress restores 1 Health Bar slot and 5 Mana."""
    from .combat import heal_character_slots
    if hours <= 0:
        return []
    targets = _rest_targets(ctx, None if refs == "all" else list(refs))
    out = []
    for c in targets:
        if c["hp_max"] is not None and (c["hp_current"] or 0) < c["hp_max"]:
            try:
                out.append(heal_character_slots(ctx.conn, ctx.campaign_id, ctx.turn_id, c, hours, f"{hours}h mending"))
            except ChangeError:
                pass
        if c["mana_max"] is not None and (c["mana_current"] or 0) < c["mana_max"]:
            new = min(c["mana_max"], (c["mana_current"] or 0) + 5 * hours)
            ctx.conn.execute("UPDATE characters SET mana_current = ? WHERE id = ?", (new, c["id"]))
            out.append(f"{c['name']} Mana {c['mana_current']} → {new} (mending)")
    return out


def day_info(conn, cid: int) -> dict[str, Any]:
    camp = one(conn, "SELECT game_time_minutes, day_length_minutes FROM campaigns WHERE id = ?", (cid,))
    length = camp["day_length_minutes"] or 1800   # standard 30-hour dungeon day
    t = camp["game_time_minutes"]
    return {"day": t // length + 1, "minutes_into_day": t % length, "minutes_to_reset": length - t % length, "day_length": length}


def register(ops: dict) -> None:
    ops.update({
        "skill": (SkillChange, _apply_skill),
        "level": (LevelAward, _apply_level),
        "ai_favor": (AiFavor, _apply_ai_favor),
        "group": (GroupUpsert, _apply_group),
        "move_group": (MoveGroup, _apply_move_group),
        "move_character": (MoveCharacter, _apply_move_character),
        "scene": (SceneChange, _apply_scene),
        "remember": (Remember, _apply_remember),
        "use_ability": (UseAbility, _apply_use_ability),
        "rest": (Rest, _apply_rest),
    })

