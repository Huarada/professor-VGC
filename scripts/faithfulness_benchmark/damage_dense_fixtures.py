"""20 additional fixtures, engineered specifically to maximize the number of
genuine damage_range claims per replay — not trap-category diversity (the
original 10 fixtures.py already cover that).

Per the user's own statistical framing: damage_range is the headline metric
(the one category where grounding actually shows an effect), so growing the
sample size specifically there is worth more than growing it with generic
fixtures where most claims fall into categories that already tie. Every
species used here was verified present in the real Chaos data (a direct
`Container.chaos().build_match_context()` query, not a visual scan of the
dumped JSON — see the session transcript this was built in) BEFORE being
used, and every (attacker, move, defender) triple in `_EXCHANGES` was swept
against the real SmogonCalcAdapter (not a fake) for a nonzero result before
being committed — the same validation discipline as the original 10 (see
fixtures.py's own docstring for why: a real Pokemon type immunity produces a
genuine, correct 0.0% that would otherwise look exactly like "the calculator
is broken").
"""

from __future__ import annotations

from scripts.faithfulness_benchmark.fixtures import Fixture


def _build_damage_log(
    p1: tuple[str, str],
    p2: tuple[str, str],
    turns: list[list[tuple[str, str, str, int]]],
    winner: str = "Ash",
) -> dict:
    """Build a minimal, valid 2v2 Showdown log where each exchange is one
    real (non-status) move against a real target. `turns` is a list of
    turns; each turn is a list of (attacker_species, move, defender_species,
    defender_hp_after) — hp_after is flavor for the log's own "actual
    result" text (a faithful answer may cite it as remaining HP), NOT the
    ground truth for damage_range verification, which always comes from a
    live calc-engine query against the real Chaos-derived spread, so it
    doesn't need to match the calc's projection (see the type-immunity
    fixture bug this project already found and fixed once for what DOES
    have to be checked instead: the move must not be immune against the
    target's real typing — this file's fixtures were all swept for that
    before being committed)."""
    lines = ["|player|p1|Ash|1|1|", "|player|p2|Gary|2|1|"]
    sides = {"p1": list(p1), "p2": list(p2)}
    for player, mons in sides.items():
        for sp in mons:
            lines.append(f"|poke|{player}|{sp}, L50|")
    lines.append("|start")
    slot_of: dict[str, str] = {}
    for player, mons in sides.items():
        for i, sp in enumerate(mons):
            slot = player + ("a" if i == 0 else "b")
            slot_of[sp] = slot
            lines.append(f"|switch|{slot}: {sp}|{sp}, L50|100/100")
    for turn_idx, exchanges in enumerate(turns, start=1):
        lines.append(f"|turn|{turn_idx}")
        for attacker, move, defender, hp_after in exchanges:
            lines.append(f"|move|{slot_of[attacker]}: {attacker}|{move}|{slot_of[defender]}: {defender}")
            if hp_after <= 0:
                lines.append(f"|-damage|{slot_of[defender]}: {defender}|0 fnt")
                lines.append(f"|faint|{slot_of[defender]}: {defender}")
            else:
                lines.append(f"|-damage|{slot_of[defender]}: {defender}|{hp_after}/100")
    lines.append(f"|win|{winner}")
    return {"formatid": "gen9championsvgc2026regmb", "log": "\n".join(lines) + "\n"}


_QUESTION = "Walk me through every hit this game and roughly how much damage each one dealt."

