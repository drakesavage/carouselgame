"""Rule search over the Carousel bibles: Markdown sections indexed with page-like references.

* BIBLES lists the documents in source precedence (design §3). Each is read from the Carousel source dir, never
  written. A missing file is reported in the index status; the others are still indexed.
* Every `#`/`##` heading starts a new "page" (1-based); the heading text is kept at the top of its page, and a
  passage's ref reads `System Bible v0.4.1 §12 Plot Armor`.
* Chunks are indexed with SQLite FTS5 (porter stemming, BM25 ranking) in data/rulebooks.db (never in Git). The file
  is only ever rebuilt whole and then read, so it uses a rollback journal, not WAL: no -wal file can outlive a swap.
* "Semantic" retrieval without a paid embedding API: the GM model supplies several phrasings of what it needs and
  results are merged with reciprocal-rank fusion. Equal scores follow source precedence.
* The importer rebuilds the index after each reference import (carousel/importer.py): into rulebooks.db.new, then
  swapped in, the same way as reference.db.

Precedence (CORE_MECHANICS 2): campaign rulings and house rules outrank these passages; the GM tool layer returns
them first and labels every result with its source type.
"""
from __future__ import annotations

import collections
import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from ..db import now_iso, rows

SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
  id INTEGER PRIMARY KEY,
  path TEXT NOT NULL,
  filename TEXT NOT NULL,
  title TEXT NOT NULL,
  sha256 TEXT NOT NULL,
  page_count INTEGER NOT NULL,      -- sections
  precedence INTEGER NOT NULL,      -- position in BIBLES
  source_type TEXT NOT NULL DEFAULT 'official',
  indexed_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS chunks (
  id INTEGER PRIMARY KEY,
  document_id INTEGER NOT NULL REFERENCES documents(id),
  page INTEGER NOT NULL,            -- 1-based section number
  heading TEXT NOT NULL,
  seq INTEGER NOT NULL,
  text TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS index_status (id INTEGER PRIMARY KEY CHECK (id = 1), status_json TEXT NOT NULL);
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(text, content='chunks', content_rowid='id', tokenize='porter unicode61');
CREATE INDEX IF NOT EXISTS ix_chunks_doc_page ON chunks(document_id, page);
"""

BIBLES = [
    ("system/Carousel_System_Bible_v0.4.1.md", "System Bible v0.4.1"),
    ("roles/Carousel_Role_Bible_v0_4_2_Implementation.md", "Role Bible v0.4.2"),
    ("tropes/Carousel_Trope_Bible_v0.1.1.md", "Trope Bible v0.1.1"),
    ("trope engine/Game_at_Carousel_Trope_Engine_v0.2.1.md", "Trope Engine v0.2.1"),
    ("storyline/Storyline_Screenplay_Bible_v0_1_1.md", "Storyline Screenplay Bible v0.1.1"),
    ("storyline/Carousel_Storyline_Production_Standard_v0_2.md", "Storyline Production Standard v0.2"),
]
CHUNK_CHARS = 1400
CHUNK_OVERLAP = 200
HEADING = re.compile(r"^#{1,2}\s+(.+?)\s*#*\s*$")


def open_db(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def _remove(path: Path) -> None:
    for p in (path, Path(f"{path}-wal"), Path(f"{path}-shm"), Path(f"{path}-journal")):
        p.unlink(missing_ok=True)


def sections(text: str, title: str) -> list[tuple[str, str]]:
    """(heading, text) per `#`/`##` heading; text before the first heading is a section headed by the title."""
    out: list[tuple[str, list[str]]] = [(title, [])]
    for line in text.splitlines():
        m = HEADING.match(line)
        if m:
            out.append((m.group(1).strip(), [m.group(1).strip()]))
        else:
            out[-1][1].append(line)
    result = [(h, "\n".join(body).strip()) for h, body in out]
    if not result[0][1]:
        result = result[1:]   # nothing before the first heading
    return result


def _chunk(text: str) -> list[str]:
    if len(text) <= CHUNK_CHARS:
        return [text] if text else []
    chunks, start = [], 0
    while start < len(text):
        end = min(len(text), start + CHUNK_CHARS)
        if end < len(text):
            cut = text.rfind("\n", start + CHUNK_CHARS // 2, end)
            end = cut if cut > 0 else end
        chunks.append(text[start:end].strip())
        if end >= len(text):
            break
        start = max(end - CHUNK_OVERLAP, start + 1)
    return [c for c in chunks if c]


def _ingest(conn: sqlite3.Connection, path: Path, rel: str, title: str, precedence: int) -> dict:
    raw = path.read_bytes()
    parts = sections(raw.decode("utf-8-sig"), title)
    doc_id = conn.execute("""INSERT INTO documents (path, filename, title, sha256, page_count, precedence, indexed_at)
                             VALUES (?,?,?,?,?,?,?)""",
                          (str(path), rel, title, hashlib.sha256(raw).hexdigest(), len(parts), precedence, now_iso())).lastrowid
    n = 0
    for page, (heading, text) in enumerate(parts, start=1):
        for seq, chunk in enumerate(_chunk(text)):
            cid = conn.execute("INSERT INTO chunks (document_id, page, heading, seq, text) VALUES (?,?,?,?,?)",
                               (doc_id, page, heading, seq, chunk)).lastrowid
            conn.execute("INSERT INTO chunks_fts (rowid, text) VALUES (?, ?)", (cid, chunk))
            n += 1
    return {"title": title, "file": rel, "sections": len(parts), "chunks": n}


def rebuild(db_path: Path, source_dir: Path) -> dict:
    """Build a fresh index of every bible at db_path (replacing whatever was there). Returns the index status."""
    _remove(db_path)
    conn = open_db(db_path)
    try:
        status: dict = {"state": "done", "source_dir": str(source_dir), "indexed": [], "missing": []}
        with conn:
            for precedence, (rel, title) in enumerate(BIBLES):
                p = Path(source_dir) / rel
                if p.is_file():
                    status["indexed"].append(_ingest(conn, p, rel, title, precedence))
                else:
                    status["missing"].append({"title": title, "file": rel})
            status["finished_at"] = now_iso()
            conn.execute("INSERT INTO index_status (id, status_json) VALUES (1, ?)", (json.dumps(status),))
        return status
    finally:
        conn.close()


def rebuild_index(settings) -> dict:
    """Rebuild data/rulebooks.db from the bibles beside the live one, then swap it in."""
    from ..carousel.importer import _swap
    new = settings.rulebook_db.with_suffix(".db.new")
    try:
        status = rebuild(new, settings.source_dir)
        for stale in ("-wal", "-shm"):   # left by an index written in WAL mode before this one
            Path(f"{settings.rulebook_db}{stale}").unlink(missing_ok=True)
        _swap(new, settings.rulebook_db)
        return status
    finally:
        new.unlink(missing_ok=True)


def index_status(conn: sqlite3.Connection) -> dict:
    """The last rebuild's status; "stale" for an index built before the bibles (or never finished)."""
    try:
        r = conn.execute("SELECT status_json FROM index_status WHERE id = 1").fetchone()
    except sqlite3.OperationalError:
        return {"state": "stale"}
    return json.loads(r[0]) if r else {"state": "stale"}


def list_documents(conn: sqlite3.Connection) -> list[dict]:
    return rows(conn, """SELECT d.id, d.filename, d.title, d.page_count, d.source_type, d.indexed_at,
                                (SELECT COUNT(*) FROM chunks c WHERE c.document_id = d.id) AS chunks FROM documents d ORDER BY d.precedence""")


STOPWORDS = set("the a an and or of to in on for with by is are be as at it this that from what how does do can when which who".split())


def _fts_terms(query: str) -> list[str]:
    words = re.findall(r"[a-z0-9]+", query.lower())
    return [w for w in words if w not in STOPWORDS and len(w) > 1][:16]


def _run_query(conn: sqlite3.Connection, query: str, limit: int, document_id: int | None) -> list[int]:
    terms = _fts_terms(query)
    if not terms:
        return []
    out: list[int] = []
    # Exact phrase first (strong signal), then all-terms, then any-term.
    for match in (f'"{" ".join(terms)}"', " AND ".join(f'"{t}"' for t in terms), " OR ".join(f'"{t}"' for t in terms)):
        sql = "SELECT c.id FROM chunks_fts JOIN chunks c ON c.id = chunks_fts.rowid WHERE chunks_fts MATCH ?"
        params: list = [match]
        if document_id:
            sql += " AND c.document_id = ?"
            params.append(document_id)
        sql += " ORDER BY bm25(chunks_fts) LIMIT ?"
        params.append(limit)
        for r in conn.execute(sql, params):
            if r[0] not in out:
                out.append(r[0])
        if len(out) >= limit:
            break
    return out[:limit]


@dataclass
class Passage:
    chunk_id: int
    document: str
    filename: str
    page: int
    text: str
    score: float
    source_type: str
    heading: str = ""

    def ref(self) -> str:
        return f"{self.document} §{self.page} {self.heading}".rstrip()


def search(conn: sqlite3.Connection, queries: list[str] | str, limit: int = 6, document_id: int | None = None) -> list[Passage]:
    if isinstance(queries, str):
        queries = [queries]
    fused: dict[int, float] = collections.defaultdict(float)
    for q in queries:
        for rank, chunk_id in enumerate(_run_query(conn, q, limit * 3, document_id)):
            fused[chunk_id] += 1.0 / (60 + rank)
    if not fused:
        return []
    found = {r["id"]: r for r in conn.execute(
        f"""SELECT c.id, c.page, c.heading, c.text, d.title, d.filename, d.source_type, d.precedence FROM chunks c
            JOIN documents d ON d.id = c.document_id WHERE c.id IN ({','.join('?' * len(fused))})""", list(fused))}
    best = sorted(fused.items(), key=lambda kv: (-kv[1], found[kv[0]]["precedence"], kv[0]))[:limit]
    return [Passage(r["id"], r["title"], r["filename"], r["page"], r["text"], round(score, 5), r["source_type"], r["heading"])
            for r, score in ((found[c], s) for c, s in best)]


def page_text(conn: sqlite3.Connection, document_id: int, page: int) -> str:
    """A section's text (its chunks joined; the overlap between chunks is not removed)."""
    return "\n".join(r["text"] for r in conn.execute("SELECT text FROM chunks WHERE document_id = ? AND page = ? ORDER BY seq", (document_id, page)))
