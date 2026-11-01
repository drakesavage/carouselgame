"""Character dossier: a structured, player-safe view of one character for the dossier UI.

Everything here is derived from the authoritative records (characters, Carousel sheet, items, statuses, events,
player-visible quests and relationships) without changing them, inventing values or reading GM-only tables
(gm_secrets, GM-visibility events/quests/relationships). Missing data stays missing; the UI shows an empty state.
Classification (equipment slots) only sorts records for display.
"""
from __future__ import annotations

import re
import sqlite3
from typing import Any

from ..db import one, rows
from .views import character_sheet, life_state

SLOT_WORDS: list[tuple[str, str]] = [
    ("head", r"\b(hat|helmet|helm|cap|hood|crown|goggles|mask|bandana)\b"),
    ("hands", r"\b(glove|gloves|gauntlet|gauntlets|bracer|bracers)\b"),
    ("feet", r"\b(boot|boots|shoe|shoes|sandals)\b"),
    ("armor", r"\b(armor|armour|carapace|vest|uniform|harness|jacket|coat|robe|chest ?guard|breastplate|mail|shirt)\b"),
    ("weapon", r"\b(crossbow|bow|staff|quarterstaff|club|spear|knife|dagger|sword|axe|mace|hammer|pistol|gun|rifle|javelins?|"
               r"wand|whip|stick|blade|shield)\b"),
    ("accessory", r"\b(lanyard|ring|amulet|badge|necklace|pendant|charm|bracelet|belt|cloak|earring|pin|collar)\b"),
]
SLOT_LABELS = {"weapon": "Weapons", "armor": "Armor & clothing", "head": "Head", "hands": "Hands", "feet": "Feet",
               "accessory": "Accessories", "other": "Other equipped"}


def _first(name: str) -> str:
    return re.sub(r"\(.*?\)", "", name).split()[0] if name else ""


def _mentions(text: str, name: str) -> bool:
    if not text or not name:
        return False
    names = {name, _first(name)}
    return any(len(n) > 2 and re.search(rf"\b{re.escape(n)}\b", text, re.I) for n in names)


def _norm(name: str) -> str:
    """'Clear Lane', 'clear_lane' and 'Clear-Lane!' are the same ability."""
    return re.sub(r"[^a-z0-9]+", " ", str(name).lower().replace("’", "'").replace("'", "")).strip()


def _flat(v: Any) -> str:
    if isinstance(v, dict):
        return " ".join(f"{k} {_flat(x)}" for k, x in v.items())
    if isinstance(v, list):
        return " ".join(_flat(x) for x in v)
    return str(v) if v is not None else ""


def _list(v: Any) -> list:
    return v if isinstance(v, list) else ([v] if v else [])




# ------------------------------------------------------------------ equipment
def item_slot(item: dict) -> str:
    for slot, pat in SLOT_WORDS:
        if re.search(pat, item["name"], re.I):
            return slot
    return "other"


def _item_view(i: dict) -> dict:
    p = i.get("properties") or {}
    effects = [str(e) for e in _list(p.get("effects"))]
    for a in _list(p.get("attachments")):
        if isinstance(a, dict):
            effects += [f"{a.get('name', 'Attachment')}: {e}" for e in _list(a.get("effects"))]
    special = any(k in p for k in ("attachments", "construction", "capacity", "components", "modules", "charges"))
    return {"id": i["id"], "name": i["name"], "quantity": i.get("quantity"), "equipped": bool(i.get("equipped")),
            "deployed": bool(i.get("deployed")), "rarity": i.get("rarity"), "description": i.get("description"),
            "slot": item_slot(i), "effects": effects, "notes": [str(n) for n in _list(p.get("notes"))],
            "status": p.get("status") or p.get("current_status"), "provenance": p.get("provenance"), "special": special,
            "properties": {k: v for k, v in p.items() if k not in ("effects", "notes", "source_key")}}


# ------------------------------------------------------------------ the dossier


