"""Combat engine (CORE_MECHANICS §10–11, Core Rulebook pp. 81–96, 269).

Round sequence (BOOK 10.3): 1 Mob Action Declaration → 2 Crawler Reaction (Interrupts, e.g. Evade) →
3 Mob Action Resolution → 4 Crawler Action Phase → 5 Clean Up.

* Mobs don't roll to hit (GM rolls only damage). A crawler who spent an Action to Evade makes an Evade Check
  (d20 + Evade bonus, normally DEX) against each declared Attack's to-hit value. Success avoids it (Area: half);
  Natural 20 / Amazing grant Advantage on the next Attack against that Mob; Major Fail adds Floor damage and a
  Minor Injury after combat; Critical Fail doubles damage.
* Crawler Attack: d20 + Attack Skill Rank + Stat Mod vs Mob Evade (stat block, default 10 + DEX Mod + Floor).
  Natural 20 doubles base damage dice; Amazing Success adds the Floor Number.
* Ammunition (BOOK p. 183): Bow, Crossbow and Handgun Attacks each spend one round from the supply the sheet names (or
  `ammunition`), whose count must be established; the shots are tallied for recovery after combat (CORE 12.3). A
  Crossbow fires once per round; a Handgun needs an Action to reload after a Major Fail or worse.
* Call a Play (BOOK p. 88, CORE 10.9): an Action; 2d6 keep the higher. When the ally makes the proposed Attack the
  bonus is added to the Check if it needs it, otherwise to damage. INTERPRETATION: a Called Play lasts through the next
  round, then lapses.
* Intervene (BOOK pp. 84, 93, CORE 7.7): an Interrupt Action after another crawler's Attack result is known; +1d6 to
  that total, which may turn a miss (usually a Near Miss) into a hit.
* Catcher (BOOK p. 189, CORE 10.7): an Interrupt in the Crawler Reaction Phase by a crawler with Ranks in Catcher, on a
  direct (non-Area) Attack declared at an adjacent ally: the catcher takes it with no Evade, gaining +5 DR at Ranks 5,
  10 and 15.
* Damage order (BOOK 11.1): DR → resistance → vulnerability → immunity → Health Bar. Damage is applied in whole
  Health Bar slots; damage smaller than a slot is lost (Core p. 94).
* 0% HB: Mobs die; crawlers gain Dying (countdown = CON Mod, INTERPRETATION — the book says "CON" on p. 96 and
  "CON Mod" in Table 11). Clean Up and any damage reduce the countdown; healing ≥1 slot ends Dying.

Mob stat blocks are GM-only. Player views get names and Health Bar percentages (memory/views.py).
State lives in encounters.combatants_json; crawler Health lives on the character row (single source of truth).
"""
from __future__ import annotations

import json
import math
import re
import sqlite3
from typing import Any, Literal

from pydantic import BaseModel, Field

from ..db import now_iso, one, rows
from .checks import STAT_ALIASES, degree_of, is_success
from .dice import DiceRoller, RollResult, record_roll
from .engine import ChangeError, Ctx, apply_change, resolve_character

DYING_BASIS = "con_mod"   # INTERPRETATION; see module docstring
AMMO_SKILLS = {"bow", "crossbow", "handgun", "rifle", "shotgun", "sling"}   # each Attack spends ammunition (BOOK p. 183)


def _singular(text: str) -> str:
    """'Shared Crossbow Bolts' and 'Crossbow Bolt' compare equal."""
    words = [w[:-1] if w.endswith("s") and not w.endswith("ss") else w for w in re.findall(r"[a-z]+", text.lower())]
    return " ".join(words)


# ------------------------------------------------------------------ inputs
class MobAttack(BaseModel):
    name: str
    to_hit: int = Field(ge=0, le=100)                # the Evade Difficulty for crawlers ("13+F" already resolved)
    damage: str                                       # e.g. "1d8+3"
    damage_type: str | None = None
    range: str | None = None
    area: bool = False
    fail_effect: str | None = None                   # applied by the GM on Evade Major Fail or worse


class StatBlock(BaseModel):
    name: str
    kind: Literal["mob", "elite", "boss", "npc"] = "mob"
    quality: Literal["low", "high"] = "low"
    count: int = Field(default=1, ge=1, le=30)
    health_bar: list[int] | None = None               # slot values, e.g. [2, 2, 2]
    slots: int | None = Field(default=None, ge=1, le=200)
    per_slot: int | None = Field(default=None, ge=1, le=1000)
    evade: int | None = Field(default=None, ge=0, le=100)
    dex_mod: int = 0
    dr: int = Field(default=0, ge=0, le=100)
    resistances: dict[str, Literal["resist", "vulnerable", "immune"]] = Field(default_factory=dict)
    actions: int | None = Field(default=None, ge=1, le=30)
    max_slots_per_hit: int | None = Field(default=None, ge=1, le=50)   # e.g. Goblin "Spunk": one slot per hit unless Amazing+
    attacks: list[MobAttack] = Field(default_factory=list)
    level: int | None = None
    notes: str | None = None


class StartCombat(BaseModel):
    name: str
    foes: list[StatBlock] = Field(min_length=1)
    party: list[str] | None = None                    # default: active in-party crawlers
    surprise: Literal["none", "crawlers", "mobs"] = "none"
    setup: str | None = None                          # terrain, positions, cover, escape routes (BOOK 10.1)


# ------------------------------------------------------------------ helpers
def _floor(conn: sqlite3.Connection, cid: int) -> int:
    f = one(conn, "SELECT current_floor FROM campaigns WHERE id = ?", (cid,))["current_floor"]
    if f is None:
        raise ChangeError("current floor is unknown; set it before combat")
    return f


def active_encounter(conn: sqlite3.Connection, cid: int) -> dict | None:
    return one(conn, "SELECT * FROM encounters WHERE campaign_id = ? AND status = 'active' ORDER BY id DESC LIMIT 1", (cid,))


def _require(conn, cid) -> dict:
    enc = active_encounter(conn, cid)
    if not enc:
        raise ChangeError("no active combat; start one with combat_start")
    return enc


