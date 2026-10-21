"""Versioned, replayable planted fixtures; generation is engine self-consistency."""

from __future__ import annotations

import math
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.domain.bo3_inference.models import (
    DeclaredSet,
    HPReading,
    InferenceOptions,
    ProjectionRequest,
    StatName,
    TeamSheet,
)
from src.domain.interfaces import CandidateProjectionPort
from src.domain.models import CalcRequest, PokemonSet


class Fixture(BaseModel):
    """Truth is kept outside the submitted teamsheet and never sent to the solver."""

    model_config = ConfigDict(extra="forbid")
    name: str
    provenance: Literal["engine_generated", "independently_recorded"] = "engine_generated"
    games: list[str] = Field(min_length=1, max_length=3)
    teamsheet: TeamSheet
    truth: dict[str, DeclaredSet] = Field(min_length=1)
    options: InferenceOptions
    expected_coverage: Literal["exhaustive", "bounded"] = "exhaustive"
    equivalent_to: str | None = None

    @model_validator(mode="after")
    def declared_truth(self) -> Fixture:
        if not self.options.enabled:
            raise ValueError("A validation fixture must enable inference.")
        if any(not value.nature for value in self.truth.values()):
            raise ValueError("Ground truth must declare a nature.")
        return self


class Corpus(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[1] = 1
    generator_engine_version: str
    rule_version: str
    fixtures: list[Fixture] = Field(min_length=1)

    @model_validator(mode="after")
    def named_cases(self) -> Corpus:
        seen = set()
        for fixture in self.fixtures:
            if fixture.name in seen or fixture.equivalent_to and fixture.equivalent_to not in seen:
                raise ValueError("Fixture names must be unique; equivalence must reference an earlier fixture.")
            seen.add(fixture.name)
        return self


def replay(damage: int, hp: int, mode: str, number: int) -> str:
    """Render HP independently from the production parser/display constraint code."""
    maximum = hp if mode == "exact" else 100
    remaining = max(0, hp - damage)
    shown = remaining if mode == "exact" else math.ceil(100 * remaining / hp)
    return "\n".join([
        "|tier|[Gen 9 Champions] VGC 2026 Reg M-B (Bo3)",
        "|player|p1|Alice|", "|player|p2|Bob|",
        "|poke|p1|Garchomp, L50|", "|poke|p2|Incineroar, L50|",
        # The attacker's actual HP is 185 with two native HP points.
        f"|switch|p1a: Chomp|Garchomp, L50|{'185/185' if mode == 'exact' else '100/100'}",
        f"|switch|p2a: Cat|Incineroar, L50|{maximum}/{maximum}",
        f"|turn|{number}", "|move|p1a: Chomp|MOVE|p2a: Cat",
        f"|-damage|p2a: Cat|{shown}/{maximum}", "|win|" + ("Bob" if number == 2 else "Alice"),
    ])


def transformed(games: list[str]) -> list[str]:
    """Swap side ownership and nicknames without changing the participant identity."""
    return [game.replace("p1", "SIDE").replace("p2", "p1").replace("SIDE", "p2")
            .replace("Chomp", "NewDragon").replace("Cat", "NewTiger") for game in games]


def generate(port: CandidateProjectionPort) -> Corpus:
    """Generate twelve baseline series plus symmetry and resource-limit variants."""
    rules = port.rules()
    fixtures: list[Fixture] = []
    targets: list[tuple[str, str, dict[StatName, int]]] = [
        ("physical_bulk", "Impish", {"hp": 32, "atk": 0, "def": 32, "spa": 0, "spd": 2, "spe": 0}),
        ("special_bulk", "Calm", {"hp": 16, "atk": 0, "def": 0, "spa": 0, "spd": 24, "spe": 26}),
        ("fast_neutral", "Hardy", {"hp": 0, "atk": 32, "def": 0, "spa": 0, "spd": 2, "spe": 32}),
    ]
    attacks: list[tuple[str, str, StatName, StatName, str]] = [
        ("physical", "Dragon Claw", "atk", "def", "Adamant"),
        ("special", "Dragon Pulse", "spa", "spd", "Modest"),
    ]
    for category, move, attack_stat, defense_stat, nature in attacks:
        attacker = DeclaredSet(participant="Alice", slot=1, species="Garchomp", item="",
                               ability="Rough Skin", nature=nature,
                               stat_points={"hp": 2, attack_stat: 32, "spe": 32})
        for label, target_nature, points in targets:
            target = DeclaredSet(participant="Bob", slot=1, species="Incineroar", item="",
                                 ability="Intimidate", nature=target_nature, stat_points=points)
            projection = port.project_batch([ProjectionRequest(
                calc=CalcRequest(gen=0, attacker=PokemonSet(species="Garchomp", ability="Rough Skin", item=""),
                                 defender=PokemonSet(species="Incineroar", ability="Intimidate", item=""), move=move),
                attacker_points=attacker.stat_points, defender_points=target.stat_points,
                attacker_nature=nature, defender_nature=target_nature,
                before=HPReading(numerator=100, denominator=100, mode="percent"),
                attacker_before=HPReading(numerator=100, denominator=100, mode="percent"),
            )])[0]
            if not projection.supported or not projection.witnesses:
                raise ValueError(f"Cannot generate {category}/{label}: {projection.reason}")
            rolls = sorted({w.damage for w in projection.witnesses if w.hp_before == projection.maximum_hp})
            public = target.model_copy(update={"nature": None, "stat_points": {
                stat: value for stat, value in points.items() if stat not in {"hp", defense_stat}
            }})
            sheet = TeamSheet(registered_team=True, pokemon=(attacker, public))
            truth = {"Alice:slot1": attacker, "Bob:slot1": target}
            for mode in ("exact", "percent"):
                name = f"{category}/{label}/{mode}"
                games = [replay(damage, projection.maximum_hp, mode, index).replace("MOVE", move)
                         for index, damage in enumerate([rolls[0], rolls[len(rolls) // 2], rolls[-1]], 1)]
                options = InferenceOptions(hp_mode=mode, timeout_seconds=60)
                fixtures.append(Fixture(name=name, games=games, teamsheet=sheet, truth=truth, options=options))
                fixtures.append(Fixture(name=name + "/swapped_renamed", games=transformed(games),
                                        teamsheet=sheet, truth=truth, options=options, equivalent_to=name))
    baseline = fixtures[0]
    fixtures.append(baseline.model_copy(update={"name": "budget/one_assignment", "expected_coverage": "bounded",
        "options": baseline.options.model_copy(update={"max_assignments": 1})}))
    fixtures.append(baseline.model_copy(update={"name": "budget/one_projection", "expected_coverage": "bounded",
        "options": baseline.options.model_copy(update={"max_projections": 1})}))
    return Corpus(generator_engine_version=rules.engine_version, rule_version=rules.version, fixtures=fixtures)
