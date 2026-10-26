# M2 playtest 2: after the M2.1 integrity fixes (2026-10-25)

**Setup.** The same baseline as playtest 1 (the backup taken before it), restored into a fresh scratch folder, served
on its own port with its ledger link repointed to a scratch clone; the live campaign and the ledger repository were not
touched. GM: auto routing. 18 scripted turns repeating playtest 1's scenarios, then 12 adaptive turns played as Cliff
with deliberately inventive, non-linear plans (as the campaign's history does), plus a save/load check and two server
restarts. Codex hit the ChatGPT plan's usage limit mid-run (19:27 to 19:43); those turns failed cleanly, were rolled
back, and were replayed after the limit lifted.

**Run.** 30 meaningful turns recorded (29 in the log plus the save/load check): 28 completed, 1 failed (a Codex
timeout, rolled back). 27 went to Codex, 2 to Gemini (status questions).

## Exit gate: met for state integrity

| Kind | Start → end | Explained by |
|---|---|---|
| Game time | 1,519 → 3,506 minutes | the recorded time events sum to exactly 1,987; the long rest counted 480 once |
| Gold | 4,016 → 3,906 | 70 armor for Nate, 40 Negotiation training |
| Misc. Junk | 66 → 54 | 8 Siphon build, 4 signal kite |
| Ammunition | pistol rounds 22 → 21; bolts 64 | Terri's provocation shot |
| Items | armor to Nate (equipped), bolas to Priya, Siphon built (Assembly consumed), signal kite built | yes |
| HP / Mana | unchanged | no one was hit |
| Positions | campaign, groups and members agreed after every turn | 0 consistency failures in 29 turns |
| Advancement | 4 → 12 cycles (8 counted), one checkpoint | each counted turn had a consequential resolution; none on status, travel, rest or bookkeeping turns |
| Levels | every party crawler +1; marked Skills rolled (Animal Handling, Cheese Rolling, Fabricate up; Kite Fighting not) | the one checkpoint, resolved at the saferoom, with Level-up benefits left as an explicit task |

**No fabricated canonical outcomes.** Contact with the recon team went to the mechanics GM (H3); asked for their
report, the GM said the records hold no observations and offered to run the reconnaissance instead of inventing one.
**Save/load:** restoring "Before the Siphon build" reproduced the state recorded after that turn exactly; undoing the
restore returned the state exactly. **Restart:** the server was restarted twice mid-run; state was unchanged each time.

## The fixes, as seen in play

- **H1** the long rest advanced 8 hours, once.
- **H2** no cycle on status, travel, rest, transfers or the save; 8 cycles in 29 turns (playtest 1: 12 in 17).
- **H3** "message the recon team" and "debrief them" went to Codex; no invented reports.
- **H4/H5** every move kept the campaign, the player character's group and its members together.
- **M6** "Let's save here" created the manual save point "Before the Siphon build".
- **M5** the checkpoint left an open "Level-up benefits" project instead of settling it inside an unrelated turn.

## Homebrew under pressure (the adaptive stretch)

The GM ruled on unscripted plans with the right tools and real costs, and never granted success for cleverness alone:
a signal kite built with the `craft` tool (4 Junk, 4 hours, a Check) and launched from the pickup's reel (Kite
Fighting); a mirror-camera idea questioned on its physics before anything was spent; a barrel rolled at the gate with
Maya's Cheese Rolling under a recorded ONE-SCENE ruling, the prison's response rolled on a table; a low kite pass that
failed forward (the kite snagged on the wall); Ghost, who is not sapient, failing to follow Scout's cues; Nate stopping
a shot that would have cut the kite loose; one pistol round spent on a provocation. The prison never came out, so
combat interrupts were not exercised live in this run (they were in playtest 1, and regression tests cover them).

## Remaining issues

- **Codex availability (operational).** The ChatGPT plan's usage limit stopped Codex for 16 minutes, and one turn timed
  out at 240 s; both failed cleanly. The usage limit is now reported plainly instead of "exited with code 1: no output".
- **Scene friction (low).** Twice the GM started a scene without closing the previous one; the batch was rejected and
  retried, costing a round.
- **Presence heuristic (low).** The new "absent character speaking" flag fired for Erin on an overwatch rooftop in voice
  contact with Cliff (turn 27): a nearby group in the same scene counts as absent. It is a warning, not a block.
- **Judgment calls to watch.** Holding position by the beacon counted a cycle because the GM recorded the rendezvous as
  a fact; the recon team's "Kite spotted" reply was given without an off-screen roll (defensible: no roll for the routine).
- **Owner rulings still pending:** Erin's arrow supply (the engine now refuses to swap in the shared cache), paid
  training (resolved here as grinding hours under the GM's ONE-SCENE ruling), and the party's trap records.
