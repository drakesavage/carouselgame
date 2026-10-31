"""Generate a character: the player-visible layer lands on their sheet, the private layer only in the GM's companion sheet."""
import json

import pytest

from app.db import one, transaction
from app.memory.generate import generate
from app.rules.engine import apply_change

SECRET = "SECRETLY-EMBEZZLED-FROM-THE-PAINTBALL-CLUB"


class FakeCodex:
    def __init__(self):
        self.prompts = []

    def _run(self, prompt, schema, label, developer):
        self.prompts.append(prompt)
        return json.dumps({
            "gender": "woman", "pronouns": "she/her", "apparent_age": "early 30s",
            "appearance": {"summary": "Wiry woman in dusty coveralls, auburn braid.", "physique": "Wiry, 5'6\".", "outfit": "Paintball coveralls.",
                           "signature": "A chipped front tooth.", "expression": "Wary, jaw set."},
            "role": "Course referee", "personality_summary": "Blunt and practical.", "traits": ["blunt", "practical"],
            "voice": "Short sentences, dry.", "quirks": ["taps her whistle when thinking"],
            "known_background": {"occupation": "Paintball referee", "home": "unknown", "family": "unknown", "history": "unknown",
                                 "relationship_to_pc": "Met today under the fence."},
            "known_motivation": "Get out of here alive.",
            "private": {"backstory": "Grew up in Tallahassee. " + SECRET, "past_trauma": "Lost a brother.", "loose_end": "Unpaid debt.",
                        "regret": "Never called her mother back.", "wants": ["safety"], "fears": ["being left behind"], "secrets": [SECRET],
                        "values_and_lines": ["won't abandon the injured"], "contradiction": "Brave for others, terrified alone.",
                        "how_they_decide": "Weighs who could get hurt.", "under_pressure": "Goes quiet.", "view_of_pc": "Owes him.",
                        "attitude_to_pc": 1, "beats": [{"trigger": "someone is left behind", "surface_as": "she refuses to move"}],
                        "arc_seeds": ["learns to ask for help"]}}), {}


def _npc(conn, cid, **kw):
    with transaction(conn):
        return apply_change(conn, cid, None, {"op": "npc", "name": "Jamie Cole", "location": "Laundromat of Infinite Socks (demo)",
                                              "description": "Pinned by the fallen fence.", **kw})["character_id"]


def test_generate_fills_the_sheet_and_keeps_the_private_layer_for_the_gm(client, conn, campaign_id):
    from app.memory.context import build_context
    jid = _npc(conn, campaign_id, pronouns="she/her", gender="woman")
    fake = FakeCodex()
    out = generate(conn, campaign_id, jid, None, provider=fake)
    assert out["pronouns"] == "she/her" and "Pinned by the fallen fence." in fake.prompts[0]   # the record goes to the writer as canon
    c = one(conn, "SELECT * FROM characters WHERE id = ?", (jid,))
    assert c["bio"] == "Pinned by the fallen fence."                                           # established text is kept
    assert c["sheet"]["visual"]["reference"]["outfit"] == "Paintball coveralls." and c["sheet"]["narrative"]["background"] == {
        "occupation": "Paintball referee", "relationship_to_pc": "Met today under the fence."}  # "unknown" never shown as known
    assert one(conn, "SELECT attitude FROM npc_profiles WHERE character_id = ?", (jid,))["attitude"] == 1
    sheet = one(conn, "SELECT content, source FROM gm_secrets WHERE campaign_id = ? AND kind = 'companion_sheet' AND subject = ?", (campaign_id, c["slug"]))
    assert SECRET in sheet["content"] and sheet["source"] == "generated"
    ctx = build_context(conn, campaign_id, "I talk to Jamie Cole"); text = ctx if isinstance(ctx, str) else getattr(ctx, "text", str(ctx))
    assert SECRET in text and "GM-ONLY private companion sheet" in text                         # the GM plays her from it
    d = client.get(f"/api/campaigns/{campaign_id}/characters/{jid}/dossier").json()
    assert d["private_sheet"] == "generated" and d["story"]["identity"]["pronouns"] == "she/her"
    for url in (f"/api/campaigns/{campaign_id}/characters/{jid}/dossier", f"/api/campaigns/{campaign_id}/npcs", f"/api/campaigns/{campaign_id}/characters",
                f"/api/campaigns/{campaign_id}", f"/api/campaigns/{campaign_id}/messages", f"/api/campaigns/{campaign_id}/search?q=Jamie",
                f"/api/campaigns/{campaign_id}/journal", f"/api/campaigns/{campaign_id}/chronology"):
        assert SECRET not in client.get(url).text, url                                           # never reaches the player
    generate(conn, campaign_id, jid, None, provider=fake)                                             # regenerating rewrites the same sheet
    assert one(conn, "SELECT COUNT(*) n FROM gm_secrets WHERE kind = 'companion_sheet' AND subject = ?", (c["slug"],))["n"] == 1


def test_generate_refuses_the_player_character_and_hand_written_sheets(conn, campaign_id):
    pc = one(conn, "SELECT active_character_id a FROM campaigns WHERE id = ?", (campaign_id,))["a"]
    with pytest.raises(ValueError):
        generate(conn, campaign_id, pc, None, provider=FakeCodex())
    jid = _npc(conn, campaign_id)
    slug = one(conn, "SELECT slug FROM characters WHERE id = ?", (jid,))["slug"]
    conn.execute("INSERT INTO gm_secrets (campaign_id, subject, kind, content, source, created_at) VALUES (?,?,?,?,?,datetime('now'))",
                 (campaign_id, slug, "companion_sheet", "owner's own notes", "ledger"))
    with pytest.raises(ValueError):
        generate(conn, campaign_id, jid, None, provider=FakeCodex())
    assert one(conn, "SELECT content FROM gm_secrets WHERE subject = ? AND kind = 'companion_sheet'", (slug,))["content"] == "owner's own notes"
