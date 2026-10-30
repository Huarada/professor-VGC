# Validation evidence and benchmark history

These are archived measurements from the runs described below, not a fresh
measurement of the current checkout or of hidden-spread identification accuracy.
For BO3 inference gates and their limitations, see [the validation snapshot](bo3-inference/validation/README.md).
Return to the [project overview](../README.md).

## How well does it match real games?

Measured, not asserted — against what **actually happened** in real public
Showdown games, not against the pipeline's own numbers.

**The external truth is the battle log.** An independent reader
([`observed_damage.py`](../scripts/faithfulness_benchmark/observed_damage.py),
which imports nothing from `src/`) takes the damage each hit really did from
the raw `-damage` lines. Both the AI's damage claims and the engine's projected
damage ranges are checked against it, on 40 recent public
`gen9championsvgc2026regmb` replays.

**Agreement is judged within a tolerance band, not as an exact match:** ±2
percentage points because the log shows HP rounded to whole percents, plus a
relative **±5%** because a real Pokémon's EVs/nature differ from the one spread
the projection assumes. Every rate carries a **95% confidence interval**
(Wilson), and the comparison an exact interval for its odds ratio.

| Checked against the real log (±2pp ±5%) | Rate (95% CI) |
|---|---|
| AI damage claims — **A**: the grounded pipeline | **61.2% (56.4–65.8%)**, 251/410 |
| AI damage claims — **B**: the same LLM given only the raw log | 52.2% (47.1–57.3%), 190/364 |
| A vs B | odds ratio **1.45 (1.08–1.94)**, p = 0.013 |
| Claims about hits that never happened | A: 11 · B: 87 |
| Engine projection contains the real damage (non-KO hits, no LLM) | **59.7% (54.5–64.7%)**, 209/350 |
| Engine projection reaches a real KO | 94.7% (90.7–97.0%), 195/206 |
| Engine misses explained by EV/nature variance alone | 100% (97.5–100%), 152/152 |

> **Engine rows re-measured (ADR-039, ADR-040).** Same 40 games, 588 observed
> hits. Usage data names abilities and items by id (`lifeorb`) and the calc
> engine silently ignored them (ADR-039: non-KO 53.2% -> 55.5%). Then every
> miss that no EV/nature spread explained (32) was traced to a cause:
> Illusion, Last Respects / Eruption / Supreme Overlord missing the battle
> state they scale with, usage data not found for rare species or Megas,
> Transform, forme timing, a resist berry the log shows was not there, moves
> the engine cannot project — all fixed (ADR-040: non-KO 57.7%, KOs 93.9%,
> mean miss 11.4pp -> 8.9pp). Pokemon Champions games are now calculated under
> Champions' own damage rules and stat points (ADR-041), multi-hit moves
> count the hits the log shows, and abilities the log reveals through
> `[from] ability:` are used, and a spread move that hit one target takes no
> 0.75x reduction (ADR-042: Reg M-B non-KO 59.7%, Reg M-C 66.8%, no Reg M-B
> miss left outside the EV/nature envelope; Focus Sash survivals now count as
> the lethal hits they were). The AI-claim rows (A vs B) were re-run on the
> current code (gpt-4o-mini, 40 games, 2 runs of 20) and use the official
> usage data.

> **Data note (ADR-035).** All rows use the official September 2026 VGC usage
> data. (Earlier versions used bundled dumps that turned out to be singles
> stats: the engine row was 47.9% then, and the first A vs B run used them.)

The conclusion does not hinge on the tolerance chosen — the same claims
re-scored:

| Relative tolerance | A (95% CI) | B (95% CI) | Odds ratio (95% CI) | p |
|---|---|---|---|---|
| 0% (±2pp only) | 58.8% (54.0–63.4%) | 50.8% (45.7–55.9%) | 1.38 (1.03–1.85) | 0.030 |
| **5%** | **61.2% (56.4–65.8%)** | **52.2% (47.1–57.3%)** | **1.45 (1.08–1.94)** | **0.013** |
| 10% | 65.4% (60.6–69.8%) | 53.3% (48.2–58.4%) | 1.65 (1.23–2.23) | 0.0007 |
| 15% | 69.8% (65.1–74.0%) | 56.3% (51.2–61.3%) | 1.79 (1.32–2.43) | 0.0001 |

