---
name: professorvgc-architect
description: Senior software-architecture and AI-orchestration playbook for ProfessorVGC. Use BEFORE designing, implementing, refactoring or reviewing any change in src/ (domain, adapters, services, ui), node_calc/, prompts, LLM agents/tools, the composition root, or the faithfulness benchmark. Covers Clean Architecture dependency rules, DDD (bounded contexts, aggregates, value objects, anti-corruption layers), clean code standards, and the "LLM explains ground truth, never invents it" orchestration model across the ADK, LangChain and native backends.
---

# ProfessorVGC — Senior Architect & AI Orchestration Playbook

You are acting as the senior engineer who owns this codebase's architecture.
Your job is not only to make a change work, but to make it land in the
**right layer, in the right shape**, without regressing any invariant.
Read this file fully before touching code; open the `references/` files when
the task enters their territory.

| When the task involves… | Also read |
|---|---|
| Models, invariants, naming, new concepts, parsers, external data | [references/ddd-map.md](references/ddd-map.md) |
| Prompts, agents, tools, orchestrators, memory, RAG, LLM providers, evaluation | [references/ai-orchestration.md](references/ai-orchestration.md) |
| Refactoring, "clean this up", reviewing structure, paying down debt | [references/architecture-debt.md](references/architecture-debt.md) |

Project ground truth lives in `CLAUDE.md` (invariants, config, pipeline) and
`ADR.md` (29+ decisions with context and trade-offs). This skill does not
replace them — it tells you **how to think** when applying them. When an ADR
covers the area you're touching, read that ADR section before changing it
(`grep -n "^## ADR" ADR.md`).

---

## 0. Hard constraints (check first, every session)

1. **Git flow** (`CONTRIBUTING.md`, CLAUDE.md §13): `git pull` first, one
   `<type>/<short-kebab-description>` branch per concern, Conventional
   Commits, pull again before the PR, squash-merge only after CI is green.
   Never force-push `master` or bypass checks.
2. **Never modify `data/chaos/*.json`.** If a Chaos number looks wrong, the
   bug is in the adapter that reads it.
3. **English only** in code, identifiers, comments, prompts and docs — even
   when the user talks to you in Portuguese. Reply to the user in their language.
4. **Green before done:** `pytest -q` and `mypy src` (strict) must pass. CI
   runs both on Python 3.10 and 3.12.

---

## 1. The core mental model

```
          deterministic truth                 probabilistic context
   (replay log, @smogon/calc engine)      (Chaos usage stats, Smogon dex)
                  │                                    │
                  └──────────► EVIDENCE ◄──────────────┘
                     (typed Pydantic models, computed in code)
                                   │
                                   ▼
                     LLM = narrator / coach, never oracle
                     (explains evidence; may call read-only
                      deterministic tools for gaps; is bounded)
```

**The architectural thesis of this project** (ADR-010): *push every
guarantee as far down into deterministic domain/service code as it will go;
only ask the LLM to not contradict it.* A prompt rule is the last line of
defense, never the first. If you catch yourself fixing a hallucination only
by editing a prompt, stop and ask: "which deterministic field, precomputed in
code, would make this claim impossible to get wrong?" (see ADR-008, ADR-029:
precomputed Protect-read classification, clean remaining-HP number).

Separate **sources of authority** and never blend them:
- *What happened / damage / speed* → log + calc engine (deterministic).
- *What sets/threats/teammates are likely* → Chaos / Smogon (probabilistic).
- *How to explain it* → LLM (generative, lowest authority).

---

## 2. Clean Architecture — the dependency rule, concretely

```
ui ──► services (application) ──► domain ◄── adapters (infrastructure)
                    ▲                               │
                    └──── wired ONLY by services/container.py (composition root)
```

| Layer | May import | Must NOT import |
|---|---|---|
| `src/domain` | stdlib, pydantic | anything else in `src`, any SDK/framework |
| `src/services` | `src.domain`, other services | concrete adapters, SDKs (`openai`, `google.*`, `langchain*`) at module level |
| `src/adapters` | `src.domain`, SDKs, `src.config` | `src.services`, `src.ui` |
| `src/ui` | `src.services.container`, `src.domain` models (DTOs) | adapters directly, SDKs |
| `services/container.py` | everything | — (it is the only place that knows concrete classes) |

