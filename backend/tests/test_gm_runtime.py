"""GM runtime reliability: staged turns (no write transaction while the GM thinks), dice preserved across replays,
concurrent-change detection, crash recovery, the live Codex event watcher, job-file retention and context budgets."""
import json
import os
import sys
import textwrap
import time
import uuid

import pytest

from app.db import connect, one, rows
from app.gm import providers
from app.gm.orchestrator import TurnService, recover_interrupted_turns
from app.gm.providers import CodexCliProvider, IsolationViolation, MockProvider, ProviderError, ProviderResponse, ToolCall
from app.memory.context import build_context


def _turn(client, cid, text, key=None):
    return client.post(f"/api/campaigns/{cid}/turns", json={"text": text, "idempotency_key": key or uuid.uuid4().hex}).json()


def _hp(client, cid, slug="juniper"):
    return next(p for p in client.get(f"/api/campaigns/{cid}").json()["party"] if p["slug"] == slug)["hp_current"]


def test_no_write_lock_is_held_while_the_gm_thinks(client, settings, demo_id):
    seen = {}

    class Probe(MockProvider):
        def complete(self, system, steps, tools, *, job_label):
            other = connect(settings.campaign_db)
            other.execute("PRAGMA busy_timeout = 0")
            t = time.monotonic()
            other.execute("BEGIN IMMEDIATE")  # would fail with "database is locked" under the old design
            other.execute("ROLLBACK")
            seen[job_label] = time.monotonic() - t
            other.close()
            if not any(s["kind"] == "tool_results" for s in steps):
                return ProviderResponse([ToolCall("roll_dice", {"expression": "1d6", "reason": "probe"})], None)
            return ProviderResponse([], "Done.")

    client.app_state.turns = TurnService(settings, Probe, run_async=False)
    body = _turn(client, demo_id, "anything")
    assert body["status"] == "completed", body
    assert len(seen) == 2 and all(v < 1 for v in seen.values())


def test_dice_are_drawn_once_and_reused_by_every_replay(client, settings, demo_id):
    class TwoStep(MockProvider):
        def complete(self, system, steps, tools, *, job_label):
            res = [r for s in steps if s["kind"] == "tool_results" for r in s["results"]]
            if not res:
                return ProviderResponse([ToolCall("roll_dice", {"expression": "2d4+2", "reason": "Healing Potion", "character": "player"})], None)
            if len(res) == 1:
                heal = res[0]["result"]["total"]
                return ProviderResponse([ToolCall("apply_changes", {"changes": [{"op": "hp", "character": "player", "delta": heal, "cause": "potion"}]}),
                                         ToolCall("roll_dice", {"expression": "1d20", "reason": "second"})], None)
            return ProviderResponse([], "You feel better.")

    client.app_state.turns = TurnService(settings, TwoStep, run_async=False)
    c = connect(settings.campaign_db)
    c.execute("UPDATE characters SET hp_current = 1 WHERE slug = 'juniper'")
    body = _turn(client, demo_id, "drink")
    assert body["status"] == "completed", body
    audit = rows(c, "SELECT reason, total FROM dice_rolls WHERE turn_id = ? ORDER BY id", (body["turn_id"],))
    assert [a["reason"] for a in audit] == ["Healing Potion", "second"]  # replays never add or re-roll dice
    assert _hp(client, demo_id) == 1 + audit[0]["total"]
    assert [r["total"] for r in body["result"]["rolls"]] == [a["total"] for a in audit]
    diag = one(c, "SELECT usage_json FROM turn_diagnostics WHERE turn_id = ?", (body["turn_id"],))["usage"]
    assert diag["staged_calls"] == 3 and diag["continuity"]["checks"]


