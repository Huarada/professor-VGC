"""Line-by-line reader for the Showdown battle-log protocol.

The anti-corruption layer between Showdown's ``|``-delimited protocol and the
domain: every protocol id (``SunnyDay``, ``move: Electric Terrain``,
``p1a: Nick``, ``45/100 par``, ...) is translated here and never leaves this
module. One handler per protocol command (see ``_LogReader._handlers``).

Besides rosters and the ordered action timeline, the reader keeps a running
ledger of the battle state (HP, status, held items, weather, terrain, screens,
who is on the field) and stamps a :class:`~src.domain.models.BattleSnapshot`
on every move event, so downstream re-checks see the state AT THAT MOVE.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable

from src.domain.exceptions import LogParsingError
from src.domain.models import (
    BattleEvent,
    BattleEventKind,
    BattleOutcome,
    BattleSnapshot,
    FieldConditions,
    FieldWindow,
    GameState,
    KOEvent,
    MonState,
    PokemonSet,
    SideState,
    TargetHit,
)

_REF = re.compile(r"^(?P<player>p\d)(?P<slot>[a-z]?): (?P<nick>.+)$")

# Protect-family names as they appear in "-activate|POKEMON|move: X" when that
# move fully blocked the current attack (protocol vocabulary, not move data).
_PROTECT_FAMILY = {
    "protect", "detect", "spikyshield", "banefulbunker", "kingsshield",
    "quickguard", "wideguard", "craftyshield", "obstruct", "silktrap",
    "burningbulwark", "matblock", "maxguard",
}

# The five stat stages that actually feed the damage/speed calc — accuracy
# and evasion changes are real Showdown -boost/-unboost events too, but
# neither @smogon/calc's boosts option nor this project's field model uses
# them, so they're deliberately not tracked.
_CALC_STATS = {"atk", "def", "spa", "spd", "spe"}

# Showdown weather/terrain/side-condition ids -> in-game (domain) names.
_WEATHER = {
    "sunnyday": "Sun", "raindance": "Rain", "sandstorm": "Sand", "hail": "Hail",
    "snow": "Snow", "snowscape": "Snow", "desolateland": "Harsh Sunshine",
    "primordialsea": "Heavy Rain", "deltastream": "Strong Winds",
}
_TERRAIN = {
    "electricterrain": "Electric", "grassyterrain": "Grassy",
    "psychicterrain": "Psychic", "mistyterrain": "Misty",
}
_SCREENS = {"reflect": "Reflect", "lightscreen": "Light Screen", "auroraveil": "Aurora Veil"}
_STATUS_NAMES = {
    "par": "paralysis", "brn": "burn", "psn": "poison", "tox": "bad poison",
    "slp": "sleep", "frz": "freeze",
}
# "[from] move: X" / "[from] ability: X" sources that GIVE the subject a new item.
_ITEM_GAINED_FROM = {
    "trick", "switcheroo", "bestow", "thief", "covet", "recycle",
    "harvest", "pickup", "magician", "pickpocket",
}

_Key = tuple[str, str]  # (player, roster identity)


def _id(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def _tag_value(parts: list[str], tag: str) -> str:
    """Value of a ``[tag] value`` suffix part (``""`` when absent)."""
    prefix = f"[{tag}]"
    for part in parts:
        part = part.strip()
        if part.startswith(prefix):
            return part[len(prefix):].strip()
    return ""


def _parse_hp(text: str) -> tuple[float | None, str]:
    """``"45/100 par"`` -> ``(45.0, "par")``; ``"0 fnt"`` -> ``(0.0, "fnt")``."""
    pieces = text.strip().split()
    if not pieces:
        return None, ""
    condition = pieces[1] if len(pieces) > 1 else ""
    if "/" not in pieces[0]:
        try:
            return float(pieces[0]), condition
        except ValueError:
            return None, condition
    current, _, maximum = pieces[0].partition("/")
    try:
        cur, top = float(current), float(maximum)
    except ValueError:
        return None, condition
    if top <= 0:
        return None, condition
    return round(cur / top * 100, 1), condition


def split_details(details: str) -> tuple[str, int]:
    """Split ``"Garchomp, L50, M"`` into ``("Garchomp", 50)``."""
    parts = [p.strip() for p in details.split(",")]
    level = 50
    for part in parts[1:]:
        if part.upper().startswith("L") and part[1:].isdigit():
            level = int(part[1:])
    return parts[0], level


@dataclass
class _MonDraft:
    """Mutable builder for one roster entry; frozen into a PokemonSet at the end."""

    species: str
    level: int = 50
    ability: str | None = None
    item: str | None = None  # ORIGINAL held item, once revealed
    moves: list[str] = field(default_factory=list)
    battle_formes: list[str] = field(default_factory=list)

    def build(self) -> PokemonSet:
        return PokemonSet(
            species=self.species, level=self.level, ability=self.ability,
            item=self.item, moves=list(self.moves), battle_formes=list(self.battle_formes),
        )


@dataclass
class _OpenWindow:
    name: str
    start: int


class _LogReader:
    """Single-use state machine: feed it a whole log via :meth:`read`."""

    def __init__(self) -> None:
        self.players: dict[str, dict[str, _MonDraft]] = {}
        self.player_names: dict[str, str] = {}
        self.brought: dict[str, list[str]] = {}
        self.slot_species: dict[str, str] = {}  # "p1a" -> roster key
        self.events: list[BattleEvent] = []
        self.kos: list[KOEvent] = []
        self.fainted_in_move: set[tuple[int, str]] = set()
        self.current_move: BattleEvent | None = None
        self.winner_name: str | None = None
        self.forfeited_name: str | None = None
        self.turn = 0
        # running battle-state ledger
        self.hp: dict[_Key, float] = {}
        self.status: dict[_Key, str] = {}
        self.held: dict[_Key, str] = {}  # current item when determined ("" = none)
        self.gained_item: set[_Key] = set()
        self.revealed: set[tuple[str, str, str]] = set()
        self.on_field: dict[str, _Key] = {}  # "p1a" -> key
        self.fainted: dict[str, list[str]] = {}
        self.helped: set[tuple[int, str, str]] = set()
        # turn windows for the per-turn FieldConditions summary
        self.tailwind: dict[str, list[list[int]]] = {}
        self.tw_open: dict[str, int] = {}
        self.trick_room: list[list[int]] = []
        self.tr_open: int | None = None
        self.weather_windows: list[FieldWindow] = []
        self.weather_open: _OpenWindow | None = None
        self.terrain_windows: list[FieldWindow] = []
        self.terrain_open: _OpenWindow | None = None
        self.screen_windows: dict[str, list[FieldWindow]] = {}
        self.screens_open: dict[str, dict[str, int]] = {}
        self._handlers: dict[str, Callable[[list[str]], None]] = {
            "turn": self._on_turn,
            "player": self._on_player,
            "poke": self._on_poke,
            "switch": self._on_switch,
            "drag": self._on_switch,
            "detailschange": self._on_detailschange,
            "-mega": self._on_mega,
            "move": self._on_move,
            "-damage": self._on_damage,
            "-heal": self._on_heal,
            "-sethp": self._on_heal,
            "-activate": self._on_activate,
            "-supereffective": lambda parts: self._add_effect("super effective"),
            "-resisted": lambda parts: self._add_effect("resisted"),
            "-immune": lambda parts: self._add_effect("immune"),
            "-crit": lambda parts: self._add_effect("crit"),
            "-miss": lambda parts: self._add_effect("missed"),
            "faint": self._on_faint,
            "-sidestart": self._on_sidestart,
            "-sideend": self._on_sideend,
            "-fieldstart": self._on_fieldstart,
            "-fieldend": self._on_fieldend,
            "-weather": self._on_weather,
            "-status": self._on_status,
            "-curestatus": self._on_curestatus,
            "-cureteam": self._on_cureteam,
            "-ability": self._on_ability,
            "-boost": self._on_boost,
            "-unboost": self._on_boost,
            "-item": self._on_item,
            "-enditem": self._on_enditem,
            "-singleturn": self._on_singleturn,
            "-message": self._on_message,
            "win": self._on_win,
        }

    # -- entry point ------------------------------------------------------ #

    def read(self, text: str) -> GameState:
        for line in text.splitlines():
            if not line.startswith("|"):
                continue
            parts = line.split("|")
            if len(parts) < 2:
                continue
            handler = self._handlers.get(parts[1])
            if handler is not None:
                handler(parts)
        if not self.players:
            preview = text.strip().splitlines()[:1]
            hint = f" First line seen: {preview[0]!r}." if preview else ""
            raise LogParsingError(
                "No player or Pokemon lines were found in the battle log. A Showdown "
                "log is expected to contain '|player|p1|...', '|poke|p1|Species, L50|' "
                f"and/or '|switch|p1a: Nick|Species, L50|...' lines.{hint}"
            )
        return self._build_state()

    # -- identity helpers ------------------------------------------------- #

    @staticmethod
    def _split_ref(ref: str) -> tuple[str, str, str] | None:
        """Split ``"p1a: Torkoal"`` into ``("p1", "a", "Torkoal")``."""
        m = _REF.match(ref.strip())
        if not m:
            return None
        return m.group("player"), m.group("slot"), m.group("nick").strip()

    def _resolve(self, ref: str) -> _Key | None:
        """Roster identity for a protocol reference (slot first, then nick)."""
        split = self._split_ref(ref)
        if split is None:
            return None
        player, slot, nick = split
        return player, self.slot_species.get(player + slot) or nick

    def _register(self, player: str, key: str, level: int = 50) -> _MonDraft:
        bucket = self.players.setdefault(player, {})
        return bucket.setdefault(key, _MonDraft(species=key, level=level))

    def _side_of_ref(self, ref: str) -> str:
        split = self._split_ref(ref)
        return split[0] if split else (ref.split(":")[0].strip() or "p?")

    def _emit(self, kind: BattleEventKind, key: _Key | None, text: str, effects: list[str]) -> None:
        player, actor = key if key else ("", "")
        self.events.append(
            BattleEvent(
                turn=self.turn, kind=kind, actor=actor, actor_player=player,
                effects=effects, text=text,
            )
        )

    def _add_effect(self, effect: str) -> None:
        if self.current_move is not None and effect not in self.current_move.effects:
            self.current_move.effects.append(effect)

    # -- roster / turn ---------------------------------------------------- #

    def _on_turn(self, parts: list[str]) -> None:
        try:
            self.turn = int(parts[2])
        except (IndexError, ValueError):
            pass
        self.current_move = None

    def _on_player(self, parts: list[str]) -> None:
        if len(parts) > 2:
            self.players.setdefault(parts[2], {})
            if len(parts) > 3 and parts[3].strip():
                self.player_names[parts[2]] = parts[3].strip()

    def _on_poke(self, parts: list[str]) -> None:
        if len(parts) > 3:
            species, level = split_details(parts[3])
            self._register(parts[2], species, level)

    def _on_switch(self, parts: list[str]) -> None:
        if len(parts) <= 3:
            return
        split = self._split_ref(parts[2])
        if split is None:
            return
        player, slot, nick = split
        species, level = split_details(parts[3])
        # Showdown's "nick" (the ref before the colon) is the STABLE identity
        # for this battle slot; "details" is just the CURRENT appearance and
        # changes after a Mega Evolution / in-battle forme change. If this
        # Pokemon was already registered under its nick (e.g. it mega evolved,
        # switched out, and is now switching back in as "<Species>-Mega"),
        # reuse that same entry instead of registering a fresh one — otherwise
        # the new entry starts with an empty moveset and later gets treated as
        # a different Pokemon than the one whose moves were already recorded.
        key = nick if nick in self.players.get(player, {}) else species
        self._register(player, key, level)
        self._record_forme(player, key, species)
        brought = self.brought.setdefault(player, [])
        if key not in brought:
            brought.append(key)
        self.slot_species[player + slot] = key
        self.on_field[player + slot] = (player, key)
        if len(parts) > 4:
            hp, condition = _parse_hp(parts[4])
            if hp is not None:
                self.hp[(player, key)] = hp
            if condition in _STATUS_NAMES:
                self.status[(player, key)] = condition
        self.current_move = None
        self.events.append(
            BattleEvent(
                turn=self.turn, kind="switch", actor=key, actor_player=player, slot=slot,
                text=f"{player} sent out {species}.",
            )
        )

    def _record_forme(self, player: str, key: str, observed: str) -> None:
        """Note that ``key`` was seen in-battle as a DIFFERENT species string
        (Mega Evolution / other forme change) than its registered identity."""
        mon = self.players.get(player, {}).get(key)
        if mon is not None and observed != mon.species and observed not in mon.battle_formes:
            mon.battle_formes.append(observed)

    def _on_detailschange(self, parts: list[str]) -> None:
        # Mega Evolution / other in-battle forme change (e.g. Zygarde,
        # Mimikyu-Busted). The identity doesn't change — only the appearance —
        # but it is recorded so calcs can use the real forme's stats, and it is
        # a visible timeline event: it can be the single most strategically
        # important fact in the game (e.g. Mega Gengar gaining Shadow Tag).
        if len(parts) <= 3:
            return
        key = self._resolve(parts[2])
        if key is None:
            return
        species, _level = split_details(parts[3])
        self._record_forme(key[0], key[1], species)
        self._emit("forme_change", key, f"{key[0]} {key[1]} transformed into {species}.", [species])

    def _on_mega(self, parts: list[str]) -> None:
        # "|-mega|p1a: Charizard|Charizard|Charizardite Y" reveals the stone.
        if len(parts) > 4 and parts[4].strip():
            key = self._resolve(parts[2])
            if key is not None:
                self._item_revealed(key, parts[4].strip())

    # -- moves and their results ----------------------------------------- #

    def _snapshot(self) -> BattleSnapshot:
        active: dict[str, list[str]] = {}
        for slot_ref in sorted(self.on_field):
            player, key = self.on_field[slot_ref]
            active.setdefault(player, []).append(key)
        mons: dict[str, MonState] = {}
        for player, bucket in self.players.items():
            for key in bucket:
                k = (player, key)
                mons[BattleSnapshot.key(player, key)] = MonState(
                    hp_percent=self.hp.get(k), status=self.status.get(k, ""),
                    item=self.held.get(k),
                )
        return BattleSnapshot(
            weather=self.weather_open.name if self.weather_open else "",
            terrain=self.terrain_open.name if self.terrain_open else "",
            screens={p: list(open_) for p, open_ in self.screens_open.items() if open_},
            active=active,
            fainted={p: list(names) for p, names in self.fainted.items()},
            mons=mons,
        )

    def _on_move(self, parts: list[str]) -> None:
        if len(parts) <= 3:
            return
        split = self._split_ref(parts[2])
        if split is None:
            self.current_move = None
            return
        player, slot, nick = split
        move = parts[3].strip()
        bucket = self.players.get(player, {})
        brought = self.brought.get(player, [])
        species = (
            nick if nick in bucket
            else self.slot_species.get(player + slot) or (brought[-1] if brought else None)
        )
        mon = bucket.get(species) if species else None
        if mon is not None and move and move not in mon.moves:
            mon.moves.append(move)
        actor = species or nick
        effects: list[str] = []
        if any("[spread]" in p for p in parts[4:]):
            effects.append("spread")
        if (self.turn, player, actor) in self.helped:
            effects.append("helping hand")
        self.current_move = BattleEvent(
            turn=self.turn, kind="move", actor=actor, actor_player=player, slot=slot,
            move=move, effects=effects, state=self._snapshot(),
        )
        self.events.append(self.current_move)

    def _record_hit(self, key: _Key, hp_after: float | None, blocked_by: str = "") -> None:
        move = self.current_move
        if move is None:
            return
        player, species = key
        hit = TargetHit(player=player, species=species, hp_after=hp_after, blocked_by=blocked_by)
        for i, existing in enumerate(move.hits):
            if (existing.player, existing.species) == key:
                move.hits[i] = hit  # multi-hit move: keep the final HP
                return
        move.hits.append(hit)

    def _on_damage(self, parts: list[str]) -> None:
        if len(parts) <= 3:
            return
        key = self._resolve(parts[2])
        if key is None:
            return
        hp, condition = _parse_hp(parts[3])
        fainted = condition == "fnt"
        if hp is not None:
            self.hp[key] = 0.0 if fainted else hp
        source = _tag_value(parts[4:], "from")
        if source:
            if source.lower().startswith("item:"):
                holder = self._resolve(_tag_value(parts[4:], "of")) or key
                self._item_revealed(holder, source.split(":", 1)[1].strip())
            return  # residual / item / ability damage, not the move's direct hit
        if self.current_move is None:
            return
        _player, species = key
        if species not in self.current_move.targets:
            self.current_move.targets.append(species)
        if fainted:
            self.current_move.results.append(f"{species} fainted")
            self.fainted_in_move.add((self.turn, species))
            self._record_hit(key, 0.0)
        else:
            pct = parts[3].strip().split("/")[0]
            self.current_move.results.append(f"{species}->{pct}%")
            self._record_hit(key, hp)

    def _on_heal(self, parts: list[str]) -> None:
        if len(parts) <= 3:
            return
        key = self._resolve(parts[2])
        if key is None:
            return
        hp, _condition = _parse_hp(parts[3])
        if hp is not None:
            self.hp[key] = hp
        source = _tag_value(parts[4:], "from")
        if source.lower().startswith("item:"):
            self._item_revealed(key, source.split(":", 1)[1].strip())

    def _on_activate(self, parts: list[str]) -> None:
        if len(parts) <= 3:
            return
        reason = parts[3].strip()
        key = self._resolve(parts[2])
        if key is None:
            return
        if reason.lower().startswith("item:"):
            self._item_revealed(key, reason.split(":", 1)[1].strip())
            return
        # A Pokemon blocked THIS move with a Protect-family move. The blocker
        # never gets a "-damage" line (so it would otherwise silently vanish
        # from the event), which is exactly the causal link a risk/reward read
        # of the turn needs: "this move WAS aimed at them, and they read it."
        if self.current_move is None or not reason.lower().startswith("move:"):
            return
        block_move = reason.split(":", 1)[1].strip()
        if _id(block_move) not in _PROTECT_FAMILY:
            return
        _player, species = key
        if species not in self.current_move.blocked:
            self.current_move.blocked.append(species)
        self.current_move.results.append(f"{species} blocked ({block_move})")
        self._record_hit(key, self.hp.get(key), blocked_by=block_move)

    def _on_faint(self, parts: list[str]) -> None:
        if len(parts) <= 2:
            return
        split = self._split_ref(parts[2])
        key = self._resolve(parts[2])
        if split is None or key is None:
            return
        player, species = key
        self.hp[key] = 0.0
        self.on_field.pop(player + split[1], None)
        self.fainted.setdefault(player, []).append(species)
        self.kos.append(KOEvent(turn=self.turn, fainted=species, player=player))
        if (self.turn, species) not in self.fainted_in_move:
            self._emit("faint", key, f"{species} ({player}) fainted.", [])

    # -- field ------------------------------------------------------------ #

    def _on_sidestart(self, parts: list[str]) -> None:
        if len(parts) <= 3:
            return
        player = self._side_of_ref(parts[2])
        condition = parts[3].replace("move:", "").strip()
        if _id(condition) == "tailwind":
            self.tw_open.setdefault(player, self.turn)
        elif _id(condition) in _SCREENS:
            condition = _SCREENS[_id(condition)]
            self.screens_open.setdefault(player, {}).setdefault(condition, self.turn)
        else:
            return
        self._emit("side_start", (player, ""), f"{player}'s side: {condition} started.", [condition])

    def _on_sideend(self, parts: list[str]) -> None:
        if len(parts) <= 3:
            return
        player = self._side_of_ref(parts[2])
        condition = parts[3].replace("move:", "").strip()
        if _id(condition) == "tailwind":
            start = self.tw_open.pop(player, self.turn)
            self.tailwind.setdefault(player, []).append([start, self.turn])
        elif _id(condition) in _SCREENS:
            condition = _SCREENS[_id(condition)]
            start = self.screens_open.get(player, {}).pop(condition, self.turn)
            self.screen_windows.setdefault(player, []).append(
                FieldWindow(name=condition, start_turn=start, end_turn=self.turn)
            )
        else:
            return
        self._emit("side_end", (player, ""), f"{player}'s side: {condition} ended.", [condition])

    def _close_terrain(self) -> None:
        if self.terrain_open is not None:
            self.terrain_windows.append(
                FieldWindow(
                    name=self.terrain_open.name, start_turn=self.terrain_open.start,
                    end_turn=self.turn,
                )
            )
            self.terrain_open = None

    def _on_fieldstart(self, parts: list[str]) -> None:
        if len(parts) <= 2:
            return
        condition = parts[2].replace("move:", "").strip()
        if _id(condition) == "trickroom":
            if self.tr_open is None:
                self.tr_open = self.turn
            return
        terrain = _TERRAIN.get(_id(condition))
        if terrain is None:
            return
        self._close_terrain()  # a new terrain replaces the old one
        self.terrain_open = _OpenWindow(terrain, self.turn)
        self._emit("terrain", None, f"{terrain} Terrain was set{self._source_suffix(parts)}.", [terrain])

    def _on_fieldend(self, parts: list[str]) -> None:
        if len(parts) <= 2:
            return
        condition = parts[2].replace("move:", "").strip()
        if _id(condition) == "trickroom":
            if self.tr_open is not None:
                self.trick_room.append([self.tr_open, self.turn])
                self.tr_open = None
            return
        terrain = _TERRAIN.get(_id(condition))
        if terrain is None or self.terrain_open is None or self.terrain_open.name != terrain:
            return
        self._close_terrain()
        self._emit("terrain", None, f"{terrain} Terrain ended.", [""])

    def _source_suffix(self, parts: list[str]) -> str:
        """`` (from p1 Torkoal's Drought)`` for an ability-set field condition."""
        source = _tag_value(parts[3:], "from")
        holder = self._resolve(_tag_value(parts[3:], "of"))
        if source.lower().startswith("ability:") and holder is not None:
            return f" (from {holder[0]} {holder[1]}'s {source.split(':', 1)[1].strip()})"
        return ""

    def _on_weather(self, parts: list[str]) -> None:
        if len(parts) <= 2 or any(p.strip() == "[upkeep]" for p in parts[3:]):
            return
        weather = _WEATHER.get(_id(parts[2]), "")
        if not weather and _id(parts[2]) not in ("", "none"):
            weather = parts[2].strip()  # an id this table doesn't know yet: keep it visible
        current = self.weather_open.name if self.weather_open else ""
        if weather == current:
            return
        if self.weather_open is not None:
            self.weather_windows.append(
                FieldWindow(
                    name=self.weather_open.name, start_turn=self.weather_open.start,
                    end_turn=self.turn,
                )
            )
            self.weather_open = None
        if weather:
            self.weather_open = _OpenWindow(weather, self.turn)
            text = f"Weather became {weather}{self._source_suffix(parts)}."
        else:
            text = "The weather ended."
        self._emit("weather", None, text, [weather])

    # -- status / items / abilities / boosts ----------------------------- #

    def _on_status(self, parts: list[str]) -> None:
        if len(parts) <= 3:
            return
        key = self._resolve(parts[2])
        status = parts[3].strip()
        if key is None or status not in _STATUS_NAMES:
            return
        self.status[key] = status
        self._emit(
            "status", key, f"{key[0]} {key[1]} was afflicted with {_STATUS_NAMES[status]}.",
            [status],
        )

    def _cure(self, key: _Key) -> None:
        status = self.status.pop(key, "")
        if status:
            self._emit(
                "cure", key, f"{key[0]} {key[1]} was cured of {_STATUS_NAMES[status]}.", [status]
            )

    def _on_curestatus(self, parts: list[str]) -> None:
        if len(parts) > 2:
            key = self._resolve(parts[2])
            if key is not None:
                self._cure(key)

    def _on_cureteam(self, parts: list[str]) -> None:
        if len(parts) > 2:
            player = self._side_of_ref(parts[2])
            for key in [k for k in self.status if k[0] == player]:
                self._cure(key)

    def _on_ability(self, parts: list[str]) -> None:
        # An ability that actively TRIGGERED (Intimidate, Trace, Download, ...)
        # — Showdown only announces observable activations. Recorded as a
        # timeline event AND on the roster entry so later calcs use the real,
        # CONFIRMED ability instead of a Chaos-guessed one. First reveal wins:
        # a later "-ability" for the same mon (e.g. Trace copying something)
        # is a temporary ability and must not overwrite the original.
        if len(parts) <= 3:
            return
        key = self._resolve(parts[2])
        if key is None:
            return
        ability = parts[3].strip()
        mon = self._register(*key)
        if not mon.ability:
            mon.ability = ability
        self._emit("ability", key, f"{key[0]} {key[1]}'s ability {ability} activated.", [ability])

    def _on_boost(self, parts: list[str]) -> None:
        # A real stat stage change (Intimidate, Swords Dance, a self-drop, ...)
        # — consumed by TurnReplaySimulator's running stage ledger.
        if len(parts) <= 4 or parts[3].strip() not in _CALC_STATS:
            return
        key = self._resolve(parts[2])
        if key is None:
            return
        try:
            amount = int(parts[4].strip())
        except ValueError:
            return
        delta = amount if parts[1] == "-boost" else -amount
        if not delta:
            return
        self._register(*key)
        stat = parts[3].strip()
        stages = abs(delta)
        direction = "rose" if delta > 0 else "fell"
        self._emit(
            "boost", key,
            f"{key[0]} {key[1]}'s {stat} {direction} by {stages} stage{'s' if stages != 1 else ''}.",
            [stat, str(delta)],
        )

    def _item_revealed(self, key: _Key, item: str) -> None:
        mon = self._register(*key)
        if mon.item is None and key not in self.gained_item:
            mon.item = item
        self.held[key] = item
        marker = (key[0], key[1], item)
        if marker not in self.revealed:
            self.revealed.add(marker)
            self._emit("item", key, f"{key[0]} {key[1]}'s item revealed: {item}.", [item, "revealed"])

    def _on_item(self, parts: list[str]) -> None:
        if len(parts) <= 3:
            return
        key = self._resolve(parts[2])
        if key is None:
            return
        item = parts[3].strip()
        source = _tag_value(parts[4:], "from")
        if _id(source.split(":", 1)[-1]) in _ITEM_GAINED_FROM:
            self.gained_item.add(key)
            self.held[key] = item
            self._emit("item", key, f"{key[0]} {key[1]} obtained {item}.", [item, "acquired"])
            return
        self._item_revealed(key, item)

    def _on_enditem(self, parts: list[str]) -> None:
        if len(parts) <= 3:
            return
        key = self._resolve(parts[2])
        if key is None:
            return
        item = parts[3].strip()
        mon = self._register(*key)
        if mon.item is None and key not in self.gained_item:
            mon.item = item  # it was the original item all along
        self.held[key] = ""
        self._emit("item", key, f"{key[0]} {key[1]} lost its {item}.", [item, "lost"])

    def _on_singleturn(self, parts: list[str]) -> None:
        # "|-singleturn|p1b: Garchomp|move: Helping Hand|[of] p1a: Amoonguss":
        # the TARGET's move later this turn is boosted 1.5x.
        if len(parts) > 3 and _id(parts[3].replace("move:", "")) == "helpinghand":
            key = self._resolve(parts[2])
            if key is not None:
                self.helped.add((self.turn, key[0], key[1]))

    def _on_message(self, parts: list[str]) -> None:
        # e.g. "|-message|42 s forfeited." — the game ended WITHOUT the
        # forfeiting side's team being defeated in play.
        if len(parts) > 2 and parts[2].strip().endswith("forfeited."):
            self.forfeited_name = parts[2].strip()[: -len(" forfeited.")].strip()

    def _on_win(self, parts: list[str]) -> None:
        if len(parts) > 2:
            self.winner_name = parts[2].strip()

    # -- result ----------------------------------------------------------- #

    def _close_open_windows(self) -> None:
        for player, start in self.tw_open.items():
            self.tailwind.setdefault(player, []).append([start, self.turn])
        if self.tr_open is not None:
            self.trick_room.append([self.tr_open, self.turn])
        if self.weather_open is not None:
            self.weather_windows.append(
                FieldWindow(
                    name=self.weather_open.name, start_turn=self.weather_open.start,
                    end_turn=self.turn,
                )
            )
        self._close_terrain()
        for player, open_ in self.screens_open.items():
            for name, start in open_.items():
                self.screen_windows.setdefault(player, []).append(
                    FieldWindow(name=name, start_turn=start, end_turn=self.turn)
                )

    def _build_state(self) -> GameState:
        self._close_open_windows()
        for event in self.events:
            if event.kind == "move":
                event.text = self._render_move(event)
        final_statuses: dict[str, dict[str, str]] = {}
        for (player, key), status in self.status.items():
            final_statuses.setdefault(player, {})[key] = status
        sides = [
            SideState(
                player=player,
                player_name=self.player_names.get(player, ""),
                team=[draft.build() for draft in mons.values()],
                active=self.brought.get(player, []),
            )
            for player, mons in self.players.items()
        ]
        return GameState(
            turn=self.turn,
            sides=sides,
            outcome=self._build_outcome(),
            field=FieldConditions(
                tailwind=self.tailwind, trick_room=self.trick_room,
                weather=self.weather_windows, terrain=self.terrain_windows,
                screens=self.screen_windows, final_statuses=final_statuses,
            ),
        )

    @staticmethod
    def _render_move(event: BattleEvent) -> str:
        line = f"{event.actor_player} {event.actor} used {event.move}"
        if event.effects:
            line += f" ({', '.join(event.effects)})"
        if event.results:
            line += " — " + "; ".join(event.results)
        return line

    def _player_named(self, name: str | None) -> str | None:
        if not name:
            return None
        return next((p for p, n in self.player_names.items() if n == name), None)

    def _build_outcome(self) -> BattleOutcome:
        winner_player = self._player_named(self.winner_name)
        forfeited_player = self._player_named(self.forfeited_name)
        highlights = [
            f"Turn {ko.turn}: {ko.fainted} ({self.player_names.get(ko.player, ko.player)}) fainted."
            for ko in self.kos
        ]
        if self.forfeited_name:
            highlights.append(
                f"{self.forfeited_name} ({forfeited_player or '?'}) FORFEITED — the game did "
                "NOT end by their team being defeated in play."
            )
        if self.winner_name:
            highlights.append(f"Winner: {self.winner_name} ({winner_player or '?'}).")
        return BattleOutcome(
            winner_player=winner_player,
            winner_name=self.winner_name,
            turns=self.turn,
            kos=self.kos,
            events=self.events,
            highlights=highlights,
            forfeited_player=forfeited_player,
            forfeited_name=self.forfeited_name,
        )


def read_log(text: str) -> GameState:
    """Parse raw Showdown battle-log text into a :class:`GameState`."""
    return _LogReader().read(text)
