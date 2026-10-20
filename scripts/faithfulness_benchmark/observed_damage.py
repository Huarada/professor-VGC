"""Observed damage straight from a battle log — the benchmark's external truth.

Deliberately INDEPENDENT of the product: this module does not import the
pipeline's parser (``src.adapters.parsers``) or anything it produces. It reads
the raw Showdown protocol lines itself, so the reference it yields cannot
inherit a bug from the system being evaluated — the circularity the original
faithfulness benchmark had (claims were checked against the pipeline's own
calc output, which is exactly what the pipeline told the LLM).

For every direct hit of a move it records the target's HP right before and
right after, as Showdown displays it (percent of max HP). Observed damage is
``hp_before - hp_after``. Measurement caveats, all reported per hit so the
consumer can filter on them rather than guess:

- ``crit``: the hit was a critical hit (no calc range models a random crit);
- ``hit_count > 1``: a multi-hit move (``hp_after`` is after the last hit);
- ``survived``: Focus Sash / Focus Band / Sturdy left the target on 1 HP, so
  the observed value is a LOWER bound too (the move was lethal);
- ``fainted``: the target fainted, so the observed value is only a LOWER bound
  of the damage the move would have dealt (overkill is never shown);
- spectator logs round HP to whole percents, so every reading carries about
  ±1 percentage point of rounding.

Illusion (Zoroark) is resolved here too, independently of the product: a
``|replace|`` line reveals that the Pokemon in that slot was the Illusion user
since it switched in, and the "same" Pokemon entering a second slot while
already on the field is the side's Illusion user from team preview.
"""

from __future__ import annotations

from dataclasses import dataclass, field

HP_ROUNDING_PP = 1.0
"""Rounding of one displayed HP percent; a difference of two readings is ±2pp."""


@dataclass
class ObservedHit:
    turn: int
    attacker: str
    attacker_player: str
    move: str
    defender: str
    defender_player: str
    hp_before: float
    hp_after: float
    crit: bool = False
    hit_count: int = 1
    spread: bool = False
    fainted: bool = False
    survived: bool = False

    @property
    def lower_bound(self) -> bool:
        """The observed damage is capped (KO, or a Sash/Sturdy survival): the move
        was at least that strong."""
        return self.fainted or self.survived

    @property
    def damage(self) -> float:
        """Observed damage in percentage points of max HP (a lower bound on a KO)."""
        return round(self.hp_before - self.hp_after, 1)

    @property
    def clean(self) -> bool:
        """A single, non-critical hit — directly comparable to a calc range."""
        return not self.crit and self.hit_count == 1

    @property
    def projectable(self) -> bool:
        """Comparable to a calc range: not a crit. A multi-hit move is projected
        for the number of hits the log shows landing (``CalcRequest.hits``)."""
        return not self.crit


@dataclass
class _Mon:
    player: str
    species: str
    hp: float = 100.0


@dataclass
class _PendingMove:
    turn: int
    attacker: _Mon
    move: str
    spread: bool
    hits: dict[tuple[str, str], ObservedHit] = field(default_factory=dict)

    def start_hit(self, target: _Mon) -> ObservedHit:
        """Open a hit on ``target`` with no damage lines applied yet."""
        hit = ObservedHit(
            turn=self.turn, attacker=self.attacker.species,
            attacker_player=self.attacker.player, move=self.move,
            defender=target.species, defender_player=target.player,
            hp_before=target.hp, hp_after=target.hp, spread=self.spread, hit_count=0,
        )
        self.hits[(target.player, target.species)] = hit
        return hit


def _hp_percent(field_text: str) -> float | None:
    token = field_text.strip().split(" ")[0]
    if token == "0":
        return 0.0
    current, sep, maximum = token.partition("/")
    if not sep:
        return None
    maximum = maximum.rstrip("gyr")  # "50/100g": Showdown's HP-bar colour suffix
    try:
        return round(float(current) / float(maximum) * 100, 1)
    except (ValueError, ZeroDivisionError):
        return None


def _slot(ref: str) -> str:
    """``"p1a: Nick"`` -> ``"p1a"``."""
    return ref.split(":", 1)[0].strip()


def _nick_key(ref: str) -> str:
    """``"p1a: Nick"`` -> ``"p1:Nick"`` (stable across slots for one Pokemon)."""
    slot, _, nick = ref.partition(":")
    return f"{slot.strip()[:2]}:{nick.strip()}"


def _has_from(parts: list[str]) -> bool:
    return any(p.strip().startswith("[from]") for p in parts)


_ILLUSION_USERS = ("Zoroark", "Zorua")


def _replaced_next(lines: list[str], after: int, slot: str) -> bool:
    """Whether the next entry into ``slot`` after line ``after`` is a ``|replace|``."""
    for line in lines[after + 1:]:
        parts = line.split("|")
        if len(parts) > 2 and parts[1] in ("switch", "drag", "replace") and parts[2].startswith(f"{slot}:"):
            return parts[1] == "replace"
    return False


def _reattribute(hits: list[ObservedHit], disguise: _Mon, real: _Mon) -> None:
    """Hits recorded on/by ``disguise`` were really ``real``'s."""
    for hit in hits:
        if (hit.attacker_player, hit.attacker) == (disguise.player, disguise.species):
            hit.attacker = real.species
        if (hit.defender_player, hit.defender) == (disguise.player, disguise.species):
            hit.defender = real.species


