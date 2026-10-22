"""Map teamsheet declarations to stable series identities and causal audiences."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence

from src.domain.bo3_inference.models import DeclaredSet, FieldSource, TeamSheet
from src.domain.exceptions import SeriesValidationError
from src.domain.models import GameState
from src.domain.names import to_id
from src.domain.regulation import normalize_species
from src.domain.series import Bo3Evidence


def person(name: str) -> str:
    return "".join(character for character in name.lower() if character.isalnum())


def base(name: str) -> str:
    return re.sub(r"mega[xyz]?$", "", normalize_species(name))


def identities(
    evidence: Bo3Evidence, games: Sequence[GameState], sheet: TeamSheet,
) -> tuple[dict[str, DeclaredSet], list[dict[tuple[str, str], str]]]:
    names = {person(name): name for name in evidence.players}
    slots: dict[tuple[str, str], int] = {}
    declarations: dict[str, DeclaredSet] = {}
    for entry in sheet.pokemon:
        if person(entry.participant) not in names:
            raise SeriesValidationError(f"Teamsheet player {entry.participant!r} is not in this series.")
        name = names[person(entry.participant)]
        key = (name, base(entry.species))
        if key in slots and slots[key] != entry.slot:
            raise SeriesValidationError("A base species and its Mega cannot occupy two registered slots.")
        slots[(name, base(entry.species))] = entry.slot
    maps = []
    seen: set[tuple[str, str]] = set()
    for number, (game, facts) in enumerate(zip(games, evidence.games), 1):
        mapping = {}
        for side in game.sides:
            name = facts.players_by_side[side.player]
            for mon in side.team:
                key = (name, base(mon.species))
                seen.add(key)
                if key not in slots:
                    occupied = {slot for (owner, _), slot in slots.items() if owner == name}
                    slots[key] = next((slot for slot in range(1, 7) if slot not in occupied), 0)
                    if slots[key] == 0:
                        raise SeriesValidationError("Cannot map the series roster to six stable slots.")
                pokemon_id = f"{name}:slot{slots[key]}"
                mapping[(side.player, mon.species)] = pokemon_id
                declarations.setdefault(pokemon_id, DeclaredSet(participant=name, slot=slots[key], species=mon.species,
                                                               source="replay_identity_only", available_from_game=number))
        maps.append(mapping)
    if any((names[person(entry.participant)], base(entry.species)) not in seen for entry in sheet.pokemon):
        raise SeriesValidationError("A teamsheet species does not match any declared series roster.")
    return declarations, maps


def visible_declarations(
    initial: Mapping[str, DeclaredSet], sheet: TeamSheet, number: int, perspective: str,
) -> dict[str, DeclaredSet]:
    result = {key: entry for key, entry in initial.items() if entry.available_from_game <= number}
    for entry in sheet.pokemon:
        if entry.available_from_game > number or entry.only_game is not None and entry.only_game != number:
            continue
        visible = entry.visibility == "public" or perspective == "analyst" or (
            entry.visibility == "owner_private" and person(perspective) == person(entry.participant)
        )
        if not visible:
            continue
        pokemon_id = next(key for key, value in initial.items()
                          if person(value.participant) == person(entry.participant) and value.slot == entry.slot)
        previous = result.get(pokemon_id, initial[pokemon_id])
        changes: dict[str, object] = {"source": previous.source + ";" + entry.source}
        for field in ("nature", "item", "ability"):
            old, new = getattr(previous, field), getattr(entry, field)
            if old is not None and new is not None and to_id(str(old)) != to_id(str(new)):
                raise SeriesValidationError(f"Conflicting declared {field} for {pokemon_id}.")
            changes[field] = new if new is not None else old
        points = dict(previous.stat_points)
        for stat, value in entry.stat_points.items():
            if stat in points and points[stat] != value:
                raise SeriesValidationError(f"Conflicting declared stat points for {pokemon_id}.")
            points[stat] = value
        changes["stat_points"] = points
        moves = {to_id(move): move for move in (*previous.moves, *entry.moves)}
        if len(moves) > 4:
            raise SeriesValidationError(f"Declared moves exceed four for {pokemon_id}.")
        changes["moves"] = tuple(moves.values())
        changes["field_sources"] = {**previous.field_sources, **entry.field_sources}
        try:
            result[pokemon_id] = DeclaredSet.model_validate({**previous.model_dump(), **changes})
        except ValueError as exc:
            raise SeriesValidationError(str(exc)) from exc
    return result


def merge_sheets(manual: TeamSheet, public: TeamSheet) -> TeamSheet:
    """Preserve manual slot identities while adding visible public facts, never overrides."""
    slots = {(person(entry.participant), base(entry.species)): entry.slot for entry in manual.pokemon}
    occupied: dict[str, set[int]] = {}
    for (owner, _), slot in slots.items():
        occupied.setdefault(owner, set()).add(slot)
    entries = list(manual.pokemon)
    for entry in public.pokemon:
        owner = person(entry.participant)
        key = (owner, base(entry.species))
        if key not in slots:
            used = occupied.setdefault(owner, set())
            slot = entry.slot if entry.slot not in used else next((i for i in range(1, 7) if i not in used), 0)
            if not slot:
                raise SeriesValidationError("Manual and public teamsheets cannot fit six registered slots.")
            slots[key] = slot
            used.add(slot)
        candidate = entry.model_copy(update={"slot": slots[key]})
        if candidate not in entries:
            entries.append(candidate)
    return TeamSheet(registered_team=manual.registered_team or public.registered_team, pokemon=tuple(entries))


def with_reveals(
    declarations: Mapping[str, DeclaredSet], traits: Mapping[str, Mapping[str, tuple[str, int]]],
) -> dict[str, DeclaredSet]:
    """Merge only confirmed original traits; reject contradictory registrations."""
    result = dict(declarations)
    for key, fields in traits.items():
        updates = {}
        sources = dict(result[key].field_sources)
        for field, (value, available) in fields.items():
            known = getattr(result[key], field)
            if known is not None and to_id(str(known)) != to_id(value):
                raise SeriesValidationError(f"Declared initial {field} for {key} contradicts a replay reveal.")
            if known is None:
                updates[field] = value
                sources[field] = FieldSource(source=f"replay_game_{available - 1}", visibility="public",
                                             available_from_game=available)
        result[key] = result[key].model_copy(update={**updates, "field_sources": sources})
    return result
