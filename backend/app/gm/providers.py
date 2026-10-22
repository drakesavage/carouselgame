"""Model-provider abstraction for the AI Game Master.

A provider receives the system prompt, the turn's step list and the tool catalogue, and returns either tool
requests or final narration (or both). Providers never touch the database; the orchestrator executes tools.

* CodexCliProvider — runs `codex exec` non-interactively (the user's ChatGPT sign-in; no API billing).
  Each call is an isolated, ephemeral, read-only-sandboxed job whose final message must match a JSON schema.
* MockProvider     — deterministic scripted GM for development and tests; exercises the same tool path.

Extension point: implement `complete()` for another backend (a local model, another CLI) and register it in
`make_provider`.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from ..config import Settings, resolve_codex_bin


@dataclass
class ToolCall:
    name: str
    arguments: dict[str, Any]
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])


@dataclass
class ProviderResponse:
    tool_calls: list[ToolCall]
    narration: str | None
    meta: dict[str, Any] = field(default_factory=dict)


class ProviderError(RuntimeError):
    pass


class GMProvider(Protocol):
    name: str

    def complete(self, system: str, steps: list[dict], tools: list[dict], *, job_label: str) -> ProviderResponse: ...

    def summarize(self, text: str) -> str: ...


def render_steps(steps: list[dict]) -> str:
    out = []
    for s in steps:
        kind = s["kind"]
        if kind == "context":
            out.append("## CURRENT CAMPAIGN CONTEXT (authoritative state from the database)\n" + s["text"])
        elif kind == "player":
            out.append("## PLAYER INPUT (Cliff's player, this turn)\n" + s["text"])
        elif kind == "gm_request":
            calls = "\n".join(f"- {c['name']}({json.dumps(c['arguments'], ensure_ascii=False)})" for c in s["tool_calls"])
            out.append("## YOUR PREVIOUS TOOL REQUESTS THIS TURN\n" + calls + (f"\n(draft narration you wrote: {s['draft']})" if s.get("draft") else ""))
        elif kind == "tool_results":
            res = "\n".join(f"- {r['name']} → {json.dumps(r['result'], ensure_ascii=False)}" for r in s["results"])
            out.append("## TOOL RESULTS (computed by the server; authoritative)\n" + res)
        elif kind == "instruction":
            out.append("## SERVER NOTE\n" + s["text"])
    return "\n\n".join(out)


def tool_catalogue(tools: list[dict]) -> str:
    return "\n\n".join(f"### {t['name']}\n{t['description']}\nArguments: {json.dumps(t['parameters'], ensure_ascii=False)}" for t in tools)


def output_schema(tool_names: list[str]) -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["tool_calls", "narration"],
        "properties": {
            "tool_calls": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["name", "arguments_json"],
                    "properties": {
                        "name": {"type": "string", "enum": tool_names},
                        "arguments_json": {"type": "string", "description": "JSON object with the tool's arguments"},
                    },
                },
            },
            "narration": {"type": ["string", "null"]},
        },
    }


OUTPUT_PROTOCOL = """# RESPONSE PROTOCOL
Reply ONLY with the JSON object required by the output schema. Do not run shell commands or read files; everything
you need is in this prompt, and the server runs your tools.
- `tool_calls`: tools to run now (each `arguments_json` is a JSON object encoded as a string). Batch independent calls.
- `narration`: the player-facing narration in Markdown, or null while you still need tool results.
If you request rolls, checks, searches or lookups, set narration to null: you will be called again with results.
If you only need to record bookkeeping (apply_changes, count_cycle, record_ruling, gm_note, system_notice,
reveal_secret) you may return those tool calls together with final narration; if any of them fails validation
you will be called again to fix it. A response with no tool calls and non-null narration ends the turn."""


# ------------------------------------------------------------------ Codex CLI
CAPACITY = re.compile(r"at capacity|rate.?limit(?:ed)?|too many requests|\b429\b|overloaded|server (?:is )?busy|temporarily unavailable", re.I)
DISABLED_FEATURES = ["memories", "apps", "plugins", "browser_use", "browser_use_external", "computer_use", "in_app_browser", "image_generation"]


class CodexCliProvider:
    name = "codex"

    def __init__(self, settings: Settings, model: str | None = None, effort: str | None = None):
        self.settings = settings
        self.model = model if model is not None else settings.codex_model
        self.effort = effort or settings.codex_reasoning_effort
        self.retry_delays = [15, 45]

    def available(self) -> tuple[bool, str]:
        binary = resolve_codex_bin()
        if not binary:
            return False, "Codex CLI not found (install the Codex app or set CODEX_BIN)"
        return True, binary

    def _run(self, prompt: str, schema: dict, label: str) -> tuple[str, dict]:
        binary = resolve_codex_bin()
        if not binary:
            raise ProviderError("Codex CLI not found. Install/sign in to Codex or set CODEX_BIN; or switch the GM to mock mode.")
        job = self.settings.codex_jobs_dir / f"{time.strftime('%Y%m%d-%H%M%S')}-{label}-{uuid.uuid4().hex[:6]}"
        job.mkdir(parents=True, exist_ok=True)
        (job / "schema.json").write_text(json.dumps(schema), encoding="utf-8")
        (job / "prompt.txt").write_text(prompt, encoding="utf-8")
        final = job / "final.json"
        args = [binary, "exec", "--skip-git-repo-check", "--ephemeral"]
        for f in DISABLED_FEATURES:
            args += ["--disable", f]
        args += ["--sandbox", "read-only", "--cd", str(job), "--output-schema", str(job / "schema.json"), "-o", str(final)]
        if self.model:
            args += ["-m", self.model]
        if self.effort:
            args += ["-c", f'model_reasoning_effort="{self.effort}"']
        args.append("-")
        meta: dict[str, Any] = {"job_dir": str(job), "model": self.model or "(codex default)", "effort": self.effort}
        for attempt, delay in enumerate([0, *self.retry_delays]):
            if delay:
                time.sleep(delay)
            started = time.monotonic()
            try:
                proc = subprocess.run(args, input=prompt.encode("utf-8"), capture_output=True, cwd=job,
                                      timeout=self.settings.codex_timeout_s, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            except subprocess.TimeoutExpired:
                raise ProviderError(f"Codex timed out after {self.settings.codex_timeout_s}s") from None
            meta["duration_s"] = round(time.monotonic() - started, 1)
            stderr = proc.stderr.decode("utf-8", "replace")
            (job / "stderr.txt").write_text(stderr, encoding="utf-8")
            (job / "stdout.txt").write_text(proc.stdout.decode("utf-8", "replace"), encoding="utf-8")
            if proc.returncode == 0 and final.exists():
                return final.read_text(encoding="utf-8"), meta
            if CAPACITY.search(stderr) and attempt < len(self.retry_delays):
                continue
            tail = "\n".join(stderr.strip().splitlines()[-6:])
            raise ProviderError(f"Codex exited with code {proc.returncode}: {tail or 'no output'}")
        raise ProviderError("Codex is at capacity; try again shortly")

    def complete(self, system: str, steps: list[dict], tools: list[dict], *, job_label: str) -> ProviderResponse:
        prompt = "\n\n".join([system, "# TOOLS\n" + tool_catalogue(tools), render_steps(steps), OUTPUT_PROTOCOL])
        raw, meta = self._run(prompt, output_schema([t["name"] for t in tools]), job_label)
        try:
            data = json.loads(raw)
            calls = [ToolCall(c["name"], json.loads(c["arguments_json"] or "{}")) for c in data.get("tool_calls") or []]
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            raise ProviderError(f"Codex returned malformed output: {exc}") from None
        narration = data.get("narration")
        return ProviderResponse(calls, narration.strip() if isinstance(narration, str) and narration.strip() else None, meta)

    def summarize(self, text: str) -> str:
        schema = {"type": "object", "additionalProperties": False, "required": ["summary"], "properties": {"summary": {"type": "string"}}}
        prompt = ("Summarize this tabletop RPG transcript excerpt for the GM's long-term memory in under 250 words. "
                  "Keep names, decisions, promises, consequences, unresolved threads and numbers that matter. "
                  "Reply only with the JSON object.\n\n" + text)
        raw, _ = self._run(prompt, schema, "summary")
        return json.loads(raw)["summary"]


# ------------------------------------------------------------------ Mock
class MockProvider:
    """Scripted GM. Picks tools from keywords in the player's input so the full pipeline (checks, dice,
    validated state changes, notices, persistence) can be exercised without any model."""

    name = "mock"

    def complete(self, system: str, steps: list[dict], tools: list[dict], *, job_label: str) -> ProviderResponse:
        player = next(s["text"] for s in steps if s["kind"] == "player")
        text = player.lower()
        results = [r for s in steps if s["kind"] == "tool_results" for r in s["results"]]
        rounds = sum(1 for s in steps if s["kind"] == "gm_request")
        ctx = next(s for s in steps if s["kind"] == "context")
        pc = ctx.get("player_character", "player")
        if rounds == 0:
            if any(w in text for w in ("search", "look", "inspect", "examine", "investigate")):
                return ProviderResponse([ToolCall("skill_check", {"character": pc, "skill": "Investigation", "stat": "intelligence",
                                                                  "dc_kind": "unopposed", "reason": "Search the area"})], None)
            if any(w in text for w in ("potion", "heal", "drink")):
                return ProviderResponse([ToolCall("roll_dice", {"expression": "2d4+2", "reason": "Healing Potion", "character": pc})], None)
            if any(w in text for w in ("rest", "sleep", "wait", "nap")):
                return ProviderResponse([ToolCall("apply_changes", {"changes": [{"op": "advance_time", "minutes": 60, "reason": "Rest"}]}),
                                         ToolCall("count_cycle", {"decision": "Chose to rest", "resolution": "An hour passes in the saferoom"})],
                                        "*[MOCK GM]* You rest. The saferoom hums. Somewhere, a sponsor is disappointed.")
            if any(w in text for w in ("rule", "how does", "what happens")):
                return ProviderResponse([ToolCall("search_rules", {"queries": [player, "skill check difficulty"], "limit": 3})], None)
            if any(w in text for w in ("loot", "pick up", "grab", "take")):
                return ProviderResponse([ToolCall("apply_changes", {"changes": [{"op": "add_item", "character": pc, "name": "Suspicious Gray Rock",
                                                                                  "quantity": 1, "description": "It is a rock. The System insists it is 'collectible'."}]}),
                                         ToolCall("system_notice", {"text": "New Item! **Suspicious Gray Rock.** Congratulations on picking up the floor.", "style": "notice"})],
                                        "*[MOCK GM]* You pocket the rock. It feels judgmental.")
            return ProviderResponse([ToolCall("system_notice", {"text": "The System notes your decision and has *chosen* not to comment. Yet.", "style": "notice"})],
                                    f"*[MOCK GM]* You decide to: “{player.strip()[:200]}”. The dungeon considers this, then lets the moment pass. "
                                    "(Mock mode narrates generically; switch the GM to Codex in Settings for real play.)")
        last = results[-1] if results else None
        if last and last["name"] == "roll_dice" and rounds == 1:
            heal = last["result"].get("total", 0)
            return ProviderResponse([ToolCall("apply_changes", {"changes": [
                {"op": "remove_item", "character": pc, "name": "Healing Potion", "quantity": 1, "cause": "drunk"},
                {"op": "hp", "character": pc, "delta": heal, "cause": "Healing Potion"}]})],
                f"*[MOCK GM]* You knock back the potion. It tastes like cherry cough syrup and regret. You heal {heal}.")
        if last and last["name"] == "skill_check":
            r = last["result"]
            outcome = "you find a loose panel hiding a single, sad copper coin" if r.get("success") else "you find nothing but dust and a smell"
            return ProviderResponse([], f"*[MOCK GM]* {r.get('summary', '')}\n\nYou search carefully — {outcome}.")
        if last and last["name"] == "search_rules":
            refs = [p["ref"] for p in last["result"].get("official", [])][:3]
            return ProviderResponse([], "*[MOCK GM]* The relevant references are: " + ("; ".join(refs) if refs else "no indexed rulebook passages matched."))
        if any(r["ok"] is False for r in results):
            errs = "; ".join(r["result"].get("error", "") for r in results if not r["ok"])
            return ProviderResponse([], f"*[MOCK GM]* That didn't work out mechanically: {errs}")
        return ProviderResponse([], "*[MOCK GM]* The moment resolves.")

    def summarize(self, text: str) -> str:
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        return " / ".join(ln[:140] for ln in lines[-12:])


def make_provider(name: str, settings: Settings, model: str | None = None, effort: str | None = None) -> GMProvider:
    if name == "codex":
        return CodexCliProvider(settings, model=model, effort=effort)
    if name == "mock":
        return MockProvider()
    raise ValueError(f"unknown GM provider {name!r} (use codex or mock)")


def prune_jobs(settings: Settings, keep: int = 60) -> None:
    d = settings.codex_jobs_dir
    if not d.exists():
        return
    jobs = sorted((p for p in d.iterdir() if p.is_dir()), key=lambda p: p.name)
    for p in jobs[:-keep]:
        shutil.rmtree(p, ignore_errors=True)
