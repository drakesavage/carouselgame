"""Compute ledger edits from the console's campaign state.

Every proposed edit compares the console's current value with the ledger *baseline* (the ledger commit the
console last imported from or synced to). Only fields that the console actually changed produce edits, so
untouched history is never rewritten. Classification against the ledger's current working tree (three-way) is
done in sync.py.

The mapping is the inverse of importer/ledger.py; keep the two in step when either changes.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from ..db import one, rows
from .yamledit import Edit, get, slugify

DAY_MINUTES_DEFAULT = 1800
EQUIPMENT_KEYS = {"equipped", "wielded", "worn", "natural_armor", "carried", "hotlisted", "other", "owned_elsewhere", "deployed_elsewhere"}
STATUS_LISTS = ("conditions", "injuries", "buffs", "debuffs")


@dataclass
class Change:
    file: str
    edit: Edit | None              # None for jsonl/create kinds
    summary: str
    group: str
    visibility: str = "player"     # "gm" for gm_state/**
    kind: str = "yaml"             # yaml | jsonl_append | create_file
    payload: Any = None            # jsonl records or file data
    id: str = ""
    status: str = "clean"          # clean | already_applied | conflict
    detail: dict = field(default_factory=dict)

    def finalize(self) -> "Change":
        key = json.dumps([self.file, self.kind, self.edit.op if self.edit else None, self.edit.path if self.edit else None,
                          self.edit.value if self.edit else self.payload], default=str, sort_keys=True)
        self.id = hashlib.sha1(key.encode()).hexdigest()[:12]
        return self


def fmt_minutes(m: int) -> str:
    return f"{m // 60}h{m % 60:02d}m"


def _norm_bool(v: Any) -> bool | None:
    if isinstance(v, bool):
        return v
    if isinstance(v, str) and v.lower() in ("true", "false"):
        return v.lower() == "true"
    return None


def _int(v: Any) -> int | None:
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, str) and v.strip().lstrip("-").isdigit():
        return int(v)
    return None


def _unresolved(entry: dict) -> bool:
    return any(isinstance(entry.get(k), str) and entry[k].strip().lower().startswith(("unresolved", "unknown", "unquantified"))
               for k in ("count_status", "quantity_status"))


class Builder:
    def __init__(self, conn: sqlite3.Connection, cid: int, base: dict[str, Any], hw: dict[str, int]):
        self.conn, self.cid, self.base, self.hw = conn, cid, base, hw
        self.changes: list[Change] = []
        self.warnings: list[str] = []
        self.today = date.today().isoformat()

    def set(self, file: str, path: list, value: Any, summary: str, group: str, visibility: str = "player", link: list | None = None) -> None:
        old = get(self.base.get(file), path)
        if old == value:
            return
        detail = {"from": old, "to": value, **({"link": link} if link else {})}
        self.changes.append(Change(file, Edit("set", path, value), summary, group, visibility, detail=detail).finalize())

    def append(self, file: str, path: list, value: Any, summary: str, group: str, visibility: str = "player", cat: str | None = None,
               link: list | None = None) -> None:
        doc = self.base.get(file)
        if get(doc, path) is None and path:
            # list doesn't exist in the baseline: create it (merging any earlier appends to the same new list)
            for c in self.changes:
                if c.file == file and c.edit and c.edit.op == "set" and c.edit.path == path and isinstance(c.edit.value, list):
                    c.edit.value.append(value)
                    c.summary += f"; {summary}"
                    if link:
                        c.detail.setdefault("links", []).append(link)
                    c.finalize()
                    return
            self.changes.append(Change(file, Edit("set", path, [value]), summary, group, visibility,
                                       detail={"to": value, "cat": cat, **({"links": [link]} if link else {})}).finalize())
            return
        self.changes.append(Change(file, Edit("append", path, value), summary, group, visibility,
                                   detail={"to": value, "cat": cat, **({"links": [link]} if link else {})}).finalize())

    # ------------------------------------------------------------------ campaign
    def campaign(self) -> None:
        f = "state/campaign.yaml"
        B = self.base.get(f)
        if not isinstance(B, dict):
            return
        camp = one(self.conn, "SELECT * FROM campaigns WHERE id = ?", (self.cid,))
        fc = B.get("floor_clock") or {}
        dti = B.get("dungeon_time_inventory") or {}
        elapsed, deadline = camp["game_time_minutes"], camp["floor_deadline_minutes"]
        base_remaining = _int(fc.get("remaining_minutes"))
        if deadline is not None:
            remaining = max(0, deadline - elapsed)
            if remaining != base_remaining:
                reasons = [e["summary"] for e in self._events("time_advanced")]
                reason = "Crawler Console play: " + ("; ".join(r.split(" — ", 1)[-1] for r in reasons)[:400] or "time passed")
                g = "Floor clock"
                self.set(f, ["floor_clock", "remaining_minutes"], remaining, f"Floor clock {fmt_minutes(base_remaining or 0)} → {fmt_minutes(remaining)}", g)
                if base_remaining is not None:
                    self.set(f, ["floor_clock", "previous_remaining_minutes"], base_remaining, "Previous remaining minutes recorded", g)
                if "remaining_display" in fc:
                    self.set(f, ["floor_clock", "remaining_display"], fmt_minutes(remaining), "Remaining display updated", g)
                self.set(f, ["floor_clock", "last_change_reason"], reason, "Clock change reason", g)
                if base_remaining is not None and remaining > base_remaining:
                    self.set(f, ["floor_clock", "increase_reason"], reason, "Increase reason (required by the validator)", g)
                self._roster_mirror(remaining)
        if "elapsed_floor_minutes" in dti and _int(dti.get("elapsed_floor_minutes")) != elapsed:
            day = _int(dti.get("standard_dungeon_day_minutes")) or DAY_MINUTES_DEFAULT
            in_day = elapsed % day
            rem = max(0, deadline - elapsed) if deadline is not None else None
            derived = {"elapsed_floor_minutes": elapsed, "current_dungeon_day": elapsed // day + 1, "elapsed_in_current_day_minutes": in_day,
                       "elapsed_in_current_day_display": fmt_minutes(in_day), "time_until_next_daily_reset_minutes": day - in_day,
                       "time_until_next_daily_reset_display": fmt_minutes(day - in_day)}
            if rem is not None:
                derived["next_daily_reset_at_remaining_minutes"] = rem - (day - in_day)
                derived["next_daily_reset_at_remaining_display"] = fmt_minutes(max(0, rem - (day - in_day)))
            for k, v in derived.items():
                if k in dti:
                    self.set(f, ["dungeon_time_inventory", k], v, f"Dungeon time: {k.replace('_', ' ')} → {v}", "Floor clock")
        if _int(B.get("current_floor")) != camp["current_floor"] and camp["current_floor"] is not None:
            self.set(f, ["current_floor"], camp["current_floor"], f"Current floor → {camp['current_floor']}", "Campaign")
        base_loc = B.get("current_location")
        combined = f"{base_loc} — {B['current_area']}" if B.get("current_area") and base_loc else base_loc
        if camp["current_location"] and camp["current_location"] != combined:
            new = camp["current_location"]
            if B.get("current_area") and new.endswith(f" — {B['current_area']}"):
                new = new[: -len(f" — {B['current_area']}")]
            self.set(f, ["current_location"], new, f"Location → {new}", "Campaign")
        if _norm_bool(B.get("safe_room_status")) != bool(camp["safe_room"]):
            self.set(f, ["safe_room_status"], bool(camp["safe_room"]), f"Saferoom → {bool(camp['safe_room'])}", "Campaign")
        if camp["active_objective"] and camp["active_objective"] != B.get("active_objective"):
            self.set(f, ["active_objective"], camp["active_objective"], "Active objective updated", "Campaign")
        self._objectives(f, B)
        for fact in rows(self.conn, """SELECT id, subject, fact, certainty FROM lore_facts WHERE campaign_id = ? AND visibility = 'player'
                                       AND source LIKE 'turn %' AND id > ? ORDER BY id""", (self.cid, self.hw.get("fact", 0))):
            text = fact["fact"] if fact["certainty"] == "canonical" else f"[{fact['certainty']}] {fact['fact']}"
            self.append(f, ["world_state_notes"], text, f"World note: {text[:90]}", "World", cat="fact")
        for r in rows(self.conn, "SELECT id, turn_id, topic, ruling, label, certainty FROM rulings WHERE campaign_id = ? AND id > ? ORDER BY id",
                      (self.cid, self.hw.get("ruling", 0))):
            entry = f"[{r['label']}, {r['certainty']}] {r['topic']}: {r['ruling']} (Crawler Console turn {r['turn_id']}, {self.today})"
            self.append(f, ["campaign_rulings", "crawler_console"], entry, f"Ruling: {r['topic']}", "Rulings", cat="ruling")

    def _roster_mirror(self, remaining: int) -> None:
        f = "state/roster.yaml"
        o = get(self.base.get(f), ["current_state_override"])
        if isinstance(o, dict):
            if "floor_clock_remaining_minutes" in o:
                self.set(f, ["current_state_override", "floor_clock_remaining_minutes"], remaining, "Roster mirror of floor clock", "Floor clock")
            if "floor_clock_remaining_display" in o:
                self.set(f, ["current_state_override", "floor_clock_remaining_display"], fmt_minutes(remaining), "Roster mirror of clock display", "Floor clock")

    def _objectives(self, f: str, B: dict) -> None:
        to_ledger = {"active": "open", "completed": "completed", "failed": "failed", "paused": "paused"}
        base_objs = {o.get("id"): o for o in (B.get("open_objectives") or []) if isinstance(o, dict)}
        existing_ids = set(base_objs)
        for q in rows(self.conn, "SELECT * FROM quests WHERE campaign_id = ? AND visibility = 'player' ORDER BY id", (self.cid,)):
            desc = q["description"] or ""
            if desc.startswith("Ledger objective id: "):
                oid = desc.split(": ", 1)[1].strip()
                o = base_objs.get(oid)
                if not o:
                    continue
                want = to_ledger.get(q["status"], q["status"])
                base_status = {"active": "open"}.get(str(o.get("status")), str(o.get("status")))
                if want != base_status:
                    self.set(f, ["open_objectives", {"id": oid}, "status"], want, f"Objective “{q['title'][:60]}” → {want}", "Quests")
                if q["progress"] and q["progress"] != o.get("progress"):
                    self.set(f, ["open_objectives", {"id": oid}, "progress"], q["progress"], f"Objective progress: {q['title'][:60]}", "Quests")
            elif (q.get("details") or {}).get("ledger_source"):
                continue  # seeded from another ledger record (reservation note, checkpoint promise): already in the ledger
            else:
                oid = slugify(q["title"])[:60]
                while oid in existing_ids:
                    oid += "-x"
                existing_ids.add(oid)
                entry = {"id": oid, "summary": q["title"], "status": to_ledger.get(q["status"], q["status"])}
                if q["kind"] != "quest":
                    entry["kind"] = q["kind"]
                if q["progress"]:
                    entry["progress"] = q["progress"]
                entry["source"] = f"Crawler Console {self.today}"
                self.append(f, ["open_objectives"], entry, f"New {q['kind']}: {q['title'][:70]}", "Quests", link=["quest", [q["id"]], oid])

    # ------------------------------------------------------------------ characters
    def characters(self) -> None:
        for c in rows(self.conn, "SELECT * FROM characters WHERE campaign_id = ? ORDER BY id", (self.cid,)):
            prov = c["provenance"] or ""
            if not prov.startswith("ledger:"):
                if prov not in ("demo",):
                    self.warnings.append(f"{c['name']} exists only in the console; new characters are not written to the ledger yet.")
                continue
            f = prov.split(":", 1)[1]
            B = self.base.get(f)
            if not isinstance(B, dict):
                self.warnings.append(f"{c['name']}: {f} is not in the ledger baseline; skipped.")
                continue
            self._character(f, B, c)

    def _character(self, f: str, B: dict, c: dict) -> None:
        g = c["name"]
        m = B.get("mechanics") or {}
        if c.get("location") and "location" in B and B.get("location") != c["location"]:
            self.set(f, ["location"], c["location"], f"Location → {c['location']}", g)  # an unknown position never overwrites the ledger's wording
        if c["level"] is not None and _int(B.get("level")) != c["level"]:
            self.set(f, ["level"], c["level"], f"Level {B.get('level')} → {c['level']}", g)
            o = get(self.base.get("state/roster.yaml"), ["current_state_override", "current_members", c["slug"], "level"])
            if o is not None:
                self.set("state/roster.yaml", ["current_state_override", "current_members", c["slug"], "level"], c["level"], f"Roster mirror of {g}'s Level", g)
        for res, label in (("hp", "health"), ("mana", "mana")):
            bm = m.get(label)
            if isinstance(bm, dict):
                for part in ("current", "max"):
                    v = c[f"{res}_{part}"]
                    if v is not None and _int(bm.get(part)) != v:
                        self.set(f, ["mechanics", label, part], v, f"{label.title()} {part} {bm.get(part)} → {v}", g)
        if c["ai_favor"] is not None and "ai_favor" in m and _int(m.get("ai_favor")) != c["ai_favor"]:
            self.set(f, ["mechanics", "ai_favor"], c["ai_favor"], f"AI Favor {m.get('ai_favor')} → {c['ai_favor']}", g)
        if c["status"] and B.get("status") is not None and str(B.get("status")) != c["status"]:
            self.set(f, ["status"], c["status"], f"Status → {c['status']}", g)
        st = c.get("stats") or {}
        for src, dst in (("stats", "stats"), ("modifiers", "stat_modifiers")):
            for k, v in (st.get(src) or {}).items():
                if isinstance(m.get(dst), dict) and k in m[dst] and _int(m[dst][k]) != v:
                    self.set(f, ["mechanics", dst, k], v, f"{k.title()} {'mod ' if src == 'modifiers' else ''}{m[dst][k]} → {v}", g)
        d = c.get("derived") or {}
        if "unallocated_stat_points" in d and "unallocated_stat_points" in m and _int(m.get("unallocated_stat_points")) != d["unallocated_stat_points"]:
            self.set(f, ["mechanics", "unallocated_stat_points"], d["unallocated_stat_points"], f"Unallocated Stat points → {d['unallocated_stat_points']}", g)
        # Visual references are not written back: state/characters/VISUAL_BIBLE.md is the canonical visual source.
        self._skills(f, m, c, g)
        self._statuses(f, m, c, g)
        self._items(f, B, c, g)
        for a in rows(self.conn, "SELECT * FROM achievements WHERE campaign_id = ? AND character_id = ? AND id > ? ORDER BY id",
                      (self.cid, c["id"], self.hw.get("achievement", 0))):
            self.append(f, ["achievements"], {"title": a["title"], "description": a["description"], "date": self.today, "source": "Crawler Console"},
                        f"Achievement: {a['title']}", g, cat="achievement")

    def _visual(self, f: str, B: dict, c: dict, g: str) -> None:
        """Imported visual references (importer/visual_reference.py) go to the character file's `visual.reference`, and
        the traits they settle leave `visual.unestablished`. Inverse of the importer copying `visual` into the sheet."""
        vis = (c.get("sheet") or {}).get("visual") or {}
        ref = vis.get("reference")
        if not ref:
            return
        bvis = B.get("visual")
        if not isinstance(bvis, dict):
            self.warnings.append(f"{c['name']}: {f} has no visual block; the visual reference stays in the console.")
            return
        clean = {k: v for k, v in ref.items() if k != "imported_at"}  # the import time is console bookkeeping
        self.set(f, ["visual", "reference"], clean, "Visual reference: " + ", ".join(
            str(x) for x in (ref.get("demographic"), ref.get("age") or ref.get("size")) if x), g)
        if "unestablished" in bvis and (vis.get("unestablished") or []) != (bvis.get("unestablished") or []):
            self.set(f, ["visual", "unestablished"], vis.get("unestablished") or [], "Unestablished visual traits settled by the reference", g)

    def _skills(self, f: str, m: dict, c: dict, g: str) -> None:
        section = {"skill": "skills", "spell": "spells", "equipment_skill": "equipment_granted_skills"}
        marked = []
        for s in rows(self.conn, "SELECT * FROM skills WHERE character_id = ? ORDER BY id", (c["id"],)):
            sec = section.get(s["kind"])
            if not sec or not isinstance(m.get(sec), dict):
                continue
            key = s["key"]
            if key and key in m[sec]:
                entry = m[sec][key]
                base_rank = _int(entry.get("rank")) if isinstance(entry, dict) else _int(entry)
                if s["rank"] is not None and base_rank != s["rank"]:
                    path = ["mechanics", sec, key, "rank"] if isinstance(entry, dict) else ["mechanics", sec, key]
                    self.set(f, path, s["rank"], f"{s['name']} Rank {base_rank} → {s['rank']}", g)
            elif not key:
                new_key = slugify(s["name"])
                self.set(f, ["mechanics", sec, new_key], {"name": s["name"], "rank": s["rank"], "provenance": f"Crawler Console {self.today}"},
                         f"New {s['kind'].replace('_', ' ')}: {s['name']} {s['rank']}", g, link=["skill", [s["id"]], new_key])
                key = new_key
            if s["kind"] == "skill" and s["marked"] and key:
                marked.append(key)
        base_marks = list(m.get("advancement_marks") or [])
        if "advancement_marks" in m and set(base_marks) != set(marked):
            new = [k for k in base_marks if k in marked] + [k for k in marked if k not in base_marks]
            self.set(f, ["mechanics", "advancement_marks"], new, f"Advancement marks → {', '.join(new) or 'none'}", g)

    def _statuses(self, f: str, m: dict, c: dict, g: str) -> None:
        if not any(k in m for k in STATUS_LISTS):
            return
        db = rows(self.conn, "SELECT name, description, polarity FROM status_effects WHERE character_id = ?", (c["id"],))
        db_names = {s["name"].lower() for s in db}

        def name_of(e):
            return (e if isinstance(e, str) else str(e.get("name") or e.get("id") or "")).lower()
        base_names = {name_of(e) for k in STATUS_LISTS for e in (m.get(k) or [])}
        lists = {k: list(m.get(k) or []) for k in STATUS_LISTS if k in m}
        changed = set()
        for k, entries in lists.items():
            kept = [e for e in entries if name_of(e) in db_names]
            if len(kept) != len(entries):
                lists[k] = kept
                changed.add(k)
        for s in db:
            if s["name"].lower() in base_names:
                continue
            target = {"buff": "buffs", "debuff": "debuffs"}.get(s["polarity"], "conditions")
            lists.setdefault(target, [])
            lists[target].append(s["name"] if not s["description"] else f"{s['name']} — {s['description']}")
            changed.add(target)
        for k in changed:
            self.set(f, ["mechanics", k], lists[k], f"{k.title()} → {', '.join(map(str, lists[k])) or 'none'}", g)

    def _group_items(self, owner_sql: str, params: tuple, base_entries: list[dict]) -> tuple[dict[str, int | None], dict[str, dict]]:
        """Desired quantity per baseline item id, plus new items keyed by name."""
        by_name = {str(e.get("name", "")).lower(): e.get("id") for e in base_entries if isinstance(e, dict)}
        want: dict[str, int | None] = {}
        new: dict[str, dict] = {}
        for it in rows(self.conn, f"SELECT * FROM items WHERE campaign_id = ? AND {owner_sql}", (self.cid, *params)):
            key = it["item_key"] or by_name.get(it["name"].lower())
            props = it.get("properties") or {}
            if props.get("source_key") in EQUIPMENT_KEYS:
                continue
            if key is not None and key in {e.get("id") for e in base_entries if isinstance(e, dict)}:
                prev = want.get(key, 0)
                want[key] = None if (prev is None or it["quantity"] is None) else prev + it["quantity"]
            else:
                n = new.setdefault(it["name"].lower(), {"name": it["name"], "quantity": 0, "description": it["description"], "rarity": it["rarity"],
                                                         "effects": props.get("effects"), "deployed": it["deployed"], "row_ids": []})
                n["row_ids"].append(it["id"])
                n["quantity"] = None if (n["quantity"] is None or it["quantity"] is None) else n["quantity"] + it["quantity"]
        return want, new

    def _sync_list(self, f: str, path_key: str, entries: list, want: dict, g: str, label: str) -> None:
        for e in entries:
            if not isinstance(e, dict) or e.get("id") is None:
                continue
            base_q = _int(e.get("quantity"))
            desired = want.get(e["id"], 0)
            if desired is None:
                continue  # still unknown in the console: leave the ledger's own wording alone
            if _unresolved(e):
                if desired == 0 and base_q in (0, None) and e["id"] not in want:
                    continue
                self.set(f, [path_key, {"id": e["id"]}, "quantity"], desired, f"{label}{e.get('name')}: count established at {desired}", g)
                for k in ("count_status", "quantity_status"):
                    if k in e:
                        self.set(f, [path_key, {"id": e["id"]}, k], f"established in Crawler Console {self.today}", f"{e.get('name')}: {k} resolved", g)
            elif base_q != desired and not (base_q is None and "quantity" not in e):
                self.set(f, [path_key, {"id": e["id"]}, "quantity"], desired, f"{label}{e.get('name')} ×{base_q} → ×{desired}", g)

    def _new_entry(self, n: dict, taken: set) -> dict:
        iid = slugify(n["name"])
        while iid in taken:
            iid += "-2"
        taken.add(iid)
        entry = {"id": iid, "name": n["name"], "quantity": n["quantity"]}
        if n.get("rarity"):
            entry["rarity"] = n["rarity"]
        if n.get("description"):
            entry["note"] = n["description"]
        if n.get("effects"):
            entry["effects"] = n["effects"]
        entry["provenance"] = f"Crawler Console {self.today}"
        return entry

    def _items(self, f: str, B: dict, c: dict, g: str) -> None:
        inv = [e for e in (B.get("inventory") or []) if isinstance(e, dict)]
        want, new = self._group_items("owner_character_id = ?", (c["id"],), inv)
        if "inventory" in B or new:
            self._sync_list(f, "inventory", inv, want, g, "")
        taken = {e.get("id") for e in inv}
        for n in new.values():
            if n["quantity"] == 0:
                continue
            entry = self._new_entry(n, taken)
            self.append(f, ["inventory"], entry, f"Gains {entry['quantity'] if entry['quantity'] is not None else '?'}× {n['name']}", g,
                        link=["item", n["row_ids"], entry["id"]])
        # equipment lists are not rewritten; flag removals for manual reconciliation
        eq = B.get("equipment") or {}
        for key, entries in (eq.items() if isinstance(eq, dict) else []):
            if key not in EQUIPMENT_KEYS or not isinstance(entries, list):
                continue
            for e in entries:
                if isinstance(e, dict) and e.get("id") and _int(e.get("quantity")) != 0 and not one(self.conn, "SELECT id FROM items WHERE owner_character_id = ? AND item_key = ?", (c["id"], e["id"])):
                    self.warnings.append(f"{g}: '{e.get('name')}' ({key}) is no longer carried in the console; equipment lists are not edited automatically.")

    def shared(self) -> None:
        f = "state/shared_inventory.yaml"
        B = self.base.get(f)
        if not isinstance(B, dict):
            return
        sections = {k: [e for e in (B.get(k) or []) if isinstance(e, dict)] for k in ("items", "deployed_assets", "unquantified_items")}
        all_entries = [e for v in sections.values() for e in v]
        want, new = self._group_items("owner_character_id IS NULL", (), all_entries)
        for k in ("items", "deployed_assets"):
            self._sync_list(f, k, sections[k], want, "Shared stash", "")
        # an unquantified entry that now has a count (a ruling or a set_quantity) moves to the counted items list
        counted = [e for e in sections["unquantified_items"] if want.get(e.get("id")) is not None]
        for e in counted:
            desc = one(self.conn, "SELECT description FROM items WHERE campaign_id = ? AND owner_character_id IS NULL AND lower(name) = lower(?)",
                       (self.cid, e.get("name", "")))
            entry = {**{k: v for k, v in e.items() if k != "note"}, "quantity": want[e["id"]], "note": (desc or {}).get("description") or e.get("note")}
            self.append(f, ["items"], entry, f"{e.get('name')} now counted: {want[e['id']]}", "Shared stash")
        if counted:
            keep = [e for e in sections["unquantified_items"] if e not in counted]
            self.set(f, ["unquantified_items"], keep, f"{len(counted)} item(s) leave the unquantified list", "Shared stash")
        taken = {e.get("id") for e in all_entries}
        for n in new.values():
            if n["quantity"] == 0:
                continue
            entry = self._new_entry(n, taken)
            self.append(f, ["deployed_assets" if n["deployed"] else "items"], entry,
                        f"Stash gains {entry['quantity'] if entry['quantity'] is not None else '?'}× {n['name']}", "Shared stash",
                        link=["item", n["row_ids"], entry["id"]])

    # ------------------------------------------------------------------ advancement, rolls, GM notes
    def _events(self, type_: str, visibility: str | None = "player") -> list[dict]:
        sql = "SELECT * FROM events WHERE campaign_id = ? AND type = ? AND id > ? AND rolled_back = 0"
        params: list = [self.cid, type_, self.hw.get("event", 0)]
        if visibility:
            sql += " AND visibility = ?"
            params.append(visibility)
        return rows(self.conn, sql + " ORDER BY id", params)

    def advancement(self) -> None:
        f = "state/advancement.yaml"
        B = self.base.get(f)
        clock = one(self.conn, "SELECT * FROM clocks WHERE campaign_id = ? AND key = 'advancement_cycles'", (self.cid,))
        if not isinstance(B, dict) or not clock:
            return
        # campaign.yaml current_state mirrors the live counter (owner decision, M1), so it can't go stale beside it
        live = f"{clock['current']}/{clock['maximum']}"
        mirror = get(self.base.get("state/campaign.yaml"), ["current_state", "advancement_cycles"])
        if isinstance(mirror, str):
            self.set("state/campaign.yaml", ["current_state", "advancement_cycles"], live, f"Campaign mirror of advancement cycles {mirror} → {live}", "Advancement")
        base_cur = _int(B.get("cycles_current"))
        counted = [e["summary"].split(" — ", 1)[-1] for e in self._events("clock_changed") if (e.get("payload") or {}).get("key") == "advancement_cycles"]
        checkpoints = self._events("advancement_checkpoint")
        if clock["current"] == base_cur and not counted and not checkpoints:
            return
        g = "Advancement"
        self.set(f, ["cycles_current"], clock["current"], f"Cycles {base_cur}/{clock['maximum']} → {clock['current']}/{clock['maximum']}", g)
        pending = clock["current"] >= (clock["maximum"] or 12)
        if isinstance(B.get("checkpoint_pending"), bool):
            self.set(f, ["checkpoint_pending"], pending, f"Checkpoint pending → {pending}", g)
        entry: dict[str, Any] = {"date": self.today, "checkpoint": f"crawler-console-{self.today}",
                                 "basis": "Substantive decision-and-resolution cycles counted by the Crawler Console GM.",
                                 "counted_cycles": counted or ["(none counted in this sync)"],
                                 "result": f"{base_cur}/{clock['maximum']} -> {clock['current']}/{clock['maximum']}"}
        if checkpoints:
            lines = [ln for e in checkpoints for ln in ((e.get("payload") or {}).get("lines") or [])]
            entry["advancement_resolution"] = lines
            if "last_resolved_checkpoint" in B:
                self.set(f, ["last_resolved_checkpoint"], entry["checkpoint"], "Last resolved checkpoint", g)
        self.append(f, ["reconciliations"], entry, f"Reconciliation entry ({len(counted)} cycles)", g)

    def rolls(self) -> None:
        recs = {"rolls/public.jsonl": [], "gm_state/rolls.jsonl": []}
        for r in rows(self.conn, """SELECT d.*, c.slug FROM dice_rolls d LEFT JOIN characters c ON c.id = d.character_id
                                    WHERE d.campaign_id = ? AND d.turn_id IS NOT NULL AND d.rolled_back = 0 AND d.id > ? ORDER BY d.id""",
                        (self.cid, self.hw.get("roll", 0))):
            det = r.get("detail") or {}
            kept = [d["value"] for d in (r.get("dice") or []) if d.get("kept")]
            mods = []
            if det.get("skill"):
                mods.append(f"{det['skill']} {det.get('rank')}")
            if det.get("stat"):
                mods.append(f"{str(det['stat'])[:3].upper()} {det.get('stat_mod')}")
            mods += [f"{m['source']} {m['value']:+d}" for m in det.get("modifiers") or []]
            mods += [f"{h['helper']} Help {h['value']}" for h in det.get("help") or []]
            rec = {"actor": r["slug"], "check": det.get("skill") or (r["reason"] or "roll"), "date": (r["created_at"] or "")[:10],
                   "die": r["expression"], "event": f"crawler-console-turn-{r['turn_id']}", "raw": det.get("d20s") or kept,
                   "total": r["total"], "target": r["target"], "outcome": r["degree"] or r["outcome"],
                   "modifiers": "; ".join(mods) or None, "rng_source": r["rng"], "provenance": f"Crawler Console roll #{r['id']}",
                   "visibility": "gm" if r["hidden"] else "public"}
            if det.get("natural") is not None:
                rec["kept"] = det["natural"]
            if r["reason"]:
                rec["reason"] = r["reason"]
            recs["gm_state/rolls.jsonl" if r["hidden"] else "rolls/public.jsonl"].append({k: v for k, v in rec.items() if v is not None})
        for f, items in recs.items():
            if items:
                vis = "gm" if f.startswith("gm_state") else "player"
                self.changes.append(Change(f, None, f"Append {len(items)} roll record{'s' if len(items) != 1 else ''}", "Rolls" if vis == "player" else "GM-only",
                                           vis, kind="jsonl_append", payload=items, detail={"cat": "roll"}).finalize())

    def gm_notes(self) -> None:
        f = "gm_state/console_notes.yaml"
        notes = []
        for s in rows(self.conn, """SELECT * FROM gm_secrets WHERE campaign_id = ? AND kind != 'companion_sheet'
                                    AND (source IS NULL OR source NOT LIKE 'ledger:%') AND id > ? ORDER BY id""", (self.cid, self.hw.get("secret", 0))):
            notes.append({"id": f"console-secret-{s['id']}", "date": (s["created_at"] or "")[:10], "subject": s["subject"], "kind": s["kind"],
                          "status": "revealed" if s["revealed"] else "unrevealed", "content": s["content"]})
        for fct in rows(self.conn, """SELECT * FROM lore_facts WHERE campaign_id = ? AND visibility = 'gm' AND source LIKE 'turn %' AND id > ? ORDER BY id""",
                        (self.cid, self.hw.get("fact", 0))):
            notes.append({"id": f"console-fact-{fct['id']}", "date": (fct["created_at"] or "")[:10], "subject": fct["subject"], "kind": "fact",
                          "certainty": fct["certainty"], "content": fct["fact"]})
        B = self.base.get(f)
        for n in notes:
            if B is None and not any(c.file == f for c in self.changes):
                doc = {"schema_version": 1, "confidentiality": "GM-only. Never expose unrevealed entries to the player.",
                       "source": "Crawler Console GM notes", "notes": [n]}
                self.changes.append(Change(f, None, "Create GM-only console notes file", "GM-only", "gm", kind="create_file", payload=doc,
                                           detail={"cat": "secret"}).finalize())
            elif B is None:
                c = next(c for c in self.changes if c.file == f)
                c.payload["notes"].append(n)
                c.finalize()
            else:
                self.append(f, ["notes"], n, "GM-only note", "GM-only", "gm", cat="secret")
        # reveal status of notes written by an earlier sync
        if isinstance(B, dict):
            for idx, n in enumerate(B.get("notes") or []):
                sid = str(n.get("id", ""))
                if sid.startswith("console-secret-"):
                    row = one(self.conn, "SELECT revealed FROM gm_secrets WHERE id = ? AND campaign_id = ?", (int(sid.rsplit("-", 1)[1]), self.cid))
                    if row and row["revealed"] and n.get("status") != "revealed":
                        self.set(f, ["notes", {"id": sid}, "status"], "revealed", "GM note marked revealed", "GM-only", "gm")
        for e in self._events("npc_attitude", None):
            self.warnings.append(f"Not written to the ledger (no field for it yet): {e['summary']}")

    def build(self) -> tuple[list[Change], list[str]]:
        self.campaign()
        self.characters()
        self.shared()
        self.advancement()
        self.rolls()
        self.gm_notes()
        self.corrections()
        return self.changes, self.warnings

    def corrections(self) -> None:
        """Owner-approved corrections (reconcile.py) carry their ledger edits into the reviewed write-back."""
        from ..reconcile import corrections
        for cor in corrections(self.conn, self.cid):
            for e in cor.get("ledger_edits") or []:
                self.set(e["file"], list(e["path"]), e["value"], f"Correction #{cor['id']}: {cor['subject']}", "Corrections")


def files_needed(conn: sqlite3.Connection, cid: int) -> list[str]:
    files = ["state/campaign.yaml", "state/roster.yaml", "state/shared_inventory.yaml", "state/advancement.yaml", "gm_state/console_notes.yaml"]
    for c in rows(conn, "SELECT provenance FROM characters WHERE campaign_id = ? AND provenance LIKE 'ledger:%'", (cid,)):
        files.append(c["provenance"].split(":", 1)[1])
    return files
