"""Controlled experiment: does the judge (`judge.py::extract_claims`) treat
a damage claim differently based on PHRASING STYLE alone?

This is a follow-up to the benchmark's own bias self-audit (see README's
"Bias self-audit" section). One of the four audit questions was "was the
judge blind to which condition produced the text?" -- the code answer is
unambiguous (`extract_claims(llm, answer_text)` never receives a condition
label, see judge.py's docstring). But a subtler, un-code-provable risk
remains: Condition A's prose tends to be precise/confident ("dealt 62-74%,
a guaranteed 2HKO") while Condition B's naive prose tends to hedge ("I'd
guess somewhere around 62-74% or so, hard to say exactly"). If the judge
extracts hedgy phrasing LESS reliably than confident phrasing -- even
though it never knows which condition wrote it -- that would still
structurally penalize Condition B's TRUE statements more often than
Condition A's, independent of any label leak.

This script tests that directly: N base facts, each phrased once in
"confident/grounded style" and once in "hedgy/naive style", with the exact
same stated numeric range. Extraction fidelity to what the text actually
says (not to any external ground truth -- there is none here, correctness
isn't the point) is compared between the two styles. If extraction rate and
numeric fidelity are statistically indistinguishable between styles, that
is real evidence -- not a recollection, not a hypothesis -- against a
structural pro-confident/anti-hedge bias in the judge.

Usage:
    python -m scripts.faithfulness_benchmark.style_blindness_check
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # repo root

from src.services.container import Container

from scripts.faithfulness_benchmark.judge import extract_claims

_OUT_DIR = Path(__file__).resolve().parent / "out"

# Each entry: (base_id, min_pct, max_pct, confident_text, hedgy_text).
# The stated numeric range is IDENTICAL within a pair -- only the framing
# differs. Species/moves are varied and plausible but not tied to any real
# fixture; nothing here is verified against real ground truth on purpose,
# because this experiment measures EXTRACTION FIDELITY TO THE TEXT, not
# factual correctness (verify.py's job, which is 100% deterministic Python
# and therefore cannot be style-biased by construction -- the only place
# style COULD leak in is the judge's extraction step, which is what this
# isolates).
_PAIRS: list[tuple[str, float, float, str, str]] = [
    (
        "garchomp_eq_staraptor", 62.0, 74.0,
        "Garchomp's Earthquake hit Staraptor for 62-74% of its health, "
        "putting it in range for a guaranteed 2HKO next turn.",
        "I think Garchomp's Earthquake probably did somewhere around "
        "62-74% or so to Staraptor -- hard to say exactly, but it looked "
        "like a solid chunk of its health.",
    ),
    (
        "fluttermane_moonblast_torkoal", 88.0, 104.0,
        "Flutter Mane's Moonblast dealt 88-104% to Torkoal, an outright OHKO.",
        "Flutter Mane's Moonblast maybe took off something like 88 to 104 "
        "percent from Torkoal? It's rough to tell, but it seemed close to "
        "a knockout.",
    ),
    (
        "kingambit_suckerpunch_gengar", 35.0, 42.0,
        "Kingambit's Sucker Punch connected for 35-42% against Gengar.",
        "Kingambit's Sucker Punch, I'd guess, did somewhere in the 35 to "
        "42 percent range on Gengar -- not totally certain though.",
    ),
    (
        "basculegion_wavecrash_raichu", 50.0, 59.0,
        "Basculegion's Wave Crash dealt 50-59% to Raichu, nearly a 2HKO.",
        "Not 100% sure, but Basculegion's Wave Crash seemed to do roughly "
        "50-59% or thereabouts to Raichu.",
    ),
    (
        "amoonguss_sludgebomb_ironhands", 18.0, 22.0,
        "Amoonguss's Sludge Bomb did 18-22% to Iron Hands.",
        "Amoonguss's Sludge Bomb, best guess, was somewhere around 18 to "
        "22 percent on Iron Hands, though I could be off.",
    ),
    (
        "dragapult_shadowball_talonflame", 55.0, 65.0,
        "Dragapult's Shadow Ball hit Talonflame for 55-65%, a likely 2HKO.",
        "Dragapult's Shadow Ball -- and again, I'm not fully sure -- "
        "probably landed somewhere near 55 to 65 percent on Talonflame.",
    ),
]

_TOLERANCE = 0.5  # percentage points; extraction is a text-parsing task, not OCR


def _extraction_ok(claim, expected_min: float, expected_max: float) -> bool:
    if claim.claim_type != "damage_range":
        return False
    if claim.min_percent is None or claim.max_percent is None:
        return False
    return (
        abs(claim.min_percent - expected_min) <= _TOLERANCE
        and abs(claim.max_percent - expected_max) <= _TOLERANCE
    )


def main() -> None:
    container = Container()
    llm = container.build_llm()

    rows = []
    for base_id, lo, hi, confident_text, hedgy_text in _PAIRS:
        for style, text in (("confident", confident_text), ("hedgy", hedgy_text)):
            claims = extract_claims(llm, text)
            dmg_claims = [c for c in claims if c.claim_type == "damage_range"]
            extracted = len(dmg_claims) >= 1
            fidelity = any(_extraction_ok(c, lo, hi) for c in dmg_claims) if extracted else False
            rows.append({
                "base_id": base_id,
                "style": style,
                "text": text,
                "expected_range": [lo, hi],
                "n_claims_extracted": len(claims),
                "n_damage_claims_extracted": len(dmg_claims),
                "extracted_any_damage_claim": extracted,
                "extracted_value_matches_text": fidelity,
                "raw_extracted": [c.model_dump() for c in dmg_claims],
            })
            tag = "OK " if fidelity else ("PARTIAL" if extracted else "MISS")
            print(f"[{tag}] {base_id:32s} {style:10s} -> "
                  f"{len(dmg_claims)} damage claim(s), fidelity={fidelity}")

    confident_rows = [r for r in rows if r["style"] == "confident"]
    hedgy_rows = [r for r in rows if r["style"] == "hedgy"]

    def rate(rs, key):
        return sum(1 for r in rs if r[key]) / len(rs) if rs else None

    summary = {
        "n_pairs": len(_PAIRS),
        "confident": {
            "extraction_rate": rate(confident_rows, "extracted_any_damage_claim"),
            "fidelity_rate": rate(confident_rows, "extracted_value_matches_text"),
        },
        "hedgy": {
            "extraction_rate": rate(hedgy_rows, "extracted_any_damage_claim"),
            "fidelity_rate": rate(hedgy_rows, "extracted_value_matches_text"),
        },
    }

    print("\n=== STYLE-BLINDNESS CHECK SUMMARY ===")
    print(f"Confident style: extraction={summary['confident']['extraction_rate']:.1%}  "
          f"fidelity={summary['confident']['fidelity_rate']:.1%}")
    print(f"Hedgy style:     extraction={summary['hedgy']['extraction_rate']:.1%}  "
          f"fidelity={summary['hedgy']['fidelity_rate']:.1%}")
    gap = summary["confident"]["fidelity_rate"] - summary["hedgy"]["fidelity_rate"]
    print(f"Fidelity gap (confident - hedgy): {gap:+.1%}")

    _OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = _OUT_DIR / f"style_blindness-{int(time.time())}.json"
    out_path.write_text(json.dumps({"summary": summary, "rows": rows}, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nFull report written to {out_path}")

    container.shutdown()


if __name__ == "__main__":
    main()
