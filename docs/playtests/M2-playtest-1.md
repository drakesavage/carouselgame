# M2 playtest 1: transcript vs state (2026-10-25)

**Setup.** A disposable copy of the live campaign (fresh verified backup restored into a scratch folder, a second server
on port 8790, its ledger link repointed to a scratch clone of the ledger; the live campaign and the ledger repository
were never touched). GM: auto routing (Gemini for questions and talk-only turns, Codex for mechanics). Every turn was
submitted through the real turn API; the state was snapshotted before and after each turn and compared with the
narration, the GM's integrity flags and the consistency check (`app/consistency.py`).

**Run.** 47 turns (33 scripted, 14 played adaptively to reach a fight once the scripted hunts found no Mobs): 46
completed, 1 failed safely. 44 turns went to Codex, 3 to Gemini; about 32 minutes of GM time; 28 rolls.

**Scenarios covered.** Status checks, shop visit and purchase, negotiation, equipment transfer and equipping, a
reservation pickup by the wrong person, paid training, a party split (three groups), a skill sweep, off-screen contact
with the recon team, travel, two Mob hunts (no Mobs rolled), a short and a long rest, crafting the Gold Siphon, setting
a trap, a recap, a save request, and a combat (surprise, two Brain Boilers, crossbow, pistol, bow, Call a Play, an
Intervene attempt, ammunition recovery).

## Exit gate: not met

M2's gate is "no unexplained change to currency, time, cycle, position, inventories or HP across the playtest".

| Kind | Start → end | Explained? |
|---|---|---|
| Gold | 4,016 → 3,896 | yes: 70 armor for Nate, 50 training |
| Misc. Junk | 66 → 58 | yes: the Siphon build (plus the Gold Siphon Assembly consumed) |
| Ammunition | bolts 64 → 62, pistol rounds 22 → 20, arrow cache 15 → 13 | yes: one per shot |
| Inventory | armor to Nate (equipped), bolas Marcus → Priya, Siphon built | yes |
| HP | unchanged (no one was hit) | yes |
| **Time** | 1,519 → 3,225 minutes | **no: the long rest counted 8 hours twice (H1)** |
| **Advancement cycles** | 19 counted, one checkpoint (+1 Level to all twelve) | **no: inflated (H2)** |
| **Positions** | — | **no: campaign vs group and group vs members drift (H4, H5)** |

## Issues

Severity: high = canonical state drifts or invented facts; medium = a rule bypassed or a tool that misleads the GM;
low = friction or cost. Each has a reproduction from this run.

### High

- **H1. A long rest advanced the clock twice** (turn 30). The GM called `advance_time` 480 and the `rest` op, which also
  advances 480. Narration said 195h remained; two turns later the GM corrected itself to 187h, but the clock kept the
  extra 8 hours. *Fix:* the rest op's time must be the only time for the rest (refuse or merge a separate advance for
  the same rest in one turn), and say so in the tool description.
- **H2. Advancement cycles are inflated.** 12 cycles in the first 17 turns triggered the checkpoint and +1 Level for all
  twelve crawlers (turn 17); 19 in total. Counted: lending the bolas (6), walking back (13), entering the saferoom (17),
  ending a hunt that found nothing (24), the long rest (30), and every instruction inside one fight (43-46). CORE 14.3
  excludes bookkeeping, repetition and "multiple rolls or Actions that merely resolve the same player decision".
  *Fix:* `count_cycle` should refuse a second cycle inside one combat and on travel/rest/bookkeeping turns, and the
  integrity check should flag the rest; the owner decides whether the turn-17 Level stands in the real campaign
  (it was only on the copy).
- **H3. The Gemini talk path invents off-screen outcomes.** "Message the recon team" (turn 11) and "debrief them when
  they arrive" (27) were routed as conversation; Gemini narrated a full scouting report and the team's return with no
  off-screen Check (CORE 15.2) and no records. Turn 41 (also Gemini) recorded the team's holding position as a
  canonical fact. Codex later retracted the invented return (turn 28) and refused to act on the invented Brain Boiler
  sighting. *Fix:* route contact with or reports from an off-screen group to the mechanics GM, and keep the talk path
  from recording facts or narrating what absent characters did.
