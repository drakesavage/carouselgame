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
  current_location TEXT,
  safe_room INTEGER NOT NULL DEFAULT 0,
  game_time_minutes INTEGER NOT NULL DEFAULT 0,    -- game time elapsed in the campaign
  active_character_id INTEGER,                     -- the player character
  active_objective TEXT,
  objective_quests_json TEXT NOT NULL DEFAULT '[]',
  node_id TEXT,                                    -- the player character's group's travel-graph node (carousel/travel.py)
  travel_seq INTEGER NOT NULL DEFAULT 0,           -- travels so far; seeds the interruption decision (carousel/interruptions.py)
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS characters (
  id INTEGER PRIMARY KEY,
  campaign_id INTEGER NOT NULL REFERENCES campaigns(id),
  slug TEXT NOT NULL,
  name TEXT NOT NULL,
  kind TEXT NOT NULL,                     -- pc | npc | contact
  roster_category TEXT,                   -- player_character | core_party | pets_companions | allied_npcs | ...
  species TEXT,
  status TEXT NOT NULL DEFAULT 'active',  -- active | dead | missing | dormant
  location TEXT,
  in_party INTEGER NOT NULL DEFAULT 0,
  party_order INTEGER NOT NULL DEFAULT 0,
  sheet_json TEXT NOT NULL DEFAULT '{}',    -- remaining player-visible sheet sections (attacks, narrative, visual...)
  bio TEXT,
  portrait_path TEXT,
  provenance TEXT NOT NULL DEFAULT 'manual',
  mettle INTEGER, moxie INTEGER, hustle INTEGER, savvy INTEGER, grit INTEGER,  -- base stats (NULL = not assigned yet)
  role_id TEXT,                            -- starting Archetype (reference roles, kind base)
  aspect_id TEXT,
  advanced_role_id TEXT,
  ruleset_version TEXT,                    -- e.g. SB-0.4.1/role-0.4.2, pinned when stats are initialized
  role_reason TEXT,                        -- why Carousel assigned the role (player-visible)
  stars INTEGER NOT NULL DEFAULT 0,        -- unspent reward stars (5 = 1 stat point; rules/story_ops.py)
  pending_stat_points INTEGER NOT NULL DEFAULT 0,
  starless_streak INTEGER NOT NULL DEFAULT 0,  -- concluded stories in a row with no stars gained
  UNIQUE (campaign_id, slug)
);

-- Trope tickets a character owns. ticket_ref: hb.ticket.* (reference tickets) or trope:<id> (reference tropes).
CREATE TABLE IF NOT EXISTS character_tickets (
  id INTEGER PRIMARY KEY,
  campaign_id INTEGER NOT NULL REFERENCES campaigns(id),
  character_id INTEGER NOT NULL REFERENCES characters(id),
  ticket_ref TEXT NOT NULL,
  equipped INTEGER NOT NULL DEFAULT 0,
  uses_left INTEGER,                       -- NULL = no counted uses (the GM applies the text)
  uses_max INTEGER,
  recharge TEXT,                           -- scene | story | NULL
  hidden INTEGER NOT NULL DEFAULT 0,       -- 1 = an NPC's unrevealed trope (GM-only)
  source TEXT,
  acquired_turn_id INTEGER,
  UNIQUE (character_id, ticket_ref)
);

