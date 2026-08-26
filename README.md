# ProfessorVGC

[![CI](https://github.com/Huarada/oracle-vgc/actions/workflows/ci.yml/badge.svg)](https://github.com/Huarada/oracle-vgc/actions/workflows/ci.yml)

A **Clean-Architecture** engine that analyzes Pokemon VGC battles by combining a
**deterministic** damage-calc layer (`@smogon/calc` via Node IPC) with a
**probabilistic** metagame feed (Smogon *Chaos* usage stats) and two LLM stages,
orchestrated with **LangChain**, for selection and natural-language explainability
(bring your own key).

## Pipeline (maps 1:1 to the flow diagram)

```
replay JSON + question
   │  LogParser.parse                       ← "LIMPEZA / FILTRAR (DETERMINISMO)"
   ▼
GameState (species involved)
   │  SelectionStrategy / LCEL selection    ← 1ª IA  (needs memory)
   ▼
SelectionPlan (focus species + matchups)
   ├─▶ MetaStatsProvider (Chaos)            ← EV/IV/items/abilities/counters
   ├─▶ CalcEngineAdapter (Node @smogon/calc)← "Determinismo": damage + speed
   └─▶ StrategyKnowledgeProvider (Smogon)   ← archetypes + teammates
   │  LLM explanation (LCEL)                ← 2ª IA  (needs memory)
   ▼
AnalysisResult  ─────────────────────────▶ green "Resposta OUTPUT" node
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

## Orchestration (LangChain)

The orchestration technology is a **pluggable infrastructure choice** behind the
`AnalysisPipeline` port. Two interchangeable backends implement it identically:

| Backend | Class | How the LLM stages run |
|---------|-------|------------------------|
| `langchain` (default) | `LangChainAnalysisOrchestrator` | **LCEL** chains: `RunnableLambda(build_messages) \| chat_model \| JsonOutputParser` for selection, `… \| StrOutputParser` for explanation. |
| `native` | `AnalysisService` | Direct provider SDK calls through the `LLMProvider` port. |

Select at runtime via `PROFESSORVGC_ORCHESTRATOR=langchain|native` or the UI dropdown.

LangChain is confined to the adapters/services layers and never appears in a
domain signature:

- `src/adapters/llm/langchain_provider.py` — `LangChainLLMProvider` (implements
  the domain `LLMProvider` on any `BaseChatModel`) + a BYOK `build_chat_model`
  factory for `ChatOpenAI` / `ChatGoogleGenerativeAI`.
- `src/adapters/llm/langchain_tools.py` — the calc/Chaos/strategy ports exposed
  as `StructuredTool`s, ready for a `langchain.agents.create_agent` tool-calling
  agent for interactive "what-if" follow-ups.
- `src/services/langchain_orchestrator.py` — the LCEL pipeline.

Both backends share the deterministic core (`MatchupEvaluator`,
`selection_logic`), so switching orchestration technology never changes a single
damage roll — the LLM only ever explains ground-truth numbers, never invents them.

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

## Design

The app's visual language — a light "battle notebook" sky-blue theme (Lora
display / Nunito body / Space Mono label typography, frosted glass cards,
color-coded accents) — is ported from a Figma Make design prototype; see
ADR-026 for the decision and what was and wasn't carried over (notably: no
JS-driven canvas background, since Streamlit's `st.markdown` HTML can't
reliably execute injected `<script>` tags — a static CSS gradient stands in
for it). `.streamlit/config.toml` drives Streamlit's own native widget
theming; `src/ui/app.py`'s `_DESIGN_TOKENS_CSS`/`_COMPONENT_THEME_CSS`
carry the rest.

The app's backdrop (the whole page, not just the battle panel) is a built-in
CSS wallpaper — a sky-blue gradient with a faint graph-paper grid, no
external image, so it can never fail to load. It's a fixed background
(`background-attachment: fixed`), which is what gives the parallax feel: the
wallpaper stays put in the viewport while the Q&A/analysis content scrolls
over it. The battle-replay panel (the field the sprites stand on) keeps its
own, deliberately darker "genetics lab" gradient backdrop — a different room
of the same facility, unaffected by the page theme around it.

**To use your own image instead, no code edit needed** — drop a file into
[`src/ui/assets/backgrounds/`](src/ui/assets/backgrounds/):

| Drop this file | Replaces |
|---|---|
| `page.jpg` / `.jpeg` / `.png` / `.webp` | The whole app's backdrop |
| `battle-stage.jpg` / `.jpeg` / `.png` / `.webp` | The battle panel's backdrop |

It's picked up automatically on the next page load, base64-embedded behind a
legibility scrim (light-blue for the page, dark for the battle stage — each
matched to that backdrop's own text color) and the same
`background-attachment: fixed`/`background-size: cover` rules the built-in
gradient uses — see that folder's own README for size/format guidance.
Nobody has dropped an image in by default, which is a working state, not a
gap: the built-in gradients (`_LAB_BACKGROUND_CSS_DEFAULT` /
`_PAGE_BACKGROUND_CSS_DEFAULT` in [`src/ui/app.py`](src/ui/app.py)) are
complete backdrops on their own.

## Adding background music (optional)

Drop an audio file into [`src/ui/assets/audio/`](src/ui/assets/audio/),
named `theme.mp3` / `theme.mp4` / `theme.m4a` / `theme.wav` / `theme.ogg` —
a small player, with its own "Background music" on/off checkbox in the
sidebar, appears automatically, no code edit needed. Unchecking it removes
the player entirely, not just pauses it. It requests autoplay with sound
on page load and loops once started — a *request*, not a guarantee:
browsers enforce their own autoplay policy and commonly block it on a
visitor's first visit regardless, in which case the visible player
controls are how they start it manually. Nobody has dropped a track in by
default, which is a working
state — no player or checkbox at all, not a gap; see that folder's own
README for size guidance and how to change the loop/autoplay defaults.

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

Point `PROFESSORVGC_CHAOS_DATA_PATH` at a real Smogon Chaos dump, e.g.
`gen9championsvgc2026regmb.json`. A trimmed sample lives in `sample_data/`.
The adapter converts Chaos's `Nature:e/e/e/e/e/e` (EVs ÷ 8) encoding back to
real 0-252 EVs and keeps only the Top-N per category to stay ~1 KB per prompt.

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
  `LangChainAnalysisOrchestrator`) and register it in `Container.build_pipeline`.
