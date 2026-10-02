# CLAUDE.md — ProfessorVGC

> Project memory for AI assistants (Claude in VS Code, Claude Code, etc.).
> **Language policy: this file, all code, identifiers, comments, prompts and docs
> are written in ENGLISH**, because the project targets the international dev
> community. Keep it that way in every change.


## 1. What this project is

ProfessorVGC (the product name) analyzes Pokémon **VGC**
battles. It combines:

- a **deterministic** layer — exact damage/speed from Smogon's `@smogon/calc`
  (via a Node subprocess) plus a structured reconstruction of the battle log;
- a **probabilistic** layer — metagame usage stats (Smogon *Chaos* files and the
  official `@pkmn/smogon` data) for likely sets, threats and synergies;
- an **LLM orchestration** layer (Google ADK by default; LangChain or native) that turns the above into
  a natural-language coaching explanation.

The guiding principle: **the LLM explains ground truth; it never invents it.**

---

## 2. Methodology (agreed and approved in conversation) — read first

These are hard invariants. Do not regress them.

1. **Determinism vs. probability are separated.** Damage, speed and “what actually
   happened” are deterministic and come from the engine/log. Sets/threats/synergy
   are probabilistic and come from Chaos/Smogon stats. Never mix the two sources
   of authority.
2. **The battle log is ground truth for ordering and causality.** The parser
   reconstructs an ordered timeline of actions; the LLM must follow it exactly and
   never assert who moved first / who KO'd whom beyond what the timeline shows. A
   Pokémon with no `used <move>` line before it faints did **not** act.
3. **Speed and damage are field-aware — at the moment of each move.** Weather,
   terrain, Tailwind, Trick Room, Reflect/Light Screen/Aurora Veil, Helping Hand,
   Friend Guard, plus each Pokémon's status, held item (revealed / consumed /
   Tricked) and current HP are taken from the log **as they were when that move
   was used** (the parser stamps a `BattleSnapshot` on every move event) and
   passed to the engine. A later turn's state is never applied to an earlier
   turn. Speed verdicts report *who moves first* (Trick Room inverts) and list
   the conditions.
