"""Carousel reference data at play time: reference.db is ATTACHed to campaign connections as `ref`, so engine ops
and the GM briefing read ticket text, roles and profiles from the imported data, never from the model.
Owned-ticket refs are `hb.ticket.*` (homebrew tickets) or `trope:<id>` (trope bible)."""
from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path
from typing import Literal

from ..db import connect

ONCE_PER = re.compile(r"^\s*once per (scene|story(?:line)?)\b", re.I)


def attach_reference(conn: sqlite3.Connection, path: Path) -> bool:
    """Call right after connect() (ATTACH is not allowed inside a transaction)."""
    if not Path(path).exists():
        return False
    conn.execute("ATTACH DATABASE ? AS ref", (str(path),))
    return True


def open_campaign(settings) -> sqlite3.Connection:
    conn = connect(settings.campaign_db)
    attach_reference(conn, settings.reference_db)
    return conn


def has_reference(conn: sqlite3.Connection) -> bool:
    return any(r[1] == "ref" for r in conn.execute("PRAGMA database_list"))


def require_reference(conn: sqlite3.Connection) -> None:
    if not has_reference(conn):
        from ..rules.engine import ChangeError  # lazy: the rules package imports this module (carousel_ops)
        raise ChangeError("reference data not imported; run import-data.cmd")


def recharge_of(defn: dict) -> Literal["scene", "story"] | None:
    explicit = (defn.get("recharge") or "").lower()
    if explicit:
        return "scene" if explicit == "scene" else "story" if explicit.startswith("story") else None
    m = ONCE_PER.match(defn.get("rule") or "")
    return None if not m else "scene" if m.group(1).lower() == "scene" else "story"


def ticket_definition(conn: sqlite3.Connection, ref: str) -> dict | None:
    """{ref, name, text, uses_max, recharge, source}, or None if the reference data has no such ticket."""
    require_reference(conn)
    if ref.startswith("trope:"):
        r = conn.execute("SELECT name, variants_json FROM ref.tropes WHERE id = ?", (ref[len("trope:"):],)).fetchone()
        if not r:
            return None
        text = "\n".join(v.get("description") or "" for v in json.loads(r["variants_json"]) if v.get("description"))
        return {"ref": ref, "name": r["name"], "text": text, "uses_max": None, "recharge": None, "source": "trope_bible"}
    r = conn.execute("SELECT name, text, raw_json FROM ref.tickets WHERE id = ?", (ref,)).fetchone()
    if not r:
        return None
    raw = json.loads(r["raw_json"])
    defn = raw.get("original_ticket_definition") or raw.get("shared_pool_entry") or {}
    defn = {**defn, "rule": defn.get("rule") or r["text"]}
    return {"ref": ref, "name": r["name"], "text": r["text"], "uses_max": defn.get("uses"), "recharge": recharge_of(defn), "source": "homebrew"}


def place_image(conn: sqlite3.Connection, place_id: str) -> str | None:
    """The place's identity image, relative to Location_Art_Production/assets (served at /location-art/)."""
    if not has_reference(conn):
        return None
    r = conn.execute("SELECT rel_path FROM ref.place_images WHERE place_id = ? AND kind = 'identity'", (place_id,)).fetchone()
    return r[0] if r else None
