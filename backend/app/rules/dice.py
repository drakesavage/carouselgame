"""Dice service. All randomness comes from the OS CSPRNG (secrets.randbelow); every roll is recorded.

Supported expressions (case-insensitive, whitespace ignored):
    d20, 2d6, 3d6+2, 1d8-1, 4d6kh3 (keep highest 3), 2d20kl1 (keep lowest 1), 2d6+1d4+3
"""
from __future__ import annotations

import json
import re
import secrets
import sqlite3
from dataclasses import asdict, dataclass, field
from typing import Callable

from ..db import now_iso

RNG_NAME = "secrets.randbelow (OS CSPRNG)"
MAX_DICE = 100
MAX_SIDES = 1000

_TERM = re.compile(r"([+-])?(?:(\d*)d(\d+)(?:(kh|kl)(\d+))?|(\d+))")


class DiceError(ValueError):
    pass


@dataclass
class Die:
    sides: int
    value: int
    kept: bool = True


@dataclass
class RollResult:
    expression: str
    dice: list[Die]
    modifier: int
    total: int
    target: int | None = None
    outcome: str | None = None
    reason: str | None = None
    id: int | None = None
    hidden: bool = False
    degree: str | None = None
    detail: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = asdict(self)
        return d


def csprng_die(sides: int) -> int:
    return secrets.randbelow(sides) + 1


@dataclass
class DiceRoller:
    """Rolls dice. `source` is injectable only so tests can verify arithmetic; production always uses csprng_die."""

    source: Callable[[int], int] = field(default=csprng_die)

    def roll(self, expression: str, target: int | None = None, reason: str | None = None) -> RollResult:
        if re.search(r"[\dd]\s+\d", expression.lower()):
            raise DiceError(f"ambiguous whitespace in dice expression {expression!r}")
        expr = expression.replace(" ", "").lower()
        if not expr:
            raise DiceError("empty dice expression")
        pos = 0
        dice: list[Die] = []
        modifier = 0
        total = 0
        count_dice = 0
        while pos < len(expr):
            m = _TERM.match(expr, pos)
            if not m or m.end() == pos:
                raise DiceError(f"cannot parse dice expression {expression!r} at {expr[pos:]!r}")
            if pos > 0 and not m.group(1):
                raise DiceError(f"missing +/- between terms in {expression!r}")
            sign = -1 if m.group(1) == "-" else 1
            if m.group(6) is not None:
                value = int(m.group(6))
                modifier += sign * value
                total += sign * value
            else:
                n = int(m.group(2) or 1)
                sides = int(m.group(3))
                if n < 1 or sides < 2:
                    raise DiceError("need at least one die with two or more sides")
                count_dice += n
                if count_dice > MAX_DICE or sides > MAX_SIDES:
                    raise DiceError("dice expression too large")
                group = [Die(sides, self.source(sides)) for _ in range(n)]
                if m.group(4):
                    keep = int(m.group(5))
                    if keep < 1 or keep > n:
                        raise DiceError("keep count must be between 1 and number of dice")
                    order = sorted(range(n), key=lambda i: group[i].value, reverse=m.group(4) == "kh")
                    for i in order[keep:]:
                        group[i].kept = False
                for d in group:
                    if not 1 <= d.value <= sides:
                        raise DiceError("die source returned out-of-range value")
                total += sign * sum(d.value for d in group if d.kept)
                dice.extend(group)
            pos = m.end()
        if not dice:
            raise DiceError("expression contains no dice")
        outcome = None
        if target is not None:
            outcome = "success" if total >= target else "failure"
        return RollResult(expression=expression.strip(), dice=dice, modifier=modifier, total=total,
                          target=target, outcome=outcome, reason=reason)


def record_roll(conn: sqlite3.Connection, campaign_id: int, result: RollResult, *, turn_id: int | None,
                character_id: int | None = None, hidden: bool = False, rng: str = RNG_NAME) -> RollResult:
    """Persist a roll to the audit log. Call with an autocommit connection so the record survives a failed turn."""
    cur = conn.execute(
        """INSERT INTO dice_rolls (campaign_id, turn_id, character_id, expression, dice_json, modifier, total,
                                   target, outcome, reason, degree, detail_json, hidden, rng, created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (campaign_id, turn_id, character_id, result.expression, json.dumps([asdict(d) for d in result.dice]),
         result.modifier, result.total, result.target, result.outcome, result.reason, result.degree,
         json.dumps(result.detail), int(hidden), rng, now_iso()),
    )
    result.id = cur.lastrowid
    result.hidden = hidden
    return result
