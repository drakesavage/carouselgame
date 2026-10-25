"""Write-back to the Git ledger: always against throwaway git copies, never the real clone."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from app.db import now_iso, one, transaction
from app.importer.ledger import import_ledger
from app.ledger import sync
from app.rules.checks import CheckRequest, perform_check
from app.rules.dice import DiceRoller
from app.rules.engine import apply_change
from conftest import FIXTURES, scripted

REAL = Path(__file__).resolve().parents[2] / "campaign_sources" / "dccrepository"
AUTHOR = {"author_name": "Test Player", "author_email": "player@example.com"}


def git(root, *args):
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com", "-C", str(root), *args],
                          check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def mini_repo(tmp_path):
    root = tmp_path / "mini"
    shutil.copytree(FIXTURES / "mini_ledger", root)
    git(root, "init", "-q", "-b", "main")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "fixture")
    return root


@pytest.fixture
def real_repo(tmp_path):
    if not (REAL / ".git").exists():
        pytest.skip("real campaign ledger clone not present")
    root = tmp_path / "real"
    subprocess.run(["git", "-c", "core.longpaths=true", "clone", "-q", "--no-hardlinks", str(REAL), str(root)], check=True)
    return root


def _turn(conn, cid):
    return conn.execute("INSERT INTO turns (campaign_id, idempotency_key, status, player_input, created_at) VALUES (?,?,?,?,?)",
                        (cid, f"k{now_iso()}{id(conn)}", "completed", "test", now_iso())).lastrowid


def _apply(conn, cid, pv, **kw):
    return sync.apply(conn, cid, fingerprint=pv["fingerprint"], selected=kw.pop("selected", None), message="Test sync", **AUTHOR, **kw)


def test_untouched_campaign_proposes_nothing(conn, mini_repo):
    cid, _ = import_ledger(conn, mini_repo)
    pv = sync.preview(conn, cid, fetch=False)
    # only the stale campaign.yaml mirror of the live advancement counter is proposed
    assert [c["summary"] for c in pv["changes"]] == ["Campaign mirror of advancement cycles 9/12 → 4/12"] and pv["blockers"] == []


def test_play_is_written_back_minimally_and_idempotently(conn, mini_repo):
    cid, _ = import_ledger(conn, mini_repo)
    with transaction(conn):
        t = _turn(conn, cid)
        apply_change(conn, cid, t, {"op": "hp", "character": "hero", "delta": -7})
        apply_change(conn, cid, t, {"op": "remove_item", "character": "hero", "name": "Healing Potion"})
        apply_change(conn, cid, t, {"op": "add_item", "character": "hero", "name": "Brass Key", "description": "Opens something."})
        apply_change(conn, cid, t, {"op": "remove_item", "character": "shared", "name": "Rope"})
        apply_change(conn, cid, t, {"op": "set_quantity", "character": "shared", "name": "Crossbow Bolt", "quantity": 14, "reason": "counted"})
        apply_change(conn, cid, t, {"op": "advance_time", "minutes": 30, "reason": "searching"})
        apply_change(conn, cid, t, {"op": "quest", "title": "Find the fixture stairwell.", "status": "completed"})
        apply_change(conn, cid, t, {"op": "quest", "title": "Return the brass key", "kind": "quest"})
        apply_change(conn, cid, t, {"op": "fact", "subject": "vending machine", "fact": "The vending machine takes teeth.", "certainty": "canonical"})
        apply_change(conn, cid, t, {"op": "fact", "subject": "moss", "fact": "GM-ONLY-SYNC-FACT-991", "visibility": "gm"})
        apply_change(conn, cid, t, {"op": "add_status", "character": "hero", "name": "Sticky", "polarity": "debuff"})
        apply_change(conn, cid, t, {"op": "achievement", "character": "hero", "title": "Key Master"})
        apply_change(conn, cid, t, {"op": "clock", "key": "advancement_cycles", "delta": 1, "reason": "Searched → found the key"})
        conn.execute("INSERT INTO gm_secrets (campaign_id, subject, kind, content, source, created_at) VALUES (?,?,?,?,?,?)",
                     (cid, "moss", "plot", "GM-ONLY-SYNC-SECRET-552", f"turn {t}", now_iso()))
        conn.execute("INSERT INTO rulings (campaign_id, turn_id, topic, ruling, certainty, label, created_at) VALUES (?,?,?,?,?,?,?)",
                     (cid, t, "Vending", "Teeth count as coins.", "provisional", "ONE-SCENE", now_iso()))
        perform_check(conn, conn, DiceRoller(source=scripted([15])), cid, t,
                      CheckRequest(character="hero", skill="Investigation", stat="int", dc=12, reason="search"))
    pv = sync.preview(conn, cid, fetch=False, redact=True)
    assert pv["blockers"] == [] and all(c["status"] == "clean" for c in pv["changes"])
    shown = json.dumps(pv["changes"]) + json.dumps(pv["files"])
    assert "GM-ONLY-SYNC-SECRET-552" not in shown and "GM-ONLY-SYNC-FACT-991" not in shown  # redacted for the player

    out = _apply(conn, cid, pv)
    assert len(out["commit"]) == 40
    hero = yaml.safe_load((mini_repo / "state/characters/hero.yaml").read_text(encoding="utf-8"))
    assert hero["mechanics"]["health"]["current"] == 23
    assert next(i for i in hero["inventory"] if i["id"] == "healing-potion")["quantity"] == 1
    assert any(i["name"] == "Brass Key" and i["quantity"] == 1 for i in hero["inventory"])
    assert "investigation" in hero["mechanics"]["advancement_marks"] and "engineering" in hero["mechanics"]["advancement_marks"]
    assert hero["mechanics"]["debuffs"] == ["Sticky"] and hero["achievements"][0]["title"] == "Key Master"
    shared = yaml.safe_load((mini_repo / "state/shared_inventory.yaml").read_text(encoding="utf-8"))
    bolt = next(i for i in shared["items"] if i["id"] == "bolt")
    assert bolt["quantity"] == 14 and bolt["count_status"].startswith("established")
    assert next(i for i in shared["items"] if i["id"] == "rope")["quantity"] == 1
    camp = yaml.safe_load((mini_repo / "state/campaign.yaml").read_text(encoding="utf-8"))
    assert camp["floor_clock"]["remaining_minutes"] == 970 and camp["floor_clock"]["previous_remaining_minutes"] == 1000
    assert camp["dungeon_time_inventory"]["elapsed_floor_minutes"] == 230
    assert next(o for o in camp["open_objectives"] if o["id"] == "find-stairs")["status"] == "completed"
    assert any(o["summary"] == "Return the brass key" for o in camp["open_objectives"])
    assert "The vending machine takes teeth." in camp["world_state_notes"]
    assert "Teeth count as coins" in camp["campaign_rulings"]["crawler_console"][0]
    adv = yaml.safe_load((mini_repo / "state/advancement.yaml").read_text(encoding="utf-8"))
    assert adv["cycles_current"] == 5 and adv["reconciliations"][-1]["result"] == "4/12 -> 5/12"
    notes = yaml.safe_load((mini_repo / "gm_state/console_notes.yaml").read_text(encoding="utf-8"))
    assert {n["content"] for n in notes["notes"]} == {"GM-ONLY-SYNC-SECRET-552", "GM-ONLY-SYNC-FACT-991"}
    assert "GM-ONLY" not in (mini_repo / "state/campaign.yaml").read_text(encoding="utf-8")
    roll = json.loads((mini_repo / "rolls/public.jsonl").read_text(encoding="utf-8").splitlines()[-1])
    assert roll["actor"] == "hero" and roll["raw"] == [15] and roll["provenance"].startswith("Crawler Console roll #")
    # untouched files are byte-identical; edited files only gained the intended lines
    assert git(mini_repo, "diff", "HEAD~1", "--stat", "--", "state/characters/sidekick.yaml") == ""
    assert git(mini_repo, "status", "--porcelain") == ""
    # a second preview has nothing left to write
    assert sync.preview(conn, cid, fetch=False)["changes"] == []


def test_ledger_edits_made_elsewhere_become_conflicts(conn, mini_repo):
    cid, _ = import_ledger(conn, mini_repo)
    with transaction(conn):
        t = _turn(conn, cid)
        apply_change(conn, cid, t, {"op": "hp", "character": "hero", "delta": -5})
        apply_change(conn, cid, t, {"op": "mana", "character": "hero", "delta": -2})
    p = mini_repo / "state/characters/hero.yaml"
    p.write_text(p.read_text(encoding="utf-8").replace("health: {current: 30, max: 30}", "health: {current: 12, max: 30}"), encoding="utf-8")
    git(mini_repo, "commit", "-qam", "edited in another session")
    pv = sync.preview(conn, cid, fetch=False)
    by = {c["summary"].split(" ")[0] + c["summary"].split(" ")[1]: c for c in pv["changes"]}
    assert by["Healthcurrent"]["status"] == "conflict" and by["Healthcurrent"]["detail"]["ledger_now"] == 12
    assert by["Manacurrent"]["status"] == "clean"
    _apply(conn, cid, pv)  # writes only the clean change
    hero = yaml.safe_load(p.read_text(encoding="utf-8"))
    assert hero["mechanics"]["health"]["current"] == 12 and hero["mechanics"]["mana"]["current"] == 6


def test_safety_checks_block_writes(conn, mini_repo):
    cid, _ = import_ledger(conn, mini_repo)
    with transaction(conn):
        apply_change(conn, cid, _turn(conn, cid), {"op": "hp", "character": "hero", "delta": -1})
    pv = sync.preview(conn, cid, fetch=False)
    (mini_repo / "scratch.txt").write_text("x")
    with pytest.raises(sync.LedgerError, match="changed since this preview|uncommitted"):
        _apply(conn, cid, pv)
    (mini_repo / "scratch.txt").unlink()
    with pytest.raises(sync.LedgerError, match="author"):
        sync.apply(conn, cid, fingerprint=pv["fingerprint"], selected=None, message="m", author_name="", author_email="x")
    # a failing ledger validator restores every file and commits nothing
    tools = mini_repo / "tools"
    tools.mkdir()
    (tools / "validate_state.py").write_text("import sys\nprint('nope: fixture says no')\nsys.exit(1)\n")
    git(mini_repo, "add", "-A")
    git(mini_repo, "commit", "-qm", "add failing validator")
    head = git(mini_repo, "rev-parse", "HEAD")
    before = (mini_repo / "state/characters/hero.yaml").read_text(encoding="utf-8")
    pv = sync.preview(conn, cid, fetch=False)
    with pytest.raises(sync.LedgerError, match="validator rejected"):
        _apply(conn, cid, pv)
    assert (mini_repo / "state/characters/hero.yaml").read_text(encoding="utf-8") == before
    assert git(mini_repo, "rev-parse", "HEAD") == head and git(mini_repo, "status", "--porcelain") == ""


def test_campaign_without_ledger_is_rejected(conn, demo_id):
    with pytest.raises(sync.LedgerError):
        sync.preview(conn, demo_id, fetch=False)


def test_api_preview_and_apply(client, conn, mini_repo):
    cid, _ = import_ledger(conn, mini_repo)
    with transaction(conn):
        apply_change(conn, cid, _turn(conn, cid), {"op": "advance_time", "minutes": 10, "reason": "x"})
    pv = client.get(f"/api/campaigns/{cid}/ledger/preview?fetch=false").json()
    assert pv["changes"] and "_changes" not in pv and pv["suggested_message"]
    r = client.post(f"/api/campaigns/{cid}/ledger/apply", json={"fingerprint": "stale", "message": "Sync", **AUTHOR})
    assert r.status_code == 409
    r = client.post(f"/api/campaigns/{cid}/ledger/apply", json={"fingerprint": pv["fingerprint"], "message": "Sync", **AUTHOR})
    assert r.status_code == 200, r.text
    assert client.get(f"/api/campaigns/{cid}/ledger/history").json()[0]["new_commit"] == r.json()["commit"]


def test_real_ledger_round_trip_passes_its_own_validator(conn, real_repo):
    cid, rep = import_ledger(conn, real_repo)
    assert [c["summary"] for c in sync.preview(conn, cid, fetch=False)["changes"]] == ["Campaign mirror of advancement cycles 11/12 → 4/12"]
    roller = DiceRoller(source=scripted([17, 20]))
    with transaction(conn):
        t = _turn(conn, cid)
        apply_change(conn, cid, t, {"op": "hp", "character": "cliff", "delta": -9})
        apply_change(conn, cid, t, {"op": "remove_item", "character": "shared", "name": "Healing Potion"})
        apply_change(conn, cid, t, {"op": "advance_time", "minutes": 60, "reason": "Gilded Feathers shopping"})
        apply_change(conn, cid, t, {"op": "add_item", "character": "scout", "name": "Personal Stabber", "rarity": "Bronze"})
        perform_check(conn, conn, roller, cid, t, CheckRequest(character="cliff", skill="Negotiation", stat="cha", dc=16, reason="haggle"))
        apply_change(conn, cid, t, {"op": "clock", "key": "advancement_cycles", "delta": 1, "reason": "Bought the stabber → Scout armed"})
    pv = sync.preview(conn, cid, fetch=False)
    assert pv["blockers"] == [] and all(c["status"] == "clean" for c in pv["changes"])
    assert pv["checkpoint_curated"] is True  # the real checkpoint has hand-written sections the generator would drop
    before = {p: (real_repo / p).read_text(encoding="utf-8") for p in ("state/characters/vada.yaml", "state/campaign.yaml")}
    with pytest.raises(sync.LedgerError, match="hand-written sections"):  # curated checkpoint is never regenerated
        _apply(conn, cid, pv, regenerate_checkpoint=True)
    out = _apply(conn, cid, pv)
    stat = git(real_repo, "show", "--stat", "--format=", out["commit"])
    assert "state/characters/cliff.yaml" in stat and "checkpoints/latest.md" not in stat and "vada.yaml" not in stat
    assert (real_repo / "state/characters/vada.yaml").read_text(encoding="utf-8") == before["state/characters/vada.yaml"]
    camp = yaml.safe_load((real_repo / "state/campaign.yaml").read_text(encoding="utf-8"))
    assert camp["floor_clock"]["remaining_minutes"] == 12951 - 60
    assert camp["dungeon_time_inventory"]["time_until_next_daily_reset_minutes"] == 351 - 60
    # the diff of the big campaign file is small (no reformatting of untouched history)
    changed = [l for l in git(real_repo, "show", "--format=", out["commit"], "--", "state/campaign.yaml").splitlines()
               if l[:1] in "+-" and not l.startswith(("+++", "---"))]
    assert len(changed) < 30, changed
    assert "Crawler Console roll #" in (real_repo / "rolls/public.jsonl").read_text(encoding="utf-8")


def test_visual_bible_is_the_visual_source_and_nothing_visual_is_written_back(conn, real_repo):
    from app.importer.visual_reference import apply as apply_reference, bible_guide
    if not (real_repo / "state/characters/VISUAL_BIBLE.md").exists():
        pytest.skip("ledger has no visual bible")
    cid, rep = import_ledger(conn, real_repo)
    assert any("Visual bible loaded for 12" in n for n in rep["notes"])
    terri = next(r for r in conn.execute("SELECT sheet_json FROM characters WHERE campaign_id = ? AND slug = 'terri-campion'", (cid,)))
    ref = json.loads(terri[0])["visual"]["reference"]
    assert (ref["demographic"], ref["age"]) == ("Black American woman", "37") and ref["source"].startswith("state/characters/VISUAL_BIBLE.md")
    assert "negative_guard" in bible_guide(conn, cid)
    # even with a different reference imported on top, write-back proposes no visual edits
    with transaction(conn):
        apply_reference(conn, cid, "## Terri Campion\n**Black American woman | 37 | Paladin**\n\nSomething else.\n", "test")
    pv = sync.preview(conn, cid, fetch=False)
    assert not [c for c in pv["changes"] if "visual" in json.dumps(c.get("path") or c.get("summary") or "").lower()]
