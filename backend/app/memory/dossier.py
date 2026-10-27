"""Character dossier: a structured, player-safe view of one character for the dossier UI.

Everything here is derived from the authoritative records (characters, skills, items, statuses, achievements, events,
player-visible quests and relationships) without changing them, inventing values or reading GM-only tables
(gm_secrets, GM-visibility events/quests/relationships). Missing data stays missing; the UI shows an empty state.
Classification (skill categories, equipment slots, effect types) only sorts records for display.
"""
from __future__ import annotations

import re
import sqlite3
from typing import Any

from ..db import one, rows
from .views import character_sheet, life_state

STAT_NAMES = {"strength": "STR", "intelligence": "INT", "constitution": "CON", "dexterity": "DEX", "charisma": "CHA"}

CATEGORY_WORDS: list[tuple[str, str]] = [
    ("combat", r"crossbow|bow\b|archery|quarterstaff|staff|unarmed|brawl|martial|sword|blade|dagger|knife|spear|javelin|club|axe|mace|"
               r"hammer|pistol|firearm|gun|rifle|throw|aiming|shield|dual wield|polearm|whip|natural weapon|bite|tail|claw|ambush|"
               r"grapple|wrestl|taunt|intervene|catcher|evasion|parry|net"),
    ("engineering", r"engineer|fabricat|repair|salvage|carving|craft|smith|alchemy|tinker|trap ?mak|demolition|mechanic|electronic|"
                    r"sewing|carpentry|construction|brew|cartograph|mapping"),
    ("social", r"negotiat|persua|intimidat|decept|bluff|leadership|perform|insight|barter|charm|diplomac|seduc|command|animal handling|"
               r"music|singing|acting"),
    ("exploration", r"investigat|stealth|survival|endurance|perception|detect|track|navigat|climb|swim|athlet|acrobat|first aid|"
                    r"medicine|lockpick|scaveng|forag|hid|sneak|search|scout|awareness|tracking|sense|smell"),
]
CATEGORY_LABELS = {"signature": "Signature / Specialties", "combat": "Combat", "engineering": "Engineering / Crafting",
                   "social": "Social", "exploration": "Exploration / Survival", "magic": "Spells", "gear": "Passive / Gear-granted",
                   "other": "Other"}

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


# ------------------------------------------------------------------ skills
def skill_category(s: dict) -> str:
    if s["kind"] == "spell":
        return "magic"
    if s["kind"] == "equipment_skill" or s.get("passive"):
        return "gear" if s["kind"] == "equipment_skill" else _named_category(s["name"]) or "gear"
    return _named_category(s["name"]) or "other"


def _named_category(name: str) -> str | None:
    for cat, pat in CATEGORY_WORDS:
        if re.search(pat, name, re.I):
            return cat
    return None


def _skill_view(s: dict, attacks: list[dict]) -> dict:
    p = s.get("properties") or {}
    upgrades = {k: v for k, v in p.items() if re.match(r"rank_\d+_upgrade|upgrade", k)}
    attack = next((a for a in attacks if (a.get("skill") or a.get("base_skill") or "").lower() == s["name"].lower()), None)
    detail = {k: v for k, v in p.items() if k not in ("specialty", "perk") and k not in upgrades}
    return {"id": s["id"], "name": s["name"], "rank": s["rank"], "kind": s["kind"], "category": skill_category(s),
            "stat": STAT_NAMES.get(str((attack or {}).get("governing_stat") or "").lower()),
            "learned": s["kind"] in ("skill", "spell"), "gear_granted": s["kind"] == "equipment_skill", "passive": bool(s.get("passive")),
            "marked": bool(s.get("marked")), "mana_cost": s.get("mana_cost"), "description": s.get("description"),
            "specialty": p.get("specialty"), "perk": p.get("perk"), "upgrades": [str(v) for v in upgrades.values()],
            "source": p.get("source"), "condition": p.get("condition"), "provenance": p.get("provenance"),
            "details": detail, "attack": attack}


