"""Exact factorization only where hidden Pokemon profiles share no constraints."""

from __future__ import annotations

from collections.abc import Sequence

from src.domain.bo3_inference.models import DamageObservation


def observation_components(observations: Sequence[DamageObservation]) -> list[list[DamageObservation]]:
    """Connected combatants stay coupled, including reversed attacker/defender roles."""
    neighbors: dict[str, set[str]] = {}
    for observation in observations:
        neighbors.setdefault(observation.attacker_id, set()).add(observation.defender_id)
        neighbors.setdefault(observation.defender_id, set()).add(observation.attacker_id)
    remaining = set(neighbors)
    groups = []
    while remaining:
        pending = [min(remaining)]
        members: set[str] = set()
        while pending:
            key = pending.pop()
            if key in members:
                continue
            members.add(key)
            pending.extend(neighbors[key] - members)
        remaining.difference_update(members)
        groups.append([sample for sample in observations if sample.attacker_id in members])
    return groups
