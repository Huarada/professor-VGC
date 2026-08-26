# Where to put your data (meta / Chaos / replays)

ProfessorVGC separates **deterministic** facts (damage calc from the battle log)
from **probabilistic** metagame context (Smogon *Chaos* usage stats). Your
scraped meta JSON feeds the probabilistic side. Here is exactly where it goes
and how the code reads it.

## 1. Metagame / Chaos usage stats  → `MetaStatsProvider`

This is the "meta context" the AI uses for likely EVs/items, common teammates
and checks-and-counters.

- **Format:** Smogon *Chaos* JSON schema, i.e. an object shaped like:

  ```json
  {
    "info": { "metagame": "gen9championsvgc2026regmb" },
    "data": {
      "Torkoal": {
        "Raw count": 800,
        "Abilities": { "Drought": 792 },
        "Items": { "Charcoal": 300 },
        "Moves": { "Eruption": 780 },
        "Spreads": { "Quiet:32/0/8/32/0/0": 240 },
        "Teammates": { "Venusaur": 260 },
        "Checks and Counters": { "Aerodactyl": { "p": 0.71 } }
      }
    }
  }
  ```

- **Where to drop the file:** put it under `data/chaos/` (e.g.
  `data/chaos/gen9championsvgc2026regmb.json`).

- **How to point the app at it:** set the env var in your `.env`:

  ```env
  PROFESSORVGC_CHAOS_DATA_PATH=data/chaos/gen9championsvgc2026regmb.json
  ```

  Read by `src/config.py` (`Settings.chaos_data_path`) and consumed by
  `src/adapters/chaos/chaos_adapter.py` (`ChaosAdapter`) and
  `src/adapters/smogon/smogon_strategy_adapter.py` (`ChaosStrategyAdapter`).

- **Where to get a real file:** Smogon publishes monthly stats at
  `https://www.smogon.com/stats/` → open a month → the `chaos/` sub-folder →
  download the JSON for your format. The EV spreads there are divided by 8;
  `ChaosAdapter` converts them back to real 0-252 EVs automatically.

> Species names are matched case/forme-insensitively (e.g. `Typhlosion-Hisui`,
> `Metagross-Mega`), so minor spelling differences between the replay and the
> Chaos file still resolve.


## Rating tiers and regulation fallback (important)

Smogon publishes **one Chaos file per rating cutoff**. Name each file
`<metagame>-<ratingCutoff>.json` and drop them all in `data/chaos/`:

```
data/chaos/
  gen9championsvgc2026regmb-0.json
  gen9championsvgc2026regmb-1500.json
  gen9championsvgc2026regmb-1630.json
  gen9championsvgc2026regmb-1760.json
```

For each analysis the app selects:

- **Ideal tier** — the highest cutoff available (e.g. `-1760`). Best players'
  sets/strategies. This drives the SUGGESTIONS.
- **Current tier** — the bracket the match's rating falls into
  (`cutoff <= rating < next cutoff`). E.g. a 1206-rated game uses `-0`. This is
  surfaced as `current_tier_stats` so the AI can contrast "at your elo" vs "ideal".

The match rating is read from the replay JSON (`rating`) automatically.

### Regulation fallback (new regulations)

Because a brand-new regulation (e.g. Reg M-B) may have little/no data for a
Pokemon, if a species is missing from the newest regulation the app walks
**older regulations of the SAME game**, nearest first, up to
`PROFESSORVGC_REG_FALLBACK_DEPTH` (default 3). It never crosses game families:
Champions falls back only to Champions, base Scarlet/Violet VGC only to base VGC.
Fallback data is tagged in each summary's `source` field (e.g.
`gen9championsvgc2026regma@1760 (fallback)`).

Naming decoded: `gen9` + `champions` (franchise, empty for base VGC) + `vgc` +
`2026` (year) + `reg` + `mb` (regulation) + `-1760` (rating cutoff). Same-game
grouping uses `gen9champions` vs `gen9` (base), so year/regulation can differ but
the game family stays fixed.