def _save(conn: sqlite3.Connection, enc: dict, state: dict) -> None:
    conn.execute("UPDATE encounters SET combatants_json = ?, round = ?, summary = ? WHERE id = ?",
                 (json.dumps(state), state["round"], state.get("setup"), enc["id"]))


def _state(enc: dict) -> dict:
    st = enc["combatants"]
    if not isinstance(st, dict) or "combatants" not in st:
        raise ChangeError("this encounter was recorded without the combat engine; end it and start a new combat")
    return st


def _find(state: dict, ref: str, side: str | None = None) -> dict:
    key = ref.strip().lower()
    pool = [c for c in state["combatants"] if side is None or c["side"] == side]
    for c in pool:
        if c["id"] == key or c["name"].lower() == key:
            return c
    hits = [c for c in pool if c["name"].lower().startswith(key) or key in c["name"].lower()]
    if len(hits) == 1:
        return hits[0]
    if not hits:
        raise ChangeError(f"no combatant matches {ref!r}; combatants: {[c['name'] for c in pool]}")
    raise ChangeError(f"{ref!r} is ambiguous: {[c['name'] for c in hits]}")


def _char_slots(c: dict) -> tuple[int, int]:
    """(slot value, max slots) for a crawler: sheet health_bar, else CON Mod slots (Core p. 94)."""
    d = c.get("derived") or {}
    hb = d.get("health_bar") if isinstance(d.get("health_bar"), dict) else {}
    per = hb.get("health_per_slot") or ((c.get("stats") or {}).get("modifiers") or {}).get("constitution")
    if not per or per <= 0:
        raise ChangeError(f"{c['name']} has no Health Bar slot value (sheet health_bar or CON Mod) recorded")
    slots = hb.get("slots") or (math.ceil((c["hp_max"] or 0) / per) if c["hp_max"] else None)
    if not slots:
        raise ChangeError(f"{c['name']} has no maximum Health recorded")
    return int(per), int(slots)


def _mod(c: dict, stat: str) -> int:
    v = ((c.get("stats") or {}).get("modifiers") or {}).get(stat)
    if v is None:
        raise ChangeError(f"{c['name']} has no recorded {stat} modifier")
    return int(v)


def parse_damage(expr: str) -> tuple[str, str]:
    """'1d6+4 Bludgeoning' -> ('1d6+4', 'Bludgeoning')."""
    """Also handles add-on dice in sheet text: '2d8 Piercing + 1d4 Aiming damage' -> ('2d8+1d4', 'Piercing')."""
    terms = re.findall(r"(\d*d\d+(?:\s*[+-]\s*\d+(?!\s*d))?)", expr or "")
    if not terms:
        raise ChangeError(f"cannot read damage expression {expr!r}")
    first = re.search(r"\d*d\d+(?:\s*[+-]\s*\d+(?!\s*d))?\s*([A-Za-z]+)?", expr)
    return "+".join(t.replace(" ", "") for t in terms), ((first.group(1) if first else "") or "")


def double_dice(expr: str) -> str:
    """Double the BASE damage dice (the first dice term) for a Critical Hit: '2d8+1d4+3' -> '4d8+1d4+3'."""
    return re.sub(r"(\d*)d(\d+)", lambda m: f"{int(m.group(1) or 1) * 2}d{m.group(2)}", expr, count=1)


def _mitigate(amount: int, dr: int, dtype: str | None, resist: dict[str, str], bypass_dr: bool = False) -> tuple[int, list[str]]:
    notes = []
    if not bypass_dr and dr:
        amount = max(0, amount - dr)
        notes.append(f"DR {dr}")
    r = resist.get((dtype or "").lower()) if dtype else None
    if r == "resist":
        amount //= 2
        notes.append(f"{dtype} resistance (half)")
    elif r == "vulnerable":
        amount *= 2
        notes.append(f"{dtype} vulnerability (double)")
    elif r == "immune":
        amount = 0
        notes.append(f"{dtype} immunity")
    return amount, notes


