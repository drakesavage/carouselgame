"""The inventory as a player uses it: categories, honest quantities, inventions by stage, Gold, reservations, materials.

Read-only views over the authoritative records (items, quests, events). Nothing here changes state; every change still
goes through the rules engine (validated ops), which refuses to spend unknown counts or more than exists.
"""
from __future__ import annotations

import re
import sqlite3

from ..db import rows
from .dossier import _norm

# (category, pattern on the item name), first match wins; trap records and construction details are checked first
CATEGORIES = (
    ("currency", r"^gold$"),
    ("book", r"\b(tome|spellbook|manual|guidebook|grimoire)\b"),
    ("vehicle", r"\b(pickup|truck|car|bicycle|bike|canoe|kayak|boat|hang glider|glider|vehicle|wagon|cart)\b"),
    ("ammunition", r"\b(bolts?|arrows?|rounds?|shells?|darts?|stakes?|ammo|cartridges?|pellets?)\b"),
    ("weapon", r"\b(crossbow|shortbow|longbow|bow|pistol|handgun|revolver|shotgun|rifle|sword|knife|dagger|stabber|club|quarterstaff|staff|"
               r"spear|spears|javelins?|axe|hatchet|pickaxe|hammer|mace|bolas|net launcher|net|harpoon|gun|blade|machete|bat|whip|sling|thumper)\b"),
    ("armor", r"\b(armou?r|shield|helmet|helm|hard hat|vest|plate|mail|gauntlets?|bracers?|carapace|chest guard|harness|apron|goggles|mask|"
              r"clothes|clothing|uniform|hide|lanyard|prosthetic)\b"),
    ("consumable", r"\b(potion|scroll|bandages?|rations?|food|meal|stout|beer|whiskey|wine|liquor|antidote|elixir|tonic|biscuits?|jerky|"
                   r"water|snacks?|candy|beans|m&ms|peanuts?|medkit|salve|syringe|pills?|dynamite|grenades?)\b"),
    ("material", r"\b(junk|scrap|wire|cable|cabling|rope|cord|parts|components?|lumber|wood|metal|cloth|fabric|tape|glue|screws|nails|"
                 r"assembly|core|stock|cache|supplies|materials?|residue)\b"),
    ("keepsake", r"\b(ornament|bobblehead|sticker|dvd|toothbrush|boxed set|freshener|guitar pick|memoir|badge|keyring|souvenir|trinket)\b"),
    ("tool", r"\b(tools?|toolkit|kit|wrench|crowbar|lockpicks?|lamp|light|torch|flashlight|map|reel|kite|camera|binoculars|compass|"
             r"ladder|mount|sight|extinguisher|blanket|carrier|notebook|drum kit)\b"),
)
TRAP_NAMES = r"\b(trap|snapweb|caltrop|alarm|barricade|battery|pod|cinch|scatterburst|thrower)\b"
INVENTION_KEYS = ("construction", "components", "installed_upgrades", "configuration_options")  # built by the party, not bought
QUANTITY_NOTES = ("quantity_status", "count_status", "quantity_semantics", "additional_unquantified_stock")
STAGES = ("planned", "designed", "in_progress", "finished", "installed")
STOP = {"the", "a", "an", "of", "and", "for", "after", "with", "build", "built", "mk", "ii", "to", "on", "in"}


def _words(text: str) -> set[str]:
    return {w for w in _norm(text).split() if len(w) > 2 and w not in STOP}


def category(i: dict) -> str:
    p = i.get("properties") or {}
    if p.get("trap") or p.get("trap_state") or re.search(TRAP_NAMES, i["name"], re.I):
        return "trap"
    if str(p.get("item_type") or "").lower() in {c for c, _ in CATEGORIES}:
        return str(p["item_type"]).lower()
    for cat, pat in CATEGORIES:
        if re.search(pat, i["name"], re.I):
            return cat
    if p.get("mechanical_skill") or p.get("hands_required"):
        return "weapon"
    return "other"


def stage_of(text: str | None) -> str | None:
    """'designed-not-built' -> designed; 'under construction' -> in_progress; 'mounted' -> installed."""
    t = (text or "").lower()
    if not t:
        return None
    if re.search(r"designed|design only|not (yet )?built|blueprint", t):
        return "designed"
    if re.search(r"in progress|under construction|building|partly built|half[- ]built", t):
        return "in_progress"
    if re.search(r"\b(installed|incorporated)\b", t):  # "with mounted parts" describes a build, not where it sits
        return "installed"
    if re.search(r"planned|proposed|idea|deferred", t):
        return "planned"
    if re.search(r"built|finished|complete|ready|functional|working|prototype", t):
        return "finished"
    return None


def item_facts(i: dict) -> dict:
    p = i.get("properties") or {}
    status = p.get("status") or p.get("current_status") or p.get("equip_status")
    invention = any(k in p for k in INVENTION_KEYS)
    stage = None
    if invention or p.get("attached_to"):
        stage = "installed" if p.get("attached_to") else stage_of(str(status)) or "finished"
        if stage in ("planned", "designed", "in_progress"):
            stage = "finished"  # an item that exists has been made; its unbuilt conversions live in the project
    note = next((str(p[k]) for k in QUANTITY_NOTES if p.get(k)), None)
    return {"category": category(i), "quantity_state": "unknown" if i.get("quantity") is None else "exact", "quantity_note": note,
            "status": str(status) if status else None, "invention": invention, "stage": stage,
            "storage": p.get("storage"), "attached_to": p.get("attached_to")}


