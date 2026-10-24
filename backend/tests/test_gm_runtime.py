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
