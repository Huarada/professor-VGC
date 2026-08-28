# Where to put your data (meta / Chaos / replays)

ProfessorVGC separates **deterministic** facts (damage calc from the battle log)
from **probabilistic** metagame context (Smogon *Chaos* usage stats). Your
scraped meta JSON feeds the probabilistic side. Here is exactly where it goes
and how the code reads it.

## 1. Metagame / Chaos usage stats  → `MetaStatsProvider`

This is the "meta context" the AI uses for likely EVs/items, common teammates
and checks-and-counters. **The running app reads this data exclusively from
Google Cloud Firestore — see "Firestore: the app's only Chaos data source"
below.** What follows here is the raw Chaos JSON *shape*, and where a LOCAL
copy of it goes for the OFFLINE tooling that loads Firestore in the first
place (`scripts/migrate_chaos_to_firestore.py`) — the app itself never opens
these files directly.

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
  `data/chaos/gen9championsvgc2026regmb.json`) — this directory is read by
  `scripts/migrate_chaos_to_firestore.py`'s `--source` flag (defaults to
  `data/chaos`) to POPULATE Firestore; it is not read by anything else.

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

## Firestore: the app's ONLY Chaos data source

The running app reads Chaos usage stats from Google Cloud Firestore
**unconditionally** — there is no local-file backend and no config knob to
select one (see `src/config.py`'s own comment, and `Container.
chaos_repository()`, which always builds a `FirestoreChaosRepository`). This
is a deliberate requirement — the app must genuinely depend on Firestore,
not merely default to it with a fallback — not just a preference between two
equally-supported options.

A local `data/chaos/*.json` dump is still very much part of this project, as
the source `scripts/migrate_chaos_to_firestore.py` reads FROM to populate
Firestore in the first place (`scripts/sync_smogon_chaos_to_firestore.py`
does the same directly from Smogon's own site, no local file needed at all)
— that is offline data-loading tooling, a different concern entirely from
what the running app itself queries to answer a question. `ChaosRepository`
(the class that reads local files, still used internally by
`ChaosTierIndex`'s shared tier-selection logic and by this project's own
fast, network-free tests) remains in the codebase for exactly that reason —
it's just never wired into `Container` for the live app anymore.

**Setup:**

1. A GCP project with a Firestore database (Native mode, any single region —
   this data is small enough to stay in the free tier regardless of region;
   `us-central1` is a reasonable default) and a service account scoped to
   `roles/datastore.user` (read/write on Firestore only — never grant
   project-wide Editor/Owner for this).
2. Populate it once from your local `data/chaos/*.json` files (never edits
   them — read-only):

   ```bash
   pip install google-cloud-firestore   # or: pip install -e ".[firestore]"
   python -m scripts.migrate_chaos_to_firestore --project-id YOUR_PROJECT \
       --credentials path/to/service-account-key.json
   # --dry-run first to see file/species counts with no Firestore connection at all
   ```

3. Point the app at it in `.env`:

   ```env
   PROFESSORVGC_FIRESTORE_PROJECT_ID=your-project-id
   PROFESSORVGC_FIRESTORE_CREDENTIALS_PATH=path/to/service-account-key.json
   ```

   Leave `PROFESSORVGC_FIRESTORE_CREDENTIALS_PATH` empty to use Application
   Default Credentials instead (`gcloud auth application-default login`, or an
   already-set `GOOGLE_APPLICATION_CREDENTIALS`) rather than a key file.

**Storage layout** (see `src/adapters/chaos/firestore_chaos_repository.py`'s
own docstring for the full rationale): one small document per tier
(`chaos_tiers/{metagame}-{cutoff}`, e.g. `chaos_tiers/gen9championsvgc2026regmb-1760`)
holding just the original `info` block, and one document PER SPECIES under a
`species` subcollection, keyed by a normalized species id, holding that
species' Chaos JSON object **verbatim** (Abilities/Items/Moves/Spreads/
Teammates/Checks and Counters/Raw count — unchanged shape, just relocated).
A real tier file is 250-300+ species and 2.5-4.5MB — one document per FILE
would risk Firestore's 1MiB document-size limit and force every lookup to
download species nobody asked about; one document per species means a
lookup is always a single, direct, cheap read by id.

**Cost:** the full dataset (4 tiers, ~1,100 species total in the shipped
sample) is roughly 13MB — comfortably inside Firestore's free tier (1GiB
storage, 50k reads/day, 20k writes/day) for any realistic usage volume. The
one-time migration itself is ~1,150 writes, also free-tier. A real GCP
project with billing enabled is still required to create a Firestore
database at all, even to stay entirely within the free tier.

**Live-verified** (2026-08-26, against a real GCP project): migrated all 4
shipped tiers (1,145 species documents), then confirmed
`ChaosAdapter(repository=FirestoreChaosRepository(...))` returns
byte-for-byte identical `MetaContext` output to the local-file backend for
the same species/rating query, including a forme-stripped lookup
(`Raichu-Mega-Y`). Two real things this surfaced, both already fixed in
`migrate_chaos_to_firestore.py`, not left as known issues:
- **Every species' Chaos data has a `"": <weight>` entry under `Moves`**
  (Smogon's own export convention for "no confirmed extra move slot", not a
  real move — `ChaosAdapter` already drops it when reading locally). Firestore
  rejects an empty string as a map key outright, which crashed the very
  first migration attempt — the script now strips it (and any other
  empty-keyed entry, at any depth) before writing, preserving every real
  key/value unchanged. See `migrate_chaos_to_firestore.py`'s own
  `_strip_empty_keys` docstring.
- **A fresh (just-created) Firestore collection can reject a burst of rapid
  writes** with `ABORTED: Too much contention on these documents` — expected
  behavior while its automatic sharding warms up, not a real error. The
  script retries a failed batch commit with exponential backoff and uses a
  smaller batch size (50, not Firestore's 500-write hard limit) to make this
  a non-issue in practice; only ever hit during the one-time migration,
  never by the running app (reads only).

**Security rules:** Firestore's "Security Rules" only gate CLIENT-side SDK
access (a browser/mobile app authenticating end-users via Firebase Auth).
This project only ever talks to Firestore server-side, via a service
account's IAM role — that path bypasses Security Rules entirely. Leave the
database in its default "production mode" (deny-all) rules; there is no
reason to open them for this project's access pattern.

**Troubleshooting: `SSL_ERROR_SSL ... CERTIFICATE_VERIFY_FAILED` /
`unable to get local issuer certificate` when connecting.** grpc (what
`google-cloud-firestore` is built on) uses its own bundled TLS root
certificates, independent of Python's `ssl` module — so `pip install
pip-system-certs` (the fix this repo already documents for the same class of
error from `openai`/`httpx`, see `scripts/faithfulness_benchmark/README.md`)
does **not** fix this one. The real cause is almost always something on the
machine TLS-intercepting outbound HTTPS with its own locally-installed root
certificate — most commonly a security suite's "web/SSL scanning" feature
(Avast, Kaspersky, ESET, ...) or a corporate proxy. Fix:

1. Find the offending root cert (Windows, PowerShell):
   ```powershell
   Get-ChildItem Cert:\CurrentUser\Root | Where-Object { $_.Subject -match "scanning|proxy|<your antivirus name>" }
   ```
2. Export it and append it to a copy of `certifi`'s own bundle (never
   replace — grpc's `GRPC_DEFAULT_SSL_ROOTS_FILE_PATH` REPLACES its default
   roots entirely, so the file must still contain the normal public CA set):
   ```powershell
   $cert = Get-ChildItem Cert:\CurrentUser\Root | Where-Object { $_.Subject -match "..." } | Select-Object -First 1
   $b64 = [Convert]::ToBase64String($cert.RawData, [System.Base64FormattingOptions]::InsertLineBreaks)
   "-----BEGIN CERTIFICATE-----`n$b64`n-----END CERTIFICATE-----" | Out-File your-root.pem -Encoding ascii
   ```
   ```bash
   python -c "import certifi; print(certifi.where())"   # find certifi's bundle
   cat <certifi-bundle> your-root.pem > combined-ca-bundle.pem
   ```
3. Point the app at the combined file: `PROFESSORVGC_FIRESTORE_GRPC_CA_BUNDLE_PATH=path/to/combined-ca-bundle.pem`
   in `.env` (or `--ca-bundle` for `migrate_chaos_to_firestore.py`). Store it
   next to your service account key, outside the repo — it's machine-specific,
   not project config, and isn't a secret either way.

## Automated Smogon sync (Cloud Run Job) — keep Firestore current automatically

`scripts/sync_smogon_chaos_to_firestore.py` fetches current-generation VGC
Chaos stats **directly from Smogon's own stats site**
(`https://www.smogon.com/stats/<month>/chaos/`) — no local files, no manual
download — decompresses each `.json.gz`, and upserts into Firestore through
the exact same write path (`chaos_firestore_writer.py`) as the manual
`migrate_chaos_to_firestore.py` above. Same storage layout, same
sanitization, same idempotent overwrite-in-place semantics.

```bash
python -m scripts.sync_smogon_chaos_to_firestore --project-id YOUR_PROJECT \
    [--month 2026-07] [--credentials path/to/key.json] [--dry-run]
```

**Scope: current-gen VGC only** (filename starts with `gen9` and contains
`vgc`) — Smogon publishes ~150 other formats a month (every OU/Ubers/
Randoms/... across every generation) this project has no use for and that
would blow well past Firestore's free tier for zero product benefit. As of
2026-07 this is 8 files (`gen9championsvgc2026regmb*` and its Bo3-format
sibling, at 4 rating cutoffs each), ~2,165 species documents total — still
comfortably free-tier.

**No history kept.** Each run overwrites the CURRENT regulation's species
documents with whatever Smogon most recently published for that same tier
id — there is no per-month snapshot. This project's own regulation-fallback
mechanism (walking OLDER regulations, not older MONTHS of the same
regulation) already covers "what if this species has no current data"; a
month-over-month trend history is a different feature this script doesn't
attempt.

### Why a scheduled Cloud Run Job, not Pub/Sub

Pub/Sub fits when something WE control emits an event we can subscribe to
(e.g. a new object landing in our own GCS bucket). Smogon's static file
server has no webhook/event mechanism at all — there is nothing to
subscribe to on the ingestion side. The right pattern for "poll an external
source that only publishes, never notifies" is a scheduled, idempotent job:

```
Cloud Scheduler (cron) --HTTP POST (OAuth)--> Cloud Run Jobs API --run--> Cloud Run Job --> Firestore
```

Smogon publishes once a month; a **weekly** schedule catches that within a
few days at negligible cost (the job itself runs in well under a minute and
is a safe no-op when nothing changed).

### Deploy runbook

One-time setup, run from the repo root. Replace `professorvgc-data` /
`firestore-professorvgc` / `us-central1` with your own project id / database
id / region if different.

**1. Enable the needed APIs:**

```bash
gcloud services enable run.googleapis.com artifactregistry.googleapis.com \
    cloudscheduler.googleapis.com cloudbuild.googleapis.com \
    --project=professorvgc-data
```

**2. Create an Artifact Registry repo for the image (one time):**

```bash
gcloud artifacts repositories create professorvgc-images \
    --repository-format=docker --location=us-central1 \
    --project=professorvgc-data
```

**3. Build and push the image** (Cloud Build — no local Docker needed):

```bash
gcloud builds submit --config scripts/cloudbuild.chaos-sync.yaml \
    --project=professorvgc-data .
```

**4. Create the Cloud Run Job**, reusing the SAME `professorvgc-firestore`
service account already scoped to `roles/datastore.user` from the manual
migration setup above — no key file needed here at all, Cloud Run attaches
this identity's credentials automatically:

```bash
gcloud run jobs create chaos-sync \
    --image=us-central1-docker.pkg.dev/professorvgc-data/professorvgc-images/chaos-sync:latest \
    --region=us-central1 \
    --service-account=professorvgc-firestore@professorvgc-data.iam.gserviceaccount.com \
    --set-env-vars="PROFESSORVGC_FIRESTORE_PROJECT_ID=professorvgc-data,PROFESSORVGC_FIRESTORE_DATABASE_ID=firestore-professorvgc,PROFESSORVGC_FIRESTORE_CHAOS_COLLECTION=chaos_tiers" \
    --max-retries=1 --task-timeout=600 \
    --project=professorvgc-data
```

**5. Test it manually once** before scheduling anything:

```bash
gcloud run jobs execute chaos-sync --region=us-central1 --project=professorvgc-data --wait
```

**6. Grant that same service account permission to BE invoked** (a
DIFFERENT permission than reading/writing Firestore — scoped to just this
one job, not project-wide):

```bash
gcloud run jobs add-iam-policy-binding chaos-sync \
    --region=us-central1 --project=professorvgc-data \
    --member="serviceAccount:professorvgc-firestore@professorvgc-data.iam.gserviceaccount.com" \
    --role="roles/run.invoker"
```

**7. Create the weekly Cloud Scheduler trigger:**

```bash
gcloud scheduler jobs create http chaos-sync-weekly \
    --location=us-central1 \
    --schedule="0 6 * * 1" \
    --uri="https://run.googleapis.com/v2/projects/professorvgc-data/locations/us-central1/jobs/chaos-sync:run" \
    --http-method=POST \
    --oauth-service-account-email=professorvgc-firestore@professorvgc-data.iam.gserviceaccount.com \
    --project=professorvgc-data
```

(`0 6 * * 1` = every Monday at 06:00 in the scheduler's configured
timezone — `gcloud scheduler jobs create http` also takes a `--time-zone`
flag if you want a specific one; it defaults to UTC.)

**Updating after a code change:** re-run steps 3 (rebuild/push) then

```bash
gcloud run jobs update chaos-sync \
    --image=us-central1-docker.pkg.dev/professorvgc-data/professorvgc-images/chaos-sync:latest \
    --region=us-central1 --project=professorvgc-data
```

**Checking logs / history:** Cloud Run Jobs execution history and logs are
in the Console (Cloud Run > Jobs > chaos-sync > Executions/Logs), or
`gcloud run jobs executions list --job=chaos-sync --region=us-central1 --project=professorvgc-data`.

**Cost:** the job itself (a container running for well under a minute, once
a week) and Cloud Scheduler (one HTTP call a week) are both effectively
free at this frequency; Artifact Registry storage for one small image is a
few cents/month at most. The only genuinely recurring cost this adds beyond
the Firestore free tier itself.

### Optional follow-up: disable automatic indexing on the `species` collection

Every field of every Firestore document is automatically indexed by
default. This project **never queries** `species` documents by content —
every read is a direct `document(normalized_id).get()`
(`FirestoreChaosRepository.mon_data`) — so those automatic indexes are
pure waste: extra write cost on every sync (this is what live-migrating
Smogon's July 2026 dump ran into: `write_tier`'s adaptive commit-splitting,
above, exists specifically because index-entry overhead pushed some
batches' real wire size well past their raw-JSON estimate) and extra
storage, for indexes nothing ever uses. Exempting the collection fixes
both, permanently:

```bash
gcloud firestore indexes fields update '*' \
    --collection-group=species \
    --database=firestore-professorvgc \
    --disable-indexes \
    --project=professorvgc-data
```

Not required for correctness (the adaptive splitting in
`chaos_firestore_writer.py` already handles oversized commits regardless),
but recommended — cheaper and faster syncs going forward, and the honest
reflection of how this data is actually accessed.

## 2. Your own scraper output

If you scrape/aggregate your own JSON, produce the **same Chaos schema** above,
drop it in `data/chaos/`, and run `scripts/migrate_chaos_to_firestore.py`
(optionally `--source your/directory` if it's elsewhere) to load it into
Firestore — nothing else changes.

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
| Meta / Chaos usage stats | Firestore, loaded from `data/chaos/<format>.json` via `migrate_chaos_to_firestore.py` | `PROFESSORVGC_FIRESTORE_PROJECT_ID` |
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
