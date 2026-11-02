"""Auto GM routing: spend the daily Gemini free-tier quotas on the turns they handle well, Codex on the rest.

Turn types (decided before any model is called, from the player's words and the campaign state):
* meta    recaps, status and rules questions        -> gemini-3.5-flash-lite (500/day)
* talk    conversation and roleplay, no mechanics   -> gemini-3.8-flash, then gemini-3.6-flash (20/day each)
* action  anything with danger, a story in progress, purchases, travel or other state changes, or any turn while a story is active -> Codex;
          also any turn that involves characters who are not with the player character (messages, reports, debriefs,
          "when they get back"), discoveries or scouting: what absent people did must be resolved, not narrated
          (playtest 1, H3)
A model without enough quota left for a turn is skipped; when none is left the turn goes to Codex.

Gemini turns run in conversation mode: only low-risk tools (lookups, notes, NPC memories and attitudes, facts,
picture suggestions). If the GM finds the turn needs state changes it calls `escalate`, and the
turn is re-run by Codex from scratch; staging means nothing was applied. Quotas reset at midnight Pacific, as Google's do.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import threading
from pathlib import Path

from ..config import Settings

DEFAULT_LIMITS = {"gemini-3.8-flash": 20, "gemini-3.6-flash": 20, "gemini-3.5-flash-lite": 500}
ROUTES = {"meta": ["gemini-3.5-flash-lite"], "talk": ["gemini-3.8-flash", "gemini-3.6-flash"], "action": []}
MIN_REQUESTS = {"meta": 2, "talk": 3}  # a turn can take a few calls; don't start one a model can't finish

SAFE_TOOLS = {"search_rules", "read_rule_page", "lookup_records", "read_record", "record_ruling", "gm_note", "system_notice",
              "suggest_image", "apply_changes"}
SAFE_OPS = {"remember", "npc_attitude"}   # no "fact": conversation never establishes canon (playtest 1, H3)
ESCALATE_SPEC = {"name": "escalate",
                 "description": "Hand this turn to the mechanics GM. Call it (alone, with narration null) as soon as the player's action needs "
                                "danger, a contested outcome, time passing, travel, money, items, rest or any other state change you can't make here.",
                 "parameters": {"reason": "str"}}
CONVERSATION_MODE = """# CONVERSATION MODE (this turn)
This turn was routed to you as conversation, roleplay or a question. You have only lookup, note, memory, attitude, fact
and picture tools; apply_changes accepts only the ops remember and npc_attitude. If answering properly needs
danger, a contested outcome, time passing, travel, money, items, rest or any other state change, call `escalate` immediately
instead of narrating it. Never narrate an outcome that would need a mechanic you can't run. Never say what a character
or group who is not present did, found, saw or says now (a report, a reply on the party chat, a return) beyond what
the records already state: call `escalate`. Never present anything new as established fact."""

RECAP = re.compile(r"\b((?:give|tell|show) me (?:a |the )?(?:quick )?(?:recap|summary|rundown|status)\w*|what do the rules say(?: about)?|recap|previously on|remind me|status check|status update|where (?:is|are) (?:everyone|we|the)|what do (?:i|we) know|"
                   r"what(?:'s| is) (?:my|our|the) |how many|how much|rules? (?:question|for|say|on)|what do the rules|how does .* work|"
                   r"ooc\b|out of character|save point)", re.I)
ACTION = re.compile(r"\b(attack|fight|kill|shoot|stab|slash|swing|hit|punch|kick|cast|spell|sneak|hide|steal|pickpocket|lockpick|pick the lock|"
                    r"climb|jump|leap|run|flee|chase|search|investigate|inspect|examine|loot|grab|take|pick up|buy|purchase|sell|pay|spend|"
                    r"trade|haggle|negotiat|bargain|persuade|intimidate|deceive|bluff|craft|build|forge|repair|brew|cook|rest|sleep|nap|"
                    r"wait|camp|travel|go to|go back|head (?:to|back|off|out|down|up|over)|walk|move|leave|enter|exit|open|split|send|"
                    r"equip|unequip|drink|eat|use|throw|heal|train|grind|level|allocate|evade|dodge|block|shove|grapple|roll|check|reserve|"
                    r"hold (?:a|the|it|this)|give|hand over|drop|set up|deploy|activate|summon|scout|explore|follow|track|offer|gold|slip|creep|approach|crawl|tiptoe|grappl)\w*", re.I)
TALK = re.compile(r"[\"“”]|\b(ask|asks|tell|tells|say|says|talk|chat|reply|replies|answer|greet|thank|apolog|comfort|reassure|joke|"
                  r"listen|wonder|confide|chat|banter|whisper|smile|nod|laugh|sigh|introduce|share|admit|promise)\w*", re.I)


OFFSCREEN = re.compile(r"\b(message|messages|text|ping|radio|chat|contact|check in|report|reports|debrief|status report|hear back|"
                       r"news from|when (?:they|he|she) (?:arrive|arrives|return|returns|get back|gets back|come back|comes back)|"
                       r"(?:they|he|she) (?:should be|must be) back|recon team|the others|discover|discovers|find out|learn)\w*", re.I)


SERVER_TURN_ROUTES = {"arrival": ("talk", "a quiet arrival (the move is already applied)"),
                      "arrival_omen": ("action", "an Omen interrupted the walk"),
                      "opening": ("action", "the opening sets the lure and the cast"),
                      "zone": ("talk", "a quiet move inside a venue"),
                      "zone_omen": ("action", "an Omen in this room"),
                      "talk_resident": ("talk", "the player walks over to a resident")}


def classify_turn(text: str, story_active: bool, absent: set[str] | frozenset = frozenset(), turn_kind: str | None = None) -> tuple[str, str]:
    """classify(), except that a turn the server started (an arrival after travel, the opening) routes by its kind. An active story
    still sends everything to Codex."""
    if turn_kind in SERVER_TURN_ROUTES and not story_active:
        return SERVER_TURN_ROUTES[turn_kind]
    return classify(text, story_active, absent)


def classify(text: str, story_active: bool, absent: set[str] | frozenset = frozenset()) -> tuple[str, str]:
    """(kind, reason) for routing. Errs toward 'action' (Codex) when unsure. `absent`: names and group labels of
    characters who are not with the player character."""
    if story_active:
        return "action", "a story is active"
    off = OFFSCREEN.search(text)
    if off:
        return "action", f"involves absent characters or discoveries ('{off.group(0)}')"
    who = next((n for n in sorted(absent, key=len, reverse=True) if len(n) > 2 and re.search(rf"\b{re.escape(n)}\b", text, re.I)), None)
    if who:
        return "action", f"mentions {who}, who is not with the player character"
    recap = RECAP.search(text)
    plain = RECAP.sub(" ", text)
    act = None
    for sentence in re.split(r"(?<=[.!?])\s+", plain):
        # An action word after a talk verb is the topic ("asks Margit whether she misses her forge"), not an action;
        # if the conversation turns into one, the GM escalates to Codex.
        talk = TALK.search(sentence)
        found = ACTION.search(sentence[: talk.start()] if talk else sentence)
        if found:
            act = found
            break
    if recap and (not act or act.group(0).lower().startswith("gold")):   # "how much Gold do we have" is a question
        return "meta", "recap, status or rules question"
    if act:
        return "action", f"mentions '{act.group(0)}'"
    if TALK.search(text):
        return "talk", "conversation without mechanics"
    if text.strip().endswith("?"):
        return "meta", "a question without an action"
    return "action", "not clearly conversation"


# ------------------------------------------------------------------ quota
def pacific_day(now: dt.datetime | None = None) -> str:
    """Today's date in US Pacific time (Gemini's daily quotas reset at midnight Pacific). Uses the US DST rule directly
    because this Windows Python has no time-zone database."""
    now = now or dt.datetime.now(dt.timezone.utc)
    y = now.year
    second_sun_march = dt.datetime(y, 3, 8 + (6 - dt.date(y, 3, 8).weekday()) % 7, 10, tzinfo=dt.timezone.utc)  # 2:00 PST = 10:00 UTC
    first_sun_nov = dt.datetime(y, 11, 1 + (6 - dt.date(y, 11, 1).weekday()) % 7, 9, tzinfo=dt.timezone.utc)      # 2:00 PDT = 09:00 UTC
    offset = -7 if second_sun_march <= now < first_sun_nov else -8
    return (now + dt.timedelta(hours=offset)).date().isoformat()


class GeminiQuota:
    """Requests used per model today (persisted in data/gemini_quota.json), with a model marked spent after a daily 429."""

    _lock = threading.Lock()

    def __init__(self, settings: Settings):
        self.path: Path = settings.data_dir / "gemini_quota.json"
        try:
            self.limits = {**DEFAULT_LIMITS, **json.loads(os.environ.get("GEMINI_DAILY_LIMITS") or "{}")}
        except ValueError:
            self.limits = dict(DEFAULT_LIMITS)

    def _load(self) -> dict:
        try:
            d = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            d = {}
        if d.get("day") != pacific_day():
            d = {"day": pacific_day(), "used": {}, "spent": []}
        return d

    def _save(self, d: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(d), encoding="utf-8")

    def remaining(self, model: str) -> int:
        with self._lock:
            d = self._load()
            if model in d["spent"]:
                return 0
            return max(0, int(self.limits.get(model, 0)) - int(d["used"].get(model, 0)))

    def record(self, model: str, n: int = 1) -> None:
        with self._lock:
            d = self._load()
            d["used"][model] = int(d["used"].get(model, 0)) + n
            self._save(d)

    def mark_spent(self, model: str) -> None:
        with self._lock:
            d = self._load()
            if model not in d["spent"]:
                d["spent"].append(model)
            self._save(d)

    def report(self) -> dict:
        with self._lock:
            d = self._load()
        return {"day": d["day"], "models": {m: {"limit": lim, "used": int(d["used"].get(m, 0)), "spent": m in d["spent"]}
                                            for m, lim in self.limits.items()}}


def pick_model(kind: str, quota: GeminiQuota, has_key: bool) -> str | None:
    if not has_key:
        return None
    for model in ROUTES.get(kind, []):
        if quota.remaining(model) >= MIN_REQUESTS.get(kind, 2):
            return model
    return None


class Escalate(Exception):
    """A conversation-mode turn needs the mechanics GM (reroute=False), or its model ran out of quota and the turn
    should be routed again (reroute=True: the next Gemini model with quota, else Codex)."""

    def __init__(self, reason: str, reroute: bool = False):
        super().__init__(reason)
        self.reroute = reroute


def check_call(name: str, args: dict) -> str | None:
    """Why a conversation-mode tool call must go to Codex, or None if it is allowed."""
    if name == "escalate":
        return str(args.get("reason") or "the GM asked for the mechanics GM")
    if name not in SAFE_TOOLS:
        return f"needs {name}"
    if name == "apply_changes":
        bad = sorted({str(c.get("op")) for c in args.get("changes") or [] if c.get("op") not in SAFE_OPS})
        if bad:
            return f"needs state changes ({', '.join(bad)})"
    return None


class AutoGM:
    """Provider factory for GM_PROVIDER=auto: `route()` picks the concrete provider for one turn."""

    name = "auto"

    def __init__(self, settings: Settings, codex_model: str | None = None, effort: str | None = None, gemini_effort: str | None = None,
                 gemini_profile: str = "large", codex_profile: str = "standard"):
        self.settings = settings
        self.codex_model, self.effort, self.gemini_effort = codex_model, effort, gemini_effort or "medium"
        self.gemini_profile, self.codex_profile = gemini_profile, codex_profile
        self.quota = GeminiQuota(settings)

    def codex(self):
        from .providers import CodexCliProvider
        p = CodexCliProvider(self.settings, model=self.codex_model, effort=self.effort)
        p.context_profile = self.codex_profile
        return p

    def gemini(self, model: str, conversation: bool = True):
        from .providers import GeminiProvider
        p = GeminiProvider(self.settings, model=model, effort=self.gemini_effort)
        p.context_profile = self.gemini_profile
        p.conversation = conversation
        return p

    def route(self, text: str, story_active: bool, absent: set[str] | frozenset = frozenset(), kind: str | None = None) -> tuple[object, dict]:
        kind, why = classify_turn(text, story_active, absent, kind)
        model = pick_model(kind, self.quota, bool(self.settings.gemini_api_key()))
        info = {"kind": kind, "reason": why, "provider": "gemini" if model else "codex", "model": model or (self.codex_model or "codex default")}
        if kind != "action" and not model:
            info["fallback"] = "no Gemini quota or key left for this turn type"
        return (self.gemini(model) if model else self.codex()), info

    def summarizer(self):
        """Transcript summaries are background work: Flash Lite while it has quota, else Codex."""
        if self.settings.gemini_api_key() and self.quota.remaining("gemini-3.5-flash-lite") >= 1:
            return self.gemini("gemini-3.5-flash-lite", conversation=False)
        return self.codex()

    def complete(self, *a, **k):  # pragma: no cover - the orchestrator always routes first
        raise RuntimeError("AutoGM must be routed before use")

    def summarize(self, text: str) -> str:
        return self.summarizer().summarize(text)