def test_a_concurrent_state_change_aborts_the_turn_without_partial_commit(client, settings, demo_id):
    class Racer(MockProvider):
        def complete(self, system, steps, tools, *, job_label):
            if not any(s["kind"] == "tool_results" for s in steps):
                return ProviderResponse([ToolCall("apply_changes", {"changes": [{"op": "advance_time", "minutes": 30, "reason": "walk"}]})], None)
            other = connect(settings.campaign_db)  # e.g. a restore or an import from another process
            other.execute("UPDATE characters SET hp_current = hp_current - 1 WHERE slug = 'bix'")
            other.close()
            return ProviderResponse([], "You walk.")

    client.app_state.turns = TurnService(settings, Racer, run_async=False)
    t0 = client.get(f"/api/campaigns/{demo_id}").json()["game_time_minutes"]
    body = _turn(client, demo_id, "walk")
    assert body["status"] == "failed" and "changed while the GM was working" in body["error"]
    assert client.get(f"/api/campaigns/{demo_id}").json()["game_time_minutes"] == t0
    assert not [m for m in client.get(f"/api/campaigns/{demo_id}/messages").json() if m["role"] == "player"]


def test_failed_codex_call_after_staging_applies_nothing_and_audits_dice(client, settings, demo_id):
    class Dies(MockProvider):
        def complete(self, system, steps, tools, *, job_label):
            if not any(s["kind"] == "tool_results" for s in steps):
                return ProviderResponse([ToolCall("roll_dice", {"expression": "1d20", "reason": "doomed"}),
                                         ToolCall("apply_changes", {"changes": [{"op": "hp", "character": "player", "delta": -2, "cause": "x"}]})], None)
            raise ProviderError("Codex exited with code 1: network down")

    client.app_state.turns = TurnService(settings, Dies, run_async=False)
    hp0 = _hp(client, demo_id)
    body = _turn(client, demo_id, "anything", key="retry-me")
    assert body["status"] == "failed" and "network down" in body["error"]
    assert _hp(client, demo_id) == hp0
    c = connect(settings.campaign_db)
    assert [r["reason"] for r in rows(c, "SELECT reason FROM dice_rolls WHERE turn_id = ?", (body["turn_id"],))] == ["[failed turn] doomed"]
    assert one(c, "SELECT stage_json IS NULL AS cleared FROM turns WHERE id = ?", (body["turn_id"],))["cleared"]
    # the same request can be retried and then succeeds
    client.app_state.turns = TurnService(settings, MockProvider, run_async=False)
    again = _turn(client, demo_id, "anything", key="retry-me")
    assert again["status"] == "completed" and again["turn_id"] == body["turn_id"]


def test_restart_mid_turn_records_staged_dice_and_fails_the_turn(conn, demo_id):
    stage = {"calls": [{"name": "roll_dice", "arguments": {"expression": "1d20"}, "ok": True}],
             "rolls": [{"result": {"expression": "1d20", "dice": [{"sides": 20, "value": 17, "kept": True}], "modifier": 0, "total": 17,
                                   "target": None, "outcome": None, "reason": "sneak", "degree": None, "detail": {}},
                        "character_id": None, "hidden": False}]}
    tid = conn.execute("INSERT INTO turns (campaign_id, idempotency_key, status, player_input, stage_json, created_at) VALUES (?,?,?,?,?,?)",
                       (demo_id, "crash-1", "processing", "sneak past", json.dumps(stage), "2026-10-24T00:00:00Z")).lastrowid
    assert recover_interrupted_turns(conn) == 1
    t = one(conn, "SELECT status, error, stage_json IS NULL AS cleared FROM turns WHERE id = ?", (tid,))
    assert t["status"] == "failed" and "Interrupted" in t["error"] and t["cleared"]
    assert [(r["reason"], r["total"]) for r in rows(conn, "SELECT reason, total FROM dice_rolls WHERE turn_id = ?", (tid,))] == [("[interrupted turn] sneak", 17)]


