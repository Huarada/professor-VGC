# Full-information BO3 corpus

The corpus scores blind inference against real Showdown BO3 series where the native
spreads and natures of **both** teams are known. `python -m scripts.bo3_truth_corpus
--out .cache/truth-corpus.json` runs every series it finds and pools the results.

## What it measures

| Metric | Meaning | Expected |
|---|---|---|
| Soundness | The true point is inside the reported interval and not in an excluded hole; the true nature is still a candidate | 100%. Any miss is a bug and exits 1 |
| Narrowed stats | Share of Pokemon stats whose certified bound removes at least one of the 33 points | Higher is better |
| Mean range removed | Average fraction of the 33 points removed per stat (declarations alone remove 0) | Higher is better |
| Fragile among narrowed | Narrowed stats that rest on a single observation | Lower is better |
| Per participant | The same metrics per player | Similar for both sides |

Soundness alone is not a quality signal: reporting 0-32 everywhere is also 100% sound.
Read it together with tightness. Compare algorithms with `--search legacy`.

Result on the recorded series (2709311277, 72 Pokemon stats, final checkpoint):

| | Propagation (ADR-068) | Legacy (ADR-062) |
|---|---|---|
| Sound | 72/72 (95% CI 94.9-100%) | 72/72 |
| Narrowed stats | 21 (29.2%) | 1 (1.4%) |
| Mean range removed | 11.5% | 0.3% |
| Narrowed stats that are single-observation | 21 of 21 | 0 of 1 |
| Soundness, player vs opponent | 36/36 and 36/36 | 36/36 and 36/36 |

Every narrowed stat depends on one hit, so one mis-modeled hit would break it. That is the
main weakness to attack next; more series are the way to see it fail or hold.

## Simulated series (scale)

`tools/bo3_sim` plays BO3 series with the official Showdown simulator (`pokemon-showdown` 0.11.11,
mod `champions`, Reg M-B Bo3 format) and writes the same `bo3-public` / `bo3-truth` pairs. Both teams
are legal, random and fully known: six random species, three attacks plus one support move each,
random nature and a random legal spread (0-32 per stat, 66 total). Spectator logs carry the open team
sheets and never the spreads. The Reg M-C format of the recorded series is not in that package; M-B uses
the same engine.

```powershell
npm --prefix tools/bo3_sim install
node tools/bo3_sim/generate.js --count 40 --seed 100 --out .cache/sim-corpus-100
.venv-windows\Scripts\python.exe -m scripts.bo3_truth_corpus --dir .cache/sim-corpus-100 --out .cache/sim-40.json
```

The same seed reproduces the same series (timestamps are fixed). Generating 40 series takes about 80 s
and scoring them about 12 minutes per algorithm.

Result on 40 simulated series (2,880 Pokemon stats, final checkpoint, seed 100):

| | Propagation (ADR-068) | Legacy (ADR-062) |
|---|---|---|
| Sound | 2880/2880 (95% CI 99.9-100%) | 2880/2880 |
| Narrowed stats | 250 (8.7%) | 38 (1.3%) |
| Soundness of the narrowed stats | 250/250 (98.5-100%) | 38/38 (90.8-100%) |
| Mean range removed | 2.3% | 0.1% |
| Narrowed stats that rest on one hit | 236 of 250 (94%) | 0 of 38 |
| Narrowed stats, SimAlpha / SimBeta | 124 / 126 | 25 / 13 |
| Series with at least one narrowed stat | 34 of 40 | 30 of 40 |

Random spreads narrow far less than the recorded series (2.3% of the range against 11.5%): that series
used extreme spreads such as 32/32/2, which damage can bound more easily. Read the 11.5% as the
best case, not the typical one. HP and Atk/Def are narrowed most; Speed almost never (damage does not
depend on it).

Two limits of this source: the bots pick moves at random, and the damage code is the simulator's own,
so it checks the inference against the game's mechanics, not against human play. Use the recorded series
as the realism check.

### 160 simulated series (seed 200)

Scaling the same generator to 160 series (11,520 Pokemon stats, final checkpoint, propagation search)
tightens the interval on soundness of the narrowed claims, as the 40-series run predicted:

| | 160 series |
|---|---|
| Sound | 11,520/11,520 (95% CI 100-100%) |
| Narrowed stats | 840 (7.3%, CI 6.8-7.8%) |
| Soundness of the narrowed stats | 840/840 (95% CI 99.5-100%) |
| Mean range removed | 1.9% |
| Narrowed stats that rest on one hit | 813 of 840 (96.8%) |
| Nature misses, errors | 0, 0 |

