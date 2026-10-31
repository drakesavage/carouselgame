"""Roles (13 starting Archetypes, 39 Aspects, 19 Advanced), their homebrew tickets, the 11-point starting profiles
(Role Bible v0.4.2 audit resolution: budget 11, base Plot Armor 11, cap 5 per stat) and the canonical trope bible.
A ticket's `text` is the rule the GM applies; its full record stays in raw_json."""
from __future__ import annotations

import json
import sqlite3

from . import refdb
from .sources import SourceFile

STATS = ("Mettle", "Moxie", "Hustle", "Savvy", "Grit")


def import_roles(ref: sqlite3.Connection, files: dict[str, SourceFile]) -> None:
    data = files["roles"].data
    overlay = data["v04_playable_overlay"]
    base_ids = {r["name"]: r["id"] for r in overlay["base_roles"]}
    for kind, key in (("base", "base_roles"), ("aspect", "aspects"), ("advanced", "advanced_roles")):
        for r in overlay[key]:
            parent = base_ids.get(r.get("base_archetype")) if kind == "aspect" else None
            ref.execute("INSERT INTO roles (id, name, kind, parent, raw_json) VALUES (?,?,?,?,?)",
                        (r["id"], r["name"], kind, parent, json.dumps(r, ensure_ascii=False)))
    pool = {t["id"]: t for t in overlay.get("shared_starting_trope_pool") or []}
    for t in data["v041_integration"]["trope_registry_overlay"]:
        text = (t.get("original_ticket_definition") or {}).get("rule") or (pool.get(t["trope_id"]) or {}).get("rule")
        if not text:
            refdb.report(ref, "ticket_without_text", t["trope_id"], "no rule text in the overlay or the shared starter pool")
        ref.execute("INSERT INTO tickets (id, name, source_role_id, relationship, text, raw_json) VALUES (?,?,?,?,?,?)",
                    (t["trope_id"], t["trope_name"], t.get("source_role_id"), t.get("relationship"), text or "",
                     json.dumps({**t, **({"shared_pool_entry": pool[t["trope_id"]]} if t["trope_id"] in pool else {})}, ensure_ascii=False)))
    for role_id, p in data["v042_audit_resolution"]["starting_stat_profiles_by_role_id"].items():
        s = p["stats"]
        ref.execute("""INSERT INTO start_profiles (role_id, mettle, moxie, hustle, savvy, grit, base_pa, provenance)
                       VALUES (?,?,?,?,?,?,?,?)""", (role_id, *(s[k] for k in STATS), p["base_pa"], p.get("provenance")))
    for t in files["tropes"].data["tropes"]:
        ref.execute("INSERT INTO tropes (id, name, full_player, has_enemy, reference_only, variants_json) VALUES (?,?,?,?,?,?)",
                    (t["id"], t["name"], int(bool(t.get("full_player"))), int(bool(t.get("has_enemy"))), int(bool(t.get("reference_only"))),
                     json.dumps(t.get("variants") or [], ensure_ascii=False)))