# ------------------------------------------------------------------ Codex event watcher (fake codex binary)
FAKE = textwrap.dedent('''
    import json, sys, time, pathlib
    out = pathlib.Path(sys.argv[sys.argv.index("-o") + 1])
    sys.stdin.read()
    mode = "{mode}"
    print(json.dumps({{"type": "thread.started", "thread_id": "t"}}), flush=True)
    print(json.dumps({{"type": "item.completed", "item": {{"type": "error", "message": "Code Mode is unavailable"}}}}), flush=True)
    if mode == "attack":
        print(json.dumps({{"type": "item.started", "item": {{"type": "{item}", "query": "x"}}}}), flush=True)
        time.sleep(30)  # a killed job never gets here
        out.write_text(json.dumps({{"tool_calls": [], "narration": "should never be used"}}))
    else:
        out.write_text(json.dumps({{"tool_calls": [], "narration": "All quiet."}}))
        print(json.dumps({{"type": "item.completed", "item": {{"type": "agent_message", "text": "{{}}"}}}}), flush=True)
        print(json.dumps({{"type": "turn.completed", "usage": {{"input_tokens": 1234, "cached_input_tokens": 0, "output_tokens": 5}}}}), flush=True)
''')


@pytest.fixture
def fake_codex(settings, tmp_path, monkeypatch):
    def make(mode="ok", item="web_search"):
        script = tmp_path / f"fake_codex_{mode}_{item}.py"
        script.write_text(FAKE.format(mode=mode, item=item), encoding="utf-8")
        real = providers.gm_exec_args

        def args(binary, job, **kw):
            return [sys.executable, str(script), *real(binary, job, **kw)[1:]]
        monkeypatch.setattr(providers, "gm_exec_args", args)
        monkeypatch.setattr(providers, "resolve_codex_bin", lambda: "codex")
        return CodexCliProvider(settings)
    return make


@pytest.mark.parametrize("item", ["web_search", "command_execution", "mcp_tool_call", "file_change", "collab_tool_call"])
def test_watcher_kills_the_job_at_the_first_unauthorized_event(fake_codex, settings, item):
    prov = fake_codex("attack", item)
    t = time.monotonic()
    with pytest.raises(IsolationViolation) as e:
        prov.complete("contract", [{"kind": "context", "text": "SECRET-CONTEXT"}, {"kind": "player", "text": "hi"}], [], job_label="t1r1")
    assert time.monotonic() - t < 15  # killed, not waited out
    assert e.value.meta["violation"]["item_type"] == item
    assert not any(settings.codex_jobs_dir.iterdir())  # nothing retained


def test_clean_job_returns_and_leaves_no_prompt_on_disk(fake_codex, settings):
    prov = fake_codex("ok")
    resp = prov.complete("contract", [{"kind": "context", "text": "SECRET-CONTEXT"}, {"kind": "player", "text": "hi"}], [], job_label="t1r1")
    assert resp.narration == "All quiet." and resp.meta["usage"]["input_tokens"] == 1234
    assert resp.meta["notices"] and not resp.meta["prompt_captured"]
    assert not any(settings.codex_jobs_dir.iterdir())


def test_prompt_capture_is_opt_in_and_expires(fake_codex, settings):
    settings.prompt_capture_file.write_text(json.dumps({"until": time.time() + 60}))
    resp = fake_codex("ok").complete("contract", [{"kind": "context", "text": "SECRET-CONTEXT"}, {"kind": "player", "text": "hi"}], [], job_label="t1r1")
    kept = list(settings.codex_jobs_dir.iterdir())
    assert resp.meta["prompt_captured"] and len(kept) == 1 and "SECRET-CONTEXT" in (kept[0] / "prompt.txt").read_text(encoding="utf-8")
    settings.prompt_capture_file.write_text(json.dumps({"until": time.time() - 1}))
    providers.prune_jobs(settings)
    assert not any(settings.codex_jobs_dir.iterdir())


