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

Result on the one recorded series (2709311277, 72 Pokemon stats, final checkpoint):

| | Propagation (ADR-068) | Legacy (ADR-062) |
|---|---|---|
| Sound | 72/72 (95% CI 94.9-100%) | 72/72 |
| Narrowed stats | 21 (29.2%) | 1 (1.4%) |
| Mean range removed | 11.5% | 0.3% |
| Narrowed stats that are single-observation | 21 of 21 | 0 of 1 |
| Soundness, player vs opponent | 36/36 and 36/36 | 36/36 and 36/36 |

Every narrowed stat depends on one hit, so one mis-modeled hit would break it. That is the
main weakness to attack next; more series are the way to see it fail or hold.

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

- Series from your own accounts reflect your play style and team shapes, not the ladder.
- A truth file is scoring data only: the search never reads it, and a test checks it is
  opened after inference ends.
- Matching a spread to the hits does not prove one global HP timeline.
- This scores stat-point inference. The text the model writes is checked separately
  (narrative guards and `scripts/bo3_depth_benchmark.py`).
