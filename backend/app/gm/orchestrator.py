"""Turn processing: player input → context → GM (tools ↔ server) → validated state → narration → autosave.

Guarantees
* Idempotency: a turn is keyed by (campaign_id, idempotency_key). Re-submitting a completed key returns the
  stored result without re-running anything; a key that is still processing reports progress.
* Atomicity without long locks: no write transaction is open while Codex runs. Tool calls are staged (run, then
  rolled back); the commit re-applies them with the same dice in one transaction together with the transcript,
  events and the autosave, after verifying the state fingerprint is unchanged. A failed turn commits nothing — except
  its dice rolls, which are recorded against the failed turn so the audit log cannot be gamed by retrying.
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
from ..memory.context import PROFILES, build_context
from ..memory.snapshots import create_save_point, state_fingerprint
from ..memory.summaries import maybe_summarize
from ..memory.views import floor_clock
from ..reference import rulebooks
from ..rules.dice import DiceRoller, record_roll
from .integrity import check as check_integrity, continuity
from .prompts import SYSTEM_PROMPT
from .providers import GMProvider, ProviderError, prune_jobs
from .router import CONVERSATION_MODE, ESCALATE_SPEC, SAFE_TOOLS, Escalate, check_call
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


class StateConflict(RuntimeError):
    """The committed campaign state is not the one the GM's turn was staged against."""


class ReplayDice:
    """Dice source for one turn. A die is drawn from the CSPRNG the first time it is needed and logged; every replay
    of the staged calls (each round, and the final commit) gets the same values back in order, so re-applying a turn
    can never re-roll it."""

    def __init__(self, base: Callable[[int], int]):
        self.base = base
        self.log: list[tuple[int, int]] = []
        self.pos = 0

    def rewind(self) -> None:
        self.pos = 0

    def __call__(self, sides: int) -> int:
        if self.pos < len(self.log):
            s, v = self.log[self.pos]
            if s != sides:
                raise StateConflict("A replayed roll asked for different dice than staging did; nothing was applied.")
        else:
            v = self.base(sides)
            self.log.append((sides, v))
        self.pos += 1
        return v


