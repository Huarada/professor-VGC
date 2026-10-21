"""Small analytic oracle, deliberately independent of Champions damage arithmetic."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from src.adapters.parsers.showdown_parser import ShowdownReplayParser
from src.adapters.teamsheet.json_teamsheet import JsonTeamSheetParser
from src.domain.bo3_inference.models import (
    DamageSupport,
    DamageWitness,
    InferenceOptions,
    MoveSupport,
    NatureSpec,
    ProjectionRequest,
    RuleProfile,
)
from src.domain.series import Bo3AnalysisRequest
from src.services.bo3_inference.coordinator import Bo3InferenceCoordinator
from src.services.series_analysis import prepare_series


class AnalyticOracle:
    """Test-only port: fixed HP 100 and damage 40 + attack points - defense points."""

    def rules(self) -> RuleProfile:
        return RuleProfile(version="analytic-oracle-v1", engine_version="independent-test-double",
                           natures=(NatureSpec(name="Hardy", plus="atk", minus="atk"),))

    def move_support(self, move: str) -> MoveSupport:
        return MoveSupport(supported=True, category="Physical")

    def project_batch(self, requests: Sequence[ProjectionRequest]) -> list[DamageSupport]:
        return [DamageSupport(supported=True, maximum_hp=100, witnesses=(DamageWitness(
            hp_before=100, damage=40 + request.attacker_points.get("atk", 0)
            - request.defender_points.get("def", 0)),)) for request in requests]


def validate_oracle() -> list[dict[str, Any]]:
    """Compare the solver with brute-force pairs; neither uses real engine damage."""
    rows = []
    for delta in range(-32, 33, 4):
        observed = 40 + delta
        # This exhaustive oracle never calls candidate generation or solver helpers.
        expected = [(attack, defense) for attack in range(33) for defense in range(33)
                    if 40 + attack - defense == observed]
        sheet = {"registered_team": True, "pokemon": [
            {"participant": "Alice", "slot": 1, "species": "Garchomp", "item": "",
             "ability": "Rough Skin", "nature": "Hardy",
             "stat_points": {"hp": 0, "def": 0, "spa": 0, "spd": 0, "spe": 0}},
            {"participant": "Bob", "slot": 1, "species": "Incineroar", "item": "",
             "ability": "Intimidate", "nature": "Hardy",
             "stat_points": {"hp": 0, "atk": 0, "spa": 0, "spd": 0, "spe": 0}},
        ]}
        source = "\n".join([
            "|tier|[Gen 9 Champions] VGC 2026 Reg M-B (Bo3)",
            "|player|p1|Alice|", "|player|p2|Bob|", "|poke|p1|Garchomp, L50|", "|poke|p2|Incineroar, L50|",
            "|switch|p1a: A|Garchomp, L50|100/100", "|switch|p2a: B|Incineroar, L50|100/100",
            "|turn|1", "|move|p1a: A|Dragon Claw|p2a: B", f"|-damage|p2a: B|{100-observed}/100", "|win|Alice",
        ])
        request = Bo3AnalysisRequest(session_id="oracle", games=[source], teamsheet=sheet,
                                     inference_options=InferenceOptions(hp_mode="exact"))
        evidence, states = prepare_series(ShowdownReplayParser(), request)
        result = Bo3InferenceCoordinator(sheets=JsonTeamSheetParser(), projection=AnalyticOracle()).analyze(
            request, evidence, states)
        assert result is not None
        checkpoint = result.checkpoints[-1]
        actual = {summary.pokemon_id: summary for summary in checkpoint.summaries}
        checks = {
            "exhaustive": checkpoint.coverage == "exhaustive",
            "joint_count": checkpoint.compatible_families == len(expected),
            "attack_range": (actual["Alice:slot1"].stat_points["atk"].minimum,
                             actual["Alice:slot1"].stat_points["atk"].maximum)
                            == (min(a for a, _ in expected), max(a for a, _ in expected)),
            "defense_range": (actual["Bob:slot1"].stat_points["def"].minimum,
                              actual["Bob:slot1"].stat_points["def"].maximum)
                             == (min(d for _, d in expected), max(d for _, d in expected)),
            "joint_examples": all((example["Alice:slot1"].points["atk"], example["Bob:slot1"].points["def"])
                                  in expected for example in checkpoint.joint_examples),
            "unique_only_at_boundary": (checkpoint.outcome == "identified_within_model") == (len(expected) == 1),
        }
        rows.append({"delta": delta, "expected_pairs": len(expected), "checks": checks, "passed": all(checks.values())})
    return rows
