"""Model-provider abstraction for the AI Game Master.

A provider receives the system prompt, the turn's step list and the tool catalogue, and returns either tool
requests or final narration (or both). Providers never touch the database; the orchestrator executes tools.

* CodexCliProvider — runs `codex exec` non-interactively (the user's ChatGPT sign-in; no API billing).
  Each call is an isolated, ephemeral, read-only-sandboxed job with no tools, the GM contract as developer
  instructions, and a final message that must match a JSON schema. Its event stream is watched live.
* GeminiProvider   — Google's Gemini REST API with a JSON response schema and no model-side tools (needs an API
  key; free-tier inputs may be used by Google to improve its products).
* MockProvider     — deterministic scripted GM for development and tests; exercises the same tool path.

Extension point: implement `complete()` for another backend (a local model, another CLI) and register it in
`make_provider`.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.request
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
you will be called again to fix it. A response with no tool calls and non-null narration ends the turn.
Never end a turn by promising to resolve something "next": if the action needs a check or roll, request it now (with
narration null) and narrate the outcome when the result comes back."""


# ------------------------------------------------------------------ Codex CLI
CAPACITY = re.compile(r"at capacity|rate.?limit(?:ed)?|too many requests|\b429\b|overloaded|server (?:is )?busy|temporarily unavailable", re.I)

# Runtime GM isolation (verified against codex-cli 0.163 with `codex debug prompt-input` and execution probes; see
# docs/GM_RUNTIME.md). The GM models run in code mode only: every tool call goes through one `exec` gateway, so
# disabling the code-mode host makes all tool calls fail closed before anything executes. The other switches remove the
# remaining tool families, their instructions and side effects, so a misconfiguration elsewhere still leaves no tools.
DISABLED_FEATURES = ["code_mode_host", "code_mode", "shell_tool", "shell_snapshot", "unified_exec", "multi_agent", "multi_agent_v2",
                     "hooks", "memories", "apps", "plugins", "browser_use", "browser_use_external", "computer_use",
                     "in_app_browser", "image_generation", "view_image", "goals", "sleep_tool", "skill_search", "tool_suggest",
                     "realtime_conversation"]
GM_CONFIG = ["mcp_servers={}", 'web_search="disabled"', "tools.web_search=false", "notify=[]", "agents.enabled=false",
             "skills.include_instructions=false", "include_environment_context=false", "include_permissions_instructions=false",
             'history.persistence="none"', 'shell_environment_policy.inherit="none"', "project_doc_max_bytes=0",
             "project_doc_fallback_filenames=[]"]
# Replaces Codex's coding-agent base instructions; the GM contract itself goes in as developer instructions.
BASE_INSTRUCTIONS = """You are the rules-bound Game Master service of a local tabletop RPG web app. You have no tools: you cannot run
commands, read or write files, browse, or start agents. Everything you need is in the developer instructions and the
user message, and the web server executes every game tool you request in your JSON reply. Reply only with the JSON
object required by the output schema."""
# The user's personal ~/.codex/AGENTS.md is still injected as a user message (codex 0.163 has no switch for it short of
# a separate CODEX_HOME, which would need a copy of the sign-in). This neutralizes it for GM jobs.
AGENTS_NOTICE = """# Scope of other instructions
Instructions labelled "AGENTS.md instructions" are the operator's personal preferences for coding sessions. They do not
apply to this game: ignore them and follow only these developer instructions and the response protocol."""
# Event items a GM job may produce. Anything else (command_execution, file_change, web_search, mcp_tool_call,
# collab/agent calls, todo lists, ...) is a violation: the job is killed at once and its result discarded.
ALLOWED_ITEMS = {"agent_message", "reasoning", "error"}
ALLOWED_EVENTS = {"thread.started", "turn.started", "turn.completed", "turn.failed", "item.started", "item.updated", "item.completed", "error"}
# Environment variables never handed to the Codex process.
SCRUB_ENV = re.compile(r"TOKEN|SECRET|PASSCODE|PASSWORD|API_KEY|CREDENTIAL|^GM_|^CRAWLER_|^CAMPAIGN_|^RULEBOOK_", re.I)