| Stat | Narrowed | Mean range removed |
|---|---|---|
| HP | 19.2% | 4.6% |
| Def | 8.4% | 2.3% |
| Atk | 6.4% | 1.8% |
| SpD | 5.3% | 1.5% |
| SpA | 4.2% | 1.1% |
| Spe | 0.3% (5 of 1,920) | 0.1% |

The first pass at this size aborted four series and reported one unsound stat. All five were parser bugs,
not inference bugs: Skill Swap / Wandering Spirit gave the swapped ability to the wrong Pokemon, Pickpocket
and Magician credited the victim instead of the thief, and a berry flung at a target counted as the
target's item. They are fixed with tests (`tests/test_ability_reveal_holder.py`), and the affected series
re-scored sound. Read the 100% as "no false claim", not as precision: the engine rarely narrows, and Speed
almost never.

### Simulated series with real team sheets

`--teams` makes the generator use the open team sheets of real public series instead of random teams. It
reads every `bo3-public-showteam-*.json` in a folder, keeps species, item, ability, moves and nature, and
draws only the hidden spreads, `--variants N` times per series:

```powershell
node tools/bo3_sim/generate.js --teams tests/fixtures/series --variants 5 --spreads mixed --seed 700 --out .cache/sim-real-teams
.venv-windows\Scripts\python.exe -m scripts.bo3_truth_corpus --dir .cache/sim-real-teams --out .cache/sim-real-teams-score.json
```

`--spreads` is `random` (the concentrated random draw above), `meta` (fast attacker, bulky attacker or
bulky body, 66 points exactly; Speed 0 for Trick Room users and Speed-lowering natures) or `mixed`
(half each, the default).

Reg M-C is not in the `pokemon-showdown` package (0.11.11 ships M-A and M-B). The generator registers
`gen9championsvgc2026regmcbo3` at run time: the same `champions` mod and rules as M-B with the legality
filter (`Obtainable`) lifted, so the M-C rosters validate (Rillaboom, Indeedee, Pawmot, the new Megas).
Logs carry the real M-C format name. The M-C banlist is not applied, and any M-C data change the package
does not ship is not reproduced. Without `--teams` the default stays M-B.

Result on 110 series (22 real teams x 5 spread draws, seed 700, Reg M-C format, 7,920 Pokemon stats), generated
with the Showdown master overlay below:

| | 110 series, real teams |
|---|---|
| Sound | 7,920/7,920 (95% CI 100-100%) |
| Narrowed stats | 663 (8.4%, CI 7.8-9.0%) |
| Soundness of the narrowed stats | 663/663 (95% CI 99.4-100%) |
| Mean range removed | 2.9% |
| Narrowed stats that rest on one hit | 634 of 663 (95.6%) |
| Nature misses, errors | 0, 0 |

HP is narrowed most (21.7%), then Def (10.2%), SpD (7.2%), Atk (6.1%), SpA (4.4%) and Speed (0.7%).

A first run of the same series with the unpatched package data scored 7,918 of 7,920 sound and 666 of 668 narrowed
claims sound.

The two unsound stats of that first run are both Attack (series sim700x108 Pawmot, sim700x109 Golisopod-Mega,
true point 32 reported as 0-8 and 0-12). Each rests on one hit where the engine predicts more damage than the
simulator dealt. Both were stale simulator data, not engine or search defects:

- **Pawmot, Double Shock.** Champions flags Double Shock as a punching move, so Iron Fist multiplies it by 1.2.
  `@smogon/calc` has this; the package version of the simulator does not (76-91 damage against 93-109 for the
  same spread). The live Showdown master sets `punch` on the move in `data/mods/champions/moves.ts`.
- **Golisopod-Mega, Leech Life.** The package gives Golisopod-Mega a placeholder ability (Emergency Exit); the
  live data and the calculator use Tough Claws (x1.3), the same factor as the damage gap (82-97 against 105-124).

