"""GM tools: the only way the model affects the game. Each tool validates its arguments and runs real code
(dice, rules engine, retrieval). Results go back to the model; player-visible effects are collected for the UI."""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from ..db import now_iso, one, rows
from ..memory.views import fts_query
from ..reference import rulebooks
from ..rules.checks import ADVANCEMENT_CLOCK, CheckRequest, perform_check, resolve_advancement_checkpoint
from ..rules.dice import DiceError, DiceRoller, record_roll
from ..rules.engine import ChangeError, apply_change, ops_reference, resolve_character

# Tools whose results the model does not need before narrating (see OUTPUT_PROTOCOL).
BOOKKEEPING_TOOLS = {"apply_changes", "count_cycle", "record_ruling", "gm_note", "system_notice", "reveal_secret", "suggest_image", "save_game"}

TOOL_SPECS: list[dict] = [
    {"name": "skill_check",
     "description": "Roll a Core Check: 1d20 + Skill Rank + Stat Mod + modifiers vs Difficulty. Omit `skill` for a Stat Check. "
                    "Omit `dc` to use the default for dc_kind (stat: 10+Floor; unopposed: 10+2×Floor; opposed: 10+antagonist_mod+Floor). "
                    "Untrained skills automatically roll with Disadvantage and no Rank. Trained non-Passive skills get their "
                    "advancement mark automatically. help: one entry per helper, dice '1d6' if they have Ranks in the Skill else '1d2' (they spend an Action). The result `summary` is the roll line to show.",
     "parameters": {"character": "str", "skill": "str?", "stat": "str", "dc": "int?", "dc_kind": "stat|unopposed|opposed?",
                    "antagonist_mod": "int?", "advantage": "int? (count)", "disadvantage": "int? (count)",
                    "modifiers": "[{source, value}]?", "help": "[{helper, dice}]?", "passive": "bool?", "reason": "str", "hidden": "bool? (secret GM check)"}},
    {"name": "roll_dice",
     "description": "Roll any other dice (damage, random tables, NPC outcomes, loot). Expression like '2d8+3', '4d6kh3', '2d20kl1'. "
                    "Set target to compare total >= target. hidden=true for secret GM rolls.",
     "parameters": {"expression": "str", "reason": "str", "target": "int?", "character": "str?", "hidden": "bool?"}},
    {"name": "apply_changes",
     "description": "Validate and apply durable state changes. Each change is an object with an `op`. Characters may be referenced by "
                    "name, slug, numeric id or 'player'; items by name or item_id. character null/'shared' = shared stash. "
                    "Use skill/level/ai_favor for training and awards, group/move_group/move_character for party splits (never teleport "
                    "people: only move who actually goes), scene to keep the current scene current, remember for NPC memories and private "
                    "beats, quest kind commitment/reservation for promises and held items (a reservation is never an owned item: when it is "
                    "claimed, complete the reservation quest earlier in the same batch, then add_item), "
                    "use_ability for limited uses, rest in saferooms (a rest advances the clock itself: never also advance_time for it), advance_time mending for calm hours. Scene fields, quest details, groups "
                    "and remember(visibility player) are SHOWN TO THE PLAYER: put hidden material only in gm_note or remember(visibility gm). Ops:\n" + ops_reference(),
     "parameters": {"changes": "[change objects]"}},
    {"name": "search_rules",
     "description": "Search campaign house rules, established rulings, and the indexed official rulebooks (with page refs). Give 2-4 "
                    "phrasings: the rule's name, synonyms, and the situation in plain words. Campaign results outrank official ones.",
     "parameters": {"queries": "[str]", "limit": "int? (default 5)"}},
    {"name": "read_rule_page",
     "description": "Read the full text of an official rulebook page found by search_rules (use the document_id and page).",
     "parameters": {"document_id": "int", "page": "int"}},
    {"name": "lookup_records",
     "description": "Search imported campaign records (ledger YAML/Markdown, including GM-only companion sheets), lore facts, and the "
                    "event log. Returns section ids and snippets; use read_record for full text.",
     "parameters": {"query": "str", "limit": "int? (default 6)"}},
    {"name": "read_record", "description": "Read one imported record section in full.", "parameters": {"section_id": "int"}},
    {"name": "record_ruling",
     "description": "Record an adjudication so it stays consistent. label: BOOK | CAMPAIGN | INTERPRETATION | ONE-SCENE | OPEN.",
     "parameters": {"topic": "str", "ruling": "str", "label": "str", "certainty": "canonical|provisional|uncertain?", "sources": "[str]?"}},
    {"name": "gm_note", "description": "Store GM-only continuity (hidden consequence, NPC private reaction/plan, secret clock). Never shown to the player.",
     "parameters": {"subject": "str", "content": "str", "kind": "plot|motivation|surprise|note?"}},
    {"name": "reveal_secret",
     "description": "Mark a GM secret revealed after the player legitimately learned it, recording only the part they learned as a player-visible fact.",
     "parameters": {"secret_id": "int", "player_visible_fact": "str", "subject": "str"}},
    {"name": "system_notice",
     "description": "Emit a System AI message to the player (style: notice | achievement | warning | tooltip). For achievements also "
                    "record them with apply_changes op 'achievement'.",
     "parameters": {"text": "str (Markdown)", "style": "str?"}},
    {"name": "suggest_image",
     "description": "Suggest a picture for the player to approve (rendered locally by ComfyUI; nothing renders without their click). "
                    "Use sparingly, at most one per turn, when the party first meets a notable character or Mob, gets a notable "
                    "item, or enters a striking new place. kind: character | mob | item | scene (scene = the current scene). "
                    "description: what it LOOKS like, only what the party has seen; never GM-only material. Skip it if the "
                    "subject already has a picture.",
     "parameters": {"kind": "str", "subject": "str (name; ignored for scene)", "description": "str (visual only, <= 60 words)", "reason": "str?"}},
    {"name": "count_cycle",
     "description": "Count one substantive decision-and-resolution cycle toward the advancement checkpoint (12 = one Level interval), "
                    "AFTER the decision is resolved. The server refuses recaps/status/rules/save turns, turns with only bookkeeping (time, "
                    "travel, scenes, rests, transfers, notes), a second cycle in one turn, and a second cycle in one combat. A refusal is normal: "
                    "do not retry it.",
     "parameters": {"decision": "str", "resolution": "str"}},
    {"name": "resolve_advancement_checkpoint",
     "description": "Resolve a due advancement checkpoint: +1 Level for the active HUD party, d20-vs-Rank checks for marked Rank 0-4 Skills, "
                    "clear resolved marks, carry excess cycles. Only at a saferoom, debrief, save point or floor transition.",
     "parameters": {"occasion": "str"}},
]
COMBAT_SPECS: list[dict] = [
    {"name": "combat_start",
     "description": "Start a mechanical combat (CORE_MECHANICS §10). Record setup first (terrain, positions, cover, escape routes). "
                    "foes are stat blocks: {name, kind: mob|elite|boss, quality: low|high, count, health_bar: [slot values] or slots+per_slot, "
                    "evade (resolved number, e.g. 13+F -> 16), dr, dex_mod, resistances {type: resist|vulnerable|immune}, actions?, max_slots_per_hit? (e.g. Spunk), "
                    "attacks: [{name, to_hit (resolved), damage e.g. '1d8+3', damage_type, range, area, fail_effect}]}. Take numbers from the "
                    "rulebook stat block when one exists (search_rules), otherwise build a fair one and record_ruling it. "
                    "party defaults to the active in-party crawlers present. surprise: none | crawlers | mobs.",
     "parameters": {"name": "str", "foes": "[stat block]", "party": "[str]?", "surprise": "str?", "setup": "str"}},
    {"name": "combat_declare_mob_attacks",
     "description": "Step 1: declare every Mob Attack this round [{mob, attack, target}] (targets can't change after Evades). Then STOP and "
                    "narrate the incoming Attacks; the player decides whether their crawler Evades or uses other Interrupts. Decide NPC allies yourself.",
     "parameters": {"attacks": "[{mob, attack, target}]"}},
    {"name": "combat_evade", "description": "Step 2 Interrupt: a crawler spends an Action to Evade all Attacks declared against them this round.",
     "parameters": {"crawler": "str"}},
    {"name": "combat_resolve_mob_attacks",
     "description": "Step 3: resolve declared Mob Attacks: Evade Checks for evaders, damage rolls, DR, Health Bar slots, Dying. Moves to the Crawler Action Phase.",
     "parameters": {}},
    {"name": "combat_attack",
     "description": "Step 4 crawler Attack: d20 + Skill Rank + Stat Mod vs the Mob's Evade, damage on a hit (Natural 20 doubles dice, Amazing adds Floor). "
                    "attack = name of an attack on the crawler's sheet (or give skill + stat + damage). Spends one Action; marks the Skill. "
                    "Bow, Crossbow and Handgun Attacks spend one of the ammunition the sheet names (or `ammunition`: the item), whose count "
                    "must be established; a Crossbow fires once per round; a Handgun needs combat_action 'Reload' after a Major Fail. "
                    "A Called Play for this attacker is applied automatically.",
     "parameters": {"attacker": "str", "target": "str", "attack": "str?", "advantage": "int?", "disadvantage": "int?",
                    "modifiers": "[{source, value}]?", "skill": "str?", "stat": "str?", "damage": "str?", "ammunition": "str?"}},
    {"name": "combat_catch",
     "description": "Step 2 Interrupt (Catcher, BOOK p. 189): a crawler with Ranks in Catcher spends an Action to take a declared direct "
                    "(non-Area) Attack aimed at an adjacent ally. The catcher takes it with no Evade; +5 DR at Catcher Rank 5, 10, 15.",
     "parameters": {"catcher": "str", "ally": "str", "mob": "str?", "attack": "str?"}},
    {"name": "combat_call_play",
     "description": "Step 4 Action (Call a Play, BOOK p. 88): propose a specific Action for an ally; 2d6 keep the higher is the bonus. When "
                    "that ally Attacks (the target, if given) the bonus is added to the Check if it needs it, otherwise to damage. Lasts "
                    "through the next round.",
     "parameters": {"caller": "str", "ally": "str", "action": "str", "target": "str?"}},
    {"name": "combat_intervene",
     "description": "Interrupt (Intervene, BOOK pp. 84, 93): after another crawler's Attack misses this round (usually a Near Miss), a "
                    "crawler spends an Action to add 1d6 to that total; a hit then deals damage. Once per Attack. attacker: whose miss "
                    "(default: the latest eligible one), so other Attacks can be resolved first.",
     "parameters": {"crawler": "str", "attacker": "str?"}},
    {"name": "combat_action", "description": "Spend an Action on anything else (Move, Help, Retrieve, Reload, Use Hotlist Item, non-attack Spell, Taunt...). Resolve its check/effect with the normal tools.",
     "parameters": {"combatant": "str", "action": "str"}},
    {"name": "combat_damage", "description": "Apply other damage (traps, debuffs, environment, Mob-vs-Mob): amount or dice expression; bypass_dr for Debuff damage.",
     "parameters": {"target": "str", "amount": "int?", "expression": "str?", "damage_type": "str?", "bypass_dr": "bool?", "cause": "str"}},
    {"name": "combat_end_round", "description": "Step 5 Clean Up: Dying countdowns tick, Actions reset, next round begins at Mob Action Declaration.", "parameters": {}},
    {"name": "combat_end", "description": "End the combat (victory, retreat, parley...). Pending Minor Injuries from bad Evades apply now.", "parameters": {"outcome": "str"}},
    {"name": "heal_slots", "description": "Heal a crawler by Health Bar slots (healing is always by slots; e.g. Heal Spell 20% = 2 slots). Ends Dying. Works in or out of combat.",
     "parameters": {"character": "str", "slots": "int", "cause": "str"}},
]
TOOL_SPECS.extend(COMBAT_SPECS)
TOOL_SPECS.extend([
    {"name": "grind",
     "description": "Grinding montage (BOOK 14.6-14.7): add hours to a Skill's grind progress; when hours reach its Rank (once per Skill per "
                    "dungeon day) roll the advancement check; also adds Level-grind progress. map_bonus: +1 hour for 5+ hour sessions with a "
                    "Neighborhood Map. Does not advance time: also call advance_time.",
     "parameters": {"character": "str", "skill": "str", "hours": "int", "map_bonus": "bool?"}},
    {"name": "party_check",
     "description": "Party Skill Check (CORE 7.8; BOOK p. 61) when the whole group performs one task together (sneaking past a post, "
                    "climbing down together); not when one qualified character can do it for everyone. Every participant rolls the Skill "
                    "vs the same Difficulty (no Help); the party succeeds if half or more succeed; each Amazing Success/Critical Hit turns "
                    "one teammate's failure into a Success, each Major/Critical Fail turns another's Success into a Failure. Give "
                    "participants, or group (a party group key) for its members. members: per-character Skill/Stat/Advantage overrides. "
                    "offscreen=true resolves a group the player character is NOT in (CORE 15.2): declare objective and stakes (what Failure "
                    "means) before the roll; rolls are secret by default and the outcome is logged; then apply its consequences with apply_changes.",
     "parameters": {"skill": "str", "stat": "str", "participants": "[str]?", "group": "str?", "members": "[{character, skill?, stat?, advantage?, disadvantage?, modifiers?}]?",
                    "dc": "int?", "dc_kind": "unopposed|opposed|stat?", "antagonist_mod": "int?", "modifiers": "[{source, value}]?", "reason": "str",
                    "hidden": "bool?", "offscreen": "bool?", "objective": "str?", "stakes": "str?", "on_success": "str?"}},
    {"name": "trap",
     "description": "Traps the party built (CORE 13.7, CAMPAIGN). action record: set a trap's full record BEFORE it is deployed (spec: "
                    "components, ammunition {item, per_trigger}?, deploy {actions, minutes}, trigger, area, check {kind: attack (attack_bonus "
                    "vs Evade) | operator (operator_skill + operator_stat) | save (save_stat vs dc) | none}, damage?, effects?, reset {actions, "
                    "minutes}, reset_notes?, single_use?, bulk, label CAMPAIGN|INTERPRETATION|BOOK, source). Never invent a record to resolve "
                    "a trigger: if a trap has none, ask for a ruling first. Records are locked while deployed. action deploy (trap, location, by): "
                    "armed, spending its deploy cost (Actions in combat, else time). action trigger (trap, target, operator?): its Check and "
                    "damage, ammunition spent. action reset (trap, by). action stow (trap). action status: every trap's record and condition.",
     "parameters": {"action": "record|deploy|trigger|reset|stow|status", "trap": "str?", "spec": "object?", "location": "str?", "by": "str?",
                    "target": "str?", "operator": "str?", "advantage": "int?", "target_number": "int? (the target's Evade outside combat)"}},
    {"name": "save_game",
     "description": "The player asked to save: creates a named manual save point when this turn commits (restore it from Saves & Settings).",
     "parameters": {"label": "str"}},
    {"name": "craft",
     "description": "One crafting attempt (CORE 13.1-13.6; BOOK pp. 223-227), declared in full first: nothing is spent unless every "
                    "material is on hand with an established count. kind common: 1 Misc. Junk, no Check. kind mundane: give size "
                    "(Table 43: tiny 1 junk/1h, small 4/4h, petite 16/9h, medium 32/16h, large 64/25h...) or project (a declared project "
                    "quest; its design supplies skill, Difficulty, hours, junk, lead and Advantage). Materials are consumed on the attempt, the "
                    "clock advances (advance_clock false if a parallel task already covers the time), the lead crafter rolls Fabricate (or "
                    "Engineering with moving_parts) with help [{helper, dice}]; Success adds the item (and completes the project), Failure "
                    "salvages by the margin (Table 47). Magical crafting is refused below Floor 4.",
     "parameters": {"crafter": "str", "item": "str", "kind": "common|mundane?", "size": "str?", "moving_parts": "bool?", "project": "str?",
                    "skill": "str?", "dc": "int?", "hours": "int?", "junk": "int?", "materials": "[{name, quantity, owner?, salvageable?}]?",
                    "help": "[{helper, dice}]?", "advantage": "int?", "disadvantage": "int?", "modifiers": "[{source, value}]?",
                    "owner": "str?", "description": "str?", "advance_clock": "bool?", "reason": "str?"}},
    {"name": "resolve_floor_end",
     "description": "At floor end: roll advancement for every marked Rank 5+ Skill (1d20 vs Rank). advantage: [{character, skill}] for "
                    "benefits such as the Human race's one eligible Rank 9-or-lower check with Advantage.",
     "parameters": {"occasion": "str", "advantage": "[{character, skill}]?"}},
])
BOOKKEEPING_TOOLS.update({"combat_action"})
TOOL_NAMES = [t["name"] for t in TOOL_SPECS]


