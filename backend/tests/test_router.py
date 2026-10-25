"""Auto GM routing: turn classification, daily Gemini quotas, conversation mode and escalation to Codex."""
import datetime as dt
import json
import uuid

import pytest

from app.db import connect, one, rows
from app.gm import router
from app.gm.orchestrator import TurnService
from app.gm.providers import MockProvider, ProviderResponse, ToolCall
from app.gm.router import GeminiQuota, check_call, classify, pacific_day, pick_model


@pytest.mark.parametrize("text, kind", [
    ("Status check before I decide anything: where is everyone right now?", "meta"),
    ("Previously on: give me a recap of the last session.", "meta"),
    ("What is my current HP?", "meta"),
    ("Cliff asks Margit what she thinks of the siphon design and whether she misses her forge.", "talk"),
    ('"You doing okay, Vada?" Cliff kneels and scratches behind her ears.', "talk"),
    ("Before I commit: what do the rules say about sneaking? Then Cliff slips behind the awning.", "action"),
    ("I send Nate and Priya back to The Molting Pigeon to rest.", "action"),
    ("Cliff offers her 5 Gold now as a holding fee.", "action"),
    ("I look around the market.", "action"),
])
def test_classify(text, kind):
    assert classify(text, False)[0] == kind


def test_any_turn_during_combat_goes_to_codex():
    assert classify('"Surrender!" Cliff shouts at the goblin.', True) == ("action", "combat is active")


def test_pacific_day_follows_us_dst():
    assert pacific_day(dt.datetime(2026, 7, 1, 6, 30, tzinfo=dt.timezone.utc)) == "2026-06-30"   # 23:30 PDT
    assert pacific_day(dt.datetime(2026, 7, 1, 7, 30, tzinfo=dt.timezone.utc)) == "2026-07-01"
    assert pacific_day(dt.datetime(2026, 1, 1, 7, 30, tzinfo=dt.timezone.utc)) == "2025-12-31"   # 23:30 PST


def test_quota_falls_through_models_then_to_codex(settings):
    q = GeminiQuota(settings)
    assert pick_model("talk", q, True) == "gemini-3.8-flash"
    q.record("gemini-3.8-flash", 18)   # 2 left: not enough to start a talk turn
    assert pick_model("talk", q, True) == "gemini-3.6-flash"
    q.mark_spent("gemini-3.6-flash")
    assert pick_model("talk", q, True) is None
    assert pick_model("meta", q, True) == "gemini-3.5-flash-lite"
    assert pick_model("meta", q, False) is None and pick_model("action", q, True) is None
    d = json.loads(q.path.read_text())
    d["day"] = "2000-01-01"  # a new Pacific day resets everything
    q.path.write_text(json.dumps(d))
    assert q.remaining("gemini-3.6-flash") == 20


def test_check_call_allows_only_low_risk_tools():
    assert check_call("gm_note", {"subject": "x", "content": "y"}) is None
    assert check_call("apply_changes", {"changes": [{"op": "remember"}, {"op": "npc_attitude"}]}) is None
    assert "hp" in check_call("apply_changes", {"changes": [{"op": "remember"}, {"op": "hp"}]})
    assert check_call("skill_check", {}) == "needs skill_check"
    assert check_call("escalate", {"reason": "needs a roll"}) == "needs a roll"


class FakeAuto:
    """Routes like AutoGM but with scripted providers."""

    def __init__(self, gemini, codex, kind="talk"):
        self.g, self.c, self.kind = gemini, codex, kind

    def route(self, text, combat):
        return self.g, {"kind": self.kind, "reason": "test", "provider": "gemini", "model": "gemini-3.8-flash"}

    def codex(self):
        return self.c

    def summarizer(self):
        return self.c