4. **Per-turn verification (feedback loop).** For every move actually used, the
   engine is re-consulted **turn by turn** under that move's battle state —
   projected damage vs. the logged result (KO text from the target's real HP),
   the speed order, better confirmed moves into **every** opposing target,
   incoming KO threats, and Protect / switch / speed-control options built only
   from moves and Pokémon confirmed this game. The ground truth is not compiled
   once for the whole game.
5. **Matchups are cross-side only.** The 1st AI (selection) may never pair two
   Pokémon from the same roster; a deterministic guardrail enforces it.
6. **Metagame context covers all in-play Pokémon** (both sides), not just the
   leads or the selection focus.
7. **Chaos rating tiers.** Use the **highest** rating cutoff (e.g. `-1760`) as the
   *ideal* suggestion, and also surface the **current** bracket that the match's
   rating falls into (`cutoff <= rating < next cutoff`).
8. **Regulation fallback.** If a species is missing from the newest regulation,
   fall back to older regulations of the **same game** (Champions → Champions;
   base S/V → base S/V), nearest first, up to `PROFESSORVGC_REG_FALLBACK_DEPTH` (3).
9. **Official Smogon data is preferred for strategy prose.** When enabled,
   `@pkmn/smogon` `analyses` provide natural-language strategy; `stats` drive
   team-synergy suggestions; `sets` drive moveset/item/ability/EV advice. Chaos is
   the fallback. Everything degrades gracefully if the network is down.
10. **No placeholder code.** Every file is complete and runnable. Typed contracts
    (Pydantic v2) at every boundary. Tests accompany features.

---

## 3. Architecture & layers (Clean Architecture + SOLID)

Dependencies point inward. Business code depends only on **Protocols** in
`src/domain/interfaces.py`; concrete adapters are injected at the single
composition root `src/services/container.py`.

| Layer | Package | Rule |
|-------|---------|------|
| Domain (core) | `src/domain` | Pydantic models, Protocols, exceptions. Zero framework deps. |
| Adapters (infra) | `src/adapters` | Chaos, Showdown parser, Node calc IPC, Smogon (Chaos-derived + official), OpenAI/Gemini, LangChain, memory, generic Node IPC. |
| Services (use cases) | `src/services` | Orchestration (native + LangChain), selection, matchup eval, turn simulator, suggestions, DI container. |
| Presentation | `src/ui` | Streamlit. Pure view — calls a use case, renders a DTO. |
| Polyglot subsystem | `node_calc` | Node workers: `@smogon/calc` (damage/speed) and `@pkmn/smogon` (official data), each behind a stdin/stdout IPC boundary. |

**Ports (Protocols):** `LogParser`, `MetaStatsProvider`, `CalcEngineAdapter`,
`StrategyKnowledgeProvider`, `SmogonSuggestionSource`, `PromptRepository`,
`EmbeddingProvider`, `ConversationMemory`, `LLMProvider`, `SelectionStrategy`,
`AnalysisPipeline`.

**Three interchangeable orchestrators** behind `AnalysisPipeline`:
`AdkAnalysisOrchestrator` (Google ADK, default), `LangChainAnalysisOrchestrator`
(LCEL) and `AnalysisService` (native). All three receive the same injected
`GroundTruthAssembler` (`src/services/ground_truth.py`), which builds the typed
`AnalysisEvidence` once — so switching orchestration never changes a damage roll
(pinned by `tests/test_backend_parity.py`). Each orchestrator only owns how it
runs selection and explanation. The agents' tools are one framework-agnostic
core (`EvidenceTools`) wrapped per framework and injected by the container.
No module in `src/services` or `src/ui` imports `src/adapters`; only the
container does.

---

## 4. Directory map

```
professor-VGC/
├── node_calc/                      # Node subsystems
│   ├── calc_server.js              # @smogon/calc IPC (damage, field-aware speed, moveInfo, formeResolves)
│   ├── smogon_dex_server.js        # @pkmn/smogon IPC (analyses/sets/stats/species)
│   └── src/{calcEngine.js,smogonDex.js}
├── src/
│   ├── domain/{models,interfaces,exceptions,replay_view_models}.py
│   ├── adapters/
│   │   ├── parsers/showdown_parser.py        # input shapes (replay JSON / raw log / team JSON)
│   │   ├── parsers/showdown_log/             # log protocol -> GameState (package: protocol, roster, timeline,
│   │   │                                     #   combatants, field_ledger, state, handlers/, reader)
│   │   ├── parsers/replay_viewer_parser.py   # separate, UI-only parse for the battle panel
│   │   ├── replay_url_fetcher.py             # Showdown replay URL -> replay JSON
│   │   ├── calc/smogon_calc_adapter.py       # Python side of @smogon/calc IPC (CalcField -> engine field)
│   │   ├── chaos/{chaos_repository,chaos_adapter,chaos_tier_index,firestore_chaos_repository,species_normalize}.py
│   │   ├── smogon/{smogon_strategy_adapter,smogon_dex_adapter,composite_strategy,semantic_strategy_retriever,archetype_signals}.py
│   │   ├── llm/{openai_provider,gemini_provider,langchain_provider,adk_provider,base}.py
│   │   ├── llm/{openai,gemini}_embedding_provider.py
│   │   ├── llm/evidence_tools.py             # the agents' 3 tools, framework-agnostic
│   │   ├── llm/{adk_tools,langchain_tools}.py  # thin per-framework wrappers of evidence_tools
│   │   ├── llm/prompts/{*.txt,__init__.py}   # prompt artifacts + FilePromptRepository
│   │   ├── memory/conversation_memory.py
│   │   └── node_ipc.py                        # generic Node IPC client
│   ├── services/
│   │   ├── ground_truth.py                   # GroundTruthAssembler -> AnalysisEvidence (shared by all backends)
│   │   ├── analysis_service.py               # native pipeline
│   │   ├── langchain_orchestrator.py         # LCEL pipeline
│   │   ├── adk_orchestrator.py               # Google ADK pipeline (default)
│   │   ├── selection_service.py + selection_logic.py   # 1st AI + cross-side guard
│   │   ├── matchup_evaluator.py              # deterministic verdicts (field-aware, mirror-safe set index)
│   │   ├── turn_simulator.py                 # per-move ground-truth verification + Protect reads
│   │   ├── battle_moment.py                  # MoveMoment: battle state at one move (field, HP, status, items)
│   │   ├── decision_review.py                # incoming threats + Protect/switch/speed-control options
│   │   ├── battle_context.py                 # rosters/candidates/outcome summary
│   │   ├── concept_tracking.py               # recurring-topic signal from memory
│   │   ├── suggestion_service.py             # improvement intent + sets/stats
│   │   └── container.py                      # composition root (DI) — the only importer of adapters
│   ├── ui/app.py                             # Streamlit entry point (+ theme, landing, loading, battle_panel, results, audio, icons)
│   └── config.py                             # env-driven Settings
├── data/chaos/                     # raw Chaos dumps for the offline Firestore tooling (never edit)
├── data/replays/                   # optional replay dataset
├── sample_data/                    # bundled sample chaos + replay
├── scripts/                        # Firestore migration/sync + faithfulness benchmark
├── tests/                          # pytest (fakes; no network/keys needed)
├── ADR.md                          # architecture decision records
├── DATA.md                         # data layout + tiers + fallback + official Smogon
└── CLAUDE.md                       # this file
```

---

## 5. Configuration (environment, prefix `PROFESSORVGC_`)

Read by `src/config.py` (`Settings`, pydantic-settings; `.env` supported).

| Var | Default | Purpose |
|-----|---------|---------|
| `PROFESSORVGC_ORCHESTRATOR` | `adk` | `adk` (Google Agent Development Kit), `langchain` (LCEL) or `native`. |
| `PROFESSORVGC_DEFAULT_PROVIDER` | `gemini` | `openai` or `gemini` (BYOK). |
| `PROFESSORVGC_OPENAI_API_KEY` / `PROFESSORVGC_GEMINI_API_KEY` | — | Bring your own key. |
| `PROFESSORVGC_OPENAI_MODEL` / `PROFESSORVGC_GEMINI_MODEL` | gpt-4o-mini / gemini-3.5-flash | Model ids. Gemini model is validated at Settings CONSTRUCTION time (a `field_validator`, `src/config.py`) — this project requires 3.5+; an older id fails immediately at app startup, not lazily on first Gemini call. `require_modern_gemini_model` (`src/adapters/llm/base.py`, reusing the same parser) re-checks at every point-of-use as defense in depth against an already-running process holding a stale cached `Settings`. |
| `PROFESSORVGC_REG_FALLBACK_DEPTH` | `3` | Max previous regulations to search. |
| `PROFESSORVGC_CHAOS_TOP_N` | `3` | Top-N kept per category. |
| `PROFESSORVGC_FIRESTORE_PROJECT_ID` / `..._DATABASE_ID` / `..._CHAOS_COLLECTION` / `..._CREDENTIALS_PATH` | — / `(default)` / `chaos_tiers` / — | Firestore is the app's ONLY Chaos data source — no local-file fallback, no config knob to select one (a competition requirement, not a preference; see DATA.md). Credentials path empty = Application Default Credentials. |
| `PROFESSORVGC_USE_SMOGON_DEX` | `false` | Enable official `@pkmn/smogon` analyses/sets/stats. |
| `PROFESSORVGC_SMOGON_DEX_TIMEOUT_SECONDS` | `30` | Timeout for the dex worker. |
| `PROFESSORVGC_USE_SEMANTIC_STRATEGY` | `false` | Rank Smogon analysis passages against the question via embeddings (needs `USE_SMOGON_DEX=true`); see ADR-027. |
| `PROFESSORVGC_OPENAI_EMBEDDING_MODEL` / `PROFESSORVGC_GEMINI_EMBEDDING_MODEL` | text-embedding-3-small / models/text-embedding-004 | Embedding model ids. |
| `PROFESSORVGC_SEMANTIC_STRATEGY_TOP_K` | `3` | Passages kept per species after ranking. |
| `PROFESSORVGC_NODE_BINARY` | `node` | Node executable. |
| `PROFESSORVGC_CALC_GEN` | `9` | Generation. |
| `PROFESSORVGC_CALC_TIMEOUT_SECONDS` | `20` | Calc IPC timeout. |

---

## 6. Data layout (see DATA.md for full detail)

> **⚠️ NEVER edit, delete, "fix", reformat, or otherwise modify any file inside
> `data/chaos/` — for any reason, under any circumstances, even to "correct" a
> value that looks wrong.** These are raw, externally-sourced Smogon Chaos
> usage-stat dumps; this codebase only ever *reads* them (see `ChaosAdapter`/
> `ChaosRepository`). If a Chaos-derived number in the app looks wrong, the bug
> is in how the code parses/uses that file (fix the adapter/service code and
> add a test), or the file itself needs to be *replaced* by the user with a
> fresh dump from Smogon — an AI assistant must never hand-edit the JSON. This
> rule is absolute and does not get relaxed by a specific task's phrasing.

- **Chaos files:** name them `<metagame>-<ratingCutoff>.json`, e.g.
  `gen9championsvgc2026regmb-1760.json`, and drop all tiers in `data/chaos/`.
  The repository selects the *ideal* (max cutoff) and *current* (rating bracket)
  tiers and walks regulation fallback within the same game family.
- **Species name resolution** is case/forme-insensitive and trims trailing forme
  segments progressively (`Raichu-Mega-Y` → `Raichu-Mega` → `Raichu`).
- **Official Smogon (`PROFESSORVGC_USE_SMOGON_DEX=true`):** `analyses/sets/stats` fetched
  at runtime via `@pkmn/smogon`; needs network; falls back to Chaos when down.
- **Empty sections** mean the data isn't in the loaded files — add the right tier
  and/or previous-regulation files. It is a data-availability issue, not a bug.

---

## 7. The analysis pipeline (maps to the drawio flow)

```
replay JSON/log + question
  → LogParser.parse            (clean/determinism: rosters, ordered timeline, field, rating)
  → SelectionStrategy.select   (1st AI, memory-aware, cross-side matchups only)
  → GroundTruthAssembler       (shared by every backend; builds AnalysisEvidence):
      → MetaStatsProvider          (Chaos tiers + reg fallback; covers ALL in-play mons)
      → MatchupEvaluator           (field-aware damage + speed verdicts)
      → TurnReplaySimulator        (per move, at that move's battle state: projected-vs-actual,
                                    speed, alternatives into every target, KO threats,
                                    Protect/switch/speed-control options, Protect reads)
      → StrategyKnowledgeProvider  (official Smogon analyses → Chaos fallback)
      → [if requested] SuggestionService (official sets + usage stats: synergy/adjustments)
  → explanation (2nd AI)       (memory-aware, ground-truth-locked prompt; agent
                                backends may call the read-only EvidenceTools)
  → AnalysisResult             (UI DTO)
```

---

## 8. Determinism & anti-hallucination invariants (guarded by prompts + code)

- Timeline order **is** the move order; the explanation prompt forbids contradicting it.
- Speed verdicts carry `conditions` (Tailwind/paralysis/scarf/Trick Room) and mean
  “moves first”, not raw stat.
- An unrevealed spread is backed off the most-used Chaos spread (never invented);
  a Mega/forme uses its real stats when the engine knows it, otherwise the verdict
  carries a `stat_caveat`. The `description` string states the exact spread used.
- Defensive advice (Protect / switch / speed control) is precomputed in
  `decision_options` from confirmed moves and brought Pokémon only; the prompt
  forbids recommending one that is not listed.
- Fainted-before-acting is explicit; never attribute a KO to such a Pokémon.

---

## 9. Maintainability patterns / how to extend

- **New LLM vendor:** add an adapter in `src/adapters/llm/`, register in
  `Container.build_llm` / `build_chat_model`. Nothing else changes.
- **New calc backend (Rust/HTTP/…):** implement `CalcEngineAdapter`, swap in the
  container. Domain/services untouched (polyglot isolation).
- **New orchestration backend:** implement `AnalysisPipeline` owning only
  selection + explanation; take the shared `GroundTruthAssembler`, a
  `PromptRepository` and (for agents) the injected tools; register in
  `Container.build_pipeline` and add it to `tests/test_backend_parity.py`.
- **New evidence:** add it once in `GroundTruthAssembler`/`AnalysisEvidence`
  (and `build_explanation_context`); every backend gets it.
- **New agent tool:** add a method to `EvidenceTools` (Gemini-safe signature:
  no default values); add its LangChain schema in `langchain_tools.py`.
- **New battle-log fact:** add a handler method to the matching group in
  `src/adapters/parsers/showdown_log/handlers/` (one method per protocol command); if a calc needs it, put it in the per-move
  `BattleSnapshot` and read it through `MoveMoment`.
- **Different meta source/schema:** implement `MetaStatsProvider` (a single
  `build_match_context`) and/or `StrategyKnowledgeProvider`; wire in the container.
- **Node subsystems** talk one JSON line in / one JSON line out via
  `src/adapters/node_ipc.py`. Network calls must return `{ok:false,error}` so the
  Python side can fall back — never let a subsystem crash the pipeline.
- **Style:** complete implementations (no TODOs), full type hints, Google-style
  docstrings, domain-specific exceptions (`ProfessorVGCError` subclasses), prompts in
  `src/adapters/llm/prompts/*.txt` (never hardcoded in logic).
- **Tests:** every feature has pytest coverage using fakes (no network/keys/Node
  required). Run `pytest -q` before committing. Keep the suite green.

---

## 10. Bug-fix & feature log (chronological — the working branch)

1. **fix(parser):** full replay downloads (JSON with a `log` field) pasted as text
   raised “Replay contains no sides/teams”. String path now decodes then delegates
   to the dict path (which reads `log`). Moves attributed to the `|move|` mon.
   Descriptive parser errors + a UI tip.
2. **feat(analysis):** extract the battle **result** (winner, faints, rosters) as
   ground truth; enforce **cross-side** matchups; Chaos name resolution; `DATA.md`.
3. **feat(parser):** ordered **action timeline** to stop hallucinated causality
   (e.g. a mon that fainted before acting is shown as not acting).
4. **feat(context):** metagame context covers **all in-play** Pokémon, not the focus.
5. **feat(calc):** **field-aware** speed/damage — Tailwind, paralysis, Trick Room,
   weather; speed verdict = “moves first” + conditions.
6. **feat(chaos):** rating-tier selection (**ideal** high-elo + **current** bracket)
   and **regulation fallback** (same game, ≤3 regs) + progressive forme resolution.
7. **feat(analysis):** **per-turn** ground-truth verification (projected vs actual
   damage, speed each turn) — the feedback loop.
8. **feat(smogon):** official **`@pkmn/smogon`** `analyses/sets/stats` behind
   `PROFESSORVGC_USE_SMOGON_DEX`, with a composite strategy provider (official → Chaos).
9. **feat(orchestration):** third `AnalysisPipeline` backend, **Google ADK**
   (`AdkAnalysisOrchestrator`), made the **default** `PROFESSORVGC_ORCHESTRATOR`.
   Schema-constrained (`output_schema`) tool-less agent for selection; a bounded
   tool-calling agent for explanation, wired to the same three deterministic
   tools as the LangChain backend (`adk_tools.py` mirrors `langchain_tools.py`).
   Gemini goes through ADK's native model path; OpenAI goes through ADK's own
   documented `LiteLlm` wrapper. `native` and `langchain` remain fully available.
10. **feat(chaos):** optional **Google Cloud Firestore** backend for Chaos
    data (`PROFESSORVGC_CHAOS_BACKEND=local|firestore`, default `local`,
    zero behavior change). Tier/regulation-fallback selection was extracted
    into a storage-agnostic `ChaosTierIndex` shared by both
    `ChaosRepository` (local files) and the new `FirestoreChaosRepository`;
    `ChaosAdapter`/`ChaosStrategyAdapter` now accept either via a
    `ChaosRepositoryLike` Protocol, injected once and shared between them
    by `Container` (halves reads vs. each building its own). Storage layout:
    one tiny doc per tier, one doc PER SPECIES (keyed by a normalized id) so
    a lookup is always a single direct read, never a collection scan or a
    whole-tier download — see `firestore_chaos_repository.py`'s docstring.
    `scripts/migrate_chaos_to_firestore.py` populates it from the existing
    local files (read-only, never edits them).
11. **feat(competition):** two hard guarantees, both enforced in code, not
    just config defaults: (a) `Settings.gemini_model` has a `field_validator`
    (`src/config.py`) requiring Gemini 3.5+ — fails at Settings CONSTRUCTION
    time (app startup), before any provider is even selected;
    `require_modern_gemini_model` (`src/adapters/llm/base.py`) re-checks at
    every point a Gemini client actually gets built, as defense in depth for
    an already-running process holding a stale cached `Settings`. (b) The
    `local`/`PROFESSORVGC_CHAOS_BACKEND` config knob was removed entirely —
    `Container.chaos_repository()` now unconditionally builds a
    `FirestoreChaosRepository`, with no fallback and no way to select local
    files for the live app. `ChaosRepository` (local files) and the
    `data/chaos/` directory remain relevant only to the offline migration/
    sync tooling (`scripts/migrate_chaos_to_firestore.py`,
    `scripts/sync_smogon_chaos_to_firestore.py`, each with its own
    `--source`/CLI-level default, not a `Settings` field anymore) and to
    this project's own fast, network-free tests — never wired into the
    running app itself anymore.
12. **refactor(architecture):** one shared `GroundTruthAssembler` →
    `AnalysisEvidence` for all three backends (parity-tested); one
    framework-agnostic `EvidenceTools` core; `PromptRepository` and
    `SmogonSuggestionSource` ports in the domain (services/UI no longer import
    adapters); frozen value objects; parser, simulator and UI split into
    cohesive modules (ADR-030).
13. **fix(analysis):** per-move battle state — weather wars, status from the
    turn it happened (cures removed), terrain/screens/Helping Hand/Friend
    Guard, revealed/consumed/Tricked items, current HP in KO chances
    (`BattleSnapshot`); weather was previously sent as Showdown ids that
    `@smogon/calc` ignored. Mirror-safe `(player, species)` set index; move
    category/Protect/speed-control from the engine (`moveInfo`) instead of a
    hand-kept list; incoming threats + Protect/switch/speed-control options and
    retarget alternatives per move (ADR-031).

Test count grew alongside these.

---

## 11. Running & testing

```bash
python -m pip install -r requirements.txt
cd node_calc && npm install && cd ..     # @smogon/calc (+ @pkmn/* if using official data)
cp .env.example .env                     # fill a provider key; optionally enable smogon dex
pytest -q                                # all green; no network or keys required (Node tests skip without Node)
mypy src                                 # strict; CI runs both on Python 3.10 and 3.12
streamlit run src/ui/app.py
# Validation against REAL games (ADR-033):
python -m scripts.faithfulness_benchmark.replay_corpus --count 40          # public replays -> data/replays/cache
python -m scripts.faithfulness_benchmark.run_engine_calibration --chaos local   # projections vs real damage, no LLM
python -m scripts.faithfulness_benchmark.run_log_grounded --provider openai     # AI claims vs real damage (API calls)
# Node engine smoke test:
cd node_calc && npm run smoke
```

---

## 12. Environment constraints & known limitations

- **Official Smogon data needs network at runtime** (`data.pkmn.cc`). If blocked,
  the app falls back to the Chaos data (read from Firestore) automatically.
- **Projected per-turn damage** uses the revealed set where the log reveals it
  and the most-used Chaos spread otherwise; treat it as a baseline, not the exact
  roll (the prompt already frames it this way).
- **Tailwind / Trick Room** are tracked per turn (a Tailwind set mid-turn counts
  for that whole turn); weather, terrain, screens, status, items and HP are exact
  per move.
- **Projected damage vs. reality** (ADR-033): on 40 real Reg M-B games the
  projected range contained the real damage for only ~40% of non-KO hits —
  unrevealed sets differ from the most-used Chaos set. Treat projections as
  a baseline; the log's observed damage is what happened. Correctness claims
  about the AI must come from `run_log_grounded.py`, not the projection-based
  `run.py` (circular).
- **Switch options** assume the switch-in takes the threat's strongest confirmed
  hit; they do not model the opponent re-targeting.
- **Empty meta/strategy sections** = missing Chaos data in Firestore (tier or
  previous regulation), not a code bug — load it with the scripts in DATA.md.
- The tier files shipped under `data/chaos/` are **samples** to demonstrate the
  mechanism; replace them with the real Smogon Chaos dumps for the format.

---

## 13. Contributing, branches and releases

Human contributors and AI assistants follow the same flow
(`CONTRIBUTING.md`): sync `master` (`git pull --ff-only`), branch as
`<type>/<short-kebab-description>` (`feat/`, `fix/`, `refactor/`, `test/`,
`docs/`, `chore/`, `ci/`), Conventional Commit messages, `git pull --rebase
origin master` again before opening the PR, one concern per PR, squash-merge
only after CI (pytest on 3.10/3.12, Node tests, `mypy --strict`) is green.
Never force-push `master`, never bypass checks. Security reports go through
`SECURITY.md`. Deployment is the `Dockerfile` (Cloud Run); there are no
patch/bundle handoff scripts any more.
