"""Showdown replay parser (deterministic cleaning stage).

Turns a raw Showdown replay — either a decoded JSON object or the raw ``|``-
delimited battle log text — into a typed :class:`~src.domain.models.GameState`.
This is the *LIMPEZA PARA FILTRAR POKEMON ENVOLVIDOS (DETERMINISMO)* node in
the flow diagram: no probabilities, no LLM, just structural extraction.

Beyond the rosters, it reconstructs the ORDERED action timeline (moves, their
targets, damage, effectiveness and faints) so the explanation AI reasons about
what actually happened instead of guessing turn order or causality.
"""

from __future__ import annotations

import json
import re
from typing import Any

from src.domain.exceptions import LogParsingError
from src.domain.models import (
    BattleEvent,
    BattleOutcome,
    FieldConditions,
    GameState,
    KOEvent,
    PokemonSet,
    SideState,
)

_POKE_LINE = re.compile(r"^\|poke\|(?P<player>p\d)\|(?P<details>[^|]+)")
_REF = re.compile(r"^(?P<player>p\d)(?P<slot>[a-z]?): (?P<nick>.+)$")

# Protect-family moves: a "-activate|POKEMON|move: X" for one of these means
# that move fully blocked the current attack against that Pokemon this turn.
_PROTECT_FAMILY = {
    "protect", "detect", "spikyshield", "banefulbunker", "kingsshield",
    "quickguard", "wideguard", "craftyshield", "obstruct", "silktrap",
    "burningbulwark", "matblock",
}

# The five stat stages that actually feed the damage/speed calc — accuracy
# and evasion changes are real Showdown -boost/-unboost events too, but
# neither @smogon/calc's boosts option nor this project's field model uses
# them, so they're deliberately not tracked.
_CALC_STATS = {"atk", "def", "spa", "spd", "spe"}


def _norm_move_name(name: str) -> str:
    return name.lower().replace(" ", "").replace("-", "").replace("'", "")


