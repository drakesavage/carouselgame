"""Combat engine against the book: slot damage, Evade degrees, crits, Dying, action economy, secrecy."""
import json

import pytest

from app.db import one, transaction
from app.memory.context import build_context
from app.rules.combat import Combat, StartCombat, damage_mob, double_dice, parse_damage
from app.rules.dice import DiceRoller
from app.rules.engine import ChangeError
from conftest import scripted

GOBLINS = {"name": "Basement Goblin", "count": 2, "health_bar": [2, 2, 2], "evade": 14, "dr": 0,
           "attacks": [{"name": "Rusty Club", "to_hit": 13, "damage": "1d6+1", "damage_type": "Bludgeoning", "fail_effect": "Dazed"}],
           "notes": "SECRET-STATBLOCK-NOTE"}


def cb(conn, cid, values=None):
    return Combat(conn, cid, None, DiceRoller(source=scripted(values)) if values else DiceRoller())


def hp(conn, slug):
    return one(conn, "SELECT hp_current FROM characters WHERE slug = ?", (slug,))["hp_current"]


def start(conn, cid, **kw):
    with transaction(conn):
        return cb(conn, cid).start(StartCombat.model_validate({"name": "Laundry Room Brawl", "foes": [GOBLINS], "setup": "Narrow aisles.", **kw}))


def test_book_damage_examples():
    mob = {"name": "Test", "slots": [4] * 10, "max_hb": 40, "defeated": False}
    lost, _ = damage_mob(mob, 22)          # Core p. 94: 22 damage vs 4-point slots -> 5 slots, 2 lost
    assert lost == 5 and sum(mob["slots"]) == 20
    assert damage_mob(mob, 3)[0] == 0       # less than one slot: no damage
    spunky = {"name": "Goblin", "slots": [1, 1], "max_hb": 2, "defeated": False, "max_slots_per_hit": 1}
    assert damage_mob(spunky, 9, "Success")[0] == 1 and damage_mob(spunky, 9, "Amazing Success")[0] == 1 and spunky["defeated"]
    assert double_dice("2d8+3") == "4d8+3" and double_dice("d6+1") == "2d6+1" and double_dice("2d8+1d4") == "4d8+1d4"
    assert parse_damage("2d8 Piercing + 1d4 Aiming damage") == ("2d8+1d4", "Piercing")


def test_round_flow_evade_damage_attack_and_actions(conn, demo_id):
    out = start(conn, demo_id)
    assert out["foes"] == ["Basement Goblin 1", "Basement Goblin 2"]
    with transaction(conn):
        c = cb(conn, demo_id)
        with pytest.raises(ChangeError, match="Crawler Action Phase"):
            c.attack("juniper", "goblin 1", skill="Investigation", stat="int", damage="1d4")
        c.declare([{"mob": "Basement Goblin 1", "attack": "Rusty Club", "target": "Juniper"},
                   {"mob": "Basement Goblin 2", "attack": "Rusty Club", "target": "Bix"}])
        c.evade("Juniper")
    j0, b0 = hp(conn, "juniper"), hp(conn, "bix")
    with transaction(conn):
        r = cb(conn, demo_id, [18, 5]).resolve()   # Juniper Evade d20 18 (+3) = 21 vs 13; Bix hit for 1d6=5 +1
    assert hp(conn, "juniper") == j0
    # Bix: 6 damage - DR 1 = 5; slot value = CON Mod 1 -> 5 slots lost
    assert hp(conn, "bix") == b0 - 5
    assert any("avoided" in line for line in r["lines"])
    with transaction(conn):
        a = cb(conn, demo_id, [20, 3, 4]).attack("Juniper", "Goblin 1", skill="Investigation", stat="int", damage="1d8+2")
    # Natural 20 -> Critical Hit, damage dice doubled: 2d8 (3+4) + 2 = 9 vs slots [2,2,2] -> defeated
    assert a["hit"] and "Critical Hit" in a["lines"][0] and "DEFEATED" in a["lines"][1]
    assert a["actions_left"] == 0 and a["skill_marked"] is True
    with transaction(conn):
        with pytest.raises(ChangeError, match="no Actions left"):
            cb(conn, demo_id, [15]).attack("Juniper", "Goblin 2", skill="Investigation", stat="int", damage="1d4")
        e = cb(conn, demo_id).end_round()
    assert e["round"] == 2 and e["foes_remaining"] == ["Basement Goblin 2"]
    enc = (one(conn, "SELECT combatants_json FROM encounters WHERE status = 'active'")["combatants"])
    assert all(c["actions_left"] == 2 for c in enc["combatants"] if c["side"] == "party")


