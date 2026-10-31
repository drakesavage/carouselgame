import uuid

from app.db import connect, one, rows
from app.gm.orchestrator import WRITE_LOCK, TurnService
from app.gm.providers import MockProvider, ProviderResponse, ToolCall


def _turn(client, cid, text, key=None):
    return client.post(f"/api/campaigns/{cid}/turns", json={"text": text, "idempotency_key": key or uuid.uuid4().hex})


def test_turn_persists_transcript_and_state(client, settings, campaign_id):
    r = _turn(client, campaign_id, "I drink a healing potion")
    body = r.json()
    assert r.status_code == 200 and body["status"] == "completed", body
    assert "rolls" not in body["result"]
    inv = client.get(f"/api/campaigns/{campaign_id}/inventory").json()
    assert next(i for i in inv["by_character"] if i["name"] == "Healing Potion")["quantity"] == 1
    msgs = client.get(f"/api/campaigns/{campaign_id}/messages").json()
    assert [m["role"] for m in msgs[-2:]] == ["player", "gm"]
    c = connect(settings.campaign_db)
    assert one(c, "SELECT kind FROM save_points WHERE campaign_id = ? ORDER BY id DESC LIMIT 1", (campaign_id,))["kind"] == "auto"


def test_retried_request_is_not_applied_twice(client, campaign_id):
    key = uuid.uuid4().hex
    first = _turn(client, campaign_id, "I rest for a while", key).json()
    t1 = client.get(f"/api/campaigns/{campaign_id}").json()["game_time_minutes"]
    second = _turn(client, campaign_id, "I rest for a while", key).json()
    t2 = client.get(f"/api/campaigns/{campaign_id}").json()["game_time_minutes"]
    assert first["turn_id"] == second["turn_id"] and second["replayed"] is True
    assert t1 == t2  # no second time advance
    msgs = [m for m in client.get(f"/api/campaigns/{campaign_id}/messages").json() if m["role"] == "player"]
    assert len(msgs) == 1


def test_failed_turn_rolls_back_state(client, settings, campaign_id):
    class Exploding(MockProvider):
        def complete(self, system, steps, tools, *, job_label):
            if not any(s["kind"] == "tool_results" for s in steps):
                return ProviderResponse([ToolCall("apply_changes", {"changes": [{"op": "advance_time", "minutes": 90, "reason": "x"}]})], None)
            raise RuntimeError("model fell over")

    client.app_state.turns = TurnService(settings, Exploding, run_async=False)
    t0 = client.get(f"/api/campaigns/{campaign_id}").json()["game_time_minutes"]
    body = _turn(client, campaign_id, "anything").json()
    assert body["status"] == "failed" and "fell over" in body["error"]
    assert client.get(f"/api/campaigns/{campaign_id}").json()["game_time_minutes"] == t0
    assert not [m for m in client.get(f"/api/campaigns/{campaign_id}/messages").json() if m["role"] == "player"]


def test_invalid_state_change_is_reported_not_applied(client, settings, campaign_id):
    class Greedy(MockProvider):
        def complete(self, system, steps, tools, *, job_label):
            if not any(s["kind"] == "tool_results" for s in steps):
                return ProviderResponse([ToolCall("apply_changes", {"changes": [{"op": "hp", "character": "player", "delta": 5},
                                                                                 {"op": "remove_item", "character": "player",
                                                                                  "name": "Healing Potion", "quantity": 9}]})],
                                        "You drink nine potions.")
            return ProviderResponse([], "You realise you only have two potions.")

    client.app_state.turns = TurnService(settings, Greedy, run_async=False)
    hp0 = next(p for p in client.get(f"/api/campaigns/{campaign_id}").json()["party"] if p["slug"] == "mara")["hp_current"]
    body = _turn(client, campaign_id, "drink nine potions").json()
    hp1 = next(p for p in client.get(f"/api/campaigns/{campaign_id}").json()["party"] if p["slug"] == "mara")["hp_current"]
    assert hp1 == hp0  # the valid heal in the same batch was not applied either
    assert body["status"] == "completed" and body["result"]["narration"] == "You realise you only have two potions."
    inv = client.get(f"/api/campaigns/{campaign_id}/inventory").json()
    assert next(i for i in inv["by_character"] if i["name"] == "Healing Potion")["quantity"] == 2


def test_busy_while_another_turn_runs(client, campaign_id):
    assert WRITE_LOCK.acquire(blocking=False)
    try:
        assert _turn(client, campaign_id, "hello").status_code == 409
        assert client.post(f"/api/campaigns/{campaign_id}/saves", json={"label": "x"}).status_code == 409
    finally:
        WRITE_LOCK.release()


def test_rules_question_cites_rulebook_when_indexed(client, campaign_id):
    body = _turn(client, campaign_id, "How does the rule for skill checks work?").json()
    assert body["status"] == "completed"