**What it means.**

- **Grounding helps, modestly.** About 9 points more claims consistent with
  the log, significant at every tolerance, and eight times fewer invented hits.
  It is not the 80× an earlier, circular benchmark suggested.
- **The ceiling is the assumed sets, not the engine.** Unrevealed
  EVs/natures/items/abilities are backed off the single most-used Chaos set.
  ~70% of the engine's misses fit some other EV/nature spread; the rest come
  from unrevealed abilities or items (e.g. Liquid Voice, Scrappy) or species
  without usage data. Projected damage is a baseline; the log's observed
  damage is what happened.
- **Pool runs over distinct games.** LLM output varies from run to run: each
  run of 20 games alone was borderline at ±5% (p = 0.054 and 0.039).
  Results are reported pooled over different games, with intervals.

Methodology, every run, the sensitivity analysis, the EV/nature envelope and
the bias audit: [`scripts/faithfulness_benchmark/README.md`](../scripts/faithfulness_benchmark/README.md)
("Round 6"); decisions: ADR-033 and ADR-034 in [`ADR.md`](../ADR.md).

<details>
<summary>Earlier rounds: faithfulness to the evidence (circular for damage)</summary>

The first benchmark rounds used hand-authored fixtures and checked each claim
against the pipeline's **own projected ranges** — the very numbers Condition A
was given. That measures faithfulness to the evidence, not correctness, and
judges Condition B against assumptions it never saw, which is why its odds
ratios were so large:

| Provider / model | Orchestrator | Grounded | Naive | Odds ratio | p |
|---|---|---|---|---|---|
| OpenAI gpt-4o-mini | native | 92.0% | 12.6% | 80.0 | <0.0001 |
| OpenAI gpt-4o-mini | adk | 72.2% | 14.1% | 15.23 | <0.0001 |
| OpenAI gpt-4o-mini | langchain | 73.8% | 14.1% | 17.14 | <0.0001 |
| Gemini 3.5-flash | adk (default) | 73.3% | 11.0% | 22.27 | 2.70e-15 |

Use these only as evidence that the explanation repeats its evidence
faithfully; cite the real-game table above for correctness.

</details>

### Reproduce

```bash
python -m scripts.faithfulness_benchmark.replay_corpus --count 40              # public replays -> data/replays/cache (git-ignored)
python -m scripts.faithfulness_benchmark.chaos_corpus                          # official VGC usage tiers -> data/chaos-cache (git-ignored)
python -m scripts.faithfulness_benchmark.run_engine_calibration --chaos data/chaos-cache  # engine vs real damage, no LLM
python -m scripts.faithfulness_benchmark.run_log_grounded --provider openai --chaos data/chaos-cache --limit 20             # games 1-20 (API calls)
python -m scripts.faithfulness_benchmark.run_log_grounded --provider openai --chaos data/chaos-cache --limit 20 --offset 20 # games 21-40
python -m scripts.faithfulness_benchmark.rescore_log_grounded out/RUN1.json out/RUN2.json --relative-tolerance 0.05  # pool + any band, no API calls
```

`--relative-tolerance` sets the band (default 0.05); `rescore_log_grounded`
refuses to pool runs that share a game.

Faithfulness runs on the hand-authored fixtures (A vs. B against the
pipeline's own evidence; LLM calls):

```bash
python -m scripts.faithfulness_benchmark.run --limit 2                          # smoke test, 2 fixtures
python -m scripts.faithfulness_benchmark.run                                    # all 30 fixtures + Fisher's exact test
python -m scripts.faithfulness_benchmark.run --orchestrator adk --provider gemini
python -m scripts.faithfulness_benchmark.run_orchestrator_comparison            # adk vs langchain vs native
python -m scripts.faithfulness_benchmark.style_blindness_check                  # judge bias self-audit
python -m scripts.faithfulness_benchmark.damage_error_metrics out/RUN.json      # MAE/RMSE of a saved run, no API calls
pytest tests/test_faithfulness_benchmark_verify.py tests/test_percent_classifier.py tests/test_benchmark_stats.py -q  # harness only, offline
```

Reports go to `scripts/faithfulness_benchmark/out/` (git-ignored). Every
command and option: [scripts/faithfulness_benchmark/README.md](../scripts/faithfulness_benchmark/README.md#running-it).
