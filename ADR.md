# Architecture Decision Records — Determinism & Anti-Hallucination Hardening

> Companion to [CLAUDE.md](CLAUDE.md). CLAUDE.md states the invariants; this
> file records **why** thirteen specific changes were made, what alternatives were
> considered, and what trade-offs remain. Written in English per the project's
> language policy (see CLAUDE.md §0). Session date: 2026-07-24/25.

---

## How to read this document

Each ADR follows the same shape: **Context** (the reported symptom and its
root cause), **Decision** (what was changed and, critically, *why that layer*
and *why that shape*), **Alternatives considered**, **Consequences**
(including honestly-stated residual limitations), and **Files touched**.

All thirteen specific-change decisions were constrained by the same
non-negotiable: the project's Clean Architecture boundary (`domain` →
`adapters`/`services` → `ui`, all wiring through `src/services/container.py`).
None of them introduce a new dependency direction, a new orchestration
backend, or a bypass of the `CalcEngineAdapter` / `StrategyKnowledgeProvider`
protocols. Where a fix could have been "patched" at the prompt layer alone
(telling the LLM to just not hallucinate), it wasn't — every fix here moves
the guarantee as far downstream into deterministic code as it will go, and
only asks the LLM to *faithfully report* what determinism already
guarantees. That single principle — **push correctness into the
domain/service layer; only ask the LLM to not contradict it** — is the
thread connecting all fourteen ADRs (ADR-010 names it explicitly as its own
synthesis record).

---

## ADR-001 — Bench-only Pokémon must never be treated as in-play

**Status:** Accepted · `fix/bench-only-matchup-hallucination`

### Context
The LLM narrated a matchup — *"Raichu → Whimsicott using Zap Cannon: 28.9%–
34.8%"* — for a Pokémon that was team-previewed (`|poke|` line) but **never
switched into the actual battle**. The number itself was a real, correctly
computed calc result; the problem was that the calc was ever run for that
Pokémon at all, and the LLM had no way to know it hadn't played.

Root cause: `GameState.side_of()` mapped every species from `side.team` (the
full 6-mon preview roster) to its owning player. `SelectionStrategy`'s
cross-side fallback (`cross_side_matchups`, used whenever the 1st AI's own
output was empty/unusable) enumerated `side_of.keys()` — i.e. the full
preview roster, not the 4 Pokémon actually brought. Two other roster-derived
call sites (`rosters()`, `candidate_species()`) already correctly used
`side.brought()`; `side_of()` was the one path that hadn't been updated to
match.

### Decision
Make `side_of()` consistent with the rest of the domain model: derive it from
`side.brought()` (Pokémon actually seen switching in), falling back to the
full team **only** for a side that never switched anyone in at all (mirroring
the existing fallback shape in `rosters()`). No new method, no new field —
the fix is a one-line change to an existing domain method's data source,
because the *contract* ("which Pokémon exist for this analysis") already had
a single home (`GameState`) and a single correct definition (`brought()`)
elsewhere in the same class. Introducing a second, competing definition would
have been the actual architecture smell here.

### Alternatives considered
- **Filter at the selection-logic layer instead** (`cross_side_matchups`
  taking an explicit species allow-list). Rejected: this would have fixed the
  fallback path but left `side_of()` itself lying about roster membership to
  every other caller (`turn_simulator.py`, `matchup_evaluator.py` also call
  `game_state.side_of()`). Fixing the source of truth once is strictly safer
  than filtering every consumer.
- **Filter in the prompt** ("ignore Pokémon that didn't switch in"). Rejected
  on principle (see the connecting thread above): the LLM has no reliable way
  to audit that against six team-preview lines buried in a JSON context; the
  guarantee belongs in the domain layer where it can be unit-tested.

### Consequences
- `candidate_species()`, `rosters()`, `side_of()`, and every matchup/turn
  path built on top of them now agree on exactly one definition of "in
  play." Verified live: a real replay's 6-mon preview roster (2 benched per
  side) now correctly reduces to the 4 actually-brought Pokémon everywhere.
- No known regressions: the only consumer relying on the old (broader)
  behavior was the very fallback this fix targets.

### Files touched
`src/domain/models.py` (`GameState.side_of()`), `tests/test_bench_only_exclusion.py` (new).

---

## ADR-002 — Never fabricate a move; preserve move history across Mega Evolution

**Status:** Accepted · `fix/bench-only-matchup-hallucination` (2nd commit)

### Context
A verdict read *"Garchomp → Talonflame using Tackle: 11.1%–12.4%"* — Tackle
is not a move that Garchomp used, or plausibly runs, in this replay. Two
independent bugs compounded:

1. **Parser identity fragmentation.** Showdown's log distinguishes a
   Pokémon's stable per-slot *nickname* (`p2a: Staraptor`) from its current
   *appearance* (`details`, e.g. `Staraptor-Mega, L50` after Mega Evolving).
   The switch/drag handler was registering roster entries by `details`
   (current appearance) instead of by the *nick* (stable identity). The
   consequence: the moment a Pokémon Mega Evolved and was later switched back
   in, it was re-registered as a **second, brand-new PokemonSet** — with an
   empty moveset — under the post-Mega name, orphaning the moves already
   recorded against the pre-Mega name. (This is also why an earlier
   diagnostic run showed a 4-Pokémon team listed as 5 entries: `Raichu` +
   `Raichu-Mega-Y`, `Staraptor` + `Staraptor-Mega`.)
2. **A hardcoded fallback move.** `MatchupEvaluator._candidate_moves()`
   returned `mon.moves or [_DEFAULT_MOVE]` (`_DEFAULT_MOVE = "Tackle"`). Any
   Pokémon with zero recorded moves — whether genuinely (never acted) or
   because of bug #1 — silently got a fabricated move instead of being
   skipped.

### Decision
- **Fix identity at the source**, in the parser: the switch/drag handler now
  resolves a `key` — the existing bucket entry keyed by `nick` if one already
  exists, else the current `species` string (first appearance) — and
  registers/tracks under that stable key. This exactly mirrors logic
  `_handle_move` *already* used independently (`nick if nick in bucket else
  ...`) to attribute moves correctly; the switch handler was the one place
  that hadn't adopted the same rule. Making both agree, rather than adding a
  reconciliation pass afterward, keeps the parser a single linear pass with
  no second normalization stage.
- **Remove the fallback, don't relabel it.** `_candidate_moves()` now returns
  `list(mon.moves)` — nothing else. A Pokémon with no confirmed moves simply
  produces no verdict for that pairing (`_best_move_verdict` returns `None`,
  already handled by the existing `if best is None: continue`). No new
  control flow was needed — the "skip gracefully" path already existed; it
  was being bypassed by a value that always evaluated truthy in a `mon.moves
  or [...]` expression.

### Alternatives considered
- **Keep both post-/pre-Mega entries and merge them at read time**
  (`index_sets`). Rejected: this would require every future consumer of
  `side.team` to know about the merge convention, whereas fixing the parser
  once means every downstream layer sees one clean identity per Pokémon, by
  construction.
- **Replace `Tackle` with a "smarter" generic filler** (e.g. the Pokémon's
  highest-base-power learnable move from the dex). Rejected outright: this
  is exactly the class of fabrication CLAUDE.md's anti-hallucination
  invariants forbid — a plausible-sounding number is *worse* than no number,
  because it's indistinguishable from ground truth to the reader.

### Consequences
- Roster counts are now accurate (verified live: 4 Pokémon per side, not 5).
- A Pokémon's confirmed moveset is now the *union* of everything it used
  across the whole game, correctly attributed regardless of when it Mega
  Evolved — this is also what makes ADR-004 possible (the optimal-play
  feedback loop needs a trustworthy, non-fragmented moveset to enumerate).
- Residual, explicitly accepted limitation: using the **pre-Mega base
  species** as the one identity for all turns (including post-Mega ones) is
  a stat approximation for Mega'd Pokémon. This was already true before the
  fix in a less consistent form; ADR-005 makes it an honest, visible caveat
  instead of a silent one.

### Files touched
`src/adapters/parsers/showdown_parser.py`, `src/services/matchup_evaluator.py`,
`tests/test_matchup_no_fabricated_moves.py` (new).

---

## ADR-003 — Resolve a real published Smogon format instead of failing on a fictional one

**Status:** Accepted · `fix/smogon-dex-context`

### Context
Every species queried through the official `@pkmn/smogon` integration came
back as "No usage data available," even for staple VGC Pokémon. Two
independent misconfigurations, not a single bug:

1. `ORACLE_USE_SMOGON_DEX` was unset (defaults `false`) — the official-data
   path was never engaged at all.
2. `ORACLE_CHAOS_DATA_PATH` pointed at a single 8-species demo file
   (`sample_data/...json`) instead of the full tiered directory
   (`data/chaos/`), so even the Chaos **fallback** was starved of data for
   most of the roster, including a species (Basculegion) that the existing
   regulation-fallback logic could already have found in an older-regulation
   tier — the mechanism worked; the directory it was pointed at was wrong.
3. Once enabled, the Node layer (`smogonDex.js`) always queried
   `@pkmn/smogon` with the replay's *literal* format id — a project-specific
   fictional/future regulation (`gen9championsvgc2026regmb`) that Smogon's
   real host has never published. Confirmed live: `analyses()` silently
   returned empty, and `stats()`/`sets()` **threw**, because the library
   tries to `JSON.parse()` the host's 404 HTML response as if it were data.

### Decision
- **Config, not code, for #1 and #2** — this was a deployment/environment
  problem, not an architectural one; the composition root (`Container`) and
  the Chaos regulation-fallback (`ChaosRepository`) already implement exactly
  the behavior wanted, once pointed at the right inputs.
- **Graceful degrade-and-retry inside the adapter, for #3.** `smogonDex.js`
  now retries every lookup unscoped (no format filter) whenever the caller's
  literal format returns nothing, and every fetch is wrapped so a bad/unknown
  format degrades to "no data" instead of throwing. `analyses()` additionally
  sorts VGC-tagged formats first, since a VGC-focused tool should prefer real
  VGC strategy prose over, say, a Singles NU analysis, when both exist.

### Alternatives considered
- **Hardcode a list of "current" real VGC format ids to try** (e.g.
  `gen9vgc2025regh`). Rejected: this goes stale every time Smogon ships a new
  regulation and requires a code change to keep working. Deriving the
  preferred format from `analyses()`'s own unscoped response (which already
  enumerates every format Smogon has data for, this generation) is
  self-updating and needs no maintenance.
- **Silently swallow the error and keep showing "no data."** Rejected: this
  was the status quo, and it made a config/format problem indistinguishable
  from "this Pokémon genuinely isn't analyzed" (which is also a real,
  legitimate case — see Raichu below).

### Consequences
- Verified live: 7 of 8 previously-empty species now return real official
  Smogon data (via a real, published format that isn't the literal replay
  format); the 8th (Raichu) still correctly shows nothing from Smogon,
  because Smogon has genuinely never published a written analysis for it in
  any Gen 9 format — an honest negative, not a bug.
- `CompositeStrategyProvider`'s official→Chaos fallback order is unchanged;
  this ADR only makes the "official" side actually reachable.

### Files touched
`node_calc/src/smogonDex.js`, `.env` / `.env.example` (config, not committed
secrets), no Python changes.

---

## ADR-004 — Per-turn optimal-play feedback loop (deterministic counterfactuals)

**Status:** Accepted · `feat/turn-optimal-play-feedback-loop`

### Context
The existing `TurnReplaySimulator` already re-consulted the calc engine for
every real action, every turn — but only for the move actually used, i.e. it
could confirm *"was the logged result close to the projection,"* never
*"was there a better play available."* The user asked for exactly that:
re-run the engine, per turn, across every confirmed move for that Pokémon,
ranked toward OHKO, and expose the top few as explicit ground truth.

### Decision
Extend the *existing* per-turn loop rather than build a parallel one. A new
`TurnReplaySimulator._best_alternatives()` re-runs `calc.calculate()` for
every move in `attacker.moves` (the same real, non-fabricated moveset ADR-002
guarantees — deliberately reusing that guarantee rather than re-deriving a
moveset by some other means) against that turn's real target and real field
conditions, filters out status moves (reusing the existing `_STATUS_MOVES`
set), and sorts `(is_ko_guaranteed, max_percent)` descending, keeping the top
4. Because `simulate()` already iterates every turn of the whole game, this
loop's coverage is automatically exhaustive — **no restructuring of the
simulator's control flow was needed**, which is the strongest evidence this
was the right layer for the feature: the architecture already had the shape
the new requirement needed.

A new domain type, `OptimalMoveOption`, was added rather than reusing
`DamageResult` verbatim — `DamageResult` carries fields (`damage_rolls`,
`attacker`/`defender` echoed as strings) that are redundant at this
per-alternative granularity and would have bloated the JSON context sent to
the explanation LLM for no benefit.

### Alternatives considered
- **Compute this once per Pokémon (like `MatchupEvaluator`'s spotlight
  matchups) instead of per turn.** Rejected: the whole point is that the
  *field conditions* (Tailwind, Trick Room, an opponent's current HP/status)
  differ turn to turn, so "what was optimal" is only meaningful when
  evaluated against that turn's exact real state — a single aggregate
  calculation could not honestly answer the question asked.
- **Have the LLM rank the alternatives itself from raw per-move damage
  numbers.** Rejected per the connecting thread: ranking-by-KO-then-damage
  is a deterministic, testable rule; leaving it to the LLM reintroduces
  exactly the class of inconsistency (arithmetic on numbers as text) this
  whole session has been closing off.

### Consequences
- `TurnCheck.best_alternatives` (≤4, ranked) is now part of the exhaustive
  per-turn ground truth, verified live to produce genuinely actionable
  counterfactuals (e.g. a real turn where the move used had only a 77.7%
  chance to 2HKO while a confirmed alternative was a guaranteed 2HKO), and to
  respect type immunities the same way the rest of the engine does (0%
  correctly shown for an Electric move into a Ground-type).
- Prompt updated to require citing `best_alternatives`' own numbers rather
  than a guess when judging or proposing a better line for a past turn.

### Files touched
`src/domain/models.py` (`OptimalMoveOption`, `TurnCheck.best_alternatives`),
`src/services/turn_simulator.py`, `src/adapters/llm/prompts/explanation_system.txt`,
`tests/test_turn_simulator_best_alternatives.py` (new).

---

## ADR-005 — Make every approximation visible instead of silent

**Status:** Accepted · `feat/deterministic-transparency-and-forme-caveat`

### Context
Two related transparency gaps surfaced once ADR-004 shipped:

1. The **UI** had two verdict blocks — a small "spotlight matchups" list and
   a separate, collapsed "turn-by-turn" list — with no labeling to say which
   one was exhaustive. A user reasonably read the small, always-collapsed
   block as "the" deterministic output and was confused that it held only
   one entry.
2. A specific damage figure (Zap Cannon vs Basculegion, 81.0%–96.4%) didn't
   match the user's own manual calculation (131.6%–156.1%) for the *same*
   nominal matchup. Root cause, confirmed by directly querying the calc
   engine: the attacking Raichu had Mega Evolved into a **custom forme**
   (`Raichu-Mega-Y`) this specific mod defines, which simply does not exist
   in the standard `@pkmn/dex` package the calc engine is built on — verified
   live, the engine has zero stat data for it. Every calc for that Pokémon
   was silently computed against its un-evolved base stats. This is a true
   data-availability gap: the mod's custom stat block isn't published
   anywhere our tooling (or, realistically, any offline tooling) can reach.

### Decision
Since the *number itself* cannot be made correct without data that doesn't
exist anywhere accessible, the decision was to make the **approximation
explicit** everywhere it appears, rather than attempt a fix that would either
(a) silently keep using base stats, or (b) guess at custom stats and risk
manufacturing a second, fabricated number — which ADR-002 already established
as unacceptable.

- `PokemonSet.battle_formes: list[str]` — the parser (reusing the exact
  identity-merge logic from ADR-002, which already distinguishes "current
  appearance" from "stable identity") now *records* an observed forme change
  without re-fragmenting the roster.
- `MatchupEvaluator.forme_caveat()` turns that into a plain-language note,
  wired into both `MatchupVerdict.stat_caveat` and `TurnCheck.stat_caveat` —
  the two existing "one verdict" containers, not a new parallel structure.
- Every damage figure (spotlight matchup, per-turn check, and best
  alternative) now also carries the calc engine's own `description` string
  (e.g. `"0 SpA Raichu Zap Cannon vs. 0 HP / 0 SpD Basculegion: ..."`) — this
  data already existed inside `DamageResult.description` from the very first
  version of the calc adapter; it simply hadn't been propagated onto the two
  newer, narrower result types (`TurnDamageCheck`, `OptimalMoveOption`).
- The explanation prompt now *requires* citing `stat_caveat` when non-empty
  and the EV/nature spread from `description` when presenting a headline
  number — closing the loop so the LLM cannot present an approximated figure
  as if it were exact.
- UI: relabeled both expanders with entry counts and a one-line explanation
  of which is exhaustive; the turn-by-turn one now defaults to expanded.

### Alternatives considered
- **Attempt to model the custom Mega's stats** (e.g. by inferring a
  plausible stat boost from observed damage rolls). Rejected: this is
  reverse-engineering a fabrication from evidence, which is a more
  sophisticated but not fundamentally different violation of the "never
  invent data" invariant than the ADR-002 `Tackle` fallback was.
  Transparency about a genuine limitation is the only honest option here.
- **Hide Mega'd Pokémon from calc entirely** (skip verdicts/turns involving
  them). Rejected: a *base-stat approximation with a clear caveat* is more
  useful to a VGC coach than no analysis at all for a Pokémon that plays a
  central role in the game — and the timeline/turn-by-turn ground truth
  (actual damage taken, actual faints) is completely unaffected by this
  limitation regardless, since that comes from the log, not the calc engine.

### Consequences
- No damage number in this pipeline can now be silently wrong about a Mega
  Evolution or other unrecognized forme — the reader always has both the
  spread assumption (`description`) and, when relevant, the forme caveat.
- This is the first ADR in the session that adds a *user-facing* honesty
  signal rather than only fixing an internal correctness bug — a deliberate
  shift once the underlying data gap was confirmed to be unfixable with
  available tooling.

### Files touched
`src/domain/models.py` (`PokemonSet.battle_formes`, `MatchupVerdict.stat_caveat`,
`TurnCheck.stat_caveat`, `TurnDamageCheck.description`,
`OptimalMoveOption.description`), `src/adapters/parsers/showdown_parser.py`,
`src/services/matchup_evaluator.py`, `src/services/turn_simulator.py`,
`src/ui/app.py`, `src/adapters/llm/prompts/explanation_system.txt`,
`tests/test_forme_caveat.py` (new).

---

## ADR-006 — Make Protect-family blocks a first-class, quantified event

**Status:** Accepted · `feat/protect-prediction-risk-reward`

