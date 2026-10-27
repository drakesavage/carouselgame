"""The achievements page: every crawler's achievements (the player character first, then companions) with what each
rewarded, and the loot boxes they are still holding. Player-visible records only."""
from __future__ import annotations

import re
import sqlite3

from ..db import one, rows

TIERS = ("Bronze", "Silver", "Gold", "Platinum", "Legendary", "Celestial")
BOX_NAME = re.compile(r"^(%s)\b.*\bbox$" % "|".join(TIERS), re.I)


def _is_box(item: dict) -> bool:
    return bool((item.get("properties") or {}).get("loot_box")) or bool(BOX_NAME.match(item["name"].strip()))


def page(conn: sqlite3.Connection, cid: int) -> dict:
    pc_id = (one(conn, "SELECT active_character_id FROM campaigns WHERE id = ?", (cid,)) or {}).get("active_character_id")
    pc_group = (one(conn, "SELECT assignment FROM characters WHERE id = ?", (pc_id or -1,)) or {}).get("assignment")
    chars = rows(conn, """SELECT id, name, kind, in_party, status, assignment, portrait_path, portrait_crop_json FROM characters WHERE campaign_id = ?
                          ORDER BY id = ? DESC, in_party DESC, kind = 'crawler' DESC, name""", (cid, pc_id or -1))
    out, total, unopened = [], 0, 0
    for c in chars:
        achs = rows(conn, """SELECT a.id, a.title, a.description, a.reward, a.game_time_minutes, a.created_at, i.name AS box,
                                    i.properties_json AS box_props_json FROM achievements a LEFT JOIN items i ON i.id = a.box_item_id
                             WHERE a.campaign_id = ? AND a.character_id = ? ORDER BY a.id DESC""", (cid, c["id"]))
        for a in achs:
            props = a.pop("box_props") or {}
            a["box_opened"] = bool(props.get("opened")) if a["box"] else None
        boxes = [{"id": i["id"], "name": i["name"], "tier": (i.get("properties") or {}).get("tier") or (BOX_NAME.match(i["name"].strip()) or [None, None])[1],
                  "from": (i.get("properties") or {}).get("from_achievement")}
                 for i in rows(conn, "SELECT id, name, quantity, properties_json FROM items WHERE campaign_id = ? AND owner_character_id = ? AND quantity > 0",
                               (cid, c["id"])) if _is_box(i)]
        companion = c["in_party"] or c["kind"] == "crawler" or (pc_group and c.get("assignment") == pc_group)
        if not achs and not boxes and c["id"] != pc_id and not (companion and c["status"] not in ("dead", "deceased")):
            continue
        total += len(achs)
        unopened += len(boxes)
        out.append({"id": c["id"], "name": c["name"], "is_player": c["id"] == pc_id, "in_party": bool(c["in_party"]), "status": c["status"],
                    "portrait_path": c["portrait_path"], "portrait_crop": c.get("portrait_crop"), "achievements": achs, "boxes": boxes})
    return {"characters": out, "total": total, "unopened_boxes": unopened}
