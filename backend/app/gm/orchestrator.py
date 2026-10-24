"""Turn processing: player input → context → GM (tools ↔ server) → validated state → narration → autosave.

Guarantees
* Idempotency: a turn is keyed by (campaign_id, idempotency_key). Re-submitting a completed key returns the
  stored result without re-running anything; a key that is still processing reports progress.
* Atomicity: all state changes of a turn are staged in one SQLite transaction and committed together with the
  transcript, events and the autosave. A failed turn rolls back completely — except its dice rolls, which are
  re-recorded against the failed turn so the audit log cannot be gamed by retrying.
* One turn at a time (WRITE_LOCK). Other write endpoints refuse with 409 while the GM is working.
"""
from __future__ import annotations

import json
import threading
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from ..config import Settings
from ..db import connect, now_iso, one, rows, transaction
from ..memory.context import build_context
from ..memory.snapshots import create_save_point
from ..memory.summaries import maybe_summarize
from ..memory.views import floor_clock
from ..reference import rulebooks
from ..rules.dice import DiceRoller, record_roll
from .integrity import check as check_integrity
from .prompts import SYSTEM_PROMPT
from .providers import GMProvider, ProviderError, prune_jobs
from .tools import BOOKKEEPING_TOOLS, TOOL_SPECS, ToolRunner

WRITE_LOCK = threading.Lock()


class BusyError(RuntimeError):
    pass


@dataclass
class Progress:
    stage: str = "queued"
    detail: str = ""
    rounds: int = 0
    started: float = field(default_factory=time.time)
    log: list[str] = field(default_factory=list)

    def set(self, stage: str, detail: str = "") -> None:
        self.stage, self.detail = stage, detail
        self.log.append(f"{round(time.time() - self.started, 1)}s {stage}{': ' + detail if detail else ''}")


def recover_interrupted_turns(conn) -> int:
    """At startup: a turn still 'processing' was cut off by a stop/crash; its transaction never committed."""
    return conn.execute("UPDATE turns SET status = 'failed', error = 'Interrupted: the server stopped while this turn was being resolved. Nothing was applied; retry it.', "
                        "completed_at = ? WHERE status = 'processing'", (now_iso(),)).rowcount


