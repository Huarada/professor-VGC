# Replay viewer visual fixture

Run `npm run dev` from `web/` and open
`http://127.0.0.1:5173/tests/replay-preview.html?turn=3`.

The JSON is the public battle-view projection of game 1 in
`tests/fixtures/series/bo3-public-2709311277.json`. It exercises the exact
turn shown in the reported UI issue: Incineroar and Gholdengo versus Kingambit
and Mega Floette, with earlier turn actions still visible in the action strip.
The fixture contains no private stat-point spread.
