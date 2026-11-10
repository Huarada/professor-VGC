# Recorded BO3 deterministic validation plan

Scope: the testProfessor/Huarada series with user-supplied reference spreads.
This plan validates damage compatibility and conservative public inference. It
does not require exact recovery of an unidentifiable spread.

## 1. Inputs and source boundaries

Public replay IDs, in chronological order:

1. [gen9championsvgc2026regmcbo3-2709311277](https://replay.pokemonshowdown.com/gen9championsvgc2026regmcbo3-2709311277)
2. [gen9championsvgc2026regmcbo3-2709313348](https://replay.pokemonshowdown.com/gen9championsvgc2026regmcbo3-2709313348)
3. [gen9championsvgc2026regmcbo3-2709316498](https://replay.pokemonshowdown.com/gen9championsvgc2026regmcbo3-2709316498)

Use the archived [public logs](../../tests/fixtures/series/bo3-public-2709311277.json)
for reproducible offline tests. The separate
[reference spreads](../../tests/fixtures/series/bo3-truth-2709311277.json) are scoring
data only. Never paste them into the public-inference input during this test.

The registered identity is participant plus slot, not species or Dex number.
Both players have Garchomp with different natures/spreads. Champions uses fixed
IVs of 31 and native points 0-32 per stat, with a total at most 66.

Known spreads, declared natures and replay events are reference facts. The actual
random damage rolls and all exact private HP values were not supplied. Integer
roll generation and compatibility checks are deterministic; the original spread
can still be ambiguous. Damage is produced by the native Smogon worker. The
independent oracle checks both HP readings and boundary colors, not a second
independent implementation of the entire damage formula.

## 2. Automated acceptance matrix

Run commands from the `professor-VGC` repository root, using the existing Python
environment and installed `node_calc` dependencies. These checks require no model,
embeddings, database writes or live replay downloads.

| ID | Check | Expected result / failure criterion |
|---|---|---|
| D01 | Source provenance | Public/truth SHA-256 and engine/rule metadata recorded. Reference data loads only after the public search finishes. |
| D02 | Coverage | Exactly 44 unique direct-hit IDs: 10 / 18 / 16 per game. Exactly 15 involve Floette. Missing, duplicated or empty damage collections fail. |
| D03 | Known reference calibration | All 44 hits have a native damage witness matching **both** initial and final integer HP displays. All 12 registered natures match the reference. |
| D04 | HP boundaries | Floor rounding, living 1%, exact full HP at 100%, zero on faint, and 20/50 color qualifiers pass fixed integer examples. Invalid before-HP must fail even if after-HP matches. |
| D05 | Causal effects | Removing real aura, burn, Last Respects faint count, spread reduction, boosts, Life Orb or terrain changes native roll endpoints in the expected direction. |
| D06 | Healing / residual / Mega | Sitrus changes Incineroar HP 12% to 37% before the final Wave Crash. Recoil/burn changes attacker HP separately. Mega abilities and stat-stage resets follow event time. |
| D07 | Connected HP | The recorded Rillaboom and Raichu two-attack sequences share at least one integer HP witness. This checks those pairs, not the entire battle HP trajectory. |
| D08 | BO3 chronology | After-game observation counts are 10 / 28 / 44. Before-game checkpoints contain earlier games only. Existing bounds do not widen within this registered series. |
| D09 | Identity invariance | Swapping every game's sides, swapping only game 2, renaming actors, and combining swaps/renames preserve canonical causal requests and series score. |
| D10 | Public truth retention | Every reported bound contains the supplied true point value. A wrong known spread, unsupported projection or uncertified narrowing fails the report. |
| D11 | Useful public inference | At least one certified bound narrows. Under the saved 12,000-projection work cap, Gholdengo HP points narrow 0-30 / 2-30 / 4-30, retaining the true 27. |
| D12 | Honest uncertainty | Public Floette remains unresolved; unplayed Charizard stays at 0-32. Unsupported candidates are unresolved, not certified exclusions. |
| D13 | Conditional Floette oracle | Fixing other supplied attributes for validation gives SpA 17-32 / 27-32 / 27-32. True 32 survives; no exact identification is claimed. |
| D14 | Repetition / resources | Two identical native batches produce identical support. Projection caps are respected; a game cannot consume later games' reserved budget. |

Run the targeted regression tests:

```powershell
.venv-windows\Scripts\python.exe -m pytest tests/test_real_series_testbench.py tests/test_known_spread_series.py tests/test_bo3_game_budget.py -q
```

Run native worker tests independently:

```powershell
npm --prefix node_calc test
```

Then generate a fresh report:

```powershell
.venv-windows\Scripts\python.exe -m scripts.bo3_real_series_benchmark --out .cache/bo3-deterministic-review.json
```

Acceptance requires process exit code 0, `schema_version: 2`, `passed: true` and
every entry in `checks` equal to true. Inspect failures by their check name and
damage ID; never delete a failed row to obtain a passing result. The checked-in
[report](validation/real-series-2709311277.json) is the comparison artifact.

## 3. Expected numerical results and how to interpret them

| Checkpoint | Cumulative usable hits | Floette SpA, conditional oracle | Gholdengo HP points, public benchmark |
|---|---:|---|---|
| After game 1 | 10 | 17-32 | 0-30 |
| After game 2 | 28 | 27-32 | 2-30 |
| After game 3 | 44 | 27-32 | 4-30 |

These columns use different knowledge. The Floette oracle fixes the other hidden
attributes to their supplied values. Public inference never receives them and
still leaves Floette at 0-32. Do not replace public bounds with conditional ones.
All numbers above are **native points**, not the resulting level-50 stat values.

The benchmark uses 12,000 projections / 12,000 assignments, not the application's
larger default projection budget. Exact public bounds can change under different
work budgets or wall-clock exhaustion. Always require truth retention, causal
cutoffs, explicit limitations and certified exclusions; do not require the UI to
reproduce these exact benchmark endpoints under different settings.

Seventeen hits end in KOs and therefore censor the damage at the remaining HP.
More appearances or more KOs do not guarantee a unique spread. An alternative
legal Floette allocation also matches the 44 modeled local damage observations.
These tests do not prove a complete, globally consistent HP trajectory or general
accuracy across the metagame.

## 4. Optional manual application check

This checks rendering/explanation and uses the configured model when submitting
an analysis; it is separate from the offline deterministic checks above.

Start the local app:

```powershell
.venv-windows\Scripts\python.exe scripts/run_local.py --server.port 8502
```

1. Open `http://localhost:8502`, choose BO3, and supply the three replays in order.
   If live links are unavailable, paste each archived game's `log` value. Keep the
   public perspective and do not enter the known hidden point allocations.
2. Check score **Huarada 2 / testProfessor 1** and three game reviews. The import
   should identify 12 registered Pokemon and declared natures.
3. Inspect each game's inference checkpoint. Verify accumulation 10 / 28 / 44,
   no future evidence in earlier reviews, no conflict with supplied true points,
   and visible limitations if a search budget expires.
4. Check that original spreads are distinguished from suggested changes. Exact
   Floette 32 SpA must not be presented as proven. Conditional 27-32 is a testbench
   result and must not appear as a public inference result for this run.
5. Check level-50 attribute ranges separately from native point bounds. Mega
   Floette uses Fairy Aura; its base teamsheet ability is a separate fact.
6. Start a separate BO3 analysis and verify evidence from this series is not
   reused solely because a Pokemon name/species matches.

If a manual check fails, retain the replay IDs, selected perspective, search
options, checkpoint/claim source, error text and the generated JSON report.
Generic coaching prose alone is insufficient evidence of a calculator failure.

## 5. Completion gate

After changes, run the full suite and static checks. On Windows, initialize the
existing system-certificate helper and use the bundled LiteLLM cost map so SDK
imports do not attempt a remote price-catalog lookup:

```powershell
$env:LITELLM_LOCAL_MODEL_COST_MAP = "True"
.venv-windows\Scripts\python.exe -c "from scripts.local_runtime import configure_trust; configure_trust(windows=True); import pytest; raise SystemExit(pytest.main(['-q']))"
.venv-windows\Scripts\python.exe -m mypy src scripts/bo3_real_series_benchmark.py
.venv-windows\Scripts\python.exe -m ruff check src scripts tests --select F
```

Record counts, input hashes, engine version, command results and any remaining
limitations. A green offline report validates the recorded deterministic cases;
it does not establish model explanation quality or exact recovery of every spread.


## 6. Gemini deadline regressions (offline)

```powershell
.venv-windows\Scripts\python.exe -m pytest tests/test_gemini_recovery.py tests/test_bo3_analysis.py -q
```

These tests use mocks and a fake monotonic clock; they make no paid model calls.
A direct Gemini completion defaults to 90 seconds per attempt and 180 seconds
total, including backoff, with at most three transient attempts. The second
attempt receives only the remaining time; resetting the whole budget on retry
must fail the tests. Permanent errors must not be retried. Deadline messages
identify either the BO3 game review or series summary. These checks validate
client policy, not remote service availability or model answer quality.