# ------------------------------------------------------------------ damage application
def damage_character(conn: sqlite3.Connection, cid: int, turn_id: int | None, char: dict, amount: int,
                     combatant: dict | None = None, cause: str = "") -> str:
    """Apply already-mitigated damage to a crawler's Health Bar in whole slots; handle Dying."""
    ctx = Ctx(conn, cid, turn_id)
    per, max_slots = _char_slots(char)
    dying = one(conn, "SELECT id, description FROM status_effects WHERE character_id = ? AND name = 'Dying'", (char["id"],))
    if dying:
        left = _dying_left(dying) - 1
        return _set_dying(ctx, char, left, f"took damage while Dying ({cause})")
    cur_slots = math.ceil((char["hp_current"] or 0) / per)
    lost = min(cur_slots, amount // per)
    if lost == 0:
        return f"{char['name']} shrugs off {amount} damage (less than one {per}-point Health Bar slot)"
    new_slots = cur_slots - lost
    hp_new = min(char["hp_current"], new_slots * per)
    conn.execute("UPDATE characters SET hp_current = ? WHERE id = ?", (hp_new, char["id"]))
    pct = round(100 * new_slots / max_slots)
    summary = f"{char['name']} loses {lost} Health Bar slot{'s' if lost != 1 else ''} ({char['hp_current']} → {hp_new} HP, {pct}%)" + (f" — {cause}" if cause else "")
    ctx.event("hp_changed", summary, {"character_id": char["id"], "before": char["hp_current"], "after": hp_new, "slots_lost": lost})
    if new_slots == 0:
        countdown = _mod(char, "constitution") if DYING_BASIS == "con_mod" else ((char.get("stats") or {}).get("stats") or {}).get("constitution", 1)
        summary += "; " + _set_dying(ctx, char, max(0, int(countdown)), "reduced to 0% Health Bar")
    return summary


def _dying_left(row: dict) -> int:
    m = re.search(r"(\d+)", row.get("description") or "")
    return int(m.group(1)) if m else 0


def _set_dying(ctx: Ctx, char: dict, left: int, why: str) -> str:
    ctx.conn.execute("DELETE FROM status_effects WHERE character_id = ? AND name = 'Dying'", (char["id"],))
    if left <= 0:
        ctx.conn.execute("UPDATE characters SET status = 'dead', hp_current = 0 WHERE id = ?", (char["id"],))
        summary = f"{char['name']} DIES ({why})"
        ctx.event("character_death", summary, {"character_id": char["id"]})
        return summary
    ctx.conn.execute("INSERT INTO status_effects (character_id, name, description, polarity, source) VALUES (?,?,?,?,?)",
                     (char["id"], "Dying", f"{left} rounds before death (CON Mod countdown; INTERPRETATION)", "debuff", "combat"))
    summary = f"{char['name']} is Dying — {left} round{'s' if left != 1 else ''} left ({why})"
    ctx.event("character_down", summary, {"character_id": char["id"], "rounds_left": left})
    return summary


def heal_character_slots(conn: sqlite3.Connection, cid: int, turn_id: int | None, char: dict, slots: int, cause: str = "") -> str:
    per, max_slots = _char_slots(char)
    cur = math.ceil((char["hp_current"] or 0) / per)
    new = min(max_slots, cur + slots)
    hp_new = min(char["hp_max"] or new * per, new * per)
    conn.execute("UPDATE characters SET hp_current = ? WHERE id = ?", (hp_new, char["id"]))
    ended = conn.execute("DELETE FROM status_effects WHERE character_id = ? AND name = 'Dying'", (char["id"],)).rowcount if new > 0 else 0
    summary = f"{char['name']} heals {new - cur} slot{'s' if new - cur != 1 else ''} ({char['hp_current']} → {hp_new} HP)" + (" and is no longer Dying" if ended else "") + (f" — {cause}" if cause else "")
    Ctx(conn, cid, turn_id).event("hp_changed", summary, {"character_id": char["id"], "before": char["hp_current"], "after": hp_new})
    return summary


def damage_mob(m: dict, amount: int, degree: str | None = None) -> tuple[int, str]:
    """Remove whole slots right-to-left while the damage covers them; remainder is lost. Returns (slots_lost, text).
    A stat-block cap (max_slots_per_hit) applies unless the hit was an Amazing Success or Critical Hit."""
    remaining, lost = amount, 0
    slots = m["slots"]
    cap = m.get("max_slots_per_hit") if degree not in ("Amazing Success", "Critical Hit") else None
    while slots and remaining >= slots[-1] and (cap is None or lost < cap):
        remaining -= slots.pop()
        lost += 1
    m["defeated"] = not slots
    pct = round(100 * sum(slots) / m["max_hb"]) if m["max_hb"] else 0
    if not lost:
        return 0, f"{m['name']} takes no meaningful damage ({amount} < one slot)"
    return lost, f"{m['name']} loses {lost} slot{'s' if lost != 1 else ''} ({pct}% left)" + (" — DEFEATED" if m["defeated"] else "")


# ------------------------------------------------------------------ engine
class Combat:
    def __init__(self, conn: sqlite3.Connection, cid: int, turn_id: int | None, roller: DiceRoller):
        self.conn, self.cid, self.turn_id, self.roller = conn, cid, turn_id, roller
        self.rolls: list[tuple[RollResult, int | None, bool]] = []

    def _roll(self, expr: str, *, reason: str, target: int | None = None, char_id: int | None = None, hidden: bool = False,
              detail: dict | None = None, degree: str | None = None) -> RollResult:
        r = self.roller.roll(expr, target=target, reason=reason)
        r.detail = detail or {}
        if degree:
            r.degree = degree
        record_roll(self.conn, self.cid, r, turn_id=self.turn_id, character_id=char_id, hidden=hidden)
        self.rolls.append((r, char_id, hidden))
        return r

    def _char(self, c: dict) -> dict:
        return one(self.conn, "SELECT * FROM characters WHERE id = ?", (c["character_id"],))

    def _event(self, type_: str, summary: str, payload: dict | None = None, visibility: str = "player") -> None:
        Ctx(self.conn, self.cid, self.turn_id).event(type_, summary, payload or {}, visibility)

    # -- lifecycle
    def start(self, req: StartCombat) -> dict:
        if active_encounter(self.conn, self.cid):
            raise ChangeError("a combat is already active; end it first")
        floor = _floor(self.conn, self.cid)
        if req.party:
            chars = [resolve_character(self.conn, self.cid, p) for p in req.party]
        else:
            chars = rows(self.conn, "SELECT * FROM characters WHERE campaign_id = ? AND in_party = 1 AND kind = 'crawler' AND status = 'active' ORDER BY party_order", (self.cid,))
        if not chars:
            raise ChangeError("no crawlers in the fight")
        combatants = []
        for ch in chars:
            _char_slots(ch)  # fail early if the sheet can't take damage
            combatants.append({"id": f"c{ch['id']}", "name": ch["name"], "side": "party", "character_id": ch["id"],
                               "actions_left": 1 if req.surprise == "crawlers" else (0 if req.surprise == "mobs" else 2),
                               "evading": False, "advantage_vs": [], "pending_injury": False, "fired": [], "reload": False})
        n_party = len(combatants)
        for sb in req.foes:
            if sb.health_bar:
                hb = list(sb.health_bar)
            elif sb.slots and sb.per_slot:
                hb = [sb.per_slot] * sb.slots
            else:
                raise ChangeError(f"{sb.name}: give health_bar (slot values) or slots + per_slot from the stat block")
            for i in range(sb.count):
                name = sb.name if sb.count == 1 else f"{sb.name} {i + 1}"
                actions = sb.actions or (n_party if (sb.kind in ("elite", "boss") or sb.quality == "high") else 1)
                combatants.append({"id": f"m{len(combatants)}", "name": name, "side": "foe", "kind": sb.kind,
                                   "slots": list(hb), "max_hb": sum(hb), "evade": sb.evade if sb.evade is not None else 10 + sb.dex_mod + floor,
                                   "dr": sb.dr, "resistances": sb.resistances, "actions": actions, "actions_left": actions,
                                   "attacks": [a.model_dump() for a in sb.attacks], "defeated": False, "notes": sb.notes,
                                   "max_slots_per_hit": sb.max_slots_per_hit})
        phase = "surprise" if req.surprise == "crawlers" else "mob_declaration"
        state = {"round": 1, "phase": phase, "surprise": req.surprise, "setup": req.setup, "floor": floor, "declared": [], "combatants": combatants,
                 "plays": [], "shots": {}, "last_attack": None}
        eid = self.conn.execute("INSERT INTO encounters (campaign_id, name, status, round, summary, combatants_json) VALUES (?,?,?,?,?,?)",
                                (self.cid, req.name, "active", 1, req.setup, json.dumps(state))).lastrowid
        foes = [c["name"] for c in combatants if c["side"] == "foe"]
        self._event("encounter_start", f"Combat begins: {req.name} ({', '.join(foes)})", {"encounter_id": eid, "surprise": req.surprise})
        lead = {"crawlers": "Crawlers have surprise: each crawler gets one free Action before Round 1.",
                "mobs": "Mobs have surprise: crawlers get no Actions until Round 1's Crawler Action Phase.",
                "none": "Round 1 begins with Mob Action Declaration."}[req.surprise]
        return {"summary": f"Combat started: {req.name}. {lead}", "phase": phase, "party": [c["name"] for c in combatants if c["side"] == "party"], "foes": foes}

    def declare(self, attacks: list[dict]) -> dict:
        enc = _require(self.conn, self.cid)
        st = _state(enc)
        if st["phase"] not in ("mob_declaration", "surprise"):
            raise ChangeError(f"Mob Actions are declared at the start of a round (current phase: {st['phase']})")
        st["phase"] = "crawler_reaction"
        st["declared"] = []
        used: dict[str, int] = {}
        for a in attacks:
            mob = _find(st, str(a.get("mob", "")), "foe")
            if mob["defeated"]:
                raise ChangeError(f"{mob['name']} is defeated")
            atk = next((x for x in mob["attacks"] if x["name"].lower() == str(a.get("attack", "")).lower()), None) or (mob["attacks"][0] if len(mob["attacks"]) == 1 and not a.get("attack") else None)
            if not atk:
                raise ChangeError(f"{mob['name']} has no attack {a.get('attack')!r}; options: {[x['name'] for x in mob['attacks']]}")
            used[mob["id"]] = used.get(mob["id"], 0) + 1
            if used[mob["id"]] > mob["actions"]:
                raise ChangeError(f"{mob['name']} only has {mob['actions']} Action(s) per round")
            tgt = _find(st, str(a.get("target", "")), "party")
            st["declared"].append({"mob": mob["id"], "attack": atk["name"], "target": tgt["id"]})
        _save(self.conn, enc, st)
        lines = [f"{_find(st, d['mob'])['name']} → {d['attack']} at {_find(st, d['target'])['name']}" for d in st["declared"]]
        return {"summary": "Mob Actions declared; crawlers may now react (Evade, Intervene, Catcher, Healing, Taunt).", "declared": lines}

    def evade(self, who: str) -> dict:
        enc = _require(self.conn, self.cid)
        st = _state(enc)
        if st["phase"] != "crawler_reaction":
            raise ChangeError("Evade is an Interrupt taken in the Crawler Reaction Phase, after Mob Actions are declared")
        c = _find(st, who, "party")
        if c["evading"]:
            return {"summary": f"{c['name']} is already evading this round"}
        self._spend(c, "Evade")
        c["evading"] = True
        _save(self.conn, enc, st)
        n = sum(1 for d in st["declared"] if d["target"] == c["id"])
        return {"summary": f"{c['name']} spends an Action to Evade ({n} declared Attack{'s' if n != 1 else ''} incoming)", "actions_left": c["actions_left"]}

    def _spend(self, c: dict, what: str) -> None:
        if c["side"] == "party":
            ch = self._char(c)
            if one(self.conn, "SELECT id FROM status_effects WHERE character_id = ? AND name = 'Dying'", (ch["id"],)) or ch["status"] == "dead":
                raise ChangeError(f"{c['name']} cannot act (Dying or dead)")
        if c["actions_left"] <= 0:
            raise ChangeError(f"{c['name']} has no Actions left this round (tried: {what})")
        c["actions_left"] -= 1

    def spend_action(self, who: str, action: str) -> dict:
        enc = _require(self.conn, self.cid)
        st = _state(enc)
        c = _find(st, who)
        self._spend(c, action)
        reloaded = action.strip().lower().startswith("reload") and c.get("reload")
        if reloaded:
            c["reload"] = False
        _save(self.conn, enc, st)
        return {"summary": f"{c['name']} uses an Action: {action}" + (" (handgun reloaded)" if reloaded else ""), "actions_left": c["actions_left"]}

    # -- Interrupts and plays
    def catch(self, catcher: str, ally: str, mob: str | None = None, attack: str | None = None) -> dict:
        enc = _require(self.conn, self.cid)
        st = _state(enc)
        if st["phase"] != "crawler_reaction":
            raise ChangeError("Catcher is an Interrupt taken in the Crawler Reaction Phase, after Mob Actions are declared")
        c, a = _find(st, catcher, "party"), _find(st, ally, "party")
        if c["id"] == a["id"]:
            raise ChangeError("Catcher takes a hit meant for an adjacent ally, not for yourself")
        ch = self._char(c)
        srow = one(self.conn, "SELECT rank FROM skills WHERE character_id = ? AND lower(name) = 'catcher'", (ch["id"],))
        if not srow or not srow["rank"]:
            raise ChangeError(f"{ch['name']} has no Ranks in Catcher")
        hits = [d for d in st["declared"] if d["target"] == a["id"] and not d.get("caught_for")
                and (not mob or _find(st, mob, "foe")["id"] == d["mob"]) and (not attack or d["attack"].lower() == attack.lower())]
        if not hits:
            raise ChangeError(f"no declared Attack at {a['name']} matches; Catcher answers a declared Attack")
        d = hits[0]
        m = _find(st, d["mob"])
        if next(x for x in m["attacks"] if x["name"] == d["attack"]).get("area"):
            raise ChangeError("Catcher only takes direct (non-Area) Attacks")
        self._spend(c, "Catcher")
        d["target"], d["caught_for"] = c["id"], a["id"]
        _save(self.conn, enc, st)
        bonus = 5 * min(3, int(srow["rank"]) // 5)
        return {"summary": f"{c['name']} leaps in to take {m['name']}'s {d['attack']} for {a['name']} (no Evade on it"
                           + (f"; +{bonus} DR from Catcher Rank {srow['rank']}" if bonus else "") + ")", "actions_left": c["actions_left"]}

    def call_play(self, caller: str, ally: str, action: str, target: str | None = None) -> dict:
        enc = _require(self.conn, self.cid)
        st = _state(enc)
        if st["phase"] not in ("crawler_action", "surprise"):
            raise ChangeError(f"Call a Play is an Action in the Crawler Action Phase (current: {st['phase']})")
        c, a = _find(st, caller, "party"), _find(st, ally, "party")
        if c["id"] == a["id"]:
            raise ChangeError("a Called Play proposes an Action for an ally")
        if not action.strip():
            raise ChangeError("the proposal must be specific enough that everyone knows what fulfils it")
        mob = _find(st, target, "foe") if target else None
        self._spend(c, "Call a Play")
        r = self._roll("2d6kh1", reason=f"{c['name']} calls a play for {a['name']}: {action}", char_id=c["character_id"],
                       detail={"kind": "call_a_play", "character": c["name"], "ally": a["name"], "action": action})
        st.setdefault("plays", []).append({"caller": c["id"], "ally": a["id"], "action": action, "target": mob["id"] if mob else None,
                                           "bonus": r.total, "round": st["round"], "used": False})
        _save(self.conn, enc, st)
        return {"summary": f"{c['name']} calls a play: {a['name']} — {action}" + (f" (vs {mob['name']})" if mob else "")
                           + f". Called Play bonus +{r.total}.", "bonus": r.total, "actions_left": c["actions_left"]}

    def intervene(self, who: str) -> dict:
        enc = _require(self.conn, self.cid)
        st = _state(enc)
        last = st.get("last_attack")
        c = _find(st, who, "party")
        if not last or last["round"] != st["round"]:
            raise ChangeError("Intervene answers another crawler's Attack result this round; there is none to change")
        if last["attacker"] == c["id"]:
            raise ChangeError("Intervene helps another crawler, not your own Attack")
        if last["hit"] or last.get("intervened"):
            raise ChangeError("that Attack " + ("already hit" if last["hit"] else "has already been Intervened on"))
        a, mob = _find(st, last["attacker"]), _find(st, last["mob"])
        if mob["defeated"]:
            raise ChangeError(f"{mob['name']} is already defeated")
        self._spend(c, "Intervene")
        r = self._roll("1d6", reason=f"{c['name']} Intervenes in {a['name']}'s Attack", char_id=c["character_id"],
                       detail={"kind": "intervene", "character": c["name"], "ally": a["name"]})
        total = last["total"] + r.total
        degree = degree_of(last["natural"], total, mob["evade"])
        last.update({"total": total, "degree": degree, "intervened": True})
        lines = [f"{c['name']} Intervenes: +{r.total} → {a['name']}'s total {total} vs Evade {mob['evade']} → {degree}"]
        if is_success(degree):
            last["hit"] = True
            lines += self._hit(st, a, self._char(a), mob, degree, last["damage"], last.get("damage_bonus", 0))
        _save(self.conn, enc, st)
        return {"summary": " ".join(lines), "lines": lines, "hit": last["hit"], "actions_left": c["actions_left"]}

    def _hit(self, st: dict, a: dict, ch: dict, mob: dict, degree: str, dmg_expr: str, bonus: int = 0) -> list[str]:
        expr, dtype = parse_damage(dmg_expr)
        if degree == "Critical Hit":
            expr = double_dice(expr)
        dr_ = self._roll(expr, reason=f"{ch['name']} damage to {mob['name']}", char_id=ch["id"],
                         detail={"kind": "damage", "target": mob["name"], "critical": degree == "Critical Hit"})
        floor_bonus = st["floor"] if degree == "Amazing Success" else 0
        final, notes = _mitigate(dr_.total + floor_bonus + bonus, mob["dr"], dtype, mob.get("resistances") or {})
        _, text = damage_mob(mob, final, degree)
        extra = (f" + Floor {floor_bonus}" if floor_bonus else "") + (f" + Called Play {bonus}" if bonus else "")
        if mob["defeated"]:
            self._event("mob_defeated", f"{mob['name']} defeated by {ch['name']}", {"mob": mob["name"]})
        return [f"Damage {expr} = {dr_.total}{extra}{' − ' + ', '.join(notes) if notes else ''} = {final}. {text}"]

    def _ammo(self, ch: dict, skill: str, entry: dict | None, override: str | None) -> dict | None:
        """The item an Attack spends one of (BOOK p. 183: 'Each Attack requires proper ammunition'), or None."""
        text = override or (entry or {}).get("ammunition")
        if not text:
            if skill.lower() in AMMO_SKILLS:
                raise ChangeError(f"{ch['name']}'s {skill} Attack needs ammunition: give `ammunition` (the item it spends)")
            return None
        want = _singular(re.sub(r"\b(shared|supply|the|of)\b", " ", text.lower()))
        for owner_sql, args in (("owner_character_id = ?", (ch["id"],)), ("owner_character_id IS NULL", ())):
            for it in rows(self.conn, f"SELECT * FROM items WHERE campaign_id = ? AND {owner_sql} AND (quantity IS NULL OR quantity > 0)", (self.cid, *args)):
                if _singular(it["name"].lower()) == want:
                    if it["quantity"] is None:
                        raise ChangeError(f"the count of {it['name']} is not established; settle it with set_quantity before firing")
                    return it
        raise ChangeError(f"no {text!r} with a count for {ch['name']} to fire; establish it (add_item/set_quantity) or name `ammunition`")

    def resolve(self) -> dict:
        enc = _require(self.conn, self.cid)
        st = _state(enc)
        if st["phase"] != "crawler_reaction":
            raise ChangeError("declare Mob Actions first (combat_declare_mob_attacks)")
        floor = st["floor"]
        lines = []
        for d in st["declared"]:
            mob, tgt = _find(st, d["mob"]), _find(st, d["target"])
            if mob["defeated"]:
                lines.append(f"{mob['name']} was defeated before resolving {d['attack']}")
                continue
            atk = next(x for x in mob["attacks"] if x["name"] == d["attack"])
            ch = self._char(tgt)
            mult, extra, note = 1.0, 0, "did not Evade"
            catch_dr = 0
            if d.get("caught_for"):
                srow = one(self.conn, "SELECT rank FROM skills WHERE character_id = ? AND lower(name) = 'catcher'", (ch["id"],))
                catch_dr = 5 * min(3, int((srow or {}).get("rank") or 0) // 5)
                note = f"caught for {_find(st, d['caught_for'])['name']} (no Evade" + (f"; +{catch_dr} DR" if catch_dr else "") + ")"
            elif tgt["evading"]:
                bonus = (ch.get("derived") or {}).get("evade")
                bonus = int(bonus) if isinstance(bonus, (int, float)) else _mod(ch, "dexterity")
                r = self._roll(f"1d20+{bonus}", reason=f"Evade {mob['name']}'s {atk['name']}", target=atk["to_hit"], char_id=ch["id"],
                               detail={"kind": "evade", "character": ch["name"], "evade_bonus": bonus, "dc": atk["to_hit"]})
                nat = r.dice[0].value
                r.degree = degree_of(nat, r.total, atk["to_hit"])
                r.outcome = "success" if is_success(r.degree) else "failure"
                r.detail["natural"], r.detail["d20s"] = nat, [nat]
                r.detail["summary"] = f"{ch['name']} — Evade: d20 {nat} + {bonus} = {r.total} vs {atk['to_hit']} → {r.degree}"
                self.conn.execute("UPDATE dice_rolls SET degree = ?, outcome = ?, detail_json = ? WHERE id = ?",
                                  (r.degree, r.outcome, json.dumps(r.detail), r.id))
                if is_success(r.degree):
                    if r.degree in ("Critical Hit", "Amazing Success"):
                        tgt["advantage_vs"].append(mob["id"])
                    if not atk["area"]:
                        lines.append(f"{r.detail['summary']} — avoided" + (f"; Advantage on next Attack vs {mob['name']}" if mob["id"] in tgt["advantage_vs"] else ""))
                        continue
                    mult, note = 0.5, "evaded the Area Attack (half damage)"
                elif r.degree == "Major Fail":
                    extra, note = floor, "Evade Major Fail (+Floor damage, Minor Injury after combat)"
                    tgt["pending_injury"] = True
                elif r.degree == "Critical Fail":
                    mult, note = 2.0, "Evade Critical Fail (double damage)"
                    tgt["pending_injury"] = True
                else:
                    note = f"failed to Evade ({r.degree})"
                lines.append(r.detail["summary"])
            expr, dtype = parse_damage(atk["damage"])
            dr = (ch.get("derived") or {}).get("damage_resistance")
            dr = (int(dr) if isinstance(dr, (int, float)) else 0) + catch_dr
            dmg_roll = self._roll(expr, reason=f"{mob['name']} {atk['name']} damage", char_id=ch["id"],
                                  detail={"kind": "damage", "target": ch["name"]})
            raw = int(dmg_roll.total * mult) + extra
            final, notes = _mitigate(raw, dr, atk.get("damage_type") or dtype, {})
            text = damage_character(self.conn, self.cid, self.turn_id, ch, final, tgt, f"{mob['name']} {atk['name']}")
            lines.append(f"{mob['name']} {atk['name']} → {ch['name']}: {note}; {dmg_roll.total}{f' ×{mult:g}' if mult != 1 else ''}{f' +{extra}' if extra else ''}"
                         f"{' − ' + ', '.join(notes) if notes else ''} = {final} damage. {text}"
                         + (f" Fail effect to apply: {atk['fail_effect']}" if atk.get("fail_effect") and note.startswith("Evade") and "Fail" in note else ""))
        st["phase"] = "crawler_action"
        st["declared"] = []
        _save(self.conn, enc, st)
        self._event("combat_mob_phase", f"Round {st['round']}: Mob Actions resolved", {"lines": lines})
        return {"summary": f"Round {st['round']} Mob Actions resolved; Crawler Action Phase.", "lines": lines}

    def attack(self, attacker: str, target: str, attack: str | None = None, advantage: int = 0, disadvantage: int = 0,
               modifiers: list[dict] | None = None, skill: str | None = None, stat: str | None = None, damage: str | None = None,
               ammunition: str | None = None) -> dict:
        enc = _require(self.conn, self.cid)
        st = _state(enc)
        if st["phase"] not in ("crawler_action", "surprise"):
            raise ChangeError(f"crawler Attacks happen in the Crawler Action Phase (current: {st['phase']}); resolve Mob Actions first")
        a, mob = _find(st, attacker, "party"), _find(st, target, "foe")
        if mob["defeated"]:
            raise ChangeError(f"{mob['name']} is already defeated")
        ch = self._char(a)
        sheet_attacks = (ch.get("sheet") or {}).get("attacks") or {}
        entries = list(sheet_attacks.values()) if isinstance(sheet_attacks, dict) else list(sheet_attacks)
        entry = None
        if attack:
            entry = next((e for e in entries if isinstance(e, dict) and attack.lower() in str(e.get("name", "")).lower()), None)
        skill = skill or (entry or {}).get("skill") or (entry or {}).get("base_skill")
        stat_key = STAT_ALIASES.get(str(stat or (entry or {}).get("governing_stat") or "").lower())
        if not skill or not stat_key:
            raise ChangeError(f"{ch['name']}: name a sheet attack ({[e.get('name') for e in entries if isinstance(e, dict)]}) or give skill + stat + damage")
        srow = one(self.conn, "SELECT rank FROM skills WHERE character_id = ? AND lower(name) = lower(?) AND kind IN ('skill','spell','equipment_skill') ORDER BY rank DESC", (ch["id"], skill))
        rank = int(srow["rank"]) if srow and srow["rank"] else 0
        untrained = rank == 0
        dmg_expr = damage or (entry or {}).get("damage")
        if not dmg_expr:
            raise ChangeError("no damage expression for this attack")
        smod = _mod(ch, stat_key)
        adv = advantage + (1 if mob["id"] in a["advantage_vs"] else 0)
        dis = disadvantage + (1 if untrained else 0) + (1 if (entry or {}).get("attack_roll_condition", "").lower() == "disadvantage" else 0)
        net = adv - dis
        d20 = "2d20kh1" if net > 0 else "2d20kl1" if net < 0 else "1d20"
        extra = sum(int(m.get("value", 0)) for m in modifiers or [])
        sk = skill.lower()
        if sk == "crossbow" and "crossbow" in a.get("fired", []):
            raise ChangeError(f"{ch['name']}'s Crossbow has a cooldown of once per round (BOOK p. 183)")
        if sk == "handgun" and a.get("reload"):
            raise ChangeError(f"{ch['name']} must spend an Action to reload the handgun first (combat_action 'Reload')")
        ammo = self._ammo(ch, skill, entry, ammunition)
        self._spend(a, "Attack")
        if ammo:
            apply_change(self.conn, self.cid, self.turn_id, {"op": "remove_item", "item_id": ammo["id"], "name": ammo["name"],
                                                              "character": ch["name"] if ammo["owner_character_id"] else "shared",
                                                              "quantity": 1, "cause": f"fired at {mob['name']}"})
            st.setdefault("shots", {})[ammo["name"]] = st.get("shots", {}).get(ammo["name"], 0) + 1
        a.setdefault("fired", []).append(sk)
        if mob["id"] in a["advantage_vs"]:
            a["advantage_vs"].remove(mob["id"])
        r = self._roll(f"{d20}+{rank}+{smod}" + (f"{extra:+d}" if extra else ""), reason=f"{ch['name']} attacks {mob['name']} ({entry.get('name') if entry else skill})",
                       target=mob["evade"], char_id=ch["id"],
                       detail={"kind": "attack", "character": ch["name"], "skill": skill, "rank": rank, "stat": stat_key, "stat_mod": smod,
                               "modifiers": modifiers or [], "help": [], "untrained": untrained, "dc": mob["evade"],
                               "advantage": "advantage" if net > 0 else "disadvantage" if net < 0 else "none"})
        nat = next(d.value for d in r.dice if d.sides == 20 and d.kept)
        r.degree = degree_of(nat, r.total, mob["evade"])
        play = next((p for p in st.get("plays", []) if not p["used"] and p["ally"] == a["id"] and p["target"] in (None, mob["id"])), None)
        play_line, play_damage = None, 0
        if play:
            play["used"] = True
            caller = _find(st, play["caller"])["name"]
            if is_success(r.degree):
                play_damage = play["bonus"]
                play_line = f"{caller}'s Called Play: the Attack hits without it, so +{play['bonus']} damage"
            else:
                boosted = degree_of(nat, r.total + play["bonus"], mob["evade"])
                play_line = f"{caller}'s Called Play +{play['bonus']}: {r.total + play['bonus']} vs Evade {mob['evade']} → {boosted}"
                r.total += play["bonus"]
                r.degree = boosted
        r.outcome = "success" if is_success(r.degree) else "failure"
        r.detail.update({"natural": nat, "d20s": [d.value for d in r.dice if d.sides == 20],
                         "summary": f"{ch['name']} — {skill}: d20 {nat} + Rank {rank} + {stat_key[:3].upper()} {smod:+d}{f' {extra:+d}' if extra else ''} = {r.total} vs Evade {mob['evade']} → {r.degree}"})
        self.conn.execute("UPDATE dice_rolls SET degree = ?, outcome = ?, detail_json = ? WHERE id = ?", (r.degree, r.outcome, json.dumps(r.detail), r.id))
        marked = False
        if srow and rank > 0:
            marked = bool(self.conn.execute("UPDATE skills SET marked = 1 WHERE character_id = ? AND lower(name) = lower(?) AND kind = 'skill' AND marked = 0 AND passive = 0",
                                            (ch["id"], skill)).rowcount)
        lines = [r.detail["summary"]] + ([play_line] if play_line else [])
        if ammo:
            lines.append(f"Spent 1 {ammo['name']} ({ammo['quantity'] - 1} left)")
        st["last_attack"] = {"attacker": a["id"], "mob": mob["id"], "round": st["round"], "natural": nat, "total": r.total, "degree": r.degree,
                             "hit": is_success(r.degree), "damage": dmg_expr, "damage_bonus": play_damage}
        if sk == "handgun" and r.degree in ("Major Fail", "Critical Fail"):
            a["reload"] = True
        if is_success(r.degree):
            lines += self._hit(st, a, ch, mob, r.degree, dmg_expr, play_damage)
        else:
            lines.append({"Near Miss": "Near Miss — an ally may Intervene (+1d6) with an Action.",
                          "Major Fail": "Major Fail — apply a complication (dropped weapon, lost footing, ammunition...).",
                          "Critical Fail": "Critical Fail — worst plausible result; consider hitting the wrong target."}.get(r.degree, "Miss."))
        _save(self.conn, enc, st)
        foes_left = [c["name"] for c in st["combatants"] if c["side"] == "foe" and not c["defeated"]]
        return {"summary": " ".join(lines), "lines": lines, "hit": is_success(r.degree), "skill_marked": marked,
                "actions_left": a["actions_left"], "foes_remaining": foes_left}

    def damage(self, target: str, amount: int | None = None, expression: str | None = None, damage_type: str | None = None,
               bypass_dr: bool = False, cause: str = "") -> dict:
        enc = _require(self.conn, self.cid)
        st = _state(enc)
        c = _find(st, target)
        if expression:
            amount = self._roll(expression, reason=f"Damage to {c['name']}: {cause}", detail={"kind": "damage", "target": c["name"]}).total
        if amount is None:
            raise ChangeError("give amount or expression")
        if c["side"] == "foe":
            final, notes = _mitigate(amount, c["dr"], damage_type, c.get("resistances") or {}, bypass_dr)
            _, text = damage_mob(c, final)
            if c["defeated"]:
                self._event("mob_defeated", f"{c['name']} defeated ({cause})", {"mob": c["name"]})
        else:
            ch = self._char(c)
            dr = (ch.get("derived") or {}).get("damage_resistance")
            final, notes = _mitigate(amount, int(dr) if isinstance(dr, (int, float)) else 0, damage_type, {}, bypass_dr)
            text = damage_character(self.conn, self.cid, self.turn_id, ch, final, c, cause)
        _save(self.conn, enc, st)
        return {"summary": f"{amount}{' − ' + ', '.join(notes) if notes else ''} = {final}. {text}"}

    def heal(self, target: str, slots: int, cause: str = "") -> dict:
        enc = _require(self.conn, self.cid)
        st = _state(enc)
        c = _find(st, target)
        if c["side"] == "foe":
            raise ChangeError("Mob healing is narrative; adjust its slots with the GM's judgment")
        return {"summary": heal_character_slots(self.conn, self.cid, self.turn_id, self._char(c), slots, cause)}

    def end_round(self) -> dict:
        enc = _require(self.conn, self.cid)
        st = _state(enc)
        lines = []
        for c in st["combatants"]:
            if c["side"] == "party":
                ch = self._char(c)
                dying = one(self.conn, "SELECT * FROM status_effects WHERE character_id = ? AND name = 'Dying'", (ch["id"],))
                if dying:
                    lines.append(_set_dying(Ctx(self.conn, self.cid, self.turn_id), ch, _dying_left(dying) - 1, "Clean Up"))
                c["actions_left"], c["evading"], c["fired"] = 2, False, []
            else:
                c["actions_left"] = c["actions"]
        lapsed = [p for p in st.get("plays", []) if not p["used"] and p["round"] < st["round"]]
        st["plays"] = [p for p in st.get("plays", []) if not p["used"] and p["round"] >= st["round"]]
        if lapsed:
            lines.append("Called Play lapsed: " + "; ".join(f"{_find(st, p['ally'])['name']} — {p['action']}" for p in lapsed))
        st["last_attack"] = None
        st["round"] += 1
        st["phase"], st["declared"] = "mob_declaration", []
        foes_left = [c["name"] for c in st["combatants"] if c["side"] == "foe" and not c["defeated"]]
        _save(self.conn, enc, st)
        lines.append(f"Round {st['round']} begins." + ("" if foes_left else " No foes remain — end the combat."))
        return {"summary": " ".join(lines), "lines": lines, "round": st["round"], "foes_remaining": foes_left}

    def end(self, outcome: str) -> dict:
        enc = _require(self.conn, self.cid)
        st = _state(enc)
        ctx = Ctx(self.conn, self.cid, self.turn_id)
        lines = []
        for c in st["combatants"]:
            if c["side"] == "party" and c.get("pending_injury"):
                self.conn.execute("INSERT INTO status_effects (character_id, name, description, polarity, source) VALUES (?,?,?,?,?)",
                                  (c["character_id"], "Minor Injury", "From a badly failed Evade; removed by a Short Rest", "debuff", "combat"))
                lines.append(f"{c['name']} gains a Minor Injury now that the adrenaline wears off")
                ctx.event("status_added", f"{c['name']} gains status: Minor Injury", {"character_id": c["character_id"]})
        if st.get("shots"):
            lines.append("Ammunition fired: " + ", ".join(f"{n}x {k}" for k, n in st["shots"].items())
                         + ". Recovery depends on impact, access and time (CORE 12.3): roll or rule it, then add_item what is found.")
        st["phase"] = "ended"
        self.conn.execute("UPDATE encounters SET status = 'resolved', combatants_json = ?, summary = COALESCE(summary, '') || ? WHERE id = ?",
                          (json.dumps(st), f"\nOutcome: {outcome}", enc["id"]))
        defeated = [c["name"] for c in st["combatants"] if c["side"] == "foe" and c["defeated"]]
        ctx.event("encounter_end", f"Combat ends: {enc['name']} — {outcome}", {"defeated": defeated, "rounds": st["round"]})
        return {"summary": f"Combat over after {st['round']} round(s): {outcome}", "lines": lines, "defeated": defeated,
                "ammunition_fired": st.get("shots") or {}}


def public_encounter(enc: dict | None, conn: sqlite3.Connection) -> dict | None:
    """Player-safe view: no stat blocks, Evade values, DR, attacks or notes; foes show Health Bar % only."""
    if not enc:
        return None
    st = enc.get("combatants")
    out = {"id": enc["id"], "name": enc["name"], "round": enc["round"], "summary": enc.get("summary")}
    if not isinstance(st, dict) or "combatants" not in st:
        out["combatants"] = []
        return out
    out["phase"] = st.get("phase")
    out["combatants"] = []
    for c in st["combatants"]:
        if c["side"] == "foe":
            out["combatants"].append({"id": c["id"], "name": c["name"], "side": "foe", "defeated": c["defeated"],
                                      "hb_percent": round(100 * sum(c["slots"]) / c["max_hb"]) if c["max_hb"] else 0})
        else:
            ch = one(conn, "SELECT hp_current, hp_max, status FROM characters WHERE id = ?", (c["character_id"],))
            dying = one(conn, "SELECT description FROM status_effects WHERE character_id = ? AND name = 'Dying'", (c["character_id"],))
            out["combatants"].append({"id": c["id"], "name": c["name"], "side": "party", "actions_left": c["actions_left"], "evading": c["evading"],
                                      "hp_current": ch["hp_current"], "hp_max": ch["hp_max"], "dead": ch["status"] == "dead",
                                      "dying": dying["description"] if dying else None})
    return out