class TurnService:
    def __init__(self, settings: Settings, provider_factory: Callable[[], GMProvider], roller: DiceRoller | None = None,
                 run_async: bool = True):
        self.settings = settings
        self.provider_factory = provider_factory
        self.roller = roller or DiceRoller()
        self.run_async = run_async
        self.progress: dict[int, Progress] = {}

    def _conn(self):
        return connect(self.settings.campaign_db)

    def _rule_conn(self):
        p: Path = self.settings.rulebook_db
        return rulebooks.open_db(p) if p.exists() else None

    # ------------------------------------------------------------ public
    def submit(self, campaign_id: int, text: str, idempotency_key: str) -> dict:
        text = (text or "").strip()
        if not text:
            raise ValueError("empty input")
        if len(text) > 8000:
            raise ValueError("input too long (8000 characters max)")
        conn = self._conn()
        try:
            if not one(conn, "SELECT id FROM campaigns WHERE id = ?", (campaign_id,)):
                raise KeyError("campaign not found")
            existing = one(conn, "SELECT * FROM turns WHERE campaign_id = ? AND idempotency_key = ?", (campaign_id, idempotency_key))
            if existing and existing["status"] in ("completed", "processing"):
                return self.describe(existing, replayed=existing["status"] == "completed")
            if not WRITE_LOCK.acquire(blocking=False):
                raise BusyError("The GM is still resolving another turn")
            try:
                if existing:  # failed earlier: retry the same turn row
                    conn.execute("UPDATE turns SET status = 'processing', error = NULL, player_input = ? WHERE id = ?", (text, existing["id"]))
                    turn_id = existing["id"]
                else:
                    turn_id = conn.execute(
                        "INSERT INTO turns (campaign_id, idempotency_key, status, player_input, created_at) VALUES (?,?,?,?,?)",
                        (campaign_id, idempotency_key, "processing", text, now_iso())).lastrowid
                self.progress[turn_id] = Progress()
            except BaseException:
                WRITE_LOCK.release()
                raise
        finally:
            conn.close()
        if self.run_async:
            threading.Thread(target=self._process_and_release, args=(turn_id,), daemon=True, name=f"turn-{turn_id}").start()
        else:
            self._process_and_release(turn_id)
        return self.get(turn_id)

    def get(self, turn_id: int) -> dict:
        conn = self._conn()
        try:
            t = one(conn, "SELECT * FROM turns WHERE id = ?", (turn_id,))
        finally:
            conn.close()
        if not t:
            raise KeyError("turn not found")
        return self.describe(t)

    def describe(self, t: dict, replayed: bool = False) -> dict:
        p = self.progress.get(t["id"])
        return {"turn_id": t["id"], "campaign_id": t["campaign_id"], "status": t["status"], "replayed": replayed,
                "error": t["error"], "result": t.get("response"),
                "progress": {"stage": p.stage, "detail": p.detail, "elapsed_s": round(time.time() - p.started, 1)} if p and t["status"] == "processing" else None}

    # ------------------------------------------------------------ processing
    def _process_and_release(self, turn_id: int) -> None:
        try:
            self._process(turn_id)
        finally:
            WRITE_LOCK.release()

    def _process(self, turn_id: int) -> None:
        prog = self.progress.setdefault(turn_id, Progress())
        conn = self._conn()
        rule_conn = self._rule_conn()
        turn = one(conn, "SELECT * FROM turns WHERE id = ?", (turn_id,))
        cid = turn["campaign_id"]
        provider = self.provider_factory()
        runner = ToolRunner(conn, rule_conn, self.roller, cid, turn_id)
        steps: list[dict] = []
        usage: list[dict] = []
        t_before = one(conn, "SELECT game_time_minutes FROM campaigns WHERE id = ?", (cid,))["game_time_minutes"]
        try:
            with transaction(conn):
                prog.set("loading", "campaign state")
                steps = [build_context(conn, cid, turn["player_input"], self.settings.recent_message_window, rule_conn),
                                     {"kind": "player", "text": turn["player_input"]}]
                narration = None
                for round_no in range(1, self.settings.max_tool_rounds + 1):
                    prog.rounds = round_no
                    prog.set("gm_thinking", f"{provider.name} round {round_no}")
                    resp = provider.complete(SYSTEM_PROMPT, steps, TOOL_SPECS, job_label=f"t{turn_id}r{round_no}")
                    usage.append({"round": round_no, **{k: resp.meta.get(k) for k in ("usage", "duration_s", "model", "effort", "prompt_chars") if k in resp.meta}})
                    if not resp.tool_calls:
                        narration = resp.narration
                        if narration:
                            break
                        steps.append({"kind": "instruction", "text": "You returned neither tool calls nor narration. Narrate the outcome now."})
                        continue
                    steps.append({"kind": "gm_request", "tool_calls": [{"name": c.name, "arguments": c.arguments} for c in resp.tool_calls],
                                  "draft": resp.narration})
                    prog.set("resolving", ", ".join(c.name for c in resp.tool_calls))
                    results = [{"name": c.name, **runner.run(c.name, c.arguments)} for c in resp.tool_calls]
                    steps.append({"kind": "tool_results", "results": results})
                    all_bookkeeping = all(c.name in BOOKKEEPING_TOOLS for c in resp.tool_calls)
                    any_failed = any(not r["ok"] or any(not x.get("ok", True) for x in r["result"].get("results", [])) for r in results)
                    if resp.narration and all_bookkeeping and not any_failed:
                        narration = resp.narration
                        break
                    if round_no == self.settings.max_tool_rounds - 1:
                        steps.append({"kind": "instruction", "text": "Final round: narrate the outcome now using the results you have."})
                if not narration:
                    raise ProviderError("the GM did not produce narration within the tool-round limit")
                prog.set("saving", "transcript, events, autosave")
                response = self._commit_turn(conn, cid, turn, narration, runner, provider.name)
                conn.execute("UPDATE turns SET status = 'completed', provider = ?, response_json = ?, completed_at = ? WHERE id = ?",
                             (provider.name, json.dumps(response), now_iso(), turn_id))
                create_save_point(conn, cid, f"After turn {turn_id}", kind="auto", turn_id=turn_id, keep_auto=self.settings.autosave_keep)
                fx = runner.effects
                flags = check_integrity(conn, cid, turn["player_input"], narration, fx.changes, fx.rolls, fx.errors, fx.notices)
                t_after = one(conn, "SELECT game_time_minutes FROM campaigns WHERE id = ?", (cid,))["game_time_minutes"]
                self._save_diagnostics(conn, turn_id, cid, steps, usage, flags, {"game_time_before": t_before, "game_time_after": t_after,
                                                                                   "changes": fx.changes, "errors": fx.errors})
            prog.set("done")
            try:
                maybe_summarize(conn, cid, provider, self.settings.summarize_every_messages)
            except Exception:  # summaries are an optimisation; never fail a committed turn over them
                traceback.print_exc()
            prune_jobs(self.settings)
        except Exception as exc:
            prog.set("failed", str(exc)[:300])
            err = f"{type(exc).__name__}: {exc}"
            with transaction(conn):
                conn.execute("UPDATE turns SET status = 'failed', error = ?, provider = ?, completed_at = ? WHERE id = ?",
                             (err[:2000], provider.name, now_iso(), turn_id))
                for res, char_id, hidden in runner.effects.roll_records:  # dice audit survives the rollback
                    res.reason = f"[failed turn] {res.reason or ''}"
                    record_roll(conn, cid, res, turn_id=turn_id, character_id=char_id, hidden=hidden)
                self._save_diagnostics(conn, turn_id, cid, steps, usage, [{"level": "error", "message": err[:1000]}],
                                       {"game_time_before": t_before, "game_time_after": t_before, "changes": [], "errors": runner.effects.errors,
                                        "rolled_back": True})
            if not isinstance(exc, (ProviderError, ValueError, KeyError)):
                traceback.print_exc()
        finally:
            conn.close()
            if rule_conn is not None:
                rule_conn.close()

    @staticmethod
    def _save_diagnostics(conn, turn_id: int, cid: int, steps: list[dict], usage: list[dict], flags: list[dict], extra: dict) -> None:
        """GM-only record of what the GM saw and did this turn (served only by /api/gm)."""
        def trim(v, n=4000):
            t = json.dumps(v, ensure_ascii=False, default=str)
            return v if len(t) <= n else t[:n] + "…"
        ctx = steps[0] if steps and steps[0].get("kind") == "context" else {}
        trimmed = []
        for st in steps[1:]:
            st = dict(st)
            if st.get("kind") == "tool_results":
                st["results"] = [{**r, "result": trim(r.get("result"))} for r in st["results"]]
            trimmed.append(st)
        conn.execute("INSERT OR REPLACE INTO turn_diagnostics (turn_id, campaign_id, context_text, steps_json, usage_json, integrity_json, created_at) VALUES (?,?,?,?,?,?,?)",
                     (turn_id, cid, ctx.get("text", ""), json.dumps(trimmed, default=str),
                      json.dumps({"rounds": usage, "context_sizes": ctx.get("sizes"), "present": ctx.get("present"), **extra}, default=str),
                      json.dumps(flags), now_iso()))

    def _commit_turn(self, conn, cid: int, turn: dict, narration: str, runner: ToolRunner, provider_name: str) -> dict:
        ts = now_iso()
        fx = runner.effects
        pmsg = conn.execute("INSERT INTO messages (campaign_id, turn_id, role, content, created_at) VALUES (?,?,?,?,?)",
                            (cid, turn["id"], "player", turn["player_input"], ts)).lastrowid
        camp = one(conn, "SELECT game_time_minutes, floor_deadline_minutes FROM campaigns WHERE id = ?", (cid,))
        card_types = ("item_added", "achievement", "level_awarded", "skill_changed", "quest_added", "quest_completed", "quest_failed",
                      "status_added", "character_down", "character_death", "mob_defeated", "advancement_checkpoint", "encounter_start",
                      "encounter_end", "stats_allocated", "ai_favor_changed", "floor_collapse", "dungeon_day", "group_moved", "scene_started")
        cards = [{"type": e["type"], "summary": e["summary"]} for e in rows(conn, f"""SELECT type, summary FROM events WHERE turn_id = ? AND visibility = 'player'
                 AND type IN ({','.join('?' * len(card_types))}) ORDER BY id""", (turn["id"], *card_types))]
        meta = {"rolls": fx.rolls, "changes": fx.changes, "rules_cited": sorted(set(fx.rules_cited)), "provider": provider_name,
                "floor_clock": floor_clock(camp), "cards": cards}
        gmsg = conn.execute("INSERT INTO messages (campaign_id, turn_id, role, content, meta_json, created_at) VALUES (?,?,?,?,?,?)",
                            (cid, turn["id"], "gm", narration, json.dumps(meta), ts)).lastrowid
        notice_ids = []
        for n in fx.notices:
            notice_ids.append(conn.execute("INSERT INTO messages (campaign_id, turn_id, role, content, meta_json, created_at) VALUES (?,?,?,?,?,?)",
                                           (cid, turn["id"], "system", n["text"], json.dumps({"style": n["style"]}), ts)).lastrowid)
        conn.execute("UPDATE campaigns SET updated_at = ? WHERE id = ?", (ts, cid))
        return {"player_message_id": pmsg, "gm_message_id": gmsg, "notice_message_ids": notice_ids, "narration": narration,
                "rolls": fx.rolls, "changes": fx.changes, "notices": fx.notices, "rules_cited": meta["rules_cited"],
                "state_changed": bool(fx.changes)}
