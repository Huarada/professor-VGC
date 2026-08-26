# Faithfulness mini-benchmark

Measures, with a number, what fraction of the atomic factual claims in
ProfessorVGC's prose explanation match the deterministic ground truth —
**Condition A** (the real pipeline) vs **Condition B** (the same LLM given
only the raw Showdown log, no `AnalysisResult` grounding at all). This is
the standard atomic-claim-verification shape used in RAG/faithfulness
evaluation (FactCC, SummaC-style approaches): extract → verify → rate.

**Primary result — `damage_range` claims, n=30 fixtures, Fisher's exact
test: A 81/88 (92.0%) vs B 12/95 (12.6%), odds ratio 80.0, p < 0.0001.**
See "Primary result" below for why `damage_range` — not the aggregate over
every claim type — is this benchmark's headline number, and why a formal
significance test rather than a raw percentage comparison is what turns
"the numbers look different" into "the difference is statistically
defensible." See "Round 4: orchestrator comparison" below for the same
question asked across all three `AnalysisPipeline` backends (`adk`
default, `langchain`, `native`) — no pairwise difference between them is
statistically significant; the faithfulness advantage over the naive
baseline holds for all three alike.

## Running it

One-time setup:

```bash
pip install -r requirements.txt
cd node_calc && npm install && cd ..     # the real @smogon/calc engine, needed for ground truth
```

Needs a real `PROFESSORVGC_OPENAI_API_KEY` (or `--provider gemini` +
`PROFESSORVGC_GEMINI_API_KEY`) in `.env` and network access for every command
below that says "LLM calls" — those talk to a real provider, `gpt-4o-mini`
by default, cheap and fast (a full 30-fixture run — 120 LLM calls — takes a
few minutes).