# ------------------------------------------------------------------ the dossier
def build(conn: sqlite3.Connection, cid: int, char_id: int) -> dict | None:
    c = character_sheet(conn, cid, char_id)
    if not c:
        return None
    sheet = c.get("sheet") or {}
    narrative = sheet.get("narrative") or {}
    group = one(conn, "SELECT key, label, location, activity FROM party_groups WHERE campaign_id = ? AND key = ?", (cid, c.get("assignment"))) \
        if c.get("assignment") else None
    camp = one(conn, "SELECT active_character_id FROM campaigns WHERE id = ?", (cid,))

    items = [_item_view(i) for i in c["items"]]
    shared = rows(conn, "SELECT id, name, quantity, equipped, deployed, slot, rarity, description, properties_json FROM items "
                        "WHERE campaign_id = ? AND owner_character_id IS NULL", (cid,))
    linked = [dict(_item_view(i), shared=True) for i in shared if _mentions(i["name"] + " " + _flat(i.get("properties")), c["name"])]
    quests = rows(conn, """SELECT id, kind, title, description, status, progress, details_json, due_at_minutes, owner_character_id FROM quests
                           WHERE campaign_id = ? AND visibility = 'player' AND status IN ('active','paused')""", (cid,))
    mine = [q for q in quests if q.get("owner_character_id") == char_id
            or _mentions(" ".join(str(q.get(k) or "") for k in ("title", "description", "progress")) + " " + _flat(q.get("details")), c["name"])]

    story = {"role": narrative.get("current_role") or c.get("bio"), "personality": narrative.get("personality_summary"),
             "traits": [str(t) for t in _list(narrative.get("traits"))],
             "relationships": c.get("relationships") or [], "profile": c.get("npc_profile"), "quests": mine,
             "appearance": ((sheet.get("visual") or {}).get("reference") or None),
             "assignment": group, "location": (group or {}).get("location") or c.get("location"),
             "identity": sheet.get("identity") if isinstance(sheet.get("identity"), dict) else None}

    history = []
    for e in rows(conn, """SELECT id, type, summary, game_time_minutes, created_at, payload_json FROM events WHERE campaign_id = ?
                           AND visibility = 'player' AND rolled_back = 0 ORDER BY id DESC LIMIT 400""", (cid,)):
        payload = e.get("payload") or {}
        if payload.get("character_id") == char_id or _mentions(e["summary"], c["name"]):
            history.append({"kind": _history_kind(e["type"] + " " + e["summary"]), "text": e["summary"], "when": e.get("game_time_minutes"),
                            "at": e.get("created_at"), "source": "play"})
    for i in items:
        if i["provenance"]:
            history.append({"kind": "item", "text": f"{i['name']}: {i['provenance']}", "when": None, "source": "item record"})

    return {"id": c["id"], "slug": c["slug"], "name": c["name"], "kind": c["kind"], "is_player": bool(camp and camp["active_character_id"] == c["id"]),
            "status": c.get("status"), "life": life_state(c), "in_party": bool(c.get("in_party")), "roster_category": c.get("roster_category"),
            "portrait_path": c.get("portrait_path"), "portrait_crop": c.get("portrait_crop"),
            "role": {"id": c.get("role_id"), "reason": c.get("role_reason"), "aspect_id": c.get("aspect_id"), "advanced_role_id": c.get("advanced_role_id")},
            "stats": c["stats"], "rewards": {"stars": c.get("stars") or 0, "pending_stat_points": c.get("pending_stat_points") or 0},
            "tickets": c["tickets"], "statuses": c.get("statuses") or [], "group": group, "location": story["location"],
            "equipment": {"equipped": [i for i in items if i["equipped"]], "carried": [i for i in items if not i["equipped"]],
                          "linked_shared": linked, "commitments": mine, "slot_labels": SLOT_LABELS},
            "story": story, "history": history,
            # whether Generate has run / is locked by a hand-written private sheet (existence only, never its content)
            "private_sheet": (lambda s: None if not s else "generated" if s["source"] == "generated" else "authored")(
                one(conn, "SELECT source FROM gm_secrets WHERE campaign_id = ? AND kind = 'companion_sheet' AND subject = ?", (cid, c["slug"])))}


def _history_kind(text: str) -> str:
    t = text.lower()
    if re.search(r"ticket|trope", t):
        return "ticket"
    if re.search(r"injur|wound|dying|recover|heal", t):
        return "injury"
    if re.search(r"item|acquir|bought|purchas|equip", t):
        return "item"
    return "milestone"