- **H4. The campaign location and the player character's group drift apart** (turns 3-6, 13): `set_location` moved the
  campaign to Secondhand Saviors while the group stayed at Gilded Feathers; later the group set off en route while the
  campaign stayed at Deathstroke Hall. *Fix:* one source of truth: moving the campaign moves the player character's
  group, and moving that group moves the campaign.
- **H5. Updating a group's location does not move its members** (turn 42): the recon group was placed at the masonry
  yard but its six members stayed "unknown", so six consistency checks failed for the rest of the run. *Fix:* the group
  op sets members' location whenever it changes the group's (the M1 fix covered joining a group, not moving one).

### Medium

- **M1. Erin's arrows: the owner's decision was bypassed** (turn 44). Her sheet's "Arrow supply" has no count; the GM
  passed the shared 15-arrow cache as `ammunition`, and the engine spent from it. *Fix:* the owner rules on Erin's arrows;
  the attack tool should refuse an item that does not match the sheet's supply unless a ruling is recorded.
- **M2. Intervene only reaches the latest Attack** (turn 44): the GM resolved Erin's shot before Maya's Intervene on
  Cliff's Near Miss, and the tool then refused it. *Fix:* let Intervene name any missed Attack this round that has not
  been intervened on.
- **M3. The Called Play roll record is inconsistent** (turn 46): Terri's roll is logged as total 16 vs 17 with degree
  Success; the +4 bonus changed the degree but the logged total kept the pre-bonus value. *Fix:* log the boosted total.
- **M4. Scene-op friction**: six rejected batches because the GM sent `action: "set"` or `"end"` (accepted: start,
  update, close), costing tool rounds. *Fix:* accept the obvious aliases.
- **M5. A turn failed at the tool-round limit** (turn 47, rolled back cleanly): during ammunition recovery the GM spent
  its rounds settling the turn-17 checkpoint's unprocessed Level-up benefits, then one invalid field (`remember`
  importance 4, max 3) rejected the final batch. *Fix:* clamp out-of-range fields instead of rejecting the batch, and make
  a resolved checkpoint list its pending Level-up work explicitly.
- **M6. "Let's save here" made no save point** (turn 33): the GM has no save tool and only narrated a bookmark
  (autosaves still ran). *Fix:* a save tool, or the router answering saves itself.
- **M7. Paid training was resolved as grinding** (turn 8): two hours at Deathstroke Hall became Negotiation grind hours
  and Level-grind progress, under a ruling the GM recorded itself. *Fix:* the owner decides how paid training works.

### Low

- **L1.** A turn can be "completed" a moment before the GM lock is released, so an immediate next submit gets 409.
- **L2.** Status checks were routed to Codex because they mention "Gold" (cost, not correctness).
- **L3.** Price quotes raise "Gold mentioned but not changed" flags (noise).

## What held

- The reservation rule: nobody but Scout may collect the Personal Stabber (turns 5, 31).
- Purchases, equipping and transfers matched the narration exactly; every Gold change was explained.
- Crafting went through the `craft` tool: 8 Misc. Junk plus the Gold Siphon Assembly, 8 hours, Amazing Success, project
  completed; a follow-up delivery commitment was recorded.
- The GM refused to arm the Porcupine Pod without a trap record and asked for one (turn 26).
- Codex never invented a Mob, a target or an encounter; two hunts honestly found nothing; it would not join the recon
  team until it was located (turns 34-40).
- Combat: surprise, party Stealth Checks, one round of ammunition per shot, a Called Play turning a miss into a hit,
  combat end with the recovery prompt.
- A failed turn rolled back with its dice kept in the audit (turn 47).
- Codex corrected two of its own and Gemini's errors in later turns (the time misstatement, the invented return).
