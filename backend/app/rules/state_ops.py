"""Validated state ops added in schema v2 (registered into engine.CHANGE_OPS at import).

Party organization: groups with a location and activity; only a group's own members move with it.
Scene and memory: the active scene, episodic/NPC memories.
"""
from __future__ import annotations

import json
import re
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from ..db import now_iso, one, rows
from .engine import ChangeError, Ctx, resolve_character

def _slug(s: str) -> str:
    return "".join(ch if ch.isalnum() else "-" for ch in s.lower()).strip("-")




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


# ------------------------------------------------------------------ rest (BOOK 11.4) and mending


def register(ops: dict) -> None:
    ops.update({
        "group": (GroupUpsert, _apply_group),
        "move_group": (MoveGroup, _apply_move_group),
        "move_character": (MoveCharacter, _apply_move_character),
        "scene": (SceneChange, _apply_scene),
        "remember": (Remember, _apply_remember),
    })

