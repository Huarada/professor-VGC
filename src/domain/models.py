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
from typing import Any, Literal

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

    model_config = ConfigDict(frozen=True)

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

    model_config = ConfigDict(frozen=True)

    turn: int
    fainted: str  # species that fainted
    player: str  # side that lost the Pokemon (e.g. "p1")


class TargetHit(BaseModel):
    """What one move did to ONE target, with the target's owning side.

    ``targets``/``results`` on :class:`BattleEvent` are species-only strings
    (kept for the human-readable timeline); this is the structured,
    side-qualified form the deterministic re-checks read, so a mirror match
    (the same species on both sides) never confuses whose Pokemon was hit.
    """

    model_config = ConfigDict(frozen=True)

    player: str
    species: str
    hp_after: float | None = None
    """Target's HP percent after the hit (0.0 on a faint; None when blocked)."""
    blocked_by: str = ""
    """Protect-family move that blocked this move for this target, if any."""


class MonState(BaseModel):
    """One Pokemon's point-in-time battle state (see :class:`BattleSnapshot`)."""

    model_config = ConfigDict(frozen=True)

    hp_percent: float | None = None
    status: str = ""  # "par" | "brn" | "psn" | "tox" | "slp" | "frz" | "" (healthy)
    item: str | None = None
    """``None`` = not determined yet at this point of the log (the original
    item may still be revealed later); ``""`` = confirmed no item (consumed,
    knocked off, ...); otherwise the item held right now."""


class BattleSnapshot(BaseModel):
    """The battle state at the exact moment one move was used.

    Stamped on every move event by the parser (the only component that reads
    the raw log), so every per-move re-check uses the weather/terrain/screens,
    HP, status and items IN EFFECT AT THAT MOMENT — never the end-of-game
    value applied retroactively to earlier turns.
    """

    model_config = ConfigDict(frozen=True)

    weather: str = ""
    terrain: str = ""
    screens: dict[str, list[str]] = Field(default_factory=dict)
    """player -> Reflect / Light Screen / Aurora Veil currently up on that side."""
    active: dict[str, list[str]] = Field(default_factory=dict)
    """player -> species on the field right now."""
    fainted: dict[str, list[str]] = Field(default_factory=dict)
    """player -> species already fainted."""
    mons: dict[str, MonState] = Field(default_factory=dict)
    """``"<player>:<species>"`` -> state, for every Pokemon seen so far."""

    @staticmethod
    def key(player: str, species: str) -> str:
        return f"{player}:{species}"

    def mon(self, player: str, species: str) -> MonState:
        return self.mons.get(self.key(player, species), MonState())


BattleEventKind = Literal[
    "move", "switch", "faint", "ability", "boost", "forme_change",
    "weather", "terrain", "side_start", "side_end", "status", "cure", "item",
]


class BattleEvent(BaseModel):
    """One ordered event from the battle log (a move, switch or faint).

    Capturing the exact ordered ACTIONS — not just faints — is what stops the
    explanation AI from inventing causality (e.g. claiming a Pokemon attacked
    when it actually fainted before acting).

    Field/state changes (weather, terrain, screens, status, items) are ordered
    events too, so every per-turn re-check can replay the exact state at the
    moment each move was used — e.g. a weather war that flips sun to rain in
    the middle of a turn, or a burn that only exists from turn 6 onward.
    Their ``effects`` payload is:

    - ``weather``/``terrain``: ``[name]`` (``""`` when it ended);
    - ``side_start``/``side_end``: ``[condition]`` for ``actor_player``'s side;
    - ``status``/``cure``: ``[status]`` for ``actor``;
    - ``item``: ``[item, change]`` where change is ``"revealed"``,
      ``"acquired"`` (Trick/Switcheroo) or ``"lost"`` (consumed/removed).
    """

    turn: int
    kind: BattleEventKind
    actor: str = ""  # species that acted / switched in
    actor_player: str = ""  # "p1" | "p2"
    slot: str = ""  # battle position letter ("a"/"b") for switch/move events
    move: str = ""
    targets: list[str] = Field(default_factory=list)
    hits: list[TargetHit] = Field(default_factory=list)
    """Structured, side-qualified per-target outcome (see :class:`TargetHit`).
    Empty for events built without a log (tests, structured JSON input); the
    re-checks then fall back to ``targets`` + side resolution."""
    state: BattleSnapshot | None = None
    """Battle state at the moment of this move (move events parsed from a log
    only; ``None`` otherwise)."""
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


class FieldWindow(BaseModel):
    """One named field condition and the inclusive turn window it covered.

    Two windows may share a turn: a weather war (e.g. Charizard-Mega-Y's sun
    replaced by Politoed's rain mid-turn) ends one window and opens the next
    on the same turn.
    """

    model_config = ConfigDict(frozen=True)

    name: str
    start_turn: int
    end_turn: int

    def covers(self, turn: int) -> bool:
        return self.start_turn <= turn <= self.end_turn

    def length(self) -> int:
        return self.end_turn - self.start_turn + 1


class FieldConditions(BaseModel):
    """Per-turn summary of the field, extracted from the battle log.

    Turn windows are inclusive. This is the TURN-level view used for timeline
    annotations and the whole-game matchup verdicts; the per-move re-checks
    replay the ordered state events on ``BattleOutcome.events`` instead, so a
    change in the middle of a turn is applied exactly where it happened.

    Weather/terrain names are the in-game names (``Sun``, ``Rain``, ``Sand``,
    ``Snow``, ``Hail``, ``Harsh Sunshine``, ``Heavy Rain``, ``Strong Winds``;
    ``Electric``, ``Grassy``, ``Psychic``, ``Misty``), never Showdown protocol
    ids — the parser translates them.
    """

    model_config = ConfigDict(frozen=True)

    tailwind: dict[str, list[list[int]]] = Field(default_factory=dict)  # player -> windows
    trick_room: list[list[int]] = Field(default_factory=list)
    weather: list[FieldWindow] = Field(default_factory=list)
    terrain: list[FieldWindow] = Field(default_factory=list)
    screens: dict[str, list[FieldWindow]] = Field(default_factory=dict)
    """player -> Reflect / Light Screen / Aurora Veil windows on that side."""
    final_statuses: dict[str, dict[str, str]] = Field(default_factory=dict)
    """player -> species -> non-volatile status still active when the game
    ended (cures applied). Only the whole-game verdicts read this; the
    per-turn re-checks use the status in effect at each move instead."""

    @staticmethod
    def _in_windows(windows: list[list[int]], turn: int) -> bool:
        return any(w[0] <= turn <= w[1] for w in windows)

    @staticmethod
    def _dominant(windows: list[FieldWindow]) -> str:
        """The condition that covered the most turns (first one on a tie)."""
        totals: dict[str, int] = {}
        for window in windows:
            totals[window.name] = totals.get(window.name, 0) + window.length()
        return max(totals, key=lambda name: totals[name]) if totals else ""

    def had_tailwind(self, player: str) -> bool:
        return bool(self.tailwind.get(player))

    def tailwind_active(self, player: str, turn: int) -> bool:
        return self._in_windows(self.tailwind.get(player, []), turn)

    def trick_room_active(self, turn: int) -> bool:
        return self._in_windows(self.trick_room, turn)

    def had_trick_room(self) -> bool:
        return bool(self.trick_room)

    def weather_on(self, turn: int) -> list[str]:
        """Every weather active at some point during ``turn``, in order."""
        return [w.name for w in self.weather if w.covers(turn)]

    def terrain_on(self, turn: int) -> list[str]:
        """Every terrain active at some point during ``turn``, in order."""
        return [w.name for w in self.terrain if w.covers(turn)]

    def screens_on(self, player: str, turn: int) -> list[str]:
        """Screens up on ``player``'s side at some point during ``turn``."""
        return [w.name for w in self.screens.get(player, []) if w.covers(turn)]

    def dominant_weather(self) -> str:
        return self._dominant(self.weather)

    def dominant_terrain(self) -> str:
        return self._dominant(self.terrain)


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

    model_config = ConfigDict(frozen=True)

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

    model_config = ConfigDict(frozen=True)

    metagame: str = "unknown"
    pokemon_stats: dict[str, PokemonMetaSummary] = Field(default_factory=dict)
    current_tier_stats: dict[str, PokemonMetaSummary] = Field(default_factory=dict)
    rating_note: str = ""


class SideField(BaseModel):
    """Side conditions that change damage or speed for one side of a calc."""

    model_config = ConfigDict(frozen=True)

    tailwind: bool = False
    reflect: bool = False
    light_screen: bool = False
    aurora_veil: bool = False
    helping_hand: bool = False
    """The Pokemon on this side was boosted by an ally's Helping Hand this turn."""
    friend_guard: bool = False
    """An ally with Friend Guard is on the field next to this side's Pokemon."""

    def labels(self) -> list[str]:
        names = {
            "tailwind": "Tailwind", "reflect": "Reflect", "light_screen": "Light Screen",
            "aurora_veil": "Aurora Veil", "helping_hand": "Helping Hand",
            "friend_guard": "Friend Guard",
        }
        return [label for key, label in names.items() if getattr(self, key)]


class CalcField(BaseModel):
    """Typed field state for one calc, in domain vocabulary (the calc adapter
    translates it to the engine's own option names)."""

    model_config = ConfigDict(frozen=True)

    weather: str = ""
    terrain: str = ""
    trick_room: bool = False
    attacker_side: SideField = Field(default_factory=SideField)
    defender_side: SideField = Field(default_factory=SideField)

    def swapped(self) -> "CalcField":
        """The same field seen from the defender's side (attacker <-> defender)."""
        return self.model_copy(
            update={"attacker_side": self.defender_side, "defender_side": self.attacker_side}
        )


class MoveInfo(BaseModel):
    """Static move data from the calc engine's dex (never a hand-kept list)."""

    model_config = ConfigDict(frozen=True)

    name: str
    known: bool = False
    category: str = ""  # "Physical" | "Special" | "Status" ("" when unknown)
    is_spread: bool = False
    is_protect: bool = False
    """Protect-family (stalling) move: Protect, Detect, Spiky Shield, ..."""
    speed_control: str = ""
    """"tailwind" | "trick_room" | "speed_drop" | "paralysis" | "" (none)."""
    speed_drop_stages: int = 0
    """Guaranteed Speed stages removed from the target (e.g. 1 for Icy Wind)."""

    @property
    def is_damaging(self) -> bool:
        """Unknown moves count as damaging so the engine itself gets to judge
        (and reject) them, rather than being silently skipped here."""
        return self.category != "Status" or not self.known


class CalcRequest(BaseModel):
    """Input contract for a single deterministic damage calculation."""

    model_config = ConfigDict(frozen=True)

    gen: int = 9
    attacker: PokemonSet
    defender: PokemonSet
    move: str
    field: CalcField = Field(default_factory=CalcField)
    defender_hp_percent: float | None = None
    """Defender's CURRENT HP percent, so KO chances reflect damage already
    taken (None = full HP)."""

    @field_validator("move")
    @classmethod
    def _move_not_blank(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("move must be a non-empty string")
        return value.strip()


class DamageResult(BaseModel):
    """Output contract returned by the Node ``@smogon/calc`` subsystem."""

    model_config = ConfigDict(frozen=True)

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

    model_config = ConfigDict(frozen=True)

    faster: str
    slower: str
    faster_speed: int
    slower_speed: int
    is_tie: bool = False
    trick_room: bool = False
    conditions: list[str] = Field(default_factory=list)


class MatchupVerdict(BaseModel):
    """Deterministic verdict for a single ordered attacker->defender matchup."""

    model_config = ConfigDict(frozen=True)

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

    model_config = ConfigDict(frozen=True)

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

    model_config = ConfigDict(frozen=True)

    focus_species: list[str] = Field(default_factory=list)
    matchups: list[tuple[str, str]] = Field(default_factory=list)
    rationale: str = ""


class TurnDamageCheck(BaseModel):
    """Deterministic re-check of one move's damage against a single target."""

    model_config = ConfigDict(frozen=True)

    target: str
    target_player: str = ""
    target_hp_before_percent: float | None = None
    """Target's HP right before the hit; ``projected_ko_text`` is computed
    from this HP, not from full HP (None = unknown, full HP assumed)."""
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

    model_config = ConfigDict(frozen=True)

    move: str
    target: str
    min_percent: float = 0.0
    max_percent: float = 0.0
    ko_chance_text: str = ""
    is_ko_guaranteed: bool = False
    description: str = ""  # e.g. "0 SpA Raichu Zap Cannon vs. 0 HP/0 SpD Basculegion: ..."


class ThreatCheck(BaseModel):
    """The strongest confirmed attack one opposing active Pokemon had into the
    actor at the moment the actor moved (at the actor's real HP then)."""

    model_config = ConfigDict(frozen=True)

    attacker: str
    attacker_player: str
    move: str
    min_percent: float = 0.0
    max_percent: float = 0.0
    ko_chance_text: str = ""
    can_ko: bool = False
    """The top roll covers the actor's remaining HP (a same-turn KO is possible)."""
    moves_first: bool | None = None
    """Whether this threat moves before the actor under that turn's field
    (None when the speed check was unavailable)."""


class DecisionKind(str, Enum):
    """Non-attacking plays a VGC player weighs every turn."""

    PROTECT = "protect"
    SWITCH = "switch"
    SPEED_CONTROL = "speed_control"


class DecisionOption(BaseModel):
    """One deterministic, engine-verified alternative to the play that was made.

    Only built from moves CONFIRMED for that Pokemon this game and from
    Pokemon actually brought, and only when an incoming threat could KO the
    actor that turn — never a guess and never noise for a safe turn.
    """

    model_config = ConfigDict(frozen=True)

    kind: DecisionKind
    summary: str
    move: str = ""  # the Protect / speed-control move this option uses
    switch_to: str = ""
    against: str = ""  # the threat this option answers
    max_percent_taken: float | None = None
    """For a switch: the worst confirmed hit the switch-in would have taken."""


class TurnCheck(BaseModel):
    """Per-turn ground-truth verification of one action against the engine.

    For every move actually used in the battle, the deterministic engine is
    re-consulted under the field state AT THAT MOVE (weather, terrain,
    screens, Helping Hand, status, items, HP): projected damage for the move
    that was used, the field-aware speed order, and the conditions applied.
    This is the turn-by-turn feedback loop (not a single whole-game pass).

    ``best_alternatives`` closes the loop further: the engine is re-consulted
    for EVERY damaging move confirmed for that Pokemon this game against EVERY
    opposing Pokemon on the field at that moment (so a better target is
    surfaced, not only a better move into the same target). The top 4 (ranked
    OHKO-first, then by damage) are kept. ``incoming_threats`` and
    ``decision_options`` cover the non-attacking side of the decision:
    whether the actor was in KO range, and whether Protect, a switch or speed
    control (each confirmed for this game) would have answered that threat.
    """

    model_config = ConfigDict(frozen=True)

    turn: int
    actor: str
    actor_player: str = ""
    actor_hp_percent: float | None = None
    move: str = ""
    effects: list[str] = Field(default_factory=list)
    conditions: list[str] = Field(default_factory=list)  # field active at this move
    damage_checks: list[TurnDamageCheck] = Field(default_factory=list)
    best_alternatives: list[OptimalMoveOption] = Field(default_factory=list)
    incoming_threats: list[ThreatCheck] = Field(default_factory=list)
    decision_options: list[DecisionOption] = Field(default_factory=list)
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

    model_config = ConfigDict(frozen=True)

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

    model_config = ConfigDict(frozen=True)

    tool: str
    """Tool name: ``damage_calc`` / ``chaos_meta_stats`` / ``smogon_strategy``."""
    arguments: dict[str, Any] = Field(default_factory=dict)
    ok: bool = True
    """Whether the underlying deterministic call succeeded (mirrors the same
    ``{ok:false,error}`` degrade convention used at the Node IPC boundary)."""
    summary: str = ""
    """Short, UI-facing preview of the result or error — not the full payload."""


class AnalysisEvidence(BaseModel):
    """Everything the deterministic + probabilistic stages produced for one
    analysis turn — the single ground-truth bundle every orchestration
    backend hands to its explanation stage. Built once by the shared
    ``GroundTruthAssembler`` so the backends can never drift apart."""

    model_config = ConfigDict(frozen=True)

    selection: SelectionPlan
    meta_context: MetaContext
    verdicts: list[MatchupVerdict] = Field(default_factory=list)
    turn_checks: list[TurnCheck] = Field(default_factory=list)
    protect_reads: list[ProtectRead] = Field(default_factory=list)
    strategies: list[SmogonStrategy] = Field(default_factory=list)
    improvement_suggestions: dict[str, Any] = Field(default_factory=dict)
    recurring_concepts: list[dict[str, str]] = Field(default_factory=list)
    battle_result: str = ""


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

    model_config = ConfigDict(frozen=True)

    role: str  # "user" | "assistant" | "system"
    content: str
