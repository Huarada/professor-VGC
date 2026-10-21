# BO3 inference testbench

Runs offline with Python and the installed Champions Node worker. No provider
keys, model calls, Chaos enrichment, Firestore access or replay downloads.

From the repository root:

```bash
python -m scripts.bo3_inference_benchmark.run --out /tmp/bo3-validation.json
python -m pytest -q tests/test_bo3_inference.py tests/test_bo3_inference_engine.py tests/test_bo3_benchmark.py
```

On Windows, replace `/tmp/bo3-validation.json` with a writable report path.
The CLI exits **0** when all checks pass, **1** for failed judgments, and **2**
for invalid inputs or infrastructure errors. A missing worker is a failure in
this bench, never a successful skipped evaluation. CI runs the corpus on Python
3.12 in addition to the normal Python 3.10/3.12 test matrix.

## Two sources of evidence

1. **Independent analytic oracle:** 17 attack-minus-defense differences, with a
   brute-force enumeration of 33 x 33 pairs. Compares joint family counts, marginal
   extrema, retained joint examples and uniqueness against the production solver.
   The simple analytic damage port validates search logic, not game mechanics.
2. **Frozen engine-generated corpus:** 26 three-game series (78 games), combining
   physical/special attacks, three hidden defensive profiles, exact/public rounded
   HP, low/middle/high damage rolls, swapped sides and renamed nicknames. Two extra
   scenarios deliberately cap assignments or projections. These inputs validate
   engine/adapter/parser/solver consistency; they are not independently observed
   real-game damage and cannot establish real-world accuracy.

The submitted teamsheet omits the hidden target nature and relevant investment.
Evaluation truth is stored separately and never submitted to inference. Each run
compares the reported outer ranges with that truth and independently probes each
modeled damage constraint using the planted assignment. Neither check exposes the
entire joint relation: marginal retention alone cannot prove joint retention.

Further gates check exhaustive versus bounded coverage, no false uniqueness on
budget exhaustion, no conflicts, eligible observation counts, chronological
checkpoints, non-increasing compatible-family counts across these fixed-dimension
series, projection budgets, and side/nickname invariance. The unit suite separately
covers private/future knowledge, unsupported mechanics, failures and BO1 parity.

## Reports and interpretation

The [archived report](../../docs/bo3-inference/validation/results.json) includes
per-case checks, exact denominators, engine/rule versions, corpus SHA-256,
production projection counts, observed latency and limitations. The
[summary](../../docs/bo3-inference/validation/README.md) describes this run.

`marginal_width_reduction` compares the sum of reported interval widths before
game 1 and after game 3. It is not joint candidate-volume reduction, identification
accuracy or a confidence score. Production projection counts exclude fixture
generation and evaluation-only truth probes. Latency has no cross-machine gate.
Count reductions compare the same axes/nature grouping within each series; do
not compare family counts across unrelated scenarios as equal-sized search spaces.

## Reproducing and extending the corpus

The default command reads the committed
[`fixtures.json`](../../docs/bo3-inference/validation/fixtures.json); it does not
regenerate expected observations. Explicit regeneration after an intentional
engine/rule change is available:

```bash
python -m scripts.bo3_inference_benchmark.run --generate-fixtures --out /tmp/bo3-regenerated.json
```

Review fixture changes separately from the resulting metrics. Regeneration uses
the installed engine and overwrites the corpus path. `--corpus PATH` can select
another validated `Corpus` JSON file. Fixture names are unique and equivalence
references must point to earlier fixtures. The current judge expects one eligible
damage observation per game. For independent recorded fixtures, supply actual
sets under `truth`, keep hidden fields out of `teamsheet`, set provenance to
`independently_recorded`, and retain the record's source alongside that corpus.
The archived corpus currently contains **zero independently recorded fixtures**.
