"""Location art: the identity image for each place, from the location art studio's review index. Paths are stored
relative to Location_Art_Production/assets, the folder served at /location-art. Index rows for IDs the gazetteer
doesn't have (provisional PROV-LOC-* art) are reported, not kept."""
from __future__ import annotations

import sqlite3

from . import refdb
from .sources import SourceFile

ASSETS_PREFIX = "assets/"


def import_art(ref: sqlite3.Connection, files: dict[str, SourceFile]) -> None:
    reconciled = files.get("reconciliation")
    refs = (reconciled.data.get("references") if reconciled else None)
    for r in refs if refs is not None else files["location_art"].data["references"]:
        pid, rel = r["location_id"], r["relative_image_path"]
        if not ref.execute("SELECT 1 FROM places WHERE id = ?", (pid,)).fetchone():
            refdb.report(ref, "art_unmatched", pid, f"art index names a place the gazetteer doesn't have: {rel}")
            continue
        rel = rel[len(ASSETS_PREFIX):] if rel.startswith(ASSETS_PREFIX) else rel
        ref.execute("INSERT OR REPLACE INTO place_images (place_id, kind, rel_path) VALUES (?, 'identity', ?)", (pid, rel))
    for gap in reconciled.data.get("image_gaps", []) if reconciled else []:
        refdb.report(ref, "asset_missing", gap["id"], f"{gap['name']}: {gap['reason']}")
