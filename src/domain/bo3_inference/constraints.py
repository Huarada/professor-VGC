"""Measurement compatibility; no SDK, transport or metagame assumptions."""

from __future__ import annotations

from src.domain.bo3_inference.models import DamageObservation, DamageSupport, HPReading


def displays(hp: int, maximum: int, reading: HPReading) -> bool:
    """Champions floors public HP, clamping living Pokemon to at least 1%."""
    if reading.mode == "exact":
        return reading.denominator == maximum and hp == reading.numerator
    displayed = max(1, hp * reading.denominator // maximum) if hp > 0 else 0
    if displayed != reading.numerator:
        return False
    if reading.color and reading.numerator == 20:
        return (hp * 5 > maximum) == (reading.color == "y")
    if reading.color and reading.numerator == 50:
        return (hp * 2 > maximum) == (reading.color == "g")
    return True


def agrees(observation: DamageObservation, support: DamageSupport) -> bool:
    """A KO is censored damage, not an exact numerical roll."""
    return any(
        displays(max(0, witness.hp_before - witness.damage), support.maximum_hp, observation.after)
        for witness in support.witnesses
    )
