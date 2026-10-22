import uuid

from app.db import connect, one, rows
from app.gm.orchestrator import WRITE_LOCK, TurnService
from app.gm.providers import MockProvider, ProviderResponse, ToolCall


def _turn(client, cid, text, key=None):
    return client.post(f"/api/campaigns/{cid}/turns", json={"text": text, "idempotency_key": key or uuid.uuid4().hex})


def test_turn_persists_transcript_and_state(client, settings, demo_id):
    before = client.get(f"/api/campaigns/{demo_id}").json()
    r = _turn(client, demo_id, "I drink a healing potion")
    body = r.json()
    assert r.status_code == 200 and body["status"] == "completed", body
    res = body["result"]
    assert res["rolls"] and res["rolls"][0]["reason"] == "Healing Potion"
    heal = res["rolls"][0]["total"]
    after = client.get(f"/api/campaigns/{demo_id}").json()
    pc_before = next(p for p in before["party"] if p["slug"] == "juniper")
    pc_after = next(p for p in after["party"] if p["slug"] == "juniper")
    assert pc_after["hp_current"] == min(pc_before["hp_max"], pc_before["hp_current"] + heal)
    inv = client.get(f"/api/campaigns/{demo_id}/inventory").json()
    assert next(i for i in inv["by_character"] if i["name"] == "Healing Potion")["quantity"] == 1
    msgs = client.get(f"/api/campaigns/{demo_id}/messages").json()
    assert [m["role"] for m in msgs[-2:]] == ["player", "gm"]
    assert msgs[-1]["meta"]["floor_clock"]["remaining_minutes"] is not None
    c = connect(settings.campaign_db)
    assert one(c, "SELECT kind FROM save_points WHERE campaign_id = ? ORDER BY id DESC LIMIT 1", (demo_id,))["kind"] == "auto"
    assert one(c, "SELECT COUNT(*) AS n FROM dice_rolls WHERE turn_id = ?", (body["turn_id"],))["n"] == 1


def test_retried_request_is_not_applied_twice(client, demo_id):
    key = uuid.uuid4().hex
    first = _turn(client, demo_id, "I rest for a while", key).json()
    t1 = client.get(f"/api/campaigns/{demo_id}").json()["game_time_minutes"]
    second = _turn(client, demo_id, "I rest for a while", key).json()
    t2 = client.get(f"/api/campaigns/{demo_id}").json()["game_time_minutes"]
    assert first["turn_id"] == second["turn_id"] and second["replayed"] is True
    assert t1 == t2  # no second time advance
    msgs = [m for m in client.get(f"/api/campaigns/{demo_id}/messages").json() if m["role"] == "player"]
    assert len(msgs) == 1


def test_failed_turn_rolls_back_state_but_keeps_dice_audit(client, settings, demo_id):
    class Exploding(MockProvider):
        def complete(self, system, steps, tools, *, job_label):
            if not any(s["kind"] == "tool_results" for s in steps):
                return ProviderResponse([ToolCall("roll_dice", {"expression": "1d20", "reason": "doomed"}),
                                         ToolCall("apply_changes", {"changes": [{"op": "advance_time", "minutes": 90, "reason": "x"}]})], None)
            raise RuntimeError("model fell over")

    client.app_state.turns = TurnService(settings, Exploding, run_async=False)
    t0 = client.get(f"/api/campaigns/{demo_id}").json()["game_time_minutes"]
    body = _turn(client, demo_id, "anything").json()
    assert body["status"] == "failed" and "fell over" in body["error"]
    assert client.get(f"/api/campaigns/{demo_id}").json()["game_time_minutes"] == t0
    c = connect(settings.campaign_db)
    audit = rows(c, "SELECT reason FROM dice_rolls WHERE turn_id = ?", (body["turn_id"],))
    assert len(audit) == 1 and audit[0]["reason"].startswith("[failed turn]")
    assert not [m for m in client.get(f"/api/campaigns/{demo_id}/messages").json() if m["role"] == "player"]


def test_invalid_state_change_is_reported_not_applied(client, settings, demo_id):
    class Greedy(MockProvider):
        def complete(self, system, steps, tools, *, job_label):
            if not any(s["kind"] == "tool_results" for s in steps):
                return ProviderResponse([ToolCall("apply_changes", {"changes": [{"op": "hp", "character": "player", "delta": 5},
                                                                                 {"op": "remove_item", "character": "player",
                                                                                  "name": "Healing Potion", "quantity": 9}]})],
                                        "You drink nine potions.")
            return ProviderResponse([], "You realise you only have two potions.")

    client.app_state.turns = TurnService(settings, Greedy, run_async=False)
    hp0 = next(p for p in client.get(f"/api/campaigns/{demo_id}").json()["party"] if p["slug"] == "juniper")["hp_current"]
    body = _turn(client, demo_id, "drink nine potions").json()
    hp1 = next(p for p in client.get(f"/api/campaigns/{demo_id}").json()["party"] if p["slug"] == "juniper")["hp_current"]
    assert hp1 == hp0  # the valid heal in the same batch was not applied either
    assert body["status"] == "completed" and body["result"]["narration"] == "You realise you only have two potions."
    inv = client.get(f"/api/campaigns/{demo_id}/inventory").json()
    assert next(i for i in inv["by_character"] if i["name"] == "Healing Potion")["quantity"] == 2


def test_busy_while_another_turn_runs(client, demo_id):
    assert WRITE_LOCK.acquire(blocking=False)
    try:
        assert _turn(client, demo_id, "hello").status_code == 409
        assert client.post(f"/api/campaigns/{demo_id}/saves", json={"label": "x"}).status_code == 409
    finally:
        WRITE_LOCK.release()


def test_rules_question_cites_rulebook_when_indexed(client, demo_id):
    body = _turn(client, demo_id, "How does the rule for skill checks work?").json()
    assert body["status"] == "completed"
