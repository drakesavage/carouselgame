from pathlib import Path

import pytest

from app.reference import rulebooks

SYSTEM = """# Carousel System Bible

How the town works.

## 12 Plot Armor

Plot Armor soaks one lethal moment per story. A character with no Plot Armor left can die.

## 13 Tickets

A ticket is a trope card. Its holder spends a use to invoke it.
"""
TROPES = """# Trope Bible

## The Trope Master

The Trope Master reads hidden tropes. It also soaks a lethal moment, like plot armor.
"""


def write(root: Path, rel: str, text: str) -> Path:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


@pytest.fixture
def source(tmp_path):
    root = tmp_path / "src"
    write(root, rulebooks.BIBLES[0][0], SYSTEM)
    write(root, rulebooks.BIBLES[2][0], TROPES)
    return root


def built(tmp_path, source):
    status = rulebooks.rebuild(tmp_path / "rb.db", source)
    return rulebooks.open_db(tmp_path / "rb.db"), status


def doc_id(conn, title):
    return conn.execute("SELECT id FROM documents WHERE title = ?", (title,)).fetchone()[0]


def test_each_heading_starts_a_page(tmp_path, source):
    conn, _ = built(tmp_path, source)
    system = doc_id(conn, "System Bible v0.4.1")
    assert conn.execute("SELECT page_count FROM documents WHERE id = ?", (system,)).fetchone()[0] == 3
    page = rulebooks.page_text(conn, system, 2)
    assert page.startswith("12 Plot Armor") and "soaks one lethal moment" in page and "trope card" not in page


def test_search_gives_section_refs_in_source_precedence(tmp_path, source):
    conn, _ = built(tmp_path, source)
    hits = rulebooks.search(conn, "plot armor lethal", limit=3)
    assert hits[0].ref() == "System Bible v0.4.1 §2 12 Plot Armor"
    assert hits[0].page == 2 and "Plot Armor soaks" in hits[0].text


def test_a_changed_bible_changes_the_index_and_a_missing_one_is_reported(tmp_path, source):
    first, status = built(tmp_path, source)
    first.close()
    assert len(status["indexed"]) == 2
    assert "Role Bible v0.4.2" in {m["title"] for m in status["missing"]}
    write(source, rulebooks.BIBLES[2][0], TROPES.replace("hidden tropes", "sealed envelopes"))
    conn, _ = built(tmp_path, source)
    assert rulebooks.search(conn, "sealed envelopes")
    assert not rulebooks.search(conn, "hidden")
    assert rulebooks.index_status(conn)["missing"]


def test_search_api_returns_campaign_rules_first(client, settings, source, campaign_id):
    rulebooks.rebuild(settings.rulebook_db, source)
    body = client.get(f"/api/rules/search?q=diner neutral ground&cid={campaign_id}").json()
    assert body["campaign"] and body["campaign"][0]["title"] == "Test house rule"
    body = client.get("/api/rules/search?q=plot armor").json()
    hit = body["official"][0]
    assert hit["ref"] == "System Bible v0.4.1 §2 12 Plot Armor"
    page = client.get(f"/api/rules/page?document_id={hit['document_id']}&page=2").json()
    assert "Plot Armor soaks" in page["text"]
    docs = client.get("/api/rules/documents").json()
    assert len(docs["documents"]) == 2 and docs["index_status"]["missing"]


def test_the_real_bibles_are_indexed(tmp_path):
    from tests.carousel.conftest import REAL_SOURCE
    if not (REAL_SOURCE / rulebooks.BIBLES[0][0]).exists():
        pytest.skip("Carousel source packages not present")
    status = rulebooks.rebuild(tmp_path / "rb.db", REAL_SOURCE)
    assert len(status["indexed"]) == len(rulebooks.BIBLES) and not status["missing"]
    hits = rulebooks.search(rulebooks.open_db(tmp_path / "rb.db"), "Trope Master", limit=3)
    assert {"Trope Bible v0.1.1", "Trope Engine v0.2.1"} & {h.document for h in hits}
