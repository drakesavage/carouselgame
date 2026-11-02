"""Omens and their story beats. Position comes from the linked gazetteer place, never from the registry's Map X/Y
(an older reconstruction with a different axis convention): the `Gazetteer ID` when present, else the `Map Anchor`
name. Every record type is kept (hubs, cues, non-Omens) so nothing can launch from a record that isn't playable.
The beat dossier is GM-only as a whole (its claims name killers and mechanisms); only a player-safe projection is
stored beside it."""
from __future__ import annotations

import json
import sqlite3

from . import refdb
from .import_places import place_by_name, resolve_place
from .sources import SourceFile

AREA_CATEGORIES = {"District", "Public square", "Rural zone", "Riverside district"}
SPAWN_PREFIXES = (("Visible as an inspectable anomaly", "fixed"), ("Spawn as a visible moving encounter", "roaming"),
                  ("Must acquire/retain the specified item", "item"))
DROPPED_KEYS = ("Map X / 1000", "Map Y / 1000", "Map Coordinates")


def _field(row: dict, prefix: str) -> str | None:
    """Registry headers contain typographic dashes (sometimes mis-encoded): match on the stable prefix."""
    return next((v for k, v in row.items() if k.startswith(prefix)), None)


def spawn_mode(text: str | None) -> str:
    return next((mode for prefix, mode in SPAWN_PREFIXES if (text or "").startswith(prefix)), "special")


def import_omens(ref: sqlite3.Connection, files: dict[str, SourceFile]) -> None:
    data = files["omens"].data
    reconciled = files.get("reconciliation")
    links = reconciled.data.get("omen_places", {}) if reconciled else {}
    for row in data["registry"]:
        place, method = None, None
        if row.get("Gazetteer ID"):
            place, method = resolve_place(ref, row["Gazetteer ID"]), "gazetteer_id"
        elif row.get("Map Anchor"):
            place, method = place_by_name(ref, row["Map Anchor"]), "anchor_name"
        if row["ID"] in links:
            place = resolve_place(ref, links[row["ID"]]["place_id"])
            if place is None:
                raise ValueError(f"reconciled Omen has unknown place: {row['ID']}")
            method = "source_identity_reconciliation"
        if place is None:
            method = None
            if row["Record Type"] == "Playable Omen":
                refdb.report(ref, "omen_unplaced", row["ID"], f"no gazetteer place for {row.get('Gazetteer ID') or row.get('Map Anchor')!r}")
        category = ref.execute("SELECT category,north,west FROM places WHERE id = ?", (place,)).fetchone() if place else None
        placement = None if place is None else ("area" if category and category[0] in AREA_CATEGORIES else "place")
        if category and (category[1] is None or category[2] is None):
            placement = "unplaced"
            if row["Record Type"] == "Playable Omen":
                refdb.report(ref, "omen_placement_pending", row["ID"], f"source venue {place} has no authored parcel; no world pin or route spawn")
        op, target = row.get("Trigger Operator"), row.get("Trigger Target")
        beats = {"hook": _field(row, "Story Beat 1"), "escalation": _field(row, "Story Beat 2"), "climax": _field(row, "Story Beat 3"),
                 "ending_requirement": row.get("Ending Requirement")}
        raw = {k: v for k, v in row.items() if k not in DROPPED_KEYS}
        ref.execute("""INSERT INTO omens (id, record_type, description, story_lock, playable_title, place_id, anchor_method, placement,
                                          spawn_mode, choice_trigger, beats_json, raw_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (row["ID"], row["Record Type"], row.get("Omen / Description"), row.get("Story Lock Key"), row.get("Playable Story Title"),
                     place, method, placement,
                     spawn_mode(row.get("Spawn / Timing")), f"{op}:{target}" if op and target else (row.get("Implementation Choice") or None),
                     json.dumps(beats, ensure_ascii=False), json.dumps(raw, ensure_ascii=False)))
    for pool in data.get("hub_seed_pools") or []:
        ref.execute("INSERT INTO hub_seed_pools (hub_id, raw_json) VALUES (?,?)", (pool["hub_id"], json.dumps(pool, ensure_ascii=False)))
    for d in files["beats"].data["dossiers"]:
        patch = reconciled.data.get("dossier_patches", {}).get(d["omen_id"], {}) if reconciled else {}
        if patch:
            d = {**d, "continuity": {**d.get("continuity", {}), **patch.get("continuity", {})}}
        public = {"omen_id": d["omen_id"], "title": d.get("title"), "story_lock_key": d.get("story_lock_key"),
                  "player_visible_premise": (d.get("scouting") or {}).get("player_visible_premise")}
        ref.execute("INSERT INTO omen_beats (omen_id, dossier_json, sealed_json) VALUES (?,?,?)",
                    (d["omen_id"], json.dumps(public, ensure_ascii=False), json.dumps(d, ensure_ascii=False)))
