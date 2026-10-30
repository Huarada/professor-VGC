"""Measurement compatibility; no SDK, transport or metagame assumptions."""

from __future__ import annotations

from src.domain.bo3_inference.models import DamageObservation, DamageSupport, HPReading


def displays(hp: int, maximum: int, reading: HPReading) -> bool:
    """Showdown publishes ceiling-rounded HP; exact mode also fixes maximum HP."""
    if reading.mode == "exact":
        return reading.denominator == maximum and hp == reading.numerator
    return (hp * reading.denominator + maximum - 1) // maximum == reading.numerator


def agrees(observation: DamageObservation, support: DamageSupport) -> bool:
    """A KO is censored damage, not an exact numerical roll."""
    return any(
        displays(max(0, witness.hp_before - witness.damage), support.maximum_hp, observation.after)
        for witness in support.witnesses
    )