def _signature_score(sk: dict) -> float:
    return (sk["rank"] or 0) * 2 + (3 if sk["specialty"] or sk["perk"] else 0) + (2 if sk["upgrades"] else 0) + (1 if sk["marked"] else 0)


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
def build(conn: sqlite3.Connection, cid: int, char_id: int) -> dict | None:
    c = character_sheet(conn, cid, char_id)
    if not c:
        return None
    sheet = c.get("sheet") or {}
    narrative = sheet.get("narrative") or {}
    derived = c.get("derived") or {}
    st = c.get("stats") or {}
    attacks = [dict(a, key=k) for k, a in (sheet.get("attacks") or {}).items() if isinstance(a, dict)] \
        if isinstance(sheet.get("attacks"), dict) else [a for a in _list(sheet.get("attacks")) if isinstance(a, dict)]
    group = one(conn, "SELECT key, label, location, activity FROM party_groups WHERE campaign_id = ? AND key = ?", (cid, c.get("assignment"))) \
        if c.get("assignment") else None
    camp = one(conn, "SELECT active_character_id FROM campaigns WHERE id = ?", (cid,))

    # attributes
    vals, mods, base = st.get("stats") or {}, st.get("modifiers") or {}, st.get("unenhanced") or {}
    top = max((v for v in vals.values() if isinstance(v, (int, float))), default=None)
    attributes = [{"key": k, "abbr": a, "name": k.title(), "value": vals.get(k), "modifier": mods.get(k),
                   "unenhanced": base.get(k) if base.get(k) is not None and base.get(k) != vals.get(k) else None,
                   "standout": top is not None and vals.get(k) == top} for k, a in STAT_NAMES.items() if k in vals or k in mods]

    # skills
    skills = [_skill_view(s, attacks) for s in c["skills"]]
    ranked = sorted((s for s in skills if (s["rank"] or 0) > 0), key=_signature_score, reverse=True)
    signature_ids = [s["id"] for s in ranked[:6]]
    for s in skills:
        s["signature"] = s["id"] in signature_ids or bool(s["specialty"] or s["perk"])

    # signature build: class and race records plus skill specialties, exactly as recorded
    sel = sheet.get("floor_3_selection") or {}
    klass, race = sel.get("class") if isinstance(sel.get("class"), dict) else {}, sel.get("race") if isinstance(sel.get("race"), dict) else {}
    highlights = []
    for k, v in (klass or {}).items():
        if k not in ("name", "types", "benefits", "detriments", "custom_build_points", "source") and isinstance(v, str):
            highlights.append({"title": k.replace("_", " ").title(), "detail": v, "source": "class feature"})
    # the ledger's own definitions of custom class features ("class_features": {"clear_lane": "Reduced penalties when..."})
    features = {_norm(k): str(v) for k, v in ((klass or {}).get("class_features") or {}).items() if isinstance(v, str)} \
        if isinstance((klass or {}).get("class_features"), dict) else {}
    for b in _list((klass or {}).get("benefits")):
        text = b.get("benefit") if isinstance(b, dict) else str(b)
        if text and not re.match(r"^[+-]\d+\s+\w+$", text.strip()) and not any(h["title"].lower() == text.lower() for h in highlights):
            highlights.append({"title": text, "detail": features.pop(_norm(text), None), "source": "class benefit"})
    for k, v in features.items():  # defined features with no matching benefit line
        highlights.append({"title": k.title(), "detail": v, "source": "class feature"})
    for s in ranked:
        if s["specialty"]:
            highlights.append({"title": s["specialty"], "detail": f"{s['name']} specialty (Rank {s['rank']})", "source": "skill specialty"})
        if s["perk"]:
            highlights.append({"title": s["perk"], "detail": f"{s['name']} perk", "source": "skill perk"})
    build_info = {"class_name": c.get("class_name") or (klass or {}).get("name"), "class_types": _list((klass or {}).get("types")),
                  "class_benefits": [b.get("benefit") if isinstance(b, dict) else str(b) for b in _list((klass or {}).get("benefits"))],
                  "class_detriments": [d.get("detriment") if isinstance(d, dict) else str(d) for d in _list((klass or {}).get("detriments"))],
                  "race": (race or {}).get("name") or c.get("race"),
                  "race_benefits": [str(b.get("benefit")) if isinstance(b, dict) else str(b) for b in _list((race or {}).get("benefits"))],
                  "role": narrative.get("current_role") or c.get("bio"), "tactics": [str(t) for t in _list(narrative.get("tactical_tendencies"))],
                  "highlights": highlights}

    # items
    items = [_item_view(i) for i in c["items"]]
    equipped = [i for i in items if i["equipped"]]
    shared = rows(conn, "SELECT id, name, quantity, equipped, deployed, slot, rarity, description, properties_json FROM items "
                        "WHERE campaign_id = ? AND owner_character_id IS NULL", (cid,))
    linked = [_item_view(i) for i in shared if _mentions(i["name"] + " " + _flat(i.get("properties")), c["name"])]
    for i in linked:
        i["shared"] = True
    quests = rows(conn, """SELECT id, kind, title, description, status, progress, details_json, due_at_minutes, owner_character_id FROM quests
                           WHERE campaign_id = ? AND visibility = 'player' AND status IN ('active','paused')""", (cid,))
    mine = [q for q in quests if q.get("owner_character_id") == char_id
            or _mentions(" ".join(str(q.get(k) or "") for k in ("title", "description", "progress")) + " " + _flat(q.get("details")), c["name"])]
    for q in mine:
        (q.get("details") or {}).pop("ledger_source", None)

    # effects, each with exactly one type
    effects = []
    for h in highlights:
        if h["source"] in ("class feature", "class benefit"):
            effects.append({"type": "class", "name": h["title"], "detail": h["detail"]})
    for b in build_info["race_benefits"]:
        effects.append({"type": "race", "name": b, "detail": f"{build_info['race']} race"})
    for s in skills:
        for label, val in (("specialty", s["specialty"]), ("perk", s["perk"])):
            if val:
                effects.append({"type": "specialty", "name": val, "detail": f"{s['name']} {label}"})
        for u in s["upgrades"]:
            effects.append({"type": "learned", "name": f"{s['name']} upgrade", "detail": u})
        if s["gear_granted"]:
            effects.append({"type": "equipment", "name": f"{s['name']} {s['rank']}", "detail": s["source"] or "granted by equipment"})
    for i in equipped:
        for e in i["effects"]:
            effects.append({"type": "equipment", "name": i["name"], "detail": e})
    for x in c.get("statuses") or []:
        effects.append({"type": "status", "name": x.get("name"), "detail": x.get("description") or x.get("polarity"), "polarity": x.get("polarity")})
    for u in c.get("limited_uses") or []:
        effects.append({"type": "limited", "name": u["ability"], "detail": f"{u['uses']}/{u['uses_allowed']} per {str(u['per']).replace('_', ' ')}"})
    for k, h in (sheet.get("hud_upgrades") or {}).items():
        if isinstance(h, dict):
            effects.append({"type": "equipment", "name": h.get("name") or k, "detail": "; ".join(map(str, _list(h.get("effects"))))})

    # story: player-visible records only (sheet narrative, public relationships and profile, visual reference)
    bg = narrative.get("background") if isinstance(narrative.get("background"), dict) else {}
    story = {"role": narrative.get("current_role") or c.get("bio"), "personality": narrative.get("personality_summary"),
             "traits": [str(t) for t in _list(narrative.get("traits"))], "tactics": build_info["tactics"],
             "background": {k: v for k, v in bg.items() if k not in ("past_trauma", "loose_end", "regret")},
             "past_trauma": bg.get("past_trauma"), "loose_end": bg.get("loose_end"), "regret": bg.get("regret"),
             "other": {k: v for k, v in narrative.items() if k not in ("current_role", "personality_summary", "traits", "tactical_tendencies", "background")},
             "relationships": c.get("relationships") or [], "profile": c.get("npc_profile"), "quests": mine,
             "appearance": ((sheet.get("visual") or {}).get("reference") or None),
             "assignment": group, "location": (group or {}).get("location") or c.get("location"),
             "identity": sheet.get("identity") if isinstance(sheet.get("identity"), dict) else None}

    # history: recorded events and notes only
    history = []
    for e in rows(conn, """SELECT id, type, summary, game_time_minutes, created_at, payload_json FROM events WHERE campaign_id = ?
                           AND visibility = 'player' AND rolled_back = 0 ORDER BY id DESC LIMIT 400""", (cid,)):
        payload = e.get("payload") or {}
        if payload.get("character_id") == char_id or _mentions(e["summary"], c["name"]):
            history.append({"kind": _history_kind(e["type"] + " " + e["summary"]), "text": e["summary"], "when": e.get("game_time_minutes"),
                            "at": e.get("created_at"), "source": "play"})
    for a in c.get("achievements") or []:
        history.append({"kind": "achievement", "text": f"{a['title']}: {a.get('description') or ''}".strip(": "), "when": a.get("game_time_minutes"), "source": "achievement"})
    for n in _list(sheet.get("reconciliation_notes")):
        history.append({"kind": _history_kind(str(n)), "text": str(n), "when": None, "source": "ledger note"})
    for s in skills:
        if s["provenance"]:
            history.append({"kind": "skill", "text": f"{s['name']} (Rank {s['rank']}): {s['provenance']}", "when": None, "source": "skill record"})
    for i in items:
        if i["provenance"]:
            history.append({"kind": "item", "text": f"{i['name']}: {i['provenance']}", "when": None, "source": "item record"})
    lp = derived.get("level_progress") if isinstance(derived.get("level_progress"), dict) else {}
    if lp.get("note"):
        history.append({"kind": "level", "text": str(lp["note"]), "when": None, "source": "level record"})

    hb = derived.get("health_bar") if isinstance(derived.get("health_bar"), dict) else {}
    size = derived.get("size") if isinstance(derived.get("size"), dict) else {"value": derived.get("size")}
    combat = {"health": {"current": c.get("hp_current"), "max": c.get("hp_max"), "slots": hb.get("slots"), "per_slot": hb.get("health_per_slot")},
              "mana": {"current": c.get("mana_current"), "max": c.get("mana_max")}, "evade": derived.get("evade"),
              "damage_resistance": derived.get("damage_resistance"), "move": derived.get("move"), "step": derived.get("step"),
              "size": size, "resistances": _list(derived.get("resistances") or sheet.get("resistances")), "statuses": c.get("statuses") or [],
              "ai_favor": c.get("ai_favor"), "attacks": attacks, "level_progress": lp or None,
              "unallocated_stat_points": derived.get("unallocated_stat_points")}

    return {"id": c["id"], "slug": c["slug"], "name": c["name"], "kind": c["kind"], "is_player": camp and camp["active_character_id"] == c["id"],
            "crawler_number": c.get("crawler_number"), "level": c.get("level"), "race": c.get("race") or c.get("species"),
            "class_name": c.get("class_name"), "status": c.get("status"), "life": life_state(c), "in_party": bool(c.get("in_party")), "roster_category": c.get("roster_category"),
            "portrait_path": c.get("portrait_path"), "portrait_crop": c.get("portrait_crop"), "role": build_info["role"],
            "group": group, "location": story["location"], "attributes": attributes, "combat": combat, "build": build_info,
            "skills": skills, "signature_skill_ids": signature_ids, "category_labels": CATEGORY_LABELS,
            "effects": effects, "equipment": {"equipped": equipped, "carried": [i for i in items if not i["equipped"]], "linked_shared": linked,
                                              "commitments": mine, "slot_labels": SLOT_LABELS},
            "story": story, "history": history, "hotlist": _list(sheet.get("hotlist")),
            # whether Generate has run / is locked by a hand-written private sheet (existence only, never its content)
            "private_sheet": (lambda s: None if not s else "generated" if s["source"] == "generated" else "authored")(
                one(conn, "SELECT source FROM gm_secrets WHERE campaign_id = ? AND kind = 'companion_sheet' AND subject = ?", (cid, c["slug"])))}


def _history_kind(text: str) -> str:
    t = text.lower()
    if re.search(r"\blevel\b", t):
        return "level"
    if re.search(r"injur|wound|dying|recover|heal", t):
        return "injury"
    if re.search(r"achievement", t):
        return "achievement"
    if re.search(r"item|box|loot|acquir|bought|purchas|equip", t):
        return "item"
    if re.search(r"rank|skill|advance", t):
        return "skill"
    return "milestone"