# (fixture_id, p1 pair, p2 pair, turns[exchanges])
_SPECS: list[tuple[str, tuple[str, str], tuple[str, str], list[list[tuple[str, str, str, int]]]]] = [
    ("dmg01_dragapult_volcarona", ("Dragapult", "Volcarona"), ("Talonflame", "Toxapex"), [
        [("Dragapult", "Shadow Ball", "Talonflame", 55), ("Volcarona", "Bug Buzz", "Toxapex", 62)],
        [("Talonflame", "Brave Bird", "Dragapult", 40), ("Toxapex", "Sludge Bomb", "Volcarona", 48)],
    ]),
    ("dmg02_skeledirge_bellibolt", ("Skeledirge", "Bellibolt"), ("Annihilape", "Gholdengo"), [
        [("Skeledirge", "Torch Song", "Annihilape", 58), ("Bellibolt", "Thunderbolt", "Gholdengo", 50)],
        [("Annihilape", "Drain Punch", "Bellibolt", 45), ("Gholdengo", "Shadow Ball", "Skeledirge", 60)],
    ]),
    ("dmg03_rotomwash_grimmsnarl", ("Rotom-Wash", "Grimmsnarl"), ("Sinistcha", "Hatterene"), [
        [("Rotom-Wash", "Hydro Pump", "Sinistcha", 42), ("Grimmsnarl", "Spirit Break", "Hatterene", 55)],
        [("Sinistcha", "Matcha Gotcha", "Rotom-Wash", 65), ("Hatterene", "Dazzling Gleam", "Grimmsnarl", 38)],
    ]),
    ("dmg04_malamar_chandelure", ("Malamar", "Chandelure"), ("Krookodile", "Klefki"), [
        [("Malamar", "Superpower", "Krookodile", 48), ("Chandelure", "Flamethrower", "Klefki", 50)],
        [("Krookodile", "Earthquake", "Chandelure", 40), ("Klefki", "Play Rough", "Malamar", 52)],
    ]),
    ("dmg05_farigiraf_armarouge", ("Farigiraf", "Armarouge"), ("Arcanine", "Gliscor"), [
        [("Farigiraf", "Hyper Voice", "Arcanine", 60), ("Armarouge", "Flamethrower", "Gliscor", 55)],
        [("Arcanine", "Flare Blitz", "Farigiraf", 35), ("Gliscor", "Earthquake", "Armarouge", 48)],
    ]),
    ("dmg06_maushold_azumarill", ("Maushold", "Azumarill"), ("Milotic", "Sceptile"), [
        [("Maushold", "Population Bomb", "Milotic", 50), ("Azumarill", "Liquidation", "Sceptile", 42)],
        [("Milotic", "Scald", "Azumarill", 58), ("Sceptile", "Leaf Storm", "Maushold", 30)],
    ]),
    ("dmg07_gallade_vaporeon", ("Gallade", "Vaporeon"), ("Empoleon", "Sableye"), [
        [("Gallade", "Psycho Cut", "Empoleon", 55), ("Vaporeon", "Scald", "Sableye", 60)],
        [("Empoleon", "Flash Cannon", "Gallade", 45), ("Sableye", "Foul Play", "Vaporeon", 50)],
    ]),
    ("dmg08_vileplume_tyranitar", ("Vileplume", "Tyranitar"), ("Meowscarada", "Primarina"), [
        [("Vileplume", "Giga Drain", "Meowscarada", 62), ("Tyranitar", "Rock Slide", "Primarina", 48)],
        [("Meowscarada", "Flower Trick", "Vileplume", 20), ("Primarina", "Moonblast", "Tyranitar", 55)],
    ]),
    ("dmg09_corviknight_hippowdon", ("Corviknight", "Hippowdon"), ("Mimikyu", "Archaludon"), [
        [("Corviknight", "Iron Head", "Mimikyu", 48), ("Hippowdon", "Earthquake", "Archaludon", 52)],
        [("Mimikyu", "Play Rough", "Corviknight", 45), ("Archaludon", "Flash Cannon", "Hippowdon", 50)],
    ]),
    ("dmg10_whimsicott_glimmora", ("Whimsicott", "Glimmora"), ("Espathra", "Excadrill"), [
        [("Whimsicott", "Moonblast", "Espathra", 40), ("Glimmora", "Power Gem", "Excadrill", 55)],
        [("Espathra", "Dazzling Gleam", "Whimsicott", 50), ("Excadrill", "Iron Head", "Glimmora", 35)],
    ]),
    ("dmg11_mamoswine_dragonite", ("Mamoswine", "Dragonite"), ("Gyarados", "Clefable"), [
        [("Mamoswine", "Ice Shard", "Gyarados", 60), ("Dragonite", "Extreme Speed", "Clefable", 55)],
        [("Gyarados", "Waterfall", "Mamoswine", 42), ("Clefable", "Moonblast", "Dragonite", 48)],
    ]),
    ("dmg12_palafin_swampert", ("Palafin", "Swampert"), ("Garchomp", "Staraptor"), [
        [("Palafin", "Wave Crash", "Staraptor", 45), ("Swampert", "Earthquake", "Garchomp", 50)],
        [("Garchomp", "Rock Slide", "Palafin", 40), ("Staraptor", "Brave Bird", "Swampert", 55)],
    ]),
    ("dmg13_ceruledge_kingambit", ("Ceruledge", "Kingambit"), ("Gengar", "Torkoal"), [
        [("Ceruledge", "Bitter Blade", "Gengar", 48), ("Kingambit", "Sucker Punch", "Torkoal", 40)],
        [("Gengar", "Shadow Ball", "Ceruledge", 52), ("Torkoal", "Eruption", "Kingambit", 45)],
    ]),
    ("dmg14_raichu_incineroar", ("Raichu", "Incineroar"), ("Basculegion", "Dragapult"), [
        [("Raichu", "Volt Switch", "Basculegion", 55), ("Incineroar", "Flare Blitz", "Dragapult", 48)],
        [("Basculegion", "Wave Crash", "Raichu", 30), ("Dragapult", "Draco Meteor", "Incineroar", 40)],
    ]),
    ("dmg15_volcarona_toxapex", ("Volcarona", "Toxapex"), ("Skeledirge", "Bellibolt"), [
        [("Volcarona", "Fiery Dance", "Skeledirge", 58), ("Toxapex", "Scald", "Bellibolt", 52)],
        [("Skeledirge", "Hex", "Volcarona", 45), ("Bellibolt", "Parabolic Charge", "Toxapex", 60)],
    ]),
    ("dmg16_annihilape_gholdengo", ("Annihilape", "Gholdengo"), ("Rotom-Wash", "Grimmsnarl"), [
        [("Annihilape", "Rage Fist", "Rotom-Wash", 50), ("Gholdengo", "Make It Rain", "Grimmsnarl", 42)],
        [("Rotom-Wash", "Volt Switch", "Annihilape", 55), ("Grimmsnarl", "Spirit Break", "Gholdengo", 48)],
    ]),
    ("dmg17_sinistcha_hatterene", ("Sinistcha", "Hatterene"), ("Malamar", "Chandelure"), [
        [("Sinistcha", "Matcha Gotcha", "Malamar", 60), ("Hatterene", "Psychic", "Chandelure", 45)],
        [("Malamar", "Knock Off", "Sinistcha", 50), ("Chandelure", "Shadow Ball", "Hatterene", 40)],
    ]),
    ("dmg18_krookodile_klefki", ("Krookodile", "Klefki"), ("Farigiraf", "Armarouge"), [
        [("Krookodile", "Knock Off", "Farigiraf", 55), ("Klefki", "Foul Play", "Armarouge", 48)],
        [("Farigiraf", "Hyper Voice", "Krookodile", 100), ("Armarouge", "Armor Cannon", "Klefki", 30)],
    ]),
    ("dmg19_arcanine_gliscor", ("Arcanine", "Gliscor"), ("Maushold", "Azumarill"), [
        [("Arcanine", "Extreme Speed", "Maushold", 40), ("Gliscor", "Earthquake", "Azumarill", 52)],
        [("Maushold", "Population Bomb", "Arcanine", 30), ("Azumarill", "Play Rough", "Gliscor", 45)],
    ]),
    ("dmg20_milotic_sceptile", ("Milotic", "Sceptile"), ("Gallade", "Vaporeon"), [
        [("Milotic", "Ice Beam", "Gallade", 55), ("Sceptile", "Leaf Storm", "Vaporeon", 42)],
        [("Gallade", "Close Combat", "Sceptile", 35), ("Vaporeon", "Muddy Water", "Milotic", 60)],
    ]),
]


def _make_fixture(spec: tuple) -> Fixture:
    fixture_id, p1, p2, turns = spec
    replay = _build_damage_log(p1, p2, turns)
    all_moves = {ex[1] for turn in turns for ex in turn}
    return Fixture(
        id=fixture_id,
        tags=["damage_dense"],
        question=_QUESTION,
        note=(
            f"Damage-density fixture: {p1[0]}/{p1[1]} vs {p2[0]}/{p2[1]}, "
            f"{sum(len(t) for t in turns)} real, non-immune exchanges across "
            f"{len(turns)} turns — engineered to maximize genuine damage_range "
            f"claims, not trap-category coverage (see fixtures.py for that)."
        ),
        replay=replay,
    )


DAMAGE_DENSE_FIXTURES: list[Fixture] = [_make_fixture(spec) for spec in _SPECS]
