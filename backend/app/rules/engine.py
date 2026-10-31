"""Rules engine: the only code path that mutates authoritative game state during play.

The GM never edits state directly. It *requests* changes (see CHANGE_OPS); each request is validated here
against current state, applied inside the caller's transaction, and recorded as an immutable event.
Invalid requests raise ChangeError and change nothing, and the GM is told why.

Extension point: add a new op by writing a pydantic model + an `_apply_<op>` function and registering it
in CHANGE_OPS. Carousel rules (tickets, Plot Armor, story phases, fate) land here as further ops, not in prompts.
"""
from __future__ import annotations

import json
import re
import sqlite3
from typing import Any, Callable, Literal

from pydantic import BaseModel, Field, ValidationError, field_validator

from ..db import now_iso, one, rows


class ChangeError(ValueError):
    pass


# ---------------------------------------------------------------- op models


class AddItem(BaseModel):
    op: Literal["add_item"]
    character: str | None = None  # None/"shared" = shared stash
    name: str = Field(min_length=1, max_length=120)
    quantity: int = Field(default=1, ge=1, le=100000)
    description: str | None = None
    rarity: str | None = None
    properties: dict[str, Any] = Field(default_factory=dict)


class RemoveItem(BaseModel):
    op: Literal["remove_item"]
    item_id: int | None = None   # stable id (preferred over name when known)
    character: str | None = None
    name: str
    quantity: int = Field(default=1, ge=1, le=100000)
    cause: str | None = None


class TransferItem(BaseModel):
    op: Literal["transfer_item"]
    item_id: int | None = None   # stable id (preferred over name when known)
    from_character: str | None = None
    to_character: str | None = None
    name: str
    quantity: int = Field(default=1, ge=1, le=100000)


class EquipItem(BaseModel):
    op: Literal["equip_item"]
    item_id: int | None = None   # stable id (preferred over name when known)
    character: str
    name: str
    equipped: bool = True
    slot: str | None = None


POLARITY_ALIASES = {"negative": "debuff", "bad": "debuff", "harmful": "debuff", "injury": "debuff", "condition": "neutral",
                    "positive": "buff", "good": "buff", "beneficial": "buff"}


class AddStatus(BaseModel):
    op: Literal["add_status"]
    character: str
    name: str = Field(min_length=1, max_length=80)
    description: str | None = None
    polarity: Literal["buff", "debuff", "neutral"] = "neutral"

    @field_validator("polarity", mode="before")
    @classmethod
    def _alias(cls, v):
        return POLARITY_ALIASES.get(str(v).lower(), str(v).lower()) if v is not None else "neutral"
    duration_minutes: int | None = Field(default=None, ge=1)


class RemoveStatus(BaseModel):
    op: Literal["remove_status"]
    character: str
    name: str


class AdvanceTime(BaseModel):
    op: Literal["advance_time"]
    minutes: int = Field(ge=1, le=60 * 24 * 10)
    reason: str
    # CAMPAIGN mending: each full hour outside combat/stress restores 1 Health Bar slot + 5 Mana for these characters
    mending: list[str] | Literal["all"] | None = None


class SetLocation(BaseModel):
    op: Literal["set_location"]
    location: str = Field(min_length=1)
    safe_room: bool | None = None
    description: str | None = None


class QuestUpsert(BaseModel):
    op: Literal["quest"]
    title: str = Field(min_length=1, max_length=160)
    # commitment = a promise or NPC agreement; reservation = something held/reserved but NOT owned (never an item)
    kind: Literal["quest", "project", "crafting", "commitment", "reservation"] | None = None
    details: dict[str, Any] | None = None        # merged: design status, contributors, resources, build time, difficulty, terms...
    due_in_minutes: int | None = Field(default=None, ge=0)   # game minutes from now
    owner: str | None = None                     # character who holds the commitment
    description: str | None = None
    status: Literal["active", "completed", "failed", "paused"] | None = None
    priority: int | None = Field(default=None, ge=1, le=3)
    progress: str | None = None
    giver: str | None = None
    immediate: bool | None = None                # true: part of the immediate objective (in order added); false: no longer


