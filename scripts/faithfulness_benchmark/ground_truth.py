"""Builds the deterministic ground-truth lookup for one fixture.

Every lookup here is derived ONLY from :class:`~src.domain.models.GameState`
(the parsed log) and :class:`~src.domain.models.AnalysisResult` (the real
pipeline's own computed output) — never from an LLM. This is exactly what
step 4 of the benchmark spec calls for: verification is a plain Python
comparison against structured data the project already produces.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from src.domain.models import AnalysisResult, GameState


def _norm(text: str) -> str:
    """Same normalization the production code uses for move/species matching
    (see turn_simulator._norm_move) — lowercase, strip spaces/hyphens/apostrophes,
    so "Zap Cannon" / "zap-cannon" / "ZAP CANNON" all compare equal."""
    return text.lower().replace(" ", "").replace("-", "").replace("'", "")


def _norm_forme(text: str) -> str:
    """Looser than _norm: also drops the word "mega" so "Mega Gengar" and
    "Gengar-Mega" compare equal regardless of which order/style prose uses."""
    return _norm(text).replace("mega", "")


def _core(text: str) -> str:
    """Normalize a player/side reference down to its bare comparable core:
    lowercase, drop the word "player" and all non-alphanumeric characters, so
    "Player b (p2)", "Player 2" and "p2" all reduce to something a short
    substring check can match against each other."""
    return "".join(ch for ch in text.lower().replace("player", "") if ch.isalnum())


def _side_aliases(player_id: str, player_name: str) -> set[str]:
    """Every short token that could plausibly stand in for one side in prose:
    its raw id ("p2"), the bare digit ("2"), "player2", and its display name."""
    aliases = {_core(player_id)}
    digit = player_id[-1] if player_id and player_id[-1].isdigit() else ""
    if digit:
        aliases.add(digit)
        aliases.add(f"player{digit}")
    if player_name:
        aliases.add(_core(player_name))
    return {a for a in aliases if a}


def _matches_side(claimed: str, aliases: set[str]) -> bool:
    core = _core(claimed)
    if not core:
        return False
    return any(core == a or core in a or a in core for a in aliases)


def resolve_species(text: str, known: set[str]) -> str | None:
    """Resolve a judge-extracted subject string to a real, known species.

    The production explanation prompt REQUIRES every mention to carry its
    p1/p2 side prefix (CLAUDE.md's own anti-misattribution rule), so a
    faithful grounded answer routinely says "p2 Garchomp", "Gengar (p2)", or
    "Ash's Flutter Mane" rather than a bare species name. An exact-string
    match against the known roster would wrongly flag every one of those as
    a hallucinated Pokemon. Instead: normalize the same way real_species
    already is, then check whether any known species name appears as a
    substring of the claimed text — preferring the longest match, so e.g.
    "Raichu" doesn't shadow a real "Alolan Raichu" entry (not applicable to
    this benchmark's fixtures, but a real product could hit it)."""
    norm = _norm(text)
    if norm in known:
        return norm
    candidates = [k for k in known if k and k in norm]
    if not candidates:
        return None
    return max(candidates, key=len)


@dataclass
class GroundTruth:
    game_state: GameState
    analysis: AnalysisResult | None

    real_species: set[str] = field(default_factory=set)  # normalized, ALL known (incl. benched)
    in_play: set[str] = field(default_factory=set)  # normalized, side.brought() only
    moves_used: dict[str, set[str]] = field(default_factory=dict)  # species(norm) -> moves(norm)
    forme_changes: dict[str, set[str]] = field(default_factory=dict)  # species(norm) -> formes(norm)
    boost_deltas: list[tuple[str, str, int]] = field(default_factory=list)  # (species, stat, delta)
    blocked_pairs: set[tuple[str, str]] = field(default_factory=set)  # (blocker(norm), move(norm))
    damage_ranges: dict[tuple[str, str, str], list[tuple[float, float]]] = field(default_factory=dict)
    winner: str | None = None
    forfeited: str | None = None
    winner_aliases: set[str] = field(default_factory=set)
    loser_aliases: set[str] = field(default_factory=set)
    forfeited_aliases: set[str] = field(default_factory=set)

    @classmethod
    def build(cls, game_state: GameState, analysis: AnalysisResult | None) -> "GroundTruth":
        gt = cls(game_state=game_state, analysis=analysis)
        gt.real_species = {_norm(s) for s in game_state.involved_species()}
        gt.in_play = {_norm(s) for s in game_state.side_of().keys()}

        if game_state.outcome is not None:
            for event in game_state.outcome.events:
                if event.kind == "move" and event.move:
                    gt.moves_used.setdefault(_norm(event.actor), set()).add(_norm(event.move))
                    for blocker in event.blocked:
                        gt.blocked_pairs.add((_norm(blocker), _norm(event.move)))
                elif event.kind == "forme_change" and event.effects:
                    gt.forme_changes.setdefault(_norm(event.actor), set()).add(
                        _norm_forme(event.effects[0])
                    )
                elif event.kind == "boost" and len(event.effects) == 2:
                    stat, delta_str = event.effects
                    try:
                        delta = int(delta_str)
                    except ValueError:
                        continue
                    gt.boost_deltas.append((_norm(event.actor), stat.lower(), delta))
            gt.winner = game_state.outcome.winner_name
            gt.forfeited = game_state.outcome.forfeited_name

            side_by_player = {s.player: s for s in game_state.sides}
            winner_player = game_state.outcome.winner_player
            if winner_player and winner_player in side_by_player:
                winner_side = side_by_player[winner_player]
                gt.winner_aliases = _side_aliases(winner_side.player, winner_side.player_name)
                for pid, side in side_by_player.items():
                    if pid != winner_player:
                        gt.loser_aliases |= _side_aliases(side.player, side.player_name)
            forfeited_player = game_state.outcome.forfeited_player
            if forfeited_player and forfeited_player in side_by_player:
                fside = side_by_player[forfeited_player]
                gt.forfeited_aliases = _side_aliases(fside.player, fside.player_name)

        if analysis is not None:
            for tc in analysis.turn_checks:
                for dmg in tc.damage_checks:
                    key = (_norm(tc.actor), _norm(dmg.target), _norm(tc.move))
                    gt.damage_ranges.setdefault(key, []).append(
                        (dmg.projected_min_percent, dmg.projected_max_percent)
                    )
                # best_alternatives share the same (single) target as the turn's
                # real move whenever one exists.
                target = tc.damage_checks[0].target if tc.damage_checks else None
                if target:
                    for alt in tc.best_alternatives:
                        key = (_norm(tc.actor), _norm(target), _norm(alt.move))
                        gt.damage_ranges.setdefault(key, []).append((alt.min_percent, alt.max_percent))
            for v in analysis.verdicts:
                dmg = v.best_damage
                key = (_norm(v.attacker), _norm(v.defender), _norm(v.best_move))
                gt.damage_ranges.setdefault(key, []).append((dmg.min_percent, dmg.max_percent))

        return gt
