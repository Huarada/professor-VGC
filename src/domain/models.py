"""Domain models — hard contracts for every boundary in the pipeline.

These Pydantic v2 models are the single source of truth for the shapes that
flow through the system:

    raw replay JSON  ->  GameState            (parser adapter output)
    GameState        ->  MetaContext          (chaos adapter output)
    matchup request  ->  DamageResult         (calc engine output)
    everything       ->  AnalysisResult       (service output / UI DTO)

No behaviour lives here beyond validation and light normalization. Business
rules live in services; infrastructure lives in adapters.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Stat(str, Enum):
    """The six canonical Pokemon stats, keyed by Showdown/Smogon short codes."""

    HP = "hp"
    ATK = "atk"
    DEF = "def"
    SPA = "spa"
    SPD = "spd"
    SPE = "spe"


class Archetype(str, Enum):
    """Common VGC macro-strategies referenced by the ADR."""

    SWEEPER = "sweeper"
    SAFE_SWAPPER = "safe_swapper"
    TRICK_ROOM = "trick_room"
    PERISH_TRAP = "perish_trap"
    HYPER_OFFENSE = "hyper_offense"
    BALANCE = "balance"
    UNKNOWN = "unknown"


class StatSpread(BaseModel):
    """An EV or IV spread expressed as real in-game values (0-252 for EVs)."""

    model_config = ConfigDict(frozen=True, populate_by_name=True)

    hp: int = 0
    atk: int = 0
    def_: int = Field(default=0, alias="def")
    spa: int = 0
    spd: int = 0
    spe: int = 0

    def as_dict(self) -> dict[str, int]:
        """Return the spread keyed by Showdown short codes."""
        return {
            "hp": self.hp,
            "atk": self.atk,
            "def": self.def_,
            "spa": self.spa,
            "spd": self.spd,
            "spe": self.spe,
        }


class PokemonSet(BaseModel):
    """A concrete, (partially) known set for a single Pokemon in a battle.

    Fields may be ``None`` when the information is hidden (incomplete-
    information game). Missing fields are later back-filled with the most
    likely values coming from the Chaos metagame snapshot.
    """

    species: str
    level: int = 50
    ability: str | None = None
    item: str | None = None
    tera_type: str | None = None
    nature: str | None = None
    status: str | None = None  # e.g. "par", "brn", "slp" (affects speed/damage)
    moves: list[str] = Field(default_factory=list)
    evs: StatSpread | None = None
    ivs: StatSpread | None = None
    battle_formes: list[str] = Field(default_factory=list)
    """Other appearances (``details``) observed for this same identity mid-game,
    e.g. a Mega Evolution ("Raichu-Mega-Y") or another in-battle forme change.
    All damage/speed calcs still use ``species`` (the base identity, so move
    history stays attached across the change) — this only records that a change
    happened, so callers can flag when the calc's stats may not reflect it."""
    boosts: dict[str, int] = Field(default_factory=dict)
    """Stat stage modifiers (-6..+6) ACTIVE AT THE MOMENT this particular
    PokemonSet snapshot represents — e.g. -1 atk from an observed Intimidate.
    Not a fixed attribute of the Pokemon like ability/item: a fresh,
    boost-adjusted copy is built per turn by TurnReplaySimulator (stages
    reset to empty every time the real Pokemon switches out, exactly as in
    the actual game), never mutated on the shared indexed set."""

    @field_validator("species")
    @classmethod
    def _species_not_blank(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("species must be a non-empty string")
        return value.strip()


class SideState(BaseModel):
    """One player's side of the battle."""

    player: str
    player_name: str = ""
    team: list[PokemonSet] = Field(default_factory=list)
    active: list[str] = Field(default_factory=list)

    def brought(self) -> list[str]:
        """Species actually brought into the game (seen switching in)."""
        return list(self.active)


class KOEvent(BaseModel):
    """A single faint recorded from the battle log."""

    turn: int
    fainted: str  # species that fainted
    player: str  # side that lost the Pokemon (e.g. "p1")


class BattleEvent(BaseModel):
    """One ordered event from the battle log (a move, switch or faint).

    Capturing the exact ordered ACTIONS — not just faints — is what stops the
    explanation AI from inventing causality (e.g. claiming a Pokemon attacked
    when it actually fainted before acting).
    """

    turn: int
    kind: str  # "move" | "switch" | "faint" | "ability" | "boost" | "forme_change"
    actor: str = ""  # species that acted / switched in
    actor_player: str = ""  # "p1" | "p2"
    move: str = ""
    targets: list[str] = Field(default_factory=list)
    effects: list[str] = Field(default_factory=list)  # spread / super effective / ...
    results: list[str] = Field(default_factory=list)  # e.g. "Torkoal->43%", "Pyroar fainted"
    blocked: list[str] = Field(default_factory=list)
    """Species that blocked THIS move with a Protect-family move (Protect,
    Detect, Spiky Shield, Wide Guard, ...). Kept separate from ``targets`` (so
    existing "first real target" logic elsewhere is unaffected) — this is what
    lets a risk/reward read of the turn be grounded in the real declared
    target of a blocked spread move, not just inferred from a nearby Protect
    line."""
    text: str = ""  # deterministic human-readable rendering


class BattleOutcome(BaseModel):
    """Deterministic result and timeline extracted from the battle log.

    This is what lets the explanation AI reason about what *actually happened*
    (the ordered actions, who won, which Pokemon fainted and when) instead of
    only reasoning about isolated damage rolls. Populated only when a battle
    log is available.
    """

    winner_player: str | None = None  # "p1" | "p2"
    winner_name: str | None = None
    turns: int = 0
    kos: list[KOEvent] = Field(default_factory=list)
    events: list[BattleEvent] = Field(default_factory=list)  # ordered timeline
    highlights: list[str] = Field(default_factory=list)  # human-readable faint list
    forfeited_player: str | None = None  # "p1" | "p2" — set when THIS player quit early
    forfeited_name: str | None = None
    """When non-None, the game did NOT end by a team being fully defeated in
    play — the named player forfeited/disconnected. The explanation AI must
    know this: it must not narrate the win as "earned" through further
    strategy beyond what the timeline actually shows, and must not imply the
    forfeiting side's remaining (unfainted, un-brought) Pokemon lost a fight
    they never had."""


class FieldConditions(BaseModel):
    """Deterministic field/status ledger extracted from the battle log.

    Turn windows are inclusive ``[start_turn, end_turn]`` pairs. This feeds the
    field-aware speed and damage calculations so the engine (and the AI) reason
    about the REAL conditions (Tailwind, Trick Room, weather, status) instead of
    raw base stats.
    """

    tailwind: dict[str, list[list[int]]] = Field(default_factory=dict)  # player -> windows
    trick_room: list[list[int]] = Field(default_factory=list)
    weather: str = ""
    statuses: dict[str, str] = Field(default_factory=dict)  # species -> last status

    @staticmethod
    def _in_windows(windows: list[list[int]], turn: int) -> bool:
        return any(w[0] <= turn <= w[1] for w in windows)

    def had_tailwind(self, player: str) -> bool:
        return bool(self.tailwind.get(player))

    def tailwind_active(self, player: str, turn: int) -> bool:
        return self._in_windows(self.tailwind.get(player, []), turn)

    def trick_room_active(self, turn: int) -> bool:
        return self._in_windows(self.trick_room, turn)

    def had_trick_room(self) -> bool:
        return bool(self.trick_room)


class GameState(BaseModel):
    """Structured, deterministic snapshot extracted from a Showdown replay.

    This is the output of the *cleaning/filtering (determinism)* stage in the
    flow diagram: raw unstructured replay JSON becomes a typed object listing
    exactly which Pokemon are involved plus, when a log is present, the result.
    """

    format_id: str = "gen9vgc2025"
    turn: int = 0
    rating: int | None = None
    sides: list[SideState] = Field(default_factory=list)
    outcome: BattleOutcome | None = None
    field: FieldConditions | None = None

    def involved_species(self) -> list[str]:
        """Return the de-duplicated list of every species seen in the battle."""
        seen: dict[str, None] = {}
        for side in self.sides:
            for mon in side.team:
                seen.setdefault(mon.species, None)
            for name in side.active:
                seen.setdefault(name, None)
        return list(seen.keys())

    def side_of(self) -> dict[str, str]:
        """Map each species ACTUALLY BROUGHT into the game to its owning player.

        Deliberately excludes team-preview-only Pokemon (listed in ``|poke|``
        lines but never switched in): they never played, so they must never be
        offered as a deterministic-calc matchup or narrated as if they acted.
        Falls back to the full team only for a side that never switched anyone
        in (e.g. a still-in-progress or malformed log), mirroring
        :func:`~src.services.battle_context.rosters`.
        """
        owner: dict[str, str] = {}
        for side in self.sides:
            names = side.brought() or [mon.species for mon in side.team]
            for name in names:
                owner.setdefault(name, side.player)
        return owner

    def brought_by_player(self) -> dict[str, list[str]]:
        """Map each player id to the species it actually brought into the game."""
        return {side.player: side.brought() for side in self.sides}


class PokemonMetaSummary(BaseModel):
    """Top-N metagame statistics for a single species (from Chaos data)."""

    top_abilities: dict[str, float] = Field(default_factory=dict)
    top_items: dict[str, float] = Field(default_factory=dict)
    top_moves: dict[str, float] = Field(default_factory=dict)
    top_spreads: list[str] = Field(default_factory=list)  # human-readable, for the LLM prompt
    top_spread_nature: str | None = None
    top_spread_evs: StatSpread | None = None
    """Structured form of top_spreads[0] (the single most-used nature/EV
    spread in this tier) — machine-readable so MatchupEvaluator.enrich_set
    can back-fill a calc request's nature/EVs when the replay never revealed
    them, instead of silently defaulting to 0 EVs/neutral nature. None when
    no spread data exists for this species."""
    threats_winrate: dict[str, float] = Field(default_factory=dict)
    source: str = ""  # which reg/rating-tier produced this (may be a fallback)

    def is_empty(self) -> bool:
        return not (self.top_abilities or self.top_items or self.top_moves)


class MetaContext(BaseModel):
    """Consolidated, compact metagame context injected into LLM prompts.

    ``pokemon_stats`` holds the IDEAL (highest rating tier, with reg fallback)
    stats that drive suggestions. ``current_tier_stats`` holds the stats for the
    rating bracket the analyzed match falls into, so the AI can contrast the
    aspirational meta with what actually happens at the player's ladder tier.
    """

    metagame: str = "unknown"
    pokemon_stats: dict[str, PokemonMetaSummary] = Field(default_factory=dict)
    current_tier_stats: dict[str, PokemonMetaSummary] = Field(default_factory=dict)
    rating_note: str = ""


class CalcRequest(BaseModel):
    """Input contract for a single deterministic damage calculation."""

    gen: int = 9
    attacker: PokemonSet
    defender: PokemonSet
    move: str
    field: dict[str, Any] = Field(default_factory=dict)

    @field_validator("move")
    @classmethod
    def _move_not_blank(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("move must be a non-empty string")
        return value.strip()


class DamageResult(BaseModel):
    """Output contract returned by the Node ``@smogon/calc`` subsystem."""

    attacker: str
    defender: str
    move: str
    damage_rolls: list[int] = Field(default_factory=list)
    min_percent: float = 0.0
    max_percent: float = 0.0
    ko_chance_text: str = ""
    is_ko_guaranteed: bool = False
    description: str = ""


class SpeedComparison(BaseModel):
    """Deterministic speed-tier comparison between two Pokemon.

    ``faster`` is the Pokemon that MOVES FIRST under the given field conditions
    (Tailwind, paralysis, Choice Scarf, Trick Room). ``conditions`` lists the
    modifiers that were applied so the explanation AI can cite them.
    """

    faster: str
    slower: str
    faster_speed: int
    slower_speed: int
    is_tie: bool = False
    trick_room: bool = False
    conditions: list[str] = Field(default_factory=list)


class MatchupVerdict(BaseModel):
    """Deterministic verdict for a single ordered attacker->defender matchup."""

    attacker: str
    defender: str
    best_move: str
    best_damage: DamageResult
    speed: SpeedComparison | None = None
    stat_caveat: str = ""
    """Non-empty when the attacker/defender was observed with an in-battle
    forme change (e.g. Mega Evolution) the calc engine has no stats for, so
    this verdict was computed with the base form instead — see the text."""


class SmogonStrategy(BaseModel):
    """Narrative strategy knowledge for a species (from Smogon dex/usage)."""

    species: str
    overview: str = ""
    common_sets: list[str] = Field(default_factory=list)
    common_teammates: list[str] = Field(default_factory=list)
    archetypes: list[Archetype] = Field(default_factory=list)
    # Which passage(s) `overview` actually came from, and why — e.g. "semantic
    # retrieval: 2/7 chunks across 3 formats" vs "" (the plain default: first
    # available format, no ranking). Purely informational (surfaced in the
    # "Strategies" debug expander in the UI), never read by the LLM prompt —
    # keeps the retrieval mechanism honest/inspectable without it becoming
    # another thing the explanation could hallucinate about.
    retrieval_note: str = ""


class AnalysisRequest(BaseModel):
    """Everything the UI hands to the orchestrator for one analysis turn."""

    session_id: str
    replay_json: dict[str, Any] | None = None
    replay_raw_text: str | None = None
    question: str = ""
    provider: str = "openai"


class SelectionPlan(BaseModel):
    """Output of the 1st AI: which species/matchups matter for the question."""

    focus_species: list[str] = Field(default_factory=list)
    matchups: list[tuple[str, str]] = Field(default_factory=list)
    rationale: str = ""


class TurnDamageCheck(BaseModel):
    """Deterministic re-check of one move's damage against a single target."""

    target: str
    projected_min_percent: float = 0.0
    projected_max_percent: float = 0.0
    projected_ko_text: str = ""
    actual_result: str = ""  # what the log recorded, e.g. "->43%" or "fainted"
    actual_hp_remaining_percent: float | None = None
    """The same fact as ``actual_result``, as a plain number instead of an
    embedded string — HOW MUCH HP THE TARGET HAD LEFT after this hit (0.0 on
    a faint). This is deliberately NOT the same quantity as
    ``projected_min/max_percent`` (which is damage DEALT this hit): giving
    the explanation model a clean, separately-labeled number for each
    removes any need for it to derive one from the other inline in prose,
    which is where a live faithfulness benchmark found it sometimes
    conflates the two (see ADR-029). ``None`` when this target only appears
    here because it blocked the move with Protect — no HP changed for them.
    """
    description: str = ""  # e.g. "0 SpA Raichu Zap Cannon vs. 0 HP/0 SpD Basculegion: ..."


class OptimalMoveOption(BaseModel):
    """One candidate move's projected outcome, for ranking the optimal play.

    Computed only from moves CONFIRMED for that Pokemon this game (never a
    guess/placeholder), against the turn's real target, under that turn's real
    field conditions.
    """

    move: str
    target: str
    min_percent: float = 0.0
    max_percent: float = 0.0
    ko_chance_text: str = ""
    is_ko_guaranteed: bool = False
    description: str = ""  # e.g. "0 SpA Raichu Zap Cannon vs. 0 HP/0 SpD Basculegion: ..."


class TurnCheck(BaseModel):
    """Per-turn ground-truth verification of one action against the engine.

    For every move actually used in the battle, the deterministic engine is
    re-consulted under THAT turn's field conditions: projected damage for the
    move that was used, the field-aware speed order, and the conditions applied.
    This is the turn-by-turn feedback loop (not a single whole-game pass).

    ``best_alternatives`` closes the loop further: the engine is re-consulted
    again for EVERY OTHER move confirmed for that Pokemon this game (up to and
    including a guaranteed OHKO), against the same real target and field, so
    the optimal play that turn is validated deterministically instead of only
    checking the move that was actually used. The top 4 (ranked OHKO-first,
    then by damage) are kept.
    """

    turn: int
    actor: str
    actor_player: str = ""
    move: str = ""
    effects: list[str] = Field(default_factory=list)
    conditions: list[str] = Field(default_factory=list)  # field active that turn
    damage_checks: list[TurnDamageCheck] = Field(default_factory=list)
    best_alternatives: list[OptimalMoveOption] = Field(default_factory=list)
    speed: "SpeedComparison | None" = None
    note: str = ""
    stat_caveat: str = ""
    """Non-empty when the actor/target was observed with an in-battle forme
    change (e.g. Mega Evolution) the calc engine has no stats for, so this
    turn's numbers were computed with the base form instead — see the text."""


class ProtectRead(BaseModel):
    """Deterministic classification of one Protect-family block.

    Computed once by :meth:`~src.services.turn_simulator.TurnReplaySimulator.
    build_protect_reads` from data already present in ``turn_by_turn_checks``,
    so the explanation AI narrates a precomputed conclusion (spread vs.
    genuine single-target read, whether the block was actually under lethal
    pressure, whether it was misallocated relative to a teammate lost the
    same turn) instead of deriving that game-theory judgment itself from raw
    numbers, which is where predictive analyses used to go shallow or wrong.
    """

    turn: int
    blocker: str
    blocker_player: str
    attacker: str
    attacker_player: str
    move: str
    is_spread_move: bool
    """True when the blocked move also hit (or could hit) another target the
    same turn — that "protect-resistant" coverage means committing to the
    move never required a correct read, so it is NOT a prediction."""
    value_denied: TurnDamageCheck
    """The blocked target's own damage_checks entry — the exact stake the
    block avoided."""
    other_targets_hit: list[TurnDamageCheck] = Field(default_factory=list)
    """The SAME move's damage_checks entries for targets that were NOT
    blocked this turn (empty for a single-target move)."""
    is_genuine_read: bool
    """NOT is_spread_move. A single-target move that got blocked fully
    whiffs if the read is wrong — this is the one worth calling a "read"."""
    was_immediate_ko_threat: bool
    """True when value_denied's projected_ko_text shows a same-turn OHKO
    chance (contains "OHKO"), as opposed to a multi-hit KO text like
    "guaranteed 2HKO" which is not a threat THIS turn."""
    misallocated: bool
    """True when the blocker was NOT under immediate KO threat this turn
    while a teammate on the same side fainted the same turn — protecting the
    wrong Pokemon, not a good decision even if the game was later won."""
    teammate_fainted: str = ""
    """Species that fainted this same turn on the blocker's side, if any."""


class AgentToolInvocation(BaseModel):
    """One on-demand deterministic lookup the explanation agent made mid-turn.

    Populated only by the LangChain backend's agentic follow-up path (see
    ADR-028): the explanation stage there is a bounded tool-calling agent
    (``langchain.agents.create_agent``) wrapping the SAME deterministic ports
    (``CalcEngineAdapter``/``MetaStatsProvider``/``StrategyKnowledgeProvider``)
    used everywhere else, so a result is exactly as trustworthy as any other
    calc/Chaos/Smogon lookup in this pipeline — it is simply requested by the
    model, on demand, for a question the precomputed context didn't already
    cover (e.g. a hypothetical item/moveset), rather than precomputed. The
    native ``AnalysisService`` has no agent loop and never populates this.
    """

    tool: str
    """Tool name: ``damage_calc`` / ``chaos_meta_stats`` / ``smogon_strategy``."""
    arguments: dict[str, Any] = Field(default_factory=dict)
    ok: bool = True
    """Whether the underlying deterministic call succeeded (mirrors the same
    ``{ok:false,error}`` degrade convention used at the Node IPC boundary)."""
    summary: str = ""
    """Short, UI-facing preview of the result or error — not the full payload."""


class AnalysisResult(BaseModel):
    """Final DTO rendered by the UI (the green *Resposta OUTPUT* node)."""

    session_id: str
    question: str
    answer: str
    selection: SelectionPlan
    meta_context: MetaContext
    verdicts: list[MatchupVerdict] = Field(default_factory=list)
    turn_checks: list[TurnCheck] = Field(default_factory=list)
    protect_reads: list[ProtectRead] = Field(default_factory=list)
    strategies: list[SmogonStrategy] = Field(default_factory=list)
    agent_tool_calls: list[AgentToolInvocation] = Field(default_factory=list)
    battle_result: str = ""
    provider: str = "openai"


class ChatMessage(BaseModel):
    """A single conversational turn stored in memory."""

    role: str  # "user" | "assistant" | "system"
    content: str