class NpcAttitude(BaseModel):
    op: Literal["npc_attitude"]
    npc: str
    delta: int = Field(ge=-6, le=6)
    note: str | None = None


BOX_TIERS = ("Bronze", "Silver", "Gold", "Platinum", "Legendary", "Celestial")


class RecordFact(BaseModel):
    op: Literal["fact"]
    subject: str
    fact: str
    certainty: Literal["canonical", "uncertain", "provisional"] = "canonical"
    visibility: Literal["player", "gm"] = "player"


class NpcUpsert(BaseModel):
    """Record a named person the party has met (or update what the party knows of them), so they appear on People."""
    op: Literal["npc"]
    name: str = Field(min_length=1, max_length=80)
    role: str | None = Field(default=None, max_length=120)          # what they are to the party, e.g. "Game Guide"
    category: Literal["allied_npcs", "neutral_npcs", "hostile_npcs", "contacts", "dungeon_staff"] | None = None
    gender: str | None = Field(default=None, max_length=40)          # decided when they are introduced, as anyone looking at them would see
    pronouns: str | None = Field(default=None, max_length=30)        # used in narration, e.g. "she/her"
    apparent_age: str | None = Field(default=None, max_length=40)    # e.g. "mid-40s"
    description: str | None = Field(default=None, max_length=600)   # appearance and manner as the party saw them
    location: str | None = Field(default=None, max_length=160)
    personality: str | None = Field(default=None, max_length=300)
    voice: str | None = Field(default=None, max_length=200)
    known_motivation: str | None = Field(default=None, max_length=300)
    attitude: int | None = Field(default=None, ge=-3, le=3)        # only when first recorded; later shifts use npc_attitude
    in_party: bool | None = None    # true when they travel and fight with the player character's party


class SetQuantity(BaseModel):
    op: Literal["set_quantity"]
    item_id: int | None = None   # stable id (preferred over name when known)
    character: str | None = None
    name: str
    quantity: int = Field(ge=0, le=1000000)
    reason: str


class ClockChange(BaseModel):
    op: Literal["clock"]
    key: str
    delta: int = Field(ge=-1000, le=1000)
    reason: str


# ---------------------------------------------------------------- helpers
def resolve_character(conn: sqlite3.Connection, campaign_id: int, ref: str | None, *, allow_shared: bool = False) -> dict | None:
    if ref is None or (allow_shared and ref.strip().lower() in {"shared", "stash", "party", ""}):
        if allow_shared:
            return None
        raise ChangeError("a character is required")
    key = ref.strip().lower()
    if key in {"player", "me", "self", "pc"}:
        camp = one(conn, "SELECT active_character_id FROM campaigns WHERE id = ?", (campaign_id,))
        if camp and camp["active_character_id"]:
            return one(conn, "SELECT * FROM characters WHERE id = ?", (camp["active_character_id"],))
    if key.isdigit():
        c = one(conn, "SELECT * FROM characters WHERE campaign_id = ? AND id = ?", (campaign_id, int(key)))
        if c:
            return c
    for sql in (
        "SELECT * FROM characters WHERE campaign_id = ? AND (lower(slug) = ? OR lower(name) = ?)",
        "SELECT * FROM characters WHERE campaign_id = ? AND (lower(name) LIKE ? || '%' OR lower(slug) LIKE ? || '%')",
    ):
        found = rows(conn, sql, (campaign_id, key, key))
        if len(found) == 1:
            return found[0]
        if len(found) > 1:
            raise ChangeError(f"character reference {ref!r} is ambiguous: {[f['name'] for f in found]}")
    raise ChangeError(f"no character matches {ref!r}")


def _find_item(conn: sqlite3.Connection, campaign_id: int, owner_id: int | None, name: str) -> dict | None:
    owner_sql = "owner_character_id IS NULL" if owner_id is None else "owner_character_id = ?"
    params: tuple = (campaign_id,) if owner_id is None else (campaign_id, owner_id)
    exact = rows(conn, f"SELECT * FROM items WHERE campaign_id = ? AND {owner_sql} AND lower(name) = lower(?) AND (quantity IS NULL OR quantity > 0)",
                 params + (name.strip(),))
    if exact:
        return exact[0]
    partial = rows(conn, f"SELECT * FROM items WHERE campaign_id = ? AND {owner_sql} AND lower(name) LIKE '%' || lower(?) || '%' AND (quantity IS NULL OR quantity > 0)",
                   params + (name.strip(),))
    if len(partial) == 1:
        return partial[0]
    if len(partial) > 1:
        raise ChangeError(f"item {name!r} is ambiguous: {[p['name'] for p in partial]}")
    return None


