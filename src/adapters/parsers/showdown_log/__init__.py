"""Showdown battle-log protocol reader (anti-corruption layer).

Turns ``|``-delimited battle-log text into a domain
:class:`~src.domain.models.GameState`: rosters, the ordered action timeline
and a per-move :class:`~src.domain.models.BattleSnapshot` of the battle
state. Protocol strings never leave this package.

Modules, by responsibility:

- ``protocol`` — wire-format vocabulary and fragment parsing (pure);
- ``roster`` — players, rosters, stable Pokemon identity;
- ``timeline`` — ordered events, the current move, faints, outcome;
- ``combatants`` — HP, status, held items, who is on the field;
- ``field_ledger`` — Tailwind, Trick Room, weather, terrain, screens;
- ``state`` — the aggregate of the above plus the per-move snapshot;
- ``handlers`` — one group of protocol-command handlers per concern;
- ``replay_frames`` — per-turn frames -> the battle panel's ``BattleReplay``;
- ``reader`` — line splitting and dispatch.
"""

from __future__ import annotations

from src.adapters.parsers.showdown_log.reader import read_log, read_replay

__all__ = ["read_log", "read_replay"]