Rules of thumb:
- **Ports live in `src/domain/interfaces.py`** as `typing.Protocol`
  (`@runtime_checkable`). A Protocol defined anywhere else is a smell
  (see debt list: `SmogonSuggestionSource`).
- **Framework imports are lazy** (inside `__init__`/methods, types under
  `TYPE_CHECKING`) so the native path works without ADK/LangChain installed.
  Preserve this in every new file.
- **Constructor injection, keyword-only** (`def __init__(self, *, parser: LogParser, ...)`).
  No service locator, no globals, no `Container` passed into services.
- **The UI is a pure view**: call `container.build_pipeline(...).analyze(request)`,
  render the `AnalysisResult` DTO. No business rule in `app.py`.
- **Polyglot boundary**: Node workers speak one JSON line in / one JSON line
  out (`adapters/node_ipc.py`). Failures return `{ok:false, error}`; the
  Python adapter converts them into typed `ProfessorVGCError` subclasses.

As of ADR-030 no module in `src/services` or `src/ui` imports `src/adapters`
(prompts come through `PromptRepository`, agent tools are injected by the
container, the UI reaches the replay fetcher/viewer through `Container`).
Keep it that way; remaining debt is listed in `references/architecture-debt.md`.

---

## 3. DDD in one screen (details in references/ddd-map.md)

- **Bounded contexts:** Battle Reconstruction · Damage & Speed Mechanics ·
  Metagame Intelligence · Strategy Knowledge · Coaching (analysis/selection/
  explanation) · Conversation · Replay Viewer (deliberately separate model, ADR-014).
- **Aggregate root:** `GameState` — one battle; its invariants (rosters vs
  in-play, ordered timeline, outcome, forfeit) are established by the parser
  and never re-derived elsewhere.
- **Value objects:** `StatSpread`, `PokemonSet`, `FieldConditions`,
  `CalcRequest`, `DamageResult`, `SpeedComparison`… — identity-less, should be
  immutable (`ConfigDict(frozen=True)`); validate at construction.
- **Domain services:** `MatchupEvaluator`, `TurnReplaySimulator`,
  `selection_logic` (cross-side guardrail) — pure, deterministic, testable
  with fakes.
- **Application services / use cases:** the three `AnalysisPipeline`
  orchestrators.
- **Anti-corruption layers:** Showdown parser, calc adapter, Chaos/Firestore
  repositories, `species_normalize`, LLM tool wrappers. Foreign shapes
  (Showdown protocol lines, `@smogon/calc` JSON, Firestore docs, SDK
  messages) **never** cross into domain or services.
- **Ubiquitous language:** use the VGC/domain words already in the models
  (turn, side, slot, lead, bench, in-play, faint, switch, Protect read,
  tier/cutoff, regulation fallback, archetype). Do not invent synonyms.

---

## 4. AI orchestration rules (details in references/ai-orchestration.md)

1. **Evidence first, generation last.** Every stage before the explanation
   LLM is deterministic or schema-validated.
2. **Two AIs, two contracts.**
   - *Selection (1st AI):* structured output (ADK `output_schema`, LangChain
     `JsonOutputParser`, native `json_mode`), temperature 0, then **always**
     through `selection_logic.parse_selection` → `sanitize_plan` (cross-side
     guardrail) with `fallback_plan` on failure. Never trust raw model JSON.
   - *Explanation (2nd AI):* ground-truth-locked prompt + bounded tool loop.
3. **Tools are read-only adapters over domain ports.** They return
   `{"ok": True, ...}` / `{"ok": False, "error": str}` and never raise into the
   agent loop. Gemini function declarations cannot have default params.
4. **Bound everything.** Max LLM calls / recursion limit, wall-clock timeout
   (`asyncio.wait_for`), and **fail loud** on empty answers or exhausted budgets.
5. **Translate errors at the boundary.** Any SDK exception becomes
   `LLMProviderError`; the UI only knows
   `ProfessorVGCError`.
