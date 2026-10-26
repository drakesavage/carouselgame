"""Ability explanations: templates without a model, rulebook entries by heading, labelled homebrew, no GM material sent."""
import json

from app.db import now_iso, one, rows, transaction
from app.memory import abilities
from app.memory.abilities import Ability, RuleIndex, explain_missing, key_for, template
from app.reference.rulebooks import open_db


def test_plain_bonus_lines_are_explained_without_a_model():
    bow = template(Ability(key_for("class benefit", "+2 Bow"), "class benefit", "+2 Bow"))
    assert bow and "+2 Ranks in the Bow Skill" in bow[1]
    stats = template(Ability("x", "class benefit", "+3 Dexterity +2 Intelligence +2 Constitution"))
    assert stats and "+3 to Dexterity" in stats[1] and "+2 to Constitution" in stats[1]
    # not plain Stat/Skill bonuses: left for the rules or a written explanation
    for name in ("+2 to all Stats", "+1 DR Buff", "+2 Hot Stuff Aura; +2 DR", "Clear Lane"):
        assert template(Ability("x", "class benefit", name)) is None, name
    assert template(Ability("x", "skill", "+2 Bow")) is None  # only build benefits are templated


def test_rulebook_entries_are_found_by_their_heading(tmp_path):
    rc = open_db(tmp_path / "rb.db")
    with rc:
        rc.execute("INSERT INTO documents (id, path, title, filename, sha256, page_count, source_type, indexed_at) VALUES (1, 'core.pdf', ?, 'core.pdf', 'x', 300, 'official', ?)",
                   ("Dungeon Crawler Carl RPG — Core Rulebook", now_iso()))
        rc.execute("INSERT INTO chunks (document_id, page, seq, text) VALUES (1, 201, 0, ?)",
                   ("Use it in a scene about stealth generally.\nSTEALTH\nDexterity\nLimitations: You must be out of sight of all foes to begin "
                    "this Check. Use this Skill to move undetected. " + "More words. " * 20 + "\nSWIMMING\nStrength\nYou swim.",))
    idx = RuleIndex(rc)
    text, ref = idx.entry("Stealth")
    assert ref == "Dungeon Crawler Carl RPG — Core Rulebook, p. 201"
    assert text.startswith("STEALTH") and "out of sight" in text and "SWIMMING" not in text
    assert idx.entry("Cheese Rolling") is None


class FakeCodex:
    """Stands in for the Codex runner: records the prompt and answers every key."""

    def __init__(self, basis="rulebook"):
        self.prompts, self.basis = [], basis

    def _run(self, prompt, schema, label, developer):
        self.prompts.append(developer + "\n" + prompt)
        keys = [line[len("## key: "):] for line in prompt.splitlines() if line.startswith("## key: ")]
        items = [{"key": k, "summary": f"does {k}", "explanation": f"What {k} does.", "basis": self.basis, "citation": "Made-up Book, p. 1"} for k in keys]
        return json.dumps({"items": items}), {}


def test_explanations_are_cached_labelled_and_attached_without_gm_material(conn, demo_id, settings):
    with transaction(conn):
        conn.execute("INSERT INTO gm_secrets (campaign_id, subject, kind, content, created_at) VALUES (?,?,?,?,?)",
                     (demo_id, "plot", "plot", "GMSECRET-ABILITY-PROMPT-5521", now_iso()))
    fake = FakeCodex(basis="rulebook")
    counts = explain_missing(conn, demo_id, settings, rc=None, provider=fake, log=lambda m: None)
    assert fake.prompts and counts.get("rulebook") is None  # no passage was given, so no rulebook claim survives
    assert sum(counts.values()) == len(rows(conn, "SELECT id FROM ability_explanations WHERE campaign_id = ?", (demo_id,)))
    assert not any("GMSECRET-ABILITY-PROMPT-5521" in p for p in fake.prompts)
    codex = rows(conn, "SELECT basis, citation FROM ability_explanations WHERE campaign_id = ? AND model = 'codex'", (demo_id,))
    assert codex and all(r["basis"] in ("record", "homebrew") and r["citation"] in ("campaign record", "homebrew interpretation") for r in codex)

    # second run: everything is cached, nothing is sent
    again = FakeCodex()
    assert explain_missing(conn, demo_id, settings, rc=None, provider=again, log=lambda m: None) == {} and not again.prompts

    # the dossier carries each explanation
    from app.memory.dossier import build
    char = one(conn, "SELECT c.id FROM characters c JOIN skills s ON s.character_id = c.id WHERE c.campaign_id = ? LIMIT 1", (demo_id,))
    d = abilities.attach(conn, demo_id, build(conn, demo_id, char["id"]))
    assert d["skills"] and all(s["explain"] and s["explain"]["summary"] for s in d["skills"])
    assert d["explanations_missing"] == 0


def test_dossier_api_and_explain_endpoint(client, demo_id):
    chars = client.get(f"/api/campaigns/{demo_id}/characters").json()
    d = client.get(f"/api/campaigns/{demo_id}/characters/{chars[0]['id']}/dossier").json()
    assert "explanations_missing" in d and all("explain" in s for s in d["skills"])
    assert client.get(f"/api/campaigns/{demo_id}/abilities/explain").json() == {"state": "idle"}
