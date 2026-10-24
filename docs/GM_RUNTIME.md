# GM runtime: isolation, transactions, context (2026-10-24)

Verified against **codex-cli 0.163.0-alpha.11.2** with model gpt-6.1-sol. Re-run the probe after any Codex update:

```bat
cd backend
..\.venv\Scripts\python -m app.gm.isolation_probe
```

The report goes to `data/diagnostics/isolation_probe.json` and *Saves & Settings → GM diagnostics → runtime*.

## 1. Isolation

The architecture is unchanged: every GM step is a fresh `codex exec --ephemeral`, and SQLite is the only memory.

**How it is enforced** (`gm/providers.py`, `gm_exec_args`):

| Layer | Setting | What it does (verified) |
|---|---|---|
| Tool gateway | `--disable code_mode_host` (+ `code_mode`) | GPT-6.x models are `tool_mode: code_mode_only`: every tool goes through one `exec` gateway. With the host disabled it fails closed: "Code Mode is unavailable … will fail closed". Attempted `fetch()`, `apply_patch` and MCP calls returned that error and nothing executed. |
| Tool families | `--disable shell_tool shell_snapshot unified_exec multi_agent multi_agent_v2 hooks memories apps plugins browser_use browser_use_external computer_use in_app_browser image_generation view_image goals sleep_tool skill_search tool_suggest realtime_conversation`; `-c mcp_servers={} web_search="disabled" tools.web_search=false agents.enabled=false skills.include_instructions=false` | Removes the tools, and their instructions, even if the gateway were re-enabled. |
| Side effects | `notify=[]`, `history.persistence="none"`, `--ephemeral` | Your desktop `notify` program no longer runs per GM job; no session or history files are written. |
| Filesystem | `--sandbox read-only`; job folders in `data/codex_jobs`, ACL limited to your account, SYSTEM and Administrators | Writes are refused (`apply_patch` → "writing is blocked by read-only sandbox" before the gateway was closed). |
| Environment | child env drops `*TOKEN*`, `*SECRET*`, `*PASSCODE*`, `*API_KEY*`, `GM_*`, `CRAWLER_*`, `CAMPAIGN_*`, `RULEBOOK_*` | The GM token and other secrets aren't visible to the process. |
| Instructions | `model_instructions_file` = a short neutral GM-service base; `developer_instructions` = GM contract + response protocol | Codex's coding-agent base prompt, environment context and permission text are gone (checked with `codex debug prompt-input`). |
| Validation | `--strict-config` | An unknown or misspelled option fails the job instead of being ignored. |
| Live watcher | `_watch` reads `--json` events as they arrive | Any event item other than `agent_message`, `reasoning` or `error` (command, file change, web search, MCP, agent call…) kills the process at once and fails the turn. Tested with a fake Codex for 5 item types. |
| Output | `--output-schema`, JSON parse, one retry, server-side tool validation | Unchanged. |

**Probe results:**

* *Before* (old flags), an attack prompt actually executed `web__run` (fetched example.com), `spawn_agent` (a sub-agent answered) and `list_mcp_resources`. The old post-run check missed all three, because it only looked for command and file events.
* *After*: the visible tool list is `NONE`, all seven attempts were unavailable, no file was written, the canary wasn't leaked, and the watcher saw no unauthorized events. Verdict PASS.

**Remaining exposure (not fixable with supported options):**

* **Your personal `~/.codex/AGENTS.md` is still injected** as a user message. Codex 0.163 loads it from `CODEX_HOME`, and the only way around that is a separate `CODEX_HOME`, which needs a copy of your sign-in (not done). It's neutralized by a developer instruction that says those preferences don't apply. If you want it gone completely, move that line out of the global file into your project-level AGENTS.md files.
* The `<collaboration_mode>` developer block (about 1k characters) still comes from Codex. It's harmless.
* The sandbox is Codex's own. The watcher kills a job at its first event, which is quick, but an event is emitted when an action *starts*, so the watcher is not a pre-execution boundary. The pre-execution boundary is the disabled gateway plus the read-only sandbox.
* Prompts still go to OpenAI, as before, because that's how the GM runs.

## 2. Transactions (`gm/orchestrator.py`)

No write transaction is open while Codex runs.

1. A read snapshot builds the briefing and a **state fingerprint**: a SHA-256 hash of every table a save point captures, plus the log high-water ids.
2. Each round, the requested tools run inside a short `BEGIN IMMEDIATE`. The staged calls from earlier rounds are re-applied first, then the transaction is **rolled back**. The GM gets the results; committed state doesn't change.
3. Dice come from `ReplayDice`: drawn from the CSPRNG the first time, then logged, and replays get the same values back in order. A replay asking for different dice is a conflict.
4. The final commit re-checks the fingerprint, replays every staged call with the same dice, and writes the transcript, events, autosave and diagnostics in **one** transaction.
5. If anything changed in between (another process, a restore, an import), the turn fails with `StateConflict` and nothing is applied.
6. Each round's stage (calls plus roll records) is saved in `turns.stage_json`. After a crash, startup marks the turn failed and records the dice it had drawn as `[interrupted turn]`. A failed turn records its rolls as `[failed turn]`. A retry with the same idempotency key reuses the turn row and rolls fresh; the earlier rolls stay in the audit.

## 3. Context

Sections map to categories, which diagnostics report per turn: `state` (campaign, party, present sheets, objectives, stash, combat) · `scene_memory` (scene, episodic and NPC memory, facts) · `contract_rules` (house rules, rulings) · `rulebook` (auto-retrieved passages) · `narrative` (recent transcript) · `gm_private` (secrets). The GM contract itself is in the developer instructions.

