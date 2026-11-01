from app.db import transaction
from app.gm.prompts import SYSTEM_PROMPT
from app.memory.context import build_context
from tests.carousel.test_revisions import COMMANDO, revision
from tests.carousel.test_story_ops import begin, seen
from tests.conftest import apply

STORY_SENTENCE = ("Stories: never start one yourself. When the player's own action matches an Omen's Choice action, call apply_changes op "
                  "begin_story with that action and the cast. Move the Plot Cycle with advance_phase only when a trigger is met, never on a "
                  "timer, and never kill a character to meet a phase. The sealed truth never changes: a good deduction that fits it succeeds. "
                  "Story-altering tickets revise the future only through revise_screenplay. End every story with conclude_story and honest "
                  "Performance and Difficulty stars (0-2 each).")


def section(text, name):
    start = text.index(f"## {name}\n")
    nxt = text.find("\n## ", start + 4)
    return text[start: nxt if nxt >= 0 else len(text)]


def brief(conn, cid, **kw):
    return build_context(conn, cid, "We keep going.", **kw)


def start(conn, cid):
    seen(conn, cid, "OM-001")
    return begin(conn, cid)["story_id"]


def test_story_section_while_a_story_runs(conn, campaign_id):
    start(conn, campaign_id)
    apply(conn, campaign_id, {"op": "camera", "on": True, "focus": ["Mara"], "reason": "her move"})
    t = brief(conn, campaign_id)["text"]
    s = section(t, "STORY")
    assert "Mini OM-001" in s and "Party" in s and "pressure 1" in s and "camera on" in s and "version 0" in s
    assert "Mara Vance" in s and "lead" in s and "screen moments 1" in s and "Jonah Reyes" in s
    assert "MINI-OBLIGATION Party" in s and "MINI-OBLIGATION First Blood" in s
    assert "The scarecrow is burned." in s and "enter:farm" in s and "MINI-OM-001" in s
    assert "## OMENS HERE" not in t


def test_sealed_truth_only_in_gm_only(conn, campaign_id):
    sid = start(conn, campaign_id)
    t = brief(conn, campaign_id)["text"]
    gm = t.index("## GM-ONLY SECRETS")
    assert t.index("MINI-SEALED-TRUTH") > gm and t.count("MINI-SEALED-TRUTH") == 1
    g = section(t, "GM-ONLY SECRETS")
    sha = conn.execute("SELECT sealed_sha256 FROM story_packets WHERE story_id = ?", (sid,)).fetchone()[0]
    assert f"Sealed truth (story #{sid}, sha256 {sha[:12]})" in g
    for s in ("MINI-COUNTERPLAY", "MINI-ANTAGONIST", "MINI-CLUE"):
        assert s in g


def test_revision_notes_appear(conn, campaign_id):
    start(conn, campaign_id)
    apply(conn, campaign_id, {"op": "grant_ticket", "character": "Jonah", "ticket": COMMANDO, "source": "test"})
    apply(conn, campaign_id, {"op": "equip_ticket", "character": "Jonah", "ticket": COMMANDO})
    apply(conn, campaign_id, revision())
    s = section(brief(conn, campaign_id)["text"], "STORY")
    assert "REVISION-DETAIL" in s and "version 1" in s


def test_omens_here_without_a_story(conn, campaign_id):
    with transaction(conn):
        conn.execute("UPDATE campaigns SET node_id = 'CAR-086' WHERE id = ?", (campaign_id,))
    seen(conn, campaign_id, "OM-001", "OM-003")
    t = brief(conn, campaign_id)["text"]
    o = section(t, "OMENS HERE")
    assert "OM-001" in o and "A scarecrow facing the wrong way" in o and "enter:farm" in o and "visible" in o
    assert "OM-003" not in o and "## STORY" not in t


def test_sealed_truth_survives_a_tight_budget(conn, campaign_id):
    from app.db import now_iso
    with transaction(conn):
        for i in range(5):
            conn.execute("INSERT INTO gm_secrets (campaign_id, subject, kind, content, created_at) VALUES (?,?,?,?,?)",
                         (campaign_id, "mara", "plot", f"OTHER-SECRET-{i} " + "x" * 300, now_iso()))
    start(conn, campaign_id)
    r = brief(conn, campaign_id, budgets={"secrets": 200})
    assert "MINI-SEALED-TRUTH" in section(r["text"], "GM-ONLY SECRETS")
    assert any(o.startswith("GM-ONLY SECRETS") for o in r["omissions"])


def test_prompt_has_the_story_contract():
    assert STORY_SENTENCE in SYSTEM_PROMPT