class IsolationViolation(ProviderError):
    def __init__(self, message: str, meta: dict):
        super().__init__(message)
        self.meta = meta


def _toml_str(text: str) -> str:
    return json.dumps(text, ensure_ascii=False)


def gm_exec_args(binary: str, job: Path, *, developer: str, schema: Path | None, final: Path, model: str | None,
                 effort: str | None) -> list[str]:
    """The exact `codex exec` command line used for GM jobs (also used by the isolation probe)."""
    args = [binary, "exec", "--strict-config", "--skip-git-repo-check", "--ephemeral", "--json", "--sandbox", "read-only", "--cd", str(job)]
    for f in DISABLED_FEATURES:
        args += ["--disable", f]
    for kv in GM_CONFIG:
        args += ["-c", kv]
    (job / "base.md").write_text(BASE_INSTRUCTIONS, encoding="utf-8")
    args += ["-c", f"model_instructions_file={_toml_str(str(job / 'base.md'))}", "-c", f"developer_instructions={_toml_str(developer)}"]
    if schema is not None:
        args += ["--output-schema", str(schema)]
    args += ["-o", str(final)]
    if model:
        args += ["-m", model]
    if effort:
        args += ["-c", f'model_reasoning_effort="{effort}"']
    args.append("-")
    return args


def _event_error(lines: list[str]) -> str:
    """The last error message in Codex's --json event stream (it reports usage limits there, not on stderr)."""
    for line in reversed(lines):
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        msg = ev.get("message") or (ev.get("error") or {}).get("message") if isinstance(ev, dict) else None
        if ev.get("type") in ("error", "turn.failed") and msg:
            return str(msg)
    return ""


def secure_dir(path: Path) -> None:
    """Limit a folder to this account (plus SYSTEM and Administrators): no inherited 'Authenticated Users' access."""
    path.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        path.chmod(0o700)
        return
    acl = subprocess.run(["icacls", str(path)], capture_output=True, text=True).stdout
    if "Authenticated Users" not in acl and "BUILTIN\\Users" not in acl:
        return
    user = os.environ.get("USERNAME", "")
    if os.environ.get("USERDOMAIN"):
        user = os.environ["USERDOMAIN"] + "\\" + user
    subprocess.run(["icacls", str(path), "/inheritance:r", "/grant:r", f"{user}:(OI)(CI)F", "/grant:r", "*S-1-5-18:(OI)(CI)F",
                    "/grant:r", "*S-1-5-32-544:(OI)(CI)F"], capture_output=True, check=False)


def child_env() -> dict[str, str]:
    return {k: v for k, v in os.environ.items() if not SCRUB_ENV.search(k)}