def test_gm_command_line_is_isolated_and_child_env_is_scrubbed(tmp_path, monkeypatch):
    monkeypatch.setenv("GM_TOKEN", "should-not-leak")
    args = providers.gm_exec_args("codex", tmp_path, developer="CONTRACT", schema=None, final=tmp_path / "f", model=None, effort=None)
    joined = " ".join(args)
    for flag in ("code_mode_host", "shell_tool", "multi_agent", "view_image", "apps", "plugins"):
        assert f"--disable {flag}" in joined
    assert 'web_search="disabled"' in args and "--strict-config" in args and "--ephemeral" in args
    assert args[args.index("--sandbox") + 1] == "read-only"
    assert any(a.startswith("developer_instructions=") and "CONTRACT" in a for a in args)
    assert "GM_TOKEN" not in providers.child_env()


# ------------------------------------------------------------------ context budgets
def test_context_budgets_report_what_was_left_out(conn, demo_id):
    full = build_context(conn, demo_id, "I look around the laundromat")
    tight = build_context(conn, demo_id, "I look around the laundromat", budgets={"present_each": 200, "scene": 100, "max_companions": 0})
    assert len(tight["text"]) < len(full["text"])
    assert any("sheet cut" in o for o in tight["omissions"]) and any(o.startswith("CURRENT SCENE") for o in tight["omissions"])
    assert set(full["categories"]) >= {"state", "scene_memory", "contract_rules", "narrative"}