def extract_observed_hits(log: str) -> list[ObservedHit]:
    """Every direct move hit in ``log``, in order, with the target's HP around it."""
    mons: dict[str, _Mon] = {}  # nick key -> mon (identity = species at first switch-in)
    on_slot: dict[str, _Mon] = {}  # "p1a" -> mon currently there
    hits: list[ObservedHit] = []
    preview: dict[str, list[str]] = {}  # player -> team preview species
    entry: dict[str, tuple[int, float]] = {}  # slot -> (hits recorded before, prior HP)
    turn = 0
    pending: _PendingMove | None = None

    def close() -> None:
        nonlocal pending
        if pending is not None:
            hits.extend(pending.hits.values())
        pending = None

    lines = log.splitlines()
    for index, line in enumerate(lines):
        parts = line.split("|")
        if len(parts) < 2:
            continue
        tag = parts[1]
        if tag == "turn":
            close()
            turn = int(parts[2]) if len(parts) > 2 and parts[2].strip().isdigit() else turn
        elif tag == "swap" and len(parts) > 3 and parts[3].strip().isdigit():
            # Ally Switch: this Pokemon and its ally exchange battle positions.
            origin = _slot(parts[2])
            target = origin[:2] + "ab"[int(parts[3]) % 2]
            on_slot[origin], on_slot[target] = on_slot.get(target), on_slot.get(origin)
            on_slot = {slot: mon for slot, mon in on_slot.items() if mon is not None}
        elif tag == "poke" and len(parts) > 3:
            preview.setdefault(parts[2], []).append(parts[3].split(",")[0].strip())
        elif tag == "replace" and len(parts) > 3:
            # Illusion broke: the slot held this Pokemon since it switched in.
            slot = _slot(parts[2])
            disguise = on_slot.get(slot)
            key = _nick_key(parts[2])
            real = mons.setdefault(key, _Mon(player=key[:2], species=parts[3].split(",")[0].strip()))
            if disguise is not None and disguise is not real:
                start, prior_hp = entry.get(slot, (len(hits), disguise.hp))
                real.hp, disguise.hp = disguise.hp, prior_hp
                _reattribute(hits[start:], disguise, real)
                if pending is not None:
                    _reattribute(list(pending.hits.values()), disguise, real)
                    pending.hits = {(h.defender_player, h.defender): h for h in pending.hits.values()}
            on_slot[slot] = real
        elif tag in ("switch", "drag") and len(parts) > 3:
            close()
            slot, key = _slot(parts[2]), _nick_key(parts[2])
            species = parts[3].split(",")[0].strip()
            other = slot[:2] + ("b" if slot.endswith("a") else "a")
            illusion = [s for s in preview.get(slot[:2], []) if s.startswith(_ILLUSION_USERS)]
            # A later |replace| in either slot settles it instead (with the real name).
            settled_later = _replaced_next(lines, index, slot) or _replaced_next(lines, index, other)
            if (key in mons and on_slot.get(other) is mons[key] and len(illusion) == 1
                    and not settled_later):
                key = f"{slot[:2]}:{illusion[0]}"  # the same Pokemon cannot be in two slots
                species = illusion[0]
            mon = mons.setdefault(key, _Mon(player=key[:2], species=species))
            entry[slot] = (len(hits), mon.hp)
            if len(parts) > 4:
                hp = _hp_percent(parts[4])
                if hp is not None:
                    mon.hp = hp
            on_slot[slot] = mon
        elif tag == "move" and len(parts) > 3:
            close()
            attacker = on_slot.get(_slot(parts[2]))
            if attacker is not None:
                pending = _PendingMove(
                    turn=turn, attacker=attacker, move=parts[3].strip(),
                    spread=any(p.startswith("[spread]") for p in parts[4:]),
                )
        elif tag in ("-damage", "-heal", "-sethp") and len(parts) > 3:
            target = on_slot.get(_slot(parts[2]))
            hp = _hp_percent(parts[3])
            if target is None or hp is None:
                continue
            if tag == "-damage" and not _has_from(parts[4:]) and pending is not None:
                hit = pending.hits.get((target.player, target.species))
                if hit is None:
                    hit = pending.start_hit(target)
                hit.hp_after, hit.hit_count, hit.fainted = hp, hit.hit_count + 1, hp == 0.0
            target.hp = hp
        elif tag in ("-enditem", "-ability") and len(parts) > 3 and pending is not None:
            saved = parts[3].strip() in ("Focus Sash", "Focus Band", "Sturdy")
            holder = on_slot.get(_slot(parts[2]))
            if saved and holder is not None and (tag == "-ability") == (parts[3].strip() == "Sturdy"):
                # Opened now, filled by the "-damage" that follows (as for a crit).
                hit = pending.hits.get((holder.player, holder.species)) or pending.start_hit(holder)
                hit.survived = True
        elif tag == "-crit" and len(parts) > 2 and pending is not None:
            crit_target = on_slot.get(_slot(parts[2]))
            if crit_target is not None:
                # "-crit" precedes its "-damage" line: open the hit now, the
                # damage line fills in HP and the hit count.
                crit_hit = pending.hits.get((crit_target.player, crit_target.species))
                (crit_hit or pending.start_hit(crit_target)).crit = True
        elif tag in ("upkeep", "win", "tie"):
            close()
    close()
    # A "-crit" with no following "-damage" (e.g. into a Substitute) left a 0-hit stub.
    return [h for h in hits if h.hit_count > 0]