def _usage(stdout: str) -> dict[str, Any]:
    """Token usage and safety signals from `codex exec --json` events (also used on stored event logs)."""
    out: dict[str, Any] = {}
    for line in stdout.splitlines():
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if ev.get("type") == "turn.completed" and isinstance(ev.get("usage"), dict):
            out["usage"] = ev["usage"]
        item = ev.get("item") or {}
        if isinstance(item, dict) and item.get("type") and item["type"] not in ALLOWED_ITEMS:
            out["commands_run"] = out.get("commands_run", 0) + 1
    return out


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

    def _watch(self, proc: subprocess.Popen, meta: dict) -> tuple[list[str], str | None]:
        """Read the event stream as it arrives; kill the job on the first event that isn't an allowed message."""
        lines: list[str] = []
        violation = None
        assert proc.stdout is not None
        for raw in proc.stdout:
            line = raw.decode("utf-8", "replace").rstrip("\n")
            lines.append(line)
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            etype = ev.get("type")
            item = ev.get("item") if isinstance(ev.get("item"), dict) else {}
            if etype == "turn.completed" and isinstance(ev.get("usage"), dict):
                meta["usage"] = ev["usage"]
            if etype == "item.completed" and item.get("type") == "error":
                meta.setdefault("notices", []).append(str(item.get("message", ""))[:200])
            if etype not in ALLOWED_EVENTS or (item and item.get("type") not in ALLOWED_ITEMS):
                violation = f"{etype}/{item.get('type') if item else '-'}"
                meta["violation"] = {"event": etype, "item_type": item.get("type") if item else None,
                                     "detail": json.dumps(item or ev, ensure_ascii=False)[:300]}
                proc.kill()
                break
        return lines, violation

    def _run(self, prompt: str, schema: dict | None, label: str, developer: str) -> tuple[str, dict]:
        binary = resolve_codex_bin()
        if not binary:
            raise ProviderError("Codex CLI not found. Install/sign in to Codex or set CODEX_BIN; or switch the GM to mock mode.")
        capture = self.settings.prompt_capture_active()
        root = self.settings.codex_jobs_dir
        secure_dir(root)
        job = root / f"{time.strftime('%Y%m%d-%H%M%S')}-{label}-{uuid.uuid4().hex[:6]}"
        job.mkdir(mode=0o700)
        meta: dict[str, Any] = {"model": self.model or "(codex default)", "effort": self.effort, "prompt_captured": capture}
        try:
            schema_path = None
            if schema is not None:
                schema_path = job / "schema.json"
                schema_path.write_text(json.dumps(schema), encoding="utf-8")
            final = job / "final.json"
            args = gm_exec_args(binary, job, developer=developer, schema=schema_path, final=final, model=self.model, effort=self.effort)
            for attempt, delay in enumerate([0, *self.retry_delays]):
                if delay:
                    time.sleep(delay)
                started = time.monotonic()
                proc = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=job, env=child_env(),
                                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                err_chunks: list[bytes] = []
                err_reader = threading.Thread(target=lambda: err_chunks.append(proc.stderr.read()), daemon=True)  # type: ignore[union-attr]
                err_reader.start()
                timer = threading.Timer(self.settings.codex_timeout_s, proc.kill)
                timer.start()
                try:
                    proc.stdin.write(prompt.encode("utf-8"))  # type: ignore[union-attr]
                    proc.stdin.close()  # type: ignore[union-attr]
                    lines, violation = self._watch(proc, meta)
                    proc.wait()
                finally:
                    timer.cancel()
                err_reader.join(5)
                timed_out = time.monotonic() - started >= self.settings.codex_timeout_s
                meta["duration_s"] = round(time.monotonic() - started, 1)
                stderr = b"".join(err_chunks).decode("utf-8", "replace")
                if capture:
                    (job / "prompt.txt").write_text(f"[developer]\n{developer}\n\n[user]\n{prompt}", encoding="utf-8")
                    (job / "events.jsonl").write_text("\n".join(lines), encoding="utf-8")
                    (job / "stderr.txt").write_text(stderr, encoding="utf-8")
                if violation:
                    raise IsolationViolation(f"Codex attempted an unauthorized action ({violation}); the job was stopped and its result discarded", dict(meta))
                if timed_out:
                    raise ProviderError(f"Codex timed out after {self.settings.codex_timeout_s}s")
                if proc.returncode == 0 and final.exists():
                    return final.read_text(encoding="utf-8"), meta
                if CAPACITY.search(stderr) and attempt < len(self.retry_delays):
                    continue
                tail = "\n".join(stderr.strip().splitlines()[-6:]) or _event_error(lines)
                if re.search(r"usage limit", tail, re.I):  # the ChatGPT plan's limit: say so instead of "exited with code 1"
                    raise ProviderError(f"Codex usage limit reached on your ChatGPT plan: {tail[:300]}")
                raise ProviderError(f"Codex exited with code {proc.returncode}: {tail or 'no output'}")
            raise ProviderError("Codex is at capacity; try again shortly")
        finally:
            if capture:
                meta["job_dir"] = str(job)
            else:
                shutil.rmtree(job, ignore_errors=True)

    def complete(self, system: str, steps: list[dict], tools: list[dict], *, job_label: str) -> ProviderResponse:
        developer = "\n\n".join([system, OUTPUT_PROTOCOL, AGENTS_NOTICE])
        prompt = "\n\n".join(["# TOOLS\n" + tool_catalogue(tools), render_steps(steps)])
        for attempt in range(2):  # one retry for malformed structured output
            raw, meta = self._run(prompt, output_schema([t["name"] for t in tools]), f"{job_label}{'-retry' if attempt else ''}", developer)
            try:
                data = json.loads(raw)
                calls = [ToolCall(c["name"], json.loads(c["arguments_json"] or "{}")) for c in data.get("tool_calls") or []]
                break
            except (json.JSONDecodeError, KeyError, TypeError, AttributeError) as exc:
                if attempt:
                    raise ProviderError(f"Codex returned malformed output twice: {exc}") from None
        meta["prompt_chars"] = len(prompt) + len(developer)
        narration = data.get("narration")
        return ProviderResponse(calls, narration.strip() if isinstance(narration, str) and narration.strip() else None, meta)

    def summarize(self, text: str) -> str:
        schema = {"type": "object", "additionalProperties": False, "required": ["summary"], "properties": {"summary": {"type": "string"}}}
        developer = ("Summarize this tabletop RPG transcript excerpt for the GM's long-term memory in under 250 words. "
                     "Keep names, decisions, promises, consequences, unresolved threads and numbers that matter. "
                     "Reply only with the JSON object.\n\n" + AGENTS_NOTICE)
        raw, _ = self._run(text, schema, "summary", developer)
        return json.loads(raw)["summary"]


