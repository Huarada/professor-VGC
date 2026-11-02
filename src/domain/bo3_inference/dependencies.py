"""Attribute dependencies of event-time damage evidence."""

from collections.abc import Sequence

from src.domain.bo3_inference.models import DamageObservation, StatName
from src.domain.names import to_id


def relevant_axes(observations: Sequence[DamageObservation]) -> dict[str, set[StatName]]:
    result: dict[str, set[StatName]] = {}
    for observation in observations:
        attack: StatName = observation.attack_stat or ("atk" if observation.category == "Physical" else "spa")
        defense: StatName = observation.defense_stat or ("def" if observation.category == "Physical" else "spd")
        if observation.request.field.wonder_room:
            defense = "spd" if defense == "def" else "def"
        source = observation.defender_id if observation.attack_source == "defender" else observation.attacker_id
        result.setdefault(observation.attacker_id, set())
        result.setdefault(observation.defender_id, set()).add("hp")
        if not observation.stat_independent:
            result.setdefault(source, set()).add(attack)
            result[observation.defender_id].add(defense)
        if observation.uses_speed:
            result[observation.attacker_id].add("spe")
            result[observation.defender_id].add("spe")
        if observation.category_from_stats:
            result[observation.attacker_id].update(("atk", "spa"))
            result[observation.defender_id].update(("def", "spd"))
        if observation.uses_attacker_hp or observation.attacker_before.mode == "exact" or to_id(observation.request.attacker.ability) in {
            "blaze", "torrent", "overgrow", "swarm", "defeatist",
        }:
            result[observation.attacker_id].add("hp")
    return result