def _owner_label(c: dict | None) -> str:
    return c["name"] if c else "the shared stash"


class Ctx:
    def __init__(self, conn: sqlite3.Connection, campaign_id: int, turn_id: int | None):
        self.conn, self.campaign_id, self.turn_id = conn, campaign_id, turn_id

    def game_time(self) -> int:
        return one(self.conn, "SELECT game_time_minutes FROM campaigns WHERE id = ?", (self.campaign_id,))["game_time_minutes"]

    def event(self, type_: str, summary: str, payload: dict, visibility: str = "player") -> None:
        self.conn.execute(
            """INSERT INTO events (campaign_id, turn_id, type, summary, payload_json, visibility, game_time_minutes, created_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            (self.campaign_id, self.turn_id, type_, summary, json.dumps(payload), visibility, self.game_time(), now_iso()),
        )


# ---------------------------------------------------------------- appliers


def _unreserved(ctx: Ctx, name: str) -> None:
    """Pending gear is nobody's yet: a reserved, unclaimed item can't be added or equipped until the reservation is resolved."""
    from ..memory.inventory import pending_reservation
    q = pending_reservation(ctx.conn, ctx.campaign_id, name)
    if q:
        raise ChangeError(f"{name} is reserved and not yet claimed ({q['title']}); resolve that reservation first in this batch "
                          f"(quest op on that title with status completed, or details owned: true) before handing the item over")


def _apply_add_item(ctx: Ctx, op: AddItem) -> dict:
    _unreserved(ctx, op.name)
    owner = resolve_character(ctx.conn, ctx.campaign_id, op.character, allow_shared=True)
    owner_id = owner["id"] if owner else None
    existing = _find_item(ctx.conn, ctx.campaign_id, owner_id, op.name)
    if existing and existing["name"].lower() == op.name.strip().lower() and not op.properties:
        # NULL (unknown) + n stays unknown: we never invent the missing count.
        ctx.conn.execute("UPDATE items SET quantity = quantity + ? WHERE id = ?", (op.quantity, existing["id"]))
        item_id = existing["id"]
    else:
        item_id = ctx.conn.execute(
            """INSERT INTO items (campaign_id, owner_character_id, name, quantity, description, rarity, properties_json)
               VALUES (?,?,?,?,?,?,?)""",
            (ctx.campaign_id, owner_id, op.name.strip(), op.quantity, op.description, op.rarity, json.dumps(op.properties)),
        ).lastrowid
    summary = f"{_owner_label(owner)} receives {op.quantity}× {op.name.strip()}"
    ctx.event("item_added", summary, {"item_id": item_id, "owner_id": owner_id, "quantity": op.quantity})
    return {"summary": summary, "item_id": item_id}


def _take(ctx: Ctx, owner: dict | None, name: str, quantity: int, item_id: int | None = None) -> dict:
    owner_id = owner["id"] if owner else None
    item = _item_by_id(ctx, item_id, owner) if item_id else _find_item(ctx.conn, ctx.campaign_id, owner_id, name)
    if not item:
        raise ChangeError(f"{_owner_label(owner)} has no item matching {name!r}")
    if item["quantity"] is None:
        raise ChangeError(f"the ledger does not establish how many {item['name']} {_owner_label(owner)} has; "
                          "establish the count with set_quantity before spending or moving it")
    if item["quantity"] < quantity:
        raise ChangeError(f"{_owner_label(owner)} has only {item['quantity']}× {item['name']}")
    if item["quantity"] == quantity:
        ctx.conn.execute("UPDATE items SET quantity = 0, equipped = 0 WHERE id = ?", (item["id"],))
    else:
        ctx.conn.execute("UPDATE items SET quantity = quantity - ? WHERE id = ?", (quantity, item["id"]))
    return item


def _apply_remove_item(ctx: Ctx, op: RemoveItem) -> dict:
    owner = resolve_character(ctx.conn, ctx.campaign_id, op.character, allow_shared=True)
    item = _take(ctx, owner, op.name, op.quantity, op.item_id)
    summary = f"{_owner_label(owner)} loses {op.quantity}× {item['name']}" + (f" — {op.cause}" if op.cause else "")
    ctx.event("item_removed", summary, {"item_id": item["id"], "quantity": op.quantity})
    ctx.conn.execute("DELETE FROM items WHERE id = ? AND quantity = 0", (item["id"],))
    return {"summary": summary, "item_id": item["id"]}


def _apply_transfer_item(ctx: Ctx, op: TransferItem) -> dict:
    src = resolve_character(ctx.conn, ctx.campaign_id, op.from_character, allow_shared=True)
    dst = resolve_character(ctx.conn, ctx.campaign_id, op.to_character, allow_shared=True)
    if (src or {}).get("id") == (dst or {}).get("id"):
        raise ChangeError("source and destination are the same")
    item = _take(ctx, src, op.name, op.quantity, op.item_id)
    dst_id = dst["id"] if dst else None
    same = _find_item(ctx.conn, ctx.campaign_id, dst_id, item["name"])
    if same and same["name"] == item["name"] and same["properties"] == item["properties"]:
        ctx.conn.execute("UPDATE items SET quantity = quantity + ? WHERE id = ?", (op.quantity, same["id"]))
    else:
        ctx.conn.execute(
            """INSERT INTO items (campaign_id, owner_character_id, name, quantity, description, rarity, properties_json)
               VALUES (?,?,?,?,?,?,?)""",
            (ctx.campaign_id, dst_id, item["name"], op.quantity, item["description"], item["rarity"], json.dumps(item["properties"])),
        )
    ctx.conn.execute("DELETE FROM items WHERE id = ? AND quantity = 0", (item["id"],))
    summary = f"{op.quantity}× {item['name']}: {_owner_label(src)} → {_owner_label(dst)}"
    ctx.event("item_transferred", summary, {"item": item["name"], "from": (src or {}).get("id"), "to": dst_id, "quantity": op.quantity})
    return {"summary": summary}


def _apply_equip_item(ctx: Ctx, op: EquipItem) -> dict:
    c = resolve_character(ctx.conn, ctx.campaign_id, op.character)
    item = _item_by_id(ctx, op.item_id, c) if op.item_id else _find_item(ctx.conn, ctx.campaign_id, c["id"], op.name)
    if not item:
        raise ChangeError(f"{c['name']} is not carrying {op.name!r}")
    ctx.conn.execute("UPDATE items SET equipped = ?, slot = COALESCE(?, slot) WHERE id = ?", (int(op.equipped), op.slot, item["id"]))
    summary = f"{c['name']} {'equips' if op.equipped else 'unequips'} {item['name']}"
    ctx.event("item_equipped", summary, {"item_id": item["id"], "equipped": op.equipped})
    return {"summary": summary}


def _apply_add_status(ctx: Ctx, op: AddStatus) -> dict:
    c = resolve_character(ctx.conn, ctx.campaign_id, op.character)
    expires = ctx.game_time() + op.duration_minutes if op.duration_minutes else None
    ctx.conn.execute("DELETE FROM status_effects WHERE character_id = ? AND lower(name) = lower(?)", (c["id"], op.name))
    ctx.conn.execute(
        "INSERT INTO status_effects (character_id, name, description, polarity, expires_at_minutes, source) VALUES (?,?,?,?,?,?)",
        (c["id"], op.name, op.description, op.polarity, expires, "play"),
    )
    summary = f"{c['name']} gains status: {op.name}" + (f" ({op.duration_minutes} min)" if op.duration_minutes else "")
    ctx.event("status_added", summary, {"character_id": c["id"], "name": op.name, "expires_at_minutes": expires})
    return {"summary": summary}


def _apply_remove_status(ctx: Ctx, op: RemoveStatus) -> dict:
    c = resolve_character(ctx.conn, ctx.campaign_id, op.character)
    n = ctx.conn.execute("DELETE FROM status_effects WHERE character_id = ? AND lower(name) = lower(?)", (c["id"], op.name)).rowcount
    if not n:
        raise ChangeError(f"{c['name']} has no status named {op.name!r}")
    summary = f"{c['name']} is no longer {op.name}"
    ctx.event("status_removed", summary, {"character_id": c["id"], "name": op.name})
    return {"summary": summary}


def _apply_advance_time(ctx: Ctx, op: AdvanceTime) -> dict:
    camp = one(ctx.conn, "SELECT game_time_minutes FROM campaigns WHERE id = ?", (ctx.campaign_id,))
    before = camp["game_time_minutes"]
    after = before + op.minutes
    ctx.conn.execute("UPDATE campaigns SET game_time_minutes = ?, updated_at = ? WHERE id = ?", (after, now_iso(), ctx.campaign_id))
    expired = rows(ctx.conn,
                   """SELECT s.id, s.name, c.name AS who FROM status_effects s JOIN characters c ON c.id = s.character_id
                      WHERE c.campaign_id = ? AND s.expires_at_minutes IS NOT NULL AND s.expires_at_minutes <= ?""",
                   (ctx.campaign_id, after))
    for s in expired:
        ctx.conn.execute("DELETE FROM status_effects WHERE id = ?", (s["id"],))
    summary = f"{op.minutes} minutes pass — {op.reason}"
    ctx.event("time_advanced", summary, {"before": before, "after": after, "expired_statuses": [f"{s['who']}: {s['name']}" for s in expired]})
    for s in expired:
        ctx.event("status_expired", f"{s['who']}'s {s['name']} wears off", {"status": s["name"]})
    return {"summary": summary, "game_time_minutes": after}

def _apply_set_location(ctx: Ctx, op: SetLocation) -> dict:
    sets, params = ["current_location = ?", "updated_at = ?"], [op.location, now_iso()]
    if op.safe_room is not None:
        sets.append("safe_room = ?")
        params.append(int(op.safe_room))
    ctx.conn.execute(f"UPDATE campaigns SET {', '.join(sets)} WHERE id = ?", (*params, ctx.campaign_id))
    if not one(ctx.conn, "SELECT id FROM locations WHERE campaign_id = ? AND lower(name) = lower(?)", (ctx.campaign_id, op.location)):
        ctx.conn.execute("INSERT INTO locations (campaign_id, name, description) VALUES (?,?,?)",
                         (ctx.campaign_id, op.location, op.description))
    summary = f"Party moves to {op.location}"
    ctx.event("location_changed", summary, {"location": op.location, "safe_room": op.safe_room})
    # the player character's group (and so its members) is where the campaign is (playtest 1, H4)
    from .state_ops import pc_group, remember_saferoom
    if op.safe_room:
        remember_saferoom(ctx, op.location)
    g = pc_group(ctx)
    if g and g["location"] != op.location:
        ctx.conn.execute("UPDATE party_groups SET location = ? WHERE id = ?", (op.location, g["id"]))
        ctx.conn.execute("UPDATE characters SET location = ? WHERE campaign_id = ? AND assignment = ?", (op.location, ctx.campaign_id, g["key"]))
        ctx.event("group_moved", f"{g['label']} → {op.location} (with the player character)", {"key": g["key"], "from": g["location"], "to": op.location})
    return {"summary": summary}


def _apply_quest(ctx: Ctx, op: QuestUpsert) -> dict:
    q = one(ctx.conn, "SELECT * FROM quests WHERE campaign_id = ? AND lower(title) = lower(?)", (ctx.campaign_id, op.title.strip()))
    extra: dict[str, Any] = {}
    if op.details is not None:
        extra["details_json"] = json.dumps({**((q or {}).get("details") or {}), **op.details})
    if op.due_in_minutes is not None:
        extra["due_at_minutes"] = ctx.game_time() + op.due_in_minutes
    if op.owner:
        extra["owner_character_id"] = resolve_character(ctx.conn, ctx.campaign_id, op.owner)["id"]
    if q:
        fields = {k: v for k, v in op.model_dump(exclude={"op", "title", "details", "due_in_minutes", "owner", "immediate"}).items() if v is not None}
        fields.update(extra)
        if not fields and op.immediate is None:
            raise ChangeError("quest update has no changes")
        if fields:
            ctx.conn.execute(f"UPDATE quests SET {', '.join(f'{k} = ?' for k in fields)}, updated_at = ? WHERE id = ?",
                             (*fields.values(), now_iso(), q["id"]))
        verb = {"completed": "completed", "failed": "failed"}.get(op.status or "", "updated")
        summary = f"Quest {verb}: {q['title']}"
        etype = "quest_" + verb
    else:
        ctx.conn.execute(
            """INSERT INTO quests (campaign_id, kind, title, description, status, priority, progress, giver, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (ctx.campaign_id, op.kind or "quest", op.title.strip(), op.description, op.status or "active",
             op.priority or 2, op.progress, op.giver, now_iso()),
        )
        if extra:
            qid = ctx.conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            ctx.conn.execute(f"UPDATE quests SET {', '.join(f'{k} = ?' for k in extra)} WHERE id = ?", (*extra.values(), qid))
        summary = f"New {op.kind or 'quest'}: {op.title.strip()}"
        etype = "quest_added"
    if op.immediate is not None:
        qid = q["id"] if q else ctx.conn.execute("SELECT id FROM quests WHERE campaign_id = ? AND title = ? ORDER BY id DESC",
                                                 (ctx.campaign_id, op.title.strip())).fetchone()[0]
        ids = json.loads(one(ctx.conn, "SELECT objective_quests_json AS j FROM campaigns WHERE id = ?", (ctx.campaign_id,))["j"] or "[]")
        ids = [i for i in ids if i != qid] + ([qid] if op.immediate else [])
        ctx.conn.execute("UPDATE campaigns SET objective_quests_json = ? WHERE id = ?", (json.dumps(ids), ctx.campaign_id))
        summary += " (now part of the immediate objective)" if op.immediate else " (no longer the immediate objective)"
    ctx.event(etype, summary, op.model_dump(exclude={"op"}))
    return {"summary": summary}


def _item_by_id(ctx: "Ctx", item_id: int, owner: dict | None) -> dict:
    it = one(ctx.conn, "SELECT * FROM items WHERE id = ? AND campaign_id = ?", (item_id, ctx.campaign_id))
    if not it:
        raise ChangeError(f"no item #{item_id}")
    if owner is not None and it["owner_character_id"] != owner["id"]:
        raise ChangeError(f"item #{item_id} ({it['name']}) is not carried by {owner['name']}")
    if owner is None and it["owner_character_id"] is not None:
        raise ChangeError(f"item #{item_id} ({it['name']}) is not in the shared stash")
    return it


def _apply_npc_attitude(ctx: Ctx, op: NpcAttitude) -> dict:
    c = resolve_character(ctx.conn, ctx.campaign_id, op.npc)
    prof = one(ctx.conn, "SELECT attitude FROM npc_profiles WHERE character_id = ?", (c["id"],))
    if prof is None:
        ctx.conn.execute("INSERT INTO npc_profiles (character_id, attitude) VALUES (?, 0)", (c["id"],))
        prof = {"attitude": 0}
    base = prof["attitude"] if prof["attitude"] is not None else 0
    new = max(-3, min(3, base + op.delta))
    ctx.conn.execute("UPDATE npc_profiles SET attitude = ? WHERE character_id = ?", (new, c["id"]))
    was = f"{prof['attitude']:+d}" if prof["attitude"] is not None else "unrecorded (0)"
    summary = f"{c['name']}'s attitude shifts ({was} → {new:+d})" + (f" — {op.note}" if op.note else "")
    ctx.event("npc_attitude", summary, {"character_id": c["id"], "before": prof["attitude"], "after": new})
    return {"summary": summary}


def _apply_fact(ctx: Ctx, op: RecordFact) -> dict:
    ctx.conn.execute(
        "INSERT INTO lore_facts (campaign_id, subject, fact, certainty, visibility, source, created_at) VALUES (?,?,?,?,?,?,?)",
        (ctx.campaign_id, op.subject, op.fact, op.certainty, op.visibility, f"turn {ctx.turn_id}", now_iso()),
    )
    summary = f"Recorded {op.certainty} fact about {op.subject}"
    ctx.event("fact_recorded", summary, {"subject": op.subject}, visibility=op.visibility)
    return {"summary": summary, "visibility": op.visibility}


def _apply_npc(ctx: Ctx, op: NpcUpsert) -> dict:
    name = op.name.strip()
    c = one(ctx.conn, "SELECT * FROM characters WHERE campaign_id = ? AND lower(name) = lower(?)", (ctx.campaign_id, name))
    camp = one(ctx.conn, "SELECT active_character_id FROM campaigns WHERE id = ?", (ctx.campaign_id,))
    if c and c["id"] == camp["active_character_id"]:
        raise ChangeError("the player character is not an NPC")
    if c is None:
        base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "npc"
        slug, n = base, 2
        while one(ctx.conn, "SELECT id FROM characters WHERE campaign_id = ? AND slug = ?", (ctx.campaign_id, slug)):
            slug, n = f"{base}-{n}", n + 1
        sheet = {"narrative": {"current_role": op.role}} if op.role else {}
        ident = {k: v for k, v in (("gender", op.gender), ("pronouns", op.pronouns), ("apparent_age", op.apparent_age)) if v}
        if ident:
            sheet["identity"] = ident
        cid = ctx.conn.execute(
            """INSERT INTO characters (campaign_id, slug, name, kind, roster_category, status, location, in_party, bio, sheet_json, provenance)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (ctx.campaign_id, slug, name, "npc", op.category or ("core_party" if op.in_party else "neutral_npcs"),
             "active", op.location, int(bool(op.in_party)), op.description, json.dumps(sheet), f"turn {ctx.turn_id}")).lastrowid
        ctx.conn.execute("INSERT INTO npc_profiles (character_id, personality, voice, known_motivation, attitude, last_seen) VALUES (?,?,?,?,?,?)",
                         (cid, op.personality, op.voice, op.known_motivation, op.attitude, op.location))
        summary = f"Met {name}" + (f", {op.role}" if op.role else "") + (f" ({op.location})" if op.location else "")
        ctx.event("npc_met", summary, {"character_id": cid, "role": op.role})
        return {"summary": summary, "character_id": cid}
    sets = {k: v for k, v in {"roster_category": op.category, "location": op.location, "bio": op.description}.items() if v is not None}
    if op.in_party is not None:
        sets["in_party"] = int(op.in_party)
        if op.in_party and not op.category and c.get("roster_category") in (None, "neutral_npcs", "allied_npcs", "contacts"):
            sets["roster_category"] = "core_party"
    ident = {k: v for k, v in (("gender", op.gender), ("pronouns", op.pronouns), ("apparent_age", op.apparent_age)) if v}
    if op.role or ident:
        sheet = c.get("sheet") or {}
        if op.role:
            sheet.setdefault("narrative", {})["current_role"] = op.role
        if ident:
            sheet.setdefault("identity", {}).update(ident)
        sets["sheet_json"] = json.dumps(sheet)
    if sets:
        ctx.conn.execute(f"UPDATE characters SET {', '.join(f'{k} = ?' for k in sets)} WHERE id = ?", (*sets.values(), c["id"]))
    prof = {k: v for k, v in {"personality": op.personality, "voice": op.voice, "known_motivation": op.known_motivation,
                              "last_seen": op.location}.items() if v is not None}
    if not one(ctx.conn, "SELECT character_id FROM npc_profiles WHERE character_id = ?", (c["id"],)):
        ctx.conn.execute("INSERT INTO npc_profiles (character_id, attitude) VALUES (?, ?)", (c["id"], op.attitude))
    if prof:
        ctx.conn.execute(f"UPDATE npc_profiles SET {', '.join(f'{k} = ?' for k in prof)} WHERE character_id = ?", (*prof.values(), c["id"]))
    summary = f"Updated what the party knows of {c['name']}"
    ctx.event("npc_updated", summary, {"character_id": c["id"], "fields": sorted([*sets, *prof])})
    return {"summary": summary, "character_id": c["id"]}


def _apply_set_quantity(ctx: Ctx, op: SetQuantity) -> dict:
    owner = resolve_character(ctx.conn, ctx.campaign_id, op.character, allow_shared=True)
    item = _item_by_id(ctx, op.item_id, owner) if op.item_id else _find_item(ctx.conn, ctx.campaign_id, owner["id"] if owner else None, op.name)
    if not item:
        raise ChangeError(f"{_owner_label(owner)} has no item matching {op.name!r}")
    ctx.conn.execute("UPDATE items SET quantity = ? WHERE id = ?", (op.quantity, item["id"]))
    before = "unknown" if item["quantity"] is None else item["quantity"]
    summary = f"{item['name']} count for {_owner_label(owner)} set {before} → {op.quantity} — {op.reason}"
    ctx.event("item_count_set", summary, {"item_id": item["id"], "before": item["quantity"], "after": op.quantity})
    return {"summary": summary}


def _apply_clock(ctx: Ctx, op: ClockChange) -> dict:
    clock = one(ctx.conn, "SELECT * FROM clocks WHERE campaign_id = ? AND key = ?", (ctx.campaign_id, op.key))
    if not clock:
        raise ChangeError(f"no clock {op.key!r}")
    new = max(0, clock["current"] + op.delta)
    if clock["maximum"] is not None:
        new = min(new, clock["maximum"])
    ctx.conn.execute("UPDATE clocks SET current = ? WHERE id = ?", (new, clock["id"]))
    summary = f"{clock['label']}: {clock['current']} → {new}" + (f"/{clock['maximum']}" if clock["maximum"] else "") + f" — {op.reason}"
    ctx.event("clock_changed", summary, {"key": op.key, "before": clock["current"], "after": new}, visibility=clock["visibility"])
    return {"summary": summary, "visibility": clock["visibility"]}


CHANGE_OPS: dict[str, tuple[type[BaseModel], Callable[[Ctx, Any], dict]]] = {
    "add_item": (AddItem, _apply_add_item),
    "remove_item": (RemoveItem, _apply_remove_item),
    "transfer_item": (TransferItem, _apply_transfer_item),
    "equip_item": (EquipItem, _apply_equip_item),
    "add_status": (AddStatus, _apply_add_status),
    "remove_status": (RemoveStatus, _apply_remove_status),
    "advance_time": (AdvanceTime, _apply_advance_time),
    "set_location": (SetLocation, _apply_set_location),
    "quest": (QuestUpsert, _apply_quest),
    "npc_attitude": (NpcAttitude, _apply_npc_attitude),
    "fact": (RecordFact, _apply_fact),
    "set_quantity": (SetQuantity, _apply_set_quantity),
    "npc": (NpcUpsert, _apply_npc),
    "clock": (ClockChange, _apply_clock),
}


def apply_change(conn: sqlite3.Connection, campaign_id: int, turn_id: int | None, change: dict) -> dict:
    """Validate and apply one change inside the caller's open transaction. Uses a SAVEPOINT so a
    failing change leaves no partial writes while earlier changes in the turn stay staged."""
    op_name = change.get("op")
    if op_name not in CHANGE_OPS:
        raise ChangeError(f"unknown op {op_name!r}; valid ops: {sorted(CHANGE_OPS)}")
    model, fn = CHANGE_OPS[op_name]
    try:
        parsed = model.model_validate(change)
    except ValidationError as exc:
        raise ChangeError(f"invalid {op_name} change: {exc.errors(include_url=False)}") from None
    conn.execute("SAVEPOINT change")
    try:
        result = fn(Ctx(conn, campaign_id, turn_id), parsed)
    except BaseException:
        conn.execute("ROLLBACK TO change")
        conn.execute("RELEASE change")
        raise
    conn.execute("RELEASE change")
    result["op"] = op_name
    return result


def ops_reference() -> str:
    """Compact op catalogue for the GM prompt (field names and types only)."""
    lines = []
    for name, (model, _) in CHANGE_OPS.items():
        fields = []
        for fname, f in model.model_fields.items():
            if fname == "op":
                continue
            ann = getattr(f.annotation, "__name__", None) or str(f.annotation).replace("typing.", "")
            fields.append(f"{fname}{'' if f.is_required() else '?'}: {ann}")
        lines.append(f"- {name}: {', '.join(fields)}")
    return "\n".join(lines)

