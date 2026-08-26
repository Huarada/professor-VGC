"""Standalone Showdown replay parser for the UI's battle-replay panel ONLY.

Deliberately independent of ``src/adapters/parsers/showdown_parser.py`` (the
LLM pipeline's parser) — no shared class, no imported helpers, no shared
mutable state. This means a small amount of low-level parsing (ref-splitting,
HP-field parsing) is intentionally duplicated between the two files. That is
a deliberate trade-off, not an oversight: this module exists so the visual
battle panel can be built, changed, and even get things wrong without ANY
risk of regressing the already-hardened LLM analysis pipeline (ADR-006
through ADR-013), and vice versa. See ADR-014 in ADR.md.

Produces a :class:`~src.domain.replay_view_models.BattleReplay` — a turn-by-
turn HP/status/active-roster/field-condition ledger that does not exist
anywhere else in this codebase (the LLM pipeline never needed one: it reasons
from the ordered action timeline, not from a per-turn HP snapshot).
"""

from __future__ import annotations

import json
import re
from typing import Any

from src.domain.replay_view_models import BattleReplay, ReplayPokemonState, ReplayTurnSnapshot

_REF = re.compile(r"^(?P<player>p\d)(?P<slot>[a-z]?): (?P<nick>.+)$")


def _split_ref(ref: str) -> tuple[str, str, str] | None:
    """Split ``"p1a: Torkoal"`` into ``("p1", "a", "Torkoal")``."""
    m = _REF.match(ref.strip())
    if not m:
        return None
    return m.group("player"), m.group("slot"), m.group("nick").strip()


def _split_species(details: str) -> str:
    """Extract the species name from a Showdown "details" field, e.g.
    ``"Charizard, L50, F"`` -> ``"Charizard"``."""
    return details.split(",")[0].strip()


def _parse_hp_field(raw: str) -> tuple[float, bool]:
    """Parse a Showdown HP field: ``"100/100"``, ``"57/100 par"``, ``"0 fnt"``.

    Returns (hp_percent, fainted).
    """
    token = raw.strip().split(" ")[0]
    if raw.strip().endswith("fnt") or token == "0":
        return 0.0, True
    if "/" in token:
        try:
            cur, maxhp = token.split("/")
            pct = float(cur) / float(maxhp) * 100
            pct = max(0.0, min(100.0, round(pct, 1)))
            return pct, pct <= 0.0
        except (ValueError, ZeroDivisionError):
            return 100.0, False
    return 100.0, False


def parse_replay_for_viewer(replay: dict[str, Any] | str) -> BattleReplay:
    """Parse structured replay JSON or raw log text into a BattleReplay.

    Mirrors the LLM parser's input normalization (JSON string -> dict -> log
    text) but is otherwise a completely separate implementation. Returns an
    empty ``BattleReplay`` (no snapshots) rather than raising when the input
    has no usable log — callers render nothing in that case, never an error.
    """
    if isinstance(replay, str):
        text = replay.strip()
        if text.startswith("{"):
            try:
                decoded = json.loads(text)
            except json.JSONDecodeError:
                return BattleReplay()
            return parse_replay_for_viewer(decoded)
        return _parse_log_text(text)
    if isinstance(replay, dict):
        log = replay.get("log")
        if isinstance(log, str) and log.strip():
            return _parse_log_text(log)
        return BattleReplay()
    return BattleReplay()