-- Temporary stat and Plot Armor changes; base stats are never edited by them.
CREATE TABLE IF NOT EXISTS stat_modifiers (
  id INTEGER PRIMARY KEY,
  campaign_id INTEGER NOT NULL REFERENCES campaigns(id),
  character_id INTEGER NOT NULL REFERENCES characters(id),
  target TEXT NOT NULL CHECK (target IN ('mettle','moxie','hustle','savvy','grit','plot_armor')),
  op TEXT NOT NULL CHECK (op IN ('add','multiply')),
  value REAL NOT NULL,
  source TEXT NOT NULL,
  scope TEXT NOT NULL CHECK (scope IN ('scene','story','until_removed')),
  hidden INTEGER NOT NULL DEFAULT 0,       -- 1 = its source is a secret (e.g. an NPC's hidden trope): GM-only
  turn_id INTEGER
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

-- Player-visible NPC profile. Private motivations/plans go to gm_secrets.
CREATE TABLE IF NOT EXISTS npc_profiles (
  character_id INTEGER PRIMARY KEY REFERENCES characters(id),
  personality TEXT,
  voice TEXT,
  known_motivation TEXT,
  attitude INTEGER,                      -- -3 hostile .. +3 devoted; NULL = not recorded
  last_seen TEXT,
  ref_id TEXT                            -- Carousel resident template ID (reference.db npc_templates), when imported
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
  name TEXT NOT NULL,
  description TEXT,
  discovered INTEGER NOT NULL DEFAULT 1,
  visibility TEXT NOT NULL DEFAULT 'player',
  ref_id TEXT                            -- Carousel place ID (reference.db places / place_aliases), when known
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
  ticket_ref TEXT,                         -- the ticket this ruling interprets, if any
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
  kind TEXT,                              -- NULL (player input) | arrival | arrival_omen
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
  node_id TEXT,                              -- travel-graph node
  UNIQUE (campaign_id, key)
);

-- Carousel map state: places the group knows, story flags that open graph edges (GM-only), Omens the group has seen.
CREATE TABLE IF NOT EXISTS known_places (
  campaign_id INTEGER NOT NULL REFERENCES campaigns(id),
  place_id TEXT NOT NULL,
  how TEXT,                                  -- start | visited | told
  known_at_minutes INTEGER,
  PRIMARY KEY (campaign_id, place_id)
);
CREATE TABLE IF NOT EXISTS campaign_flags (
  campaign_id INTEGER NOT NULL REFERENCES campaigns(id),
  flag TEXT NOT NULL,
  set_at_minutes INTEGER,
  source TEXT,
  PRIMARY KEY (campaign_id, flag)
);
-- Venues (carousel/venues.py): zones the group has stood in, and story overlays on a venue's links. Overlays name their
-- story and are lifted (never deleted) when it concludes; for add_zone, a is the new zone and b the zone it joins.
CREATE TABLE IF NOT EXISTS zone_visits (
  campaign_id INTEGER NOT NULL REFERENCES campaigns(id),
  zone_id TEXT NOT NULL,
  first_minutes INTEGER,
  PRIMARY KEY (campaign_id, zone_id)
);
CREATE TABLE IF NOT EXISTS venue_overlays (
  id INTEGER PRIMARY KEY,
  campaign_id INTEGER NOT NULL REFERENCES campaigns(id),
  story_id INTEGER,
  venue_id TEXT NOT NULL,
  kind TEXT NOT NULL CHECK (kind IN ('block', 'add_zone')),
  a TEXT NOT NULL,
  b TEXT NOT NULL,
  label TEXT,
  reason TEXT,
  lifted INTEGER NOT NULL DEFAULT 0,
  created_minutes INTEGER
);
CREATE TABLE IF NOT EXISTS omen_states (
  campaign_id INTEGER NOT NULL REFERENCES campaigns(id),
  omen_id TEXT NOT NULL,
  stage TEXT NOT NULL CHECK (stage IN ('visible', 'examined')),
  seen_at_node TEXT,
  seen_at_minutes INTEGER,
  PRIMARY KEY (campaign_id, omen_id)
);

-- Carousel stories (rules/story_ops.py). One row per story instance; story_packets is GM-only (the sealed truth copied
-- from the beat dossier at Choice, its hash, the current screenplay and ending profiles); revisions append, never edit.
CREATE TABLE IF NOT EXISTS stories (
  id INTEGER PRIMARY KEY,
  campaign_id INTEGER NOT NULL REFERENCES campaigns(id),
  omen_id TEXT NOT NULL,
  story_lock TEXT NOT NULL,
  title TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'concluded')),
  phase TEXT NOT NULL,
  pressure INTEGER NOT NULL DEFAULT 0,         -- 0-6, GM-only
  camera TEXT NOT NULL DEFAULT 'off' CHECK (camera IN ('on', 'off')),
  version INTEGER NOT NULL DEFAULT 0,          -- screenplay version (optimistic concurrency for revisions)
  choice_action TEXT,
  started_minutes INTEGER,
  ended_minutes INTEGER,
  ending TEXT,
  ending_reason TEXT,
  novelty INTEGER,
  started_turn_id INTEGER,
  ended_turn_id INTEGER
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_stories_active ON stories(campaign_id) WHERE status = 'active';
CREATE UNIQUE INDEX IF NOT EXISTS ux_stories_active_lock ON stories(campaign_id, story_lock) WHERE status = 'active';
CREATE TABLE IF NOT EXISTS story_packets (
  story_id INTEGER PRIMARY KEY REFERENCES stories(id),
  sealed_json TEXT NOT NULL,
  sealed_sha256 TEXT NOT NULL,
  screenplay_json TEXT NOT NULL,               -- {version, phase_plan, notes}
  endings_json TEXT NOT NULL                   -- {ending: {predicate, ...}}
);
CREATE TABLE IF NOT EXISTS story_cast (
  story_id INTEGER NOT NULL REFERENCES stories(id),
  character_id INTEGER NOT NULL REFERENCES characters(id),
  casting TEXT NOT NULL CHECK (casting IN ('lead', 'support', 'witness', 'sacrifice', 'outsider')),
  joined_phase TEXT,
  screen_moments INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (story_id, character_id)
);
CREATE TABLE IF NOT EXISTS story_revisions (   -- GM-only except public_summary
  id INTEGER PRIMARY KEY,
  story_id INTEGER NOT NULL REFERENCES stories(id),
  version INTEGER NOT NULL,
  character_id INTEGER,
  ticket_ref TEXT,
  scope TEXT NOT NULL,
  operations_json TEXT NOT NULL,
  before_json TEXT NOT NULL,
  after_json TEXT NOT NULL,
  public_summary TEXT NOT NULL,
  reason TEXT,
  turn_id INTEGER
);
-- Fate (carousel/fate.py, rules/fate_ops.py). death_events is append-only: a later restore or rescue never deletes a row.
-- missing_posters: one per character left behind by a wipe; the Diner board shows those not held and not found.
CREATE TABLE IF NOT EXISTS death_events (
  id INTEGER PRIMARY KEY,
  campaign_id INTEGER NOT NULL REFERENCES campaigns(id),
  character_id INTEGER NOT NULL REFERENCES characters(id),
  story_id INTEGER,
  phase TEXT,
  cause TEXT,                                  -- GM-only (it can name the antagonist)
  last_action TEXT,
  earned_effects_json TEXT NOT NULL DEFAULT '[]',
  witnesses_json TEXT NOT NULL DEFAULT '[]',
  node_id TEXT,
  venue_id TEXT,
  zone_id TEXT,
  items_json TEXT NOT NULL DEFAULT '[]',
  minutes INTEGER,
  turn_id INTEGER
);
CREATE TABLE IF NOT EXISTS missing_posters (
  id INTEGER PRIMARY KEY,
  campaign_id INTEGER NOT NULL REFERENCES campaigns(id),
  character_id INTEGER NOT NULL REFERENCES characters(id),
  group_no INTEGER NOT NULL,
  name TEXT NOT NULL,
  plot_armor INTEGER,
  archetype TEXT,
  last_seen TEXT,
  story_id INTEGER,
  story_lock TEXT,
  death_event_id INTEGER,
  posted_minutes INTEGER,
  held_by_character_id INTEGER,
  rescued_minutes INTEGER
);
CREATE TABLE IF NOT EXISTS danger_warnings (
  id INTEGER PRIMARY KEY,
  campaign_id INTEGER NOT NULL REFERENCES campaigns(id),
  story_id INTEGER,
  character_id INTEGER,                        -- NULL = the whole group
  rule TEXT NOT NULL,
  warning TEXT NOT NULL,
  turn_id INTEGER,
  minutes INTEGER
);
CREATE TABLE IF NOT EXISTS story_spoilers (
  campaign_id INTEGER NOT NULL REFERENCES campaigns(id),
  story_lock TEXT NOT NULL,
  element TEXT NOT NULL,
  source TEXT,
  PRIMARY KEY (campaign_id, story_lock, element)
);
CREATE TABLE IF NOT EXISTS advanced_marks (
  character_id INTEGER NOT NULL REFERENCES characters(id),
  track_role_id TEXT NOT NULL,
  marks INTEGER NOT NULL DEFAULT 0,
  unlocked INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (character_id, track_role_id)
);
CREATE TABLE IF NOT EXISTS mark_awards (       -- one mark per track per story
  story_id INTEGER NOT NULL REFERENCES stories(id),
  character_id INTEGER NOT NULL REFERENCES characters(id),
  track_role_id TEXT NOT NULL,
  feat TEXT,
  PRIMARY KEY (story_id, character_id, track_role_id)
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
    ("locations", "ref_id", "TEXT"),
    ("npc_profiles", "ref_id", "TEXT"),
    ("rulings", "ticket_ref", "TEXT"),
    ("stat_modifiers", "hidden", "INTEGER NOT NULL DEFAULT 0"),
    *[("characters", c, "INTEGER") for c in ("mettle", "moxie", "hustle", "savvy", "grit")],
    *[("characters", c, "TEXT") for c in ("role_id", "aspect_id", "advanced_role_id", "ruleset_version", "role_reason")],
    ("characters", "assignment", "TEXT"),
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
    ("campaigns", "objective_quests_json", "TEXT NOT NULL DEFAULT '[]'"),
    ("campaigns", "node_id", "TEXT"),
    ("campaigns", "travel_seq", "INTEGER NOT NULL DEFAULT 0"),
    ("party_groups", "node_id", "TEXT"),
    *[(t, c, "TEXT") for t in ("campaigns", "party_groups") for c in ("venue_id", "zone_id")],   # carousel/venues.py
    *[("characters", c, "INTEGER NOT NULL DEFAULT 0") for c in ("stars", "pending_stat_points", "starless_streak")],
    ("turns", "kind", "TEXT"),
    *[("campaigns", c, "TEXT") for c in ("opening_stage", "lure_kind", "lure_belief", "lure_resident", "lure_paid_off")],  # carousel/opening.py
    *[("characters", c, f"TEXT NOT NULL DEFAULT '{d}'") for c, d in (("physical_condition", "alive"), ("wallpaper_status", "active"),
                                                                    ("participation", "full"), ("persistent_fate", "ongoing"),
                                                                    ("rescue_eligibility", "not_applicable"))],   # carousel/fate.py
    ("characters", "rights_basis", "TEXT"),          # the ticket of an open posthumous window
    *[(t, "group_no", "INTEGER NOT NULL DEFAULT 1") for t in ("campaigns", "stories")],
    ("stories", "rescue_json", "TEXT"),              # GM-only: {"character_id", "ticket_ref", "poster_ids"}
    ("stories", "deathwatch_pace", "TEXT NOT NULL DEFAULT 'full'"),
    ("characters", "role_evidence_json", "TEXT"),   # {"quotes": [...], "contextual_reason": str} from silas_assigns             # NULL = the player's own words; arrival | arrival_omen (map_api.py travel)  # ordered quest ids that make up the immediate objective  # {cx, cy, k, ar}: square crop of the portrait image (null = default)  # comfy | codex (art/codex_image.py)  # staged tool calls and roll records while a turn is in flight
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