def _reservations(quests: list[dict]) -> list[dict]:
    out = []
    for q in quests:
        if q["kind"] != "reservation":
            continue
        d = q.get("details") or {}
        paid = d.get("paid_gold") if isinstance(d.get("paid_gold"), (int, float)) else None
        out.append({"quest_id": q["id"], "item": re.sub(r"^Reserved:\s*", "", q["title"]), "payment": d.get("payment") or ("paid" if paid else None),
                    "paid_gold": paid, "held_by": d.get("held_by"), "claim": d.get("claim"), "owned": bool(d.get("owned")),
                    # the structured details are the reconciled record; the description can be an older note
                    "note": q.get("progress") or (None if d.get("payment") else q.get("description"))})
    return out


def _projects(quests: list[dict], items: list[dict]) -> list[dict]:
    out = []
    mats = [i for i in items if i["category"] == "material" and i.get("quantity") is not None]
    for q in quests:
        if q["kind"] not in ("project", "crafting"):
            continue
        d = q.get("details") or {}
        builds = q["kind"] == "crafting" or any(k in d for k in ("build_time", "engineering_difficulty", "difficulty", "status")) \
            or any(k.endswith("_cost") for k in d)
        if not builds:
            continue  # an ongoing goal ("allocate gear", "build the network"), not something being made: it lives in the journal
        stage = stage_of(str(d.get("status") or "")) or stage_of(q.get("progress")) or "planned"
        words = _words(q["title"])
        linked = [{"id": i["id"], "name": i["name"], "owner": i.get("owner_name")} for i in items if len(words & _words(i["name"])) >= 2]
        needs = []
        for k, v in d.items():
            m = re.match(r"(.+?)_cost$", k)
            if not m or not isinstance(v, (int, float)):
                continue
            want = m.group(1).split("_")
            have = next((i for i in mats if all(w in _norm(i["name"]) for w in want)), None)
            needs.append({"material": have["name"] if have else m.group(1).replace("_", " ").title(), "need": v,
                          "have": have["quantity"] if have else None, "enough": bool(have and have["quantity"] >= v)})
        out.append({"quest_id": q["id"], "title": q["title"], "stage": stage, "status_text": d.get("status") or q.get("progress"),
                    "build_time": d.get("build_time"), "difficulty": d.get("engineering_difficulty") or d.get("difficulty"),
                    "lead": d.get("lead"), "assistant": d.get("assistant"), "linked_items": linked, "materials": needs})
    return out


def _gold_history(conn: sqlite3.Connection, cid: int, gold_id: int | None, limit: int = 30) -> list[dict]:
    if gold_id is None:
        return []
    out = []
    for e in rows(conn, """SELECT id, type, summary, game_time_minutes, payload_json FROM events
                           WHERE campaign_id = ? AND visibility = 'player' AND rolled_back = 0
                           AND type IN ('item_added','item_removed','item_count_set','item_transferred') ORDER BY id DESC LIMIT 400""", (cid,)):
        p = e.get("payload") or {}
        if p.get("item_id") == gold_id or (e["type"] == "item_transferred" and str(p.get("item", "")).lower() == "gold"):
            delta = p.get("quantity") if e["type"] == "item_added" else -p["quantity"] if e["type"] == "item_removed" and p.get("quantity") else \
                (p["after"] - p["before"]) if e["type"] == "item_count_set" and p.get("before") is not None and p.get("after") is not None else None
            out.append({"id": e["id"], "summary": e["summary"], "delta": delta, "game_time_minutes": e["game_time_minutes"]})
            if len(out) >= limit:
                break
    return out


def build(conn: sqlite3.Connection, cid: int) -> dict:
    items = rows(conn, """SELECT i.id, i.owner_character_id, c.name AS owner_name, i.name, i.quantity, i.equipped, i.deployed, i.slot,
                                 i.rarity, i.description, i.properties_json
                          FROM items i LEFT JOIN characters c ON c.id = i.owner_character_id
                          WHERE i.campaign_id = ? ORDER BY i.owner_character_id IS NOT NULL, c.name, i.equipped DESC, i.name""", (cid,))
    for i in items:
        i.update(item_facts(i))
    quests = rows(conn, """SELECT id, kind, title, description, status, progress, details_json FROM quests
                           WHERE campaign_id = ? AND visibility = 'player' AND status IN ('active','paused')""", (cid,))
    gold = next((i for i in items if i["owner_character_id"] is None and i["name"].lower() == "gold"), None)
    reservations = _reservations(quests)
    return {
        "shared": [i for i in items if i["owner_character_id"] is None],
        "by_character": [i for i in items if i["owner_character_id"] is not None],
        "gold": {"amount": gold["quantity"] if gold else None, "item_id": gold["id"] if gold else None,
                 "committed": sum(r["paid_gold"] or 0 for r in reservations if r["payment"] == "paid" and not r["owned"]),
                 "history": _gold_history(conn, cid, gold["id"] if gold else None)},
        "reservations": reservations,
        "projects": _projects(quests, items),
        "materials": [{"id": i["id"], "name": i["name"], "quantity": i["quantity"], "owner": i["owner_name"]} for i in items if i["category"] == "material"],
    }


def pending_reservation(conn: sqlite3.Connection, cid: int, name: str) -> dict | None:
    """An active reservation for this item that hasn't been claimed: the item is not anyone's yet."""
    for q in rows(conn, "SELECT id, title, details_json FROM quests WHERE campaign_id = ? AND kind = 'reservation' AND status = 'active'", (cid,)):
        if _norm(re.sub(r"^Reserved:\s*", "", q["title"])) == _norm(name) and not (q.get("details") or {}).get("owned"):
            return q
    return None
