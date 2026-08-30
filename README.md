# ProfessorVGC

[![CI](https://github.com/Huarada/professor-VGC/actions/workflows/ci.yml/badge.svg)](https://github.com/Huarada/professor-VGC/actions/workflows/ci.yml)

A **Clean-Architecture** engine that analyzes Pokemon VGC battles by combining a
**deterministic** damage-calc layer (`@smogon/calc` via Node IPC) with a
**probabilistic** metagame feed (Smogon *Chaos* usage stats) and two LLM stages,
orchestrated with **Google ADK** (Agent Development Kit) by default — **LangChain**
and a hand-rolled **native** pipeline remain available as interchangeable
backends — for selection and natural-language explainability (bring your own key).

## All Things Agentic Hackathon — pre-existing work disclosure

This repository was created **August 26, 2026** (GitHub's own repository
timestamp — verifiable via the GitHub API, not editable after the fact),
inside the Contest's Submission Period (August 3–31, 2026). Its deterministic
battle-analysis core (the Showdown replay parser, the `@smogon/calc`
damage-engine integration, base Chaos usage-stats lookup) originated in an
earlier personal project first written **July 23, 2026** — never itself an
agentic system, with no LLM orchestration, no autonomous agent, and none of
Gemini, Google ADK, or any Google Cloud infrastructure integrated. That
pre-existing work is disclosed here and incorporated the same way a starter
template would be.

**Everything the Contest requires was newly designed and built during the
Submission Period**, with git history as evidence: the Google ADK agent
orchestration (default backend, [`94a074b`](https://github.com/Huarada/professor-VGC/commit/94a074b),
2026-08-25), the Google Cloud Firestore metagame-memory backend
([`f871f80`](https://github.com/Huarada/professor-VGC/commit/f871f80), 2026-08-26), the enforced
Gemini 3.5+ requirement ([`8320737`](https://github.com/Huarada/professor-VGC/commit/8320737),
2026-08-27), and the Cloud Run deployment
([`e1e57af`](https://github.com/Huarada/professor-VGC/commit/e1e57af), 2026-08-28). Full detail:
[`HACKATHON_DISCLOSURE.md`](HACKATHON_DISCLOSURE.md).

## Pipeline (maps 1:1 to the flow diagram)

```
replay JSON + question
   │  LogParser.parse                       ← "CLEANUP / FILTER (DETERMINISM)"
   ▼
GameState (species involved)
   │  SelectionStrategy / LCEL selection    ← 1st AI  (needs memory)
   ▼
SelectionPlan (focus species + matchups)
   ├─▶ MetaStatsProvider (Chaos)            ← EV/IV/items/abilities/counters
   ├─▶ CalcEngineAdapter (Node @smogon/calc)← "Determinism": damage + speed
   └─▶ StrategyKnowledgeProvider (Smogon)   ← archetypes + teammates
   │  LLM explanation (LCEL)                ← 2nd AI  (needs memory)
   ▼
AnalysisResult  ─────────────────────────▶ green "Answer OUTPUT" node
```

## Layers

| Layer | Package | Rule |
|-------|---------|------|
| Domain (core) | `src/domain` | Pydantic models, Protocols, exceptions. Zero external deps. |
| Adapters (infra) | `src/adapters` | Chaos, Showdown parser, Node calc IPC, Smogon strategy, OpenAI/Gemini, LangChain, memory. |
| Services (use cases) | `src/services` | Orchestration (native + LangChain LCEL) + DI composition root. Depends only on Protocols. |
| Presentation | `src/ui` | Streamlit. Pure view — calls a use case, renders a DTO. |
| Polyglot subsystem | `node_calc` | Node worker exposing `@smogon/calc` over stdin/stdout. |

Dependency Inversion is enforced everywhere: services import from
`src.domain.interfaces` only. Concrete adapters are wired in
`src/services/container.py` and nowhere else.

## Orchestration (Google ADK / LangChain / native)

The orchestration technology is a **pluggable infrastructure choice** behind the
`AnalysisPipeline` port. Three interchangeable backends implement it identically:

| Backend | Class | How the LLM stages run |
|---------|-------|------------------------|
| `adk` (default) | `AdkAnalysisOrchestrator` | Google **ADK** `LlmAgent`s: a schema-constrained (`output_schema`), tool-less agent for selection; a bounded tool-calling agent for explanation. |
| `langchain` | `LangChainAnalysisOrchestrator` | **LCEL** chains: `RunnableLambda(build_messages) \| chat_model \| JsonOutputParser` for selection, a `langchain.agents.create_agent` tool-calling agent for explanation. |
| `native` | `AnalysisService` | Direct provider SDK calls through the `LLMProvider` port. |

Select at runtime via `PROFESSORVGC_ORCHESTRATOR=adk|langchain|native` or the UI
dropdown. The LLM *vendor* (`PROFESSORVGC_DEFAULT_PROVIDER=openai|gemini`) is a
fully independent choice from the orchestrator — any backend works with either key.

Every orchestration framework is confined to the adapters/services layers and
never appears in a domain signature:

- `src/adapters/llm/adk_provider.py` — `build_adk_model`, a BYOK factory
  returning the `model=` argument for an ADK `Agent`: a plain Gemini model-id
  string (ADK's native path, no extra dependency) for `gemini`, or ADK's own
  documented `LiteLlm` wrapper (needs the separate `litellm` package) for `openai`.
- `src/adapters/llm/adk_tools.py` — the calc/Chaos/strategy ports exposed as
  plain, type-hinted functions (ADK auto-wraps a function's signature + Google-
  style docstring into a tool) for the explanation agent's interactive
  "what-if" follow-ups. Every parameter is required (no defaults): the Gemini
  API's function-calling schema rejects a declaration that has one.
- `src/services/adk_orchestrator.py` — the ADK pipeline. Conversation history
  is rendered into the prompt text (like the other two backends, via the
  project's own `ConversationMemory` port) rather than relying on ADK's own
  session/event replay, so memory behavior stays identical across all three
  backends; each `analyze()` call runs on a fresh, disposable ADK session.
- `src/adapters/llm/langchain_provider.py` — `LangChainLLMProvider` (implements
  the domain `LLMProvider` on any `BaseChatModel`) + a BYOK `build_chat_model`
  factory for `ChatOpenAI` / `ChatGoogleGenerativeAI`.
- `src/adapters/llm/langchain_tools.py` — the calc/Chaos/strategy ports exposed
  as `StructuredTool`s, ready for a `langchain.agents.create_agent` tool-calling
  agent for interactive "what-if" follow-ups.
- `src/services/langchain_orchestrator.py` — the LCEL pipeline.

All three backends share the deterministic core (`MatchupEvaluator`,
`selection_logic`), so switching orchestration technology never changes a single
damage roll — the LLM only ever explains ground-truth numbers, never invents them.

## Faithfulness benchmark — grounding measured, not asserted

An atomic-claim-verification benchmark (extract → verify → rate — the same
shape RAG faithfulness evaluation uses) measures what fraction of the LLM's
factual claims match deterministic ground truth: **Condition A** (the real
pipeline) vs **Condition B** (the same LLM given only the raw Showdown log,
no grounding at all). Headline metric: `damage_range` claims — the one
category where the real `@smogon/calc` engine and real Chaos-derived EV/
nature spreads do genuine, otherwise-unavailable work.

| Provider / model | Orchestrator | Grounded rate | Naive rate | Fisher odds ratio | p (two-sided) |
|---|---|---|---|---|---|
| OpenAI gpt-4o-mini | native | 92.0% | 12.6% | 80.0 | <0.0001 |
| OpenAI gpt-4o-mini | adk | 72.2% | 14.1% | 15.23 | <0.0001 |
| OpenAI gpt-4o-mini | langchain | 73.8% | 14.1% | 17.14 | <0.0001 |
| **Gemini 3.5-flash** | **adk (competition default)** | **73.3%** | **11.0%** | **22.27** | **2.70e-15** |

The grounding effect holds across LLM vendor and orchestration framework
alike — every combination tested clears statistical significance by an
enormous margin (odds ratio 15–80x). Full methodology, every round's raw
numbers, and an honesty audit for unintentional bias toward the grounded
condition: [`scripts/faithfulness_benchmark/README.md`](scripts/faithfulness_benchmark/README.md).

## Setup

```bash
# 1. Python core
python -m pip install -r requirements.txt
# — or, equivalently, the packaging-metadata path (also gives mypy/pytest):
#   python -m pip install -e ".[dev]"

# 2. Node calc engine
cd node_calc && npm install && cd ..

# 3. Configuration (bring your own key)
cp .env.example .env      # then fill PROFESSORVGC_OPENAI_API_KEY or PROFESSORVGC_GEMINI_API_KEY
```

Verify the Node engine standalone:

```bash
cd node_calc && npm run smoke      # prints a Garchomp→Sinistcha calc as JSON
```

## Run

```bash
streamlit run src/ui/app.py
```

## Customizing the UI (optional)

The default theme (light "battle notebook" sky-blue, no external assets)
works with nothing configured. To customize it without touching code: drop
an image into [`src/ui/assets/backgrounds/`](src/ui/assets/backgrounds/)
(`page.*` for the whole app, `battle-stage.*` for the battle panel) or an
audio file into [`src/ui/assets/audio/`](src/ui/assets/audio/)
(`theme.mp3`/`.mp4`/`.m4a`/`.wav`/`.ogg`, with its own on/off toggle in the
sidebar). Each folder's own README covers size/format guidance.

## Official Smogon data (optional)

Set `PROFESSORVGC_USE_SMOGON_DEX=true` to pull Smogon's official analyses/sets/stats via
`@pkmn/smogon` at runtime (Node deps installed by `npm install` in `node_calc`).
Strategies then use official analyses (with the local Chaos data as fallback), and
team-improvement questions use official sets + usage stats. See DATA.md.

### Semantic strategy retrieval (optional, needs the above)

Set `PROFESSORVGC_USE_SEMANTIC_STRATEGY=true` to rank Smogon's official analysis
passages (one per format's overview, one per set's own description) against the
user's actual question via embeddings, instead of always using the first available
format's overview verbatim. Reuses whichever LLM provider key is already
configured — no separate credential. A lightweight, dependency-free
implementation (no vector database): embeddings + in-memory cosine similarity
over the handful of paragraphs Smogon actually publishes per species. See
ADR-027 for the full design and why this — and not the conversation
memory — is where retrieval genuinely earns its keep in this project.

## Chaos data

The running app reads Chaos usage stats **exclusively from Google Cloud
Firestore** — set `PROFESSORVGC_FIRESTORE_PROJECT_ID` (and, once, populate the
database — see DATA.md's "Firestore: the app's ONLY Chaos data source"
section for the full setup). There is no local-file fallback: this is a
deliberate requirement, not a default.

Populate Firestore from a real Smogon Chaos dump (a trimmed sample lives in
`sample_data/`) with:

```bash
python -m scripts.migrate_chaos_to_firestore --project-id YOUR_PROJECT
# or, directly from Smogon's own site, no local file needed:
python -m scripts.sync_smogon_chaos_to_firestore --project-id YOUR_PROJECT
```

The adapter converts Chaos's `Nature:e/e/e/e/e/e` (EVs ÷ 8) encoding back to
real 0-252 EVs and keeps only the Top-N per category to stay ~1 KB per prompt
— identical logic whether the raw JSON was loaded via either script above.

## Tests

```bash
pytest             # in-memory fakes; no Node or API keys REQUIRED to pass
mypy src           # strict — the same check CI runs on every PR
```

Neither Node nor an LLM/langchain install is required for a green `pytest`
run — every test either uses an in-memory fake or skips itself cleanly
(`pytest.skip`/`pytest.importorskip`, never an error) when that specific
piece of optional infrastructure (the Node calc engine, `langchain_core`)
isn't present. If Node *is* set up (step 2 above), the calc-engine
integration tests (`test_calc_engine_*.py`) run for real against the
actual `@smogon/calc` subprocess instead of skipping — this is what
[`.github/workflows/ci.yml`](.github/workflows/ci.yml) does on every PR.

## Extending

- **New LLM vendor:** add an adapter in `src/adapters/llm/`, register it in
  `Container.build_llm` / `build_chat_model`. Nothing else changes.
- **New calc backend (Rust/HTTP/...):** implement `CalcEngineAdapter` and swap
  it in the container. Domain and services are untouched.
- **New orchestration backend:** implement `AnalysisPipeline` (like
  `AdkAnalysisOrchestrator`/`LangChainAnalysisOrchestrator`) and register it in
  `Container.build_pipeline`.
- **New Chaos storage backend:** implement `ChaosRepositoryLike` (like
  `FirestoreChaosRepository` — reuses the shared `ChaosTierIndex` for tier/
  regulation-fallback selection, only "where the JSON bytes come from" is
  backend-specific) and wire it in `Container.chaos_repository`.
  `ChaosAdapter`/`ChaosStrategyAdapter` never change.