def recover_interrupted_turns(conn) -> int:
    """At startup: a turn still 'processing' was cut off by a stop/crash. Staging never leaves writes behind, so
    nothing of it was applied; dice it had already drawn are recorded against it from its persisted stage."""
    from ..rules.dice import Die, RollResult
    stuck = rows(conn, "SELECT id, campaign_id, stage_json FROM turns WHERE status = 'processing'")
    for t in stuck:
        stage = t.get("stage") or {}  # rows() parses *_json columns
        for r in stage.get("rolls", []):
            d = r["result"]
            res = RollResult(d["expression"], [Die(**x) for x in d["dice"]], d["modifier"], d["total"], d.get("target"), d.get("outcome"),
                             f"[interrupted turn] {d.get('reason') or ''}", None, bool(r["hidden"]), d.get("degree"), d.get("detail") or {})
            record_roll(conn, t["campaign_id"], res, turn_id=t["id"], character_id=r["character_id"], hidden=bool(r["hidden"]))
        conn.execute("UPDATE turns SET status = 'failed', error = 'Interrupted: the server stopped while this turn was being resolved. "
                     "Nothing was applied; retry it.', stage_json = NULL, completed_at = ? WHERE id = ?", (now_iso(), t["id"]))
    return len(stuck)


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

    def _process(self, turn_id: int, forced: tuple | None = None) -> None:
        """No write transaction is held while the GM thinks. Each round's tool calls run against the committed state
        inside a short transaction that is then rolled back (staging); the final commit re-applies every staged call
        with the same dice in one transaction, after checking the state fingerprint is still the one the GM saw."""
        prog = self.progress.setdefault(turn_id, Progress())
        conn = self._conn()
        rule_conn = self._rule_conn()
        turn = one(conn, "SELECT * FROM turns WHERE id = ?", (turn_id,))
        cid = turn["campaign_id"]
        root = provider = self.provider_factory()
        route = None
        if forced is not None:  # an escalated conversation turn, re-run by the mechanics GM
            provider, route = forced
        elif hasattr(provider, "route"):
            combat = one(conn, "SELECT 1 AS x FROM encounters WHERE campaign_id = ? AND status = 'active'", (cid,)) is not None
            provider, route = provider.route(turn["player_input"], combat)
        conversation = bool(getattr(provider, "conversation", False))
        tools = [t for t in TOOL_SPECS if t["name"] in SAFE_TOOLS] + [ESCALATE_SPEC] if conversation else TOOL_SPECS
        system = SYSTEM_PROMPT + "\n\n" + CONVERSATION_MODE if conversation else SYSTEM_PROMPT
        dice = ReplayDice(self.roller.source)
        roller = DiceRoller(source=dice)
        staged: list[dict] = []  # executed tool calls: {name, arguments, ok}
        runner = ToolRunner(conn, rule_conn, roller, cid, turn_id, settings=self.settings)
        steps: list[dict] = []
        usage: list[dict] = []
        t_before = None
        try:
            prog.set("loading", "campaign state")
            conn.execute("BEGIN")  # read snapshot: the briefing and the fingerprint describe the same state
            try:
                t_before = one(conn, "SELECT game_time_minutes FROM campaigns WHERE id = ?", (cid,))["game_time_minutes"]
                steps = [build_context(conn, cid, turn["player_input"], self.settings.recent_message_window, rule_conn,
                                       budgets={**PROFILES.get(getattr(provider, "context_profile", "standard"), {}), **self.settings.context_budgets()}),
                         {"kind": "player", "text": turn["player_input"]}]
                base = state_fingerprint(conn, cid)
            finally:
                conn.execute("ROLLBACK")
            narration = None
            for round_no in range(1, self.settings.max_tool_rounds + 1):
                prog.rounds = round_no
                prog.set("gm_thinking", f"{provider.name} round {round_no}")
                try:
                    resp = provider.complete(system, steps, tools, job_label=f"t{turn_id}r{round_no}")
                except ProviderError as perr:
                    if getattr(provider, "name", "") == "gemini" and hasattr(root, "route") and "quota" in str(perr).lower():
                        raise Escalate(f"{getattr(provider, 'model', 'gemini')} quota: {perr}", reroute=True) from None
                    raise
                usage.append({"round": round_no, **{k: resp.meta.get(k) for k in ("usage", "duration_s", "model", "effort", "prompt_chars", "notices", "prompt_captured") if k in resp.meta}})
                if conversation:
                    why = next((w for c in resp.tool_calls if (w := check_call(c.name, c.arguments))), None)
                    if why:
                        raise Escalate(why)
                if not resp.tool_calls:
                    narration = resp.narration
                    if narration:
                        break
                    steps.append({"kind": "instruction", "text": "You returned neither tool calls nor narration. Narrate the outcome now."})
                    continue
                steps.append({"kind": "gm_request", "tool_calls": [{"name": c.name, "arguments": c.arguments} for c in resp.tool_calls],
                              "draft": resp.narration})
                prog.set("resolving", ", ".join(c.name for c in resp.tool_calls))
                runner, results = self._stage(conn, rule_conn, roller, dice, cid, turn_id, base, staged, resp.tool_calls)
                self._save_stage(conn, turn_id, staged, runner)
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
            with transaction(conn):
                runner = self._replay(conn, rule_conn, roller, dice, cid, turn_id, base, staged, final=True)
                response = self._commit_turn(conn, cid, turn, narration, runner, provider.name)
                conn.execute("UPDATE turns SET status = 'completed', provider = ?, response_json = ?, stage_json = NULL, completed_at = ? WHERE id = ?",
                             (provider.name, json.dumps(response), now_iso(), turn_id))
                create_save_point(conn, cid, f"After turn {turn_id}", kind="auto", turn_id=turn_id, keep_auto=self.settings.autosave_keep)
                fx = runner.effects
                flags = check_integrity(conn, cid, turn["player_input"], narration, fx.changes, fx.rolls, fx.errors, fx.notices)
                t_after = one(conn, "SELECT game_time_minutes FROM campaigns WHERE id = ?", (cid,))["game_time_minutes"]
                self._save_diagnostics(conn, turn_id, cid, steps, usage, flags, {"game_time_before": t_before, "game_time_after": t_after,
                                                                                   "changes": fx.changes, "errors": fx.errors,
                                                                                   "staged_calls": len(staged), "dice_drawn": len(dice.log), "route": route,
                                                                                   "continuity": continuity(conn, cid, steps[0], flags, t_before, t_after, fx.changes)})
            prog.set("done")
            try:
                maybe_summarize(conn, cid, root.summarizer() if hasattr(root, "summarizer") else provider, self.settings.summarize_every_messages)
            except Exception:  # summaries are an optimisation; never fail a committed turn over them
                traceback.print_exc()
            prune_jobs(self.settings)
        except Escalate as exc:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            conn.execute("UPDATE turns SET stage_json = NULL WHERE id = ?", (turn_id,))
            prog.set("escalating", str(exc)[:200])
            info = {**(route or {}), "escalated_from": (route or {}).get("model"), "escalation_reason": str(exc)[:300],
                    "provider": "codex", "model": "codex", "gemini_usage": usage}
            nxt = root.codex() if hasattr(root, "codex") else self.provider_factory()
            if exc.reroute and hasattr(root, "route"):
                c2 = connect(self.settings.campaign_db)
                try:
                    combat = one(c2, "SELECT 1 AS x FROM encounters WHERE campaign_id = ? AND status = 'active'", (cid,)) is not None
                finally:
                    c2.close()
                nxt, again = root.route(turn["player_input"], combat)
                info = {**again, "rerouted_from": (route or {}).get("model"), "reroute_reason": str(exc)[:300]}
            conn.close()
            if rule_conn is not None:
                rule_conn.close()
            conn = rule_conn = None
            self._process(turn_id, forced=(nxt, info))
            return
        except Exception as exc:
            prog.set("failed", str(exc)[:300])
            if getattr(exc, "meta", None):  # e.g. an isolation violation: keep what the job attempted
                usage.append({"round": prog.rounds, "failed": True, **exc.meta})
            err = f"{type(exc).__name__}: {exc}"
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            with transaction(conn):
                conn.execute("UPDATE turns SET status = 'failed', error = ?, provider = ?, stage_json = NULL, completed_at = ? WHERE id = ?",
                             (err[:2000], provider.name, now_iso(), turn_id))
                for res, char_id, hidden in runner.effects.roll_records:  # dice audit survives the rollback
                    res.reason = f"[failed turn] {res.reason or ''}"
                    record_roll(conn, cid, res, turn_id=turn_id, character_id=char_id, hidden=hidden)
                self._save_diagnostics(conn, turn_id, cid, steps, usage, [{"level": "error", "message": err[:1000]}],
                                       {"game_time_before": t_before, "game_time_after": t_before, "changes": [], "errors": runner.effects.errors,
                                        "rolled_back": True, "staged_calls": len(staged), "dice_drawn": len(dice.log), "route": route})
            if not isinstance(exc, (ProviderError, StateConflict, ValueError, KeyError)):
                traceback.print_exc()
        finally:
            if conn is not None:
                conn.close()
            if rule_conn is not None:
                rule_conn.close()

    # ------------------------------------------------------------ staging
    def _replay(self, conn, rule_conn, roller, dice: "ReplayDice", cid: int, turn_id: int, base: str, staged: list[dict],
                final: bool = False) -> ToolRunner:
        """Inside an open transaction: verify nothing else changed the campaign, then re-apply the staged calls."""
        if state_fingerprint(conn, cid) != base:
            raise StateConflict("The campaign changed while the GM was working (another write, restore or import). "
                                "Nothing from this turn was applied; retry it.")
        dice.rewind()
        runner = ToolRunner(conn, rule_conn, roller, cid, turn_id, settings=self.settings)
        for call in staged:
            if runner.run(call["name"], call["arguments"])["ok"] != call["ok"]:
                raise StateConflict(f"Replaying {call['name']} gave a different outcome; nothing from this turn was applied.")
        if final and dice.pos != len(dice.log):
            raise StateConflict("Replay used a different number of dice than staging; nothing from this turn was applied.")
        return runner

    def _stage(self, conn, rule_conn, roller, dice, cid, turn_id, base, staged, calls) -> tuple[ToolRunner, list[dict]]:
        """Run this round's calls on top of the staged ones, keep the results, and discard the writes."""
        conn.execute("BEGIN IMMEDIATE")
        try:
            runner = self._replay(conn, rule_conn, roller, dice, cid, turn_id, base, staged)
            results = [{"name": c.name, **runner.run(c.name, c.arguments)} for c in calls]
        finally:
            conn.execute("ROLLBACK")
        staged.extend({"name": c.name, "arguments": c.arguments, "ok": r["ok"]} for c, r in zip(calls, results))
        return runner, results

    @staticmethod
    def _save_stage(conn, turn_id: int, staged: list[dict], runner: ToolRunner) -> None:
        """Persist the in-flight stage (one small autocommit write) so a crash can still audit the dice already drawn."""
        rolls = [{"result": res.to_dict(), "character_id": char_id, "hidden": hidden} for res, char_id, hidden in runner.effects.roll_records]
        conn.execute("UPDATE turns SET stage_json = ? WHERE id = ?", (json.dumps({"calls": staged, "rolls": rolls}, default=str), turn_id))

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
                      json.dumps({"rounds": usage, "context_sizes": ctx.get("sizes"), "context_categories": ctx.get("categories"),
                                  "omissions": ctx.get("omissions"), "present": ctx.get("present"), **extra}, default=str),
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
