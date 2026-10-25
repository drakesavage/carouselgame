"""Read-only importer for the Git-based DCC campaign ledger (drakesavage/dccrepository layout).

Principles
* Read-only: files are opened for reading only; every file is hashed before and after and the report proves
  the source is unchanged. Git is queried with read-only commands.
* No invention: unknown values stay NULL (e.g. item counts the ledger calls unquantified); nothing is defaulted
  to make a record look complete.
* Nothing lost: besides the structured mapping, every source file is stored verbatim in `source_sections`
  (GM-only for gm_state/**) so the GM can retrieve history that has no structured table yet.
* Conflicts and gaps are reported, not silently resolved. Where the ledger documents its own precedence
  (e.g. state/advancement.yaml is "the sole live cycle counter") the documented rule is applied and reported.
* Each import creates a NEW campaign; it never overwrites an existing one.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from ..db import now_iso, transaction
from ..memory.snapshots import create_save_point

ALWAYS_INCLUDE_SECTIONS = {"2", "3.1", "3.2", "4.1", "4.2", "5.5", "5.6", "5.10", "6.2", "6.3", "6.4", "7", "7.9", "7.10", "17", "18"}
IMPLEMENTED_SECTIONS = {"6", "6.1", "6.2", "6.3", "6.4", "7.1", "7.2", "7.3", "7.4", "7.5", "7.6", "7.7", "7.9", "14.3", "14.5", "16", "16.1", "16.2", "16.3",
                        "10.2", "10.3", "10.4", "10.5", "10.8", "11.1", "11.2"}
PARTIAL_SECTIONS = {"7.8": "Party Skill Checks are run as individual skill_check calls; the half-succeed aggregation is left to the GM.",
                    "8.1": "Game time is tracked via advance_time; dungeon-day boundaries are not yet computed.",
                    "14.2": "Only correspondence-cycle Levels are automated; Quest/Boss/grinding Level awards are manual.",
                    "14.4": "Floor 3+ checkpoint banks 3 Stat points; allocation is manual.",
                    "10.7": "Evade is mechanized; Intervene, Catcher, Taunt and Healing Interrupts are spent as Actions and resolved with general tools.",
                    "10.9": "Call a Play spends an Action; its 2d6-keep-high bonus is applied by the GM as a modifier.",
                    "10.11": "Mob-vs-Mob uses combat_damage or the d12 table via roll_dice.",
                    "11.3": "Minor Injury from Evade Major/Critical Fail is automatic; other injuries are applied by the GM."}
NARRATIVE_SECTIONS_PREFIX = ("1", "2", "3", "4", "5", "9", "17", "18", "19", "20")
IMPLEMENTED_RULINGS = {"rng_fallback", "correspondence_advancement", "transparency"}
EQUIPPED_KEYS = {"equipped", "wielded", "worn", "natural_armor"}
CARRIED_KEYS = {"carried", "hotlisted", "other", "owned_elsewhere", "deployed_elsewhere"}
MECH_TOP = {"health", "mana", "stats", "unenhanced_stats", "stat_modifiers", "skills", "spells", "equipment_granted_skills",
            "conditions", "injuries", "buffs", "debuffs", "advancement_marks", "ai_favor"}
SHEET_FROM_MECH = {"attacks", "hotlist", "reconciliation_notes", "stats_note", "utility_reference", "combat_profile", "defensive_reference",
                   "hud_upgrades", "continuity_reconciliation"}
CHAR_HANDLED = {"schema_version", "id", "display_name", "kind", "species", "crawler_number", "roster_category", "status", "level",
                "class", "location", "squad", "mechanics", "equipment", "inventory"}


@dataclass
class Report:
    source: dict = field(default_factory=dict)
    imported: dict = field(default_factory=dict)
    conflicts: list[dict] = field(default_factory=list)
    manual_review: list[dict] = field(default_factory=list)
    rules_pending: list[dict] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def count(self, key: str, n: int = 1) -> None:
        self.imported[key] = self.imported.get(key, 0) + n

    def to_dict(self) -> dict:
        return {"source": self.source, "imported": self.imported, "conflicts": self.conflicts,
                "manual_review": self.manual_review, "rules_pending_implementation": self.rules_pending, "notes": self.notes}


class LedgerSource:
    """Read-only view of a ledger directory that remembers the hash of everything it read."""

    def __init__(self, root: Path):
        self.root = root.resolve()
        self.hashes: dict[str, str] = {}

    def text(self, rel: str) -> str:
        data = (self.root / rel).read_bytes()
        self.hashes[rel] = hashlib.sha256(data).hexdigest()
        return data.decode("utf-8")

    def yaml(self, rel: str) -> Any:
        return yaml.safe_load(self.text(rel))

    def exists(self, rel: str) -> bool:
        return (self.root / rel).exists()

    def glob(self, pattern: str) -> list[str]:
        return sorted(p.relative_to(self.root).as_posix() for p in self.root.glob(pattern) if p.is_file())

    def verify_unchanged(self) -> list[str]:
        return [rel for rel, h in self.hashes.items() if hashlib.sha256((self.root / rel).read_bytes()).hexdigest() != h]

    def git(self, *args: str) -> str | None:
        try:
            out = subprocess.run(["git", "-C", str(self.root), *args], capture_output=True, text=True, timeout=20,
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            return out.stdout.strip() if out.returncode == 0 else None
        except (OSError, subprocess.TimeoutExpired):
            return None


def _int(v: Any) -> int | None:
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, str) and re.fullmatch(r"-?\d+", v.strip()):
        return int(v)
    return None


UNRESOLVED_RE = re.compile(r"^\s*(unresolved|unknown|unquantified)", re.I)


def _count_unresolved(it: dict) -> bool:
    """Ledger convention: a quantity flagged unresolved/unknown/unquantified is NOT a real count (even if 0)."""
    return any(isinstance(it.get(k), str) and UNRESOLVED_RE.match(it[k]) for k in ("count_status", "quantity_status"))


def _dump(v: Any) -> str:
    return yaml.safe_dump(v, allow_unicode=True, sort_keys=False, width=110).strip()


def _walk_strings(v: Any, path: str = ""):
    if isinstance(v, dict):
        for k, x in v.items():
            yield from _walk_strings(x, f"{path}.{k}" if path else str(k))
    elif isinstance(v, list):
        for i, x in enumerate(v):
            yield from _walk_strings(x, f"{path}[{i}]")
    elif isinstance(v, str):
        yield path, v


# ------------------------------------------------------------------ source sections
def _store_sections(conn: sqlite3.Connection, cid: int, rel: str, text: str, visibility: str) -> int:
    sections: list[tuple[str, str]] = []
    if rel.endswith((".yaml", ".yml")):
        try:
            data = yaml.safe_load(text)
        except yaml.YAMLError:
            data = None
        if isinstance(data, dict):
            for k, v in data.items():
                body = _dump({k: v})
                if len(body) > 12000 and isinstance(v, dict):
                    sections.extend((f"{k}.{k2}", _dump({k: {k2: v2}})) for k2, v2 in v.items())
                else:
                    sections.append((str(k), body))
        else:
            sections.append(("(file)", text))
    elif rel.endswith(".md"):
        parts = re.split(r"(?m)^(?=#{1,3} )", text)
        for part in parts:
            if part.strip():
                head = part.strip().splitlines()[0].lstrip("#").strip()
                sections.append((head[:120], part.strip()))
    else:
        sections.append(("(file)", text))
    for name, body in sections:
        sid = conn.execute("INSERT INTO source_sections (campaign_id, document_path, section, content, visibility) VALUES (?,?,?,?,?)",
                           (cid, rel, name, body, visibility)).lastrowid
        conn.execute("INSERT INTO source_sections_fts (rowid, section, content) VALUES (?,?,?)", (sid, f"{rel} {name}", body))
    return len(sections)


# ------------------------------------------------------------------ CORE_MECHANICS
def parse_core_mechanics(text: str) -> list[dict]:
    """Split into numbered sections (## N. / ### N.N / #### heading) with BOOK/CAMPAIGN/... label detection."""
    out = []
    current = None
    for line in text.splitlines():
        m = re.match(r"^(#{2,4})\s+(?:(\d+(?:\.\d+)*)\.?\s+)?(.*)$", line)
        if m:
            if current:
                out.append(current)
            number = m.group(2)
            if number is None and current:  # unnumbered #### subsection inherits parent number
                number = current["section"]
            current = {"section": number, "title": m.group(3).strip(), "lines": []}
        elif current is not None:
            current["lines"].append(line)
    if current:
        out.append(current)
    rules = []
    for s in out:
        body = "\n".join(s["lines"]).strip().strip("-").strip()
        if not body:
            continue
        labels = sorted(set(re.findall(r"\*\*(BOOK|CAMPAIGN(?: - [A-Z ]+)?|INTERPRETATION|ONE-SCENE|OPEN):?\*\*", body)))
        labels = sorted({l.split(" - ")[0] for l in labels})
        label = labels[0] if len(labels) == 1 else ("mixed" if labels else None)
        num = s["section"] or ""
        top = num.split(".")[0]
        if num in IMPLEMENTED_SECTIONS:
            mech = "implemented"
        elif num in PARTIAL_SECTIONS:
            mech = "partial"
        elif top in NARRATIVE_SECTIONS_PREFIX or num.startswith(("8.", "15")):
            mech = "narrative_only"
        else:
            mech = "pending"
        rules.append({"section": num, "title": s["title"], "text": body, "label": label, "mechanical_status": mech,
                      "always_include": num in ALWAYS_INCLUDE_SECTIONS})
    return rules


# ------------------------------------------------------------------ characters
def _status_entries(m: dict) -> list[tuple[str, str | None, str]]:
    out = []
    for key, polarity in (("conditions", "neutral"), ("injuries", "debuff"), ("buffs", "buff"), ("debuffs", "debuff")):
        for e in m.get(key) or []:
            if isinstance(e, str):
                out.append((e, None, polarity))
            elif isinstance(e, dict):
                out.append((str(e.get("name") or e.get("id") or key), _dump(e), polarity))
    return out


def _equipment_items(eq: Any) -> list[dict]:
    items = []
    if not isinstance(eq, dict):
        return items
    for key, entries in eq.items():
        if key not in EQUIPPED_KEYS | CARRIED_KEYS:
            continue
        for e in entries if isinstance(entries, list) else [entries]:
            if isinstance(e, str):
                items.append({"name": e, "quantity": None if key in ("other",) else 1, "equipped": key in EQUIPPED_KEYS, "slot": key,
                              "properties": {"source_key": key}})
            elif isinstance(e, dict):
                props = {k: v for k, v in e.items() if k not in ("id", "name", "quantity")}
                props["source_key"] = key
                q = None if _count_unresolved(e) else _int(e.get("quantity")) if "quantity" in e else 1
                items.append({"key": e.get("id"), "name": str(e.get("name") or e.get("id") or "unnamed"),
                              "quantity": q, "equipped": key in EQUIPPED_KEYS, "slot": key, "properties": props})
    return items


def _import_character(conn: sqlite3.Connection, cid: int, rel: str, d: dict, party: list[str], rep: Report) -> int:
    m = d.get("mechanics") or {}
    sel = d.get("floor_3_selection") or {}
    race = (sel.get("race") or {}).get("name") if isinstance(sel.get("race"), dict) else None
    hp, mana = m.get("health") or {}, m.get("mana") or {}
    derived = {k: v for k, v in m.items() if k not in MECH_TOP | SHEET_FROM_MECH}
    sheet = {k: v for k, v in d.items() if k not in CHAR_HANDLED}
    sheet.update({k: v for k, v in m.items() if k in SHEET_FROM_MECH})
    for k in ("ammunition_note", "rulings"):
        if isinstance(d.get("equipment"), dict) and k in d["equipment"]:
            sheet[f"equipment_{k}"] = d["equipment"][k]
    narrative = d.get("narrative") or {}
    kind = d.get("kind") or "npc"
    char_id = conn.execute(
        """INSERT INTO characters (campaign_id, slug, name, kind, roster_category, crawler_number, species, race, class_name, level,
                                   status, location, in_party, party_order, hp_current, hp_max, mana_current, mana_max, ai_favor,
                                   stats_json, derived_json, sheet_json, bio, provenance)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (cid, d["id"], d["display_name"], kind, d.get("roster_category"), None if d.get("crawler_number") is None else str(d["crawler_number"]),
         d.get("species"), race or d.get("species"), d.get("class"), _int(d.get("level")), str(d.get("status") or "unknown"), d.get("location"),
         int(d["id"] in party), party.index(d["id"]) if d["id"] in party else 999,
         _int(hp.get("current")), _int(hp.get("max")), _int(mana.get("current")), _int(mana.get("max")), _int(m.get("ai_favor")),
         json.dumps({"stats": m.get("stats") or {}, "modifiers": m.get("stat_modifiers") or {}, "unenhanced": m.get("unenhanced_stats") or {}}),
         json.dumps(derived, default=str), json.dumps(sheet, default=str), narrative.get("current_role"), f"ledger:{rel}"),
    ).lastrowid
    rep.count("characters")
    marks = set(m.get("advancement_marks") or [])
    for kind_key, skill_kind in (("skills", "skill"), ("spells", "spell"), ("equipment_granted_skills", "equipment_skill")):
        for key, s in (m.get(kind_key) or {}).items():
            if not isinstance(s, dict):
                s = {"name": str(key), "rank": s}
            props = {k: v for k, v in s.items() if k not in ("name", "rank", "mana_cost")}
            conn.execute("""INSERT INTO skills (character_id, kind, key, name, rank, mana_cost, marked, properties_json)
                            VALUES (?,?,?,?,?,?,?,?)""",
                         (char_id, skill_kind, key, str(s.get("name") or key), _int(s.get("rank")), _int(s.get("mana_cost")),
                          int(key in marks), json.dumps(props, default=str)))
            rep.count("skills_spells")
    unknown_marks = marks - set((m.get("skills") or {}).keys()) - set((m.get("spells") or {}).keys())
    if unknown_marks:
        rep.manual_review.append({"record": rel, "issue": f"advancement_marks reference unknown skills: {sorted(unknown_marks)}"})
    for name, desc, pol in _status_entries(m):
        conn.execute("INSERT INTO status_effects (character_id, name, description, polarity, source) VALUES (?,?,?,?,?)",
                     (char_id, name, desc, pol, f"ledger:{rel}"))
        rep.count("status_effects")
    for it in _equipment_items(d.get("equipment")) + [
        {"key": i.get("id"), "name": str(i.get("name") or i.get("id")),
         "quantity": None if _count_unresolved(i) else _int(i.get("quantity")) if "quantity" in i else None,
         "equipped": str(i.get("equip_status", "")).lower() == "equipped", "slot": None,
         "properties": {k: v for k, v in i.items() if k not in ("id", "name", "quantity")}} for i in (d.get("inventory") or []) if isinstance(i, dict)]:
        if it["quantity"] == 0:
            rep.notes.append(f"{d['id']}: skipped zero-quantity item '{it['name']}' (consumed/resolved per ledger)")
            continue
        conn.execute("""INSERT INTO items (campaign_id, owner_character_id, item_key, name, quantity, equipped, slot, rarity, description, properties_json)
                        VALUES (?,?,?,?,?,?,?,?,?,?)""",
                     (cid, char_id, it.get("key"), it["name"], it["quantity"], int(it["equipped"]), it["slot"],
                      (it["properties"] or {}).get("rarity"), None, json.dumps(it["properties"], default=str)))
        rep.count("character_items")
        if it["quantity"] is None:
            rep.manual_review.append({"record": rel, "issue": f"item '{it['name']}' has no established quantity (imported as unknown)"})
    if d.get("roster_category") != "player_character":
        att = None
        conn.execute("INSERT INTO npc_profiles (character_id, personality, known_motivation, attitude) VALUES (?,?,?,?)",
                     (char_id, narrative.get("personality_summary"), narrative.get("current_role"), att))
    return char_id


# ------------------------------------------------------------------ main
def import_ledger(conn: sqlite3.Connection, root: Path, *, name_suffix: str = "") -> tuple[int | None, dict]:
    src = LedgerSource(root)
    rep = Report()
    rep.source = {"path": str(src.root), "imported_at": now_iso(), "git_head": src.git("rev-parse", "HEAD"),
                  "git_head_date": src.git("log", "-1", "--format=%cI"), "git_commits": src.git("rev-list", "--count", "HEAD"),
                  "git_remote": src.git("remote", "get-url", "origin"), "git_dirty": bool(src.git("status", "--porcelain"))}
    if not src.exists("state/campaign.yaml"):
        rep.notes.append("state/campaign.yaml not found: this is not a DCC campaign ledger. Nothing imported.")
        return None, rep.to_dict()
    camp = src.yaml("state/campaign.yaml") or {}
    if camp.get("lifecycle") != "active":
        rep.notes.append(f"Ledger lifecycle is '{camp.get('lifecycle')}': migration into the ledger was never completed, "
                         "so it holds no campaign facts. Nothing imported (no data was invented).")
        rep.source["unchanged_after_import"] = not src.verify_unchanged()
        return None, rep.to_dict()

    roster = src.yaml("state/roster.yaml") if src.exists("state/roster.yaml") else {}
    shared = src.yaml("state/shared_inventory.yaml") if src.exists("state/shared_inventory.yaml") else {}
    adv = src.yaml("state/advancement.yaml") if src.exists("state/advancement.yaml") else None
    squads = (roster or {}).get("squads") or {}
    active_squad = camp.get("active_squad")
    party = list((squads.get(active_squad) or {}).get("members") or [])
    if active_squad and not party:
        rep.manual_review.append({"record": "state/roster.yaml", "issue": f"active squad '{active_squad}' has no member list"})

    with transaction(conn):
        fc = camp.get("floor_clock") or {}
        dti = camp.get("dungeon_time_inventory") or {}
        remaining = _int(fc.get("remaining_minutes"))
        elapsed = _int(dti.get("elapsed_floor_minutes"))
        if remaining is None:
            rep.manual_review.append({"record": "state/campaign.yaml", "issue": "floor_clock.remaining_minutes is missing; floor clock imported as unknown"})
        if elapsed is None:
            rep.notes.append("Elapsed floor minutes not recorded; game time starts at 0 with the deadline set from remaining minutes.")
        deadline = None if remaining is None else (elapsed or 0) + remaining
        prev = _int(fc.get("previous_remaining_minutes"))
        if prev is not None and elapsed is not None and remaining is not None and prev != elapsed + remaining:
            rep.conflicts.append({"topic": "floor clock arithmetic", "values": {"previous_remaining": prev, "elapsed": elapsed, "remaining": remaining},
                                  "resolution": "imported remaining_minutes as authoritative; please verify"})
        location = camp.get("current_location")
        if camp.get("current_area") and location:
            location = f"{location} — {camp['current_area']}"
        cid = conn.execute(
            """INSERT INTO campaigns (name, is_demo, data_source, current_floor, current_location, safe_room, game_time_minutes,
                                      floor_deadline_minutes, active_objective, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            ((camp.get("campaign_name") or camp.get("campaign_id") or "Imported campaign") + name_suffix, 0, "ledger_import",
             _int(camp.get("current_floor")), location, int(str(camp.get("safe_room_status")).lower() == "true"), elapsed or 0, deadline,
             camp.get("active_objective"), now_iso(), now_iso()),
        ).lastrowid
        rep.count("campaigns")

        # characters
        files = [f for f in src.glob("state/characters/*.yaml")]
        by_slug: dict[str, int] = {}
        sheets: dict[str, dict] = {}
        for rel in files:
            d = src.yaml(rel)
            if not isinstance(d, dict) or not d.get("id") or not d.get("display_name"):
                rep.manual_review.append({"record": rel, "issue": "missing id/display_name; not imported"})
                continue
            if d["id"] in by_slug:
                rep.conflicts.append({"topic": "duplicate character id", "values": {"id": d["id"], "file": rel}, "resolution": "second file skipped"})
                continue
            sheets[d["id"]] = d
            by_slug[d["id"]] = _import_character(conn, cid, rel, d, party, rep)
        pcs = [s for s, d in sheets.items() if d.get("roster_category") == "player_character"]
        if len(pcs) == 1:
            conn.execute("UPDATE campaigns SET active_character_id = ? WHERE id = ?", (by_slug[pcs[0]], cid))
        else:
            rep.manual_review.append({"record": "state/characters", "issue": f"expected exactly one player_character, found {pcs}"})

        # roster cross-checks
        cats = (roster or {}).get("categories") or {}
        listed: dict[str, str] = {}
        for cat, ids in cats.items():
            for i in ids or []:
                listed[i] = cat
        for slug, d in sheets.items():
            if slug not in listed:
                rep.conflicts.append({"topic": "roster membership", "values": {"character": slug}, "resolution": "has a sheet but is not in roster.yaml categories"})
            elif listed[slug] != d.get("roster_category"):
                cat = listed[slug]
                consistent = cat == "deceased_npcs" and str(d.get("status")).lower() == "deceased"
                rep.conflicts.append({"topic": "roster category", "values": {"character": slug, "roster.yaml": cat, "sheet": d.get("roster_category")},
                                      "resolution": "sheet value imported" + ("; roster category reflects death (consistent with status)" if consistent else "; please reconcile")})
        for slug in listed:
            if slug not in sheets:
                rep.conflicts.append({"topic": "roster membership", "values": {"character": slug}, "resolution": "listed in roster.yaml but has no character sheet"})
        for slug in party:
            if slug not in sheets:
                rep.manual_review.append({"record": "state/roster.yaml", "issue": f"active-squad member '{slug}' has no sheet"})
        for slug, d in sheets.items():
            m = d.get("mechanics") or {}
            hp = m.get("health") or {}
            if _int(hp.get("current")) is not None and _int(hp.get("max")) is not None and hp["current"] > hp["max"]:
                rep.conflicts.append({"topic": "health above maximum", "values": {"character": slug, "health": hp}, "resolution": "imported as recorded"})
            if slug in party and d.get("kind") == "crawler":
                missing = [k for k in ("health", "stat_modifiers", "skills") if not m.get(k)]
                if missing or _int(d.get("level")) is None:
                    rep.manual_review.append({"record": f"state/characters/{slug}.yaml", "issue": f"field-ready crawler sheet missing {missing or ['level']}"})
            box_a = ((d.get("floor_3_selection") or {}).get("silver_earth_box") or {}).get("status")
            box_b = (d.get("floor_3_silver_earth_box") or {}).get("status")
            if box_a and box_b and box_a != box_b:
                rep.conflicts.append({"topic": "Silver Earth Box status", "values": {"character": slug, "floor_3_selection.silver_earth_box": box_a,
                                                                                     "floor_3_silver_earth_box": box_b},
                                      "resolution": "kept both in the sheet; the later floor_3_silver_earth_box record appears to supersede the selection-time note"})

        # shared inventory
        for section, deployed in (("items", 0), ("deployed_assets", 1), ("unquantified_items", 0)):
            for it in (shared or {}).get(section) or []:
                if not isinstance(it, dict):
                    continue
                q = None if section == "unquantified_items" or _count_unresolved(it) else _int(it.get("quantity"))
                if section != "unquantified_items" and "quantity" not in it:
                    q = None
                props = {k: v for k, v in it.items() if k not in ("id", "name", "quantity")}
                if q == 0:
                    rep.notes.append(f"shared: skipped zero-quantity '{it.get('name')}'")
                    continue
                conn.execute("""INSERT INTO items (campaign_id, owner_character_id, item_key, name, quantity, deployed, description, properties_json)
                                VALUES (?,?,?,?,?,?,?,?)""",
                             (cid, None, it.get("id"), str(it.get("name") or it.get("id")), q, deployed, it.get("note"), json.dumps(props, default=str)))
                rep.count("shared_items")
                if q is None:
                    rep.manual_review.append({"record": "state/shared_inventory.yaml", "issue": f"'{it.get('name')}' quantity not established (imported as unknown)"})

        # objectives → quests
        for o in camp.get("open_objectives") or []:
            if not isinstance(o, dict) or not o.get("summary"):
                continue
            status = {"open": "active", "active": "active", "complete": "completed", "completed": "completed", "paused": "paused"}.get(str(o.get("status")).lower(), "active")
            kind = "project" if re.search(r"\bbuild|craft|siphon|improve\b", o["summary"], re.I) else "quest"
            conn.execute("INSERT INTO quests (campaign_id, kind, title, description, status, priority, progress, updated_at) VALUES (?,?,?,?,?,?,?,?)",
                         (cid, kind, o["summary"], f"Ledger objective id: {o.get('id')}", status, 2, None, now_iso()))
            rep.count("quests_objectives")
        rep.notes.append("Objectives carry no priority in the ledger; all were imported at the default priority 2.")

        for note in camp.get("world_state_notes") or []:
            conn.execute("INSERT INTO lore_facts (campaign_id, subject, fact, certainty, visibility, source, created_at) VALUES (?,?,?,?,?,?,?)",
                         (cid, "world state", str(note), "canonical", "player", "state/campaign.yaml world_state_notes", now_iso()))
            rep.count("lore_facts")

        # house rules
        for key, val in (camp.get("campaign_rulings") or {}).items():
            text = "\n".join(map(str, val)) if isinstance(val, list) else _dump(val)
            mech = "implemented" if key in IMPLEMENTED_RULINGS else "narrative_only" if key == "legacy_adventure_beat_tracker" else "pending"
            conn.execute("INSERT INTO house_rules (campaign_id, section, title, text, label, status, mechanical_status, source) VALUES (?,?,?,?,?,?,?,?)",
                         (cid, None, key.replace("_", " ").title(), text, "CAMPAIGN", "canonical", mech, "state/campaign.yaml campaign_rulings"))
            rep.count("house_rules")
            if mech == "pending":
                rep.rules_pending.append({"rule": key, "source": "campaign_rulings", "text": text[:300]})
        if src.exists("CORE_MECHANICS.md"):
            for r in parse_core_mechanics(src.text("CORE_MECHANICS.md")):
                conn.execute("""INSERT INTO house_rules (campaign_id, section, title, text, label, always_include, status, mechanical_status, source)
                                VALUES (?,?,?,?,?,?,?,?,?)""",
                             (cid, r["section"], r["title"], r["text"], r["label"], int(r["always_include"]), "canonical", r["mechanical_status"], "CORE_MECHANICS.md"))
                rep.count("house_rules")
                if r["mechanical_status"] in ("pending", "partial"):
                    rep.rules_pending.append({"rule": f"§{r['section']} {r['title']}", "source": "CORE_MECHANICS.md", "status": r["mechanical_status"],
                                              "note": PARTIAL_SECTIONS.get(r["section"], "")})
        else:
            rep.manual_review.append({"record": "CORE_MECHANICS.md", "issue": "not found; no campaign constitution imported"})

        # advancement tracker (documented: state/advancement.yaml is the sole live counter)
        if adv:
            conn.execute("INSERT INTO clocks (campaign_id, key, label, kind, current, maximum, unit) VALUES (?,?,?,?,?,?,?)",
                         (cid, "advancement_cycles", "Advancement cycles", "advancement_checkpoint", _int(adv.get("cycles_current")) or 0,
                          _int(adv.get("cycles_required")) or 12, "cycles"))
            rep.count("clocks")
            legacy = ((camp.get("current_state") or {}).get("advancement_cycles"))
            live = f"{adv.get('cycles_current')}/{adv.get('cycles_required')}"
            if legacy and str(legacy) != live:
                rep.conflicts.append({"topic": "advancement cycles", "values": {"state/campaign.yaml current_state": legacy, "state/advancement.yaml": live},
                                      "resolution": "state/advancement.yaml imported: PLAY_PROTOCOL declares it the sole live counter"})
            if adv.get("checkpoint_pending"):
                rep.manual_review.append({"record": "state/advancement.yaml", "issue": "an advancement checkpoint is pending resolution"})
        # cross-file clock / gold consistency
        for rel in src.glob("state/*.yaml"):
            if rel in ("state/campaign.yaml", "state/roster.yaml", "state/shared_inventory.yaml", "state/advancement.yaml"):
                continue
            other = src.yaml(rel) or {}
            clk = other.get("clock") if isinstance(other, dict) else None
            if isinstance(clk, dict):
                after = _int(clk.get("after_minutes_remaining"))
                before = _int(clk.get("before_minutes_remaining"))
                if remaining is not None and after is not None and after != remaining:
                    rep.conflicts.append({"topic": "floor clock", "values": {"state/campaign.yaml remaining": remaining, f"{rel} before": before, f"{rel} after": after},
                                          "resolution": "imported state/campaign.yaml value; the newer snapshot suggests the clock was not updated — confirm and use set time manually"})
            tx = other.get("transaction") if isinstance(other, dict) else None
            gold = next((i for i in (shared or {}).get("items") or [] if isinstance(i, dict) and str(i.get("id")) == "gold"), None)
            if isinstance(tx, dict) and gold and _int(tx.get("gold_after")) is not None and _int(tx.get("gold_after")) != _int(gold.get("quantity")):
                rep.conflicts.append({"topic": "gold", "values": {"state/shared_inventory.yaml": gold.get("quantity"), f"{rel} gold_after": tx.get("gold_after")},
                                      "resolution": "imported shared_inventory value; confirm whether the purchase in the snapshot happened"})
        # unresolved markers anywhere in player-visible state
        for rel in ["state/campaign.yaml", "state/shared_inventory.yaml"] + files:
            data = src.yaml(rel)
            seen = 0
            for path, s in _walk_strings(data):
                leaf = re.split(r"[.\[]", path)[-1].lower()
                status_field = any(k in leaf for k in ("status", "quantity", "count", "bolts", "rounds"))
                if status_field and UNRESOLVED_RE.match(s) and seen < 8 and not path.startswith(("historical", "floor_2", "level_advancement")):
                    rep.manual_review.append({"record": rel, "path": path, "issue": s[:220]})
                    seen += 1
        rep.manual_review.append({"record": "state/characters/*", "issue": "The ledger never marks Skills as Passive. Passive Skills (e.g. Pathfinder) must be "
                                                                           "flagged by the GM when checking; consider adding `passive: true` to sheets."})

        # GM-only material
        gm_files = src.glob("gm_state/characters/*.yaml")
        for rel in gm_files:
            text = src.text(rel)
            try:
                slug = (yaml.safe_load(text) or {}).get("character_id") or Path(rel).stem
            except yaml.YAMLError:
                slug = Path(rel).stem
            conn.execute("INSERT INTO gm_secrets (campaign_id, subject, kind, content, source, created_at) VALUES (?,?,?,?,?,?)",
                         (cid, slug, "companion_sheet", text, f"ledger:{rel}", now_iso()))
            rep.count("gm_companion_sheets")
            if slug not in sheets:
                rep.manual_review.append({"record": rel, "issue": f"GM companion sheet for '{slug}' has no matching canonical sheet"})

        # roll logs
        for rel, hidden in (("rolls/public.jsonl", 0), ("gm_state/rolls.jsonl", 1)):
            if not src.exists(rel):
                continue
            for n, line in enumerate(src.text(rel).splitlines(), 1):
                if not line.strip():
                    continue
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    rep.manual_review.append({"record": rel, "issue": f"line {n} is not valid JSON"})
                    continue
                raw = _int(r.get("raw"))
                sides = _int(str(r.get("die", "")).lstrip("d"))
                outcome_text = str(r.get("outcome", ""))
                outcome = "success" if outcome_text.lower().startswith("success") else "failure" if outcome_text.lower().startswith("fail") else None
                total = _int(r.get("total")) if r.get("total") is not None else raw
                conn.execute("""INSERT INTO dice_rolls (campaign_id, turn_id, character_id, expression, dice_json, modifier, total, target, outcome,
                                                       reason, degree, detail_json, hidden, rng, created_at)
                                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                             (cid, None, by_slug.get(str(r.get("actor"))), str(r.get("die") or "?"),
                              json.dumps([{"sides": sides, "value": raw, "kept": True}] if raw is not None and sides else []),
                              0, total if total is not None else 0, _int(r.get("target")), outcome,
                              f"[imported] {r.get('check', '')} — {r.get('event', '')}", outcome_text[:80] or None, json.dumps(r, default=str),
                              hidden, str(r.get("rng_source") or "imported (rng source not recorded)"), r.get("date") or now_iso()))
                rep.count("imported_rolls")

        # verbatim records
        for rel in src.glob("*.md") + src.glob("state/*.yaml") + files + src.glob("checkpoints/*.md") + src.glob("gm_state/*.md") + gm_files + src.glob("gm_state/characters/*.md"):
            vis = "gm" if rel.startswith("gm_state/") else "player"
            rep.count("record_sections_gm" if vis == "gm" else "record_sections", _store_sections(conn, cid, rel, src.text(rel), vis))
        from .organization import seed_play_state
        state_docs = {rel: src.yaml(rel) for rel in src.glob("state/*.yaml")}
        latest_md = src.text("checkpoints/latest.md") if src.exists("checkpoints/latest.md") else None
        rep.notes.extend(seed_play_state(conn, cid, state_docs, latest_md))
        from .visual_reference import sync_bible
        vb = sync_bible(conn, cid, src.root, rep.source.get("git_head"))
        if vb.get("updated"):
            rep.notes.append(f"Visual bible loaded for {len(vb['updated'])} characters")
        create_save_point(conn, cid, "Imported from campaign ledger", kind="import")
    changed = src.verify_unchanged()
    rep.source["files_read"] = len(src.hashes)
    rep.source["unchanged_after_import"] = not changed
    if changed:
        rep.conflicts.append({"topic": "source changed during import", "values": {"files": changed}, "resolution": "re-run the import"})
    rep.notes.append("checkpoints/latest.md is a derived view and was stored for reference only; canonical YAML was imported.")
    conn.execute("INSERT INTO import_reports (campaign_id, source_path, report_json, created_at) VALUES (?,?,?,?)",
                 (cid, str(src.root), json.dumps(rep.to_dict(), default=str), now_iso()))
    return cid, rep.to_dict()


def report_markdown(report: dict) -> str:
    """Human-readable migration report. Contains file names and counts for GM-only material, never its content."""
    s = report["source"]
    lines = ["# Campaign migration report", "",
             f"- Source: `{s.get('path')}`", f"- Git: {s.get('git_remote') or 'no remote'} @ `{(s.get('git_head') or '?')[:12]}` "
             f"({s.get('git_head_date') or '?'}, {s.get('git_commits') or '?'} commits{', uncommitted changes present' if s.get('git_dirty') else ''})",
             f"- Imported at: {s.get('imported_at')}", f"- Files read: {s.get('files_read', 0)}; source unchanged after import: **{s.get('unchanged_after_import')}**", "",
             "## Successfully imported", ""]
    lines += [f"- {k.replace('_', ' ')}: {v}" for k, v in report["imported"].items()] or ["- nothing"]
    lines += ["", f"## Conflicting or ambiguous records ({len(report['conflicts'])})", ""]
    for c in report["conflicts"]:
        lines.append(f"- **{c['topic']}** — {json.dumps(c['values'], ensure_ascii=False, default=str)} → {c['resolution']}")
    lines += ["", f"## Requires manual review ({len(report['manual_review'])})", ""]
    for m in report["manual_review"]:
        lines.append(f"- `{m['record']}`{(' ' + m['path']) if m.get('path') else ''}: {m['issue']}")
    lines += ["", f"## House rules needing future mechanical implementation ({len(report['rules_pending_implementation'])})", ""]
    for r in report["rules_pending_implementation"]:
        lines.append(f"- {r['rule']} ({r.get('status', 'pending')}){' — ' + r['note'] if r.get('note') else ''}")
    lines += ["", "## Notes", ""] + [f"- {n}" for n in report["notes"]]
    return "\n".join(lines) + "\n"


def refresh_mechanical_status(conn: sqlite3.Connection) -> None:
    """Re-label imported house rules from the coverage matrix (rules/coverage.py) as the app gains mechanics (startup)."""
    from ..rules.coverage import MATRIX, classify, key_for, mechanical_label
    for r in conn.execute("SELECT id, section, title FROM house_rules").fetchall():
        rule = {"section": r["section"], "title": r["title"]}
        if key_for(rule) not in MATRIX:
            continue  # a rule the matrix does not know (another campaign's): keep its import label
        conn.execute("UPDATE house_rules SET mechanical_status = ? WHERE id = ?", (mechanical_label(classify(rule)), r["id"]))
