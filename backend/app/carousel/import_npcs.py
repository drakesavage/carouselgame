"""The 70 Dyer's Lodge residents as templates (provenance kept: canon-expanded vs original adaptation). Every
`gm_only` block, at any depth, is split into gm_json and removed from player_json. Campaigns copy templates into
their own npc_profiles later; nothing here is campaign state."""
from __future__ import annotations

import json
import sqlite3
from typing import Any

from .sources import SourceFile


def split_gm_only(value: Any, path: str = "") -> tuple[Any, dict[str, Any]]:
    """(value without any gm_only keys, {dotted path: removed gm_only content})."""
    if isinstance(value, dict):
        public, secret = {}, {}
        for k, v in value.items():
            here = f"{path}.{k}" if path else k
            if k == "gm_only":
                secret[here] = v
                continue
            public[k], inner = split_gm_only(v, here)
            secret.update(inner)
        return public, secret
    if isinstance(value, list):
        public, secret = [], {}
        for i, v in enumerate(value):
            p, s = split_gm_only(v, f"{path}[{i}]")
            public.append(p)
            secret.update(s)
        return public, secret
    return value, {}


def import_npcs(ref: sqlite3.Connection, files: dict[str, SourceFile]) -> None:
    for r in files["npcs"].data["residents"]:
        public, secret = split_gm_only(r)
        start = r.get("at_start") or {}
        ref.execute("""INSERT INTO npc_templates (id, name, provenance, canon_appearance, status, camp_role, usual_location,
                                                  player_json, gm_json, raw_json) VALUES (?,?,?,?,?,?,?,?,?,?)""",
                    (r["id"], r["name"], r.get("provenance"), int(bool(r.get("canon_appearance"))), start.get("status"), r.get("camp_role"),
                     r.get("usual_location"), json.dumps(public, ensure_ascii=False), json.dumps(secret, ensure_ascii=False),
                     json.dumps(r, ensure_ascii=False)))