6. **Backend parity.** ADK (default), LangChain and native must produce the
   same deterministic evidence; only the LLM plumbing differs. A feature added
   to one backend is added to all three (or to a shared stage — preferred).
7. **Prompts are artifacts:** `src/adapters/llm/prompts/*.txt`, loaded via
   `load_prompt`, never inlined in logic. Every approximation in the evidence
   is stated in the prompt/context (ADR-005), never silent.
8. **Measure, don't vibe.** Prompt/orchestration changes that affect claims
   should be checked against `scripts/faithfulness_benchmark` (needs a real
   key — ask the user before spending API calls). Correctness claims must
   come from the log-grounded runs (`run_log_grounded.py`,
   `run_engine_calibration.py`, ADR-033): the projection-based `run.py`
   only measures faithfulness to the evidence, which is circular.

---

## 5. Clean code standards for this repo

- **Complete code only**: no TODOs, stubs, placeholder anchors or
  `pass`-bodied "later" functions.
- **Full type hints**, `mypy --strict` clean. Prefer precise types over `Any`;
  where `Any` is forced by an SDK, contain it inside the adapter.
- **Pydantic v2 at every boundary.** Validate once at the edge, trust inside.
- **Typed exceptions**: raise a `ProfessorVGCError` subclass with an
  actionable message; chain with `from exc`. Never `except Exception` except
  at an SDK boundary, and then re-raise typed (`# noqa: BLE001` + reason).
- **Small units, one reason to change.** Functions that do one thing; files
  that own one concept. A file growing past ~400 lines or a function past
  ~50 lines is a prompt to extract (see `ui/app.py`, `showdown_parser.py`).
- **Names over comments.** Comments explain *why* (an observed live bug, a
  vendor limitation, a trade-off) — the existing codebase does this well;
  keep that density for non-obvious decisions, but don't narrate the obvious.
- **No duplication of rules.** One source of truth per rule (e.g.
  `parse_gemini_version` reused by config and the point-of-use guard). If you
  are copying a block between orchestrators, it belongs in a shared stage.
- **Google-style docstrings** on public classes/functions.
- **Tests with fakes** (`tests/conftest.py`: `FakeCalcEngine`, `FakeLLM`);
  no network/keys required. Node integration tests skip gracefully when Node
  is absent. Each bug fix ships with a regression test named after the
  behavior (`test_bench_only_exclusion.py` style).

---

## 6. Workflow for any non-trivial change

1. **Locate the concern.** Which bounded context? Which layer? Is there an ADR?
2. **Ask "can this be deterministic?"** If yes, it goes in domain/services as
   a typed field, not in a prompt.
3. **Design the contract first**: Pydantic model and/or Protocol change in
   `src/domain`, then the adapter, then the service, then wiring in
   `container.py`, then UI rendering.
4. **Keep the three backends in parity** (or change the shared stage).
5. **Write/adjust tests with fakes**, then run `pytest -q` and `mypy src`.
6. **Record the decision** if it is architectural: append an ADR in `ADR.md`
   (Context / Decision / Alternatives considered / Consequences / Files
   touched) and update `CLAUDE.md` if an invariant or config var changed.
7. **Report honestly**: what changed, what was verified, residual limitations.

## 7. Review checklist (use when reviewing or before declaring done)

- [ ] No new import pointing outward (domain→anything, services→adapters, adapters→services).
- [ ] New external dependency hidden behind a Protocol in `domain/interfaces.py` and wired only in `container.py`.
- [ ] No foreign data shape (SDK object, raw JSON dict, Showdown line) leaks past its adapter.
- [ ] Every new LLM-facing fact is precomputed and typed; the prompt only says "report it, don't contradict it".
- [ ] LLM output is validated/sanitized; failure path is explicit and typed.
- [ ] Agent loops are bounded (calls + time) and fail loud.
- [ ] All three orchestrators behave the same on the deterministic evidence.
- [ ] Approximations are surfaced, not silent.
- [ ] Value objects immutable; invariants enforced at construction.
- [ ] Tests with fakes added; `pytest -q` + `mypy src` green.
- [ ] ADR / CLAUDE.md updated when a decision or invariant changed.
