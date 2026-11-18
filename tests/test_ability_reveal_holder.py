"""`[from] ability: X` names the ability's holder; `[of]` is the holder only for some messages."""

import pytest

from src.adapters.parsers.showdown_parser import ShowdownReplayParser

HEADER = ["|player|p1|Ash|", "|player|p2|Gary|", "|start",
          "|switch|p1a: Quaquaval|Quaquaval, L50, M|100/100",
          "|switch|p2a: Araquanid|Araquanid, L50, M|100/100",
          "|switch|p2b: Pelipper|Pelipper, L50, M|100/100", "|turn|1"]


def abilities(*lines: str) -> dict[str, str | None]:
    state = ShowdownReplayParser().parse("\n".join(HEADER + list(lines)))
    return {mon.species: mon.ability for side in state.sides for mon in side.team}


@pytest.mark.parametrize("ability,line", [
    ("Water Absorb", "|-heal|p2a: Araquanid|82/100|[from] ability: Water Absorb|[of] p1a: Quaquaval"),
    ("Volt Absorb", "|-heal|p2a: Araquanid|82/100|[from] ability: Volt Absorb|[of] p1a: Quaquaval"),
    ("Dry Skin", "|-heal|p2a: Araquanid|82/100|[from] ability: Dry Skin|[of] p1a: Quaquaval"),
])
def test_an_absorbing_ability_belongs_to_the_pokemon_that_absorbed_not_its_attacker(ability, line):
    known = abilities("|move|p1a: Quaquaval|Surf|p2a: Araquanid", line)
    assert known["Araquanid"] == ability
    assert known["Quaquaval"] is None


def test_the_of_pokemon_is_still_the_holder_where_the_holder_acts():
    known = abilities(
        "|-weather|RainDance|[from] ability: Drizzle|[of] p2b: Pelipper",
        "|-damage|p1a: Quaquaval|88/100|[from] ability: Rough Skin|[of] p2a: Araquanid",
    )
    assert known["Pelipper"] == "Drizzle" and known["Araquanid"] == "Rough Skin"
    assert known["Quaquaval"] is None
