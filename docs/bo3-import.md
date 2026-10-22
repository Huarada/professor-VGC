# Import a Showdown BO3 in one action

In **Best of three**, paste any of these into the main import field:

- The Showdown series room URL (`https://play.pokemonshowdown.com/game-bestof3-...`).
- One uploaded game replay from that series, including game 2 or game 3.
- Several replay URLs together, separated by newlines or spaces.

Click **Import BO3**. The application fills the individual game fields using the
official game numbers. Review the imported links, select the applicable teamsheet
and HP mode, then click **Analyze BO3**. Importing does not consume an analysis slot
or call a model. BO1 keeps its existing single-replay input.

The individual JSON/log/URL fields remain under **Review or paste individual
games**. Import failures leave those fields unchanged. Successful imports replace
the previous inputs and clear the old BO3 analysis. Unavailable games leave their
original slot empty; game 2 is never renamed game 1 to hide missing history.

## How discovery works

The public series room contains numbered `game1`, `game2`, `game3` links in the
Showdown room protocol. A short-lived guest WebSocket connection joins only that
room, reads its existing public log, and closes. There is no login, battle action,
chat message or upload. Each child replay is then downloaded from the replay host.

Replay protocol entries also carry `bestof` metadata (series room and game number)
and optional `next` links. If the live room expired, the importer follows available
links and queries the official public replay search for a seed player's games.
Search candidates must have the same players, and are accepted only after their
own protocol metadata confirms the exact same series ID. Player names or nearby
battle IDs alone never establish series membership. Spectator chat cannot supply
these protocol entries. Repeated replay URLs are downloaded once per import.

References: official [BO3 room implementation](https://github.com/smogon/pokemon-showdown/blob/master/server/room-battle-bestof.ts)
and [client server configuration](https://github.com/smogon/pokemon-showdown-client/blob/master/config/config-example.js).

Only official Showdown hosts and Champions battle/BO3 room identifiers are
accepted. Redirects are refused. Discovery has a 60-second request deadline,
8-second per-operation timeout, a 2 MB HTTP/WebSocket response limit, at most 16
replay reads and 12 matching search candidates from the first search page. Search
may miss older or unpublished games; manual URLs remain available. Imported games
without missing slots also pass the existing series validation before being shown.
Every Analyze submission still validates the final edited inputs before quota use.

## Availability limits

- A series room is live server state and can expire. If it cannot be read, paste
  any uploaded replay from that series instead.
- A room link does not publish battle replays. Games without an accessible uploaded
  replay are reported unavailable. Save/share them in Showdown, then import again.
- If both room and published replay metadata are unavailable, the application
  cannot recover the series from the room URL alone.
- Manually supplied Champions replays without series metadata keep their supplied
  order and show a warning; they are not automatically grouped by player names.
- Two imported games do not automatically mean a complete series. The existing
  BO3 score validation determines completeness after analysis.

## User-reported example, verified 2026-10-22

Importing
`https://play.pokemonshowdown.com/game-bestof3-gen9championsvgc2026regmcbo3-2702587956`
recovered these uploaded games in official order:

| Game | Replay |
|---|---|
| 1 | [2702587957](https://replay.pokemonshowdown.com/gen9championsvgc2026regmcbo3-2702587957) |
| 2 | [2702589117](https://replay.pokemonshowdown.com/gen9championsvgc2026regmcbo3-2702589117) |
| 3 | [2702589789](https://replay.pokemonshowdown.com/gen9championsvgc2026regmcbo3-2702589789) |

The original supplied replay links were game 3 followed by game 2. No battle ID
arithmetic is used to recover game 1. Live availability may change; deterministic
tests mock the public protocol, HTTP responses and UI without network/model keys.

```bash
python -m pytest -q tests/test_series_import.py tests/test_ui_smoke.py
```


## Public teamsheets and natures

Import also extracts pre-battle `showteam` protocol exports from the published
replays. These provide only the fields explicitly present: species, item, named
ability, moves and nature. The BO3 form prefills the teamsheet and shows the count
of declared natures. Analysis repeats extraction from raw/JSON replays, so it also
works when the form was not prefilled. A known public nature stays fixed; a blank
nature stays a search dimension. Redacted investment is never interpreted as zero.

For the reported series, all 12 natures are public (Delphox and Dragapult Timid;
Sylveon, Indeedee and Blaziken Modest; Incineroar Careful; the others Adamant).
The shared official BO3 room identifier establishes registered-team continuity.
Species previews or player names alone do not. Private manual investment can be
added without becoming public; conflicting declarations fail before model calls.
Late `showteam` lines are not used to justify earlier moves.

The [public-series regression](bo3-inference/validation/public-teamsheet-regression.json)
records declared natures, eligible damage and narrowed necessary bounds. Full
hidden investment is unknown in these public logs; no hidden-set accuracy is
claimed. A bounded joint search may still retain sound constraints from an
exhaustively checked individual attack.
