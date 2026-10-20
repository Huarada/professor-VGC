"""Illusion (Zoroark, Zorua): undo the disguise before the handlers run.

A Pokemon with Illusion enters looking like the last Pokemon of its party:
the log names the DISGUISE (nickname and species) until the illusion breaks
with ``|replace|``, and keeps doing so when it never breaks. Read as written,
the Illusion user's moves, damage and faint land on the Pokemon it imitated,
which then "acts after fainting". The real identity is recoverable when:

- the illusion broke: ``|replace|pXa: Nick|Details`` — everything in that
  slot since its last switch-in was the Illusion user;
- it never broke but the log is impossible as written: the "same" Pokemon
  enters a slot while already active in the other one — the newcomer is the
  side's Illusion user (team preview names it).

Each unmasked stretch is rewritten to the real Pokemon, and its switch line
gains ``[illusion] <disguise>`` so the timeline can say so.
"""

from __future__ import annotations

import re
from typing import TypeVar

_V = TypeVar("_V")

_ILLUSION_USERS = ("Zoroark", "Zorua")
_ENTRIES = ("switch", "drag", "replace")


def _parts(line: str) -> list[str]:
    return line.split("|")


def _slot_and_nick(ref: str) -> tuple[str, str] | None:
    """``"p1a: Nick"`` -> ``("p1a", "Nick")``."""
    slot, sep, nick = ref.partition(": ")
    if not sep or len(slot) != 3 or not slot[:2].startswith("p"):
        return None
    return slot, nick.strip()


def _rename(line: str, slot: str, old: str, new: str) -> str:
    """Rewrite every ``slot: old`` reference (a whole field) to ``slot: new``."""
    return re.sub(rf"(?<=\|){re.escape(slot)}: {re.escape(old)}(?=\||$)", f"{slot}: {new}", line)


def _unmask(
    lines: list[str], start: int, end: int, slot: str, disguise: str, real: str, details: str
) -> None:
    """Lines ``start`` (the switch-in) .. ``end`` (exclusive) were ``real``."""
    switch = _parts(lines[start])
    disguised_as = switch[3].split(",")[0].strip()
    switch[2], switch[3] = f"{slot}: {real}", details
    lines[start] = "|".join(switch) + f"|[illusion] {disguised_as}"
    for i in range(start + 1, end):
        lines[i] = _rename(lines[i], slot, disguise, real)


def _swap_entries(tracked: dict[str, _V], first: str, second: str) -> None:
    held = [tracked.pop(first, None), tracked.pop(second, None)]
    for slot, value in zip((second, first), held):
        if value is not None:
            tracked[slot] = value


def _next_entry(lines: list[str], after: int, slot: str) -> int:
    """Index of the next switch/drag/replace into ``slot`` (or the log's end)."""
    for i in range(after + 1, len(lines)):
        p = _parts(lines[i])
        if len(p) > 2 and p[1] in _ENTRIES and p[2].startswith(f"{slot}:"):
            return i
    return len(lines)


def unmask_illusions(text: str) -> str:
    """``text`` with every recoverable Illusion attributed to the real Pokemon."""
    lines = text.splitlines()
    preview: dict[str, list[str]] = {}  # player -> team preview details
    nick_in: dict[str, str] = {}  # slot -> nickname shown there
    entered_at: dict[str, int] = {}  # slot -> index of its last switch-in
    for i in range(len(lines)):
        p = _parts(lines[i])
        if len(p) < 3:
            continue
        tag = p[1]
        if tag == "poke" and len(p) > 3:
            preview.setdefault(p[2], []).append(p[3])
            continue
        if tag == "swap" and len(p) > 3 and p[3].strip().isdigit():
            # Ally Switch: the two positions exchange what they track.
            first, second = p[2].partition(":")[0], p[2][:2] + "ab"[int(p[3]) % 2]
            _swap_entries(nick_in, first, second)
            _swap_entries(entered_at, first, second)
            continue
        ref = _slot_and_nick(p[2])
        if ref is None:
            continue
        slot, nick = ref
        if tag == "replace" and slot in entered_at and len(p) > 3:
            _unmask(lines, entered_at[slot], i, slot, nick_in[slot], nick, p[3])
            nick_in[slot] = nick
        elif tag in ("switch", "drag") and len(p) > 3:
            other = slot[:2] + ("b" if slot[2] == "a" else "a")
            illusion = [d for d in preview.get(slot[:2], []) if d.startswith(_ILLUSION_USERS)]
            if nick_in.get(other) == nick and len(illusion) == 1:
                end = _next_entry(lines, i, slot)
                # A later |replace| decides it instead: in this slot (with the real
                # nickname), or in the other one (then THAT was the Illusion user).
                broken_later = any(
                    j < len(lines) and _parts(lines[j])[1] == "replace"
                    for j in (end, _next_entry(lines, i, other))
                )
                if not broken_later:
                    real = illusion[0].split(",")[0].strip()
                    _unmask(lines, i, end, slot, nick, real, illusion[0])
                    nick = real
            nick_in[slot], entered_at[slot] = nick, i
        elif tag == "faint" and nick_in.get(slot) == nick:
            nick_in.pop(slot, None)
    return "\n".join(lines)