def _parse_log_text(text: str) -> BattleReplay:  # noqa: C901 - one cohesive line-walk, mirrors the LLM parser's own shape
    player_names: dict[str, str] = {}
    slot_species: dict[str, str] = {}  # "p1a" -> species (identity stable across forme changes)
    # Every per-Pokemon tracker below is keyed by (player, species) — NOT a
    # bare species string — so a mirror match (both sides bringing the same
    # species, an ordinary VGC occurrence) never collides two Pokemon's HP/
    # forme/status into one entry. This is the same bug class ADR-008 fixed
    # for the LLM pipeline's GameState.side_of(); this module is independent
    # of that fix, so it needs its own, from scratch.
    hp_percent: dict[tuple[str, str], float] = {}
    formes: dict[tuple[str, str], str] = {}
    statuses: dict[tuple[str, str], str] = {}
    boosts: dict[tuple[str, str], dict[str, int]] = {}
    log_lines: dict[int, list[str]] = {}
    avatars: dict[str, str] = {}
    team: dict[str, list[str]] = {}
    turn = 0
    winner_name: str | None = None
    forfeited_name: str | None = None

    # field ledger, mirrors the LLM parser's own tracking shape
    tailwind: dict[str, list[list[int]]] = {}
    tw_open: dict[str, int] = {}
    trick_room: list[list[int]] = []
    tr_open: int | None = None
    weather = ""

    raw_snapshots: list[
        tuple[
            int,
            dict[tuple[str, str], float],
            dict[str, str],
            dict[tuple[str, str], str],
            dict[tuple[str, str], str],
            dict[tuple[str, str], dict[str, int]],
            str,
        ]
    ] = []

    def snapshot() -> None:
        # Copy EVERY per-Pokemon/field tracker at this exact moment — formes,
        # statuses, boosts and weather only ever change forward over the
        # course of parsing, so looking them up lazily from the final,
        # fully-parsed state (instead of a turn-scoped copy) would
        # retroactively show a turn-12 Mega Evolution, status condition, stat
        # boost, or weather change on the turn-1 snapshot too.
        raw_snapshots.append(
            (
                turn,
                dict(hp_percent),
                dict(slot_species),
                dict(formes),
                dict(statuses),
                {k: dict(v) for k, v in boosts.items()},
                weather,
            )
        )

    def append_log(line: str) -> None:
        log_lines.setdefault(turn, []).append(line)

    for line in text.splitlines():
        if not line.startswith("|"):
            continue
        parts = line.split("|")
        if len(parts) < 2:
            continue
        tag = parts[1]

        if tag == "turn":
            snapshot()
            try:
                turn = int(parts[2])
            except (IndexError, ValueError):
                pass
        elif tag == "player" and len(parts) > 2:
            if len(parts) > 3 and parts[3].strip():
                player_names[parts[2]] = parts[3].strip()
            if len(parts) > 4 and parts[4].strip():
                avatars[parts[2]] = parts[4].strip()
        elif tag == "poke" and len(parts) > 3:
            team.setdefault(parts[2], []).append(_split_species(parts[3]))
        elif tag in ("switch", "drag") and len(parts) > 3:
            ref = _split_ref(parts[2])
            if ref is None:
                continue
            player, slot, nick = ref
            species = _split_species(parts[3])
            key = (
                nick
                if (player, nick) in hp_percent or (player, nick) in formes
                else species
            )
            slot_species[player + slot] = key
            pid = (player, key)
            if len(parts) > 4:
                pct, fainted = _parse_hp_field(parts[4])
                hp_percent[pid] = 0.0 if fainted else pct
            else:
                hp_percent.setdefault(pid, 100.0)
            if species != key:
                formes[pid] = species
            boosts[pid] = {}  # stat stages never persist across a switch
            append_log(f"{player} sent out {species}.")
        elif tag == "detailschange" and len(parts) > 3:
            ref = _split_ref(parts[2])
            if ref is not None:
                player, slot, _nick = ref
                existing_key = slot_species.get(player + slot)
                species = _split_species(parts[3])
                if existing_key and species != existing_key:
                    formes[(player, existing_key)] = species
        elif tag == "move" and len(parts) > 3:
            ref = _split_ref(parts[2])
            if ref is not None:
                player, slot, nick = ref
                actor = slot_species.get(player + slot) or nick
                append_log(f"{player} {actor} used {parts[3].strip()}")
        elif tag == "-damage" and len(parts) > 3:
            ref = _split_ref(parts[2])
            if ref is None:
                continue
            tp, tslot, tnick = ref
            species = slot_species.get(tp + tslot) or tnick
            pct, fainted = _parse_hp_field(parts[3])
            hp_percent[(tp, species)] = 0.0 if fainted else pct
        elif tag == "-heal" and len(parts) > 3:
            ref = _split_ref(parts[2])
            if ref is None:
                continue
            pl, sl, nick = ref
            species = slot_species.get(pl + sl) or nick
            pct, _fainted = _parse_hp_field(parts[3])
            hp_percent[(pl, species)] = pct
        elif tag == "faint" and len(parts) > 2:
            ref = _split_ref(parts[2])
            if ref is None:
                continue
            player, slot, nick = ref
            species = slot_species.get(player + slot) or nick
            hp_percent[(player, species)] = 0.0
            append_log(f"{species} ({player}) fainted.")
        elif tag == "-sidestart" and len(parts) > 3 and "Tailwind" in parts[3]:
            ref = _split_ref(parts[2])
            player = ref[0] if ref else (parts[2].split(":")[0].strip() or "p?")
            tw_open.setdefault(player, turn)
        elif tag == "-sideend" and len(parts) > 3 and "Tailwind" in parts[3]:
            ref = _split_ref(parts[2])
            player = ref[0] if ref else (parts[2].split(":")[0].strip() or "p?")
            start = tw_open.pop(player, turn)
            tailwind.setdefault(player, []).append([start, turn])
        elif tag == "-fieldstart" and len(parts) > 2 and "Trick Room" in parts[2]:
            if tr_open is None:
                tr_open = turn
        elif tag == "-fieldend" and len(parts) > 2 and "Trick Room" in parts[2]:
            if tr_open is not None:
                trick_room.append([tr_open, turn])
                tr_open = None
        elif tag == "-weather" and len(parts) > 2:
            w = parts[2].strip()
            weather = "" if w.lower() in ("none", "") else w
        elif tag in ("-boost", "-unboost") and len(parts) > 4:
            ref = _split_ref(parts[2])
            if ref is not None:
                pl, sl, nick = ref
                species = slot_species.get(pl + sl) or nick
                stat = parts[3].strip()
                try:
                    amount = int(parts[4])
                except ValueError:
                    amount = 0
                if tag == "-unboost":
                    amount = -amount
                pid = (pl, species)
                stage = boosts.setdefault(pid, {}).get(stat, 0) + amount
                boosts[pid][stat] = max(-6, min(6, stage))
        elif tag == "-status" and len(parts) > 3:
            ref = _split_ref(parts[2])
            if ref is not None:
                pl, sl, nick = ref
                species = slot_species.get(pl + sl) or nick
                statuses[(pl, species)] = parts[3].strip()
        elif tag == "-curestatus" and len(parts) > 3:
            ref = _split_ref(parts[2])
            if ref is not None:
                pl, sl, nick = ref
                species = slot_species.get(pl + sl) or nick
                statuses.pop((pl, species), None)
        elif tag == "-message" and len(parts) > 2:
            text_msg = parts[2].strip()
            if text_msg.endswith("forfeited."):
                forfeited_name = text_msg[: -len(" forfeited.")].strip()
        elif tag == "win" and len(parts) > 2:
            winner_name = parts[2].strip()

    if not player_names and not slot_species:
        # Nothing recognizable as a Showdown log was found at all — an empty
        # BattleReplay (no snapshots), not one degenerate all-empty snapshot.
        return BattleReplay()

    snapshot()  # final turn has no trailing |turn| line to trigger it

    for player, start in tw_open.items():
        tailwind.setdefault(player, []).append([start, turn])
    if tr_open is not None:
        trick_room.append([tr_open, turn])

    def tailwind_active(player: str, at_turn: int) -> bool:
        return any(start <= at_turn <= end for start, end in tailwind.get(player, []))

    def trick_room_active(at_turn: int) -> bool:
        return any(start <= at_turn <= end for start, end in trick_room)

    def conditions_for(at_turn: int, weather_at_turn: str) -> list[str]:
        labels = [f"Tailwind {p}" for p in player_names if tailwind_active(p, at_turn)]
        if trick_room_active(at_turn):
            labels.append("Trick Room")
        if weather_at_turn:
            labels.append(f"weather {weather_at_turn}")
        return labels

    winner_player: str | None = None
    if winner_name:
        for player, name in player_names.items():
            if name == winner_name:
                winner_player = player
                break
    forfeited_player: str | None = None
    if forfeited_name:
        for player, name in player_names.items():
            if name == forfeited_name:
                forfeited_player = player
                break

    snapshots: list[ReplayTurnSnapshot] = []
    for (
        snap_turn,
        hp_at_turn,
        slots_at_turn,
        formes_at_turn,
        statuses_at_turn,
        boosts_at_turn,
        weather_at_turn,
    ) in raw_snapshots:
        active: dict[str, list[str]] = {}
        for key, species in sorted(slots_at_turn.items()):
            player = key[:2]
            active.setdefault(player, []).append(species)
        pokemon: dict[str, dict[str, ReplayPokemonState]] = {}
        for (player, species), pct in hp_at_turn.items():
            pid = (player, species)
            pokemon.setdefault(player, {})[species] = ReplayPokemonState(
                species=species,
                forme=formes_at_turn.get(pid, ""),
                hp_percent=pct,
                fainted=pct <= 0.0,
                status=statuses_at_turn.get(pid, ""),
                boosts={k: v for k, v in boosts_at_turn.get(pid, {}).items() if v != 0},
            )
        snapshots.append(
            ReplayTurnSnapshot(
                turn=snap_turn,
                active=active,
                pokemon=pokemon,
                log=list(log_lines.get(snap_turn, [])),
                conditions=conditions_for(snap_turn, weather_at_turn),
            )
        )

    return BattleReplay(
        player_names=player_names,
        avatars=avatars,
        team=team,
        winner_player=winner_player,
        forfeited_player=forfeited_player,
        snapshots=snapshots,
    )