class ShowdownReplayParser:
    """Concrete :class:`~src.domain.interfaces.LogParser` for Showdown."""

    def parse(self, replay: dict[str, Any] | str) -> GameState:
        """Parse structured JSON or raw log text into a GameState."""
        if isinstance(replay, str):
            text = replay.strip()
            if text.startswith("{"):
                try:
                    decoded = json.loads(text)
                except json.JSONDecodeError as exc:
                    raise LogParsingError(
                        "Could not decode the pasted replay as JSON "
                        f"(error at line {exc.lineno}, column {exc.colno}: {exc.msg}). "
                        "Paste either the full replay JSON downloaded from Showdown "
                        "(the object containing a \"log\" field), the raw battle log "
                        "text (the lines starting with '|'), or a structured team "
                        "JSON with a \"sides\" array."
                    ) from exc
                return self.parse(decoded)
            return self._parse_log_text(text)
        if isinstance(replay, dict):
            if isinstance(replay.get("log"), str) and replay["log"].strip():
                state = self._parse_log_text(replay["log"])
                state.format_id = (
                    replay.get("formatid") or replay.get("format") or state.format_id
                )
                state.rating = self._coerce_rating(replay.get("rating"))
                return state
            return self._parse_json(replay)
        raise LogParsingError(
            f"Unsupported replay input of type {type(replay).__name__!r}. "
            "Expected a JSON string, raw battle-log text, or a decoded dict."
        )

    # -- structured JSON ------------------------------------------------- #

    def _parse_json(self, payload: dict[str, Any]) -> GameState:
        try:
            format_id = payload.get("format") or payload.get("formatid") or "gen9vgc2025"
            turn = int(payload.get("turn", 0) or 0)
            sides_payload = payload.get("sides")
            if sides_payload is None:
                sides_payload = self._sides_from_teams(payload)
            sides = [self._parse_side(side) for side in sides_payload]
            state = GameState(
                format_id=format_id, turn=turn, sides=sides,
                rating=self._coerce_rating(payload.get("rating")),
            )
        except (TypeError, ValueError, KeyError) as exc:
            raise LogParsingError(
                "Malformed replay payload: the JSON was read but its structure is "
                f"not what the parser expects ({type(exc).__name__}: {exc}). Each "
                "side needs a 'player' and a 'team' list of Pokemon objects with at "
                "least a 'species'."
            ) from exc
        if not state.sides:
            available = ", ".join(sorted(payload.keys())) or "(empty object)"
            raise LogParsingError(
                "The replay was valid JSON but contained no battle data to analyze: "
                "no 'log' field, no 'sides' array and no 'teams' object were found. "
                f"Top-level keys present: {available}. "
                "If this is a Showdown replay download, make sure you pasted the whole "
                "object including its \"log\" field; if it is a team export, wrap it as "
                '{"sides": [{"player": "p1", "team": [...]}]}.'
            )
        return state

    @staticmethod
    def _sides_from_teams(payload: dict[str, Any]) -> list[dict[str, Any]]:
        teams = payload.get("teams") or {}
        return [{"player": player, "team": team} for player, team in teams.items()]

    def _parse_side(self, side: dict[str, Any]) -> SideState:
        team = [self._parse_set(mon) for mon in side.get("team", [])]
        return SideState(
            player=str(side.get("player", "p?")),
            player_name=str(side.get("player_name", "")),
            team=team,
            active=list(side.get("active", [])),
        )

    @staticmethod
    def _parse_set(mon: dict[str, Any]) -> PokemonSet:
        return PokemonSet.model_validate(mon)

    # -- raw log text ---------------------------------------------------- #

    @staticmethod
    def _split_ref(ref: str) -> tuple[str, str, str] | None:
        """Split ``"p1a: Torkoal"`` into ``("p1", "a", "Torkoal")``."""
        m = _REF.match(ref.strip())
        if not m:
            return None
        return m.group("player"), m.group("slot"), m.group("nick").strip()

    def _parse_log_text(self, text: str) -> GameState:
        players: dict[str, dict[str, PokemonSet]] = {}
        player_names: dict[str, str] = {}
        active: dict[str, list[str]] = {}
        slot_species: dict[str, str] = {}
        events: list[BattleEvent] = []
        kos: list[KOEvent] = []
        fainted_in_move: set[tuple[int, str]] = set()
        current_move: BattleEvent | None = None
        winner_name: str | None = None
        forfeited_name: str | None = None
        # field / status ledger
        tailwind: dict[str, list[list[int]]] = {}
        tw_open: dict[str, int] = {}
        trick_room: list[list[int]] = []
        tr_open: int | None = None
        weather: str = ""
        statuses: dict[str, str] = {}
        format_id = "gen9vgc2025"
        turn = 0

        def register(player: str, species: str, level: int = 50) -> None:
            bucket = players.setdefault(player, {})
            bucket.setdefault(species, PokemonSet(species=species, level=level))

        def record_forme(player: str, key: str, observed_species: str) -> None:
            """Note that ``key`` was seen in-battle as a DIFFERENT species string
            (Mega Evolution / other forme change) than its registered identity.
            """
            mon = players.get(player, {}).get(key)
            if mon is None or observed_species == mon.species:
                return
            if observed_species not in mon.battle_formes:
                mon.battle_formes.append(observed_species)

        def record_ability(player: str, key: str, ability: str) -> None:
            """Note a CONFIRMED ability (revealed because it actively
            triggered — Intimidate, Trace, Download, ... — Showdown never
            announces a passive ability that never activates). First
            reveal wins and is never overwritten: a Pokemon has exactly one
            real ability, so a later `-ability` line for the same mon (e.g.
            Trace copying something new) is a DIFFERENT, temporary ability
            and must not overwrite the confirmed original."""
            mon = players.get(player, {}).get(key)
            if mon is not None and not mon.ability:
                mon.ability = ability

        for line in text.splitlines():
            if not line.startswith("|"):
                continue
            parts = line.split("|")
            if len(parts) < 2:
                continue
            tag = parts[1]

            if tag == "turn":
                try:
                    turn = int(parts[2])
                except (IndexError, ValueError):
                    pass
                current_move = None
            elif tag == "player" and len(parts) > 2:
                players.setdefault(parts[2], {})
                if len(parts) > 3 and parts[3].strip():
                    player_names[parts[2]] = parts[3].strip()
            elif tag == "poke" and len(parts) > 3:
                species, level = self._split_details(parts[3])
                register(parts[2], species, level)
            elif tag in ("switch", "drag") and len(parts) > 3:
                ref = self._split_ref(parts[2])
                if ref is None:
                    continue
                player, slot, nick = ref
                species, level = self._split_details(parts[3])
                # Showdown's "nick" (the ref before the colon) is the STABLE identity
                # for this battle slot; "details" is just the CURRENT appearance and
                # changes after a Mega Evolution / in-battle forme change. If this
                # Pokemon was already registered under its nick (e.g. it mega evolved,
                # switched out, and is now switching back in as "<Species>-Mega"),
                # reuse that same entry instead of registering a fresh one — otherwise
                # the new entry starts with an empty moveset and later gets treated as
                # a different Pokemon than the one whose moves were already recorded.
                bucket = players.get(player, {})
                key = nick if nick in bucket else species
                register(player, key, level)
                record_forme(player, key, species)
                active.setdefault(player, [])
                if key not in active[player]:
                    active[player].append(key)
                slot_species[player + slot] = key
                current_move = None
                events.append(
                    BattleEvent(
                        turn=turn, kind="switch", actor=key, actor_player=player,
                        text=f"{player} sent out {species}.",
                    )
                )
            elif tag == "detailschange" and len(parts) > 3:
                # Mega Evolution / other in-battle forme change (e.g. Zygarde,
                # Mimikyu-Busted). The identity (slot_species) doesn't change —
                # only the appearance — but we record it so calc results computed
                # under the base identity can be flagged as a stat approximation.
                ref = self._split_ref(parts[2])
                if ref is not None:
                    player, slot, nick = ref
                    species, _level = self._split_details(parts[3])
                    key = slot_species.get(player + slot) or nick
                    record_forme(player, key, species)
                    # Also a visible, ordered timeline event — not just the
                    # silent battle_formes bookkeeping above. Reported gap:
                    # a Mega Evolution (or other forme change) never showed
                    # up anywhere the explanation AI could see it, even
                    # though it can be the single most strategically
                    # important fact in the game (e.g. Mega Gengar gaining
                    # Shadow Tag turns the team into a trapping core) — the
                    # AI had the raw ability-activation/boost events
                    # (ADR-020/021) but never the transformation that
                    # CAUSED a new ability to be active in the first place.
                    events.append(
                        BattleEvent(
                            turn=turn, kind="forme_change", actor=key, actor_player=player,
                            effects=[species],
                            text=f"{player} {key} transformed into {species}.",
                        )
                    )
            elif tag == "move" and len(parts) > 3:
                current_move = self._handle_move(parts, turn, players, slot_species, active)
                if current_move is not None:
                    events.append(current_move)
            elif tag == "-damage" and current_move is not None and len(parts) > 3:
                if any(p.strip().startswith("[from]") for p in parts[4:]):
                    continue  # residual / item damage, not the move's direct hit
                ref = self._split_ref(parts[2])
                if ref is None:
                    continue
                tp, tslot, tnick = ref
                species = slot_species.get(tp + tslot) or tnick
                hp = parts[3].strip()
                if species not in current_move.targets:
                    current_move.targets.append(species)
                if "fnt" in hp:
                    current_move.results.append(f"{species} fainted")
                    fainted_in_move.add((turn, species))
                else:
                    pct = hp.split("/")[0]
                    current_move.results.append(f"{species}->{pct}%")
            elif tag == "-activate" and current_move is not None and len(parts) > 3:
                # A Pokemon blocked THIS move with a Protect-family move. The
                # target that blocked it never gets a "-damage" line (so it
                # would otherwise silently vanish from the event), which is
                # exactly the causal link a risk/reward read of the turn
                # needs: "this spread move WAS aimed at them, and they read it."
                reason = parts[3].strip()
                if reason.lower().startswith("move:"):
                    block_move = reason.split(":", 1)[1].strip()
                    if _norm_move_name(block_move) in _PROTECT_FAMILY:
                        ref = self._split_ref(parts[2])
                        if ref is not None:
                            bp, bslot, bnick = ref
                            species = slot_species.get(bp + bslot) or bnick
                            if species not in current_move.blocked:
                                current_move.blocked.append(species)
                            current_move.results.append(f"{species} blocked ({block_move})")
            elif tag == "-supereffective" and current_move is not None:
                self._add_effect(current_move, "super effective")
            elif tag == "-resisted" and current_move is not None:
                self._add_effect(current_move, "resisted")
            elif tag == "-immune" and current_move is not None:
                self._add_effect(current_move, "immune")
            elif tag == "-crit" and current_move is not None:
                self._add_effect(current_move, "crit")
            elif tag == "-miss" and current_move is not None:
                self._add_effect(current_move, "missed")
            elif tag == "faint" and len(parts) > 2:
                ref = self._split_ref(parts[2])
                if ref is None:
                    continue
                player, slot, nick = ref
                species = slot_species.get(player + slot) or nick
                kos.append(KOEvent(turn=turn, fainted=species, player=player))
                if (turn, species) not in fainted_in_move:
                    events.append(
                        BattleEvent(
                            turn=turn, kind="faint", actor=species, actor_player=player,
                            text=f"{species} ({player}) fainted.",
                        )
                    )
            elif tag == "-sidestart" and len(parts) > 3 and "Tailwind" in parts[3]:
                ref = self._split_ref(parts[2])
                player = ref[0] if ref else (parts[2].split(":")[0].strip() or "p?")
                tw_open.setdefault(player, turn)
            elif tag == "-sideend" and len(parts) > 3 and "Tailwind" in parts[3]:
                ref = self._split_ref(parts[2])
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
            elif tag == "-status" and len(parts) > 3:
                ref = self._split_ref(parts[2])
                if ref is not None:
                    pl, sl, nick = ref
                    species = slot_species.get(pl + sl) or nick
                    statuses[species] = parts[3].strip()
            elif tag == "-ability" and len(parts) > 3:
                # An ability that actively TRIGGERED (Intimidate, Trace,
                # Download, ...) — Showdown only ever announces this when
                # observable, never a passive ability that never activated.
                # Recorded as its own ordered timeline event (ground truth
                # the explanation AI can narrate turn by turn, matching the
                # "abilidade turno a turno" gap this closes) AND on the
                # PokemonSet itself so later calc requests use the real,
                # CONFIRMED ability instead of a Chaos-guessed one.
                ref = self._split_ref(parts[2])
                if ref is not None:
                    pl, sl, nick = ref
                    key = slot_species.get(pl + sl) or nick
                    ability = parts[3].strip()
                    register(pl, key)
                    record_ability(pl, key, ability)
                    events.append(
                        BattleEvent(
                            turn=turn, kind="ability", actor=key, actor_player=pl,
                            effects=[ability],
                            text=f"{pl} {key}'s ability {ability} activated.",
                        )
                    )
            elif tag in ("-boost", "-unboost") and len(parts) > 4 and parts[3].strip() in _CALC_STATS:
                # A real, in-game stat stage change (Intimidate, a setup
                # move like Swords Dance, a self-drop like Superpower, ...).
                # Recorded as its own ordered timeline event — ground truth
                # the explanation AI can narrate — and consumed by
                # TurnReplaySimulator to build a running per-Pokemon stage
                # ledger (reset whenever that identity switches back in,
                # exactly matching the real mechanic) so later damage/speed
                # calcs this game reflect the ACTUAL active stage instead of
                # silently assuming +0. accuracy/evasion changes are skipped
                # — neither @smogon/calc's damage/speed calc nor this
                # project's field model uses them.
                ref = self._split_ref(parts[2])
                if ref is not None:
                    pl, sl, nick = ref
                    key = slot_species.get(pl + sl) or nick
                    stat = parts[3].strip()
                    try:
                        amount = int(parts[4].strip())
                    except ValueError:
                        amount = 0
                    delta = amount if tag == "-boost" else -amount
                    if delta:
                        register(pl, key)
                        direction = "rose" if delta > 0 else "fell"
                        stages = abs(delta)
                        events.append(
                            BattleEvent(
                                turn=turn, kind="boost", actor=key, actor_player=pl,
                                effects=[stat, str(delta)],
                                text=(
                                    f"{pl} {key}'s {stat} {direction} by {stages} "
                                    f"stage{'s' if stages != 1 else ''}."
                                ),
                            )
                        )
            elif tag == "-message" and len(parts) > 2:
                # e.g. "|-message|42 s forfeited." — the game ended WITHOUT the
                # forfeiting side's team being defeated in play. Ground truth
                # the explanation AI needs so it doesn't fabricate a "how the
                # winner earned it" narrative for turns that never happened.
                text = parts[2].strip()
                if text.endswith("forfeited."):
                    forfeited_name = text[: -len(" forfeited.")].strip()
            elif tag == "win" and len(parts) > 2:
                winner_name = parts[2].strip()

        if not players:
            preview = text.strip().splitlines()[:1]
            hint = f" First line seen: {preview[0]!r}." if preview else ""
            raise LogParsingError(
                "No player or Pokemon lines were found in the battle log. A Showdown "
                "log is expected to contain '|player|p1|...', '|poke|p1|Species, L50|' "
                f"and/or '|switch|p1a: Nick|Species, L50|...' lines.{hint}"
            )

        for player, start in tw_open.items():
            tailwind.setdefault(player, []).append([start, turn])
        if tr_open is not None:
            trick_room.append([tr_open, turn])

        for event in events:
            if event.kind == "move":
                event.text = self._render_move(event)

        for bucket in players.values():
            for species, mon in bucket.items():
                if species in statuses and mon.status is None:
                    mon.status = statuses[species]

        sides = [
            SideState(
                player=player,
                player_name=player_names.get(player, ""),
                team=list(mons.values()),
                active=active.get(player, []),
            )
            for player, mons in players.items()
        ]
        outcome = self._build_outcome(
            turn, kos, events, winner_name, player_names, forfeited_name
        )
        field = FieldConditions(
            tailwind=tailwind, trick_room=trick_room, weather=weather, statuses=statuses
        )
        return GameState(
            format_id=format_id, turn=turn, sides=sides, outcome=outcome, field=field
        )

    def _handle_move(
        self,
        parts: list[str],
        turn: int,
        players: dict[str, dict[str, PokemonSet]],
        slot_species: dict[str, str],
        active: dict[str, list[str]],
    ) -> BattleEvent | None:
        ref = self._split_ref(parts[2])
        if ref is None:
            return None
        player, slot, nick = ref
        move = parts[3].strip()
        bucket = players.get(player, {})
        species = (
            nick
            if nick in bucket
            else slot_species.get(player + slot)
            or (active.get(player, [None])[-1] if active.get(player) else None)
        )
        mon = bucket.get(species) if species else None
        if mon and move and move not in mon.moves:
            mon.moves.append(move)
        effects: list[str] = []
        if any("[spread]" in p for p in parts[4:]):
            effects.append("spread")
        return BattleEvent(
            turn=turn, kind="move", actor=species or nick, actor_player=player,
            move=move, effects=effects,
        )

    @staticmethod
    def _add_effect(event: BattleEvent, effect: str) -> None:
        if effect not in event.effects:
            event.effects.append(effect)

    @staticmethod
    def _render_move(event: BattleEvent) -> str:
        line = f"{event.actor_player} {event.actor} used {event.move}"
        if event.effects:
            line += f" ({', '.join(event.effects)})"
        if event.results:
            line += " — " + "; ".join(event.results)
        return line

    @staticmethod
    def _build_outcome(
        turn: int,
        kos: list[KOEvent],
        events: list[BattleEvent],
        winner_name: str | None,
        player_names: dict[str, str],
        forfeited_name: str | None = None,
    ) -> BattleOutcome:
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
        highlights = [
            f"Turn {ko.turn}: {ko.fainted} ({player_names.get(ko.player, ko.player)}) fainted."
            for ko in kos
        ]
        if forfeited_name:
            highlights.append(
                f"{forfeited_name} ({forfeited_player or '?'}) FORFEITED — the game did "
                "NOT end by their team being defeated in play."
            )
        if winner_name:
            highlights.append(f"Winner: {winner_name} ({winner_player or '?'}).")
        return BattleOutcome(
            winner_player=winner_player,
            winner_name=winner_name,
            turns=turn,
            kos=kos,
            events=events,
            highlights=highlights,
            forfeited_player=forfeited_player,
            forfeited_name=forfeited_name,
        )

    @staticmethod
    def _coerce_rating(value: Any) -> int | None:
        try:
            return int(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _split_details(details: str) -> tuple[str, int]:
        """Split ``"Garchomp, L50, M"`` into ``("Garchomp", 50)``."""
        parts = [p.strip() for p in details.split(",")]
        species = parts[0]
        level = 50
        for part in parts[1:]:
            if part.upper().startswith("L") and part[1:].isdigit():
                level = int(part[1:])
        return species, level
