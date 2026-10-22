# BO3 validation snapshot

Run: 2026-10-21T21:13:40.984993+00:00. Engine: `0.12.0`.
Rule profile: `champions-stat-points-66-32-v1`. Python 3.12.10.

| Gate / measurement | Result |
|---|---:|
| Synthetic three-game series passed | 26/26 |
| Games in the synthetic corpus | 78 |
| Independent analytic oracle cases passed | 17/17 |
| Truth retained in reported outer ranges | 26/26 |
| Exhaustive scenarios | 24 |
| Intentionally bounded scenarios | 2 |
| Production engine projections | 31,945 |
| Median per-series latency | 0.324 s |
| Maximum per-series latency | 3.756 s |
| Marginal width reduction, exhaustive scenarios | 0.0% to 100.0% |
| Independently recorded real-game series | 0 |

The 12 baseline series cover physical/special damage, three investment/nature
profiles, and exact/rounded HP. Twelve variants swap sides and rename nicknames;
all preserve semantic results. Two controls constrain assignments/projections;
neither claims uniqueness. Each series uses low, middle and high rolls across
three games. All exhaustive cases consume the expected damage observations and
preserve truth under an evaluation-only constraint probe.

These are engine-generated consistency fixtures and an independent analytic
search oracle. They do **not** establish real-game inference accuracy, calibrated
confidence or full joint-truth retention. Outer intervals are marginal bounds;
interval-width reduction is not joint candidate-volume reduction. Timing is local
and observational. No model/API calls were made.

- [Frozen corpus](fixtures.json)
- [Full machine-readable report](results.json)
- [Reproduction and judging methodology](../../../scripts/bo3_inference_benchmark/README.md)

Corpus SHA-256 (canonical Pydantic JSON): `42ec36a4c2db176fa7ae2d6bf0bd1c8edc4a5b1f494b316259dd4d4b7d84c96c`.

## User-reported real series 2702605271

The [public-series regression report](sauron-public-regression.json) records
sauronMobile vs miro kart ds, final score 1-2. The uploaded games' metadata
confirms the exact BO3 room and game order. Twelve natures are declared in every
game; hidden investment is unavailable. Spectator chat is removed from the
versioned battle fixture.

Normalizing `move:` on redirection effects, and treating Disable/Throat Chop as
move-choice restrictions, restores eligible sample counts from 1/1/0 to 4/5/2.
The actual worker verifies these counts. Unsupported survival mechanics,
variable-power/critical/multihit attacks and active ability effects remain explicit.

The corrected 60-second / 120,000-projection and 120-second / 200,000-projection
searches both remain bounded, without narrowing any stat-point range. Neither run
proves global feasibility or that no tighter constraint could be found by another
search. The result validates acquisition and eligibility, not spread accuracy.
It must not be converted into assumed 32/32 builds. These are manual observations
on one installation, not a timing guarantee. No model calls were used.

To run the fast offline real-protocol regressions:

```bash
python -m pytest -q tests/test_public_teamsheets.py tests/test_bo3_inference_engine.py -k sauron
```

The original live room had expired. Importing an uploaded seed populates the local
public-link index, after which the expired room URL resolves on that installation.
Fresh installations can use the game's public replay URL as their seed.
