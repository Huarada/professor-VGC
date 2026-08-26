"""Fixture replays for the faithfulness mini-benchmark.

Five of these ten cases are reused verbatim (only the log text, none of the
assertions) from the project's own regression tests — each one is the exact
minimal repro for a documented ADR hallucination class, which is precisely
why they are high-signal fixtures: a grounded pipeline SHOULD get these
right, and a naive ungrounded LLM has a specific, predictable way to get
each one wrong.

The other five are hand-authored for this benchmark and were validated
against the real ShowdownReplayParser + TurnReplaySimulator before being
committed here (see the benchmark README for the validation transcript) —
none of them were taken on faith.

    id                    | trap category           | source
    ----------------------|--------------------------|---------------------------------
    bench_only            | benched Pokemon          | tests/test_bench_only_exclusion.py
    mega_evolution        | mid-game forme change    | tests/test_forme_change_events.py
    stat_stage_intimidate | stat stage tracking      | tests/test_boost_events_parsing.py
    protect_spread        | Protect vs spread move   | tests/test_protect_block_detection.py
    forfeit               | forfeit vs real KO win   | tests/test_forfeit_detection.py
    protect_genuine_read  | Protect, single-target   | new, validated below
    protect_misallocated  | Protect, misallocated    | new, validated below
    mirror_match_protect  | same species both sides  | new, validated below
    normal_clean          | control (no traps)       | new, validated below
    bench_plus_mega       | two traps at once        | new, validated below
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class Fixture(BaseModel):
    """One benchmark case: a replay plus the question asked about it."""

    id: str
    tags: list[str]
    question: str
    replay: dict[str, Any]
    note: str = ""


FIXTURES: list[Fixture] = [
    Fixture(
        id="bench_only",
        tags=["bench_only"],
        question="Walk me through this game and say which Pokemon mattered.",
        note="Whimsicott is team-previewed for p2 but never switches in.",
        replay={
            "formatid": "gen9championsvgc2026regmb",
            "log": (
                "|player|p1|a|1|1058\n|player|p2|b|2|1065\n|clearpoke\n"
                "|poke|p1|Raichu, L50, M|\n"
                "|poke|p2|Whimsicott, L50, F|\n"
                "|poke|p2|Garchomp, L50, F|\n"
                "|teampreview|4\n|start\n"
                "|switch|p1a: Raichu|Raichu, L50, M|100/100\n"
                "|switch|p2a: Garchomp|Garchomp, L50, F|100/100\n"
                "|turn|1\n"
                "|move|p1a: Raichu|Focus Blast|p2a: Garchomp\n"
                "|-damage|p2a: Garchomp|60/100\n"
                "|move|p2a: Garchomp|Earthquake|p1a: Raichu\n"
                "|-damage|p1a: Raichu|0 fnt\n|faint|p1a: Raichu\n"
                "|win|b\n"
            ),
        },
    ),
    Fixture(
        id="mega_evolution",
        tags=["mega_evolution"],
        question="What happened in this game, turn by turn?",
        note="Gengar Mega Evolves on turn 1; ground truth must attribute Shadow Ball to Mega Gengar's real forme, not a fabricated one.",
        replay={
            "formatid": "gen9championsvgc2026regmb",
            "log": (
                "|player|p1|Ash|1|1|\n|player|p2|Gary|2|1|\n"
                "|switch|p1a: Gengar|Gengar, L50, M|100/100\n"
                "|switch|p2a: Garchomp|Garchomp, L50, F|100/100\n"
                "|turn|1\n"
                "|detailschange|p1a: Gengar|Gengar-Mega, L50, M\n"
                "|-mega|p1a: Gengar|Gengar|Gengarite\n"
                "|move|p1a: Gengar|Shadow Ball|p2a: Garchomp\n"
                "|-damage|p2a: Garchomp|60/100\n"
                "|win|Ash\n"
            ),
        },
    ),
    Fixture(
        id="stat_stage_intimidate",
        tags=["stat_stage"],
        question="Explain what happened and whether the stat changes mattered.",
        note="Intimidate drops Garchomp's Atk by 1; Garchomp's own Swords Dance raises it by 2 — a naive reader has no ground truth for either.",
        replay={
            "formatid": "gen9championsvgc2026regmb",
            "log": (
                "|player|p1|Ash|1|1|\n|player|p2|Gary|2|1|\n"
                "|switch|p1a: Incineroar|Incineroar, L50, M|100/100\n"
                "|switch|p2a: Garchomp|Garchomp, L50, F|100/100\n"
                "|-ability|p1a: Incineroar|Intimidate|boost\n"
                "|-unboost|p2a: Garchomp|atk|1\n"
                "|turn|1\n"
                "|move|p2a: Garchomp|Swords Dance|p2a: Garchomp\n"
                "|-boost|p2a: Garchomp|atk|2\n"
                "|move|p1a: Incineroar|Fake Out|p2a: Garchomp\n"
                "|-damage|p2a: Garchomp|90/100\n"
                "|win|Ash\n"
            ),
        },
    ),
    Fixture(
        id="protect_spread",
        tags=["protect_read"],
        question="Was Ceruledge's Protect a good read?",
        note="Earthquake is a spread move that guaranteed-hits Kingambit regardless of Ceruledge's block — never a genuine read.",
        replay={
            "formatid": "gen9championsvgc2026regmb",
            "log": (
                "|player|p1|Ash|1|1|\n|player|p2|Gary|2|1|\n"
                "|poke|p1|Garchomp, L50|\n|poke|p2|Ceruledge, L50|\n|poke|p2|Kingambit, L50|\n"
                "|start\n"
                "|switch|p1a: Garchomp|Garchomp, L50|100/100\n"
                "|switch|p2a: Ceruledge|Ceruledge, L50|100/100\n"
                "|switch|p2b: Kingambit|Kingambit, L50|100/100\n|turn|1\n"
                "|move|p2a: Ceruledge|Protect|p2a: Ceruledge\n"
                "|-singleturn|p2a: Ceruledge|Protect\n"
                "|move|p1a: Garchomp|Earthquake|p2a: Ceruledge|[spread] p2b\n"
                "|-activate|p2a: Ceruledge|move: Protect\n"
                "|-supereffective|p2b: Kingambit|1\n"
                "|-damage|p2b: Kingambit|16/100\n|win|Ash\n"
            ),
        },
    ),
    Fixture(
        id="forfeit",
        tags=["forfeit"],
        question="How did this game end and who played better?",
        note="Ash forfeits mid-game — there is no real final score, and the winning side's plan cannot be judged from a completed match.",
        replay={
            "formatid": "gen9championsvgc2026regmb",
            "log": (
                "|player|p1|Ash|1|1|\n|player|p2|Gary|2|1|\n"
                "|poke|p1|Garchomp, L50|\n|poke|p2|Ceruledge, L50|\n"
                "|start\n"
                "|switch|p1a: Garchomp|Garchomp, L50|100/100\n"
                "|switch|p2a: Ceruledge|Ceruledge, L50|100/100\n|turn|1\n"
                "|move|p1a: Garchomp|Earthquake|p2a: Ceruledge\n"
                "|-damage|p2a: Ceruledge|40/100\n"
                "|-message|Ash forfeited.\n"
                "|win|Gary\n"
            ),
        },
    ),
    Fixture(
        id="protect_genuine_read",
        tags=["protect_read"],
        question="Was Garchomp's Protect on turn 2 a good decision?",
        note="Single-target Close Combat, nothing else happens that turn — a genuine, defensible read. Validated live (see README).",
        replay={
            "formatid": "gen9championsvgc2026regmb",
            "log": (
                "|player|p1|Ash|1|1|\n|player|p2|Gary|2|1|\n"
                "|poke|p1|Garchomp, L50|\n|poke|p1|Basculegion, L50|\n"
                "|poke|p2|Staraptor, L50|\n|poke|p2|Ceruledge, L50|\n"
                "|start\n"
                "|switch|p1a: Garchomp|Garchomp, L50|100/100\n"
                "|switch|p1b: Basculegion|Basculegion, L50|100/100\n"
                "|switch|p2a: Staraptor|Staraptor, L50|100/100\n"
                "|switch|p2b: Ceruledge|Ceruledge, L50|100/100\n"
                "|turn|1\n"
                "|move|p1a: Garchomp|Rock Slide|p2a: Staraptor|[spread] p2b\n"
                "|-damage|p2a: Staraptor|64/100\n"
                "|-damage|p2b: Ceruledge|58/100\n"
                "|turn|2\n"
                "|move|p1a: Garchomp|Protect|p1a: Garchomp\n"
                "|-singleturn|p1a: Garchomp|Protect\n"
                "|move|p2a: Staraptor|Close Combat|p1a: Garchomp\n"
                "|-activate|p1a: Garchomp|move: Protect\n"
                "|win|Ash\n"
            ),
        },
    ),
    Fixture(
        id="protect_misallocated",
        tags=["protect_read"],
        question="Did Garchomp's Protect help p2 win this exchange?",
        note="Spread Heat Wave denies no real threat to Garchomp while Staraptor faints the same turn — a misallocated block. Validated live.",
        replay={
            "formatid": "gen9championsvgc2026regmb",
            "log": (
                "|player|p1|Ash|1|1|\n|player|p2|Gary|2|1|\n"
                "|poke|p1|Charizard, L50|\n|poke|p1|Basculegion, L50|\n"
                "|poke|p2|Garchomp, L50|\n|poke|p2|Staraptor, L50|\n"
                "|start\n"
                "|switch|p1a: Charizard|Charizard, L50|100/100\n"
                "|switch|p1b: Basculegion|Basculegion, L50|100/100\n"
                "|switch|p2a: Garchomp|Garchomp, L50|100/100\n"
                "|switch|p2b: Staraptor|Staraptor, L50|100/100\n"
                "|turn|1\n"
                "|move|p2a: Garchomp|Protect|p2a: Garchomp\n"
                "|-singleturn|p2a: Garchomp|Protect\n"
                "|move|p1a: Charizard|Heat Wave|p2a: Garchomp|[spread] p2b\n"
                "|-activate|p2a: Garchomp|move: Protect\n"
                "|-damage|p2b: Staraptor|28/100\n"
                "|move|p1b: Basculegion|Wave Crash|p2b: Staraptor\n"
                "|-damage|p2b: Staraptor|0 fnt\n|faint|p2b: Staraptor\n"
                "|win|Ash\n"
            ),
        },
    ),
    Fixture(
        id="mirror_match_protect",
        tags=["protect_read", "mirror_match"],
        question="Who should get credit for the Staraptor knockout — was p2's Protect a good read?",
        note="Both sides bring a Garchomp; the global side_of() map alone would misattribute p2's block to p1. Validated live against the real fix (ADR-008).",
        replay={
            "formatid": "gen9championsvgc2026regmb",
            "log": (
                "|player|p1|Ash|1|1|\n|player|p2|Gary|2|1|\n"
                "|poke|p1|Garchomp, L50|\n|poke|p1|Charizard, L50|\n"
                "|poke|p2|Garchomp, L50|\n|poke|p2|Staraptor, L50|\n"
                "|start\n"
                "|switch|p1a: Garchomp|Garchomp, L50|100/100\n"
                "|switch|p1b: Charizard|Charizard, L50|100/100\n"
                "|switch|p2a: Garchomp|Garchomp, L50|100/100\n"
                "|switch|p2b: Staraptor|Staraptor, L50|100/100\n"
                "|turn|1\n"
                "|move|p2a: Garchomp|Protect|p2a: Garchomp\n"
                "|-singleturn|p2a: Garchomp|Protect\n"
                "|move|p1b: Charizard|Heat Wave|p2a: Garchomp|[spread] p2b\n"
                "|-activate|p2a: Garchomp|move: Protect\n"
                "|-damage|p2b: Staraptor|28/100\n"
                "|move|p1a: Garchomp|Dragon Claw|p2b: Staraptor\n"
                "|-damage|p2b: Staraptor|0 fnt\n|faint|p2b: Staraptor\n"
                "|win|Ash\n"
            ),
        },
    ),
    Fixture(
        id="normal_clean",
        tags=["control"],
        question="Summarize this game and say who played better.",
        note="No traps: a plain two-turn exchange, used as a non-adversarial control case.",
        replay={
            "formatid": "gen9championsvgc2026regmb",
            "log": (
                "|player|p1|Ash|1|1|\n|player|p2|Gary|2|1|\n"
                "|poke|p1|Torkoal, L50|\n|poke|p1|Flutter Mane, L50|\n"
                "|poke|p2|Amoonguss, L50|\n|poke|p2|Iron Hands, L50|\n"
                "|start\n"
                "|switch|p1a: Torkoal|Torkoal, L50|100/100\n"
                "|switch|p1b: Flutter Mane|Flutter Mane, L50|100/100\n"
                "|switch|p2a: Amoonguss|Amoonguss, L50|100/100\n"
                "|switch|p2b: Iron Hands|Iron Hands, L50|100/100\n"
                "|turn|1\n"
                "|move|p1b: Flutter Mane|Moonblast|p2b: Iron Hands\n"
                "|-damage|p2b: Iron Hands|54/100\n"
                "|move|p2b: Iron Hands|Wild Charge|p1b: Flutter Mane\n"
                "|-damage|p1b: Flutter Mane|85/100\n"
                "|move|p1a: Torkoal|Eruption|p2a: Amoonguss|[spread] p2b\n"
                "|-damage|p2a: Amoonguss|20/100\n"
                "|-damage|p2b: Iron Hands|10/100\n"
                "|move|p2a: Amoonguss|Spore|p1a: Torkoal\n"
                "|-status|p1a: Torkoal|slp\n"
                "|turn|2\n"
                "|move|p1b: Flutter Mane|Moonblast|p2b: Iron Hands\n"
                "|-damage|p2b: Iron Hands|0 fnt\n|faint|p2b: Iron Hands\n"
                "|move|p2a: Amoonguss|Sludge Bomb|p1b: Flutter Mane\n"
                "|-damage|p1b: Flutter Mane|30/100\n"
                "|win|Ash\n"
            ),
        },
    ),
    Fixture(
        id="bench_plus_mega",
        tags=["bench_only", "mega_evolution"],
        question="Walk me through this game and say which Pokemon mattered most.",
        note="Two traps stacked: Whimsicott is team-previewed for p2 but never brought in, AND Gengar Mega Evolves turn 1.",
        replay={
            "formatid": "gen9championsvgc2026regmb",
            "log": (
                "|player|p1|Ash|1|1|\n|player|p2|Gary|2|1|\n"
                "|poke|p1|Raichu, L50|\n|poke|p1|Torkoal, L50|\n"
                "|poke|p2|Gengar, L50|\n|poke|p2|Whimsicott, L50|\n|poke|p2|Garchomp, L50|\n"
                "|teampreview|4\n|start\n"
                "|switch|p1a: Raichu|Raichu, L50|100/100\n"
                "|switch|p1b: Torkoal|Torkoal, L50|100/100\n"
                "|switch|p2a: Gengar|Gengar, L50|100/100\n"
                "|switch|p2b: Garchomp|Garchomp, L50|100/100\n"
                "|turn|1\n"
                "|detailschange|p2a: Gengar|Gengar-Mega, L50\n"
                "|-mega|p2a: Gengar|Gengar|Gengarite\n"
                "|move|p2a: Gengar|Shadow Ball|p1a: Raichu\n"
                "|-damage|p1a: Raichu|55/100\n"
                "|move|p1a: Raichu|Thunderbolt|p2a: Gengar\n"
                "|-damage|p2a: Gengar|40/100\n"
                "|turn|2\n"
                "|move|p2a: Gengar|Shadow Ball|p1a: Raichu\n"
                "|-damage|p1a: Raichu|0 fnt\n|faint|p1a: Raichu\n"
                "|win|Gary\n"
            ),
        },
    ),
]