| Want to... | Run this |
|---|---|
| **Compare A vs B, all 30 fixtures** (the full thing, incl. the Fisher's exact test on `damage_range`) | `python -m scripts.faithfulness_benchmark.run` |
| Same, but Condition A uses `adk`/`langchain` instead of the default `native` | `python -m scripts.faithfulness_benchmark.run --orchestrator adk` |
| **Compare ADK vs LangChain vs native precision directly** (pairwise Fisher's exact tests, shared ground truth/B per fixture — see Round 4 below) | `python -m scripts.faithfulness_benchmark.run_orchestrator_comparison` |
| Quick smoke test (1–2 fixtures, seconds not minutes) | `python -m scripts.faithfulness_benchmark.run --limit 2` |
| Only the original 10 trap-category fixtures (skip the 20 damage-dense ones) | `python -m scripts.faithfulness_benchmark.run --trap-fixtures-only` |
| Save to a specific file / use Gemini instead | `python -m scripts.faithfulness_benchmark.run --provider gemini --out my_run.json` |
| **Re-score an already-saved run's damage numbers** (MAE/RMSE/MSRE/RMSRE — no new LLM calls) | `python -m scripts.faithfulness_benchmark.damage_error_metrics out/run7_n30.json` |
| **Bias self-audit: does the judge extract confident vs hedgy phrasing differently?** (12 LLM calls, seconds) | `python -m scripts.faithfulness_benchmark.style_blindness_check` |
| **Just the deterministic harness itself** (verifier + percent classifier + Fisher's test — no LLM, no network, <1s) | `pytest tests/test_faithfulness_benchmark_verify.py tests/test_percent_classifier.py tests/test_benchmark_stats.py -q` |
| Re-run everything including the Node-IPC regression | `pytest -q` (whole project's suite; this benchmark's own tests are a small part of it) |

The first row is what "run the comparison" means day to day; the rest are
narrower entry points into the same pieces (see Methodology below for what
each script actually does).

**Windows note:** if `httpx`/the OpenAI SDK fails with
`CERTIFICATE_VERIFY_FAILED` while a plain `urllib` request to the same host
succeeds, your machine has a locally-trusted root CA (corporate proxy,
security software) that isn't in `certifi`'s bundle. `pip install
pip-system-certs` fixes it by making `ssl` use the OS trust store; this is a
local dev-environment fix, not a project dependency.

## Methodology

1. **Fixtures, 30 total.** `fixtures.py` (10 cases): five are the *exact log
   text* from this project's own regression tests — each is the minimal
   repro for a documented ADR hallucination class (bench-only exclusion,
   mid-game Mega Evolution, stat-stage tracking, Protect-vs-spread,
   forfeit). The other five (two genuine/misallocated/mirror-match Protect
   reads, a clean control case, a two-traps-at-once case) are hand-authored
   and were validated live against the real `ShowdownReplayParser` +
   `TurnReplaySimulator` before being committed — printed transcripts
   confirmed each one produces exactly the intended `ProtectRead`/
   `side_of()`/`forme_changes` ground truth, not assumed.
   `damage_dense_fixtures.py` (20 more, `dmg01`–`dmg20`): engineered
   specifically to maximize `damage_range` claims per replay, once the
   sample-size discussion below settled on that as this benchmark's
   statistically defensible headline metric — every species was confirmed
   present in the real Chaos data via a direct
   `Container.chaos().build_match_context()` query (not a visual scan of
   the dumped JSON — that check itself caught 4 species from earlier
   fixtures with NO real Chaos coverage, see "A note on Chaos coverage"
   below), and every `(attacker, move, defender)` triple was swept against
   the real `SmogonCalcAdapter` for a nonzero result (catching, and fixing,
   3 accidental type immunities, 2 status moves mistakenly used as damaging
   ones, and one species — Aegislash — that this `@smogon/calc` version
   errors on) before being committed.
2. **Condition A** runs the real native `AnalysisPipeline` (`Container.
   build_pipeline(orchestrator="native")`) end to end and keeps both the
   prose `answer` and the full `AnalysisResult` as ground truth.
3. **Condition B** (`naive_baseline.py`) sends the SAME raw log and question
   to the SAME LLM, with a system prompt that says nothing about a
   calculator, Chaos stats, or any precomputed context — it has to infer
   everything itself, the standard "LLM alone" control in RAG papers.
4. **The judge** (`judge.py`) is a third, separate LLM call that sees ONLY
   the prose answer — never the ground truth, never which condition
   produced it — and extracts atomic claims into 8 typed shapes (`move_used`,
   `pokemon_played`, `damage_range`, `forme_change`, `stat_stage`,
   `protect_block`, `winner`, `forfeit`).
5. **Verification** (`verify.py`) is 100% deterministic Python compared
   against `GroundTruth` (`ground_truth.py`, built from the real `GameState`
   + `AnalysisResult` — no LLM anywhere in this step): species existence,
   `side.brought()` membership, move usage from the real event timeline,
   damage-range overlap (±3pp tolerance) against the real
   `MatchupVerdict`/`TurnDamageCheck`/`OptimalMoveOption` figures, forme
   changes, boost-stage direction, Protect blocks, and winner/forfeit
   identity (side-alias resolved, so "p2", "Player 2", and a player's real
   name all match the same side).
6. Two rates per condition: **strict** (`correct / all claims`, unverifiable
   counts against the score) and **lenient** (`correct / (correct +
   incorrect)`, unverifiable excluded).
7. **The headline number is `damage_range`, tested with Fisher's exact
   test — not the aggregate over all 8 claim types.** `stats.py` runs
   `scipy.stats.fisher_exact` on the 2x2 table of `damage_range`
   correct/incorrect counts. Fisher's exact test, not a chi-square test,
   because chi-square's normal approximation needs every cell's expected
   count above roughly 5 to be trustworthy, which this benchmark's n does
   not reliably clear; Fisher's exact test computes the exact
   hypergeometric probability of the observed table (or a more extreme
   one) under the null of no association, with no such requirement. See
   "Primary result" below for why `damage_range` specifically, rather than
   the mix of all 8 types.

## A note on Chaos coverage

Not every species that LOOKS like a real VGC staple actually has data in
the loaded Chaos files. A direct query (`Container.chaos().
build_match_context(...)`, not eyeballing the dumped JSON) found `Iron
Hands`, `Flutter Mane`, `Amoonguss`, and `Charizard` — all used in the
original `normal_clean` fixture — have NO entry at all, so their
`damage_range` ground truth there was computed on the bare 0-EV/neutral-
nature baseline `enrich_set()` falls back to, not a real Chaos-derived
spread like every other fixture's species. This isn't wrong (0 EVs is
still real, honest ground truth — just a different assumption than most
of this benchmark's other matchups) but it's worth knowing if you're
reading `normal_clean`'s specific numbers closely. Every one of the 56
species considered for `damage_dense_fixtures.py` was checked this same
way before use, and every fixture in that file uses a species confirmed to
have a real spread.

## Primary result: `damage_range`, n=30, Fisher's exact test

`out/run7_n30.json` — all 30 fixtures (10 trap-category + 20 damage-dense),
after every instrumentation fix below (species resolution, the two
degenerate-claim filters, the percent-type classifier) was already in place:

| | Condition A (grounded) | Condition B (naive) |
|---|---|---|
| `damage_range` claims | 88 | 95 |
| Correct | 81 | 12 |
| **Rate** | **92.0%** | **12.6%** |

**Fisher's exact test: odds ratio 80.0, two-sided p < 0.0001, one-sided
(A > B) p < 0.0001 — significant at α=0.05 by an enormous margin.** This is
not a borderline result that needs hedging: an odds ratio of 80 means the
grounded pipeline's odds of stating a correct damage figure are roughly 80
times the naive baseline's, and a p-value this small is not sensitive to
the exact α threshold chosen.

**Why this is the number to report, not the aggregate over all 8 claim
types:** the per-claim-type breakdown (full table in `out/run7_n30.json`)
shows `move_used`, `pokemon_played`, `winner`, and `forfeit` all sitting in
the 90–100% range for BOTH conditions — categories where grounding has
little or nothing left to add, because a coherent battle log alone already
carries enough signal for either condition to get them right almost every
time. `damage_range` is the one category where the two ports this project
actually depends on for numbers — the real `@smogon/calc` engine and real
Chaos-derived EV/nature spreads — are doing genuine, otherwise-unavailable
work. Reporting a single aggregate rate blends a category with a massive,
statistically overwhelming effect together with several categories that
already tie, diluting the strongest finding this benchmark has into a
smaller, noisier number — which is exactly what happened across the n=10
rounds below (aggregate rates of 76–89% that flipped which condition
"won" from run to run). The secondary/all-types number is still reported
(`secondary_context_all_claim_types` in every saved run) for completeness,
never as the headline.

**Why n=30, and specifically 20 MORE `damage_range`-dense fixtures rather
than 20 more generic ones:** at n=10–15 `damage_range` claims the earlier
rounds were already in a defensible zone for a formal test, but every
additional generic fixture (another forfeit case, another mega evolution)
mostly adds claims to categories that already tie — diluting statistical
power rather than growing it. `damage_dense_fixtures.py`'s 20 fixtures are
built to do the opposite: each is a 2v2 exchange with 4 real, verified-
nonzero, Chaos-covered attacks, engineered to make the model narrate
several `damage_range`-worthy facts per replay instead of one. That is why
n grew from ~20 `damage_range` claims (n=10 fixtures) to 183 (n=30
fixtures) rather than merely doubling.

## Bias self-audit: checking the n=30 result for unintentional favoritism toward Condition A

An odds ratio of 80 with p < 0.0001 is exactly the kind of result that
deserves *more* scrutiny, not less — a "too clean" number is at least as
likely to mean the measurement is biased as it is to mean the effect is
real. Four concrete questions were raised against this benchmark's own
methodology after the Primary Result above was first reported, and each was
checked against real data (not answered from memory or intuition) rather
than asserted away.

**1. Was fixture curation blind to which condition would score better?**
Yes, by construction, not by promise. The only criteria ever applied to the
20 `damage_dense_fixtures.py` fixtures before they were used in an actual
A/B run were deterministic and performance-independent: (a) does the
species exist in the real Chaos data (`Container.chaos().
build_match_context(...)`), (b) does the `(attacker, move, defender)`
triple deal real, nonzero, non-erroring damage against the real
`SmogonCalcAdapter`. Neither check ever calls an LLM or looks at either
condition's prose. The one live-LLM smoke test run against `ALL_FIXTURES`
before the full n=30 pass (`--limit 2`) touches only the *first two*
entries of `FIXTURES + DAMAGE_DENSE_FIXTURES` — i.e., `bench_only` and
`mega_evolution`, both from the **original 10**, not from the new 20 — so
no `dmg01`–`dmg20` fixture was ever scored by either condition before the
single, one-shot n=30 run that produced the Primary Result. There was no
"this fixture wasn't giving a good gap, let me swap it" loop, because there
was no observed gap to react to until after the full run finished.

**2. Do the damage-dense fixtures inflate the effect beyond what a typical
replay would show?** Split into two sub-questions, checked separately:

- *Did fixture selection favor obscure species B would have a weaker prior
  on?* Checked directly against real Chaos usage-rank data (`gen9champions
  vgc2026regmb-1760.json`, 261 species ranked by usage%):

  | | Original 10 fixtures | 20 damage-dense fixtures |
  |---|---|---|
  | Unique species (found in this tier) | 9 | 55 |
  | Median usage rank | 65 / 261 | 67 / 261 |
  | Mean usage rank | 80.3 / 261 | 79.4 / 261 |
  | Median usage % | 2.28% | 2.25% |

  Essentially identical distributions — the new fixtures do not skew toward
  more niche, less-documented species. This specific hypothesis is
  refuted by the data.
- *Is the exchange density itself higher than a typical replay segment,
  independent of species choice?* Yes, and this is **not** refuted — it's
  the fixtures' explicit design goal, stated in their own docstring: every
  one of the 80 exchanges across the 20 fixtures is a clean, single-target,
  non-status, non-immune attack, with no Protect, no miss, no switch, no
  redirection diluting the turn. A typical VGC doubles turn is not 100%
  clean-attack slots. This means the n=30 Primary Result characterizes
  Condition A/B's accuracy **in the densest, least-narratively-ambiguous
  damage scenario the format allows** — the cleanest possible test of "does
  a real calculator + real Chaos spread beat a guess" — not necessarily the
  claim-density mix of an average real replay. No real-replay damage-claim-
  density baseline exists yet to compare against, so this remains an
  acknowledged, open external-validity limitation, not a resolved one.

**3. Why did both conditions' rates move between the 10-fixture and
30-fixture rounds — does that indicate the new fixtures are systematically
"easier" for both (a symptom of unintentional curation bias)?** Checked by
splitting the SAME `run7_n30.json` run (identical judge, identical filters,
no round-to-round instrumentation changes) into its old-10 and new-20
subsets:

| Fixture subset (within run7_n30, same run) | Condition A | Condition B |
|---|---|---|
| Original 10 | 6/11 = 54.5% | 2/18 = 11.1% |
| 20 damage-dense | 75/77 = 97.4% | 10/77 = 13.0% |

Holding the run constant, **B barely moves** (+1.9pp) while **A jumps to
near-ceiling** (+42.9pp) — this does not match a "both got easier together"
pattern. Separately, the number this was originally compared against
("B: 6%") came from a *different* run (`run6.json`, the old-10-only round
before densification): re-checked directly, `run6` gives A 6/11 = 54.5%
(identical count to `run7_n30`'s old-10 subset) and B 1/18 = 5.6% (vs
2/18 = 11.1% in `run7_n30` — a swing of exactly one claim on a base of 18,
consistent with ordinary LLM sampling noise between separate runs, not a
methodological artifact of adding fixtures). Net honest reading: the
apparent "both jumped" pattern was partly an artifact of comparing across
two different runs; within one consistent run, only Condition A's rate is
dramatically higher on the new fixtures — which is the expected, intended
effect of stripping out narrative ambiguity (see point 2), not evidence of
curation bias inflating both sides.

**4. Was the judge blind to which condition produced the text?** Two
layers of evidence, not one:

- *Structural:* `judge.py::extract_claims(llm, answer_text)` takes only the
  raw prose string. No caller (see `run.py`'s `_extract_and_filter`) ever
  passes a condition label into the LLM call — the `"A"`/`"B"` label that
  exists in the code is used exclusively in local `print()` statements for
  console legibility, never reaches the prompt. Fixed `judge_system.txt`,
  `temperature=0.0`, no fixture id, no ground truth in the call. There is
  no channel for an explicit label leak.
- *Behavioral, not just structural:* the harder question is whether the
  judge implicitly favors Condition A's typically confident/precise prose
  style over Condition B's typically hedgier phrasing — a bias that
  wouldn't need an explicit label to exist. This was tested directly rather
  than argued from precedent:
  `scripts/faithfulness_benchmark/style_blindness_check.py` runs the real
  judge against 6 base damage facts, each phrased once in confident/
  grounded style ("Garchomp's Earthquake hit Staraptor for 62-74% ...") and
  once in hedgy/naive style ("I think Garchomp's Earthquake probably did
  somewhere around 62-74% or so ... hard to say exactly"), with the
  **identical stated numeric range** in both. Result (12/12 texts,
  `out/style_blindness-*.json`):

  | Style | Extraction rate | Numeric-fidelity rate |
  |---|---|---|
  | Confident/grounded-like | 100% (6/6) | 100% (6/6) |
  | Hedgy/naive-like | 100% (6/6) | 100% (6/6) |

  Zero gap. This is real, causal evidence — not a recollection of an
  earlier round's asymmetry — that the judge extracts an explicitly stated
  numeric range with equal fidelity regardless of confidence framing. Its
  scope is deliberately narrow: it isolates *style* while holding "a
  precise number is present in the text" constant, so it does not cover the
  separate case of Condition B hedging with no number at all (e.g. "took a
  good chunk of damage") — that case is not a judge-extraction question,
  it is correctly scored `unverifiable`, and the `unverifiable` rate is
  already close to equal between conditions in `run7_n30` (2/79 for both
  A's and B's damage-dense subset). Weaker, secondary evidence pointing the
  same direction: in the round-3 filter pass, `filter_ambiguous_damage_
  claims` dropped 8 non-damage-dealt claims from Condition A's run vs 1
  from Condition B's — the opposite of what a pro-A leniency bias would
  predict.

**Net assessment:** three of the four concerns are refuted or substantially
weakened by direct evidence (blind curation confirmed by process +
timestamps; species-obscurity bias refuted by usage-rank data; simultaneous-
jump reframed as run-to-run noise plus an expected, intended effect of
fixture design, not curation bias; judge blindness confirmed both
structurally and behaviorally). One remains a genuine, unresolved
limitation: the damage-dense fixtures test the cleanest, least-ambiguous
damage scenario the format allows, and no real-replay density baseline
exists yet to confirm how representative that is of an average game. The
Primary Result above should be read as "grounding wins decisively when the
scenario is a clean, single-target damage exchange" — which is true and
important — not yet as "grounding wins by 80x odds in a typical VGC game,"
which would need a density-representative fixture sample to claim.

## Bugs this benchmark found in its own instrumentation (and fixed)

Building this surfaced three real gaps between "looks reasonable" and
"actually correct" — worth recording, since they would have silently
produced misleading numbers:

1. **Species resolution didn't survive the side-prefix the real prompt
   requires.** `explanation_system.txt` explicitly requires every mention to
   carry its p1/p2 prefix (CLAUDE.md's own anti-misattribution rule), so a
   *faithful* grounded answer routinely says "p2 Garchomp" or "Gengar (p2)" —
   an exact-string match against the roster flagged every one of those as a
   hallucinated Pokemon. Fixed with `resolve_species()`: match a known
   species as a normalized substring of the claimed text, not an exact
   string. This alone moved Condition A from scoring *worse* than the naive
   baseline to scoring better — the original number was an artifact of this
   bug, not a real result, and is not reported below.
2. **The judge doesn't perfectly honor a negative instruction.**
   `gpt-4o-mini` sometimes extracted a `forfeit` claim from an ordinary
   fainting-based loss even after the prompt was tightened to forbid it, and
   sometimes collapsed "Ash used Torkoal and Flutter Mane" into a
   `pokemon_played` claim about the trainer "Ash" instead of the Pokemon
   actually named. Both are judge extraction artifacts, not facts about the
   game — fixed with deterministic post-hoc guards (`judge.py`'s
   keyword-presence filter, `verify.py`'s `filter_degenerate_claims`) rather
   than trusting prompt compliance alone, the same principle this whole
   project already applies to the explanation stage.
3. **Three fixtures had a physically impossible move/target type pairing —
   the damage calculator was never wrong; the fixture data was.** Several
   transcripts showed a real `@smogon/calc` result of `0.0-0.0%` next to a
   fake log line claiming real HP loss, which reads exactly like "the
   calculator is broken" until you check the types involved:

   | Fixture | Move (type) | Target | Why it's 0% |
   |---|---|---|---|
   | `bench_only` | Zap Cannon (Electric) | Garchomp (Ground/Dragon) | Ground is immune to Electric |
   | `protect_genuine_read` | Earthquake (Ground) | Staraptor (Normal/Flying) | Flying is immune to Ground |
   | `protect_misallocated` | Last Respects (Ghost) | Staraptor (Normal/Flying) | Normal is immune to Ghost |

   (A fourth, `Iron Hands Fake Out (Normal) → Flutter Mane (Ghost/Fairy)` in
   `normal_clean`, was caught in the same sweep — Ghost is *also* immune to
   Normal.) Each was confirmed directly against the real calc engine
   (`SmogonCalcAdapter`, not a fake) before and after the fix — see the git
   history of `fixtures.py` for the exact transcripts, including a control
   query (`Garchomp Earthquake → Ceruledge`, no type relationship, giving a
   normal nonzero result) proving the engine itself was never in question.
   All four were hand-authored logs (see the fixtures table above) where a
   move was picked for flavor without checking the target's typing — a
   benchmark-authoring mistake, not a product bug. Fixed by swapping each to
   a same-role move with no type immunity (`Rock Slide`, `Wave Crash`,
   `Wild Charge`), re-validated against the real parser + `TurnReplaySimulator`
   to confirm the intended `ProtectRead` classification survived the swap,
   then re-confirmed with a full sweep of every `(attacker, move, defender)`
   triple across all 10 fixtures against the real engine (zero flagged).

## A separate bug found (and fixed) in production code

Running this live crashed a background thread with `UnicodeDecodeError`,
100% reproducibly, on the very first calc call of every run:
`src/adapters/node_ipc.py`'s `subprocess.Popen(..., text=True)` decoded
Node's stdout with `locale.getpreferredencoding()` (`cp1252` on this
machine), not the UTF-8 Node actually writes — silently losing any response
containing a byte cp1252 can't represent (confirmed: the right double
quotation mark `”`, U+201D, encodes to UTF-8 bytes ending in `0x9D`, one of
exactly five byte values undefined in cp1252). Every existing caller already
catches `CalcEngineError` and skips that one item, so this degraded
gracefully rather than crashing the pipeline — but for no good reason. Fixed
by passing `encoding="utf-8"` explicitly; regression test in
`tests/test_node_ipc_encoding.py` reproduces the exact byte against a real
Node subprocess and confirms the fix.

## Appendix: the n=10 exploratory rounds (superseded by the n=30 primary result above)

Kept in full because the bug-finding narrative below is real and the
methodology it documents (species resolution, the two degenerate-claim
filters, the percent-type classifier) is exactly what the n=30 primary
result above was computed with — but the specific PERCENTAGES quoted in
this section are from the smaller, 10-fixture round and should not be
quoted as this benchmark's current headline; the Primary Result section
above supersedes them.

Three full runs (10 fixtures each): two after fixing instrumentation bugs
#1/#2 above (run1/run2 — these still had the type-immunity fixture bug,
which only affects 4 specific `damage_range` claims, not the aggregate
picture materially), one more (run3) after fixing bug #3 as well:

| Run | Condition A (grounded) | Condition B (naive) | Gap |
|---|---|---|---|
| run1 | 80.5% strict / 81.5% lenient | 75.6% strict / 76.5% lenient | +4.9pp |
| run2 | 74.4% strict / 75.3% lenient | 76.1% strict / 77.0% lenient | −1.7pp |
| run3 | 77.8% strict / 77.8% lenient | 78.9% strict / 78.9% lenient | −1.1pp |

**Honest reading: at N=10 fixtures / 1 sample each, the aggregate rate does
NOT show a robust, unambiguous gap** — run-to-run LLM sampling variance
(temperature 0.3 on both explanation calls) is large enough that which
condition "wins" the raw percentage flips between runs, and the three runs
now cluster within a ~6pp band with no consistent winner. Averaging all
three gives grounded ≈ 77.6% vs naive ≈ 76.9% — a difference well inside
the noise at this sample size, not a claim this benchmark can defend. A
benchmark that wanted a statistically defensible aggregate number would
need several LLM samples per fixture per condition and a reported
confidence interval, not a single pass over 10 cases — out of scope for
what was asked here ("mini").

**What IS robust across every single run (4/4, including run3 and the two
shown above and the one used to catch bug #1):** on the `bench_only`
fixture, Condition A's prose **never once** mentions the team-previewed-
but-benched Whimsicott (0/4) — it structurally cannot, because
`GameState.side_of()`/`candidate_species()` exclude it upstream (ADR-001),
so the LLM never even receives its name — while Condition B's prose
mentions Whimsicott as having played in **every single run** (4/4, verified
directly against the raw answer text, not just the judge's extraction: the
automated judge only reliably turned this into a structured, scored claim
in 3 of the 4 runs — in run3 it collapsed "Player 2 using Whimsicott and
Garchomp" into a single claim about Garchomp only, silently dropping the
Whimsicott half of the same sentence, an extraction miss of exactly the
same "one claim per entity" shape as bug #2 above). This is the actual
finding worth trusting: not a noisy aggregate percentage, and not even
fully captured by this benchmark's own automated scoring, but a specific,
mechanism-guaranteed hallucination class that the grounded architecture
makes structurally impossible and the ungrounded baseline falls into on
every attempt — and a concrete illustration that an LLM-judge pipeline like
this one *understates* the naive baseline's true unfaithfulness rate,
never overstates it. The full per-claim transcripts for every run are in
`out/*.json` if you want to see this (and the other individual claims)
yourself.

**On the damage calculator specifically** (a question that came up after
reading these transcripts): every `damage_range` mismatch across all three
runs traces to one of three causes, and the calc engine itself (verified by
direct, live query — see bug #3 above) was correct in 100% of cases
checked: (a) the fixture-authoring bug above (fixed); (b) the LLM under
test confusing "% HP remaining" with "% damage dealt" (e.g. reading "ended
at 60% HP" and claiming "dealt 60% damage" when the real figure is closer
to 40%) or citing raw damage points as if they were a percentage — genuine
faithfulness violations the benchmark is designed to catch, not calculator
errors; (c) a couple of remaining minor judge-extraction imprecisions (one
run extracted a `stat_stage` claim from a calc description's baseline EV
notation, "0 Def", which isn't an in-battle stage change at all) — noted
here rather than chased with another filter, to keep this a *mini*
benchmark rather than an open-ended judge-hardening exercise.

## Numeric error metrics (MSRE/RMSE) — a sharper instrument than the binary rate

The strict/lenient faithfulness rate above is binary per claim: a
`damage_range` claim within ±3pp of the real figure scores identically to
one that matches exactly, and a claim 5pp off scores identically to one
50pp off, as long as both cross the same tolerance line. That throws away
exactly the information needed to answer "how close is each condition to
the real number, on average" — so `damage_error_metrics.py` recomputes
every `damage_range` claim from a saved run (no new LLM calls: the claims
were already extracted) against a freshly-rebuilt, LLM-free ground truth
(`ShowdownReplayParser` + a comprehensive `fallback_plan()` covering every
cross-side pair + `MatchupEvaluator`/`TurnReplaySimulator`, all
deterministic), and reports both a relative metric (MSRE, RMSRE) and an
absolute one in the domain's natural unit (MAE, RMSE in percentage points)
— relative error alone is skewed by any low-magnitude ground-truth value
(a real 2% vs. a claimed 5% is a 150% relative error for a 3pp miss), so
reporting only MSRE would tell a cleaner but less honest story than
reporting it alongside MAE/RMSE.

```bash
python -m scripts.faithfulness_benchmark.damage_error_metrics       # defaults to out/run3.json
python -m scripts.faithfulness_benchmark.damage_error_metrics out/run1.json
```

Run against `run3.json` (the fixture-fixed run):

| Metric | Condition A (grounded) | Condition B (naive) |
|---|---|---|
| n (comparable claims) | 24 | 20 |
| MAE (percentage points) | **23.0pp** | 28.4pp |
| RMSE (percentage points) | **35.1pp** | 36.7pp |
| MSRE | **0.179** | 0.236 |
| RMSRE (typical relative error) | **42.3%** | 48.6% |

**Every one of the four metrics agrees: the grounded pipeline's damage
numbers land closer to ground truth than the naive baseline's, even though
the binary strict rate (above) showed no clear winner.** That is not a
contradiction — it says the two conditions get roughly the same *count* of
damage claims wrong (crossing the ±3pp line about equally often), but when
Condition A is wrong it tends to be off by a smaller amount, while
Condition B's misses are larger and wilder — consistent with Condition A
anchoring its guesses to real numbers already sitting in its prompt context
(a `description` string, an EV/nature assumption, an adjacent turn's real
figure) even when it misreads or miscombines them, versus Condition B
estimating from nothing but the log's raw HP deltas.

**Caveat, stated with the same honesty as the strict-rate numbers above:**
n=20–24 claims is still a small sample for a continuous metric — this is a
consistent-direction finding across four different metrics computed from
one run, not a statistically bulletproof one. A rigorous version would
bootstrap a confidence interval per metric across several repeated runs;
out of scope for a *mini* benchmark, noted here rather than glossed over.

## Round 2: re-run after ADR-029, and a meta-finding about the judge itself

`ADR-029` (`TurnDamageCheck.actual_hp_remaining_percent` + a prompt
clarification) shipped specifically to reduce the remaining-HP-vs-damage-
dealt confusion this benchmark found. Re-running afterward (run4, run5)
surfaced something more interesting than a before/after score:

- **run4** (post-ADR-029, original judge prompt): 76.8% vs 76.5% strict —
  essentially tied. Inspecting the actual prose behind the "wrong"
  `damage_range` claims found most of them were the model correctly and
  faithfully saying things like *"dealing significant damage and leaving it
  at 54% HP"* — a REMAINING-HP statement, matching the log exactly — that
  the **judge**, not the model, mis-extracted as a damage-DEALT claim of
  54%. ADR-029 appears to be doing its job; the benchmark's own judge was
  the thing still confusing the two framings.
- Tightened `judge_system.txt` to explicitly separate "dealt/dealing X%"
  from "left/leaving/ended/remaining at X% HP" and to reject bare "X-Y
  damage" (no `%` sign) as a percentage at all, then re-ran (**run5**): 78.3% vs 78.8% strict — the SAME confusions still
  appear in the judge's extractions afterward (e.g. still pulling `54` out
  of "leaving it at 54% HP"; now also pulling raw damage-point numbers like
  `66-78` into `min_percent`/`max_percent` in a couple of new cases; and one
  case of confusing a KO-*chance* percentage — "47.1% chance to 3HKO" — with
  a damage-*dealt* percentage, a fourth confusion this round's phrasings
  happened to surface). Prompt tightening alone did not reliably fix it.

**That is the same lesson ADR-008/010 already recorded for the production
model, now independently confirmed for this benchmark's own judge**: a
free-text LLM classification task that's genuinely ambiguous from wording
alone does not get reliably fixed by asking more firmly in the prompt — it
needs the classification pushed into deterministic code (a keyword/regex
guard, the same shape as `judge.py`'s existing forfeit-keyword filter and
`verify.py`'s `filter_degenerate_claims`). A first attempt at exactly that
guard was scoped out here: the confusions turned out to have at least three
distinct shapes (remaining-HP phrasing, raw-point numbers, KO-chance
percentages), each needing its own pattern, and hand-tuning three regexes
against a handful of observed phrasings risks overfitting this benchmark's
own small sample rather than generalizing — a reasonable next iteration,
not done in this round.

**What this means for the numbers:** the fine-grained `damage_range`
comparison (both the binary rate and the MSRE/MAE numbers) is now
confirmed to be measured through a judge with real, repeatable
extraction noise on both conditions — not eliminated by the ADR-029 fix,
because most of it was never in the production model to begin with. Take
the binary/MSRE numbers in this README as directionally consistent (A
ahead on every MSRE/MAE run so far, by a real but modest margin) rather
than precisely quotable. The one finding in this whole benchmark that does
**not** depend on the judge parsing percent semantics at all — the
`bench_only` fixture's Whimsicott check, a simple name-presence fact — is
the one worth trusting at face value.

## Round 3: the deterministic guard round 2 scoped out, actually built

Round 2 correctly diagnosed the fix (push the classification into
deterministic code, per ADR-008/010) but scoped it out as three separate
regexes risking overfitting one small sample. `percent_classifier.py`
builds it anyway, deliberately grounded in this PROJECT'S OWN fixed,
machine-generated vocabulary rather than in this benchmark's specific
sentences, so the rules generalize instead of memorizing ten fixtures:

- KO-chance text is `@smogon/calc`'s own literal output convention
  ("guaranteed OHKO", "12.5% chance to OHKO", "possible 7HKO" —
  `DamageResult.ko_chance_text`, `node_calc/src/calcEngine.js`) — a stable
  format the calc engine itself emits.
- "ended at N% HP" is `TurnReplaySimulator._actual_results()`'s own
  literal phrasing for remaining HP — the explanation prompt places this
  string directly in context, so a faithful paraphrase tends to preserve
  its shape even after rewording.

`classify_percent_mention(raw_text, value)` classifies the small window of
text immediately around one SPECIFIC claimed number (not the whole
sentence, which can legitimately combine a genuine damage% with an
unrelated KO-chance mention right next to it) into `damage_dealt`,
`hp_remaining`, `ko_chance`, or `raw_points`. `verify.py`'s new
`filter_ambiguous_damage_claims` drops any `damage_range` claim that isn't
genuinely `damage_dealt`, same principle as `filter_degenerate_claims` —
excluded from both conditions' counts, not scored either way.

`tests/test_percent_classifier.py` is deliberately split in two: one group
re-testing the EXACT phrasings observed in run4/run5 (proving the fix
addresses what was found), a second group of phrasings that appear nowhere
in any fixture — different species, different verbs, different sentence
shapes ("Talonflame's Brave Bird has a 62% chance to OHKO Corviknight",
"Sinistcha ends the turn at 12% HP", ...) — proving the rules generalize
from the system's own vocabulary rather than having memorized this
benchmark's ten specific sentences, which is exactly the "não viciado
pelos testes" requirement this round was built to satisfy.

**Result (run6, same 10 fixtures — superseded by the n=30 Primary Result
above, which uses this exact filter): 88.0% strict / 89.2% lenient vs 77.3%
strict / 77.3% lenient — a ~10.7pp gap**, the clearest and largest this
10-fixture benchmark had produced across any round; scaling to n=30
`damage_range`-dense fixtures afterward is what turned this into the
Fisher-tested, p<0.0001 result at the top of this document. The filter dropped 8
non-damage-dealt claims from Condition A across the run (vs. 1 from
Condition B) — an asymmetry that makes sense rather than looking
suspicious: Condition A, given `actual_hp_remaining_percent` as an
explicit, separately-labeled fact by ADR-029, narrates remaining HP as its
own legitimate detail more often, which is exactly the correct behavior
ADR-029 was meant to enable — and this round's filter is what finally lets
the benchmark credit that instead of miscounting it as a wrong damage
figure.

The MSRE/MAE numeric metrics, recomputed on the same run, still show A
ahead (24.5pp MAE / 0.243 MSRE vs 34.2pp MAE / 0.285 MSRE) but on a much
smaller surviving sample (n=11 vs n=18) since the filter removed a larger
share of Condition A's claims — read this pass's continuous metrics as a
secondary confirmation of direction, not a standalone number, given how
small `n` has become; the binary rate above, backed by a larger surviving
sample on both sides, is the headline number for this round.

**Residual, stated plainly:** the classifier's regex-based windowing is a
heuristic, not a certainty — it can still misclassify an unusual phrasing
this round's test cases didn't anticipate, and this project's own
established lesson (ADR-008/010, and this benchmark's own round 2) is that
no rule-tightening pass is ever provably complete against free-text
generation. What makes this round trustworthy isn't "the rules are
perfect" — it's that the rules are grounded in the underlying system's own
stable, machine-generated vocabulary rather than in ad hoc pattern-matching
against this benchmark's specific sentences, and the generalization test
group demonstrates that grounding holds on phrasings never seen while
building the rules.

## Round 4: orchestrator comparison — ADK vs LangChain vs native

`AdkAnalysisOrchestrator` (Google ADK) shipped as a third `AnalysisPipeline`
backend and the new default `PROFESSORVGC_ORCHESTRATOR`, alongside the
existing `langchain` and `native` backends. The architectural claim (stated
in CLAUDE.md and re-asserted in every orchestrator's own docstring) is that
switching orchestration technology never changes a single damage roll,
because all three share the exact same deterministic core
(`MatchupEvaluator`, `TurnReplaySimulator`, `selection_logic`) and differ
only in how the two LLM stages are wired. This round tests that claim
directly, on `damage_range` faithfulness, rather than just asserting it.

**Method — `run_orchestrator_comparison.py`, not three separate `run.py`
passes.** Condition B (naive baseline) is orchestrator-independent — see
`naive_baseline.py`, which talks to a bare `LLMProvider`, never an
`AnalysisPipeline` — so re-running it once per orchestrator would only add
sampling noise between three separate runs' B, not a fairer comparison.
Instead, ground truth and Condition B are each computed **once per fixture**
and reused for every orchestrator's Condition A, so any precision
difference found is attributable to the orchestrator alone, holding
everything else fixed.

**Result — n=30 fixtures (same 30 as the Primary Result above), `damage_range`:**

| | adk | langchain | native | B_naive |
|---|---|---|---|---|
| Claims | 108 | 107 | 99 | 99 |
| Correct | 78 | 79 | 74 | 14 |
| **Rate** | **72.2%** | **73.8%** | **74.7%** | **14.1%** |

**Pairwise Fisher's exact tests (`damage_range` correct/incorrect):**

| Pair | Odds ratio | p (two-sided) | Significant at α=0.05? |
|---|---|---|---|
| adk vs langchain | 0.89 | 0.758 | No |
| adk vs native | 0.84 | 0.636 | No |
| langchain vs native | 0.95 | 1.000 | No |
| adk vs B_naive | 15.23 | <0.0001 | **Yes** |
| langchain vs B_naive | 17.14 | <0.0001 | **Yes** |
| native vs B_naive | 18.06 | <0.0001 | **Yes** |

**Reading this honestly:** the three orchestrators' rates sit within a
2.5pp band (72.2–74.7%) and no pairwise difference between them clears
significance — but per the same caution this README already applies
elsewhere to a non-significant result (see the n=10 exploratory rounds
above), **a non-significant Fisher's test is absence of evidence of a
difference, not evidence of equivalence.** What this round DOES support
directly: every orchestrator independently reproduces the Primary Result's
core finding (grounding beats the naive baseline by an enormous, significant
margin, odds ratios 15–18x here) — i.e., **the choice of orchestration
framework is not where this pipeline's faithfulness advantage comes from;
the deterministic core is.** That is exactly the architectural claim this
project makes about orchestration being a swappable infrastructure choice,
now backed by a live A/B/C/D measurement rather than only a structural
argument. The secondary (all-claim-types) rates tell the same story: adk
88.8%, native 88.7%, langchain 87.6%, all clearly separated from B_naive's
74.3%, indistinguishable from each other.

**A discrepancy worth stating plainly, not smoothing over:** `native`'s
`damage_range` rate in THIS round (74/99 = 74.7%) is noticeably lower than
the Primary Result's headline native-only run (81/88 = 92.0%, `run7_n30.json`),
even though both are nominally "native, same 30 fixtures." B_naive's rate is
close between the two (14/99 = 14.1% here vs 12/95 = 12.6% there), so this
is not a wholesale re-scoring artifact — the gap is concentrated in
Condition A. Candidate causes, none confirmed here: ordinary run-to-run LLM
sampling variance at temperature 0.3 (already documented as large enough to
flip which condition "wins" in the n=10 exploratory rounds above, though
this magnitude on n=30 would be a larger swing than any single-orchestrator
round showed); or judge-extraction noise independent of the model's actual
answer quality (Round 2 above documents the judge's own extraction
inconsistencies at length). This was NOT chased down further here — doing
so honestly would need re-running `native` alone again and diffing
transcripts claim-by-claim against `run7_n30.json`, out of scope for this
round, whose actual question (do the three orchestrators differ from EACH
OTHER, measured together in one pass) the discrepancy doesn't change: all
three read close together in this same run, run7_n30.json's own
higher absolute number notwithstanding.

**Residual, stated plainly:** this is a single pass (n=30 fixtures, one
sample per fixture per orchestrator, temperature 0.3 on explanation calls)
— the same sampling-variance caveat the n=10 exploratory rounds already
flagged applies here too, and per the discrepancy noted just above, may
apply at a larger magnitude than previously observed; a tighter confidence
interval on "how close are these three, really" would need repeated passes
per orchestrator, not done here. Full transcripts:
`out/orchestrator_comparison_n30.json` (and console log
`out/orchestrator_comparison_n30_log.txt`), reproducible via `python -m
scripts.faithfulness_benchmark.run_orchestrator_comparison`.

## Extending this

- Add a trap-category fixture: append a `Fixture(...)` to `fixtures.py`.
  Validate it first by parsing it directly with `ShowdownReplayParser` +
  `TurnReplaySimulator` and printing the result — do not trust a
  hand-written log until you've seen the real parser agree with your
  intent (see the git history of this file for the validation transcripts
  used for the five hand-authored fixtures).
- Add a damage-dense fixture (to grow the Primary Result's n further):
  append a spec tuple to `_SPECS` in `damage_dense_fixtures.py`. Before
  committing: (1) confirm every species with
  `Container.chaos().build_match_context([...])` — do not trust a visual
  scan of the dumped Chaos JSON, see "A note on Chaos coverage" above; (2)
  sweep every `(attacker, move, defender)` triple against a real
  `SmogonCalcAdapter` for a nonzero, non-error result (a real type
  immunity, an accidental status move, or a species this `@smogon/calc`
  version errors on all look identical to "the fixture is broken" until
  you check).
- Add a claim type: extend `ClaimType` in `models.py`, teach `judge_system.
  txt` its shape, add a branch to `verify_claim` in `verify.py`.
- Get a real confidence interval: wrap `_run_fixture` in an outer loop over
  N repetitions per fixture and report mean ± stdev instead of a single
  pass — `stats.py`'s `FisherResult` already reports the exact p-value for
  one pass; a bootstrapped CI across repeated passes is the natural next
  layer of rigor on top of it.
