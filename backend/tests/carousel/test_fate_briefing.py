from app.db import transaction
from app.gm.prompts import SYSTEM_PROMPT
from tests.carousel.test_fate_end import jonah_dies
from tests.carousel.test_fate_ops import kill
from tests.carousel.test_rewards import conclude, running  # noqa: F401  (fixture)
from tests.carousel.test_story_briefing import brief, section
from tests.conftest import apply

DEATHWATCH = ("The player's character can't act. The player is watching. Treat what they type as requests for a cut, a pace or a summary, "
              "never as orders to the survivors. Survivors act on their own knowledge, goals and fear. No convenient rescue, no forced tragedy; "
              "the story runs to a real ending. Pace: {pace} (narration length only).")


def test_fate_section_lists_changed_axes_and_warnings(conn, campaign_id, running):
    assert "## FATE" in brief(conn, campaign_id)["text"]               # a story runs: the section is there, even if all is well
    apply(conn, campaign_id, {"op": "warn_irreversible", "rule": "leave_town", "warning": "The road doesn't come back."})
    kill(conn, campaign_id)
    fate = section(brief(conn, campaign_id)["text"], "FATE")
    assert "Mara Vance: body dead · wallpaper dead · participation observer" in fate
    assert "Jonah Reyes:" not in fate
    assert "Warnings given: leave_town: The road doesn't come back." in fate


def test_no_fate_section_between_stories_without_posters(conn, campaign_id):
    assert "## FATE" not in brief(conn, campaign_id)["text"]


def test_board_line_after_a_wipe(conn, campaign_id, running):
    kill(conn, campaign_id)
    jonah_dies(conn, campaign_id)
    conclude(conn, campaign_id)
    assert "Board outside the Diner: 2 posters (Mara Vance, Jonah Reyes)" in section(brief(conn, campaign_id)["text"], "FATE")


def test_deathwatch_copy_appears_only_while_the_pc_cant_act(conn, campaign_id, running):
    assert "## DEATHWATCH" not in brief(conn, campaign_id)["text"]
    kill(conn, campaign_id)
    assert DEATHWATCH.format(pace="full") in section(brief(conn, campaign_id)["text"], "DEATHWATCH")
    with transaction(conn):
        conn.execute("UPDATE stories SET deathwatch_pace = 'condensed' WHERE id = ?", (running,))
    assert DEATHWATCH.format(pace="condensed") in brief(conn, campaign_id)["text"]
    conclude(conn, campaign_id)                                        # Jonah survived: Mara is restored
    assert "## DEATHWATCH" not in brief(conn, campaign_id)["text"]


def test_causes_and_eligibility_are_gm_only(conn, campaign_id, running):
    kill(conn, campaign_id)
    text = brief(conn, campaign_id)["text"]
    assert "CAUSE-SECRET-LIMA-7" in section(text, "GM-ONLY SECRETS") and "CAUSE-SECRET-LIMA-7" not in section(text, "FATE")
    jonah_dies(conn, campaign_id)
    conclude(conn, campaign_id)
    text = brief(conn, campaign_id)["text"]
    assert "Rescue eligibility: Mara Vance possible" in section(text, "GM-ONLY SECRETS") and "possible" not in section(text, "FATE")


def test_rescue_story_lines(conn, campaign_id, running):
    from tests.carousel.test_rescue import rescue_begin, take, to_diner
    from tests.carousel.test_fate_ops import give
    kill(conn, campaign_id)
    jonah_dies(conn, campaign_id)
    conclude(conn, campaign_id)
    with transaction(conn):
        nora = conn.execute("""INSERT INTO characters (campaign_id, slug, name, kind, roster_category, status, in_party, provenance)
                               VALUES (?, 'nora', 'Nora Quill', 'pc', 'player_character', 'active', 1, 'test')""", (campaign_id,)).lastrowid
        conn.execute("UPDATE campaigns SET active_character_id = ? WHERE id = ?", (nora, campaign_id))
    give(conn, campaign_id, nora, "trope:2")
    to_diner(conn, campaign_id)
    take(conn, campaign_id, conn.execute("SELECT id FROM missing_posters WHERE name = 'Mara Vance'").fetchone()[0])
    rescue_begin(conn, campaign_id, [conn.execute("SELECT id FROM missing_posters WHERE name = 'Mara Vance'").fetchone()[0]])
    gm = section(brief(conn, campaign_id)["text"], "GM-ONLY SECRETS")
    assert "This story is a Rescue for: Mara Vance" in gm and "Takeover: only the rescue ticket may revise this story" in gm


def test_restored_pc_firewall_line(conn, campaign_id, running):
    kill(conn, campaign_id)
    conclude(conn, campaign_id)
    assert "Mara Vance died at Party in Mini OM-001; they don't know what happened after that unless told" in \
        section(brief(conn, campaign_id)["text"], "CAMPAIGN")
    with transaction(conn):
        conn.execute("""UPDATE characters SET sheet_json = '{"identity": {"pronouns": "she/her"}}' WHERE slug = 'mara'""")
    assert "she doesn't know what happened" in brief(conn, campaign_id)["text"]


def test_prompt_rules_for_fate_ops():
    for s in ("set_fate only for what happened in the fiction", "warn_irreversible before any rule that can cause permanent loss",
              "permanent_loss only after a warning on an earlier turn"):
        assert s in SYSTEM_PROMPT
