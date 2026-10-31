from app.db import now_iso
from app.memory.context import build_context

FG_TEXT = "Once per story, survive one certain death."


def split_gm(text: str) -> tuple[str, str]:
    head, _, gm = text.partition("## GM-ONLY SECRETS")
    return head, gm.split("\n## ")[0]


def test_present_character_brief_has_full_ticket_text_and_pa(conn, campaign_id):
    text = build_context(conn, campaign_id, "look around")["text"]
    assert "Plot Armor 11/11/11" in text and "[EQUIPPED]" in text
    assert FG_TEXT in text                                                # reference ticket text, verbatim
    assert "uses left 1/1 (recharges per story)" in text


def test_hidden_npc_ticket_only_in_gm_section(conn, campaign_id):
    text = build_context(conn, campaign_id, "Adeline, a word?")["text"]
    head, gm = split_gm(text)
    assert "Adeline Winter secretly holds HIDDENTROPE-GOLF-7" in gm and "Doors open for it alone." in gm
    assert "HIDDENTROPE-GOLF-7" not in head


def test_ticket_rulings_travel_with_the_ticket(conn, campaign_id):
    conn.execute("INSERT INTO rulings (campaign_id, topic, ruling, certainty, label, review, ticket_ref, created_at) VALUES (?,?,?,?,?,?,?,?)",
                 (campaign_id, "certain death", "A fall from the water tower counts as certain death.", "canonical", "CAMPAIGN", "approved",
                  "hb.ticket.base.final-girl", now_iso()))
    text = build_context(conn, campaign_id, "look around")["text"]
    ticket_at = text.index(FG_TEXT)
    assert text.index("A fall from the water tower counts as certain death.", ticket_at) - ticket_at < 400


def test_ticket_text_survives_tight_budgets(conn, campaign_id):
    ctx = build_context(conn, campaign_id, "x", budgets={"present_each": 300})
    assert FG_TEXT in ctx["text"]


def test_briefing_without_reference_data(conn, campaign_id):
    conn.execute("DETACH DATABASE ref")
    text = build_context(conn, campaign_id, "look around")["text"]
    assert "ticket text unavailable: reference data not imported" in text


def test_prompt_explains_ticket_numbers():
    from app.gm.prompts import SYSTEM_PROMPT
    assert "There are no dice. When a ticket fires, call apply_changes op use_trope" in SYSTEM_PROMPT