class _Roll(BaseModel):
    expression: str
    reason: str
    target: int | None = None
    character: str | None = None
    hidden: bool = False


class _Search(BaseModel):
    queries: list[str] = Field(min_length=1, max_length=6)
    limit: int = Field(default=5, ge=1, le=10)


class _Ruling(BaseModel):
    topic: str
    ruling: str
    label: str = "INTERPRETATION"
    certainty: str = "provisional"
    sources: list[str] = Field(default_factory=list)


@dataclass
class TurnEffects:
    """Everything the player should see from this turn, plus audit data for re-recording rolls on failure."""
    rolls: list[dict] = field(default_factory=list)          # player-visible roll cards
    roll_records: list[tuple] = field(default_factory=list)  # (RollResult, character_id, hidden) for failure re-record
    changes: list[str] = field(default_factory=list)
    notices: list[dict] = field(default_factory=list)
    rules_cited: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    save_label: str | None = None                            # save_game: a manual save point after the turn commits


class ToolRunner:
    def __init__(self, conn: sqlite3.Connection, rule_conn: sqlite3.Connection | None, roller: DiceRoller,
                 campaign_id: int, turn_id: int, settings=None):
        self.conn, self.rule_conn, self.roller = conn, rule_conn, roller
        self.settings = settings  # the app's Settings (art suggestions pick their painter from it); None = from the environment
        self.cid, self.turn_id = campaign_id, turn_id
        self.effects = TurnEffects()

    def run(self, name: str, args: dict[str, Any]) -> dict:
        fn = getattr(self, f"t_{name}", None)
        if fn is None:
            return {"ok": False, "result": {"error": f"unknown tool {name!r}"}}
        try:
            return {"ok": True, "result": fn(args)}
        except (ChangeError, DiceError, ValidationError, KeyError, ValueError, TypeError) as exc:
            msg = str(exc) if not isinstance(exc, ValidationError) else json.dumps(exc.errors(include_url=False), default=str)[:800]
            self.effects.errors.append(f"{name}: {msg}")
            return {"ok": False, "result": {"error": msg}}

    # -- dice
    def _card(self, res, character_id) -> None:
        self.effects.roll_records.append((res, character_id, res.hidden))
        if not res.hidden:
            self.effects.rolls.append({"roll_id": res.id, "expression": res.expression, "dice": [d.__dict__ for d in res.dice],
                                       "total": res.total, "target": res.target, "outcome": res.outcome, "degree": res.degree,
                                       "reason": res.reason, "detail": res.detail})

    def t_skill_check(self, a: dict) -> dict:
        req = CheckRequest.model_validate(a)
        out = perform_check(self.conn, self.conn, self.roller, self.cid, self.turn_id, req)
        r = one(self.conn, "SELECT * FROM dice_rolls WHERE id = ?", (out["roll_id"],))
        from ..rules.dice import Die, RollResult
        res = RollResult(r["expression"], [Die(**d) for d in r["dice"]], r["modifier"], r["total"], r["target"], r["outcome"],
                         r["reason"], r["id"], bool(r["hidden"]), r["degree"], r["detail"])
        res.detail["summary"] = out["summary"]
        self._card(res, r["character_id"])
        if out["skill_marked"]:
            self.effects.changes.append(f"{req.skill} marked for advancement")
        return out

    def t_roll_dice(self, a: dict) -> dict:
        req = _Roll.model_validate(a)
        char = resolve_character(self.conn, self.cid, req.character) if req.character else None
        res = self.roller.roll(req.expression, target=req.target, reason=req.reason)
        res.detail = {"kind": "roll", "character": char["name"] if char else None}
        record_roll(self.conn, self.cid, res, turn_id=self.turn_id, character_id=char["id"] if char else None, hidden=req.hidden)
        self._card(res, char["id"] if char else None)
        return {"roll_id": res.id, "total": res.total, "dice": [d.value for d in res.dice if d.kept], "outcome": res.outcome, "hidden": req.hidden}

    def _card_new_rolls(self) -> None:
        """Show every roll recorded during this turn that has no card yet (tools that roll internally)."""
        from ..rules.dice import Die, RollResult
        seen = {x[0].id for x in self.effects.roll_records}
        for r in rows(self.conn, "SELECT * FROM dice_rolls WHERE turn_id = ? AND campaign_id = ? ORDER BY id", (self.turn_id, self.cid)):
            if r["id"] in seen:
                continue
            self._card(RollResult(r["expression"], [Die(**d) for d in r["dice"]], r["modifier"], r["total"], r["target"], r["outcome"],
                                  r["reason"], r["id"], bool(r["hidden"]), r["degree"], r["detail"]), r["character_id"])

    def t_grind(self, a: dict) -> dict:
        from ..rules.checks import grind
        out = grind(self.conn, self.conn, self.roller, self.cid, self.turn_id, str(a["character"]), str(a["skill"]), int(a["hours"]),
                    bool(a.get("map_bonus")))
        self._card_new_rolls()
        self.effects.changes.extend(out["lines"][:-1])
        return out

    def t_party_check(self, a: dict) -> dict:
        from ..rules.party import PartyCheckRequest, party_check
        req = PartyCheckRequest.model_validate(a)
        self.conn.execute("SAVEPOINT party")  # all participants roll, or none do
        try:
            out = party_check(self.conn, self.roller, self.cid, self.turn_id, req)
        except Exception:
            self.conn.execute("ROLLBACK TO party")
            self.conn.execute("RELEASE party")
            raise
        self.conn.execute("RELEASE party")
        self._card_new_rolls()
        if not out["hidden"]:
            self.effects.changes.append(out["summary"])
        return out

    def t_trap(self, a: dict) -> dict:
        from ..rules import traps
        act = str(a.get("action") or "")
        if act == "status":
            return {"traps": traps.status(self.conn, self.cid)}
        if act not in ("record", "deploy", "trigger", "reset", "stow"):
            raise ValueError("action must be record, deploy, trigger, reset, stow or status")
        if not a.get("trap"):
            raise ValueError("name the trap")
        self.conn.execute("SAVEPOINT trap")
        try:
            if act == "record":
                out = traps.record(self.conn, self.cid, str(a["trap"]), a.get("spec") or {})
            elif act == "deploy":
                out = traps.deploy(self.conn, self.roller, self.cid, self.turn_id, str(a["trap"]), str(a.get("location") or ""), a.get("by"))
            elif act == "trigger":
                out = traps.trigger(self.conn, self.roller, self.cid, self.turn_id, str(a["trap"]), str(a.get("target") or ""),
                                    a.get("operator"), int(a.get("advantage") or 0),
                                    int(a["target_number"]) if a.get("target_number") is not None else None)
            elif act == "reset":
                out = traps.reset(self.conn, self.roller, self.cid, self.turn_id, str(a["trap"]), a.get("by"))
            else:
                out = traps.stow(self.conn, self.cid, str(a["trap"]))
        except Exception:
            self.conn.execute("ROLLBACK TO trap")
            self.conn.execute("RELEASE trap")
            raise
        self.conn.execute("RELEASE trap")
        self._card_new_rolls()
        self.effects.changes.extend(out.get("lines") or [out["summary"]])
        return out

    def t_save_game(self, a: dict) -> dict:
        self.effects.save_label = (str(a.get("label") or "").strip() or "Player save")[:120]
        return {"summary": f"Save point '{self.effects.save_label}' will be created when this turn commits"}

    def t_craft(self, a: dict) -> dict:
        from ..rules.crafting import CraftRequest, craft
        req = CraftRequest.model_validate(a)
        self.conn.execute("SAVEPOINT craft")  # one attempt is atomic: an invalid step spends nothing
        try:
            out = craft(self.conn, self.roller, self.cid, self.turn_id, req)
        except Exception:
            self.conn.execute("ROLLBACK TO craft")
            self.conn.execute("RELEASE craft")
            raise
        self.conn.execute("RELEASE craft")
        self._card_new_rolls()
        self.effects.changes.extend(l for l in out["lines"] if l != (out.get("check") or {}).get("summary"))  # the roll has its own card
        return out

    def t_resolve_floor_end(self, a: dict) -> dict:
        from ..rules.checks import resolve_floor_end
        out = resolve_floor_end(self.conn, self.conn, self.roller, self.cid, self.turn_id, str(a.get("occasion") or "floor end"), a.get("advantage"))
        self._card_new_rolls()
        self.effects.changes.extend(out["lines"])
        return out

    # -- combat
    def _combat(self, fn, *args, **kw) -> dict:
        from ..rules.combat import Combat
        cb = Combat(self.conn, self.cid, self.turn_id, self.roller)
        self.conn.execute("SAVEPOINT combat")  # one combat step is atomic: an Attack that fails validation spends no ammunition
        try:
            out = getattr(cb, fn)(*args, **kw)
        except Exception:
            self.conn.execute("ROLLBACK TO combat")
            self.conn.execute("RELEASE combat")
            raise
        self.conn.execute("RELEASE combat")
        for res, char_id, _hidden in cb.rolls:
            self._card(res, char_id)
        lines = out.get("lines") or [out["summary"]]
        self.effects.changes.extend(line for line in lines if line)
        return out

    def t_combat_start(self, a: dict) -> dict:
        from ..rules.combat import StartCombat
        return self._combat("start", StartCombat.model_validate(a))

    def t_combat_declare_mob_attacks(self, a: dict) -> dict:
        return self._combat("declare", list(a.get("attacks") or []))

    def t_combat_evade(self, a: dict) -> dict:
        return self._combat("evade", str(a.get("crawler") or a.get("character") or ""))

    def t_combat_resolve_mob_attacks(self, a: dict) -> dict:
        return self._combat("resolve")

    def t_combat_attack(self, a: dict) -> dict:
        keys = ("attack", "advantage", "disadvantage", "modifiers", "skill", "stat", "damage", "ammunition")
        return self._combat("attack", str(a["attacker"]), str(a["target"]), **{k: a[k] for k in keys if a.get(k) is not None})

    def t_combat_catch(self, a: dict) -> dict:
        return self._combat("catch", str(a["catcher"]), str(a["ally"]), a.get("mob"), a.get("attack"))

    def t_combat_call_play(self, a: dict) -> dict:
        return self._combat("call_play", str(a["caller"]), str(a["ally"]), str(a.get("action") or ""), a.get("target"))

    def t_combat_intervene(self, a: dict) -> dict:
        return self._combat("intervene", str(a["crawler"]), a.get("attacker"))

    def t_combat_action(self, a: dict) -> dict:
        return self._combat("spend_action", str(a["combatant"]), str(a.get("action") or "Action"))

    def t_combat_damage(self, a: dict) -> dict:
        return self._combat("damage", str(a["target"]), amount=a.get("amount"), expression=a.get("expression"),
                            damage_type=a.get("damage_type"), bypass_dr=bool(a.get("bypass_dr")), cause=str(a.get("cause") or ""))

    def t_combat_end_round(self, a: dict) -> dict:
        return self._combat("end_round")

    def t_combat_end(self, a: dict) -> dict:
        return self._combat("end", str(a.get("outcome") or "ended"))

    def t_heal_slots(self, a: dict) -> dict:
        from ..rules.combat import heal_character_slots
        slots = int(a["slots"])
        if not 1 <= slots <= 100:
            raise ValueError("slots must be 1-100")
        ch = resolve_character(self.conn, self.cid, str(a["character"]))
        text = heal_character_slots(self.conn, self.cid, self.turn_id, ch, slots, str(a.get("cause") or ""))
        self.effects.changes.append(text)
        return {"summary": text}

    # -- state
    def t_apply_changes(self, a: dict) -> dict:
        changes = a.get("changes")
        if not isinstance(changes, list) or not changes:
            raise ValueError("changes must be a non-empty list")
        # One call = one atomic batch: if any change is invalid, none apply (drinking a potion must both
        # remove it and heal, or do neither).
        results, applied = [], []
        self.conn.execute("SAVEPOINT batch")
        for ch in changes:
            try:
                r = apply_change(self.conn, self.cid, self.turn_id, ch if isinstance(ch, dict) else {})
                results.append({"ok": True, "summary": r["summary"]})
                applied.append(r)
            except ChangeError as exc:
                results.append({"ok": False, "op": ch.get("op") if isinstance(ch, dict) else None, "error": str(exc)})
        if not all(r["ok"] for r in results):
            self.conn.execute("ROLLBACK TO batch")
            self.conn.execute("RELEASE batch")
            errors = "; ".join(r["error"] for r in results if not r["ok"])
            raise ChangeError(f"no changes applied (batch is atomic): {errors}")
        self.conn.execute("RELEASE batch")
        self.effects.changes.extend(r["summary"] for r in applied if r.get("visibility", "player") == "player")
        return {"results": results}

    def t_count_cycle(self, a: dict) -> dict:
        decision, resolution = str(a.get("decision", "")).strip(), str(a.get("resolution", "")).strip()
        if not decision or not resolution:
            raise ValueError("decision and resolution are required")
        clock = one(self.conn, "SELECT * FROM clocks WHERE campaign_id = ? AND key = ?", (self.cid, ADVANCEMENT_CLOCK))
        if not clock:
            return {"skipped": "this campaign has no advancement cycle tracker"}
        r = apply_change(self.conn, self.cid, self.turn_id, {"op": "clock", "key": ADVANCEMENT_CLOCK, "delta": 1,
                                                             "reason": f"{decision} → {resolution}"})
        self.effects.changes.append(r["summary"])
        now = one(self.conn, "SELECT current, maximum FROM clocks WHERE id = ?", (clock["id"],))
        return {"cycles": f"{now['current']}/{now['maximum']}", "checkpoint_due": now["current"] >= (now["maximum"] or 12)}

    def t_resolve_advancement_checkpoint(self, a: dict) -> dict:
        out = resolve_advancement_checkpoint(self.conn, self.conn, self.roller, self.cid, self.turn_id, str(a.get("occasion") or "checkpoint"))
        for r in rows(self.conn, "SELECT * FROM dice_rolls WHERE turn_id = ? AND campaign_id = ? AND reason LIKE '%advancement%' ORDER BY id", (self.turn_id, self.cid)):
            if r["id"] in {x[0].id for x in self.effects.roll_records}:
                continue
            from ..rules.dice import Die, RollResult
            self._card(RollResult(r["expression"], [Die(**d) for d in r["dice"]], r["modifier"], r["total"], r["target"], r["outcome"],
                                  r["reason"], r["id"], False, r["degree"], r["detail"]), r["character_id"])
        self.effects.changes.extend(out["lines"])
        return out

    # -- knowledge
    def t_search_rules(self, a: dict) -> dict:
        req = _Search.model_validate(a)
        q = fts_query(" ".join(req.queries))
        campaign = rows(self.conn, """SELECT h.id, h.section, h.title, h.label, substr(h.text, 1, 1200) AS text FROM house_rules h
                                      WHERE h.campaign_id = ? AND h.id IN (SELECT id FROM house_rules WHERE campaign_id = ?)
                                      ORDER BY h.id""", (self.cid, self.cid))
        terms = [t.strip('"') for t in q.split(" OR ")]
        scored = sorted(((sum(t in (h["title"] + " " + h["text"]).lower() for t in terms), h) for h in campaign), key=lambda x: -x[0])
        campaign_hits = [{"source": "CAMPAIGN HOUSE RULE", **h} for s, h in scored[:3] if s >= 2]
        from ..reference.rulecenter import ruling_status
        rulings = [{"source": "PRECEDENT RULING" if st == "precedent" else "PROVISIONAL RULING (not precedent)",
                    **{k: r[k] for k in ("topic", "ruling", "label", "certainty")}}
                   for r in rows(self.conn, "SELECT topic, ruling, label, certainty, review FROM rulings WHERE campaign_id = ? ORDER BY id DESC LIMIT 200", (self.cid,))
                   if (st := ruling_status(r)) != "rejected" and sum(t in (r["topic"] + " " + r["ruling"]).lower() for t in terms) >= 2][:3]
        official = []
        if self.rule_conn is not None:
            for p in rulebooks.search(self.rule_conn, req.queries, limit=req.limit):
                doc_id = self.rule_conn.execute("SELECT document_id FROM chunks WHERE id = ?", (p.chunk_id,)).fetchone()[0]
                official.append({"source": "OFFICIAL RULEBOOK", "ref": p.ref(), "document_id": doc_id, "page": p.page, "text": p.text[:1100]})
                self.effects.rules_cited.append(p.ref())
        return {"campaign": campaign_hits, "rulings": rulings, "official": official,
                "note": "Campaign rulings/house rules outrank official text where they apply." if campaign_hits or rulings else None,
                "index_status": "no rulebooks indexed" if self.rule_conn is None else None}

    def t_read_rule_page(self, a: dict) -> dict:
        if self.rule_conn is None:
            raise ValueError("no rulebook index")
        doc = self.rule_conn.execute("SELECT title FROM documents WHERE id = ?", (int(a["document_id"]),)).fetchone()
        if not doc:
            raise ValueError("unknown document_id")
        text = rulebooks.page_text(self.rule_conn, int(a["document_id"]), int(a["page"]))
        self.effects.rules_cited.append(f"{doc['title']}, p. {a['page']}")
        return {"ref": f"{doc['title']}, p. {a['page']}", "text": text[:4000]}

    def t_lookup_records(self, a: dict) -> dict:
        query = str(a.get("query", ""))
        limit = min(int(a.get("limit") or 6), 12)
        q = fts_query(query)
        sections = rows(self.conn, """SELECT s.id, s.document_path, s.section, s.visibility,
                                             snippet(source_sections_fts, 1, '«', '»', ' … ', 48) AS snippet
                                      FROM source_sections_fts JOIN source_sections s ON s.id = source_sections_fts.rowid
                                      WHERE source_sections_fts MATCH ? AND s.campaign_id = ? ORDER BY rank LIMIT ?""", (q, self.cid, limit))
        for s in sections:
            if s["visibility"] == "gm":
                s["visibility"] = "GM-ONLY"
        terms = [t.strip('"') for t in q.split(" OR ")][:8]
        like = " OR ".join("lower(summary) LIKE ?" for _ in terms) or "0"
        events = rows(self.conn, f"SELECT id, type, summary, visibility, game_time_minutes FROM events WHERE campaign_id = ? AND rolled_back = 0 AND ({like}) ORDER BY id DESC LIMIT 8",
                      (self.cid, *[f"%{t}%" for t in terms]))
        flike = " OR ".join("lower(subject || ' ' || fact) LIKE ?" for _ in terms) or "0"
        facts = rows(self.conn, f"SELECT subject, fact, certainty, visibility FROM lore_facts WHERE campaign_id = ? AND ({flike}) ORDER BY id DESC LIMIT 8",
                     (self.cid, *[f"%{t}%" for t in terms]))
        return {"record_sections": sections, "events": events, "facts": facts}

    def t_read_record(self, a: dict) -> dict:
        s = one(self.conn, "SELECT id, document_path, section, content, visibility FROM source_sections WHERE campaign_id = ? AND id = ?", (self.cid, int(a["section_id"])))
        if not s:
            raise ValueError("unknown section_id")
        if s["visibility"] == "gm":
            s["visibility"] = "GM-ONLY — never reveal directly"
        s["content"] = s["content"][:9000]
        return s

    def t_record_ruling(self, a: dict) -> dict:
        r = _Ruling.model_validate(a)
        label = r.label.upper()
        if label not in {"BOOK", "CAMPAIGN", "INTERPRETATION", "ONE-SCENE", "OPEN"}:
            raise ValueError("label must be BOOK, CAMPAIGN, INTERPRETATION, ONE-SCENE or OPEN")
        if r.certainty not in {"canonical", "provisional", "uncertain"}:
            raise ValueError("certainty must be canonical, provisional or uncertain")
        self.conn.execute("INSERT INTO rulings (campaign_id, turn_id, topic, ruling, certainty, label, sources_json, created_at) VALUES (?,?,?,?,?,?,?,?)",
                          (self.cid, self.turn_id, r.topic, r.ruling, r.certainty, label, json.dumps(r.sources), now_iso()))
        self.effects.changes.append(f"Ruling recorded ({label}): {r.topic}")
        return {"recorded": True}

    def t_suggest_image(self, a: dict) -> dict:
        from ..art.service import create_job
        from ..config import Settings
        kind = str(a.get("kind", "")).strip().lower()
        subject = str(a.get("subject", "")).strip()
        made = one(self.conn, "SELECT COUNT(*) AS n FROM art_jobs WHERE campaign_id = ? AND turn_id = ? AND source = 'gm'", (self.cid, self.turn_id))
        if made["n"] >= 1:
            raise ValueError("one picture suggestion per turn")
        if kind in ("character", "item"):
            table = "characters" if kind == "character" else "items"
            row = one(self.conn, f"SELECT id FROM {table} WHERE campaign_id = ? AND lower(name) = lower(?)", (self.cid, subject))
            if not row and kind == "character":
                row = {"id": resolve_character(self.conn, self.cid, subject)["id"]}
            if not row:
                raise ValueError(f"no {kind} named {subject!r}")
            sid, name = row["id"], None
        elif kind == "scene":
            sc = one(self.conn, "SELECT id FROM scenes WHERE campaign_id = ? AND status = 'active' ORDER BY id DESC LIMIT 1", (self.cid,))
            if not sc:
                raise ValueError("no active scene; start one with apply_changes op scene first")
            sid, name = sc["id"], None
        elif kind == "mob":
            sid, name = None, subject
        else:
            raise ValueError("kind must be character, mob, item or scene")
        if one(self.conn, f"""SELECT id FROM art_jobs WHERE campaign_id = ? AND kind = ? AND status IN ('suggested','queued','running','done')
                               AND {'lower(subject_name) = lower(?)' if kind == 'mob' else 'subject_id = ?'}""", (self.cid, kind, name if kind == "mob" else sid)):
            return {"skipped": "that subject already has a picture or a pending suggestion"}
        job = create_job(self.conn, self.settings or Settings(), self.cid, kind, subject_id=sid, subject_name=name or subject,
                         extra=str(a.get("description") or "")[:500], source="gm", status="suggested", reason=a.get("reason"), turn_id=self.turn_id)
        self.effects.changes.append(f"Picture suggested: {job['subject_name']} ({kind}) — approve it in the Art queue")
        return {"suggestion_id": job["id"], "note": job["note"]}

    def t_gm_note(self, a: dict) -> dict:
        subject, content = str(a.get("subject", "")).strip(), str(a.get("content", "")).strip()
        if not subject or not content:
            raise ValueError("subject and content are required")
        sid = self.conn.execute("INSERT INTO gm_secrets (campaign_id, subject, kind, content, source, created_at) VALUES (?,?,?,?,?,?)",
                                (self.cid, subject, str(a.get("kind") or "note"), content, f"turn {self.turn_id}", now_iso())).lastrowid
        return {"secret_id": sid}

    def t_reveal_secret(self, a: dict) -> dict:
        s = one(self.conn, "SELECT * FROM gm_secrets WHERE campaign_id = ? AND id = ?", (self.cid, int(a["secret_id"])))
        if not s:
            raise ValueError("unknown secret_id")
        fact = str(a.get("player_visible_fact", "")).strip()
        if not fact:
            raise ValueError("player_visible_fact is required (only what the player actually learned)")
        self.conn.execute("UPDATE gm_secrets SET revealed = 1 WHERE id = ?", (s["id"],))
        r = apply_change(self.conn, self.cid, self.turn_id, {"op": "fact", "subject": str(a.get("subject") or s["subject"]), "fact": fact,
                                                             "certainty": "canonical", "visibility": "player"})
        self.effects.changes.append(r["summary"])
        return {"revealed": True}

    def t_system_notice(self, a: dict) -> dict:
        text = str(a.get("text", "")).strip()
        if not text:
            raise ValueError("text is required")
        style = str(a.get("style") or "notice")
        if style not in {"notice", "achievement", "warning", "tooltip"}:
            style = "notice"
        self.effects.notices.append({"text": text[:3000], "style": style})
        return {"queued": True}