# ------------------------------------------------------------------ Gemini API
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
GEMINI_RETRY = {429, 500, 503}
# Mature fiction (violence, crude humor) is the genre; only high-probability harms are blocked.
GEMINI_SAFETY = [{"category": c, "threshold": "BLOCK_ONLY_HIGH"} for c in
                 ("HARM_CATEGORY_HARASSMENT", "HARM_CATEGORY_HATE_SPEECH", "HARM_CATEGORY_SEXUALLY_EXPLICIT", "HARM_CATEGORY_DANGEROUS_CONTENT")]


class TokenPacer:
    """Sliding 60-second window of input tokens per model. The free tier caps input tokens per minute (250k for
    gemini-3.5-flash-lite, cached tokens included), so a call that would exceed the cap waits instead of failing.
    Shared by every provider instance in the process (one per turn)."""

    _lock = threading.Lock()
    _windows: dict[str, list[tuple[float, int]]] = {}
    _ratio: dict[str, float] = {}  # observed tokens per prompt character

    @classmethod
    def estimate(cls, model: str, chars: int) -> int:
        return int(chars * cls._ratio.get(model, 0.3)) + 500

    @classmethod
    def wait(cls, model: str, tokens: int, limit: int, sleep=time.sleep, now=time.monotonic) -> float:
        waited = 0.0
        while True:
            with cls._lock:
                t = now()
                win = [(ts, n) for ts, n in cls._windows.get(model, []) if t - ts < 60]
                cls._windows[model] = win
                used = sum(n for _, n in win)
                if not win or used + tokens <= limit:
                    win.append((t, tokens))  # reserve; corrected by record()
                    return waited
                pause = 60 - (t - win[0][0]) + 0.5
            sleep(pause)
            waited += pause

    @classmethod
    def record(cls, model: str, estimated: int, actual: int, chars: int) -> None:
        with cls._lock:
            win = cls._windows.get(model, [])
            for i in range(len(win) - 1, -1, -1):
                if win[i][1] == estimated:
                    win[i] = (win[i][0], actual)
                    break
            if actual and chars:
                cls._ratio[model] = actual / chars