# ------------------------------------------------------------------ Gemini provider (HTTP faked)
class _Resp:
    def __init__(self, payload):
        self.payload = payload

    def read(self):
        return json.dumps(self.payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _gemini(settings, monkeypatch, answers):
    import io
    import urllib.error
    from app.gm.providers import GeminiProvider
    monkeypatch.setenv("GEMINI_API_KEY", "test-key-123")
    sent = []

    def fake_urlopen(req, timeout=None):
        sent.append({"url": req.full_url, "headers": dict(req.header_items()), "body": json.loads(req.data)})
        a = answers.pop(0)
        if isinstance(a, int):
            raise urllib.error.HTTPError(req.full_url, a, "err", {}, io.BytesIO(b'{"error": {"message": "quota"}}'))
        return _Resp(a)
    monkeypatch.setattr(providers.urllib.request, "urlopen", fake_urlopen)
    prov = GeminiProvider(settings)
    prov.retry_delays = [0, 0, 0]
    return prov, sent


def _answer(obj, **usage):
    return {"candidates": [{"content": {"parts": [{"text": "thinking...", "thought": True}, {"text": json.dumps(obj)}]}, "finishReason": "STOP"}],
            "usageMetadata": {"promptTokenCount": 900, "cachedContentTokenCount": 300, "candidatesTokenCount": 40, "thoughtsTokenCount": 12, **usage}}


STEPS = [{"kind": "context", "text": "BRIEFING"}, {"kind": "player", "text": "I look around"}]


def test_gemini_sends_schema_only_and_parses_tool_calls(settings, monkeypatch):
    out = {"tool_calls": [{"name": "roll_dice", "arguments_json": "{\"expression\": \"1d20\"}"}], "narration": None}
    prov, sent = _gemini(settings, monkeypatch, [429, _answer(out)])
    resp = prov.complete("CONTRACT", STEPS, [{"name": "roll_dice", "description": "d", "parameters": {}}], job_label="t1r1")
    assert [c.name for c in resp.tool_calls] == ["roll_dice"] and resp.tool_calls[0].arguments == {"expression": "1d20"}
    assert resp.meta["usage"] == {"input_tokens": 900, "cached_input_tokens": 300, "output_tokens": 40, "reasoning_output_tokens": 12}
    assert resp.meta["retries"] == [429]
    body = sent[-1]["body"]
    assert "tools" not in body and "toolConfig" not in body  # nothing but JSON can come back
    assert body["generationConfig"]["responseMimeType"] == "application/json" and "responseJsonSchema" in body["generationConfig"]
    assert "CONTRACT" in body["systemInstruction"]["parts"][0]["text"] and "BRIEFING" in body["contents"][0]["parts"][0]["text"]
    assert "gemini-3.8-flash:generateContent" in sent[-1]["url"] and "key=" not in sent[-1]["url"]  # key only in the header
    assert sent[-1]["headers"].get("X-goog-api-key") == "test-key-123"


def test_gemini_tool_parts_are_rejected(settings, monkeypatch):
    bad = {"candidates": [{"content": {"parts": [{"functionCall": {"name": "shell", "args": {}}}]}, "finishReason": "STOP"}]}
    prov, _ = _gemini(settings, monkeypatch, [bad])
    with pytest.raises(IsolationViolation):
        prov.complete("CONTRACT", STEPS, [], job_label="t1r1")


def test_gemini_without_a_key_fails_cleanly(settings, monkeypatch):
    from app.gm.providers import GeminiProvider
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setattr(type(settings), "gemini_api_key", lambda self: None)
    with pytest.raises(ProviderError, match="No Gemini API key"):
        GeminiProvider(settings).complete("C", STEPS, [], job_label="t1r1")


def test_settings_accept_gemini(client):
    r = client.put("/api/settings", json={"gm_provider": "gemini", "gemini_model": "gemini-3.8-flash"})
    assert r.status_code == 200 and r.json()["gm_provider"] == "gemini" and "gemini" in r.json()["providers"]
    assert "gemini_api_key" not in json.dumps(r.json()).replace("gemini_key_present", "")


def test_gemini_daily_quota_is_not_retried(settings, monkeypatch):
    import io
    import urllib.error
    from app.gm.providers import GeminiProvider
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    calls = []
    body = {"error": {"code": 429, "message": "You exceeded your current quota", "details": [
        {"@type": "type.googleapis.com/google.rpc.QuotaFailure", "violations": [{"quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier"}]},
        {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "40s"}]}}

    def fake(req, timeout=None):
        calls.append(1)
        raise urllib.error.HTTPError(req.full_url, 429, "quota", {}, io.BytesIO(json.dumps(body).encode()))
    monkeypatch.setattr(providers.urllib.request, "urlopen", fake)
    with pytest.raises(ProviderError, match="daily free-tier quota"):
        GeminiProvider(settings).complete("C", STEPS, [], job_label="t1r1")
    assert len(calls) == 1


def test_token_pacer_waits_instead_of_exceeding_the_per_minute_cap():
    from app.gm.providers import TokenPacer
    TokenPacer._windows.pop("pace-test", None)
    clock = {"t": 1000.0}
    slept = []

    def sleep(s):
        slept.append(s)
        clock["t"] += s
    now = lambda: clock["t"]  # noqa: E731
    assert TokenPacer.wait("pace-test", 120_000, 230_000, sleep, now) == 0
    clock["t"] += 10
    assert TokenPacer.wait("pace-test", 100_000, 230_000, sleep, now) == 0   # 220k in the window: fits
    clock["t"] += 5
    waited = TokenPacer.wait("pace-test", 100_000, 230_000, sleep, now)     # would be 320k: wait for the first to age out
    assert waited > 0 and slept and clock["t"] - 1000.0 >= 60


def test_codex_usage_limit_is_reported_plainly():
    """Playtest 2: the ChatGPT plan's limit arrived in the --json event stream and surfaced as 'exited with code 1: no output'."""
    from app.gm.providers import _event_error
    lines = ['{"type":"turn.started"}', '{"type":"error","message":"You have hit your usage limit. Try again at 7:43 PM."}',
             '{"type":"turn.failed","error":{"message":"You have hit your usage limit. Try again at 7:43 PM."}}']
    assert "usage limit" in _event_error(lines) and "7:43 PM" in _event_error(lines)
    assert _event_error(['{"type":"turn.started"}', "not json"]) == ""
