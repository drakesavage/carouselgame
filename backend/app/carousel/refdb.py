"""data/reference.db: Carousel source data as imported (carousel/importer.py). Disposable and rebuildable; campaign
state refers to it only by stable IDs (CAR-…, OM-…, CAR-NPC-…, role and ticket IDs). *_json columns hold JSON text."""
from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS manifest (key TEXT PRIMARY KEY, path TEXT NOT NULL, sha256 TEXT NOT NULL, imported_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS places (
  id TEXT PRIMARY KEY, name TEXT, category TEXT, region TEXT, map_id TEXT, north REAL, west REAL, px REAL, py REAL,
  requires_json TEXT NOT NULL DEFAULT '[]', spoiler TEXT, status TEXT, in_town_map INTEGER NOT NULL DEFAULT 0, raw_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS place_aliases (alias_id TEXT PRIMARY KEY, target_id TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS place_names (name TEXT NOT NULL, place_id TEXT NOT NULL, PRIMARY KEY (name, place_id));
CREATE TABLE IF NOT EXISTS graph_nodes (id TEXT PRIMARY KEY, location_id TEXT, map_id TEXT, north REAL, west REAL, requires_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS graph_edges (
  id TEXT PRIMARY KEY, from_id TEXT NOT NULL, to_id TEXT NOT NULL, kind TEXT, directed INTEGER NOT NULL, requires_json TEXT NOT NULL,
  cost REAL, length_units REAL, geometry_json TEXT NOT NULL, raw_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS omens (
  id TEXT PRIMARY KEY, record_type TEXT NOT NULL, description TEXT, story_lock TEXT, playable_title TEXT, place_id TEXT,
  anchor_method TEXT, placement TEXT, spawn_mode TEXT, choice_trigger TEXT, beats_json TEXT NOT NULL DEFAULT '{}',
  raw_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS hub_seed_pools (hub_id TEXT PRIMARY KEY, raw_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS omen_beats (omen_id TEXT PRIMARY KEY, dossier_json TEXT NOT NULL, sealed_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS roles (id TEXT PRIMARY KEY, name TEXT NOT NULL, kind TEXT NOT NULL, parent TEXT, raw_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS tickets (
  id TEXT PRIMARY KEY, name TEXT NOT NULL, source_role_id TEXT, relationship TEXT, text TEXT NOT NULL, raw_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS start_profiles (
  role_id TEXT PRIMARY KEY, mettle INTEGER NOT NULL, moxie INTEGER NOT NULL, hustle INTEGER NOT NULL, savvy INTEGER NOT NULL,
  grit INTEGER NOT NULL, base_pa INTEGER NOT NULL, provenance TEXT);
CREATE TABLE IF NOT EXISTS tropes (
  id TEXT PRIMARY KEY, name TEXT NOT NULL, full_player INTEGER, has_enemy INTEGER, reference_only INTEGER, variants_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS npc_templates (
  id TEXT PRIMARY KEY, name TEXT NOT NULL, provenance TEXT, canon_appearance INTEGER, status TEXT, camp_role TEXT,
  usual_location TEXT, player_json TEXT NOT NULL, gm_json TEXT NOT NULL, raw_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS import_report (id INTEGER PRIMARY KEY, category TEXT NOT NULL, item_id TEXT, message TEXT NOT NULL);
"""


def connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), check_same_thread=False)   # FastAPI opens a dependency and runs the endpoint on different threads
    conn.row_factory = sqlite3.Row
    return conn


def init(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)


def report(conn: sqlite3.Connection, category: str, item_id: str | None, message: str) -> None:
    conn.execute("INSERT INTO import_report (category, item_id, message) VALUES (?,?,?)", (category, item_id, message))


def count(conn: sqlite3.Connection, table: str, where: str = "1=1") -> int:
    return conn.execute(f"SELECT COUNT(*) FROM {table} WHERE {where}").fetchone()[0]
