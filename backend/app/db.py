"""SQLite campaign database: schema and connection helpers.

Two kinds of tables:
  * State tables hold the *current* structured state the UI renders. They are captured by save points
    and replaced wholesale on restore (see memory/snapshots.py: SNAPSHOT_TABLES).
  * Append-only logs (turns, events, messages) are never deleted. A restore marks rows newer
    than the save point as rolled_back so history stays auditable.

Visibility: every row that could leak GM knowledge either lives in a GM-only table (gm_secrets) or carries
a `visibility` column ('player' | 'gm'). Player-facing queries live in memory/views.py and always filter.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

SCHEMA_VERSION = 2

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);

CREATE TABLE IF NOT EXISTS campaigns (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  is_demo INTEGER NOT NULL DEFAULT 0,
  data_source TEXT NOT NULL DEFAULT 'manual',      -- demo | ledger_import | manual
  current_floor INTEGER,
  current_location TEXT,
  safe_room INTEGER NOT NULL DEFAULT 0,
  game_time_minutes INTEGER NOT NULL DEFAULT 0,    -- elapsed game time on the current floor
  floor_deadline_minutes INTEGER,                  -- game minute at which the floor collapses
  active_character_id INTEGER,                     -- the player's crawler
  active_objective TEXT,
  objective_quests_json TEXT NOT NULL DEFAULT '[]',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS characters (
  id INTEGER PRIMARY KEY,
  campaign_id INTEGER NOT NULL REFERENCES campaigns(id),
  slug TEXT NOT NULL,
  name TEXT NOT NULL,
  kind TEXT NOT NULL,                     -- crawler | npc | contact
  roster_category TEXT,                   -- player_character | core_party | pets_companions | allied_npcs | ...
  crawler_number TEXT,
  species TEXT,
  race TEXT,
  class_name TEXT,
  level INTEGER,
  xp INTEGER,
  status TEXT NOT NULL DEFAULT 'active',  -- active | dead | missing | dormant
  location TEXT,
  in_party INTEGER NOT NULL DEFAULT 0,
  party_order INTEGER NOT NULL DEFAULT 0,
  hp_current INTEGER, hp_max INTEGER,
  mana_current INTEGER, mana_max INTEGER,
  ai_favor INTEGER,
  stats_json TEXT NOT NULL DEFAULT '{}',    -- {"stats":{...}, "modifiers":{...}, "unenhanced":{...}}
  derived_json TEXT NOT NULL DEFAULT '{}',  -- evade, damage_resistance, move, step, size, health_bar, level_progress...
  sheet_json TEXT NOT NULL DEFAULT '{}',    -- remaining player-visible sheet sections (attacks, narrative, visual...)
  bio TEXT,
  portrait_path TEXT,
  provenance TEXT NOT NULL DEFAULT 'manual',
  UNIQUE (campaign_id, slug)
);

CREATE TABLE IF NOT EXISTS skills (
  id INTEGER PRIMARY KEY,
  character_id INTEGER NOT NULL REFERENCES characters(id),
  kind TEXT NOT NULL DEFAULT 'skill',     -- skill | spell | equipment_skill | ability
  key TEXT,
  name TEXT NOT NULL,
  rank INTEGER,
  mana_cost INTEGER,
  passive INTEGER NOT NULL DEFAULT 0,
  marked INTEGER NOT NULL DEFAULT 0,      -- skill-use advancement mark (CORE_MECHANICS 14.5)
  description TEXT,
  properties_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS items (
  id INTEGER PRIMARY KEY,
  campaign_id INTEGER NOT NULL REFERENCES campaigns(id),
  owner_character_id INTEGER REFERENCES characters(id),  -- NULL = shared/stash
  name TEXT NOT NULL,
  quantity INTEGER CHECK (quantity IS NULL OR quantity >= 0),  -- NULL = established item, count unknown
  item_key TEXT,
  deployed INTEGER NOT NULL DEFAULT 0,
  equipped INTEGER NOT NULL DEFAULT 0,
  slot TEXT,
  rarity TEXT,
  description TEXT,
  properties_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS status_effects (
  id INTEGER PRIMARY KEY,
  character_id INTEGER NOT NULL REFERENCES characters(id),
  name TEXT NOT NULL,
  description TEXT,
  polarity TEXT NOT NULL DEFAULT 'neutral',  -- buff | debuff | neutral
  expires_at_minutes INTEGER,                -- campaign game_time_minutes; NULL = until removed
  source TEXT
);

CREATE TABLE IF NOT EXISTS achievements (
  id INTEGER PRIMARY KEY,
  campaign_id INTEGER NOT NULL REFERENCES campaigns(id),
  character_id INTEGER REFERENCES characters(id),
  title TEXT NOT NULL,
  description TEXT,
  game_time_minutes INTEGER,
  created_at TEXT NOT NULL
);

-- Player-visible NPC profile. Private motivations/plans go to gm_secrets.
CREATE TABLE IF NOT EXISTS npc_profiles (
  character_id INTEGER PRIMARY KEY REFERENCES characters(id),
  personality TEXT,
  voice TEXT,
  known_motivation TEXT,
  attitude INTEGER,                      -- -3 hostile .. +3 devoted; NULL = not recorded
  last_seen TEXT
);

CREATE TABLE IF NOT EXISTS relationships (
  id INTEGER PRIMARY KEY,
  campaign_id INTEGER NOT NULL REFERENCES campaigns(id),
  a_character_id INTEGER NOT NULL REFERENCES characters(id),
  b_character_id INTEGER NOT NULL REFERENCES characters(id),
  kind TEXT NOT NULL,
  description TEXT,
  visibility TEXT NOT NULL DEFAULT 'player'
);

CREATE TABLE IF NOT EXISTS locations (
  id INTEGER PRIMARY KEY,
  campaign_id INTEGER NOT NULL REFERENCES campaigns(id),
  floor INTEGER,
  name TEXT NOT NULL,
  description TEXT,
  discovered INTEGER NOT NULL DEFAULT 1,
  visibility TEXT NOT NULL DEFAULT 'player'
);

CREATE TABLE IF NOT EXISTS quests (
  id INTEGER PRIMARY KEY,
  campaign_id INTEGER NOT NULL REFERENCES campaigns(id),
  kind TEXT NOT NULL DEFAULT 'quest',     -- quest | project | crafting
  title TEXT NOT NULL,
  description TEXT,
  status TEXT NOT NULL DEFAULT 'active',  -- active | completed | failed | paused
  priority INTEGER NOT NULL DEFAULT 2,    -- 1 high .. 3 low
  progress TEXT,
  giver TEXT,
  visibility TEXT NOT NULL DEFAULT 'player',
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS encounters (
  id INTEGER PRIMARY KEY,
  campaign_id INTEGER NOT NULL REFERENCES campaigns(id),
  name TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'active',  -- active | resolved
  round INTEGER NOT NULL DEFAULT 1,
  summary TEXT,
  combatants_json TEXT NOT NULL DEFAULT '[]',
  visibility TEXT NOT NULL DEFAULT 'player'
);

-- Generic clocks: advancement checkpoints, faction timers, crafting timers...
CREATE TABLE IF NOT EXISTS clocks (
  id INTEGER PRIMARY KEY,
  campaign_id INTEGER NOT NULL REFERENCES campaigns(id),
  key TEXT NOT NULL,
  label TEXT NOT NULL,
  kind TEXT NOT NULL DEFAULT 'custom',    -- custom | advancement_checkpoint | faction | crafting
  current INTEGER NOT NULL DEFAULT 0,
  maximum INTEGER,
  unit TEXT,
  visibility TEXT NOT NULL DEFAULT 'player',
  UNIQUE (campaign_id, key)
);

CREATE TABLE IF NOT EXISTS house_rules (
  id INTEGER PRIMARY KEY,
  campaign_id INTEGER NOT NULL REFERENCES campaigns(id),
  section TEXT,                                        -- e.g. "7.9" for CORE_MECHANICS sections
  title TEXT NOT NULL,
  text TEXT NOT NULL,
  label TEXT,                                          -- BOOK | CAMPAIGN | INTERPRETATION | ONE-SCENE | OPEN | mixed
  always_include INTEGER NOT NULL DEFAULT 0,           -- sent to the GM on every turn
  status TEXT NOT NULL DEFAULT 'canonical',            -- canonical | provisional
  mechanical_status TEXT NOT NULL DEFAULT 'pending',   -- implemented | pending | narrative_only
  source TEXT
);

-- Established adjudications made during play.
CREATE TABLE IF NOT EXISTS rulings (
  id INTEGER PRIMARY KEY,
  campaign_id INTEGER NOT NULL REFERENCES campaigns(id),
  turn_id INTEGER,
  topic TEXT NOT NULL,
  ruling TEXT NOT NULL,
  certainty TEXT NOT NULL DEFAULT 'provisional',       -- canonical | provisional | uncertain
  label TEXT NOT NULL DEFAULT 'INTERPRETATION',        -- BOOK | CAMPAIGN | INTERPRETATION | ONE-SCENE | OPEN
  sources_json TEXT NOT NULL DEFAULT '[]',
  created_at TEXT NOT NULL
);

-- Durable facts with explicit certainty. visibility='gm' facts are never served to players.
CREATE TABLE IF NOT EXISTS lore_facts (
  id INTEGER PRIMARY KEY,
  campaign_id INTEGER NOT NULL REFERENCES campaigns(id),
  subject TEXT NOT NULL,
  fact TEXT NOT NULL,
  certainty TEXT NOT NULL DEFAULT 'canonical',         -- canonical | uncertain | provisional
  visibility TEXT NOT NULL DEFAULT 'player',
  source TEXT,
  created_at TEXT NOT NULL
);

-- GM-only. Never joined into a player query.
CREATE TABLE IF NOT EXISTS gm_secrets (
  id INTEGER PRIMARY KEY,
  campaign_id INTEGER NOT NULL REFERENCES campaigns(id),
  subject TEXT NOT NULL,
  kind TEXT NOT NULL DEFAULT 'note',      -- plot | motivation | surprise | note | import
  content TEXT NOT NULL,
  revealed INTEGER NOT NULL DEFAULT 0,
  source TEXT,
  created_at TEXT NOT NULL
);

-- Verbatim imported records (YAML/Markdown), split into sections for retrieval. Nothing is lost on import
-- even when it has no structured table yet. visibility='gm' for gm_state/**.
CREATE TABLE IF NOT EXISTS source_sections (
  id INTEGER PRIMARY KEY,
  campaign_id INTEGER NOT NULL REFERENCES campaigns(id),
  document_path TEXT NOT NULL,
  section TEXT NOT NULL,
  content TEXT NOT NULL,
  visibility TEXT NOT NULL DEFAULT 'player'
);
CREATE VIRTUAL TABLE IF NOT EXISTS source_sections_fts USING fts5(
  section, content, content='source_sections', content_rowid='id', tokenize='porter unicode61');

CREATE TABLE IF NOT EXISTS memory_summaries (
  id INTEGER PRIMARY KEY,
  campaign_id INTEGER NOT NULL REFERENCES campaigns(id),
  from_message_id INTEGER NOT NULL,
  to_message_id INTEGER NOT NULL,
  summary TEXT NOT NULL,
  created_at TEXT NOT NULL
);

-- ---------- append-only logs ----------
CREATE TABLE IF NOT EXISTS art_jobs (       -- campaign artwork rendered by the local ComfyUI (art/service.py)
  id INTEGER PRIMARY KEY,
  campaign_id INTEGER NOT NULL REFERENCES campaigns(id),
  kind TEXT NOT NULL,                     -- character | item | mob | scene
  subject_id INTEGER,                     -- characters.id / items.id / scenes.id; NULL for mobs (matched by name)
  subject_name TEXT NOT NULL,
  prompt TEXT NOT NULL,
  negative TEXT NOT NULL DEFAULT '',
  width INTEGER NOT NULL,
  height INTEGER NOT NULL,
  seed INTEGER NOT NULL,
  checkpoint TEXT,
  status TEXT NOT NULL,                   -- suggested | queued | running | done | failed | dismissed
  source TEXT NOT NULL DEFAULT 'player',  -- player | gm
  reason TEXT,
  note TEXT,
  error TEXT,
  file TEXT,
  is_current INTEGER NOT NULL DEFAULT 0,
  turn_id INTEGER,
  created_at TEXT NOT NULL,
  started_at TEXT,
  finished_at TEXT
);
CREATE TABLE IF NOT EXISTS corrections (    -- owner-approved corrections of canonical state, with evidence (reconcile.py)
  id INTEGER PRIMARY KEY,
  campaign_id INTEGER NOT NULL REFERENCES campaigns(id),
  subject TEXT NOT NULL,
  decision TEXT NOT NULL,
  evidence_json TEXT NOT NULL DEFAULT '[]',
  ops_json TEXT NOT NULL DEFAULT '[]',
  results_json TEXT NOT NULL DEFAULT '[]',
  ledger_edits_json TEXT NOT NULL DEFAULT '[]',
  resolves_json TEXT NOT NULL DEFAULT '[]',   -- import-report keys this settles (reconcile.report_keys)
  decided_by TEXT NOT NULL,
  save_point_id INTEGER,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_art_subject ON art_jobs(campaign_id, kind, subject_id);
CREATE TABLE IF NOT EXISTS ability_explanations (  -- what a skill/spell/ability does, for the dossier (derived; not campaign canon)
  id INTEGER PRIMARY KEY,
  campaign_id INTEGER NOT NULL REFERENCES campaigns(id),
  key TEXT NOT NULL,                      -- kind:normalized name (see memory/abilities.py)
  name TEXT NOT NULL,
  summary TEXT NOT NULL,                  -- one line
  explanation TEXT NOT NULL,              -- what it actually does, in plain words
  basis TEXT NOT NULL,                    -- rulebook | record | homebrew | template
  citation TEXT,                          -- rulebook page, or the ledger record it came from
  model TEXT,                             -- who wrote it: codex / template
  created_at TEXT NOT NULL,
  UNIQUE (campaign_id, key)
);
CREATE TABLE IF NOT EXISTS art_subjects (   -- the player's appearance notes per subject (art only; not campaign canon)
  campaign_id INTEGER NOT NULL REFERENCES campaigns(id),
  kind TEXT NOT NULL,
  subject_key TEXT NOT NULL,              -- characters.id / items.id / scenes.id as text, or a Mob's lower-case name
  appearance TEXT NOT NULL DEFAULT '',
  updated_at TEXT NOT NULL,
  PRIMARY KEY (campaign_id, kind, subject_key)
);

CREATE TABLE IF NOT EXISTS turns (
  id INTEGER PRIMARY KEY,
  campaign_id INTEGER NOT NULL REFERENCES campaigns(id),
  idempotency_key TEXT NOT NULL,
  status TEXT NOT NULL,                   -- processing | completed | failed
  player_input TEXT NOT NULL,
  character_id INTEGER,
  provider TEXT,
  response_json TEXT,
  error TEXT,
  rolled_back INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  completed_at TEXT,
  UNIQUE (campaign_id, idempotency_key)
);

CREATE TABLE IF NOT EXISTS messages (
  id INTEGER PRIMARY KEY,
  campaign_id INTEGER NOT NULL REFERENCES campaigns(id),
  turn_id INTEGER,
  role TEXT NOT NULL,                     -- player | gm | system
  content TEXT NOT NULL,
  meta_json TEXT NOT NULL DEFAULT '{}',
  visibility TEXT NOT NULL DEFAULT 'player',
  rolled_back INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY,
  campaign_id INTEGER NOT NULL REFERENCES campaigns(id),
  turn_id INTEGER,
  type TEXT NOT NULL,
  summary TEXT NOT NULL,
  payload_json TEXT NOT NULL DEFAULT '{}',
  visibility TEXT NOT NULL DEFAULT 'player',
  game_time_minutes INTEGER,
  rolled_back INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS save_points (
  id INTEGER PRIMARY KEY,
  campaign_id INTEGER NOT NULL REFERENCES campaigns(id),
  label TEXT NOT NULL,
  kind TEXT NOT NULL,                     -- auto | manual | import | pre_restore
  turn_id INTEGER,
  max_message_id INTEGER NOT NULL,
  max_event_id INTEGER NOT NULL,
  max_turn_id INTEGER NOT NULL,
  snapshot_json TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS restore_log (
  id INTEGER PRIMARY KEY,
  campaign_id INTEGER NOT NULL,
  save_point_id INTEGER NOT NULL,
  backup_save_point_id INTEGER,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS import_reports (
  id INTEGER PRIMARY KEY,
  campaign_id INTEGER,
  source_path TEXT NOT NULL,
  report_json TEXT NOT NULL,
  created_at TEXT NOT NULL
);

-- Write-backs to the Git campaign ledger. base_commit -> new_commit; hw_json holds high-water ids
-- (rolls, events, secrets, facts, rulings, achievements) already written, so the next sync appends only newer rows.
CREATE TABLE IF NOT EXISTS ledger_syncs (
  id INTEGER PRIMARY KEY,
  campaign_id INTEGER NOT NULL REFERENCES campaigns(id),
  ledger_path TEXT NOT NULL,
  base_commit TEXT NOT NULL,
  new_commit TEXT NOT NULL,
  hw_json TEXT NOT NULL,
  summary_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL
);

-- ---------- party organization, scenes, memory layers (schema v2) ----------
-- Where each subgroup of the roster is and what it is doing; characters.assignment points at key.
CREATE TABLE IF NOT EXISTS party_groups (
  id INTEGER PRIMARY KEY,
  campaign_id INTEGER NOT NULL REFERENCES campaigns(id),
  key TEXT NOT NULL,
  label TEXT NOT NULL,
  location TEXT,
  activity TEXT,
  status TEXT NOT NULL DEFAULT 'active',     -- active | disbanded
  source TEXT,
  UNIQUE (campaign_id, key)
);

-- The current scene the GM is running (one active per campaign) and closed scenes with their summaries.
CREATE TABLE IF NOT EXISTS scenes (
  id INTEGER PRIMARY KEY,
  campaign_id INTEGER NOT NULL REFERENCES campaigns(id),
  title TEXT NOT NULL,
  location TEXT,
  group_key TEXT,
  situation TEXT,
  conditions TEXT,
  threats TEXT,
  participants_json TEXT NOT NULL DEFAULT '[]',
  status TEXT NOT NULL DEFAULT 'active',     -- active | closed
  summary TEXT,
  started_game_minutes INTEGER,
  ended_game_minutes INTEGER,
  source TEXT,
  created_at TEXT NOT NULL
);

-- Episodic and per-character memories (canonical world facts stay in lore_facts; GM secrets in gm_secrets).
CREATE TABLE IF NOT EXISTS memories (
  id INTEGER PRIMARY KEY,
  campaign_id INTEGER NOT NULL REFERENCES campaigns(id),
  layer TEXT NOT NULL,                       -- episodic | npc
  subject TEXT NOT NULL,                     -- character slug or free text
  character_id INTEGER REFERENCES characters(id),
  content TEXT NOT NULL,
  visibility TEXT NOT NULL DEFAULT 'player', -- player = what the party saw/learned; gm = private beat
  importance INTEGER NOT NULL DEFAULT 2,     -- 1 high .. 3 low
  turn_id INTEGER,
  game_time_minutes INTEGER,
  created_at TEXT NOT NULL
);

-- Limited-use abilities and cooldowns (once per scene/day/floor/rest, or N minutes).
CREATE TABLE IF NOT EXISTS ability_uses (
  id INTEGER PRIMARY KEY,
  campaign_id INTEGER NOT NULL REFERENCES campaigns(id),
  character_id INTEGER NOT NULL REFERENCES characters(id),
  ability TEXT NOT NULL,
  per TEXT NOT NULL,                         -- scene | day | floor | short_rest | long_rest | minutes
  uses_allowed INTEGER NOT NULL DEFAULT 1,
  uses INTEGER NOT NULL DEFAULT 0,
  cooldown_minutes INTEGER,
  last_used_minutes INTEGER,
  UNIQUE (character_id, ability)
);

-- GM-only per-turn diagnostics: the context packet, tool rounds, usage and the integrity report.
CREATE TABLE IF NOT EXISTS turn_diagnostics (
  turn_id INTEGER PRIMARY KEY,
  campaign_id INTEGER NOT NULL,
  context_text TEXT NOT NULL,
  steps_json TEXT NOT NULL DEFAULT '[]',
  usage_json TEXT NOT NULL DEFAULT '{}',
  integrity_json TEXT NOT NULL DEFAULT '[]',
  created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_messages_campaign ON messages(campaign_id, id);
CREATE INDEX IF NOT EXISTS ix_events_campaign ON events(campaign_id, id);
CREATE INDEX IF NOT EXISTS ix_items_owner ON items(campaign_id, owner_character_id);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30, check_same_thread=False, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn


# Columns added after v1. init_db adds any that an existing database lacks; nothing is ever dropped or rewritten.
COLUMN_MIGRATIONS = [
    ("characters", "assignment", "TEXT"),
    ("campaigns", "day_length_minutes", "INTEGER"),
    ("quests", "details_json", "TEXT NOT NULL DEFAULT '{}'"),
    ("quests", "due_at_minutes", "INTEGER"),
    ("quests", "owner_character_id", "INTEGER"),
    ("turns", "stage_json", "TEXT"),
    ("art_jobs", "engine", "TEXT NOT NULL DEFAULT 'comfy'"),
    ("characters", "portrait_crop_json", "TEXT"),
    ("corrections", "resolves_json", "TEXT NOT NULL DEFAULT '[]'"),
    ("locations", "visited", "INTEGER NOT NULL DEFAULT 1"),
    ("art_subjects", "avoid", "TEXT NOT NULL DEFAULT ''"),
    ("art_subjects", "features", "TEXT NOT NULL DEFAULT ''"),  # 2-4 distinctive features an image must show, weighted in the prompt  # what an image model must not draw from the name ("porcupine" for a trap)  # 0 = known (seen, reported) but not entered (memory/places.py)
    ("rulings", "review", "TEXT"),          # NULL pending | approved | rejected (reference/rulecenter.py: only precedents reach later turns)
    ("rulings", "review_note", "TEXT"),
    ("rulings", "reviewed_at", "TEXT"),
    ("campaigns", "archived", "INTEGER NOT NULL DEFAULT 0"),
    ("campaigns", "floor_clock_pending_minutes", "INTEGER"),
    ("achievements", "reward", "TEXT"),   # the Reward line as announced ("You've received a Silver Adventurer Box!", or the joke)
    ("achievements", "box_item_id", "INTEGER"),  # the loot box it granted (items.properties loot_box)  # a floor clock waiting to start (op start_floor_clock); deadline is NULL meanwhile  # 1 = hidden from the campaign picker (campaigns.py)
    ("campaigns", "objective_quests_json", "TEXT NOT NULL DEFAULT '[]'"),  # ordered quest ids that make up the immediate objective  # {cx, cy, k, ar}: square crop of the portrait image (null = default)  # comfy | codex (art/codex_image.py)  # staged tool calls and roll records while a turn is in flight
]


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    for table, column, decl in COLUMN_MIGRATIONS:
        have = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        if column not in have:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
    conn.execute("INSERT OR IGNORE INTO meta(key, value) VALUES ('schema_version', ?)", (str(SCHEMA_VERSION),))
    conn.execute("UPDATE meta SET value = ? WHERE key = 'schema_version'", (str(SCHEMA_VERSION),))


def next_campaign_id(conn: sqlite3.Connection) -> int:
    """Claim a campaign id that was never used before, even by a deleted campaign (campaigns.py): links, saved views and
    art folders named after an old id must not point at a new campaign. Call inside the transaction that inserts it."""
    used = conn.execute("SELECT value FROM meta WHERE key = 'last_campaign_id'").fetchone()
    top = conn.execute("SELECT COALESCE(MAX(id), 0) FROM campaigns").fetchone()[0]
    cid = max(int(used[0]) if used else 0, top) + 1
    conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('last_campaign_id', ?)", (str(cid),))
    return cid


def remember_campaign_id(conn: sqlite3.Connection, cid: int) -> None:
    """Before deleting campaign cid, record it as used so next_campaign_id never hands it out again."""
    used = conn.execute("SELECT value FROM meta WHERE key = 'last_campaign_id'").fetchone()
    if not used or int(used[0]) < cid:
        conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('last_campaign_id', ?)", (str(cid),))


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """Explicit BEGIN IMMEDIATE ... COMMIT; rolls back on any exception."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")


def row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    out = dict(row)
    for key in list(out):
        if key.endswith("_json") and isinstance(out[key], str):
            out[key[:-5]] = json.loads(out.pop(key))
        elif key.endswith("_json") and out[key] is None:  # NULL json column: same decoded name, value None
            out[key[:-5]] = out.pop(key)
    return out


def rows(conn: sqlite3.Connection, sql: str, params: tuple | list = ()) -> list[dict[str, Any]]:
    return [row_to_dict(r) for r in conn.execute(sql, params).fetchall()]  # type: ignore[misc]


def one(conn: sqlite3.Connection, sql: str, params: tuple | list = ()) -> dict[str, Any] | None:
    return row_to_dict(conn.execute(sql, params).fetchone())


def get_setting(conn: sqlite3.Connection, key: str, default: str | None = None) -> str | None:
    r = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return r["value"] if r else default


def set_setting(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO settings(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )
