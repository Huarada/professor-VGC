"""Domain models: the Pydantic v2 contracts at every pipeline boundary.

    raw replay JSON  ->  GameState       (parser)
    GameState        ->  MetaContext     (Chaos)
    CalcRequest      ->  DamageResult    (calc engine)
    everything       ->  AnalysisResult  (service output / UI DTO)

Validation and light normalization only; rules live in services.
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
    """Common VGC macro-strategies."""

    SWEEPER = "sweeper"
    SAFE_SWAPPER = "safe_swapper"
    TRICK_ROOM = "trick_room"
    PERISH_TRAP = "perish_trap"
    HYPER_OFFENSE = "hyper_offense"
    BALANCE = "balance"
    UNKNOWN = "unknown"


class StatSpread(BaseModel):
    """An EV or IV spread in real in-game values (0-252 for EVs)."""

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
    """A (partially) known set; hidden fields are ``None`` and later
    back-filled from the most likely Chaos values."""

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
    """Formes seen mid-game (e.g. a Mega). Calcs still use ``species``; this
    only lets callers flag that stats may not reflect the change."""
    boosts: dict[str, int] = Field(default_factory=dict)
    """Stat stages (-6..+6) at the moment this copy represents; built fresh
    per turn, reset on switch-out, never mutated on the shared set."""

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
    """What one move did to one target, side-qualified so a mirror match
    (same species on both sides) is never ambiguous."""

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
    """``None`` = not known yet; ``""`` = confirmed no item; else the item held."""


class BattleSnapshot(BaseModel):
    """The battle state at the exact moment one move was used.

    Stamped on every move event by the parser, so per-move re-checks use the
    field, HP, status and items in effect then — never end-of-game values.
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
    """One ordered event from the battle log.

    The ordered actions stop the explanation from inventing causality (a
    Pokemon that fainted before acting did not act). State events carry
    ``effects``:

    - ``weather``/``terrain``: ``[name]`` (``""`` when it ended);
    - ``side_start``/``side_end``: ``[condition]`` for ``actor_player``'s side;
    - ``status``/``cure``: ``[status]`` for ``actor``;
    - ``item``: ``[item, change]``, change = ``"revealed"``/``"acquired"``/``"lost"``.
    """

    turn: int
    kind: BattleEventKind
    actor: str = ""  # species that acted / switched in
    actor_player: str = ""  # "p1" | "p2"
    slot: str = ""  # battle position letter ("a"/"b") for switch/move events
    move: str = ""
    targets: list[str] = Field(default_factory=list)
    hits: list[TargetHit] = Field(default_factory=list)
    """Side-qualified per-target outcome; empty without a log (then
    ``targets`` + side resolution are used)."""
    state: BattleSnapshot | None = None
    """Battle state at this move (log-parsed move events only)."""
    effects: list[str] = Field(default_factory=list)  # spread / super effective / ...
    results: list[str] = Field(default_factory=list)  # e.g. "Torkoal->43%", "Pyroar fainted"
    blocked: list[str] = Field(default_factory=list)
    """Species that blocked this move with a Protect-family move (kept apart
    from ``targets``)."""
    text: str = ""  # deterministic human-readable rendering


class BattleOutcome(BaseModel):
    """Result and ordered timeline from the battle log (log input only)."""

    winner_player: str | None = None  # "p1" | "p2"
    winner_name: str | None = None
    turns: int = 0
    kos: list[KOEvent] = Field(default_factory=list)
    events: list[BattleEvent] = Field(default_factory=list)  # ordered timeline
    highlights: list[str] = Field(default_factory=list)  # human-readable faint list
    forfeited_player: str | None = None  # "p1" | "p2" — set when THIS player quit early
    forfeited_name: str | None = None
    """Set when the game ended by forfeit, not by a team being defeated — the
    explanation must not narrate fights that never happened."""


class FieldWindow(BaseModel):
    """One field condition and its inclusive turn window. Two windows may
    share a turn (e.g. sun replaced by rain mid-turn)."""

    model_config = ConfigDict(frozen=True)

    name: str
    start_turn: int
    end_turn: int

    def covers(self, turn: int) -> bool:
        return self.start_turn <= turn <= self.end_turn

    def length(self) -> int:
        return self.end_turn - self.start_turn + 1


class FieldConditions(BaseModel):
    """Turn-level field summary (inclusive windows) for timeline notes and
    whole-game verdicts; per-move checks use ``BattleSnapshot`` instead.

    Names are in-game (``Sun``, ``Rain``, ``Electric``, ...), never protocol ids.
    """

    model_config = ConfigDict(frozen=True)

    tailwind: dict[str, list[list[int]]] = Field(default_factory=dict)  # player -> windows
    trick_room: list[list[int]] = Field(default_factory=list)
    weather: list[FieldWindow] = Field(default_factory=list)
    terrain: list[FieldWindow] = Field(default_factory=list)
    screens: dict[str, list[FieldWindow]] = Field(default_factory=dict)
    """player -> Reflect / Light Screen / Aurora Veil windows on that side."""
    final_statuses: dict[str, dict[str, str]] = Field(default_factory=dict)
    """player -> species -> status still active at the end (whole-game verdicts only)."""

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
    """Typed snapshot of a replay: the Pokemon involved and, with a log, the result."""

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
        """Species actually brought -> owning player.

        Team-preview-only Pokemon never played, so they are excluded; a side
        that never switched anyone in falls back to its full team.
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
    """``top_spreads[0]`` as data, to back-fill unrevealed nature/EVs in calcs."""
    threats_winrate: dict[str, float] = Field(default_factory=dict)
    source: str = ""  # which reg/rating-tier produced this (may be a fallback)

    def is_empty(self) -> bool:
        return not (self.top_abilities or self.top_items or self.top_moves)


class MetaContext(BaseModel):
    """Metagame context for the prompts: ``pokemon_stats`` = ideal (highest)
    tier, ``current_tier_stats`` = the match's own rating bracket."""

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
    """Field state for one calc, in domain vocabulary (the adapter maps it)."""

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
        """Unknown moves count as damaging, so the engine judges them."""
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
    """Defender's current HP percent for KO chances (None = full HP)."""

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
    """Who MOVES FIRST under the field (Tailwind, paralysis, Scarf, Trick
    Room); ``conditions`` lists the modifiers applied."""

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
    """Set when a forme change (e.g. Mega) had no engine stats, so base
    stats were used."""


class SmogonStrategy(BaseModel):
    """Narrative strategy knowledge for a species (from Smogon dex/usage)."""

    model_config = ConfigDict(frozen=True)

    species: str
    overview: str = ""
    common_sets: list[str] = Field(default_factory=list)
    common_teammates: list[str] = Field(default_factory=list)
    archetypes: list[Archetype] = Field(default_factory=list)
    # Where `overview` came from (shown in the UI's debug expander, never
    # sent to the LLM).
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
    """Target's HP before the hit; ``projected_ko_text`` uses it (None = full)."""
    projected_min_percent: float = 0.0
    projected_max_percent: float = 0.0
    projected_ko_text: str = ""
    actual_result: str = ""  # what the log recorded, e.g. "->43%" or "fainted"
    actual_hp_remaining_percent: float | None = None
    """HP LEFT after the hit (0.0 on a faint) — not damage dealt, so the model
    never has to derive one from the other (ADR-029). None if it protected."""
    description: str = ""  # e.g. "0 SpA Raichu Zap Cannon vs. 0 HP/0 SpD Basculegion: ..."


class OptimalMoveOption(BaseModel):
    """One candidate move's projected outcome, from moves confirmed this game,
    against that turn's real target and field."""

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
    """Whether this threat moves before the actor (None = speed unknown)."""


class DecisionKind(str, Enum):
    """Non-attacking plays a VGC player weighs every turn."""

    PROTECT = "protect"
    SWITCH = "switch"
    SPEED_CONTROL = "speed_control"


class DecisionOption(BaseModel):
    """An engine-verified alternative play, built only from confirmed moves
    and brought Pokemon, and only when a threat could KO the actor."""

    model_config = ConfigDict(frozen=True)

    kind: DecisionKind
    summary: str
    move: str = ""  # the Protect / speed-control move this option uses
    switch_to: str = ""
    against: str = ""  # the threat this option answers
    max_percent_taken: float | None = None
    """For a switch: the worst confirmed hit the switch-in would have taken."""


class TurnCheck(BaseModel):
    """Per-move ground-truth verification against the engine, under the
    field state at that move (the turn-by-turn feedback loop).

    ``best_alternatives``: top 4 confirmed damaging moves into every opposing
    active Pokemon (OHKO first, then damage). ``incoming_threats`` and
    ``decision_options``: was the actor in KO range, and would Protect, a
    switch or speed control have answered it.
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
    """Set when a forme change (e.g. Mega) had no engine stats, so base
    stats were used."""


class ProtectRead(BaseModel):
    """Deterministic classification of one Protect-family block, so the
    explanation narrates a precomputed judgment instead of deriving it."""

    model_config = ConfigDict(frozen=True)

    turn: int
    blocker: str
    blocker_player: str
    attacker: str
    attacker_player: str
    move: str
    is_spread_move: bool
    """The move also hit another target, so it needed no read."""
    value_denied: TurnDamageCheck
    """The blocked target's damage check — the stake the block avoided."""
    other_targets_hit: list[TurnDamageCheck] = Field(default_factory=list)
    """The same move's checks for targets that were not blocked."""
    is_genuine_read: bool
    """A blocked single-target move: the only case worth calling a "read"."""
    was_immediate_ko_threat: bool
    """``value_denied`` shows a same-turn OHKO chance (not a 2HKO)."""
    misallocated: bool
    """The blocker was safe while a teammate fainted the same turn."""
    teammate_fainted: str = ""
    """Species that fainted this same turn on the blocker's side, if any."""


class AgentToolInvocation(BaseModel):
    """One on-demand deterministic lookup an explanation agent made (agent
    backends only; ADR-028). Same ports as everywhere else, so equally
    trustworthy."""

    model_config = ConfigDict(frozen=True)

    tool: str
    """Tool name: ``damage_calc`` / ``chaos_meta_stats`` / ``smogon_strategy``."""
    arguments: dict[str, Any] = Field(default_factory=dict)
    ok: bool = True
    """Whether the call succeeded (the ``{ok:false,error}`` convention)."""
    summary: str = ""
    """Short, UI-facing preview of the result or error — not the full payload."""


class RegulationInfo(BaseModel):
    """The regulation an analysis was bound to, and what is legal in it."""

    model_config = ConfigDict(frozen=True)

    format_id: str
    label: str
    strict: bool
    """Pinned by the regulation controller: no data from any other regulation."""
    legal_species: list[str] = Field(default_factory=list)
    """Pokemon with usage data in this regulation's own tiers; empty when no
    data is loaded (legality unverified)."""


class AnalysisEvidence(BaseModel):
    """The ground-truth bundle every backend hands to its explanation stage,
    built once by ``GroundTruthAssembler`` so backends cannot drift apart."""

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
    regulation: RegulationInfo | None = None


class AnalysisResult(BaseModel):
    """Final DTO rendered by the UI."""

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
    regulation: RegulationInfo | None = None
    regulation_warnings: list[str] = Field(default_factory=list)
    """Regulation-guard findings shown to the user (e.g. an illegal Pokemon)."""


class ChatMessage(BaseModel):
    """A single conversational turn stored in memory."""

    model_config = ConfigDict(frozen=True)

    role: str  # "user" | "assistant" | "system"
    content: str