## 2. Your own scraper output

If you scrape/aggregate your own JSON, produce the **same Chaos schema** above
and drop it in `data/chaos/`. Nothing else changes — just repoint
`PROFESSORVGC_CHAOS_DATA_PATH`.

If your scraper produces a *different* schema, don't reshape the whole app:
write one adapter that implements the `MetaStatsProvider` Protocol
(`src/domain/interfaces.py`) — a single `build_match_context(species)` method
returning a `MetaContext` — and wire it in `src/services/container.py`
(`Container.chaos`). The domain, services and UI stay untouched (Dependency
Inversion).

## 3. Raw Showdown replays (batch dataset)

Single replays are pasted in the UI. If you want to ingest **many** games (a
folder of Showdown replay JSONs) for offline analysis or to build your own
usage stats, drop them under `data/replays/` and iterate:

```python
import json, glob
from src.adapters.parsers.showdown_parser import ShowdownReplayParser

parser = ShowdownReplayParser()
for path in glob.glob("data/replays/*.json"):
    state = parser.parse(json.load(open(path)))
    print(state.format_id, state.outcome.winner_name, state.brought_by_player())
```

`GameState.outcome` gives you the winner, per-turn faints and rosters, which is
the raw material for building your own aggregated Chaos-style stats.

## Summary

| Data | Put it in | Env var / hook |
|------|-----------|----------------|
| Meta / Chaos usage stats | `data/chaos/<format>.json` | `PROFESSORVGC_CHAOS_DATA_PATH` |
| Custom-schema meta | anywhere | implement `MetaStatsProvider`, wire in `Container` |
| Raw replay dataset | `data/replays/*.json` | parse with `ShowdownReplayParser` |

## Official Smogon data via @pkmn/smogon (analyses / sets / stats)

Beyond the local Chaos files, the app can pull Smogon's OFFICIAL data at runtime
through `@pkmn/smogon` (Node). Enable it with `PROFESSORVGC_USE_SMOGON_DEX=true` and
install the Node deps:

```bash
cd node_calc && npm install   # includes @pkmn/dex @pkmn/data @pkmn/smogon
```

What each is used for (per the ADR):

- **`smogon.analyses`** — official natural-language analyses for the analyzed
  generation. Used to strengthen the LLM explanation with real Smogon strategy
  prose. Strategies become `CompositeStrategyProvider(official -> Chaos fallback)`.
- **`smogon.stats`** — usage statistics. Used when the player asks for
  team-improvement suggestions, to identify synergies (teammates/spreads/items).
- **`smogon.sets`** — official competitive sets. Used together with the Chaos file
  when the player asks to adjust a Pokemon's moveset/item/ability/EVs, citing the
  set + usage % and the synergy within the current team.

These require network access at runtime (Smogon's data host). If it is
unreachable, each call degrades gracefully and the app falls back to the local
Chaos data — nothing crashes. The `format` passed is the replay's `formatid`
(e.g. `gen9championsvgc2026regmb`); the generation is derived from the config.

### Semantic retrieval over `smogon.analyses` (optional, see ADR-027)

`smogon.analyses` returns EVERY format Smogon has published for a species, each
with its own `overview`/`comments` AND a `description` per named set — real
prose the app previously only read one entry of (`analyses[0]`, "newest/most
relevant format first", ignoring the rest). With
`PROFESSORVGC_USE_SEMANTIC_STRATEGY=true`, `SemanticStrategyRetriever`
(`src/adapters/smogon/semantic_strategy_retriever.py`) chunks all of that text,
embeds it plus the user's question (BYOK, same key as chat), and keeps only the
passages closest to it by cosine similarity — no vector database, just an
in-memory list, since the corpus per species is a handful of short paragraphs,
not a large document store. Falls back to the plain `analyses[0]` behavior
whenever there's no question to rank against, or the embedding call itself
fails for any reason.