def test_conversation_turn_escalates_to_codex_without_applying_anything(client, settings, demo_id):
    seen = {}

    class Gem(MockProvider):
        name = "gemini"
        conversation = True

        def complete(self, system, steps, tools, *, job_label):
            seen["tools"] = {t["name"] for t in tools}
            seen["system"] = system
            if not any(s["kind"] == "tool_results" for s in steps):
                return ProviderResponse([ToolCall("gm_note", {"subject": "probe", "content": "should be discarded"})], None)
            return ProviderResponse([ToolCall("apply_changes", {"changes": [{"op": "hp", "character": "player", "delta": -3}]})], "Ouch.")

    class Cdx(MockProvider):
        def complete(self, system, steps, tools, *, job_label):
            seen["codex_tools"] = {t["name"] for t in tools}
            return ProviderResponse([], "Codex resolves it properly.")

    client.app_state.turns = TurnService(settings, lambda: FakeAuto(Gem(), Cdx()), run_async=False)
    hp0 = next(p for p in client.get(f"/api/campaigns/{demo_id}").json()["party"] if p["slug"] == "juniper")["hp_current"]
    body = client.post(f"/api/campaigns/{demo_id}/turns", json={"text": '"Hi," Juniper says.', "idempotency_key": uuid.uuid4().hex}).json()
    assert body["status"] == "completed" and body["result"]["narration"] == "Codex resolves it properly."
    assert "skill_check" not in seen["tools"] and "escalate" in seen["tools"] and "CONVERSATION MODE" in seen["system"]
    assert "skill_check" in seen["codex_tools"]
    c = connect(settings.campaign_db)
    assert not rows(c, "SELECT id FROM gm_secrets WHERE subject = 'probe'")  # the Gemini round's note was never committed
    assert next(p for p in client.get(f"/api/campaigns/{demo_id}").json()["party"] if p["slug"] == "juniper")["hp_current"] == hp0
    usage = one(c, "SELECT usage_json FROM turn_diagnostics WHERE turn_id = ?", (body["turn_id"],))["usage"]
    assert usage["route"]["escalated_from"] == "gemini-3.8-flash" and "hp" in usage["route"]["escalation_reason"]


def test_conversation_turn_that_stays_conversation_commits_on_gemini(client, settings, demo_id):
    class Gem(MockProvider):
        name = "gemini"
        conversation = True

        def complete(self, system, steps, tools, *, job_label):
            return ProviderResponse([ToolCall("apply_changes", {"changes": [{"op": "remember", "subject": "Bix (demo)",
                                                                             "content": "Juniper said hi", "visibility": "player"}]})],
                                    "Bix squeaks back.")

    client.app_state.turns = TurnService(settings, lambda: FakeAuto(Gem(), MockProvider()), run_async=False)
    body = client.post(f"/api/campaigns/{demo_id}/turns", json={"text": '"Hi, Bix," Juniper says.', "idempotency_key": uuid.uuid4().hex}).json()
    assert body["status"] == "completed" and body["result"]["narration"] == "Bix squeaks back."
    usage = one(connect(settings.campaign_db), "SELECT usage_json FROM turn_diagnostics WHERE turn_id = ?", (body["turn_id"],))["usage"]
    assert usage["route"]["provider"] == "gemini" and "escalated_from" not in usage["route"]


def test_settings_accept_auto_and_quota_endpoint(client):
    r = client.put("/api/settings", json={"gm_provider": "auto"})
    assert r.status_code == 200 and r.json()["gm_provider"] == "auto"
    q = client.get("/api/gemini/quota").json()
    assert q["models"]["gemini-3.5-flash-lite"]["limit"] == 500


def test_quota_exhausted_mid_turn_reroutes(client, settings, demo_id):
    from app.gm.providers import ProviderError

    class Spent(MockProvider):
        name = "gemini"
        conversation = True
        model = "gemini-3.8-flash"

        def complete(self, system, steps, tools, *, job_label):
            raise ProviderError("Gemini daily free-tier quota for gemini-3.8-flash is used up")

    class Next(MockProvider):
        name = "gemini"
        conversation = True

        def complete(self, system, steps, tools, *, job_label):
            return ProviderResponse([], "3.6 answers.")

    class Auto(FakeAuto):
        calls = 0

        def route(self, text, combat):
            Auto.calls += 1
            return (Spent(), {"kind": "talk", "model": "gemini-3.8-flash"}) if Auto.calls == 1 else (Next(), {"kind": "talk", "model": "gemini-3.6-flash"})

    client.app_state.turns = TurnService(settings, lambda: Auto(None, MockProvider()), run_async=False)
    body = client.post(f"/api/campaigns/{demo_id}/turns", json={"text": '"Hi," Juniper says.', "idempotency_key": uuid.uuid4().hex}).json()
    assert body["status"] == "completed" and body["result"]["narration"] == "3.6 answers."
    usage = one(connect(settings.campaign_db), "SELECT usage_json FROM turn_diagnostics WHERE turn_id = ?", (body["turn_id"],))["usage"]
    assert usage["route"]["model"] == "gemini-3.6-flash" and usage["route"]["rerouted_from"] == "gemini-3.8-flash"