class GeminiProvider:
    """Google Gemini over its REST API (urllib; no SDK). Nothing but our JSON schema is offered to the model: no
    function declarations, no Google Search or code-execution tools, so the GM can only return JSON and the server
    runs every game tool, exactly as with Codex. The key comes from GEMINI_API_KEY or a key file (Settings) and is
    sent only in the x-goog-api-key header. Stable content goes first (contract, then tools, then the briefing) so
    Gemini's implicit prompt cache can reuse it across a turn's rounds."""

    name = "gemini"

    def __init__(self, settings: Settings, model: str | None = None, effort: str | None = None):
        self.settings = settings
        self.model = (model or settings.gemini_model).removeprefix("models/")
        self.effort = effort or settings.codex_reasoning_effort
        self.retry_delays = [10, 30, 60]

    def available(self) -> tuple[bool, str]:
        return (True, self.model) if self.settings.gemini_api_key() else (False, "no Gemini API key (GEMINI_API_KEY or gemini_api_key.txt)")

    def _post(self, body: dict, meta: dict) -> dict:
        key = self.settings.gemini_api_key()
        if not key:
            raise ProviderError("No Gemini API key: set GEMINI_API_KEY or put it in data\\gemini_api_key.txt.")
        data = json.dumps(body).encode("utf-8")
        for attempt, delay in enumerate([0, *self.retry_delays]):
            if delay:
                time.sleep(delay)
            req = urllib.request.Request(GEMINI_URL.format(model=self.model), data=data, method="POST",
                                         headers={"Content-Type": "application/json", "x-goog-api-key": key})
            started = time.monotonic()
            try:
                with urllib.request.urlopen(req, timeout=self.settings.codex_timeout_s) as r:
                    meta["duration_s"] = round(time.monotonic() - started, 1)
                    out = json.loads(r.read())
                from .router import GeminiQuota
                GeminiQuota(self.settings).record(self.model)
                return out
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", "replace")
                try:
                    err = json.loads(detail)["error"]
                    msg, details = err["message"], err.get("details") or []
                except (ValueError, KeyError, TypeError):
                    msg, details = detail[:300], []
                quota_ids = [v.get("quotaId", "") for d in details for v in d.get("violations") or []]
                if exc.code == 429 and any("PerDay" in q for q in quota_ids):  # retrying only burns more of the day's quota
                    from .router import GeminiQuota
                    GeminiQuota(self.settings).mark_spent(self.model)
                    raise ProviderError(f"Gemini daily free-tier quota for {self.model} is used up ({', '.join(quota_ids)}); "
                                        "it resets daily, or switch the GM back to Codex.") from None
                wait = next((d.get("retryDelay") for d in details if str(d.get("@type", "")).endswith("RetryInfo")), None)
                if exc.code in GEMINI_RETRY and attempt < len(self.retry_delays):
                    meta.setdefault("retries", []).append(exc.code)
                    if wait and str(wait).rstrip("s").replace(".", "", 1).isdigit():
                        time.sleep(min(float(str(wait).rstrip("s")), 90))  # the server's own retry hint
                    continue
                raise ProviderError(f"Gemini API error {exc.code}: {msg[:400]}") from None
            except (urllib.error.URLError, TimeoutError) as exc:
                if attempt < len(self.retry_delays):
                    meta.setdefault("retries", []).append(str(exc)[:60])
                    continue
                raise ProviderError(f"Gemini API unreachable: {exc}") from None
        raise ProviderError("Gemini API is rate-limited or unavailable; try again shortly")

    def _generate(self, system: str, prompt: str, schema: dict, label: str) -> tuple[str, dict]:
        meta: dict[str, Any] = {"model": self.model, "effort": self.effort, "provider": "gemini", "external_api": True}
        chars = len(system) + len(prompt)
        est = TokenPacer.estimate(self.model, chars)
        limit = self.settings.gemini_tpm_limit
        if est > limit:
            raise ProviderError(f"This briefing (~{est:,} tokens) exceeds the {self.model} per-minute limit of {limit:,}; "
                                "use the standard briefing size or raise GEMINI_TPM_LIMIT on a paid tier.")
        paced = TokenPacer.wait(self.model, est, int(limit * 0.92))
        if paced:
            meta["paced_s"] = round(paced, 1)
        cfg: dict[str, Any] = {"responseMimeType": "application/json", "responseJsonSchema": schema}
        if self.effort in ("low", "medium", "high"):
            cfg["thinkingConfig"] = {"thinkingLevel": self.effort}
        elif self.effort == "minimal":
            cfg["thinkingConfig"] = {"thinkingLevel": "minimal"}
        body = {"systemInstruction": {"parts": [{"text": system}]}, "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                "generationConfig": cfg, "safetySettings": GEMINI_SAFETY}
        try:
            out = self._post(body, meta)
        except ProviderError as exc:
            if "thinking" not in str(exc).lower() or "thinkingConfig" not in cfg:
                raise
            cfg.pop("thinkingConfig")  # model without thinking levels
            out = self._post(body, meta)
        um = out.get("usageMetadata") or {}
        TokenPacer.record(self.model, est, int(um.get("promptTokenCount") or est), chars)
        meta["usage"] = {"input_tokens": um.get("promptTokenCount", 0), "cached_input_tokens": um.get("cachedContentTokenCount", 0),
                         "output_tokens": um.get("candidatesTokenCount", 0), "reasoning_output_tokens": um.get("thoughtsTokenCount", 0)}
        cands = out.get("candidates") or []
        if not cands:
            block = (out.get("promptFeedback") or {}).get("blockReason")
            raise ProviderError(f"Gemini returned no answer{f' (prompt blocked: {block})' if block else ''}")
        cand = cands[0]
        parts = (cand.get("content") or {}).get("parts") or []
        if any(k in part for part in parts for k in ("functionCall", "executableCode", "codeExecutionResult")):
            meta["violation"] = {"item_type": "tool part", "detail": json.dumps(parts)[:300]}
            raise IsolationViolation("Gemini returned a tool call although none were offered; result discarded", dict(meta))
        text = "".join(part.get("text", "") for part in parts if not part.get("thought"))
        if cand.get("finishReason") not in (None, "STOP"):
            meta["finish_reason"] = cand.get("finishReason")
            if not text.strip():
                raise ProviderError(f"Gemini stopped without an answer (finish reason {cand.get('finishReason')})")
        return text, meta

    def complete(self, system: str, steps: list[dict], tools: list[dict], *, job_label: str) -> ProviderResponse:
        developer = "\n\n".join([system, OUTPUT_PROTOCOL.replace("Do not run shell commands or read files; everything\nyou need is in this prompt, and the server runs your tools.",
                                                                   "Everything you need is in this prompt; the server runs your tools.")])
        prompt = "\n\n".join(["# TOOLS\n" + tool_catalogue(tools), render_steps(steps)])
        for attempt in range(2):  # one retry for malformed structured output
            raw, meta = self._generate(developer, prompt, output_schema([t["name"] for t in tools]), job_label)
            try:
                data = json.loads(raw)
                calls = [ToolCall(c["name"], json.loads(c["arguments_json"] or "{}")) for c in data.get("tool_calls") or []]
                break
            except (json.JSONDecodeError, KeyError, TypeError, AttributeError) as exc:
                if attempt:
                    raise ProviderError(f"Gemini returned malformed output twice: {exc}") from None
        meta["prompt_chars"] = len(prompt) + len(developer)
        narration = data.get("narration")
        return ProviderResponse(calls, narration.strip() if isinstance(narration, str) and narration.strip() else None, meta)

    def summarize(self, text: str) -> str:
        schema = {"type": "object", "additionalProperties": False, "required": ["summary"], "properties": {"summary": {"type": "string"}}}
        system = ("Summarize this tabletop RPG transcript excerpt for the GM's long-term memory in under 250 words. "
                  "Keep names, decisions, promises, consequences, unresolved threads and numbers that matter.")
        raw, _ = self._generate(system, text, schema, "summary")
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
    if name == "gemini":
        return GeminiProvider(settings, model=model, effort=effort)
    if name == "auto":
        from .router import AutoGM
        return AutoGM(settings, codex_model=model, effort=effort)
    if name == "mock":
        return MockProvider()
    raise ValueError(f"unknown GM provider {name!r} (use auto, codex, gemini or mock)")


def prune_jobs(settings: Settings, keep: int = 20) -> None:
    """Job folders are deleted after each run; only an active prompt capture keeps some. Remove captures once the
    capture window has closed, and never keep more than `keep`."""
    d = settings.codex_jobs_dir
    if not d.exists():
        return
    jobs = sorted((p for p in d.iterdir() if p.is_dir()), key=lambda p: p.name)
    stale = jobs if not settings.prompt_capture_active() else jobs[:-keep]
    for p in stale:
        shutil.rmtree(p, ignore_errors=True)
