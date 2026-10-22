"""Local rulebook reference: PDF ingestion and retrieval with page references.

* Text is extracted page by page with PyMuPDF and stored in data/rulebooks.db (never in Git, never served
  as static files). Lines repeated on many pages (watermarks, running headers/footers) are stripped.
* Chunks are indexed with SQLite FTS5 (porter stemming, BM25 ranking).
* "Semantic" retrieval without a paid embedding API: the GM model supplies several phrasings of what it
  needs (synonyms, rule names, the situation in plain words) and results are merged with reciprocal-rank
  fusion. Extension point: add an `embeddings` table + local model and fuse its ranking the same way.
* Hi-res/low-res copies of the same book are de-duplicated (smaller file wins; the text is identical).

Precedence (CORE_MECHANICS 2): campaign rulings and house rules outrank these official passages; the GM
tool layer returns them first and labels every result with its source type.
"""
from __future__ import annotations

import collections
import hashlib
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from ..db import connect, now_iso, rows

SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
  id INTEGER PRIMARY KEY,
  path TEXT NOT NULL,
  filename TEXT NOT NULL,
  title TEXT NOT NULL,
  sha256 TEXT NOT NULL UNIQUE,
  page_count INTEGER NOT NULL,
  source_type TEXT NOT NULL DEFAULT 'official',
  indexed_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS chunks (
  id INTEGER PRIMARY KEY,
  document_id INTEGER NOT NULL REFERENCES documents(id),
  page INTEGER NOT NULL,          -- 1-based PDF page
  seq INTEGER NOT NULL,
  text TEXT NOT NULL
);
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(text, content='chunks', content_rowid='id', tokenize='porter unicode61');
CREATE INDEX IF NOT EXISTS ix_chunks_doc_page ON chunks(document_id, page);
"""

KNOWN_TITLES = {
    "carl_rpg_core_rulebook": "Dungeon Crawler Carl RPG — Core Rulebook",
    "rules_for_survival": "Tutorial Floors — Rules for Survival",
    "crawlers": "Tutorial Floors — Crawlers & Customization",
    "gamemasters_toolkit": "Tutorial Floors — Game Master's Campaign Toolkit",
    "atlas-tutorial_floors": "Tutorial Floors — Atlas",
    "par_for_the_course": "Par for the Course (adventure)",
    "par_for_the_course_pre-gens": "Par for the Course — Pre-generated Crawlers",
}
CHUNK_CHARS = 1400
CHUNK_OVERLAP = 200


def open_db(path: Path) -> sqlite3.Connection:
    conn = connect(path)
    conn.executescript(SCHEMA)
    return conn


def base_name(filename: str) -> str:
    stem = Path(filename).stem.lower()
    return re.sub(r"_digital|_(hi|low)-res|_watermarked", "", stem)


def title_for(filename: str) -> str:
    base = base_name(filename)
    return KNOWN_TITLES.get(base) or base.replace("_", " ").replace("-", " ").title()


def pick_variants(pdfs: Iterable[Path]) -> tuple[list[Path], list[Path]]:
    """Group hi-res/low-res copies; keep the smallest file of each group."""
    groups: dict[str, list[Path]] = collections.defaultdict(list)
    for p in pdfs:
        groups[base_name(p.name)].append(p)
    keep, skipped = [], []
    for files in groups.values():
        files.sort(key=lambda p: p.stat().st_size)
        keep.append(files[0])
        skipped.extend(files[1:])
    return sorted(keep), sorted(skipped)


def _clean_pages(pages: list[str]) -> list[str]:
    """Drop lines that repeat on >30% of pages (watermarks, running headers/footers)."""
    counts: collections.Counter[str] = collections.Counter()
    for t in pages:
        counts.update({ln.strip() for ln in t.splitlines() if ln.strip()})
    limit = max(3, int(len(pages) * 0.3))
    noisy = {ln for ln, n in counts.items() if n > limit and len(ln) > 3}
    out = []
    for t in pages:
        lines = [ln for ln in t.splitlines() if ln.strip() and ln.strip() not in noisy]
        text = "\n".join(lines)
        text = re.sub(r"-\n(?=[a-z])", "", text)          # re-join hyphenated line breaks
        text = re.sub(r"[ \t]+", " ", text)
        out.append(text.strip())
    return out


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


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def ingest_pdf(conn: sqlite3.Connection, path: Path, source_type: str = "official") -> dict:
    import pymupdf  # local import: only needed for ingestion

    digest = sha256_file(path)
    existing = conn.execute("SELECT id, page_count FROM documents WHERE sha256 = ?", (digest,)).fetchone()
    if existing:
        return {"file": path.name, "status": "already indexed", "document_id": existing["id"], "pages": existing["page_count"]}
    doc = pymupdf.open(path)
    pages = _clean_pages([doc[i].get_text() for i in range(doc.page_count)])
    conn.execute("BEGIN IMMEDIATE")
    try:
        doc_id = conn.execute(
            "INSERT INTO documents (path, filename, title, sha256, page_count, source_type, indexed_at) VALUES (?,?,?,?,?,?,?)",
            (str(path), path.name, title_for(path.name), digest, doc.page_count, source_type, now_iso()),
        ).lastrowid
        n = 0
        for page_no, text in enumerate(pages, start=1):
            for seq, chunk in enumerate(_chunk(text)):
                cid = conn.execute("INSERT INTO chunks (document_id, page, seq, text) VALUES (?,?,?,?)", (doc_id, page_no, seq, chunk)).lastrowid
                conn.execute("INSERT INTO chunks_fts (rowid, text) VALUES (?, ?)", (cid, chunk))
                n += 1
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    empty = sum(1 for t in pages if len(t) < 40)
    return {"file": path.name, "status": "indexed", "document_id": doc_id, "pages": doc.page_count, "chunks": n,
            "near_empty_pages": empty}


def ingest_directory(conn: sqlite3.Connection, directory: Path) -> dict:
    pdfs = sorted(directory.glob("*.pdf"))
    keep, skipped = pick_variants(pdfs)
    results = [ingest_pdf(conn, p) for p in keep]
    return {"directory": str(directory), "indexed": results, "skipped_duplicates": [p.name for p in skipped]}


def list_documents(conn: sqlite3.Connection) -> list[dict]:
    return rows(conn, """SELECT d.id, d.filename, d.title, d.page_count, d.source_type, d.indexed_at,
                                (SELECT COUNT(*) FROM chunks c WHERE c.document_id = d.id) AS chunks FROM documents d ORDER BY d.title""")


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

    def ref(self) -> str:
        return f"{self.document}, p. {self.page}"


def search(conn: sqlite3.Connection, queries: list[str] | str, limit: int = 6, document_id: int | None = None) -> list[Passage]:
    if isinstance(queries, str):
        queries = [queries]
    fused: dict[int, float] = collections.defaultdict(float)
    for q in queries:
        for rank, chunk_id in enumerate(_run_query(conn, q, limit * 3, document_id)):
            fused[chunk_id] += 1.0 / (60 + rank)
    best = sorted(fused.items(), key=lambda kv: kv[1], reverse=True)[:limit]
    out = []
    for chunk_id, score in best:
        r = conn.execute("""SELECT c.id, c.page, c.text, d.title, d.filename, d.source_type FROM chunks c
                            JOIN documents d ON d.id = c.document_id WHERE c.id = ?""", (chunk_id,)).fetchone()
        out.append(Passage(r["id"], r["title"], r["filename"], r["page"], r["text"], round(score, 5), r["source_type"]))
    return out


def page_text(conn: sqlite3.Connection, document_id: int, page: int) -> str:
    return "\n".join(r["text"] for r in conn.execute("SELECT text FROM chunks WHERE document_id = ? AND page = ? ORDER BY seq", (document_id, page)))