def test_mob_evade_default_and_actions_for_elites(conn, demo_id):
    with transaction(conn):
        out = cb(conn, demo_id).start(StartCombat.model_validate({"name": "Boss", "setup": "x", "foes": [
            {"name": "Lint Wraith", "kind": "boss", "slots": 4, "per_slot": 5, "dex_mod": 2, "attacks": [{"name": "Static", "to_hit": 15, "damage": "2d6"}]}]}))
    enc = (one(conn, "SELECT combatants_json FROM encounters WHERE status = 'active'")["combatants"])
    boss = next(c for c in enc["combatants"] if c["side"] == "foe")
    assert boss["evade"] == 10 + 2 + 3      # default Mob Evade = 10 + DEX Mod + Floor (BOOK 10.8)
    assert boss["actions"] == 2             # Bosses get one Action per participating crawler
    assert out["party"] == ["Juniper Vale (demo)", "Bix (demo)"]


def test_evade_major_fail_adds_floor_damage_and_injury_after_combat(conn, demo_id):
    start(conn, demo_id)
    j0 = hp(conn, "juniper")
    with transaction(conn):
        c = cb(conn, demo_id)
        c.declare([{"mob": "Basement Goblin 1", "attack": "Rusty Club", "target": "Juniper"}])
        c.evade("Juniper")
    with transaction(conn):
        r = cb(conn, demo_id, [2, 4]).resolve()   # Evade 2+3 = 5 vs 13 -> fail by 8... use a harder attack below
    # fail by 8 is a Standard Fail: 4+1 = 5 damage - DR 1 = 4 -> 2 slots of 2
    assert hp(conn, "juniper") == j0 - 4 and "failed to Evade (Fail)" in " ".join(r["lines"])
    with transaction(conn):
        c = cb(conn, demo_id)
        c.end_round()
        enc = one(conn, "SELECT id, combatants_json FROM encounters WHERE status = 'active'")
        st = enc["combatants"]
        next(m for m in st["combatants"] if m["name"] == "Basement Goblin 1")["attacks"][0]["to_hit"] = 20
        conn.execute("UPDATE encounters SET combatants_json = ? WHERE id = ?", (json.dumps(st), enc["id"]))
        c.declare([{"mob": "Basement Goblin 1", "attack": "Rusty Club", "target": "Juniper"}])
        c.evade("Juniper")
    j1 = hp(conn, "juniper")
    with transaction(conn):
        r = cb(conn, demo_id, [3, 4]).resolve()   # 3+3 = 6 vs 20 -> Major Fail: 5 + Floor 3 = 8 - DR 1 = 7 -> 3 slots
    assert hp(conn, "juniper") == j1 - 6 and "Major Fail" in " ".join(r["lines"])
    assert "Fail effect to apply: Dazed" in " ".join(r["lines"])
    with transaction(conn):
        end = cb(conn, demo_id).end("goblins fled")
    assert "Minor Injury" in end["lines"][0]
    assert one(conn, "SELECT COUNT(*) AS n FROM status_effects WHERE name = 'Minor Injury'")["n"] == 1
    assert one(conn, "SELECT status FROM encounters ORDER BY id DESC LIMIT 1")["status"] == "resolved"


def test_dying_countdown_death_and_healing(conn, demo_id):
    start(conn, demo_id)
    with transaction(conn):
        cb(conn, demo_id).damage("Bix", amount=100, cause="falling dryer")
    assert hp(conn, "bix") == 0
    dying = one(conn, "SELECT description FROM status_effects WHERE name = 'Dying'")
    assert dying and dying["description"].startswith("1 rounds")   # Bix CON Mod +1 (INTERPRETATION)
    with transaction(conn):
        cb(conn, demo_id).heal("Bix", 2, "Heal spell")
    assert hp(conn, "bix") == 2 and not one(conn, "SELECT id FROM status_effects WHERE name = 'Dying'")
    with transaction(conn):
        cb(conn, demo_id).damage("Bix", amount=100, cause="second dryer")
        out = cb(conn, demo_id).end_round()           # Clean Up ticks the countdown to 0
    assert "DIES" in " ".join(out["lines"])
    assert one(conn, "SELECT status FROM characters WHERE slug = 'bix'")["status"] == "dead"


def test_stat_blocks_stay_secret_from_the_player(client, conn, demo_id):
    start(conn, demo_id)
    body = client.get(f"/api/campaigns/{demo_id}").text
    enc = json.loads(body)["encounter"]
    assert enc["phase"] == "mob_declaration" and {c["name"] for c in enc["combatants"]} >= {"Basement Goblin 1", "Juniper Vale (demo)"}
    assert next(c for c in enc["combatants"] if c["side"] == "foe")["hb_percent"] == 100
    for secret in ("SECRET-STATBLOCK-NOTE", "Rusty Club", "to_hit", '"evade":14', "1d6+1", "Dazed"):
        assert secret not in body
    ctx = build_context(conn, demo_id, "I swing at the goblin")["text"]
    assert "Rusty Club 13 to hit" in ctx and "Evade 14" in ctx