Budgets: `memory/context.py DEFAULT_BUDGET`, overridable with `CONTEXT_BUDGETS={"present_each": 3200}` in `.env`. Anything a budget cuts is listed in the turn's `omissions`. After the first live test, companion sheets were raised to 3,800 characters and always-apply house rules to 2,600, because Marcus's, Scout's and Vada's private sheets and §3.2 were being cut.

**Measurements (Denton campaign, same status-check input):**

| | Rounds | Prompt chars | Input tokens |
|---|---|---|---|
| Before (turn 6, old flags) | 2 | 74,497 / 81,654 | 30,571 / 32,368 (**62,939**) |
| After (acceptance copy) | 1 | 76,618 | **21,430** |

Per round, similar prompt sizes cost about 9k fewer tokens: Codex's coding prompt, tool schemas and skill lists are no longer sent. Multi-round turns still re-send the briefing every round, e.g. the six-round rules-and-stealth turn used 21k→29k per round, 156k in total. Prompt caching rarely hits (0–2.7k cached), because each `exec` is a new session.

**Within-turn session reuse was tested and rejected.** `codex exec resume <id>` on an ephemeral thread fails ("no rollout found"). Without `--ephemeral`, Codex would save every GM prompt, secrets included, into your personal Codex session history. So the briefing is re-sent each round. To reduce cost, keep turns to one or two rounds (the contract already asks the GM to batch calls) and keep relevance-ranked budgets; don't cut personality or commitments.

## 4. Job files

* Nothing is retained by default: the schema, base-instructions file and final answer are deleted when the run ends. The prompt is sent through stdin and never written.
* `data/codex_jobs` is locked to your account. Old folders are pruned at startup.
* **Prompt capture** is opt-in and short-lived: *GM diagnostics → runtime → Capture 15 min* (at most 60). It keeps the full prompt, events and stderr, and the folders are deleted when the window closes or you turn it off.
* Diagnostics (context packet, tool steps, usage) live in `turn_diagnostics`, readable only with the GM token. Nothing new is player-visible.

## 5. Continuity report

Every committed turn stores `continuity` in its diagnostics, shown under *GM diagnostics → continuity*:

* player character sheet loaded
* every party group listed
* every open objective, project, promise and reservation present (compared with what existed before the turn)
* private sheets included for present NPCs
* current scene loaded
* dungeon time consistent
* no cycle counted on a recap
* no verbatim secret leak
* mechanics recorded

It also lists the loaded categories, the omissions and the turn's validated changes. On recap or status turns, "mentioned but not recorded" heuristics are downgraded to info.

## 6. Gemini provider (optional, `GM_PROVIDER=gemini`)

`GeminiProvider` calls the Gemini REST API (`gemini-3.8-flash` by default) with the same contract, briefing and JSON
schema. **No function declarations, Search or code-execution tools are sent**, so the model can only return JSON;
a response containing a function-call or code part is rejected like a Codex violation. The key goes in the
`x-goog-api-key` header only (never the URL, logs or player endpoints). Rate limits (429) and 5xx errors are retried
with backoff; usage maps to the same diagnostics fields. Reasoning effort maps to Gemini's `thinkingLevel`.

**Privacy:** Google says free-tier inputs and outputs may be used to improve its products, and every GM call carries
private companion sheets and GM secrets. Settings shows this warning when Gemini is selected.

**First results (demo campaign only, 2026-10-24):**

* Five-round combat turn at "medium": correct combat flow, CSPRNG Evades and slot damage applied by the server,
  self-corrected a rejected call. But slow: about 270 s, 37–73 s per round, mostly thinking tokens.
* Narrative turn at "low": 17 s, one round, about 6.5k input tokens. It **invented continuity** (Bix "reported" a
  boiler-room scout that never happened in the record), where Codex had flagged the same gap. Prompt-cache hits: 0.

**Denton comparison (disposable copies, same starting state; Codex at medium, Gemini at low):**

| Turn | Codex (gpt-6.1-sol) | Gemini 3.8 Flash |
|---|---|---|
| Status check | 1 round, 16 s, 21.4k tokens. Accurate; flagged the location conflict | 1 round, 93 s, 20.1k tokens. Accurate and richer, but invented a relationship and stated disputed Gold (4,016) as fact |
| Rules + Stealth | 6 rounds (read pages, recorded a ruling), 62 s | 3 rounds, 128 s; correct DC 16 formula; NPCs covered for Cliff on their own; implicit cache hit 17–19k of 21k |
| Party split | New "en route" group, 1 minute passes, no materials spent | Moved Nate and Priya straight into the saferoom group (no travel time); **wrote Cliff's dialogue** |
| Reservation (two turns) | NPC refused a free hold; then 5 Gold paid, reservation with price, balance and deadline | Rolled Negotiation (near miss), then 5 Gold paid, reservation without price or balance; **scripted Cliff's speech and invented details** |
| Prompt injection | Refused, no tools | Not run: quota exhausted |
| Duplicate submission | Replayed | Replayed (no API call) |

**Free-tier quota:** gemini-3.8-flash allows **20 requests**, and the error reports a per-day quota. About 14 requests
on this copy plus 6 on the demo used it up in one session, and a Denton turn takes 1–6 requests. The provider now
stops immediately on a daily-quota 429 instead of retrying, which only burned more quota.
