"""Build inverse-inference evidence from raw measurements and event-time facts."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Literal

from src.domain.bo3_inference.models import DamageObservation, DeclaredSet, HPReading
from src.domain.interfaces import CandidateProjectionPort
from src.domain.models import (
    BattleEvent,
    BattleSnapshot,
    CalcField,
    CalcRequest,
    GameState,
    PokemonSet,
    SideField,
)
from src.domain.names import to_id

# These abilities depend on unprojected stats, temporary typing or global flags.
_UNMODELED = {
    "protosynthesis", "quarkdrive", "flowergift", "battery", "powerspot", "steelyspirit",
    "fairyaura", "darkaura", "aurabreak", "tabletsofruin", "vesselofruin", "swordofruin",
    "beadsofruin", "neutralizinggas", "protean", "libero", "colorchange", "illusion",
    "imposter", "parentalbond", "disguise", "iceface",
    "flashfire", "analytic", "stakeout", "slowstart", "rivalry", "plus", "minus", "trace",
}


def reading(raw: str, mode: Literal["percent", "exact"], previous: HPReading | None = None) -> HPReading | None:
    try:
        if raw.strip() in {"0", "0 fnt"} and previous:
            return HPReading(numerator=0, denominator=previous.denominator, mode=mode)
        match = re.match(r"^(\d+)/(\d+)(?:\s|$)", raw.strip())
        return HPReading(numerator=int(match[1]), denominator=int(match[2]), mode=mode) if match else None
    except ValueError:
        return None


def runtime_mon(
    side: str, species: str, state: BattleSnapshot, declaration: DeclaredSet,
) -> PokemonSet:
    mon = state.mon(side, species)
    return PokemonSet(species=species, ability=mon.ability or declaration.ability,
                      item=mon.item if mon.item is not None else declaration.item,
                      status=mon.status or None, boosts=mon.boosts,
                      battle_formes=[mon.forme] if mon.forme else [])


def field_for(event: BattleEvent, defender_side: str, allies: Mapping[tuple[str, str], PokemonSet]) -> CalcField:
    assert event.state is not None
    state = event.state
    screens = state.screens.get(defender_side, [])
    return CalcField(weather=state.weather, terrain=state.terrain, trick_room=state.trick_room,
                     gravity="Gravity" in state.rooms, magic_room="Magic Room" in state.rooms,
                     wonder_room="Wonder Room" in state.rooms,
                     attacker_side=SideField(helping_hand="helping hand" in event.effects,
                                             tailwind=event.actor_player in state.tailwind),
                     defender_side=SideField(reflect="Reflect" in screens, light_screen="Light Screen" in screens,
                                             aurora_veil="Aurora Veil" in screens, tailwind=defender_side in state.tailwind,
                                             friend_guard=any(side == defender_side and to_id(mon.ability) == "friendguard"
                                                              for (side, _), mon in allies.items())))


def observations(
    game: GameState, number: int, declarations: Mapping[str, DeclaredSet],
    identity: Mapping[tuple[str, str], str], port: CandidateProjectionPort,
    hp_mode: Literal["percent", "exact"],
) -> tuple[list[DamageObservation], list[str]]:
    result: list[DamageObservation] = []
    excluded: list[str] = []
    for index, event in enumerate(game.outcome.events if game.outcome else []):
        if event.kind != "move" or not event.state or not event.hits:
            continue
        state = event.state
        active = {(side, species): runtime_mon(side, species, state, declarations[identity[(side, species)]])
                  for side, names in state.active.items() for species in names if (side, species) in identity}
        attacker = active.get((event.actor_player, event.actor))
        move = port.move_support(event.move)
        for hit in event.hits:
            if hit.blocked_by or hit.hp_after is None:
                continue
            observation_id = f"g{number}:e{index}:{hit.player}:{hit.species}"
            target = active.get((hit.player, hit.species))
            reason = ""
            if not attacker or not target:
                reason = "Unresolved combatant identity"
            elif state.inference_exclusions:
                reason = "; ".join(state.inference_exclusions)
            elif "crit" in event.effects or event.hit_count > 1 or not move.supported:
                reason = "Critical/multi-hit or unsupported move: " + move.reason
            elif any(mon.ability is None for mon in active.values()) or attacker.item is None or target.item is None:
                reason = "Item or active ability is unknown; provide a teamsheet or earlier reveal"
            elif any(to_id(mon.ability) in _UNMODELED for mon in active.values()):
                reason = "An active ability requires additional runtime/stat modeling"
            elif any(mon.battle_formes and not mon.battle_formes[-1].startswith(mon.species + "-Mega")
                     for mon in (attacker, target)):
                reason = "Non-Mega transformed stats require additional modeling"
            before = reading(hit.hp_before_raw, hp_mode)
            after = reading(hit.hp_after_raw, hp_mode, before)
            attacker_hp = reading(state.mon(event.actor_player, event.actor).hp_raw, hp_mode)
            if not before or not after or not attacker_hp or before.numerator == 0:
                reason = reason or "Raw HP measurement is missing"
            if reason:
                excluded.append(f"{observation_id}: {reason}")
                continue
            assert attacker and target and before and after and attacker_hp
            allies = {key: mon for key, mon in active.items() if key != (hit.player, hit.species)}
            request = CalcRequest(gen=0, attacker=attacker, defender=target, move=event.move,
                                  field=field_for(event, hit.player, allies),
                                  spread_hit="spread" in event.effects, after_failed_move=event.after_failed_move,
                                  attacker_allies_fainted=len(state.fainted.get(event.actor_player, [])))
            result.append(DamageObservation(id=observation_id, game=number, event_index=index,
                                            attacker_id=identity[(event.actor_player, event.actor)],
                                            defender_id=identity[(hit.player, hit.species)], request=request,
                                            category="Physical" if move.category == "Physical" else "Special",
                                            before=before, after=after, attacker_before=attacker_hp))
    return result, excluded
