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
    "tabletsofruin", "vesselofruin", "swordofruin",
    "beadsofruin", "neutralizinggas", "protean", "libero", "colorchange", "illusion",
    "imposter", "parentalbond", "disguise", "iceface",
    "flashfire", "analytic", "stakeout", "slowstart", "rivalry", "plus", "minus", "trace",
    "aurabreak", "electromorphosis", "windpower",
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
    forme_abilities: Mapping[str, str],
) -> PokemonSet:
    mon = state.mon(side, species)
    ability = mon.ability or declaration.ability
    if re.search(r"-mega(?:-[xyz])?$", mon.forme, re.IGNORECASE):
        ability = forme_abilities.get(mon.forme)
    return PokemonSet(species=species, ability=ability,
                      item=mon.item if mon.item is not None else declaration.item,
                      status=mon.status or None, boosts=mon.boosts,
                      battle_formes=[mon.forme] if mon.forme else [])


def field_for(event: BattleEvent, defender_side: str, defender_species: str,
              active: Mapping[tuple[str, str], PokemonSet]) -> CalcField:
    assert event.state is not None
    state = event.state
    screens = state.screens.get(defender_side, [])
    weather = "" if any(to_id(mon.ability) in {"cloudnine", "airlock"} for mon in active.values()) else state.weather
    return CalcField(weather=weather, terrain=state.terrain, trick_room=state.trick_room,
                     fairy_aura=any(to_id(mon.ability) == "fairyaura" for mon in active.values()),
                     dark_aura=any(to_id(mon.ability) == "darkaura" for mon in active.values()),
                     aura_break=any(to_id(mon.ability) == "aurabreak" for mon in active.values()),
                     gravity="Gravity" in state.rooms, magic_room="Magic Room" in state.rooms,
                     wonder_room="Wonder Room" in state.rooms,
                     attacker_side=SideField(helping_hand="helping hand" in event.effects,
                                             tailwind=event.actor_player in state.tailwind),
                     defender_side=SideField(reflect="Reflect" in screens, light_screen="Light Screen" in screens,
                                             aurora_veil="Aurora Veil" in screens, tailwind=defender_side in state.tailwind,
                                             friend_guard=any(side == defender_side and species != defender_species
                                                              and to_id(mon.ability) == "friendguard"
                                                              for (side, species), mon in active.items())))


def observations(
    game: GameState, number: int, declarations: Mapping[str, DeclaredSet],
    identity: Mapping[tuple[str, str], str], port: CandidateProjectionPort,
    hp_mode: Literal["percent", "exact"],
) -> tuple[list[DamageObservation], list[str]]:
    result: list[DamageObservation] = []
    excluded: list[str] = []
    events = game.outcome.events if game.outcome else []
    formes = sorted({event.state.mon(side, species).forme for event in events if event.state
                     for side, species_list in event.state.active.items() for species in species_list
                     if re.search(r"-mega(?:-[xyz])?$", event.state.mon(side, species).forme, re.IGNORECASE)})
    forme_abilities = port.forme_abilities(formes) if formes else {}
    for index, event in enumerate(game.outcome.events if game.outcome else []):
        if event.kind != "move" or not event.state or not event.hits:
            continue
        state = event.state
        active = {(side, species): runtime_mon(side, species, state, declarations[identity[(side, species)]], forme_abilities)
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
            elif "crit" in event.effects and (not move.critical_hits or not event.critical_targets):
                reason = "Critical hit requires supported per-target critical attribution"
            elif event.hit_count > 1 and not move.multi_hit:
                reason = "Multi-hit move requires supported per-hit roll composition"
            elif move.multi_hit and (not event.hit_count or event.damage_interleaved or "crit" in event.effects):
                reason = "Multi-hit count, critical mixtures or interleaved healing/residual damage require per-hit state modeling"
            elif len(event.hits) > 1 and any(
                candidate.hp_after == 0 and (holder := active.get((candidate.player, candidate.species)))
                and to_id(holder.ability) in {"fairyaura", "darkaura", "aurabreak", "cloudnine", "airlock", "friendguard"}
                for candidate in event.hits
            ):
                reason = "A global/ally effect holder fainted during a multi-target move; per-target effect timing is unresolved"
            elif not move.supported:
                reason = "Unsupported move: " + move.reason
            elif move.requires_hit_counter and state.mon(event.actor_player, event.actor).hits_taken is None:
                reason = "Received-hit history is incomplete; Rage Fist power cannot be determined"
            elif any(mon.ability is None for mon in active.values()) or attacker.item is None or target.item is None:
                reason = "Item or active ability is unknown; provide a teamsheet or earlier reveal"
            elif to_id(target.item) == "focusband" or to_id(attacker.item) == "metronome":
                reason = "Probabilistic survival or repeated-move item counter requires additional modeling"
            elif any(to_id(mon.ability) in _UNMODELED for mon in active.values()):
                names = sorted({f"{species} ({mon.ability})" for (_, species), mon in active.items()
                                if to_id(mon.ability) in _UNMODELED})
                reason = "Active ability requires additional runtime/stat modeling: " + ", ".join(names)
            elif any(mon.battle_formes and not re.search(r"-mega(?:-[xyz])?$", mon.battle_formes[-1], re.IGNORECASE)
                     for mon in (attacker, target)):
                reason = "Non-Mega transformed stats require additional modeling"
            before = reading(hit.hp_before_raw, hp_mode)
            after = reading(hit.hp_after_raw, hp_mode, before)
            attacker_hp = reading(state.mon(event.actor_player, event.actor).hp_raw, hp_mode)
            if not before or not after or not attacker_hp or before.numerator == 0:
                reason = reason or "Raw HP measurement is missing"
            elif attacker_hp.numerator == 0:
                reason = reason or "A fainted attacker cannot produce a direct damage observation"
            elif before.denominator != after.denominator or before.mode != after.mode:
                reason = reason or "HP visibility changed during the hit"
            elif after.numerator > before.numerator:
                reason = reason or "HP increased during a purported direct damage hit"
            if reason:
                excluded.append(f"{observation_id}: {reason}")
                continue
            assert attacker and target and before and after and attacker_hp
            request = CalcRequest(gen=0, attacker=attacker, defender=target, move=event.move,
                                  field=field_for(event, hit.player, hit.species, active),
                                  spread_hit="spread" in event.effects, after_failed_move=event.after_failed_move,
                                  critical_hit=hit.critical_hit,
                                  hits=event.hit_count if move.multi_hit else None,
                                  attacker_allies_fainted=len(state.fainted.get(event.actor_player, [])),
                                  attacker_hits_taken=state.mon(event.actor_player, event.actor).hits_taken
                                  if move.requires_hit_counter else None)
            result.append(DamageObservation(id=observation_id, game=number, event_index=index,
                                            attacker_id=identity[(event.actor_player, event.actor)],
                                            defender_id=identity[(hit.player, hit.species)], request=request,
                                            category="Physical" if move.category == "Physical" else "Special",
                                            attack_source=move.attack_source, attack_stat=move.attack_stat,
                                            defense_stat=move.defense_stat, uses_attacker_hp=move.uses_attacker_hp,
                                            stat_independent=move.stat_independent,
                                            uses_speed=move.uses_speed, category_from_stats=move.category_from_stats,
                                            before=before, after=after, attacker_before=attacker_hp))
    return result, excluded