### Context
The explanation narrated Protect plays superficially ("X used Protect, a
defensive play to avoid damage") with no quantified stakes and no reasoning
about the decision itself. Root cause: when a Protect-family move blocked a
**spread** move, the blocking Pokémon never receives a `-damage` log line —
so it silently vanished from the reconstructed `BattleEvent` entirely. Neither
the ordered timeline text nor `turn_by_turn_checks` ever recorded that the
move *had been aimed at them*, or what it would have dealt had the read been
wrong. The LLM had nothing quantified to reason about, only bare narration —
this was a data-availability gap in the timeline reconstruction, not a
prompting problem, and it needed a parser-level fix before any prompt change
could produce genuine depth.

### Decision
- Parse the previously-unhandled `-activate|POKEMON|move: X` log tag: when
  `X` is a Protect-family move, record the blocking species on a **new,
  separate** `BattleEvent.blocked` list — deliberately NOT merged into
  `targets`, so no existing target-order-dependent logic (`_speed_for`,
  `_best_alternatives`, which both key off `targets[0]`) is disturbed.
- Render the block directly in the ordered timeline text (e.g. `"Ceruledge
  blocked (Protect)"` next to the move's real hit), since `battle_result` is
  the LLM's primary ground-truth read of causality.
- `TurnReplaySimulator._damage_checks` now ALSO computes projected damage
  against `blocked` targets (tagged `actual_result="blocked (Protect)"`) —
  additive to the existing per-target loop, not a new code path.
- Prompt: a new "Predictive / risk-reward analysis" section requiring every
  Protect-read turn to be quantified from this new data, classifying moves as
  spread (protect-resistant, no read required) vs. single-target (a genuine
  commitment), and framing a genuine 50/50 turn briefly in game-theory terms.

### Alternatives considered
- **Add `blocked` species directly into `targets`.** Rejected: chronologically,
  the `-activate` block line usually precedes the real `-damage` line for a
  spread move's other target, so a naive append would have made the blocked
  (unhit) Pokémon `targets[0]` — silently redirecting `_speed_for` and
  `_best_alternatives` onto the wrong, unhit defender. A separate list has no
  such ordering hazard.
- **Infer the block purely from prompt reasoning** ("if Protect appears near
  a spread move in the timeline, assume it was blocked"). Rejected per the
  session's guiding principle: an inference is not a guarantee, and the actual
  declared target of a move (`p2a: Ceruledge` in the log's `move` line itself)
  is a fact the parser can capture exactly, once.

### Consequences
- Verified live against the reported replay: Ceruledge's turn-1 Protect is
  now known to have blocked a specific, quantified projected hit — turning
  "avoided damage" into an actual number the LLM can cite.
- No change to `targets`-consuming code paths; `blocked` is purely additive.

### Files touched
`src/domain/models.py` (`BattleEvent.blocked`), `src/adapters/parsers/showdown_parser.py`,
`src/services/turn_simulator.py`, `src/adapters/llm/prompts/explanation_system.txt`,
`tests/test_protect_block_detection.py` (new).

---

## ADR-007 — Tell the calc engine this is a Doubles game

**Status:** Accepted · follow-up to `feat/protect-prediction-risk-reward`

### Context
Testing ADR-006 surfaced a second, independent, and considerably larger
problem: a user's own manual calculation for a spread-move Earthquake didn't
match ours (ours read as a "guaranteed OHKO"; theirs did not), and — separate
from that — the LLM's answer cited yet a *third*, fabricated number for the
same figure (a hallucination `NEVER recompute a number` rules hadn't fully
suppressed).

Investigating the first discrepancy found the real, previously-undiscovered
bug: `node_calc/src/calcEngine.js` constructed `@smogon/calc`'s `Field` object
without ever setting `gameType`, which **defaults to `"Singles"`**. Confirmed
directly against the library: `@smogon/calc` only applies the standard 0.75x
spread-move damage reduction (the mechanic that already exists for any move
hitting 2+ Pokémon in a real Doubles battle) when `field.gameType ===
"Doubles"`. Since this entire project analyzes VGC — which is *always*
Doubles — every spread-move calculation ever run (Earthquake, Rock Slide,
Heat Wave, Muddy Water, and every other multi-target move used throughout
this session's examples) had been silently overstated by the missing ~25%
reduction (a ~1.33x inflation). Live comparison for the exact reported
matchup: Garchomp's Earthquake vs. Ceruledge read as **113.3–136% ("guaranteed
OHKO")** under the old default; the correct Doubles figure is **85.3–101.3%
("12.5% chance to OHKO")** — a materially different, and much less certain,
result.

### Decision
Hardcode `gameType: 'Doubles'` into both `Field` constructions in
`calcEngine.js` (`calcDamage` and `compareSpeed`), unconditionally — not
threaded through as a configurable parameter from Python. This project has no
Singles mode and CLAUDE.md states its scope as VGC-only; a config knob for a
mode the product never runs in would be speculative generality, not
flexibility. The one caveat (see Consequences) is accepted rather than
engineered around, for the same reason.

Separately, since the LLM's own fabricated number surfaced *while* fixing
this, the prompt gained a dedicated rule forbidding any recomputation
(doubling/halving/summing/averaging) of a context figure, plus a rule against
implying causality between non-adjacent turns without a named, still-active
mechanism — both failure modes seen in the same reported answer.

### Alternatives considered
- **Pass `gameType` per-request from Python, computed from the actual number
  of live opposing Pokémon that turn.** More "correct" in the rare edge case
  where a spread move only has one legal target left (partner already
  fainted) — real Pokémon does not apply the reduction there. Rejected as
  disproportionate: it requires threading turn-specific board state through
  every calc call for a rare edge case, when the overwhelmingly common case
  (2 live targets) is exactly what the global default now handles correctly.
  Documented as a residual limitation instead (see Consequences).
- **Leave it configurable with `Doubles` as the new default.** Rejected: no
  caller in this codebase, now or foreseeably, would ever pass `Singles` —
  a parameter nobody varies is not a parameter, per the project's own
  "no placeholder/speculative code" convention (CLAUDE.md §2.10).

### Consequences
- Every spread-move damage figure computed by this project, retroactively,
  was too high by ~33%. This is a strict accuracy improvement with no
  known downside for VGC analysis.
- Residual, explicitly accepted limitation: because `gameType` is now a
  static "Doubles" setting rather than turn-aware, a spread move calculated
  for a turn where only ONE opposing Pokémon was actually alive/targetable
  (its partner already fainted) will still have the reduction applied, which
  very slightly understates that specific edge case. This is judged
  acceptable against the alternative of leaving the far more common case
  wrong by a full third.
- Verified live: single-target moves (e.g. Zap Cannon) are byte-for-byte
  unaffected, confirming the fix is scoped exactly to spread moves as
  intended.

### Files touched
`node_calc/src/calcEngine.js`, `src/adapters/llm/prompts/explanation_system.txt`,
`tests/test_calc_engine_gametype.py` (new — the first integration test in the
suite to exercise the real Node subprocess rather than the in-memory fake,
because this specific bug class lives entirely in how the Node side talks to
`@smogon/calc` and no fake could have caught it).

---

## ADR-008 — Precompute the Protect-read classification instead of asking the LLM to derive it

**Status:** Accepted · follow-up to `feat/protect-prediction-risk-reward`

### Context
Even after ADR-006 gave the LLM fully quantified block/spread data and ADR-007
fixed the underlying numbers, a real reported answer for "how did the winner
predict the loser's Protect" was still shallow — no numbers cited, and its
central claim was backwards. Live re-verification of the exact reported
replay through the real `TurnReplaySimulator`/`@smogon/calc` (bypassing only
the LLM call, blocked by network policy in the verification sandbox) showed
the ground truth actually says the opposite of the reported answer: the
answer credited the winner's turn-5 Heat Wave as "predicting" the loser's
Garchomp Protect, but Heat Wave is a **spread** move that guaranteed-hits
Staraptor regardless (41.3–48.8%, and Staraptor dies to the same-turn
follow-up) while the blocked Garchomp only avoided 13.7–16.4% chip — no real
threat. That Protect was a **misallocated** read on the losing side, not a
prediction by the winner. The one genuine single-target read in the game
(turn 2, Garchomp blocking Close Combat) went uncredited entirely.

Root cause: ADR-006's prompt section asked the LLM to derive this
classification itself — spread-vs-single-target, "was this an immediate
threat", "was this misallocated relative to a teammate lost the same turn"
— from raw percents and KO-chance text, in prose, under a long rule list.
`gpt-4o-mini` (this project's default model) does not reliably do that
multi-step classification correctly even with explicit rules present. This
is a prompting-robustness problem, not a data-availability one — the exact
numbers needed were already sitting in `turn_by_turn_checks`, just not
pre-classified.

### Decision
Add `TurnReplaySimulator.build_protect_reads(checks, game_state) ->
list[ProtectRead]`, run once after `simulate()` (needs the full turn list,
since "did a teammate faint this exact turn" lives in a different
`TurnCheck` than the block itself). It is a pure derived read over data
`simulate()` already computed — no new engine calls, no new HP-tracking
ledger (confirmed nothing like that exists anywhere in the parser/models
today, and building one correctly would additionally require parsing
`-heal` lines, which the parser doesn't handle — real scope creep for no
need here). Each `ProtectRead` carries: `is_spread_move`/`is_genuine_read`
(from the existing `"spread"` effects tag); `was_immediate_ko_threat`
(`"OHKO" in value_denied.projected_ko_text` — a controlled vocabulary
`@smogon/calc`'s own `kochance().text` already produces, where multi-hit
texts like `"guaranteed 2HKO"` never contain the substring `"OHKO"`);
`misallocated` (not an immediate threat AND a same-side teammate fainted
that same turn, found by scanning the other `TurnCheck`s for that turn);
`value_denied`/`other_targets_hit` (the exact `TurnDamageCheck` entries to
quote). `explanation_system.txt`'s "Predictive / risk-reward analysis"
section was rewritten to primarily instruct: look up the matching
`protect_reads` entry and report its precomputed fields, rather than
re-deriving the classification from scratch. This is the same principle as
every prior ADR: push the guarantee into deterministic code; only ask the
LLM to faithfully report it.

A second, independent bug surfaced live-verifying this against the exact
reported replay: turn 5's block resolved to the wrong player entirely.
`GameState.side_of()` (pre-existing, used everywhere species need a player)
is a global species -> single-player map built with `dict.setdefault` —
when BOTH sides bring a same-named species (this replay has a Garchomp on
BOTH teams, an ordinary VGC mirror, not an edge case), the second side's
Garchomp silently collapses onto whichever side was inserted first. This
flipped turn 5's `blocker_player` to p1 instead of p2, which zeroed out
`misallocated`/`teammate_fainted` for exactly the turn this ADR's example is
built around. Fixed locally, without touching `side_of()` itself (too many
existing call sites, including ADR-001's cross-side matchup logic, rely on
its current global semantics — redesigning the species-keyed domain model to
be player-aware everywhere is a much larger change than this fix warrants):
`build_protect_reads` now resolves a blocker's player by checking which
side's `brought()` roster actually contains that species, preferring the
side OPPOSITE the attacker (a Protect block is virtually always against an
opposing move) before ever falling back to the lossy global map.

### Alternatives considered
- **Track HP per Pokémon per turn to judge "was this Pokémon actually in KO
  range".** More general than the koChance-text check, but requires the
  parser to also handle `-heal`/drain/recoil correctly to stay accurate —
  real new scope with real correctness risk, for a signal the calc engine's
  own KO-chance text already gives for free at the same trust level as every
  other number this project already treats as ground truth.
- **Just tighten the prompt wording further (add more rules/examples).**
  Already tried once (ADR-006); a live re-check with the same replay shows
  it wasn't sufficient. Adding more prose rules does not fix a model
  reliably failing a multi-step classification task — moving the
  classification into code does.
- **Merge concentrated/split-fire detection into the same precomputed
  structure.** Deferred: it requires pairing two attackers' target choices
  within a turn, a distinct and non-trivial aggregation. Left as prose
  guidance in the prompt (unchanged from ADR-006) rather than silently
  dropped, with the gap noted here for a future pass.

### Consequences
- Live-verified against the exact reported replay: turn 1 (Ceruledge blocks
  spread Earthquake) → `is_genuine_read=False`; turn 2 (Garchomp blocks
  single-target Close Combat) → `is_genuine_read=True, misallocated=False`;
  turn 5 (Garchomp blocks spread Heat Wave while Staraptor dies) →
  `is_genuine_read=False, misallocated=True, teammate_fainted="Staraptor"`,
  `blocker_player="p2"` (correct side, post mirror-match fix) — matching the
  corrected read of the game, opposite of the original shallow answer.
- `AnalysisResult.protect_reads` also surfaces in the Streamlit transparency
  panel (`src/ui/app.py`), same pattern as `verdicts`/`turn_checks`.
- The mirror-match fix is local to `build_protect_reads`; `side_of()` itself
  is untouched, so its existing (lossy-on-mirror) behavior elsewhere in the
  codebase is unchanged — an explicitly accepted residual limitation outside
  this feature's scope, not newly introduced by it.
- Residual limitation, stated rather than hidden: the LLM call itself could
  not be re-verified live in the environment this fix was built in (network
  to OpenAI/Gemini blocked there) — the deterministic layer and its five new
  regression tests (including one dedicated to the mirror-match fix) are
  verified directly; a live answer should be spot-checked against the new
  `protect_reads` context after merging.

### Files touched
`src/domain/models.py` (`ProtectRead`, `AnalysisResult.protect_reads`),
`src/services/turn_simulator.py` (`build_protect_reads`,
`_resolve_blocker_player`), `src/services/analysis_service.py`,
`src/services/langchain_orchestrator.py`,
`src/adapters/llm/prompts/explanation_system.txt`, `src/ui/app.py`,
`tests/test_protect_reads.py` (new).

---

## ADR-009 — Actually send the observed Mega forme to the calc engine

**Status:** Accepted · `fix/upgrade-smogon-calc-for-champions-megas`

### Context
User report, with a working repro: calling `@smogon/calc` directly —
`new Pokemon(gen, 'Staraptor-Mega', {...})` — gives correct Mega Staraptor
stats and a real damage number, but this project's pipeline reported it had
no data for that forme (or several others) and silently fell back to base
Staraptor. Two independent, stacked bugs, both live-verified against the
real Node subprocess before any fix was written:

1. **Pinned dependency version.** `node_calc/package.json` pinned
   `"@smogon/calc": "^0.10.0"`. Diffing the installed 0.10.0's gen-9 species
   data against the current published 0.11.0 (what an unpinned
   `npm install @smogon/calc` — what the user ran — actually gets) found
   **49 new Mega-forme entries** added in 0.11.0, including `Staraptor-Mega`
   and, confirmed directly, `Raichu-Mega-Y` — the exact species ADR-005
   investigated and declared a genuine, permanent data-availability gap
   ("does not exist anywhere our tooling can reach"). That conclusion was
   correct against 0.10.0 at the time; it stopped being true the moment
   0.11.0 shipped, and nothing in this project was watching for that.
2. **The calc was never actually asked for the observed forme, in any
   version.** `src/adapters/parsers/showdown_parser.py`'s `record_forme`
   deliberately keeps a Mega-Evolved Pokemon's stable roster identity in
   `PokemonSet.species` (e.g. `"Staraptor"`) and records the observed
   appearance separately in `battle_formes` — a correct ADR-002/ADR-005
   decision, made so the roster/moveset merge never fragments into two
   Pokemon. But `SmogonCalcAdapter._mon_payload` only ever read
   `mon.species` — it never looked at `battle_formes` at all. So even with
   0.11.0 installed and Mega Staraptor's data available, every calc call
   for it still computed on base stats. `MatchupEvaluator.forme_caveat`'s
   docstring claim ("the calc engine has no data for non-standard formes")
   was only ever half true: the engine was simply never asked.

### Decision
- Bump `@smogon/calc` to `^0.11.0` (`node_calc/package.json`); confirmed via
  `node -e` that no exported API (`Field`, `Move`, `Pokemon`, `calculate`,
  `kochance()`, `fullDesc()`, `getFinalSpeed`) changed shape between
  versions, and `@smogon/calc` has no runtime dependency on `@pkmn/dex`/
  `@pkmn/data` (only a `@types/node` dev dependency) — zero peer-conflict
  risk with this project's other Node deps.
- `node_calc/src/calcEngine.js`: `buildPokemon` now accepts an optional
  `battleForme` on the spec. It tries that forme first via a
  `speciesResolves(gen, name)` check (`!!gen.species.get(toID(name))` — the
  library's species lookup only matches the lowercased/stripped `id` form,
  not the display name, confirmed live: `gen.species.get('Charizard-Mega-Y')`
  is `false`, `gen.species.get('charizardmegay')` is a real object) and
  falls back to the base `species` exactly as before when it doesn't
  resolve — preserving ADR-005's safety net for whatever the *next* unknown
  custom forme turns out to be, in a future format or before the next
  `@smogon/calc` release catches up.
- `SmogonCalcAdapter._mon_payload` now sends `battleForme:
  mon.battle_formes[-1]` (last observed) whenever `battle_formes` is
  non-empty. `PokemonSet.species` is untouched — the fix is scoped to the
  one place that talks to the calc engine, not the identity model.
- A new tiny read-only IPC command, `formeResolves`, exposes
  `speciesResolves` directly (`SmogonCalcAdapter.forme_resolves`, added to
  the `CalcEngineAdapter` Protocol). `MatchupEvaluator.forme_caveat` changed
  from a `@staticmethod` that unconditionally warned whenever
  `battle_formes` was non-empty, to an instance method that only warns when
  `forme_resolves` (cached per `(gen, species)` for the evaluator's
  lifetime — one extra IPC round-trip per distinct forme per analysis run)
  says the engine genuinely couldn't resolve it. A caveat on an exact
  number is exactly the kind of dishonesty this project's own transparency
  rules (ADR-005) exist to prevent — the fix is symmetric with that ADR's
  spirit, not a departure from it.

### Alternatives considered
- **Thread a per-call "usedBaseFormeFallback" flag through `DamageResult`/
  `SpeedComparison`/`TurnDamageCheck`/`OptimalMoveOption` instead of a
  dedicated existence-check command.** Would avoid the extra IPC round-trip
  entirely. Rejected: four DTOs (plus their `.model_dump()` surface reaching
  the LLM prompt and the UI) would need new fields for what is, in the end,
  a cosmetic decision (whether to print a disclaimer) — a much larger blast
  radius than a tiny, cacheable, side-effect-free existence check that
  keeps "is the number right" (calcEngine.js) and "should we disclaim it"
  (`forme_caveat`) as two independently reasoned-about, single-responsibility
  concerns.
- **Just bump the version and stop there.** Rejected as incomplete: live
  testing showed this alone does nothing for the actual numbers, because
  bug 2 exists independently of the library version — `_mon_payload` never
  read `battle_formes` in 0.10.0 either. Shipping only the version bump
  would have looked like a fix (the user's `Staraptor-Mega` case specifically
  would start "existing" in our dependency tree) while leaving every Mega'd
  Pokemon's damage numbers exactly as wrong as before.

### Consequences
- Live-verified: `Staraptor-Mega` Brave Bird vs. a neutral Sinistcha now
  reads 154.7–183.5% (guaranteed OHKO, real Mega Atk/Spe) through the full
  `MatchupEvaluator.evaluate()` path, versus 104.1–123.2% on base Staraptor
  stats — and `stat_caveat` for that verdict is now `""` (previously would
  have been a "base stats used" note on an already-correct number, or, pre
  version-bump, on a genuinely-approximated one).
- ADR-005's own motivating example, `Raichu-Mega-Y`, is now itself
  resolvable — that ADR's "does not exist anywhere accessible" framing is
  explicitly superseded for this specific species (ADR-005 as a mechanism
  — record the forme, caveat when unresolved — remains fully valid and is
  what THIS ADR builds on, not replaces).
- Residual, explicitly accepted limitation: a forme published to Showdown
  battles used for real replays but not yet in whatever `@smogon/calc`
  version is pinned at analysis time will still silently compute on base
  stats — now correctly caveated (via the live `forme_resolves` check)
  rather than silently wrong, but not exact. The dependency will need
  bumping again whenever `@smogon/calc` catches up to newly-added Champions
  Megas; nothing in this fix automates that.

### Files touched
`node_calc/package.json` (`@smogon/calc` `^0.10.0` -> `^0.11.0`),
`node_calc/src/calcEngine.js` (`toID`, `speciesResolves`, `buildPokemon`,
`formeResolves`), `node_calc/calc_server.js` (`formeResolves` command),
`src/adapters/calc/smogon_calc_adapter.py` (`_mon_payload`,
`forme_resolves`), `src/domain/interfaces.py` (`CalcEngineAdapter.
forme_resolves`), `src/services/matchup_evaluator.py` (`forme_caveat`
instance method + cache), `tests/conftest.py` (`FakeCalcEngine.
forme_resolves`), `tests/test_forme_caveat.py`,
`tests/test_calc_engine_mega_forme.py` (new).

---

## ADR-010 — Stage synthesis: verify live, push every judgment call into deterministic code

**Status:** Accepted · synthesis, no independent code change — formalizes the
shared principle behind ADR-008 and ADR-009 (`fix/precompute-protect-read-
classification`, `fix/upgrade-smogon-calc-for-champions-megas`)

### Context
Two reports arrived in the same working stage, superficially unrelated:
(1) predictive "who read whose Protect" analysis reads as shallow or
outright backwards; (2) the calc engine claims to have no data for Mega
Evolutions it demonstrably has. Root-causing both surfaced the *same*
underlying failure shape, twice:

- In both cases, some layer of the pipeline was left to make a judgment
  call it was never actually equipped to make correctly. ADR-008: the LLM
  was asked to classify spread-vs-single-target and misallocation from raw
  percentages, in prose, under a long rule list — a multi-step
  classification `gpt-4o-mini` doesn't reliably get right no matter how the
  rules are worded. ADR-009: `MatchupEvaluator.forme_caveat` assumed,
  unconditionally and permanently, that "the calc engine has no data for
  non-standard formes" — true when first written (ADR-005), but a static
  belief nothing ever re-checked against reality as the underlying library
  changed.
- In both cases, **live-verifying the reported symptom against the real
  deterministic engine — before designing a fix — surfaced a second,
  compounding bug the original report never mentioned.** ADR-008's
  live re-check of the exact reported replay found `GameState.side_of()`
  misattributing a blocked Pokemon to the wrong player whenever both sides
  field the same species (an ordinary VGC mirror match, not an edge case).
  ADR-009's live re-check of the user's own repro found that the calc
  adapter never read `PokemonSet.battle_formes` *at all*, independent of
  which `@smogon/calc` version was installed — so bumping the dependency
  alone would have looked like a fix while changing nothing.

Neither compounding bug was hypothesis-reachable from the original report
text alone; both were only found by actually running the real engine
against the real reported case and checking the output number-by-number.

### Decision
Record the shared principle explicitly, since it should now govern how this
codebase is extended, not just describe two commits after the fact:

1. **Never let an LLM prompt, or a static assumption written down once, make
   a classification a deterministic check could answer instead.** When a
   question has a real, checkable answer ("does the engine's dex resolve
   this species?", "was this move spread or single-target?", "did a
   teammate die this exact turn?"), compute it once in Python/Node and hand
   the LLM (or the caveat logic) the conclusion — never the raw ingredients
   and a hope that prose reasoning gets it right. This is CLAUDE.md §1's
   "the LLM explains ground truth; it never invents it" applied one layer
   deeper: it also means don't let a hardcoded assumption silently stand in
   for ground truth that a live check could provide instead.
2. **Reproduce a reported bug live against the real engine before designing
   the fix, not just against the in-memory fakes.** The fakes exist for
   fast regression coverage *after* the real root cause is known and fixed
   — they cannot themselves diagnose a bug that lives in real dependency
   data or real IPC behavior. Both fixes in this stage found a second real
   bug this way; a fix designed only from the bug report's own description
   would have shipped past it.
3. **When a live check becomes possible where only a static assumption
   existed before, retire the assumption — don't let an old caveat keep
   firing once what it warned about is verifiably no longer true.** A stale
   "this number is approximate" disclaimer on a now-exact number is its own
   flavor of the dishonesty ADR-005's transparency rules exist to prevent;
   correctness-signaling has to stay live, not just correctness itself.

### Alternatives considered
- **Leave ADR-008 and ADR-009 as two disconnected point-fixes.** Rejected:
  the recurring shape (unverified judgment call; stale static assumption;
  a second bug only live verification could find) is exactly the kind of
  pattern this project's ADR corpus exists to make checkable for future
  contributors, not merely coincidental between two nearby commits. Naming
  it once, explicitly, turns "we happened to do this twice" into "this is
  how we do it here."

### Consequences
- A new "does the engine actually know X" check added to this codebase
  should default to a live, cached lookup (the `forme_resolves` pattern),
  not a comment asserting a permanent limitation.
- New LLM-facing analysis in this codebase should default to precomputing
  the classification in Python (the `protect_reads` pattern) and let the
  prompt only narrate the precomputed conclusion, reserving prose reasoning
  for genuinely open judgment calls the data doesn't settle either way.
- A bug report against this codebase should be reproduced against the real
  Node engine (and, network permitting, the real LLM) before a fix is
  designed — the sandboxed environment this stage was built in couldn't
  reach the LLM providers, which is why both fixes explicitly document that
  as a residual verification gap rather than silently skipping it.

### Files touched
None — this is a synthesis record. See ADR-008 and ADR-009 for the concrete
code changes it formalizes.

---

## ADR-011 — Wrap the LangChain explanation call so a provider failure never reaches the UI raw

**Status:** Accepted · `fix/upgrade-smogon-calc-for-champions-megas`

### Context
User report with a full traceback: an exhausted OpenAI quota
(`openai.RateLimitError` / `insufficient_quota` / `credit_balance_exhausted`,
HTTP 429) crashed the Streamlit app with a raw Python stack trace instead of
a readable message, even though `src/ui/app.py` already has a
`try/except OracleVGCError` block (added in an earlier session) specifically
to render provider/parsing/calc failures as a friendly `st.error(...)`.

Root cause: that existing handler only catches `OracleVGCError` and its
subclasses. `OpenAIProvider`/`GeminiProvider` (the native orchestrator's LLM
adapters) already wrap every SDK exception into `LLMProviderError` — an
`OracleVGCError` — so the native pipeline was already safe. But
`ORACLE_ORCHESTRATOR` defaults to `langchain`, and
`LangChainAnalysisOrchestrator` never routes through those adapters at all:
it builds its own LCEL chains directly around the raw LangChain chat model
(`RunnableLambda | chat_model | StrOutputParser()`), for streaming/parsing
reasons unrelated to error handling. The selection-chain call already
tolerates failure (a bare `except Exception: raw = "{}"`, falling back to an
empty plan), but the explanation-chain call — the literal crash site in the
report's traceback — had no error handling at all, so any raw SDK exception
(quota, rate limit, auth, network) reached Streamlit unhandled. A dead-code
class, `LangChainLLMProvider` in `src/adapters/llm/langchain_provider.py`,
already implements exactly this wrapping pattern but is never used by the
orchestrator, which builds its own chain around the bare chat model instead.

### Decision
- Wrap the explanation chain's `.invoke()` call in
  `LangChainAnalysisOrchestrator.analyze()` in a
  `try/except Exception -> raise LLMProviderError(...) from exc`, the same
  shape already used in `OpenAIProvider.complete()`, `GeminiProvider.complete()`,
  and `LangChainLLMProvider.complete()` — one consistent pattern across
  every LLM call site in the codebase, native or LangChain-backed. This
  alone routes a provider failure into the SAME `except OracleVGCError`
  branch `app.py` already had, no UI changes required to stop the crash.
- Since the existing UI tip text only suggested "check your API keys" (right
  advice for an auth failure, not for an exhausted-quota one), `app.py`
  gained a small `_error_tip(exc)` helper that recognizes common
  quota-exhaustion and rate-limit substrings in an `LLMProviderError`'s own
  message (`"insufficient_quota"`, `"credit_balance_exhausted"`, `"rate
  limit"`, `"429"`, `"resource_exhausted"`, ...) and points at the specific
  fix — add billing, or switch provider in the sidebar (since deterministic
  calc/Chaos data is provider-independent, only the final explanation needs
  the LLM) — falling back to the previous generic tip for anything else.
  This is presentation-layer string formatting, not business logic, so it
  stays inside `src/ui/app.py` per the Clean Architecture boundary.

### Alternatives considered
- **Route `LangChainAnalysisOrchestrator` through the existing (currently
  unused) `LangChainLLMProvider` instead of hand-building LCEL chains.**
  Would delete the duplicate error-wrapping logic this ADR adds, but
  requires rebuilding the selection chain's JSON-mode/streaming behavior on
  top of a `LLMProvider.complete()`-shaped call, a materially larger,
  riskier change than the two-line fix this bug actually needed. Noted as a
  reasonable follow-up refactor, not done here.
- **Catch the raw exception in `app.py` instead of the orchestrator.** Would
  require the UI layer to know about LangChain/OpenAI/Gemini SDK exception
  types directly, breaking the "UI is a pure view" rule (CLAUDE.md §3) that
  every other failure category already respects by raising a typed
  `OracleVGCError` from the service/adapter layer instead.

### Consequences
- Live-verified with a raising fake chat model standing in for a failed SDK
  call (`tests/test_langchain_orchestrator.py`): the exception surfaces as
  `LLMProviderError` from `analyze()`, not a raw SDK exception — the same
  contract the native orchestrator already guaranteed.
- Deterministic calc/Chaos/turn-check data computed before the explanation
  call is simply discarded on this failure path, same as before — a partial
  result is not returned. Documented as unchanged behavior, not addressed by
  this fix (the report was specifically about the crash, not about wanting a
  partial analysis).
- `LangChainLLMProvider` remains unused dead code; left as-is (out of scope
  — see Alternatives).

### Files touched
`src/services/langchain_orchestrator.py`, `src/ui/app.py`,
`tests/test_langchain_orchestrator.py`.

---

## ADR-012 — Capture a forfeit as ground truth; forbid cross-side attribution in synthesis prose

**Status:** Accepted · `fix/detect-forfeit-and-side-attribution`

### Context
User report with a real replay and the actual generated answer: a game that
ended when `42 s` (p1) forfeited (`|-message|42 s forfeited.` immediately
before `|win|SiniVGC`) produced an explanation with two compounding
fabrications:

1. An invented framing — "SiniVGC (p2) emerged victorious... with a score of
   3-2, showcasing strategic plays and effective decision-making throughout
   the three turns" — describing a normal, fought-to-conclusion team wipeout
   that never happened. The game was 3 turns of real play followed by a
   forfeit; nothing in the data supports "effective decision-making
   throughout" implying a complete, earned victory.
2. A genuine cross-side misattribution in the concluding synthesis: "the
   winning play for SiniVGC was... Annihilape's early damage on Gholdengo...
   allowing p2 to capitalize on their speed advantage." Annihilape is p1's
   (the LOSING side's) Pokemon — its own damage output got credited to the
   winner's strategy.

Root-caused (confirmed by grep before writing any fix): the parser never
handled the `-message` log tag at all — no code path referenced it. So
`battle_result` for this game carried a `winner`, a KO-derived score, and
the 3-turn timeline, with **zero indication the match ended by forfeit**.
Left with only "here's 3 turns and a winner," the explanation model filled
the gap with a generic, plausible-sounding "how a competitive game is won"
narrative — exactly the shape of hallucination this project's prompt rules
already fight, but for a gap the deterministic layer had never closed. The
misattribution (finding #2) is independent of the forfeit and not explained
by it alone: nothing in the existing "Absolute rules" explicitly forbade
crediting a losing side's own move to the winning side's strategy in a
free-form synthesis paragraph (as opposed to misquoting a specific number,
which is already forbidden) — a real gap in its own right.

### Decision
- `BattleOutcome` gains `forfeited_player`/`forfeited_name` (`src/domain/models.py`).
  `ShowdownReplayParser` now parses `-message` lines matching `"<name>
  forfeited."` and resolves the name to a player via the existing
  `player_names` map (`src/adapters/parsers/showdown_parser.py`) — the same
  identity-resolution approach `winner_name` -> `winner_player` already used,
  no new pattern introduced.
- `outcome_summary()` (`src/services/battle_context.py`) renders an
  `"IMPORTANT — ... FORFEITED"` line as the FIRST line of `battle_result`
  whenever present — ahead of even the winner line, since it reframes how
  every other line in the summary should be read.
- `explanation_system.txt` gained two absolute rules: (a) every timeline
  action keeps its actual side's ownership through to any later synthesis/
  conclusion paragraph — citing a side's "strategy" or "why they won" must
  only draw on that side's own actions; (b) when the new IMPORTANT/FORFEITED
  line is present, state the forfeit plainly and do not invent a "how the
  winner earned it" narrative, a full-wipeout score, or an outcome for a
  Pokemon that was never brought in.

### Alternatives considered
- **Only fix the misattribution rule, treat the forfeit narrative as a
  downstream symptom that a stricter "don't overclaim" prompt rule would
  also cover.** Rejected: the forfeit is a real, checkable fact the log
  already states outright (`-message`) — leaving it undetected means every
  future forfeited game keeps handing the LLM an artificial gap to fill,
  regardless of how the prose rules are worded. This project's own working
  principle (ADR-010) is to close data gaps in deterministic code first;
  prompting around a fixable gap would have been the wrong layer.
- **Treat any `-message` line as generically informational text appended to
  the timeline**, rather than a dedicated `forfeited_player` field. Rejected:
  `-message` covers many unrelated Showdown notices (turn timer events,
  Endless Battle Clause, etc.); a forfeit specifically changes how the ENTIRE
  summary must be interpreted (it isn't just another timeline line, it
  reframes the winner/score lines above it), which justified a first-class
  field and a placement ahead of those lines, not a mid-timeline mention.

### Consequences
- Live-verified against the exact reported replay: `battle_result` now opens
  with `"IMPORTANT — 42 s (p1) FORFEITED this game..."` before the winner
  line, and the parsed `BattleOutcome.forfeited_player == "p1"`.
- The cross-side attribution rule is general-purpose, not forfeit-specific —
  it also guards a normal, fully-played game's concluding paragraph against
  the same class of mistake.
- Residual, explicitly accepted limitation: forfeit detection matches the
  exact `"<name> forfeited."` text Pokémon Showdown emits for a manual
  forfeit; a disconnect-timeout or other `-message` wording this project
  hasn't seen an example of yet would not be caught by the same check and
  would need its own pattern added when found.

### Files touched
`src/domain/models.py` (`BattleOutcome.forfeited_player`/`forfeited_name`),
`src/adapters/parsers/showdown_parser.py`, `src/services/battle_context.py`,
`src/adapters/llm/prompts/explanation_system.txt`,
`tests/test_forfeit_detection.py` (new).

---

## ADR-013 — Back-fill nature/EVs from Chaos instead of a bare 0-EV baseline

**Status:** Accepted · `fix/chaos-ev-backfill`

### Context
User report with a concrete number: `"0 SpA Choice Specs Gholdengo Shadow
Ball vs. 0 HP / 0 SpD Annihilape: 188-224 (101.6 - 121%) -- guaranteed
OHKO"`. Correctly flagged as unrealistic given the very Chaos data this
project already loads for this exact metagame — Gholdengo's real top spread
there is `Timid:2/0/0/32/0/32` (Timid, 256 SpA/256 Spe), not 0 SpA.

Root cause: `MatchupEvaluator.enrich_set()` already back-fills `ability` and
`item` from `meta.pokemon_stats[species].top_abilities`/`top_items` when a
replay doesn't reveal them — but never touched `nature`/`evs` at all, even
though `PokemonMetaSummary.top_spreads` already carried exactly this
information. That field, however, is a **human-readable display string**
(e.g. `"Jolly (16 HP / 256 Atk / 0 Def / 0 SpA / 0 SpD / 256 Spe)"`, built by
`ChaosAdapter._convert_ev_divider` for the LLM prompt) — nothing in the
codebase ever parsed it back into a machine-usable nature + EV spread for a
`CalcRequest`. So every calc request whose Pokemon didn't have a
replay-revealed spread silently fell back to the Node engine's own default
(0 EVs/neutral nature) — CLAUDE.md's own documented behavior ("Projected
damage assumes default EVs / base forme") was accurate as a description of
the code, but a needlessly pessimistic default given the exact data needed
to do much better was sitting one field over, unused.

### Decision
- `PokemonMetaSummary` gains `top_spread_nature: str | None` and
  `top_spread_evs: StatSpread | None` — the SAME single most-used spread
  `top_spreads[0]` already represents, but structured. `ChaosAdapter`
  refactors its existing `_convert_ev_divider` parsing into a shared
  `_parse_spread` helper so the display string and the new structured field
  are derived from the exact same parse, not two independent
  interpretations of the same raw Chaos string that could drift apart.
- `MatchupEvaluator.enrich_set()` (the single shared point both
  `MatchupEvaluator.evaluate()` and `TurnReplaySimulator.simulate()` already
  call for every calc request — see ADR's own "no orchestration-backend
  divergence" pattern) now also back-fills `nature`/`evs` from
  `top_spread_nature`/`top_spread_evs` when the `PokemonSet` doesn't already
  have them — a replay-revealed spread (currently never populated by the
  parser, but the check is there for when it is) always wins over the Chaos
  guess; only an actually-missing value gets filled.
- `explanation_system.txt`'s existing guidance on `DamageResult.description`
  updated to describe the new provenance: when a replay doesn't reveal a
  spread, the calc now uses the ideal tier's single most-common competitive
  spread from Chaos, not an arbitrary 0-EV baseline — still an assumption
  (not a confirmed set), and the rule to quote `description`'s exact numbers
  rather than presenting them as confirmed is unchanged, just re-grounded in
  the more realistic default.

### Alternatives considered
- **Parse `top_spreads`' display strings back into numbers at the point of
  use.** Rejected: re-deriving structured data from a string built
  specifically for LLM prose is fragile (any future change to the display
  format silently breaks the calc path) and duplicates parsing logic that
  already exists once, correctly, in `_convert_ev_divider`. Splitting the
  existing parse into a shared helper and exposing both outputs from the
  same call is strictly more robust for the same amount of code.
- **Use an average/blended spread across the top-N spreads instead of just
  the single most-used one.** Rejected: an averaged spread is not a spread
  any real player actually runs — it can land on EV totals or nature
  combinations nobody would build, undermining exactly the "don't invent
  data" principle this project holds everywhere else. The single most-used
  real spread is the best-supported single guess the data provides.

### Consequences
- Live-verified against the exact reported matchup, using the real Node
  engine and this project's actual `data/chaos/gen9championsvgc2026regmb-
  1760.json`: Gholdengo's Shadow Ball now computes as `"256 SpA Choice Specs
  Gholdengo Shadow Ball vs. 0 HP / 176 SpD Annihilape: 188-224 (101.6 -
  121%)"` — note the percentage happens to land close to the old 0-EV number
  for THIS specific matchup only because Annihilape's own top Chaos spread
  also invests heavily in SpD (176), not because the fix had no effect;
  confirmed the SpA change alone (0 EVs -> 256 EVs, same defender) moves
  Gholdengo's Shadow Ball from 188-224 to 252-296 in isolation.
- Every damage/speed calc in this project now uses a materially more
  realistic default whenever a replay doesn't reveal the real spread — this
  applies retroactively to every existing calc request, not just Gholdengo/
  Annihilape.
- Residual, explicitly accepted limitation: Chaos's own "Spreads" stat is a
  rounded/bucketed aggregate over many real players' sets (values are
  multiples of 8), so a converted spread can slightly exceed the real
  in-game 510 EV cap (confirmed live: this project's actual Gholdengo and
  Annihilape top spreads both total 528) — an artifact of Smogon's own data
  format, not introduced by this fix, and not correctable without guessing
  at which stat to shave down (which ADR-002/ADR-005's "never fabricate"
  principle forbids).
- **CLAUDE.md gained a new, permanent, unconditional rule** (§6): never
  edit/modify any file inside `data/chaos/` for any reason — they are raw,
  externally-sourced data this project only reads. Prompted directly by this
  task, since the fix belongs entirely in code (this ADR), never in the data.

### Files touched
`src/domain/models.py` (`PokemonMetaSummary.top_spread_nature`/
`top_spread_evs`), `src/adapters/chaos/chaos_adapter.py` (`_parse_spread`,
`_structured_top_spread`), `src/services/matchup_evaluator.py`
(`enrich_set`), `src/adapters/llm/prompts/explanation_system.txt`,
`CLAUDE.md` (§6 — the `data/chaos/` edit prohibition),
`tests/test_chaos_ev_backfill.py` (new).

---

## ADR-014 — A fully independent parser/model pair for the Showdown-like battle panel

**Status:** Accepted · `feat/showdown-battle-panel`

### Context
The user asked for a Showdown-like visual battle panel (team sprites, HP
bars, a turn-by-turn stepper) next to the existing Q&A. A first draft of the
plan proposed extending `ShowdownReplayParser`/`GameState`/`BattleOutcome`/
`AnalysisResult` — the exact parser and domain models the tested,
already-working LLM pipeline depends on, hardened across ADR-006 through
ADR-013 this session — to add a turn-by-turn HP ledger (which doesn't exist
anywhere in this codebase: `BattleEvent.results` only carries transient
per-move strings, and `-heal`/residual `-damage` outside a move context
aren't parsed at all today). **The user reviewed and explicitly rejected
that plan**, requiring instead that the UI's battle ledger be built by a
totally independent code path sharing no class, helper, or mutable state
with the LLM parser — so this UI feature carries zero regression risk to the
LLM pipeline, and vice versa, no matter how either evolves later.

### Decision
- New, from-scratch domain models in `src/domain/replay_view_models.py`
  (`ReplayPokemonState`, `ReplayTurnSnapshot`, `BattleReplay`) — not an
  addition to `src/domain/models.py`, so the independence is visible at the
  file level, not just in code review.
- New, from-scratch parser, `parse_replay_for_viewer()` in
  `src/adapters/parsers/replay_viewer_parser.py` — re-walks the same raw
  `|`-delimited log text with its own `_split_ref`/HP-field-parsing helpers
  (intentionally duplicated from `showdown_parser.py`, never imported),
  builds its own HP/forme/status/active-roster/field-condition ledger, and
  snapshots it at every turn boundary.
- `src/ui/app.py` calls `parse_replay_for_viewer()` on the same pasted text
  the LLM pipeline receives, as a second, unrelated parse — never routed
  through `pipeline.analyze()` or `AnalysisResult`. The two failure domains
  are kept genuinely separate: a malformed replay for the viewer renders
  "nothing to visualize" without touching the LLM call, and an LLM/provider
  failure (verified live: this sandbox can't reach OpenAI/Gemini, matching
  ADR-011) still leaves the battle panel fully rendered in the left column.
- Verified live (not guessed) against Showdown's actual sprite CDN before
  writing any UI code: the `dex` sprite folder's ID convention is "lowercase
  first segment, single hyphen, remaining segments concatenated with no
  separator" (`Charizard-Mega-Y` → `charizard-megay`, confirmed 200 OK).
  Several of this project's own Champions-format custom Megas —
  `Staraptor-Mega`, `Raichu-Mega-Y`, `Tatsugiri-Curly[-Mega]` — 404 at every
  naming variant tried, the same ~49-species gap ADR-009 already documented
  for `@smogon/calc`'s dex. Every sprite/avatar image renders with a
  client-side `onerror` cascade (see `_cascade_img_html`, the Follow-up
  below) so a missing image never shows as broken.
- Two bugs found and fixed during implementation, both by the project's own
  test-first discipline catching them before they shipped:
  - **Mirror-species collision.** Every per-Pokemon tracker
    (`hp_percent`/`formes`/`statuses`) is keyed by `(player, species)`, not
    a bare species string — otherwise both sides bringing the same species
    (an ordinary VGC occurrence, and literally what this session's own test
    replay has) would overwrite each other's HP in one dict entry. The exact
    bug class ADR-008 fixed for the LLM pipeline's `GameState.side_of()`;
    guarded here independently, from scratch, since this module shares no
    code with that fix.
  - **Un-turn-scoped forme/status/weather lookups.** The first
    implementation looked up `formes`/`statuses`/`weather` from the
    fully-parsed, final-state dicts when building EVERY snapshot (only
    `hp_percent` was correctly copied per turn boundary) — a Mega Evolution
    or weather change on turn 5 was retroactively showing on the turn-1
    snapshot too. Caught by the first test run
    (`tests/test_replay_viewer_parser.py::test_leads_snapshot`), fixed by
    snapshotting all four trackers together at every turn boundary.
- A third bug, found via `streamlit.testing.v1.AppTest` (not just pytest):
  tying the entire result-rendering area (including the stepper itself) to
  `if st.button("Analyze"...)` meant Streamlit's own rerun-on-any-widget-
  interaction model made the whole panel vanish the instant the stepper's
  own prev/next buttons or slider were used (`st.button(...)` only
  evaluates `True` on the exact run it was clicked). Fixed by computing
  once on click, storing into `st.session_state`, and rendering
  unconditionally from there on every rerun — a standard Streamlit pattern,
  but one the plan's design hadn't accounted for since it predates having a
  stepper to click.

### Follow-up (same PR, before merge): a genuinely Showdown-authentic field stage, no emoji

Shipped in the same branch/PR after the user reviewed a first working version
(stacked HP cards, generic emoji for weather/winner/forfeit) and asked for
two more things: match the real client's actual visual layout (a field
scene — background, front/back sprites, avatars — not a card list), and
replace every emoji standing in for a battle *situation* (weather, Tailwind,
winner, ...) with the real asset the Showdown client itself uses for that
situation, sourced from the client repository the user linked
(`github.com/smogon/pokemon-showdown-client`).

- **Field stage, not cards.** `_battle_stage_html` composes one absolutely-
  positioned scene: a real Showdown background (`fx/bg.png`, confirmed live
  as the client's own default field image, not guessed), the opponent's
  Pokemon as FRONT sprites near the top, the player's own side as BACK
  sprites near the bottom (larger, "closer to camera"), each with a
  Showdown-style HP info box (name, `L50`, colored bar, HP%, status/stat-
  boost badges) above it, both sides' avatars + team-icon trays in the
  corners, and a turn badge. `parse_replay_for_viewer` gained two more
  independent trackers for this: `boosts` (net stat stage per Pokemon,
  parsed from `-boost`/`-unboost`, reset on every switch-in — stat stages
  don't persist across a switch in the real game either) and `team`/
  `avatars` (from `|poke|`/`|player|` lines, previously unused by this
  module).
- **Sprite ID convention turned out to be per-species idiosyncratic, not one
  rule.** Live-testing beyond the original `charizard-megay`-style rule
  found it fails for species whose real name itself contains a hyphen —
  `Porygon-Z` needs `porygonz` (hyphen dropped), `Kommo-o` needs `kommoo`,
  while `Charizard-Mega-Y` needs `charizard-megay` (hyphen kept) — and there
  is no way to tell which rule applies from the string alone without
  Showdown's own species table. `_sprite_id_candidates` now returns both
  candidate forms; `_cascade_img_html` tries every one across every sprite
  tier (animated → static, front/back as appropriate) before ever reaching
  the pokéball placeholder.
- **Emoji → real asset, backed by the actual client CSS, not guessed.**
  Fetched `battle.css` from the linked repository directly and grepped its
  `.weather`/`.statbar` rules for real filenames before writing any
  replacement code:
  - Weather AND Trick Room (Showdown internally models Trick Room as a
    "weather" for this exact purpose) get their real `fx/weather-*.{jpg,png}`
    icon — every filename used was curl-verified live (`sunnyday`,
    `raindance`, `sandstorm`, `hail` (also covers `snow`/`snowscape`),
    `strongwind`, the four terrains, `gravity`, `magicroom`, `trickroom`,
    `wonderroom`).
  - **Tailwind deliberately stays icon-less.** The client's own CSS has no
    background-image rule for it either — Tailwind is real-game plain text
    there too, so a plain text badge (no icon) is the *faithful* choice, not
    a shortcut. Recorded here so a future contributor doesn't "fix" this by
    inventing an icon that doesn't exist in the game.
  - The winner banner replaced a trophy emoji (no such asset exists in the
    real client) with the winning trainer's own avatar image — something
    the client *does* show — via the same `_avatar_urls`/`_cascade_img_html`
    already built for the side headers.
  - The forfeit banner dropped its icon entirely: the real client renders a
    forfeit as a plain log line, no graphic, so a plain colored-text banner
    matches it more faithfully than any icon would.
  - Status-condition badges (PAR/BRN/PSN/SLP/FRZ) were already text badges,
    not images — cross-checked against `battle.css`'s `.statbar span.par`
    etc. rules and confirmed the *real* client does the same thing (colored
    text, not an icon), so no change was needed there beyond what ADR-014's
    first draft already had.
  - Scope: only emoji standing in for a *battle situation* were touched
    (weather/conditions, winner, forfeit). App-chrome emoji unrelated to any
    in-game situation (the page title/logo, the sidebar's API-key hint, the
    technical debug panels' generic `st.warning`/`st.info` icons) were left
    alone — the Showdown client has no equivalent for "here's where you
    configure your API key," so there is nothing to substitute in. (Swept in
    a later pass — see ADR-015 — once the user asked for those too.)
- **Visual verification without a browser tool.** Neither pytest nor
  `AppTest` can confirm a design *looks* right — they only confirm it
  doesn't crash. The actual generated HTML (same functions, real replay
  data) was wrapped in a small standalone preview page and published as a
  private Artifact so the user could visually confirm the redesign before
  it shipped, rather than only after `streamlit run`-ing it themselves.

### Alternatives considered
- **Extend the LLM pipeline's parser/models (the rejected first draft).**
  Smaller diff, no duplicated low-level parsing. Rejected by the user
  specifically: any risk, however small, to the already-hardened LLM
  pipeline was judged not worth it for a UI-only feature, and the ADR-010
  synthesis principle ("reproduce a bug against the real engine before
  fixing," "push judgment into deterministic code") applies just as much to
  *not introducing* new coupling as to fixing existing bugs.
- **Have `replay_viewer_parser.py` reuse `showdown_parser.py`'s private
  `_split_ref`/`_split_details` helpers instead of duplicating them.**
  Rejected: importing from the LLM parser's internals, even read-only
  static methods, is still a dependency that makes a future refactor of
  that file a two-module concern instead of one — the isolation the user
  asked for is about coupling, not just about which file owns the domain
  model.

### Consequences
- Zero lines changed in `src/adapters/parsers/showdown_parser.py`,
  `src/domain/models.py`, `src/services/analysis_service.py`, or
  `src/services/langchain_orchestrator.py` across BOTH the original panel
  and the Follow-up redesign — confirmed by running the full suite
  throughout (`104` passing before this feature, `111` after the first
  working version, `113` after the Follow-up's `boosts`/`team`/`avatars`
  additions, every pre-existing test file byte-for-byte unmodified).
- A small, accepted amount of duplicated low-level parsing between the two
  parser files — the deliberate cost of the isolation, not an oversight.
- Residual, explicitly accepted limitation (matching this project's
  convention): `-sethp` (Pain Split, Endeavor) isn't tracked by the viewer
  parser — rare in VGC, affected HP would lag until the next tracked change.
- No Streamlit-level pytest coverage added to the permanent suite (matching
  this project's existing convention that the UI has none) — but this ADR's
  own verification WAS done with `streamlit.testing.v1.AppTest`, live,
  during implementation (see Decision), which is how the session-state bug
  above was actually caught; future UI changes should consider the same
  tool for anything involving `st.session_state`/reruns, even without
  committing the test.
- A numeric Showdown avatar ID (as opposed to a named one like `"red"`) has
  no resolvable filename without the client's internal numbered-avatar
  table, which isn't published anywhere this project's tooling can reach —
  `_avatar_urls` falls back straight to a default avatar for those,
  explicitly accepted rather than guessed at.

### Files touched
`src/domain/replay_view_models.py` (new),
`src/adapters/parsers/replay_viewer_parser.py` (new), `src/ui/app.py`,
`tests/test_replay_viewer_parser.py` (new).

---

## ADR-015 — Server-verified sprites, no emoji anywhere on the page, and a lab-themed page background

**Status:** Accepted · `feat/showdown-battle-panel`

### Context
Three follow-up requests on the battle panel and page chrome ADR-014
shipped, none touching the LLM pipeline:

1. **Reported:** Kommo-o and a custom Mega Delphox intermittently rendered
   as a broken image even though the correct sprite URL was already present
   later in `_cascade_img_html`'s client-side `onerror` fallback list —
   relying purely on the browser retrying a sequence of failed image loads
   proved unreliable in at least one deployment. Live `curl` confirmed both
   URLs actually resolve (`ani/kommoo.gif` → 200, matching one of the two
   candidates `_sprite_id_candidates` already generates); the failure mode
   is the client-side retry sequence itself, not a missing candidate.
2. The user asked for every emoji on the page — not just the battle-UI ones
   ADR-014's Follow-up already swept — to be replaced with a pokéball or a
   minimalist Pikachu glyph, chosen by context.
3. The user asked for the battle panel's plain default background to become
   a laboratory interior (a Mewtwo-containment-chamber feel), then, after
   approving that, asked separately for the **whole page's** backdrop
   (currently flat black) to also read as a Pokemon research/training lab,
   documented so it's easy to swap for a real image later, and scrolling
   with a parallax feel as the Q&A/analysis content scrolls past it.

### Decision

**1. Sprite/avatar resolution moved server-side, with safe failure
handling.** `_url_is_reachable()` issues one cached `requests.head()` per
candidate URL; `_resolve_primary()` moves the first server-verified
candidate to the front of the list before `_cascade_img_html` ever builds
the `<img>` tag, so the emitted primary `src` is already correct in the
common case — the client-side `onerror` cascade (ADR-014) is kept, but
demoted to a pure defensive fallback instead of the primary resolution
mechanism. Diagnosing this live in this sandbox surfaced a second, sharper
bug before it could ship: a plain `except requests.RequestException: ok =
False` conflated "the URL doesn't exist" with "the check itself failed"
(here: `SSLError: CERTIFICATE_VERIFY_FAILED — unable to get local issuer
certificate`, the same class of local CA-trust issue already seen this
session with the OpenAI/Gemini clients, not expected in the user's real
deployment) — and was caching that failure as a permanent negative. Fixed
so **only a definitive HTTP response is ever cached**; a network-level
exception returns `False` for that one call without writing to
`_URL_REACHABLE_CACHE`, so a flaky check never permanently and wrongly
demotes a working sprite. Verified two ways: (a) confirmed the exception is
this sandbox's known SSL issue by reproducing it directly; (b) confirmed
the reordering logic itself is correct by re-running the same check with
TLS verification disabled for the diagnostic only (never shipped) — the
real `kommoo.gif` URL was correctly identified as reachable and moved to
front, `kommo-o.gif` correctly identified as not.
- **2. Two self-contained, hand-drawn SVG glyphs replace every remaining
  emoji.** `_POKEBALL_ICON` (branding/neutral notices: the page icon, the
  title bar, the sidebar's API-key tip, the "no metagame data" info box) and
  `_PIKACHU_ICON` (a noteworthy moment: a stat caveat, a misallocated-Protect
  warning, or the inline marker on the alternative that was actually
  played). Both are inline `data:image/svg+xml` URIs — no network dependency,
  same reliability principle as the sprite fix above, a decorative glyph
  can't 404 either. `st.info`/`st.warning` in this Streamlit version take an
  `icon=` param that only accepts a single emoji character or a Material
  Symbols shortcode — neither can render a custom SVG — so those call sites
  dropped `icon=` and inline the glyph into the Markdown body instead via
  `_icon_md()` (`![](data:...)`, plain CommonMark image syntax, since
  `st.info`/`st.warning` bodies in this Streamlit version render Markdown
  only, not raw HTML). `_icon_html()` covers the remaining spots that
  already use `unsafe_allow_html=True` (the title bar, the inline "used"
  marker in the turn-by-turn caption list).
- **3. Two separate lab-themed CSS backgrounds, not one.** The battle
  panel's own stage background (`_LAB_BACKGROUND_CSS`, approved first) stays
  a self-contained gradient — Showdown's own `fx/` background set is
  entirely outdoor/field-themed with nothing resembling an indoor lab, so
  there was no real asset to reuse, and a gradient can never 404. A second,
  distinct constant, `_PAGE_BACKGROUND_CSS`, does the same for the whole
  app: a cooler, greener teal (reads as a different room of the same
  facility rather than a duplicate of the battle stage), a faint blueprint
  grid, a soft ceiling-light glow, and a dark scrim baked into the same
  layered value so text in the Q&A/expanders/sidebar stays legible over it.
  Applied via a new `_inject_global_styles()`, injected right after
  `st.set_page_config`, targeting Streamlit's own stable `data-testid`
  hooks (`[data-testid="stAppViewContainer"]`) rather than `body` — Streamlit
  scrolls an inner container, not the page body itself, so `body`-level
  background rules would not have picked up the parallax effect.
  `background-attachment: fixed` + `background-size: cover` is the entire
  parallax mechanism: the wallpaper stays anchored to the viewport while
  the page's content scrolls over it.
- **Documented for hand-editing, not just left in a comment.** Both the
  inline comment above `_PAGE_BACKGROUND_CSS` and a new "Customizing the
  page background" section in `README.md` give the exact snippet to drop in
  a real image (`linear-gradient(scrim), url('...')`, keeping the scrim
  layer for legibility) and name `_LAB_BACKGROUND_CSS` as the separate knob
  for the battle-stage background specifically — the user asked to have
  this noted down for future reference, not just implemented.
- **Visual verification without a browser tool, again (per ADR-014's
  precedent).** The real HTML from `_battle_stage_html`, `_icon_html`, and
  the real `_PAGE_BACKGROUND_CSS` value were assembled into a small
  standalone Artifact preview — including a live, scrollable demo box using
  the actual background value with `background-attachment: fixed`, so the
  parallax effect itself could be checked, not just the static gradient —
  published for the user to confirm before shipping, the same pattern
  ADR-014 established.

### Alternatives considered
- **Keep resolving sprites client-side only, just reorder the fallback
  list.** Doesn't fix the reported bug: the failure was in the browser's
  own retry sequence, not candidate ordering, so no server-side change
  would be needed if that were the sole cause — but ordering alone can't
  rule out the reported bug recurring, since the root mechanism (why the
  cascade sometimes doesn't complete) was never fully pinned down; verifying
  server-side removes the dependency on that mechanism entirely regardless
  of its exact cause.
- **Cache every `_url_is_reachable()` outcome, including exceptions.**
  Simpler, one cache write path. Rejected once the SSL diagnosis above
  showed it actively makes sprite resolution *worse* than no server-side
  check at all in any environment with a local network/TLS hiccup — a
  transient failure would have permanently and silently demoted a working
  sprite for the lifetime of the process.
- **Map every emoji onto Streamlit's built-in Material Symbols (`icon=
  ":material/...:"`).** Available with zero new SVG work. Rejected: the
  user specifically asked for pokéball/Pikachu symbols, not a generic icon
  set — Material Symbols aren't Pokemon-themed and wouldn't satisfy the
  actual ask.
- **Source a real lab photo/illustration for the page background instead of
  CSS.** Rejected for the same reliability reason as the battle-stage
  background: no such asset exists in Showdown's own CDN (already
  confirmed absent while building ADR-014's Follow-up), and any third-party
  image URL reintroduces exactly the kind of external-fetch fragility Task
  1 in this same ADR was written to remove. Documented instead as a clearly
  marked, easy hand-edit for the user's own image if they want one later.

### Consequences
- `requirements.txt` gained `requests>=2.31` (Streamlit's own dependency
  tree already vendors it transitively, but pinning it directly here
  documents that `src/ui/app.py` now depends on it on its own terms, not by
  accident of Streamlit's install).
- Full suite still green (`113` passing, unchanged — no new pytest coverage
  was added for this ADR; consistent with this project's existing
  convention that the UI has no permanent pytest coverage, and this ADR's
  own verification instead used `streamlit.testing.v1.AppTest` live plus
  the published Artifact preview, per ADR-014's established pattern).
- `_url_is_reachable`'s HEAD requests add up to one small network round-trip
  per distinct sprite/avatar URL per process lifetime (cached after that) —
  accepted latency cost for the reliability gained; a slow or unreachable
  network degrades gracefully to the pre-existing client-side cascade
  behavior rather than blocking or erroring.
- Zero lines changed in `src/adapters/parsers/showdown_parser.py`,
  `src/domain/models.py`, `src/services/analysis_service.py`, or
  `src/services/langchain_orchestrator.py` — this ADR is UI-chrome-only,
  same boundary ADR-014 established.

### Files touched
`src/ui/app.py`, `requirements.txt`, `README.md`.

### Follow-up (next session): the reachability cache never actually survived a rerun

**Reported:** turn-stepping (the ◀/slider/▶ stepper) became noticeably
slower after this ADR shipped, where it used to be fast.

Root cause: `_URL_REACHABLE_CACHE: dict[str, bool] = {}` was a plain
module-level dict, and Streamlit re-executes the *entire script* top to
bottom on every rerun — every stepper click, slider drag, or button press
is a full rerun. That top-level assignment re-ran every single time too,
silently resetting the "cache" back to empty before it was ever read. The
result: every turn-step was re-doing live network HEAD requests against
Showdown's CDN for every sprite/avatar on screen, with the caching layer
providing zero actual benefit — worse than doing nothing, since it added
network latency the pure client-side `onerror` cascade never had.

Fixed by moving the check into a new `_http_head_ok()`, decorated with
`st.cache_data` — Streamlit's own primitive for state that must survive a
rerun — and keeping `_url_is_reachable()` as a thin wrapper that catches
`requests.RequestException` around it. This also preserves the "never
cache a network failure as a permanent negative" guarantee from this ADR's
main Decision for free: `st.cache_data` only memoizes a function's
*return value*, never an exception, so a timeout/DNS/TLS failure is simply
retried on the next call rather than being written into the cache as a
false negative. A bonus over the original hand-rolled dict: `st.cache_data`
is shared across sessions on the same server process too, so in a real
multi-user deployment this cost is now paid once per URL ever, not once
per URL per user.

Verified with a new AppTest-driven check (`requests.head` mocked and
call-counted, two full `at.run()` reruns simulating two stepper clicks):
before the fix, re-visiting an already-rendered turn made the same number
of HEAD calls as the first visit; after the fix, it makes zero.

---

## ADR-016 — Full rename: Oracle / OracleVGC → ProfessorVGC

**Status:** Accepted · `feat/professorvgc-rename`

### Context
The user asked to remove every reference to "Oracle"/"OracleVGC" from the
product: "o produto open source que estamos programando agora é
definitivamente Professor VGC" (the open-source product we're building now
is definitively Professor VGC). This is a breaking change for anyone with an
existing `.env` (the settings' env-var prefix is part of the change), so
before touching code the user was asked, via `AskUserQuestion`, what the new
env-var prefix should be — confirmed: `PROFESSORVGC_`.

### Decision
- **Env-var prefix**: `Settings.model_config.env_prefix` in `src/config.py`
  changed from `"ORACLE_"` to `"PROFESSORVGC_"`. Every field name on
  `Settings` is unaffected (pydantic-settings derives the env var from
  `env_prefix + field_name.upper()`, and no field is itself named after the
  product), so this one line is the entire functional surface of the
  rename on the Python config side.
- **Exception hierarchy**: `OracleVGCError` (the domain base exception every
  other domain exception derives from, in `src/domain/exceptions.py`)
  renamed to `ProfessorVGCError`, no backward-compatible alias — the user
  asked for this "definitivamente" (definitively), and this project's own
  convention (CLAUDE.md §10, "No placeholder code") is complete,
  non-provisional changes, not deprecated shims. Both of its two live call
  sites (`src/ui/app.py`'s import + `except` clause; a docstring/comment
  in `src/services/langchain_orchestrator.py` and
  `tests/test_langchain_orchestrator.py` referencing it by name) updated
  along with it.
- **Every `ORACLE_*` string literal** — error messages naming the env var a
  missing key should be set in (`gemini_provider.py`, `openai_provider.py`,
  `langchain_provider.py`), the UI's own tip text, `.env.example`, and the
  user's own local `.env` (preserving the real API key value untouched;
  `.env` is `.gitignore`d, confirmed before editing it, so this edit never
  touches version control) — renamed to `PROFESSORVGC_*`.
- **Docs**: `README.md`'s title, `DATA.md`'s intro line, and `CLAUDE.md`'s
  title/config table/exception-name mention updated. `CLAUDE.md` is this
  project's own live instruction file (loaded every session) — kept
  internally consistent with the renamed code rather than left describing a
  prefix that no longer exists.
- **Package metadata**: `pyproject.toml`'s `name = "oracle-vgc"` →
  `"professorvgc"`; `node_calc/package.json` and its `package-lock.json`
  `"oracle-vgc-calc"` → `"professorvgc-calc"`. Neither string is imported or
  referenced anywhere at runtime — pure distribution metadata — so this is
  a zero-risk rename.
- **UI title** (`src/ui/app.py`) already read plain "ProfessorVGC" with no
  "OracleVGC" suffix and no emoji — done in the prior session pass (see
  ADR-015) — so no further change was needed there for this ADR.
- **Historical documents deliberately left alone or only lightly touched:**
  - **`ADR.md`'s own pre-existing entries (ADR-001 through ADR-015)** are
    untouched. They are a chronological record of decisions as they were
    made, against the code as it was at the time — rewriting `OracleVGCError`
    to `ProfessorVGCError` inside them would misrepresent what the code
    actually was when those bugs were fixed. Same principle already applied
    in ADR-014's Follow-up note pointing forward to ADR-015 instead of
    editing itself.
  - **`APPLY_FIX_PR.md`, `PUBLISH_TO_GITHUB.md`, `open_pr.sh`,
    `publish_github.sh`** are vestigial one-time delivery documents/scripts
    from an early point in this project's history (predating the
    now-working `gh`-CLI branch/PR/merge workflow this session and every
    recent one uses — see the persistent memory note on `gh` auth being
    resolved). Only their product-name headers/comments and the `ORACLE_*`
    env-var mentions inside them were updated; their actual repository
    identifiers (`https://github.com/Huarada/oracle-vgc.git`,
    `oracle-vgc.bundle`, the suggested repo name `oracle-vgc`) were left
    exactly as-is.
  - **The GitHub repository itself, and this local working-copy folder,
    keep the name `oracle-vgc`.** Renaming a live GitHub repository changes
    every existing clone/bookmark/CI reference pointing at it — a much
    larger, harder-to-reverse action than anything else in this ADR, and
    the user's own ask was about the *product* ("o produto ... é
    definitivamente Professor VGC"), not the repository's URL slug. Left
    untouched; flagged to the user rather than decided silently.
  - `data/chaos/` was not touched, per the standing, absolute rule in
    CLAUDE.md §6 (this rule predates and is unrelated to this rename, but
    is repeated here since a careless project-wide find/replace could
    otherwise have swept through it).

### Alternatives considered
- **Keep `OracleVGCError` as a deprecated alias of `ProfessorVGCError`.**
  Zero risk to any external code importing the old name. Rejected: nothing
  in this codebase or its published history is a stable public API another
  project depends on (it's an application, not a library), and the user's
  own wording ("definitivamente") reads as wanting a clean cut, matching
  CLAUDE.md's no-placeholder-code convention.
- **Also rename the GitHub repository / local folder.** Would make the
  rename total. Rejected for this pass: strictly more disruptive than
  anything the user asked for (breaks existing links), and reversible
  later in a dedicated step if the user wants it, whereas doing it
  silently inside an otherwise code-focused ADR would not be.
- **Rewrite `OracleVGCError` mentions inside past ADR-001..015 entries for
  full-repo consistency.** Rejected: an ADR log's value is as an accurate
  record of what was true when each decision was made; editing history to
  match the present would remove that value for no functional benefit
  (nothing reads ADR.md as executable truth).

### Consequences
- **Breaking change for any existing `.env`** predating this ADR: every
  `ORACLE_*` variable must be renamed to `PROFESSORVGC_*` or `Settings()`
  silently falls back to that field's default instead of the configured
  value (pydantic-settings has no cross-prefix fallback). The user's own
  local `.env` was updated as part of this same change specifically to
  avoid that trap; `.env.example` documents the new names for anyone else
  setting up a fresh copy.
- Full suite still green (`113` passing, unchanged) — verified with
  `pytest -q` and, since `Settings`/exception changes are exercised at
  import time by nearly every module, a `streamlit.testing.v1.AppTest`
  smoke run confirming the UI still starts cleanly end to end.
- `src/domain/exceptions.py`'s docstring, `src/__init__.py`'s module
  docstring, and every `ConfigurationError` message naming a required key
  now consistently say "ProfessorVGC"/`PROFESSORVGC_*` — no code path left
  telling a user to set an env var that no longer does anything.

### Files touched
`src/config.py`, `src/domain/exceptions.py`, `src/ui/app.py`,
`src/__init__.py`, `src/adapters/llm/gemini_provider.py`,
`src/adapters/llm/openai_provider.py`,
`src/adapters/llm/langchain_provider.py`,
`src/services/langchain_orchestrator.py` (comment),
`tests/test_langchain_orchestrator.py` (comment), `pyproject.toml`,
`.env.example`, `.env` (untracked, not part of the commit),
`node_calc/package.json`, `node_calc/package-lock.json`, `README.md`,
`DATA.md`, `CLAUDE.md`, `APPLY_FIX_PR.md`, `PUBLISH_TO_GITHUB.md`,
`open_pr.sh`, `publish_github.sh`.

---

## ADR-017 — A dedicated, self-wiring folder for background images

**Status:** Accepted · `feat/background-assets-folder`

### Context
The user asked whether the repo had a dedicated place to put background
images and, if not, to create one wherever made most sense in the folder
hierarchy. This followed directly from ADR-015's documented customization
path (hand-edit `_PAGE_BACKGROUND_CSS`'s Python source to splice in a
`url(...)`) — an in-progress, uncommitted edit was already sitting in
`src/ui/app.py` at the time (`url('SEU_LINK_OU_CAMINHO_DA_IMAGEM_AQUI')`,
a literal placeholder never filled in), which had *also* deleted the
pure-CSS gradient fallback entirely — the page background was, at that
moment, silently broken (scrim-only, no gradient, an unresolvable `url()`).

### Decision
- New folder: `src/ui/assets/backgrounds/`, colocated with the
  presentation layer (`src/ui`) rather than under the repo-root `data/`
  (external Chaos dumps) or `sample_data/` (bundled test/demo fixtures) —
  neither of those categories fits a UI-only styling asset, and CLAUDE.md
  §3 already scopes `src/ui` as "Presentation... pure view," making this
  the layer a background image actually belongs to.
- **No code edit required to use it.** `_find_background_image()` looks
  for `page.<ext>` (whole-app backdrop) and/or `battle-stage.<ext>`
  (battle-panel backdrop), `<ext>` any of `.jpg/.jpeg/.png/.webp`; if
  found, `_image_data_uri()` base64-encodes it into a `data:` URI layered
  under the same dark legibility scrim ADR-015 established, replacing the
  built-in gradient for that one backdrop. Both are independent and
  optional — the folder starts empty, which is a complete, working default
  (the gradients from ADR-015 are the fallback), not a placeholder state.
- **The expensive part is cached correctly this time.** `_image_data_uri`
  is `st.cache_data`-decorated, keyed on `(path, mtime)` — a deliberate
  callback to the ADR-015 follow-up shipped one session earlier, which
  found that a *bare module-level dict* is not a working cross-rerun cache
  in Streamlit (the whole script re-executes on every rerun, wiping it).
  Reading and base64-encoding a multi-MB wallpaper on every single stepper
  click would have reintroduced that exact class of bug in a new spot;
  keying on mtime additionally makes a replaced file picked up on the next
  load without a server restart, which a bare content-only cache key
  wouldn't give for free.
- Wrote a README inside the new folder itself (not just in the top-level
  `README.md`) naming the two expected filenames, explaining why it starts
  empty, and giving basic size/format guidance — discoverable by anyone
  who opens the folder looking for "where do I put this," not only by
  someone who already found the top-level docs first.
- The stray in-progress edit found in `src/ui/app.py` (see Context) was
  superseded by this change rather than just reverted: the underlying
  gradient gained back its `_DEFAULT` suffix and remains intact as the
  fallback, and the flow the user was reaching for (get an image in
  somehow) now works without needing to touch Python source at all.

### Alternatives considered
- **A repo-root `assets/`, sibling to `data/`.** Groups all non-code
  resources at the top level. Rejected: `data/` and `sample_data/` are
  both *input data* the deterministic/probabilistic layers read (Chaos
  dumps, sample replays) — a UI styling image is a different category
  entirely, and CLAUDE.md's existing layer boundaries already give
  presentation-only assets an obvious home inside `src/ui`.
- **Streamlit's built-in static-file serving**
  (`enableStaticServing=true` + a `static/` folder next to the script,
  served at a URL). Would avoid the base64-encoding step. Rejected: it
  requires a `.streamlit/config.toml` change and has had rougher edges
  across Streamlit versions/deployment targets; the data-URI approach is
  the same self-contained pattern already used everywhere else in this
  file for icons and the sprite placeholder (ADR-015), works identically
  in every deployment target with zero extra configuration, and the
  st.cache_data fix above removes its main performance downside.
- **Cache by content hash instead of mtime.** Slightly more correct
  (survives a file being copied back with an identical mtime but different
  content, or vice versa). Rejected as unneeded complexity: hashing the
  full file to key its own cache defeats the point of caching the
  expensive read in the first place; mtime is the standard, good-enough
  signal for "did this file change" in a single-operator local-edit
  workflow like this one.

### Consequences
- Full suite still green (`113` passing, unchanged — no pytest coverage
  added, consistent with this project's no-UI-pytest convention; verified
  instead with a dedicated script that drops a real 1×1 PNG into the new
  folder, confirms `_background_css()` switches from the gradient to a
  `data:image/png;base64,...` URI, confirms re-touching the file is still
  correct, then removes it and confirms the fallback returns — plus the
  existing `pytest -q` + `AppTest` smoke run).
- A background image is now embedded directly into the page's HTML via
  `st.markdown`, base64-encoded — this is Streamlit-appropriate but means
  a very large image measurably grows every page load's payload; the
  folder's own README calls this out with a concrete size guideline (a
  few hundred KB, not tens of MB) rather than leaving it undiscovered.
- `src/ui/assets/backgrounds/` currently ships empty (no image checked
  in) — deliberate, per Context/Decision above, not an oversight.

### Files touched
`src/ui/app.py`, `src/ui/assets/backgrounds/README.md` (new),
`README.md`.

---

## ADR-018 — A full-viewport loading overlay, and closing the gap between the stepper and the answer

**Status:** Accepted · `feat/loading-overlay`

### Context
Two related complaints, from a screenshot of a real analysis: (1) the
battle panel's stepper visibly became ready/interactive before the Answer
text finished rendering, reading as confusing — did it finish or not? —
rather than as still-in-progress; (2) the existing "still working"
indicator was `st.spinner()`'s small inline text next to the button, easy
to miss, and the user asked for a big, centered, black-and-white spinning
pokéball instead, unambiguous at a glance.

Root cause of (1), once traced through the actual render order in
`main()`: both the battle panel and the Answer are already gated behind
the *same* click — nothing in the results area renders until the whole
Analyze handler finishes, so there's no code path where the Answer is
computed after the stepper within that handler. The real gap sits one
level lower: `_render_battle_panel()`'s sprite images get server-side
verified with a HEAD request (ADR-015) the first time a given species'
sprite URL is ever seen in this server process — `st.cache_data`-cached
after that (ADR-015's follow-up), but a cache MISS means a real network
round trip. That check used to happen in the unconditional render block
*after* `st.spinner()`'s coverage had already ended (the spinner only
wrapped `pipeline.analyze()`), so a first-ever view of some species showed
nothing explaining the extra beat before the battle panel appeared — and
because the battle panel (left column) is built before the Answer (right
column) in code order, any such delay reads specifically as "the stepper
got here first, the answer is still catching up," matching the report
exactly.

### Decision
- **`_loading_overlay_html()`**: a `position:fixed;inset:0` dark scrim with
  a centered, large (120px) spinning pokéball, pure CSS `@keyframes`
  rotation, z-indexed above everything. Reuses `_PLACEHOLDER_SPRITE` — the
  black-and-white pokéball already used elsewhere in this file for a
  missing sprite — rather than the branded red/white `_POKEBALL_ICON`,
  matching what was explicitly asked for (monochrome, not colored) and
  giving the "still working" state a visual identity distinct from normal
  page chrome.
- Swapped `with st.spinner(...)` for `st.empty()` holding this overlay,
  so it can be cleared with one explicit call (`loading.empty()`) inside a
  `finally` block — guaranteed to clear even if `pipeline.analyze()`
  raises, matching the existing `ProfessorVGCError` handling exactly (only
  the presentation of "busy" changed, not the error-handling control flow).
- **Closed the actual gap**: after `pipeline.analyze()` succeeds, and
  still inside the same overlay-covered `try` block, one throwaway call to
  `_battle_stage_html(replay, replay.snapshots[turn_index])` for exactly
  the turn about to render (the final/most-informative one, same one
  `turn_index` is already reset to) — its return value is discarded; it
  exists purely to populate `_http_head_ok`'s `st.cache_data` cache for
  every sprite that specific turn needs. By the time the overlay clears
  and the real unconditional render block runs, that call is a 100% cache
  hit, so the left (battle panel) and right (Answer) columns' Python work
  both become effectively instant and paint within the same tight
  back-to-back sequence, closing the visible gap between them.
- Verified end to end with a dedicated `AppTest` script driving the actual
  "Analyze" click (a fake, no-network `Pipeline.analyze` — matching this
  project's convention that tests never require real keys), which
  confirms: no exception, the result is stored, AND the overlay's own
  `alt="Loading"` marker is absent from every rendered markdown block
  afterward (proving `loading.empty()` genuinely cleared it rather than
  leaving a stuck full-screen spinner — the one failure mode that would be
  strictly worse than the original small spinner).
- Republished the session's standing visual-verification Artifact pattern
  (ADR-014/015) with the real dumped overlay markup, live-animating, for
  the user to confirm before merge — a CSS keyframe animation is exactly
  the kind of thing `pytest`/`AppTest` cannot judge "looks right."

### Alternatives considered
- **Pre-warm every turn's sprites, not just the one about to render.**
  Would also make stepping to OTHER turns instant on the very first visit.
  Rejected for this pass: multiplies the number of upfront HEAD checks
  (and thus the overlay's total up-time) by the number of turns in the
  replay, working against the exact "app feels slow" complaint this ADR
  is fixing; the existing lazy-per-turn resolution (only the turn actually
  viewed gets checked) stays the right default, this ADR only removes the
  *invisible, unexplained* instance of that cost.
- **Restructure the two columns to build both HTML strings fully before
  issuing any `st.*` render call, to shrink the browser-side stagger
  between them.** Would give a marginal, sub-frame improvement on top of
  the fix already made. Rejected as unnecessary complexity once the
  actual, measurable gap (the sprite HEAD-check network cost) was
  identified and closed — the residual gap between two adjacent, purely
  local `st.markdown` calls is not perceptible.
- **Keep `st.spinner()`, just make its text more prominent.** Minimal
  change. Rejected: doesn't address the actual reported confusion (a big,
  unmissable, centered indicator was explicitly asked for), and doesn't
  touch the real latency-gap root cause at all.

### Consequences
- Full suite still green (`113` passing, unchanged — no new pytest
  coverage added, consistent with this project's no-UI-pytest convention;
  verified instead with the dedicated `AppTest`-driven script described
  above, plus the existing smoke run).
- The overlay now also covers the sprite pre-warm call's duration, so a
  first-ever analysis touching many new species takes very slightly longer
  to show ANY result (everything is front-loaded into one busy window)
  rather than showing the battle panel first and the answer a beat later —
  a deliberate trade: a single, longer, clearly-explained wait reads better
  than two shorter ones with an actual "is it done?" ambiguity in between.
- `st.spinner()`'s own text ("Running deterministic calcs + LLM
  reasoning...") is gone; the overlay is purely visual (spinner only, no
  status text) per what was asked for ("simbolo grande... rodando").

### Files touched
`src/ui/app.py`.

---

## ADR-019 — Highlight the Answer/turn-checks/protect-reads slice matching the stepper's current turn

**Status:** Accepted · `feat/turn-highlight`

### Context
The user asked for the LLM's answer text (and, by extension, the
structured per-turn sections) to be highlighted according to whichever
turn the battle-panel stepper is currently on — e.g. moving the slider to
turn 3 should highlight turn 3's write-up in the Answer, so the visual
battle state and the narrative stay visibly tied together.

### Decision
- `_current_turn_number(replay)`: maps the stepper's `turn_index` (an
  array index into `replay.snapshots`) to the actual in-game turn number
  that snapshot represents (0 for "Leads") — the same numbering the LLM's
  answer and `TurnCheck.turn`/`ProtectRead.turn` are already keyed by.
- **Turn-by-turn checks and Protect reads** (structured, already
  turn-tagged domain data): trivial and fully reliable — each entry's own
  `.turn` field is compared directly against the current turn, and its
  header line is wrapped in Streamlit's native `:orange-background[...]`
  markdown directive when it matches.
- **The Answer** (free-form LLM prose) is the harder case: no guaranteed
  structure. `_TURN_HEADER_RE` matches a turn breakdown ONLY at the start
  of a line — `"**Turn 3**:"`, `"3. **Turn 3**:"`, plain `"Turn 3:"` — via
  `re.MULTILINE`'s `^` anchor, deliberately never a mid-sentence,
  incidental mention like "...capitalized on the play from Turn 3..."
  inside a closing summary paragraph, which would otherwise be wrongly
  treated as a new segment boundary. `_highlight_answer_by_turn` then
  wraps the segment from a matching header to the next header (or end of
  text) in the same `:orange-background[...]` directive. If the answer
  never breaks itself down by turn — a normal, common case for many
  questions that aren't asking for a turn-by-turn walkthrough — no match
  is found and the text renders completely unchanged, verified
  byte-for-byte identical in a test.
- **Deliberately not a raw HTML `<mark>` tag.** A tag placed at the exact
  start of a markdown line risks CommonMark's "HTML block" rule (any tag
  alone at a line's start, until the next blank line, has its *contents*
  passed through as raw text rather than re-parsed as markdown) — which
  would silently stop the enclosed `**bold**`/list markdown from
  rendering as anything but literal asterisks. Streamlit's own colored-text
  directive has no such ambiguity (it's a first-class part of Streamlit's
  markdown dialect, not raw HTML) and needs no `unsafe_allow_html=True`.
- A directive can't cleanly span a blank-line paragraph break, so if a
  single turn's write-up happens to contain more than one paragraph, only
  the first is highlighted — a harmless degradation (a partial highlight),
  not a rendering break, and not worth the complexity of handling multi-
  paragraph segments for what both this project's own prompt style and the
  reported example write one short paragraph/list-item per turn.

### Alternatives considered
- **Parse the answer into real HTML first (via a markdown library), then
  highlight at the HTML level.** Sidesteps the CommonMark HTML-block edge
  case entirely and would allow raw `<mark>` styling. Rejected: adds a new
  dependency and a second markdown implementation that has to faithfully
  match Streamlit's own rendering (tables, links, etc.) merely to avoid an
  edge case Streamlit's own directive syntax already avoids for free.
- **Match "Turn N" anywhere in the text, not just at a line start.**
  Simpler regex. Rejected: verified against a realistic multi-turn answer
  (the exact style from the reported example) that this incorrectly
  triggers on incidental mentions like the opening sentence ("...in Turn
  5, where Garchomp used Rock Slide...") and the closing summary
  ("Garchomp's Rock Slide in Turn 5 was the decisive play...") — either
  wrongly starting a new segment mid-sentence or wrongly highlighting the
  summary paragraph for whatever turn number it happens to mention.
- **Highlight the whole answer text differently per turn (e.g. dim
  everything except the current turn) instead of a positive highlight on
  just the matching slice.** More visually dramatic. Rejected: a bigger
  visual change than asked for, and dimming risks readability for the
  (common) case where the answer only has a light or no turn breakdown at
  all — a bare highlight degrades to "nothing highlighted," a dimming
  scheme would degrade to "everything dimmed," which reads as broken.

### Consequences
- Full suite still green (`113` passing, unchanged — no new pytest
  coverage added, consistent with this project's no-UI-pytest convention).
  Verified instead with two dedicated scripts: one exercising
  `_highlight_answer_by_turn` directly against a realistic multi-turn
  answer (confirms the exact incidental-mid-sentence-mention edge case is
  handled, confirms nested `**bold**` markdown survives inside the
  directive, confirms a non-matching turn leaves the text byte-identical);
  one full end-to-end `AppTest` driving the real Analyze click plus a
  simulated stepper move (fake pipeline, no network/keys), confirming the
  highlighted turn flips correctly in the actual rendered output when the
  slider moves.
- A Turn-checks/Protect-read entry for a turn the Answer never explicitly
  narrates still highlights correctly (they're independent mechanisms —
  structured data compared directly, prose matched by header) even when
  the Answer-side regex finds nothing to highlight for that same turn.

### Files touched
`src/ui/app.py`.

---

## ADR-020 — Explicit per-side rosters, confirmed ability events, and a tighter hypothetical-claim rule

**Status:** Accepted · `fix/analysis-quality-roster-abilities`

### Context
User report with a real generated answer, three distinct issues:
1. **Cross-side confusion**: the synthesis paragraph named Kommo-o and
   Sinistcha — both on the LOSING side (p1) — as part of the WINNING
   side's (p2) plan.
2. **An unguarded hypothetical claim**: "Incineroar used Fake Out on
   Whimsicott instead of opting for Flare Blitz, which would have
   guaranteed an OHKO" ignored that Whimsicott's most likely item (Chaos
   top item) is exactly the kind that survives a lethal hit at 1 HP — the
   claim asserted a guaranteed KO without acknowledging that assumption.
3. **"Missing turn-by-turn ability description"** — no ability
   (Intimidate, Trace, Download, ...) ever appeared anywhere in the
   context the explanation model receives.

The user also asked directly whether a recent merge had changed the LLM's
input pipeline and made analysis quality worse. Checked via `git log` on
every file the analysis pipeline touches
(`analysis_service.py`/`langchain_orchestrator.py`/`battle_context.py`/
`selection_service.py`/`selection_logic.py`/the prompt files): the most
recent change to any of them before this ADR was ADR-018's rename PR,
which touched one comment string. **No PR from this session's UI work
(ADR-014 through ADR-019) touches the LLM pipeline at all** — confirmed,
not assumed. The reported issues are pre-existing gaps this pass is the
first to find and fix, not a regression from recent UI work.

Root-caused each issue by reading the actual context-construction code,
not guessing:
1. `outcome_summary()` (`src/services/battle_context.py`) — the function
   that builds `battle_result`, the trusted-context block the prompt calls
   GROUND TRUTH — never states either side's roster anywhere. Every
   timeline line DOES carry its own `p1`/`p2` prefix (`_render_move`
   already renders `"{actor_player} {actor} used {move}"`), but there was
   no single, authoritative, high-salience statement of "who is on which
   side" — the model had to reconstruct it by tracking every event's
   inline prefix consistently across a potentially long timeline, and
   got it wrong in the concluding synthesis paragraph specifically (the
   part of the answer furthest from the actual timeline lines).
2. Verified the deterministic side of item-awareness is actually sound:
   `MatchupEvaluator.enrich_set()` already back-fills `item` from Chaos
   `top_items` (confirmed via `chaos_adapter.py` that `_top_items` sorts
   descending by usage, so "first" genuinely means "most common") for
   BOTH attacker and defender in every calc call `turn_simulator.py`
   makes, including `best_alternatives` — and `@smogon/calc`'s own
   `fullDesc()` (wired through in `node_calc/src/calcEngine.js`) already
   reflects an item like a Focus-Sash-style survival mechanic in its
   `is_ko_guaranteed`/`ko_chance_text` output. The gap wasn't the data —
   it was that the prompt never required the model to (a) ground a
   hypothetical "would have KO'd" claim in an actual computed
   `best_alternatives` entry, or (b) disclose that the entry's own
   item is itself usually a Chaos-derived assumption, not a
   replay-confirmed one.
3. `showdown_parser.py` had no branch for the `-ability` log tag AT ALL —
   grepped to confirm before writing anything. Showdown emits this
   specifically when an ability actively triggers and is therefore
   observable (Intimidate on switch-in, Trace, Download, ...) — never for
   a passive ability that never activates — so this is exactly the kind
   of "real, checkable ground truth the log already states outright"
   ADR-012 established as the right layer to fix a gap like this in.

### Decision
- **Explicit roster lines.** `outcome_summary()` now renders one
  `"<p1/p2> roster (<name>): <species>, <species>, ...."` line per side
  (via the module's own existing `rosters()` helper — already computing
  `side.brought()`, the species actually switched in, not the full
  6-mon team-preview list), placed right after the winner/score/turn-count
  lines and before the ordered timeline — the single authoritative anchor
  for side membership. The prompt's own description of `battle_result` now
  names these lines explicitly and instructs the model to re-check a
  Pokemon's side against its roster line specifically when writing a late
  synthesis paragraph, rather than trusting memory of earlier reasoning.
- **Confirmed ability events.** `showdown_parser.py` gained a `-ability`
  branch: resolves the acting Pokemon via the same `_split_ref`/
  `slot_species` pattern every other branch uses, appends a new
  `BattleEvent(kind="ability", ...)` into the same ordered timeline
  switches/faints/moves already flow through (so it's automatically
  GROUND TRUTH per the prompt's existing framing, no prompt restructuring
  needed), and back-fills the confirmed ability directly onto the
  `PokemonSet` (first reveal wins, never overwritten) so any later calc
  request for that Pokemon uses its real ability instead of a
  Chaos-guessed one. `TurnReplaySimulator.simulate()`'s move loop already
  filters to `kind == "move"`, so the new event kind is inert there by
  construction — verified with a dedicated test, not just asserted.
- **A new absolute rule** in `explanation_system.txt`: any "X would have
  KO'd/secured the game instead" claim about a move not actually used that
  turn must cite that turn's own `best_alternatives`/`deterministic_verdicts`
  entry and its `ko_chance_text`/`is_ko_guaranteed` — if the alternative
  isn't in there (never confirmed for that Pokemon this game, so the
  engine couldn't check it), say it isn't confirmed rather than asserting
  what it "would have" done; and when such a claim hinges on an
  assumed item/ability, disclose that the same way the file already
  requires disclosing an assumed EV spread.

### Alternatives considered
- **Prompt-only fix for the cross-side confusion** (e.g. "always double
  check which side a Pokemon is on"). Rejected on this project's own
  established precedent (ADR-010/ADR-012): a fixable gap in the
  deterministic context is the wrong thing to paper over with a prompt
  instruction alone — the roster line closes the actual gap the model was
  filling in by inference; the added prompt guidance is defense in depth
  on top of that, not a replacement for it.
- **Track and thread stat-boost stages (Intimidate's actual Atk drop, etc.)
  into every calc request**, so a damage projection after a boost/unboost
  event reflects the real modified stat. This would be a materially
  deeper fix — every `CalcRequest.field` call site would need a live
  per-Pokemon boost-stage ledger (the same category of stateful tracking
  `replay_viewer_parser.py` already built independently for the UI panel,
  per ADR-014, but nothing analogous exists yet for the LLM pipeline's
  `GameState`/`FieldConditions`). Scoped OUT of this pass: the user's
  report was about ability *visibility* ("missing turn-by-turn ability
  description") and item/roster correctness specifically, not about
  boost-adjusted damage numbers, and folding a stateful-tracking feature
  of this size into the same pass as three narrower, independently
  verifiable fixes would raise this ADR's risk for no ask actually made.
  Recorded here as a real, known, NOT-yet-closed gap for a future pass —
  a damage projection computed after an Intimidate (or any other boost)
  in the same game still does not reflect that stat change.
- **Re-derive a Pokemon's side from its species string via `side_of()`
  inside the prompt-building step instead of a text line.** `side_of()`
  already exists (`GameState.side_of()`, a species -> single-player map)
  and is used deterministically elsewhere (protect-read blocker
  resolution, cross-side selection guardrails) — but it's a Python-side
  lookup the LLM has no access to; the fix has to be something the model
  actually reads in its own context, which is exactly what the roster
  line is.

### Consequences
- Full suite: `120` passing (was `113`; `+7` — `test_ability_events.py`
  new, `4` tests; `test_roster_summary.py` new, `3` tests). Every
  pre-existing test file unmodified except this ADR's own additions.
- `battle_result` (and, via the same `outcome_summary()` call, the
  selection-stage prompt too — `selection_service.py`/`selection_logic.py`
  both already consume it) now carries strictly more ground truth than
  before for every game with at least one observed ability trigger; no
  existing field or format changed, so this is additive only.
- Live-checked against a realistic multi-mon example (own script, not a
  unit test) that the rendered `battle_result` text reads cleanly with the
  new roster lines and an interleaved ability-activation line in the
  timeline, in the expected chronological position.
- Residual, explicitly accepted gap (see Alternatives): stat-boost stages
  are still not threaded into calc requests — a damage number computed
  after an Intimidate/setup move in the same game is still computed
  without that stage applied. Not new to this ADR; now explicitly recorded
  rather than silently absent.
- Could not verify against the live OpenAI/Gemini API in this sandbox
  (the same local TLS trust-store issue noted in ADR-015 blocks outbound
  HTTPS here) — verification for this ADR is therefore code-level
  (parser/context-builder unit tests, a live-printed `outcome_summary()`
  sample) rather than a live before/after model response comparison; the
  user's own deployment, where outbound HTTPS already works (confirmed by
  their own screenshots earlier this session), is where the actual prompt
  change's effect on model output should be judged.

### Files touched
`src/adapters/parsers/showdown_parser.py`, `src/services/battle_context.py`,
`src/adapters/llm/prompts/explanation_system.txt`,
`tests/test_ability_events.py` (new), `tests/test_roster_summary.py` (new).

---

## ADR-021 — Stat-stage-aware damage/speed calcs, with a per-switch reset ledger

**Status:** Accepted · `feat/boost-aware-calc`

### Context
Direct follow-up to the gap ADR-020 explicitly scoped out: "a damage
projection computed after an Intimidate (or any other boost) in the same
game still does not reflect that stat change." The user asked for this to
be closed, and specifically flagged the mechanic that makes it non-trivial:
a Pokemon's stat stages reset to neutral the moment it switches back out
and in again, so a correct fix needs a stateful, switch-aware ledger, not
a single game-wide "this Pokemon has +N atk" fact.

### Decision
- **New ground truth, captured at the source.** `showdown_parser.py` gained
  a `-boost`/`-unboost` branch (the five stats that actually feed
  `@smogon/calc`'s damage/speed calc — atk/def/spa/spd/spe; accuracy/
  evasion changes are real events too but neither the calc nor this
  project's field model uses them, so they're deliberately not tracked),
  recorded as its own ordered `kind="boost"` `BattleEvent` (`effects =
  [stat, signed delta as string]`) — flowing into `battle_result`'s
  timeline exactly like the `kind="ability"` events ADR-020 added, so it's
  automatically GROUND TRUTH per the prompt's existing framing.
- **`PokemonSet.boosts: dict[str, int]`** — explicitly documented as NOT a
  fixed attribute like ability/item: a point-in-time snapshot for the exact
  calc it's attached to, never back-filled from Chaos (a stage isn't a
  "typical set" property).
- **The ledger lives in `TurnReplaySimulator.simulate()`**, the one place
  that already walks `game_state.outcome.events` in strict chronological
  order. Previously the loop skipped every non-"move" event outright; it
  now also processes `"switch"` (resets that identity's ledger entry to
  empty — same mechanic `replay_viewer_parser.py`'s independent UI-side
  ledger already uses, per ADR-014) and `"boost"` (accumulates, clamped to
  the real -6..+6 range) events as it passes them, so by the time a later
  "move" event is reached, `boost_ledger[(player, species)]` already
  reflects the exact state at that point — no separate pre-pass, no
  time-indexed lookup structure, just read the live dict at the moment
  it's needed. `MatchupEvaluator.enrich_set()` gained a `boosts` parameter
  purely to apply this snapshot onto a fresh `PokemonSet` copy (never
  mutating the shared indexed set `sets` — the same non-mutation discipline
  it already used for ability/item/nature/EVs).
- **Threaded into every calc call site that builds a defender/attacker**,
  not just the move's own attacker: `_damage_checks`, `_best_alternatives`,
  `_speed_for`, and `_stat_caveat` all now resolve `boost_ledger.get(
  (side_of.get(target_name), target_name), {})` for whichever Pokemon
  they're building — so a target's own defensive/speed stage (e.g. a
  Calm-Mind'd SpD, or a paralysis-adjacent Speed drop) affects the
  numbers exactly the same way the attacker's stage does.
- **Node/IPC pass-through**: `smogon_calc_adapter.py`'s `_mon_payload`
  includes `boosts` when non-empty; `calcEngine.js`'s `buildPokemon` maps
  it straight to `@smogon/calc`'s own `Pokemon` constructor option — a
  single library-native feature already implements the stat-multiplier
  math correctly for both `calculate()` (damage) and `getFinalSpeed()`
  (the same object's `.stats.spe`), so no calc math was hand-rolled here.
- **Prompt updated** (`explanation_system.txt`): states plainly that every
  turn_by_turn_checks/deterministic_verdicts figure already has the real,
  active stage applied — the model must NOT manually adjust a number for a
  boost/drop it sees in the timeline (it's already reflected), and should
  cite the timeline's stage-change line as the reason when a figure looks
  surprising relative to base stats, rather than assuming the number is
  wrong.

### Alternatives considered
- **Precompute boost "windows" like Tailwind/Trick Room** (`[start_turn,
  end_turn]` pairs per stage level). Rejected: a stage isn't a single
  continuous span the way Tailwind is — it can change value multiple times
  within the SAME turn (a boost, then later that same turn an unrelated
  drop), and correctness depends on event ORDER within a turn, not just
  turn number. A live, ordered-walk ledger is simpler and exactly as
  precise as the source data allows, with no windowing data structure to
  get subtly wrong.
- **Track the ledger inside the parser itself** (mirroring
  `replay_viewer_parser.py`'s independent UI-side implementation exactly).
  Rejected: the parser's own stated job (see its module docstring) is
  structural extraction, not point-in-time calc-request construction — the
  ledger only has one consumer (`TurnReplaySimulator`), so building it
  where it's used keeps the parser's output the same flat, chronological
  event list it already produces, with no new cross-cutting field on
  `GameState`/`FieldConditions` for a single caller.
- **Apply boosts in `MatchupEvaluator.evaluate()`'s spotlight matchups
  too.** Rejected for this pass: those matchups are explicitly framed in
  the prompt as "not necessarily matchups that occurred in the timeline" —
  arbitrary attacker/defender pairs with no inherent point in time, so
  "which stage snapshot applies" has no well-defined answer there the way
  it does for `turn_by_turn_checks`' actual, timestamped actions.

### Consequences
- Full suite: `134` passing (was `120`; `+14` — `test_boost_tracking.py`
  (7, ledger correctness against a recording fake), `test_boost_events_
  parsing.py` (4, parser-level), `test_calc_engine_boosts.py` (3, REAL
  Node-subprocess integration, matching the existing `test_calc_engine_
  gametype.py`/`test_calc_engine_mega_forme.py` pattern — a fake can't
  catch a break anywhere in the Python-payload/IPC-transport/JS-options
  chain). Every pre-existing test file unmodified.
- **Live-verified end to end** (parser → `TurnReplaySimulator` → the REAL
  Node `@smogon/calc` subprocess, not a script in isolation): an
  Intimidate-carrying Incineroar's switch-in measurably lowers a later
  Earthquake's projected damage from 22.2–26.7% to 14.8–17.8% against the
  same target in the same game — and `@smogon/calc`'s own `fullDesc()`
  string surfaces the exact stage applied ("-1 0 Atk Garchomp Earthquake
  vs. ..."), so the model has a directly citable number, not just a
  changed percentage to take on faith.
- `MatchupEvaluator.evaluate()`'s spotlight matchups remain stage-unaware
  (see Alternatives) — an explicitly accepted, narrower scope than
  `turn_by_turn_checks`, not an oversight.
- Could not verify the prompt wording's actual effect on a live model
  response in this sandbox (same local TLS trust-store issue as ADR-015/
  ADR-020) — the deterministic half (every number this ADR touches) is
  fully live-verified against the real engine; the prompt half should be
  judged in the user's own deployment.

### Files touched
`src/domain/models.py` (`PokemonSet.boosts`, `BattleEvent.kind` comment),
`src/adapters/parsers/showdown_parser.py`,
`src/services/matchup_evaluator.py` (`enrich_set`),
`src/services/turn_simulator.py`,
`src/adapters/calc/smogon_calc_adapter.py` (`_mon_payload`),
`node_calc/src/calcEngine.js` (`buildPokemon`),
`src/adapters/llm/prompts/explanation_system.txt`,
`tests/test_boost_tracking.py` (new), `tests/test_boost_events_parsing.py`
(new), `tests/test_calc_engine_boosts.py` (new).

---

## ADR-022 — A dedicated, self-wiring folder for background music

**Status:** Accepted · `feat/background-music`

### Context
The user asked for the option to add background music to the page,
expecting a dedicated drop folder for the audio file — the same shape of
ask ADR-017 already answered for background images, now for audio.

### Decision
- New folder: `src/ui/assets/audio/`, alongside
  `src/ui/assets/backgrounds/` — same rationale as ADR-017 for why this
  lives under the presentation layer rather than the repo-root `data/`
  (input data the deterministic/probabilistic layers read, not a UI
  asset).
- **No code edit required to use it.** `_find_audio_file()` looks for a
  single `theme.<ext>` file (`.mp3`, `.mp4`/`.m4a`, `.wav`, `.ogg`, tried
  in that order — first match wins if more than one is present);
  `_render_background_music()` renders it via Streamlit's own native
  `st.audio()` widget in the sidebar. An empty folder is the complete,
  working default — no player shown at all, not a gap.
- **`st.audio()` given the file path directly, no manual encoding.**
  Unlike the background-image feature (ADR-017), which has to hand-build a
  `data:` URI because a raw CSS `background` property has no other way to
  reach a local file, `st.audio()` is a first-class Streamlit widget that
  accepts and serves a local file path on its own — simpler, and nothing
  here needed the `st.cache_data`-keyed-by-mtime treatment ADR-017's image
  loader required: that was specifically about avoiding a base64-encode
  of a multi-MB file on every single Streamlit rerun (every stepper
  click); `st.audio()` doesn't force this project's own code to read the
  file into memory at all, so there's no equivalent cost to cache away.
- **Never autoplays.** Browsers block audio-with-sound autoplay before any
  page interaction regardless of what a framework requests, and forcing it
  on landing would be poor UX even on a browser that did allow it —
  `autoplay=False` simply matches reality and expected behavior. `loop=
  True` by default, since this is meant as ambient background music, not
  a one-shot clip.
- Documented in the new folder's own README (discoverable by anyone who
  opens it looking for "where do I put this") and in the top-level
  `README.md`'s customization section, alongside the background-image one
  — matching ADR-017's precedent of documenting in both places.

### Alternatives considered
- **Autoplay by default, muted, then unmute on first interaction** (a
  common pattern for background audio on marketing sites). Rejected:
  meaningfully more complexity (tracking "has the user interacted yet",
  wiring a mute/unmute transition) for a UX pattern that's arguably worse
  than just showing visible controls the visitor clicks once — especially
  for a coaching/analysis tool being used for focused reading, not a
  landing-page moment.
- **Hand-build an `<audio>` tag via `st.markdown(unsafe_allow_html=True)`**
  (matching the background-image feature's own data-URI approach).
  Rejected: `st.audio()` already does exactly this, natively, with a
  proper player UI, without needing raw HTML or base64 encoding — reaching
  past the framework's own built-in widget here would only add risk
  (the CommonMark/raw-HTML edge cases ADR-019 specifically had to design
  around) for no benefit.

### Consequences
- Full suite unchanged (`134` passing — no pytest coverage added, matching
  this project's no-UI-pytest convention). Verified instead with a
  dedicated script: drops a real, valid WAV file into the new folder,
  confirms `_find_audio_file()` detects it with the right MIME type, drives
  a real `AppTest` run confirming `st.audio()` renders with no exception,
  then removes the file and confirms the app still starts cleanly with no
  player at all.
- `src/ui/assets/audio/` currently ships empty (no track checked in) —
  deliberate, per Decision above, not an oversight.

### Files touched
`src/ui/app.py`, `src/ui/assets/audio/README.md` (new), `README.md`.

### Follow-up (same session): an explicit on/off checkbox

The user asked for a dedicated way to turn the music off, beyond the
player's own native pause control. Added a `"Background music"`
`st.checkbox` (default checked, real widget `key="music_enabled"` so it
persists across reruns on its own) right above the player — both the
checkbox and the player are skipped together when no track is present, so
this never shows an on/off control for a feature that isn't there.
Unchecking it removes the `st.audio` widget from the page entirely rather
than muting or pausing it in place — the most complete "off" available,
since this project holds no live handle to the browser's own audio
element between reruns to instruct it to pause instead.

Verified with a dedicated `AppTest`-driven script (a real checkbox
interaction, not a bare function call, using `AppTest.get("audio")` since
audio has no dedicated shortcut accessor the way button/checkbox do):
confirms the checkbox defaults to checked with the player rendered,
unchecking removes the player while the checkbox itself stays, and
re-checking brings it back — plus the no-track case still shows neither.

### Follow-up (same session): autoplay requested at the user's explicit ask

`st.audio(...)`'s `autoplay` flag flipped from `False` to `True`, per an
explicit follow-up request to have the track start as soon as the page
loads. Documented honestly rather than silently complying: this is a
*request* to the browser, not something this app or Streamlit can force
— browsers enforce their own autoplay-with-sound policy (e.g. Chrome's
Media Engagement Index) and commonly block it outright on a visitor's
first-ever visit to the site regardless of what the page asks for, only
relaxing it once they've interacted with the page/domain before. Nothing
server-side can detect or override that. The `"Background music"`
checkbox and the player's own visible controls remain the fallback for
whenever a browser does block it — unchanged from the prior follow-up,
now doing double duty as the recovery path for this one too.

No test added for the flag flip itself (a `True`/`False` constructor
argument, and the actual playback behavior it requests is a
browser-enforced runtime policy no test in this suite can observe) — the
existing on/off-checkbox `AppTest` script continues to cover the parts
that are actually testable (the widget renders/un-renders correctly).

---

## ADR-023 — Accept a Showdown replay URL, not just pasted JSON/log text

**Status:** Accepted · `feat/replay-url-input`

### Context
The user asked to improve the "paste a replay" input step so it also
accepts a Showdown replay URL directly — pointing at a concrete example:
`https://play.pokemonshowdown.com/battle-gen9championsvgc2026regmb-2661950350`
(the live/replay-viewer link a user actually copies from their browser)
resolves to `https://replay.pokemonshowdown.com/gen9championsvgc2026regmb-
2661950350.json` (the raw JSON), which is what the app already knows how
to parse — it just never had a way to get there from the first URL on its
own.

### Decision
- New adapter, `src/adapters/replay_url_fetcher.py`: `normalize_replay_
  json_url(text) -> str | None` (pure — recognizes the two URL shapes
  Showdown itself produces, `play.pokemonshowdown.com/battle-<id>` and
  `replay.pokemonshowdown.com/<id>[.json]`, both normalizing to the same
  canonical JSON endpoint; returns `None` for anything else, including
  pasted JSON/log text, so it's safe to call unconditionally on whatever
  the user pasted) and `fetch_replay_json(url, timeout) -> str` (one GET
  request, raising the new `ReplayFetchError` — never a raw `requests`
  exception — on any network failure, non-200 response, or empty body).
  Deliberately scoped to only these two Showdown hosts, not "try to fetch
  any http(s) URL as replay data" — a URL to something else fails fast and
  clearly instead of being silently treated as a replay source.
- New exception, `ReplayFetchError(ProfessorVGCError)` — deliberately
  distinct from the existing `LogParsingError`: a RETRIEVAL failure (the
  content was never obtained at all) is a different failure mode from
  content that was obtained but couldn't be parsed, and the UI's existing
  `_error_tip()` gives each its own tailored hint.
- Kept as a standalone adapter, not a new `Protocol`/DI-registered port
  the way `LogParser`/`CalcEngineAdapter`/etc. are. Unlike those, this
  logic has no swappable implementations to abstract over (there's only
  one Showdown), and it exists purely to pre-process the "paste a replay"
  input before EITHER existing parse path (the LLM pipeline's
  `ShowdownReplayParser` and the UI's independent `replay_viewer_parser`)
  runs — not a new capability either of those parsers needs to know about.
- Wired into `src/ui/app.py`'s Analyze handler, inside the existing
  loading-overlay window (ADR-018) and BEFORE both `parse_replay_for_
  viewer` and the `AnalysisRequest` are built: if the pasted text
  normalizes to a replay URL, it's fetched and the fetched JSON — not the
  raw URL text — becomes what both parsers and the LLM request receive.
  A fetch failure short-circuits into the exact same `last_error`/
  `last_result` session-state path every other failure already uses,
  so the existing error-rendering code needed no changes.
- Text-area label/placeholder updated to mention the URL option, with the
  user's own reported URL shape as the example.

### Alternatives considered
- **A full `ReplaySource` `Protocol` + container-registered adapter**,
  matching the pattern for `LogParser`/`CalcEngineAdapter`/etc. Rejected:
  those ports exist because this project genuinely swaps implementations
  (openai/gemini, native/langchain, Chaos-derived/official Smogon) —
  there's no analogous "swap the replay source" need here, and adding a
  Port for a single fixed external service would be complexity with no
  corresponding flexibility gained.
- **Accept a bare replay ID** (just `gen9championsvgc2026regmb-2661950350`,
  no URL) as a third input shape. Rejected as unrequested scope creep, and
  riskier: a bare alphanumeric-hyphen-numeric string is far more likely to
  accidentally collide with something inside pasted raw log text than a
  full `https://` URL is, which the normalizer would then need extra logic
  to rule out.
- **Try to fetch ANY `http(s)://` URL pasted in, not just the two Showdown
  hosts.** Rejected: a non-Showdown URL isn't a replay source at all —
  fetching it would either fail confusingly deep inside JSON parsing (a
  worse error than "not a recognized replay URL") or, worse, silently
  "succeed" by fetching arbitrary attacker-controlled content into the
  replay pipeline. Scoping to the two known Showdown hosts closes that off
  entirely.

### Consequences
- Full suite: `150` passing (was `134`; `+17`, with `1` skipped — `test_
  replay_url_fetcher.py`: pure `normalize_replay_json_url` tests (no
  network), `fetch_replay_json` tests against a mocked `requests.get`
  covering success/404/empty-body/network-exception, and one LIVE test
  against the real reported URL that skips — not fails — when the network
  is unavailable, matching `test_calc_engine_gametype.py`'s established
  pattern). Also verified with a dedicated end-to-end `AppTest` script
  (mocked fetch + fake pipeline, no network/keys) confirming the pasted
  URL is normalized to the exact right endpoint, the LLM pipeline receives
  the FETCHED content rather than the raw URL text, and the independent
  battle-panel parser does too.
- **Live-verified against the user's own exact reported URL** before
  writing any UI code: confirmed live via `curl` (bypassing this
  sandbox's known-broken local CA trust store, same class of issue as
  ADR-015/ADR-020/ADR-021) that the derived JSON endpoint returns HTTP 200
  with real replay content, then fed that real content through BOTH
  `ShowdownReplayParser` and `replay_viewer_parser` directly — both
  succeeded end to end (winner, sides, snapshots all resolved correctly)
  — before any of the UI wiring was written, so the feature was proven
  against genuine data first, not assumed to work from the normalizer
  tests alone.
- Pasting a URL whose fetch fails now shows a specific, actionable error
  (via `_error_tip`'s new `ReplayFetchError` branch) instead of the
  generic parse-failure message a raw URL string would previously have
  produced by falling straight into `ShowdownReplayParser`/
  `parse_replay_for_viewer` and failing to parse as either JSON or a log.

### Files touched
`src/adapters/replay_url_fetcher.py` (new), `src/domain/exceptions.py`
(`ReplayFetchError`), `src/ui/app.py`, `tests/test_replay_url_fetcher.py`
(new).

---

## ADR-024 — Archetype awareness: fix a dead ability-signal bug, surface Mega Evolution as ground truth

**Status:** Accepted · `fix/archetype-aware-explanation`

### Context
User report with a real replay and the actual generated answer: a Gengar
+ Archaludon vs. Raichu game where the explanation said "the decisive
moment was Raichu's Focus Blast, which secured the knockout of
Archaludon... forced p1 [to] forfeit" — accurate as a narration of events,
but missing WHY: by the user's own read, the team's plan hinged on
Archaludon as its wallbreaker behind a Perish-Trap-style core (Gengar
traps via Shadow Tag after Mega Evolving), so losing it left no path
forward — a "so what" the raw event sequence alone doesn't convey, but
the archetype/strategy data this project already gathers should support
explaining.

Investigated the actual data flow (not guessed) before writing anything:
`SmogonStrategy.archetypes` already existed and was already fed into
every explanation prompt (`strategies: [s.model_dump() for s in
strategies]`) — so the pipeline was already *trying* to give the model
this. Two independent, concrete gaps explain why it didn't help here:

1. **A genuinely dead signal.** Both strategy adapters'
   `_infer_archetypes` scanned only MOVE names for archetype signals — one
   of them (`ChaosStrategyAdapter`) even had a `"shadowtag": Archetype.
   PERISH_TRAP` entry in that move-name table, which could never match
   anything, since Shadow Tag is an ABILITY, never a move. Confirmed live
   against this project's own loaded Chaos data for this exact format:
   Gengar's tracked abilities there never include Shadow Tag at all in any
   loaded tier (`cursedbody` is the only entry in every tier) — an
   independent, separate limitation of how Chaos usage stats capture an
   ability only gained via in-battle Mega Evolution, not something a code
   fix here can correct, but exactly why the SECOND fix below matters more
   for this specific case.
2. **The decisive fact itself was invisible.** `-mega`/`detailschange` log
   lines were already parsed — but only into `PokemonSet.battle_formes`,
   silently, for the calc's stat-approximation caveat. Nothing ever put
   "Gengar Mega Evolved" into `battle_result`'s ordered timeline, the ONE
   context block the prompt calls GROUND TRUTH. Live-verified: re-parsing
   the user's exact reported replay showed a Chaos-derived `archetypes`
   tag of `balance` for Gengar (matching limitation #1 above) — but the
   REAL fact the model actually needed, "Gengar transformed into
   Gengar-Mega," was simply never in its context at all before this fix.

### Decision
- **New shared module**, `src/adapters/smogon/archetype_signals.py`:
  `infer_archetypes(moves, abilities)` — one implementation, not the two
  near-identical, already-drifted `_ARCHETYPE_SIGNALS` copies each adapter
  carried (the same duplication smell ADR-013 flagged for spread parsing).
  Move signals unchanged (Perish Song, Trick Room, Tailwind, setup moves,
  Protect/Fake Out) minus the dead `shadowtag` entry; a NEW ability-signal
  table (Shadow Tag, Arena Trap, Magnet Pull → `PERISH_TRAP`) closes the
  actual bug. Both `ChaosStrategyAdapter` and `SmogonDexAdapter` now pass
  their top abilities through alongside moves.
- **`showdown_parser.py`'s `detailschange` handler now ALSO appends a
  `kind="forme_change"` `BattleEvent`** (`"{player} {key} transformed
  into {species}."`) into the same ordered timeline switches/faints/
  abilities/boosts already flow through — automatically GROUND TRUTH per
  the prompt's existing framing, no prompt restructuring needed for the
  fact itself to appear. The pre-existing silent `battle_formes`
  bookkeeping is untouched (still feeds the calc stat-caveat).
  `TurnReplaySimulator.simulate()`'s event loop already only processes
  `"move"` after handling `"switch"`/`"boost"`, so the new kind is inert
  there by construction — verified with a dedicated test, not just
  assumed.
- **New prompt rule**: a "transformed into" timeline line is a CONFIRMED
  forme change, and the model MAY apply its own general knowledge of that
  specific forme's real mechanics (ability, typing, stats — e.g. Mega
  Gengar's ability being Shadow Tag) to explain what changed and why it
  mattered. Explicitly distinguished from the existing "never invent a
  move/set not in the data" rule: a forme's defining mechanics are public
  game knowledge applied to a confirmed, timeline-stated fact, not a guess
  about this specific Pokemon's unconfirmed set.
- **New guidance**: when a single KO or a forfeit reads as the deciding
  moment, explain WHY beyond "it fainted" — cross-reference that Pokemon's
  own `archetypes`/`overview`/`common_teammates` and any confirmed forme
  change against what the rest of the team did, and say so when the data
  actually supports identifying the team's core piece. Also documents what
  the existing `"balance"` archetype tag means (no strong signal either
  way) so the model doesn't overstate it into a specific plan.

### Alternatives considered
- **Only fix the archetype-signal bug, skip the timeline event.** Rejected
  once live-verification showed it wouldn't actually change the outcome
  for the exact reported case — Chaos's own ability-usage tracking simply
  doesn't capture Gengar's Mega-Evolution-only Shadow Tag in the loaded
  data for this format, an independent limitation. The archetype-signal
  fix is real and worth keeping (it will correctly tag species whose
  trapping ability isn't Mega-dependent, e.g. Arena-Trap Dugtrio), but
  isn't sufficient alone.
- **Try to merge the replay-observed Mega Evolution into `SmogonStrategy`
  server-side** (e.g. force an archetype tag onto a species whenever a
  forme change is observed). Rejected: `SmogonStrategy` is explicitly
  PROBABILISTIC, metagame-wide data (CLAUDE.md's core determinism-vs-
  probability separation) — baking a single game's observed fact into it
  would blur that boundary. The right layer for a single game's own fact
  is the deterministic timeline, which is exactly where the forme-change
  event now lives; connecting it to what the forme's mechanics imply is
  the kind of synthesis this project's own working principle (ADR-010)
  already delegates to the explanation model, given the right ground truth.
- **Let the model infer forme changes from move/ability lists alone**
  (e.g. notice Gengar suddenly has access to Shadow Tag-flavored framing
  without an explicit timeline line). Rejected: silently expecting the
  model to notice an implicit signal is exactly the shape of gap ADR-020/
  021 already fixed for rosters and abilities — state the fact plainly in
  ground truth rather than hope it's inferred correctly.

### Consequences
- Full suite: `162` passing (was `150`; `+12` — `test_archetype_signals.py`
  (8, including a `ChaosStrategyAdapter`-level test with a synthetic
  Shadow-Tag trapper), `test_forme_change_events.py` (4)). Every
  pre-existing test file unmodified.
- **Live-verified against the user's own exact reported replay**, the
  full non-LLM context-construction pipeline (parser → `outcome_summary`
  → `collect_strategies` against this project's real, loaded Chaos data
  for this format): confirmed the rebuilt `battle_result` now explicitly
  states "p1 Gengar transformed into Gengar-Mega." and "p2 Raichu
  transformed into Raichu-Mega-Y." in the ordered timeline, alongside the
  roster/ability/boost ground truth ADR-020/021 already added — and
  confirmed, honestly, that the archetype-tag fix alone does NOT flip
  Gengar's tag to `perish_trap` for this specific game (Chaos's own data
  limitation, documented above), so the forme-change timeline fix is the
  one actually carrying this report's fix, not the archetype-signal one.
- Could not verify the prompt wording's actual effect on a live model
  response in this sandbox (same local TLS trust-store issue as ADR-015/
  020/021/023) — the deterministic half (every fact this ADR adds to
  context) is fully live-verified against the real reported replay and
  this project's real Chaos data; the prompt half should be judged in the
  user's own deployment against the same replay.
- `smogon_dex_adapter.py` and `smogon_strategy_adapter.py` both shrank
  (each lost its own `_ARCHETYPE_SIGNALS`/`_infer_archetypes`/`_norm`
  copy) in favor of the one shared module — less code, and the two
  providers' archetype taxonomy can no longer drift apart the way it
  already had (one had `shadowtag`, the other didn't).

### Files touched
`src/adapters/smogon/archetype_signals.py` (new),
`src/adapters/smogon/smogon_strategy_adapter.py`,
`src/adapters/smogon/smogon_dex_adapter.py`,
`src/adapters/parsers/showdown_parser.py`, `src/domain/models.py`
(`BattleEvent.kind` comment), `src/adapters/llm/prompts/
explanation_system.txt`, `tests/test_archetype_signals.py` (new),
`tests/test_forme_change_events.py` (new).

---

## ADR-025 — CI (pytest + mypy on every PR), and a genuinely green suite regardless of install path

**Status:** Accepted · `feat/ci-and-clean-typecheck`

### Context
Direct feedback, two findings: (1) no `.github/workflows/` at all — 162
tests existed but nothing ran them automatically on a PR; (2) the test
suite did NOT actually pass 100% out of the box in every documented
install path — `test_langchain_orchestrator.py`/`test_langchain_provider.py`
import `langchain_core` at module level, so an environment missing it
fails those two files at COLLECTION time (an error, not the clean skip
the README's "runs with fakes, no Node or API keys required" line implies)
— a real contradiction between documentation and actual behavior in a
project whose own CLAUDE.md holds every other layer to a "no placeholder
code" standard.

### Decision
- **New `.github/workflows/ci.yml`**: a `test` job (matrix: Python 3.10 —
  the `pyproject.toml` floor — and 3.12) that also sets up Node and runs
  `npm install` in `node_calc/` before `pytest`, so the calc-engine
  integration tests (`test_calc_engine_gametype.py`/`test_calc_engine_
  mega_forme.py`/`test_calc_engine_boosts.py`) exercise the REAL
  `@smogon/calc` subprocess in CI rather than hitting their own
  environment-unavailable skip path — a stronger signal than a CI run
  that quietly skips its own integration coverage. A separate `typecheck`
  job runs `mypy src`. Triggers on every `pull_request` (any target
  branch) and every `push` to `master`.
- **Before wiring `mypy` into CI, actually ran it** — `mypy --strict src`
  had never been run in this project's history and reported 65 errors
  across 16 files. Adding a check that starts permanently red is a worse
  signal than no check at all, so this ADR includes fixing all 65: real
  missing/incomplete annotations (function signatures, generic type args
  like bare `dict`/`list`/`Sequence`), two genuine latent bugs caught in
  the process (`node_ipc.py`/`smogon_calc_adapter.py`'s IPC response
  parsing returned whatever `json.loads` produced with NO shape check —
  a non-dict response from the Node subprocess would have propagated
  silently instead of raising `CalcEngineError`; now validated), a real
  type-correctness fix (`ChatOpenAI`'s `api_key` now wrapped in
  `SecretStr`, matching its actual accepted type, not just satisfying
  mypy), and a small number of narrow, comment-justified `# type:
  ignore[...]` for genuine third-party stub gaps (`google-generativeai`
  not exporting `configure`/`GenerativeModel` from its package root
  despite both being real, stable public API — verified, not assumed).
  `TYPE_CHECKING`-only imports (`langchain_core.language_models.
  BaseChatModel`, `openai.types.chat.ChatCompletion`, etc.) restore real
  typing for these values without turning any of this project's
  deliberately-lazy optional-dependency imports into hard, top-level ones
  — the exact same "degrade gracefully" property finding #2 below
  depends on was never compromised to satisfy mypy.
- **Fix #2, both remedies from the report, not just one**: `test_
  langchain_orchestrator.py`/`test_langchain_provider.py` now open with
  `pytest.importorskip("langchain_core")` before their own module-level
  imports (matching this project's existing `test_calc_engine_*.py`
  convention for Node-unavailable environments — the exact same idiom,
  just applied to the one gap that didn't have it yet), AND `pyproject.
  toml`'s `dev` extra changed from a bare `["pytest", "mypy"]` (missing
  everything these two files' own imports need) to `["pytest", "mypy",
  "professorvgc[openai,gemini]"]` — a self-referential extra (verified
  live with `pip install -e ".[dev]" --dry-run` — pip 26.2 resolves it
  correctly) that makes `pip install -e ".[dev]"` alone a complete,
  self-sufficient test environment, matching what `pip install -r
  requirements.txt` already provided. `requirements.txt` also gained
  `mypy>=1.9` (previously only in `pyproject.toml`'s now-fixed `dev`
  extra) and `pyproject.toml`'s base `dependencies` gained `requests>=2.31`
  (already required by `src/ui/app.py` since ADR-015, but never declared
  there) — closing a second, smaller instance of the same
  docs-vs-`pyproject.toml` drift class this whole ADR is about.

### Alternatives considered
- **Just add the two `importorskip` guards, leave `pyproject.toml`'s `dev`
  extra as-is.** Rejected: doesn't actually fix `pip install -e ".[dev]"`
  as a viable, complete setup path — it would still work (tests skip
  cleanly now instead of erroring), but a `dev` extra that can't run the
  full test suite for real is itself a smaller instance of the same
  documentation-vs-reality gap being fixed here.
- **Skip fixing the 65 mypy errors; ship `mypy` in CI with `continue-on-
  error: true` or scoped to only the newest modules.** Rejected: either
  produces a check that LOOKS like type safety but doesn't enforce it (a
  permanently-soft-failing or partial check is arguably a worse signal to
  a careful reviewer than no check, since it looks like coverage that
  isn't real), and the real fix — while larger — surfaced two genuine
  latent bugs (see Decision) that a soft-landed version would have left
  buried.
- **Silence the third-party stub gaps with a blanket per-module `# type:
  ignore` or a `[[tool.mypy.overrides]]` block disabling checking for
  `gemini_provider.py`/`openai_provider.py` entirely.** Rejected: both
  files have real logic worth type-checking (parameter/return shapes,
  the message-building code) alongside the handful of SDK-stub-specific
  lines; a whole-file exemption would have thrown away checking on
  everything else in those files too. Narrow, per-line, commented ignores
  keep the exemption exactly as small as the actual gap.

### Consequences
- Full suite: `162` passing, `1` skipped in this environment (langchain_
  core installed here) — verified separately, live, in a genuinely
  langchain_core-unavailable simulation (a `sys.meta_path` finder that
  raises `ModuleNotFoundError` for that one package, run in a subprocess
  so it can't affect this session's own interpreter): `155` passing, `3`
  skipped, exit code `0` — a real, reproduced confirmation that the
  reported contradiction is closed, not just reasoned about.
- `mypy src` — `Success: no issues found in 44 source files`. This is now
  a real, enforced baseline: any future change that removes an annotation,
  reintroduces a bare generic, or breaks one of the `TYPE_CHECKING`-only
  import boundaries fails CI, not just a local, easy-to-skip habit.
- The two IPC response-shape bugs found and fixed while chasing `no-any-
  return` errors (`node_ipc.py`, `smogon_calc_adapter.py`) were latent,
  not exercised by this project's own tests before (both use in-memory
  fakes that only ever return well-shaped dicts) — a concrete example of
  strict type-checking catching something the existing test suite
  structurally could not.
- README's Setup/Tests sections updated to mention both install paths
  (`requirements.txt` and `pip install -e ".[dev]"`) and `mypy src`
  explicitly, plus a CI status badge.

### Files touched
`.github/workflows/ci.yml` (new), `pyproject.toml`, `requirements.txt`,
`README.md`, `tests/test_langchain_orchestrator.py`,
`tests/test_langchain_provider.py`, and (mypy-clean fixes only, no
behavior changes beyond the `SecretStr` wrap noted above)
`src/services/suggestion_service.py`, `src/adapters/chaos/
chaos_repository.py`, `src/adapters/node_ipc.py`, `src/adapters/parsers/
replay_viewer_parser.py`, `src/adapters/calc/smogon_calc_adapter.py`,
`src/services/selection_service.py`, `src/services/matchup_evaluator.py`,
`src/services/turn_simulator.py`, `src/services/analysis_service.py`,
`src/adapters/llm/langchain_tools.py`, `src/adapters/llm/
openai_provider.py`, `src/adapters/llm/gemini_provider.py`, `src/adapters/
llm/langchain_provider.py`, `src/services/langchain_orchestrator.py`,
`src/services/container.py`, `src/ui/app.py`.

---

## ADR-026 — Port a Figma-designed visual language onto the Streamlit UI (light "battle notebook" theme)

**Status:** Accepted · `feat/design-port-lab-notebook`

### Context
A separate Figma Make prototype (`OracleFrontEndExample/`) was built for
this project as a design reference: React 19 + Vite + Tailwind v4, a single
mocked `App.tsx` with no real backend — a light sky-blue "battle notebook"
palette (ink-navy text, blue/green/gold/purple accents), Lora + Nunito +
Space Mono typography, glass-morphism cards, an animated canvas background
(a graph-paper grid with "Pokémon equations" writing themselves), a
staged loading screen, and color-coded insight cards. The request was to
reuse it to improve this project's actual front end.

The two stacks are not interoperable at the code level — Streamlit
(server-rendered Python, no React runtime) cannot execute the prototype's
JSX/TypeScript components. Confirmed with the user (AskUserQuestion, three
framed options: port the design into Streamlit / adopt React as the real
frontend behind a new API / run both in parallel) which register of reuse
was wanted; **port the design system into the existing Streamlit app** was
chosen — keeps the current architecture, deployment, and zero-risk
separation from the LLM pipeline (per ADR's opening "battle panel plan"
constraint) fully intact.

### Decision
- **`.streamlit/config.toml`** (new): a `[theme]` block (`base="light"`,
  `primaryColor="#2563a8"`, ink-navy `textColor`) — Streamlit's own
  documented theming mechanism drives every native widget (buttons,
  sliders, text inputs, expanders, alerts) correctly and consistently,
  instead of hand-overriding dozens of internal Streamlit CSS classes one
  by one (fragile across Streamlit versions; this project is already on
  1.60).
- **Design tokens** (`_DESIGN_TOKENS_CSS` in `app.py`): the prototype's
  actual palette and type roles as CSS custom properties (`--pvgc-ink`,
  `--pvgc-blue`, `--pvgc-green`, `--pvgc-gold`, `--pvgc-purple`,
  `--pvgc-font-display/body/mono`), loaded once via a Google Fonts
  `@import` (Lora/Nunito/Space Mono) and referenced everywhere below rather
  than repeating literal hex values across this file's many `_*_html()`
  builders.
- **Component theming** (`_COMPONENT_THEME_CSS`): applies the tokens to
  Streamlit's stable `data-testid` hooks — headings get the display font,
  captions and widget labels get the mono font (uppercase, letter-spaced,
  matching the prototype's input labels), expanders and bordered
  containers become frosted "glass cards" (translucent white,
  `backdrop-filter: blur`, soft blue border), the sidebar gets the same
  glass treatment, and the primary "Analyze" button gets the prototype's
  navy→blue gradient CTA styling.
- **Page background swap**: `_PAGE_BACKGROUND_CSS_DEFAULT` changed from
  the previous dark teal "research lab" gradient to a light sky-blue
  gradient plus a faint graph-paper grid — a static/CSS stand-in for the
  prototype's animated canvas (see Alternatives below for why the canvas
  itself wasn't ported). The battle-replay panel's OWN backdrop
  (`_LAB_BACKGROUND_CSS_DEFAULT`) deliberately stays dark and unchanged —
  it already reads as "a different, darker room of the same facility" per
  its original ADR-015 rationale, and its HP boxes/side-headers are styled
  for a dark backdrop regardless of the page theme around it.
- **User-dropped background image scrim fixed for both themes**:
  `_background_css()` previously hardcoded one dark scrim for any
  user-dropped image at either backdrop. Now takes a `scrim` parameter —
  the battle stage keeps the dark scrim, the page gets a new light-blue
  scrim — because switching the page's default text color to ink-navy
  would otherwise make a user's dropped photo (still darkened by the old
  scrim) illegible under dark-on-dark text. A real correctness fix this
  port required, not just an aesthetic choice.
- **Loading overlay rebuilt**: the prototype's staged loading screen
  (pulsing ring, rotating status captions, progress dots) reproduced as
  **pure CSS keyframe animations**, not the original's `setInterval`-driven
  React state — script tags injected via `st.markdown(unsafe_allow_html=
  True)` are not reliably executed (Streamlit renders that HTML via
  `innerHTML`, and browsers do not execute dynamically-inserted `<script>`
  tags from that path), so a direct JS port would have silently done
  nothing. Every caption/dot span shares one keyframe rule and duration,
  differing only by `animation-delay`, so they take turns being visible
  with zero JavaScript — verified by inspecting the generated CSS
  directly (`{i * step}s` offsets, matching `%` windows) since there is no
  way to screenshot a running Streamlit session in this environment.
  Keeps the existing monochrome pokéball sprite (explicitly requested
  earlier, not colored) rather than switching to the prototype's red/white
  one.
- **Hero header + footer**: the prototype's sticky nav (logo, name, a
  pulsing "AI ready" status dot) and minimal footer (fan-tool disclaimer)
  ported as normal-flow HTML blocks — not `position: sticky`, since a
  truly sticky child of Streamlit's own scroll container is unreliable
  across versions.
- **Follow-up (same PR, before merge): the idle-state marketing content**.
  First pass ported only the palette/typography/component chrome and
  skipped the prototype's hero copy, tag pills, feature-card grid (with
  its hand-drawn Garchomp/Mewtwo/Pikachu icons), decorative grass row, and
  scattered-equations background texture — judged out of scope for a
  functioning tool's real page. Direct feedback (two screenshots of the
  actual rendered prototype) called this "mediocre, well short of what was
  planned" — the richer landing content was in fact wanted, not just the
  token system. Added: `_hero_section_html()` (headline, subtitle, tag
  pills — copy corrected to what this project actually does: turn-by-turn
  verified damage, ground-truth-locked LLM narration, Chaos + official
  Smogon metagame — not the prototype's generic mock copy), `_feature_
  cards_html()` (the three feature cards, with the ported mascot SVGs
  inlined directly rather than as `_POKEBALL_ICON`/`_PIKACHU_ICON`-style
  data URIs, since they're only ever used in one HTML context each),
  `_grass_row_html()` (ported almost verbatim; its per-blade jitter was
  client-side `Math.random()` — reproduced with a `random.Random(42)`
  locally-seeded generator instead, so the layout is stable across
  Streamlit reruns rather than jittering on every widget interaction),
  and `_ambient_background_html()` (a fixed, click-through, deterministically
  -positioned layer of faint formulas standing in for the prototype's
  animated canvas — using this project's OWN real formulas, e.g. the
  actual damage formula and the 508 EV cap, not decorative flavor text).
  All four are gated on a new `is_idle` flag (`session_state` has no
  `last_replay`/`last_result` yet) so they disappear once a real analysis
  exists, matching the prototype's own `phase === 'idle'`-only rendering
  and keeping the real Answer/battle panel from being pushed further down
  the page on every subsequent visit.

### Alternatives considered
- **Adopt the React prototype as the real frontend** (rejected, per the
  user's own choice among the three framed options): would need a new
  JSON API layer wrapping the existing pipeline, a second deploy target,
  and would replace a tested, working presentation layer for the sake of
  its aesthetics alone — the fastest, lowest-risk way to get "this
  design" is to paint it onto the layer that already works, not rebuild
  the layer.
- **Port the animated canvas background verbatim** (rejected): the
  prototype's `requestAnimationFrame` canvas (floating particles, a
  typewriter effect over `Σ DMG(t) = ATK × TYPE_EFF × STAB`-style
  equations) needs real JS execution, which — per the loading-overlay
  finding above — `st.markdown`'s injected HTML does not reliably provide.
  `st.components.v1.html` can run real JS in a sandboxed iframe, but only
  as a fixed-size embedded box, not a page-wide `position: fixed`
  backdrop sitting behind Streamlit's own chrome — fighting that iframe
  boundary for a purely decorative background was judged not worth the
  fragility. The static gradient + grid keeps the visual identity
  (sky-blue, graph-paper) without the unreliable mechanism.
- **Custom color-coded "insight cards" replacing `st.info`/`st.warning`**
  (rejected): the prototype's Strength/Watch Out/Key Play cards are a nice
  pattern, but this file's existing `st.info`/`st.warning` calls rely on
  Streamlit's own Markdown rendering for inline code spans and bold text
  (e.g. `` `PROFESSORVGC_CHAOS_DATA_PATH` ``); replacing them with hand-built
  HTML would mean manually reimplementing that formatting and re-escaping
  every interpolated value (Pokémon species names, etc.) with no
  proportionate payoff. Left as native Streamlit alerts, which already
  pick up sensible theme-driven coloring from the new light base theme.

### Consequences
- Zero changes to any adapter/service/domain file — this is presentation
  only (`src/ui/app.py`, `.streamlit/config.toml`). `pytest -q` (162
  passed, 1 skipped) and `mypy --strict src` (0 issues) both unaffected,
  matching this project's existing convention that the UI carries no
  pytest coverage of its own (verified instead via `streamlit.testing.v1.
  AppTest`, live rendering of every new `_*_html()` builder to check for
  malformed CSS/unresolved template braces, and a published Artifact
  preview of the actual extracted CSS applied to representative content,
  for visual sign-off neither pytest nor `AppTest` can give).
- The page's overall look flips from dark teal to a light sky-blue theme —
  a real, visible identity change, done deliberately per the user's
  request to reuse the prototype's actual design rather than adapt it to
  the previous dark palette.

## ADR-027 — Semantic retrieval over official Smogon analysis prose (RAG, scoped correctly)

**Status:** Accepted · `feat/semantic-strategy-retrieval`

### Context
Direct question, in two parts: (1) does this app's conversation memory need
to become RAG-based instead of the current "replay the full history every
turn" design; (2) if not there, where — if anywhere — would retrieval
actually earn its keep in this project.

On (1): conversation memory here is one short thread about ONE battle —
at most a few dozen short messages, comfortably inside any modern LLM's
context window. RAG solves a different problem (a large, mostly-irrelevant
corpus that doesn't fit in context); reaching for it on a bounded,
single-topic thread would be solving a scale problem this project doesn't
have, and reads as reaching for a trendy technique over engineering
judgment. **Decision: conversation memory stays exactly as it is** (`src/
adapters/memory/conversation_memory.py`, unchanged) — no code in this ADR
touches it.

On (2): `SmogonDexAdapter.get_strategy()` (the official-Smogon strategy
source) had a real, previously-undiagnosed gap. `@pkmn/smogon`'s
`analyses()` call returns EVERY format Smogon has published analysis for a
species, each with its own `overview`/`comments`, and — per `node_calc/
src/smogonDex.js`'s own `mapAnalysis()`, already being returned over IPC —
a `description` per individually named SET. `get_strategy()` only ever read
`analyses[0]` ("newest/most relevant format first", a fixed heuristic) and
only that entry's `overview`/`comments`, never any set's own `description`,
never any other format. A question like "how does it handle Trick Room" got
whatever the first format's general overview happened to say, not the
passage that actually discusses that. This IS the right-shaped problem for
retrieval: multiple candidate passages of real prose, ranking by relevance
to the actual question, already sitting unread in an existing API response.

### Decision
- **`StrategyKnowledgeProvider.get_strategy`** gains an optional `question:
  str | None = None` parameter (domain port, `src/domain/interfaces.py`).
  Every existing implementer (`ChaosStrategyAdapter`, `SmogonDexAdapter`,
  `CompositeStrategyProvider`) accepts and either ignores it (Chaos has no
  free-text to rank) or forwards it. `matchup_evaluator.collect_strategies`
  and both orchestrators (`analysis_service.py`/`langchain_orchestrator.py`)
  thread `request.question` through. Never changes a strategy's STRUCTURED
  fields (`common_sets`/`archetypes`) — only ever narrows which prose becomes
  `overview`; the deterministic/probabilistic separation (CLAUDE.md §2.1)
  stays intact, retrieval only touches the narrative layer.
- **New `EmbeddingProvider` port** (`src/domain/interfaces.py`) + two BYOK
  adapters, `OpenAIEmbeddingProvider`/`GeminiEmbeddingProvider` (`src/
  adapters/llm/*_embedding_provider.py`), mirroring the existing chat
  provider adapters exactly (`text-embedding-3-small` / `models/text-
  embedding-004`, same key the user already configured for chat — no new
  credential to manage).
- **New `SemanticStrategyRetriever`** (`src/adapters/smogon/
  semantic_strategy_retriever.py`), a `StrategyKnowledgeProvider` decorator
  around `SmogonDexAdapter`: chunks every analysis's overview+comments AND
  every set's own description (via a new `SmogonDexAdapter.
  get_analyses_raw()`), embeds each chunk plus the question, ranks by cosine
  similarity, and builds `overview` from only the top-k passages (default 3),
  tagged with their real format/set provenance. `common_sets`/`archetypes`
  are aggregated across ALL formats' sets, not just the top-ranked one's —
  strictly broader than `SmogonDexAdapter.get_strategy()`'s own default
  (analysis `[0]` only) as a side benefit.
- **Deliberately NOT a vector database.** The corpus per species is a
  handful of short paragraphs, not a large document store — an in-memory
  `list[_Chunk]` plus a ~10-line pure-Python cosine similarity (`math`
  only, no numpy/faiss/pinecone/chroma) is the right-sized implementation.
  Pulling in a vector-database dependency for a dataset this small would be
  the same "solving a scale problem that doesn't exist" mistake identified
  in part (1), just in a place that superficially looks more RAG-shaped.
- **Cached per (species, metagame) inside the retriever**, and the retriever
  itself is cached per LLM-provider name inside `Container` (new `_semantic_dex`/
  `_semantic_retrievers`) — a session's chunk embeddings are computed once
  per species ever asked about, not once per follow-up question, since
  `Container.build_native_pipeline`/`build_langchain_pipeline` (and
  therefore a fresh `strategy()` call) run on every single `analyze()` — see
  `test_chunk_index_is_cached_across_questions_for_the_same_species`.
  Verified precisely: 1 batched embed call for N document chunks + 1 call
  per question asked, never N+1 per question.
- **Graceful degradation, twice over**: no `question` provided -> skips
  straight to `SmogonDexAdapter.get_strategy()` (nothing to rank against);
  any failure in the embedding path (missing key, network, quota, no
  indexable prose at all) -> the SAME fallback, never a new way for this
  optional enhancement to break the pipeline. `SmogonStrategy.retrieval_note`
  (new, informational-only field) records which path actually ran, for the
  UI's existing "Strategies" debug expander — deliberately excluded from
  what the LLM prompt sees (`build_explanation_context` now dumps strategies
  with `exclude={"retrieval_note"}`), so the explanation narrates the
  battle, never its own retrieval plumbing.
- **Opt-in**: `PROFESSORVGC_USE_SEMANTIC_STRATEGY` (default `false`),
  requires `PROFESSORVGC_USE_SMOGON_DEX=true` (nothing to retrieve over
  otherwise). `PROFESSORVGC_{OPENAI,GEMINI}_EMBEDDING_MODEL` and
  `PROFESSORVGC_SEMANTIC_STRATEGY_TOP_K` are tunable.

### Alternatives considered
- **Vector-search the conversation history too** (rejected — see Context):
  wrong-shaped problem for this project's actual conversation length.
- **A hosted vector database** (Pinecone/Chroma/Weaviate) for the Smogon
  passages (rejected): real infra + a new external dependency for a corpus
  that fits in a Python list; the in-memory approach costs nothing extra
  to deploy and is exactly as correct at this scale.
- **Re-embed on every question** (rejected): wasteful and slower for a
  multi-question conversation about the same Pokemon; per-retriever caching
  (scoped to the container, not per-request) removes the redundant work
  entirely for the common case without adding a persistence layer.

### Consequences
- No changes to `conversation_memory.py`, `ConversationMemory`, or anything
  the earlier discussion actually flagged as a real gap there (unbounded
  growth) — that remains open, tracked separately, not silently
  substituted by this ADR's unrelated retrieval work.
- New test coverage: `tests/test_semantic_strategy_retriever.py` (7 tests,
  a deterministic bag-of-words `FakeEmbeddingProvider` proving real ranking
  behavior — a passage that actually mentions "trick room" outranks one that
  doesn't, not just "the code runs"), `tests/test_embedding_providers.py`
  (2, constructor validation, no network — same convention as the untested-
  by-design `OpenAIProvider`/`GeminiProvider` chat adapters), `tests/
  test_container_semantic_strategy.py` (4, composition-root wiring:
  enabled/disabled/dex-off/cached-per-provider). `pytest -q`: 175 passed,
  1 skipped (was 162). `mypy --strict src`: 0 issues across 47 files (was
  44) — new files fully typed from the start, no retrofit needed.
- Real network/API verification isn't possible in this sandbox (the
  standing `CERTIFICATE_VERIFY_FAILED` limitation noted in earlier ADRs);
  the ranking LOGIC is verified precisely via the fake, but a live
  OpenAI/Gemini embedding call should be exercised once in the user's real
  deployment before relying on it.

## Cross-cutting notes

- **Test discipline.** Every ADR above shipped with new pytest coverage
  (`tests/test_bench_only_exclusion.py`, `tests/test_matchup_no_fabricated_moves.py`,
  `tests/test_turn_simulator_best_alternatives.py`, `tests/test_forme_caveat.py`,
  `tests/test_protect_block_detection.py`, `tests/test_calc_engine_gametype.py`,
  `tests/test_protect_reads.py`, `tests/test_calc_engine_mega_forme.py`,
  `tests/test_langchain_orchestrator.py`, `tests/test_forfeit_detection.py`,
  `tests/test_chaos_ev_backfill.py`, `tests/test_replay_viewer_parser.py`)
  and was additionally verified live against real replay JSON and, where
  relevant, the real `@smogon/calc` / `@pkmn/smogon` engines — not only the
  in-memory fakes (ADR-007 specifically required this, since its bug lived
  entirely in Node-side library configuration a fake cannot model; ADR-008
  likewise verified live against the exact reported replay; ADR-009, ADR-012
  and ADR-013 likewise, against the user's own reported repro each time;
  ADR-014 additionally used `streamlit.testing.v1.AppTest` to catch a
  session-state bug no unit test would have caught, and a published Artifact
  preview of the real generated HTML to get visual sign-off neither pytest
  nor `AppTest` can give). Suite grew from 66 (start of session) to 113
  passing.
- **No orchestration-backend divergence.** `AnalysisService` (native) and
  `LangChainAnalysisOrchestrator` share `MatchupEvaluator` and
  `TurnReplaySimulator` unchanged, so every fix above applies identically to
  both backends — a direct benefit of the pre-existing shared-core design
  called out in CLAUDE.md §3. **Exception: ADR-011** is LangChain-orchestrator-
  specific by nature (its bug was the LCEL chain bypassing the native
  orchestrator's already-correct `OpenAIProvider`/`GeminiProvider` error
  wrapping) — the native backend never needed this fix. **ADR-014 is
  UI-only** and touches neither orchestrator.
- **Known, accepted residual limitations** (unchanged from before this
  session except where noted, now more visible rather than newly
  introduced): default EVs/nature when a replay never reveals a spread;
  base-forme stats for any Pokémon in a battle form the CURRENTLY INSTALLED
  `@smogon/calc` version's dex doesn't (yet) recognize — narrowed by ADR-009
  from "the calc engine has no data for non-standard formes" (was never
  fully true) to "not yet published in the pinned dependency version",
  live-checked and honestly caveated rather than assumed; the Doubles
  spread-reduction is applied even on the rare turn where a spread move only
  has one legal live target (ADR-007).

---

## ADR-028 — Wire the dormant agentic tool-calling path into the LangChain explanation stage

**Status:** Accepted · `feat/agentic-explanation-followup`

### Context
`src/adapters/llm/langchain_tools.py` already wrapped `CalcEngineAdapter`,
`MetaStatsProvider` and `StrategyKnowledgeProvider` as three LangChain
`StructuredTool`s (`damage_calc`, `chaos_meta_stats`, `smogon_strategy`), with
a docstring stating the intent plainly: "so a LangChain agent
(`langchain.agents.create_agent`) can call them on demand for interactive
follow-up questions." A repo-wide search found this file referenced nowhere
else — not `Container`, not `LangChainAnalysisOrchestrator`, not a single
test. It was a fully-built, never-connected capability: the explanation
stage (2nd AI) received one precomputed JSON payload
(`build_explanation_context` — deterministic calc verdicts + turn-by-turn
checks + Protect reads + Chaos meta context + Smogon strategies + optional
improvement suggestions) and produced prose from it in a single completion,
with no way to reach back into any of those sources for a question that
payload didn't already cover (a hypothetical item/EV spread, a species not
in this game, a different rating tier).

### Decision
Wire `build_langchain_tools` into `LangChainAnalysisOrchestrator`'s
explanation stage, replacing the bare `chat_model` completion with a bounded
`langchain.agents.create_agent(model=chat_model, tools=[...], system_prompt=
...)` call — the exact API the dormant docstring already named, confirmed
directly against the installed `langchain>=1.0` (1.3.14) before writing any
code. Every design choice below keeps this an *additive* capability, never a
second source of truth:

- **The precomputed context is untouched and still sent first.** The agent's
  `system_prompt` is `explanation_system.txt` plus a new, narrow addendum
  (`explanation_agent_addendum.txt`) that explicitly tells the model: only
  call a tool for something the JSON context does NOT already contain; NEVER
  call one to re-derive or "confirm" a number already given (that would
  reintroduce exactly the double-computation ADR-007's anti-recompute rule
  exists to forbid); and always present a tool's result as a fresh/
  hypothetical figure, distinct from the real game's ground truth.
- **Every tool degrades instead of raising.** `langchain_tools.py`'s three
  functions previously let a domain exception (`CalcEngineError`/
  `ChaosDataError`/`StrategyKnowledgeError`) propagate straight into the
  agent loop. Rewrote each to return `{"ok": False, "error": str(exc)}` on
  failure and `{"ok": True, **result.model_dump()}` on success — the exact
  `{ok:false,error}` convention this project already uses at the Node IPC
  boundary (`node_ipc.py`/`calc_server.js`), now applied one layer up so a
  bad on-demand lookup (an unrecognized species/move) ends the tool call, not
  the whole `analyze()` turn.
- **Bounded, not open-ended.** `agent.invoke(..., config={"recursion_limit":
  self._agent_max_steps})`, default 10 LangGraph steps (roughly a handful of
  tool round-trips) — a runaway loop is a cost/latency risk this project has
  no reason to accept for what is, by design, a rare escape hatch.
- **A new typed result, `AgentToolInvocation`** (`tool`, `arguments`, `ok`,
  `summary`), and `AnalysisResult.agent_tool_calls: list[AgentToolInvocation]`
  — populated by walking the agent's returned LangGraph message trace
  (`AIMessage.tool_calls` matched to its `ToolMessage` by id), never guessed.
  Empty for the overwhelmingly common case where the agent never calls a
  tool, and always empty for the native `AnalysisService`, which has no
  agent loop at all.
- **UI alert.** Per explicit request: whenever `agent_tool_calls` is
  non-empty, `app.py` now shows an `st.info`/`st.warning` banner (warning if
  any call has `ok=False`) naming which tools ran and how many, plus an
  expander listing each call's arguments and result/error — so a user seeing
  a richer answer than the precomputed context alone would produce can tell
  it came from a live, on-demand lookup and not a silent extra number.
- **Scoped to the LangChain backend only**, deliberately. `langchain_tools.py`
  is LangChain-specific by construction (`StructuredTool`, `create_agent`);
  giving the native `AnalysisService` an equivalent would mean hand-rolling a
  second tool-calling loop against the raw OpenAI/Gemini SDKs — real,
  separate scope, not a two-line addition. This is the same shape of decision
  as ADR-011 leaving `LangChainLLMProvider` unused rather than forcing
  parity immediately: the two backends still guarantee identical GROUND
  TRUTH numbers (unchanged by this ADR), only one of them can now also fetch
  a NEW number on demand.

### Alternatives considered
- **A third, fixed "organizer" LLM call between the deterministic stages and
  the explanation stage**, instead of an agent loop. Considered and rejected
  after discussion: the actual goal was supporting genuine follow-up/
  counterfactual questions ("what if this held a different item?"), not
  restructuring the payload the final explanation already receives in full.
  A fixed extra call would also have reintroduced the exact anti-pattern
  ADR-008/ADR-010 already named and walked back — asking a model to do a
  job (curate/prioritize precomputed ground truth) a deterministic function
  can do more reliably — for no capability the agentic path doesn't already
  provide more directly.
- **Give the native `AnalysisService` a hand-rolled tool loop for parity.**
  Rejected for this change: real, separate scope (see Decision), and no
  report has asked for it yet; noted here as a reasonable future extension,
  not silently dropped.
- **Let a raised tool exception propagate and rely on `create_agent`'s own
  default error handling.** LangGraph's `ToolNode` does have a default
  error-to-`ToolMessage` fallback, but it produces an opaque generic string,
  not the structured `{"ok": False, "error": ...}` shape
  `AgentToolInvocation.ok` needs to parse deterministically — handling it
  explicitly in `langchain_tools.py` keeps "was this on-demand lookup
  trustworthy" a checkable fact, not a guess from string content.

### Consequences
- Verified live (this repo's own installed `langchain==1.3.14`): a scripted
  agent turn that calls `damage_calc` with a hypothetical item, receives the
  `{"ok": true, ...}` result, and answers from it — end to end through
  `LangChainAnalysisOrchestrator.analyze()` — produces exactly one
  `AgentToolInvocation(tool="damage_calc", ok=True, ...)`; a scripted failure
  (an unrecognized move) produces `ok=False` with the engine's own error
  text, and the turn still completes rather than raising past `analyze()`.
- `tests/test_langchain_orchestrator.py`'s existing fakes needed one change:
  `GenericFakeChatModel.bind_tools()` raises `NotImplementedError`
  unconditionally, and `create_agent` always calls it (even for a turn that
  never uses a tool) — a `_ToolCapableFakeChatModel(GenericFakeChatModel)`
  overriding `bind_tools` to a no-op fixes every existing test with no
  behavior change to what they assert.
- `AnalysisResult.agent_tool_calls` defaults to `[]` via Pydantic
  `default_factory`, so every other caller/test constructing an
  `AnalysisResult` without it is unaffected.

### Files touched
`src/domain/models.py` (`AgentToolInvocation`, `AnalysisResult.agent_tool_calls`),
`src/adapters/llm/langchain_tools.py` (`{ok,...}`/`{ok:false,error}` wrapping),
`src/adapters/llm/prompts/explanation_agent_addendum.txt` (new),
`src/services/langchain_orchestrator.py` (`_build_explanation_agent`,
`_agent_messages`, `_extract_tool_invocations`), `src/ui/app.py` (alert +
expander), `tests/test_langchain_orchestrator.py` (`_ToolCapableFakeChatModel`,
`_SelectivelyFailingCalcEngine`, three new tests).

---

## ADR-029 — Give the explanation model a clean remaining-HP number instead of a string to derive one from

**Status:** Accepted · follow-up to `scripts/faithfulness_benchmark`

### Context
The faithfulness mini-benchmark (see its own `README.md`) found the
explanation model's remaining `damage_range` errors were never a data
problem — every calc-engine figure it was checked against was independently
confirmed correct, including Chaos-derived EV/nature spreads (verified live:
`Container.chaos().build_match_context(...)` returns a real top usage
spread, e.g. Garchomp's Jolly 16 HP/256 Atk/256 Spe, and the resulting calc
`description` matches exactly). The remaining errors were narration
mistakes with perfectly correct data already sitting in context:

1. Reading a `damage_checks` entry's `actual_result` string ("ended at 60%
   HP" — REMAINING HP) and restating it as if it were the damage the hit
   DEALT (a different figure, `projected_min/max_percent`) — e.g. citing
   "60% damage" for a hit whose real `projected_max_percent` was ~40%.
2. Quoting the raw damage-points number embedded inside a `description`
   string (e.g. the "158" in `"158-188 (81 - 96.4%)"`) as if it were itself
   a percentage, instead of the parenthesized figure.

Both are citation/derivation mistakes, not missing-information mistakes —
the model had every correct number available; it sometimes combined or
mislabeled two adjacent-but-different figures while composing prose.

### Decision
Per this project's own established principle (ADR-004/008/010): push the
guarantee as far downstream into deterministic code as it will go; only
ask the LLM to faithfully report what's already there — and prompt-only
tightening alone is not reliably sufficient for a model failing an inline
derivation (ADR-008/010 already found this on live evidence, for a
different derivation task). Rather than only adding a stricter prompt rule,
remove the derivation step itself:

- New field `TurnDamageCheck.actual_hp_remaining_percent: float | None` —
  the exact same fact `actual_result`'s string already encodes (remaining
  HP after the hit; `0.0` on a faint; `None` for a Protect block, where no
  HP changed), now also available as a plain, separately-labeled number
  the model can cite verbatim instead of parsing a string or subtracting
  from 100 in its own head.
- `TurnReplaySimulator._actual_results()` already parsed this number out of
  the log's `->NN%` result fragment to build the string — it was simply
  being discarded once formatted. Extended to return `(text, float | None)`
  per species instead of just `text`, so both facts come from the exact
  same parse of `event.results` and can never drift out of sync with each
  other. A `faint` maps to `0.0` (not `None`) since "0% HP left" is exactly
  what a faint means. A `blocked (...)` entry maps to `None` — nothing hit
  that Pokemon this move, so there is no HP-remaining fact to report at all.
- `explanation_system.txt` gained two sentences immediately next to the
  existing `actual_result`/`damage_checks` explanation, naming both
  confusions above explicitly and forbidding computing one figure from the
  other — reinforcing, not replacing, the structural fix.

This is deliberately the smallest change that removes the derivation: no
new calc-engine call, no new parsing logic (the number was already being
parsed, just not kept), no change to `actual_result`'s existing string or
any other field, and — confirmed by grepping the whole repo before making
the change — exactly one constructor site (`turn_simulator.py::
_damage_checks`) and one caller of `_actual_results()` exist, so the
blast radius is fully contained to one file plus one new, optional,
default-`None` domain field.

### Alternatives considered
- **Track HP per Pokemon turn-by-turn to compute an independent "damage
  dealt" figure and cross-check it against the calc's projection.**
  Rejected: this is the exact scope ADR-008 already declined for the same
  reason ("requires the parser to also handle -heal/drain/recoil correctly
  to stay accurate — real new scope with real correctness risk") for a fact
  `projected_min/max_percent` already provides at the same trust level as
  every other number this project treats as ground truth. A second,
  independently-computed "actual damage" figure risks becoming a second,
  competing source of truth — not a fix for a citation mistake.
- **Prompt-only fix (add the clarification sentences, no new field).**
  Considered first, but ADR-008/010 already found, on live evidence for a
  different task, that tightening wording alone does not reliably stop a
  model from failing something it could also simply be handed the answer
  to. Since this project's whole guiding thread is not repeating that
  lesson, the field ships alongside the sentences, not instead of them.
- **Restructure the whole `turn_by_turn_checks` payload shape.** Rejected
  as disproportionate: the failure is narrowly two adjacent-figure
  confusions, not a shape problem — a full restructure would touch the
  UI's own rendering (`src/ui/app.py`) and every existing test for a fix
  one additive field and two prompt sentences already cover.

### Consequences
- Fully additive and backward compatible: `actual_hp_remaining_percent`
  defaults to `None`, no existing field, method signature, UI rendering,
  or test needed to change to keep working — verified: all 203 pre-existing
  tests pass unchanged, `mypy --strict` clean.
- Flows through to both orchestrators and the UI automatically via the
  existing `AnalysisResult.turn_checks[].damage_checks[]` DTO chain — no
  change needed in `analysis_service.py`, `langchain_orchestrator.py`, or
  the composition root.
- Residual, stated rather than assumed: this ADR ships the mechanism and
  three new unit tests confirming the field is populated correctly
  (damaging hit, faint, Protect block) — it does not re-run
  `scripts/faithfulness_benchmark` against a live LLM to confirm the
  citation-error rate actually drops, since that requires new LLM calls
  this change alone doesn't need. A follow-up benchmark run is the natural
  way to verify the effect; noted here as not yet done, not claimed as
  already proven.

### Files touched
`src/domain/models.py` (`TurnDamageCheck.actual_hp_remaining_percent`),
`src/services/turn_simulator.py` (`_actual_results`, `_parse_percent`,
`_damage_checks`), `src/adapters/llm/prompts/explanation_system.txt`,
`tests/test_turn_simulator.py` (3 new/extended tests).
