import os
from pathlib import Path

import pymupdf
import pytest

from app.reference import rulebooks

WATERMARK = "Licensed to Test Buyer #000"


def _make_pdf(path: Path, pages: list[str]):
    doc = pymupdf.open()
    for text in pages:
        page = doc.new_page()
        page.insert_textbox(pymupdf.Rect(40, 40, 560, 760), text + f"\n\n{WATERMARK}", fontsize=10)
    doc.save(path)


@pytest.fixture
def books(tmp_path):
    d = tmp_path / "books"
    d.mkdir()
    pages = [f"Filler page {i} about inventory and travel." for i in range(8)]
    pages[3] = "Grappling. To grapple a foe, make an opposed Unarmed Combat check. A grappled creature cannot Move."
    pages[6] = "Mana regenerates while resting. Spell Checks use the spell's Rank."
    _make_pdf(d / "test_rulebook_digital_hi-res_watermarked.pdf", pages)
    _make_pdf(d / "test_rulebook_digital_low-res_watermarked.pdf", pages)
    _make_pdf(d / "other_guide.pdf", ["Traps deal damage when triggered.", "Bosses have tiers."])
    os.utime(d / "test_rulebook_digital_hi-res_watermarked.pdf")
    return d


def test_ingest_search_page_references_and_dedupe(books, tmp_path):
    conn = rulebooks.open_db(tmp_path / "rb.db")
    result = rulebooks.ingest_directory(conn, books)
    assert len(result["indexed"]) == 2 and len(result["skipped_duplicates"]) == 1
    hits = rulebooks.search(conn, ["how do I grab and hold an enemy", "grapple opposed check"], limit=3)
    assert hits and hits[0].page == 4 and "Grappling" in hits[0].text
    assert hits[0].ref().endswith("p. 4")
    mana = rulebooks.search(conn, "mana regeneration resting", limit=2)
    assert mana[0].page == 7
    # repeated watermark/footer lines are stripped from the index
    book = conn.execute("SELECT id FROM documents WHERE filename LIKE 'test_rulebook%'").fetchone()[0]
    assert conn.execute("SELECT COUNT(*) FROM chunks WHERE document_id = ? AND text LIKE ?", (book, f"%{WATERMARK}%")).fetchone()[0] == 0
    # re-ingesting the same file is a no-op
    again = rulebooks.ingest_directory(conn, books)
    assert all(r["status"] == "already indexed" for r in again["indexed"])


def test_search_api_returns_campaign_rules_first(client, settings, books, demo_id):
    conn = rulebooks.open_db(settings.rulebook_db)
    rulebooks.ingest_directory(conn, books)
    conn.close()
    body = client.get(f"/api/rules/search?q=socks improvised ammunition&cid={demo_id}").json()
    assert body["campaign"] and body["campaign"][0]["title"] == "Demo house rule"
    body = client.get("/api/rules/search?q=grapple").json()
    assert body["official"][0]["page"] == 4
    page = client.get(f"/api/rules/page?document_id={body['official'][0]['document_id']}&page=4").json()
    assert "Grappling" in page["text"]
