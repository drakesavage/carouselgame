"""Carousel reference data over HTTP. The player side gets only the import status (counts and source hashes, no
content); everything else is GM-only (X-GM-Token). Knowledge-gated player views of places and Omens arrive with the
maps. Map tiles are served read-only from the source folder."""
from __future__ import annotations

import json
import sqlite3
from typing import Callable

from fastapi import Depends, FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles

from .carousel import refdb, residents
from .config import Settings

TILES_DIR = "Illustrated_Map_Production/web_tiles"

# kind -> (list query, one-record query, extra GM-only query for one record or None)
KINDS: dict[str, tuple[str, str, str | None]] = {
    "places": ("SELECT id, name, category, region, map_id, north, west, px, py, status, in_town_map FROM places ORDER BY id",
               "SELECT * FROM places WHERE id = ?", None),
    "omens": ("SELECT id, record_type, description, story_lock, playable_title, place_id, placement, spawn_mode, choice_trigger FROM omens ORDER BY id",
              "SELECT * FROM omens WHERE id = ?", "SELECT dossier_json, sealed_json FROM omen_beats WHERE omen_id = ?"),
    "roles": ("SELECT id, name, kind, parent FROM roles ORDER BY kind, id", "SELECT * FROM roles WHERE id = ?", None),
    "tickets": ("SELECT id, name, source_role_id, relationship, text FROM tickets ORDER BY id", "SELECT * FROM tickets WHERE id = ?", None),
    "npcs": ("SELECT id, name, provenance, status, camp_role, usual_location FROM npc_templates ORDER BY id",
             "SELECT * FROM npc_templates WHERE id = ?", None),
}
RENAMES = {"raw_json": "raw", "gm_json": "gm", "player_json": "player", "sealed_json": "sealed", "dossier_json": "dossier",
           "requires_json": "requires", "beats_json": "beats"}


def _row(r: sqlite3.Row) -> dict:
    out = {}
    for k in r.keys():
        v = r[k]
        if k.endswith("_json") and isinstance(v, str):
            out[RENAMES.get(k, k[:-5])] = json.loads(v)
        else:
            out[k] = v
    return out


def register(app: FastAPI, settings: Settings, gm_auth: Callable) -> None:
    def ref():
        if not settings.reference_db.exists():
            raise HTTPException(404, "no Carousel reference data imported yet; run import-data.cmd")
        c = refdb.connect(settings.reference_db)
        try:
            yield c
        finally:
            c.close()

    @app.get("/api/reference/status")
    def reference_status():
        if not settings.reference_db.exists():
            return {"imported": False}
        c = refdb.connect(settings.reference_db)
        try:
            m = c.execute("SELECT key, sha256, imported_at FROM manifest ORDER BY key").fetchall()
            return {"imported": True, "imported_at": m[0]["imported_at"] if m else None,
                    "counts": {t: refdb.count(c, t) for t in ("places", "graph_edges", "omens", "roles", "tickets", "tropes", "npc_templates")},
                    "sources": {r["key"]: r["sha256"][:12] for r in m},
                    "hangouts": residents.hangout_table(c)["report"]}   # resident_hangouts.json entries the data can't place
        finally:
            c.close()

    @app.get("/api/gm/reference/{kind}")
    def gm_reference_list(kind: str, _=Depends(gm_auth), c=Depends(ref)):
        if kind not in KINDS:
            raise HTTPException(404, f"unknown reference kind {kind!r}")
        return [_row(r) for r in c.execute(KINDS[kind][0])]

    @app.get("/api/gm/assets/audit")
    def asset_audit(_=Depends(gm_auth), c=Depends(ref)):
        return {
            "findings": [_row(r) for r in c.execute("SELECT category,item_id,message FROM import_report ORDER BY id")],
            "unplaced": [_row(r) for r in c.execute("SELECT id,name,status FROM places WHERE north IS NULL OR west IS NULL")],
            "venue_holds": [_row(r) for r in c.execute("SELECT id,title,raw_json FROM venues WHERE enterable=0 ORDER BY id")],
            "relationships": [_row(r) for r in c.execute("SELECT * FROM asset_relationships ORDER BY venue_id,omen_id")],
            "illustrated_submaps_installed": False,
            "human_approval_inferred": False,
        }

    @app.get("/api/gm/reference/{kind}/{rid}")
    def gm_reference_one(kind: str, rid: str, _=Depends(gm_auth), c=Depends(ref)):
        if kind not in KINDS:
            raise HTTPException(404, f"unknown reference kind {kind!r}")
        _, one_sql, extra_sql = KINDS[kind]
        r = c.execute(one_sql, (rid,)).fetchone()
        if not r:
            raise HTTPException(404, f"no {kind} record {rid!r}")
        out = _row(r)
        if extra_sql and (x := c.execute(extra_sql, (rid,)).fetchone()):
            out.update(_row(x))
        return out

    tiles = settings.source_dir / TILES_DIR
    if tiles.is_dir():
        app.mount("/tiles", StaticFiles(directory=tiles), name="tiles")