**Reference data.** `pokemon-showdown` 0.11.11 is the newest npm release but trails the Showdown master. Compared
with the master, the package differs in only three Champions moves (Double Shock punch flag, Slash 80, Meteor
Assault 170) and in the abilities of five Mega formes (Absol-Mega-Z, Garchomp-Mega-Z, Lucario-Mega-Z,
Golisopod-Mega, Baxcalibur-Mega). `tools/bo3_sim/master-overlay.json` holds exactly those differences and the
generator applies them before building any set; it records its source and fetch date. The calculator agrees
with the master on every one of them. Real-replay evidence agrees too: Garchomp-Mega-Z in replay 2702319768
takes sandstorm damage, which Sand Force (the package's value) would prevent. Remaining calculator differences
from the master Champions data are Metal Claw (contact flag), Growth (type) and a few moves missing from the
calculator catalog (Anchor Shot, Astral Barrage, Blood Moon, Bolt Beak, Dragon Hammer, Fishious Rend, Gear
Grind, Hyper Drill, Revelation Dance, Triple Dive), which the engine already treats as unsupported.

### Real public series without spreads

Public Reg M-C series from tournament replays carry open team sheets but never the stat points, so they
cannot score accuracy. `python -m scripts.bo3_public_series_check --out .cache/public-series-check.json`
runs blind inference on every `bo3-public-showteam-*.json` that has no truth file and checks what real
games can still prove: the series validates, the search finishes, and no checkpoint reports `conflict`
(a real game always has a legal spread, so a conflict means a parser or engine bug). It exits 1 on any
error or conflict.

Result on 22 real series (20 tournament series from the Pikalytics top-contender scrape plus the two
earlier user-reported ones): 0 errors, 0 conflicts; the final checkpoint is ambiguous in 21 series and not
informative in one; 170 of 1,584 stats narrowed (10.7%) and 652 interior points excluded. This is a
consistency result, not accuracy.

## How well the simulations resemble real play

Measured on the same logs, real public series (22, from `tests/fixtures/series`) against each simulated corpus.
Hits are the eligible direct-damage observations the inference actually receives.

| Per series / game | Real (22) | Sim, real teams (110) | Sim, random teams (160) | Sim, random (40) |
|---|---|---|---|---|
| Games per series | 2.59 | 2.40 | 2.42 | 2.27 |
| Turns per game | 7.4 | 11.0 | 13.9 | 14.8 |
| Damaging hits per game | 13.0 | 19.1 | 28.8 | 30.6 |
| Eligible hits per series (median) | 20 | 27.5 | 35 | 32.5 |
| Critical hits per 100 hits | 4.2 | 4.4 | 4.7 | 4.6 |
| Mega Evolutions per game | 1.74 | 1.25 | 0.30 | 0.21 |
| Protect-type moves per game | 3.6 | 7.0 | 4.1 | 4.7 |
| Distinct species | 69 | 69 | 206 | 181 |

Largest reasons a hit is excluded, as a share of all exclusions:

- Real: copied or changed ability 28%, `-clearnegativeboost` 23%, Roost 18%, Toxic Spikes 14%, type change 9%.
- Sim, real teams: copied or changed ability 45%, type change 20%, Disguise 11%, `-clearnegativeboost` 7%, Imprison 6%.
- Sim, random teams: confusion 13%, Focus Punch 12%, Uproar 8%, Future Sight 6%, copied ability 6%.

Judgement, one source at a time:

1. **Random teams, 40 and 160 series (M-B).** Good for volume and for exercising rare mechanics (Illusion,
   Uproar, Focus Punch) and 181-206 species. Poor stand-in for real play: games last twice as long, give 1.6x
   more eligible hits, almost never Mega Evolve (0.2-0.3 against 1.74) and the excluded mechanics are ones real
   VGC rarely shows. Narrowing and tightness measured here are optimistic for real data. Soundness is the claim
   it supports well.
2. **Real team sheets, 110 series (M-C).** The closest: the same 69 species, items and move sets, Mega rate and
   exclusion mix nearer to real (the biggest gaps are copied abilities and Protect use). Still 1.5x the turns and
   1.4x the eligible hits of real games because the bots choose moves at random and waste turns (Protect 7.0 per
   game against 3.6). Spreads are drawn, not human. Best source for accuracy claims, with the bot caveat.
3. **22 real public series.** Fully realistic and the only check that real logs parse and never conflict, but
   no spread is known, so no accuracy claim. 20 eligible hits per series is the real evidence budget.
4. **Own-account series 2709311277.** Real play with known spreads, one series (72 stats). Realistic, tiny.
   Narrowed 29% on extreme spreads (32/32/2), which is the upper bound, not the typical case.
5. **Data fidelity of the simulator.** Against the Showdown master, the package differs in three moves and
   five Mega abilities (see above), now corrected by the overlay. Against the calculator, after the overlay
   the only gaps are the ones listed above (Metal Claw, Growth, unsupported moves).

Consequence for the numbers: the simulated soundness result (about 99.97% sound, 99.7% of narrowed claims) is
credible, while its narrowing rate (7-8%) should be read as an estimate under easier evidence than real games
provide. Closing the realism gap needs spreads from real players, or bots that choose moves like players.

## Strong-claim KPI (how we judge narrowing)

The goal is not to narrow every stat. A few sound, tight claims (for example Garchomp HP 0-2, Floette-Eternal
Def 0-11) already reveal spreads players never see, and "Unknown" is the honest answer without evidence. The mean
fraction of range removed hides this (many stats with no evidence dilute it), so narrowing is judged on the tail.

KPI: (1) every narrowed claim is sound, mandatory; (2) the number of claims at each width (33 values per stat);
(3) the share of Pokemon with at least one strong claim. Measured on the 110-series real-team run, final game of
each series (7,920 stats, 1,320 Pokemon; all numbers are over the simulated corpus):

| Claim | Stats | Share of stats | Sound |
|---|---|---|---|
| Interval narrowed (width < 33) | 537 | 6.8% | 537/537 |
| Width <= 24 | 325 | 4.1% | 325/325 |
| Width <= 16 (half the range) | 153 | 1.9% | 153/153 |
| Width <= 8 | 57 | 0.7% | 57/57 |
| Width <= 4 | 11 | 0.1% | 11/11 |

Median width of a narrowed stat: 22. Pokemon with at least one narrowed stat: 310 (23.5%); with a stat of width
<= 16: 102 (7.7%); width <= 8: 40 (3.0%). Stats of width <= 16 by stat: HP 35, Atk 22, Def 43, SpA 20, SpD 31,
Spe 2 (each out of 1,320). The 663 "narrowed" stats in the table above also count 126 stats with only interior
holes (excluded values inside an unchanged min-max), 537 + 126 = 663 after overlap.

Reading: a width of 8 or less is rare (under 1% of stats), and these are optimistic simulated numbers.
Targets are set on width <= 16 and <= 8 counts, never on the average.

## Adding a series

You control both accounts, so you can know both teams. Decide the spreads **before** playing.

1. **Pick spreads that do not follow convention.** Draw legal random spreads (0-32 per stat,
   total 66, IVs 31) instead of the usual 32/32/2. A convention-shaped set lets an inference
   that leans on priors look right. Random spreads test the evidence alone.
2. Play a best-of-three in `gen9championsvgc2026regmcbo3` with both accounts, open team
   sheets on, and keep the replays public.
3. Save the three replays as `tests/fixtures/series/bo3-public-<firstReplayNumber>.json`:

   ```python
   import json, urllib.request
   ids = ["gen9championsvgc2026regmcbo3-AAAA", "gen9championsvgc2026regmcbo3-BBBB", "gen9championsvgc2026regmcbo3-CCCC"]
   games = [json.load(urllib.request.urlopen(f"https://replay.pokemonshowdown.com/{i}.json")) for i in ids]
   json.dump({"source": "own accounts", "replay_ids": ids, "games": games}, open("bo3-public-AAAA.json", "w"))
   ```

4. Write `tests/fixtures/series/bo3-truth-<same number>.json` with both teams:

   ```json
   {"source": "own spreads, written before the games", "level": 50, "ivs": 31,
    "teams": {"AccountA": {"Garchomp": {"nature": "Jolly",
        "points": {"hp": 2, "atk": 32, "def": 0, "spa": 0, "spd": 0, "spe": 32}}},
              "AccountB": {"Raichu": {"nature": "Timid",
        "points": {"hp": 0, "atk": 0, "def": 0, "spa": 32, "spd": 2, "spe": 32}}}}}
   ```

   Every Pokemon the replays register needs an entry; the scorer rejects missing, extra,
   over-66 or incomplete entries before scoring.
5. Run the corpus. It is picked up automatically; no code change is needed.

## How much data

With zero failures, the 95% lower bound on soundness is about 94.9% for 72 stats (one series),
98.9% for 360 (five series) and 99.5% for 720 (ten). Aim for at least five series with varied
species, Megas, spread moves and few-hit games before claiming general accuracy.

## Limits

- Simulated series have random move choice and the simulator's own damage code; series from your own accounts reflect your play style and team shapes, not the ladder.
- A truth file is scoring data only: the search never reads it, and a test checks it is
  opened after inference ends.
- Matching a spread to the hits does not prove one global HP timeline.
- This scores stat-point inference. The text the model writes is checked separately
  (narrative guards and `scripts/bo3_depth_benchmark.py`).
