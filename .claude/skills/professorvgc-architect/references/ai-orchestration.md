# AI orchestration — patterns and rules for ProfessorVGC

The project is a **grounded-generation system**: deterministic evidence is
computed first, an LLM explains it. This file is the senior-level playbook
for anything touching prompts, agents, tools, providers, memory, RAG or
evaluation.

---

## 1. Pipeline topology

```
AnalysisRequest
  │
  ├─ ConversationMemory.load(session_id)
  ├─ LogParser.parse                      deterministic   → GameState
  ├─ Selection (1st AI)                   LLM, structured → SelectionPlan
  │     └─ parse_selection → sanitize_plan (cross-side) → fallback_plan
  ├─ MetaStatsProvider.build_match_context  probabilistic → MetaContext (ALL in-play mons)
  ├─ MatchupEvaluator.evaluate            deterministic   → MatchupVerdict[]
  ├─ TurnReplaySimulator.simulate         deterministic   → TurnCheck[]
  ├─ TurnReplaySimulator.build_protect_reads              → ProtectRead[]
  ├─ collect_strategies                   knowledge (+opt. RAG) → SmogonStrategy[]
  ├─ [wants_suggestions] build_improvement_context        → suggestions
  ├─ build_explanation_context + recurring_concepts       → evidence dict
  ├─ Explanation (2nd AI)                 LLM + bounded read-only tools → answer
  ├─ ConversationMemory.append(user, assistant)
  └─ AnalysisResult (DTO incl. agent_tool_calls, provider)
```

Three backends implement it behind `AnalysisPipeline`:
`AdkAnalysisOrchestrator` (default), `LangChainAnalysisOrchestrator`, `AnalysisService`
(native). The faithfulness benchmark showed no significant difference
between them — **because** the evidence is shared. Protect that property.

---

## 2. Principles

### 2.1 Evidence over instruction
Order of preference to stop a wrong claim:
1. Make it **impossible**: compute the fact deterministically and put it in a
   typed field (ADR-008 Protect-read classification, ADR-029 remaining HP number,
   ADR-013 Chaos EV back-fill).
2. Make it **explicit**: surface the approximation/caveat alongside the number (ADR-005).
3. Only then **instruct**: a prompt rule ("report X, don't contradict it").

Never ask the LLM to do arithmetic, ordering, classification or attribution
that code can do.

### 2.2 Structured output is a contract, not a promise
- Ask for structure with the strongest mechanism the backend offers
  (ADK `output_schema`, `JsonOutputParser`, provider `json_mode`).
- **Always** re-validate with the shared `selection_logic` pipeline; enforce
  domain invariants (cross-side matchups, roster membership, `max_matchups`)
  in code; degrade to `fallback_plan` instead of failing the turn.
- Temperature 0 for selection/classification; low (≈0.2) for explanation.

### 2.3 Tools: read-only, typed, non-throwing
- A tool is a thin adapter over a **domain port** (`CalcEngineAdapter`,
  `MetaStatsProvider`, `StrategyKnowledgeProvider`). No business logic inside.
- Return `{"ok": True, **model.model_dump()}` or `{"ok": False, "error": str}`.
  Catch only the port's typed exception; let programming errors surface in tests.
- Tools must be **side-effect free** (no memory writes, no network beyond the port).
- Vendor constraints: Gemini function declarations reject parameter
  defaults → required `str` with "pass \"\" if unknown" in the docstring.
  ADK tools must return `dict`. Docstrings are the tool schema the model
  sees — write them for the model (when to use, arg examples).
- Every invocation is surfaced as `AgentToolInvocation` (tool, args, ok,
  short summary) so the UI can show what the agent consulted.

### 2.4 Bounded agency, loud failure
- Cap iterations (`RunConfig.max_llm_calls` / LangGraph `recursion_limit`)
  **and** wall-clock time (`asyncio.wait_for` around the ADK run).
- An exhausted budget or empty final text is an **error** (`LLMProviderError`),
  never a blank answer (live bug, 2026-08-29).
- Provider retries live in the provider adapter (e.g. ADK `retry_options`),
  not in services.

### 2.5 Error boundary
- Native providers already wrap SDK errors. Framework paths that call raw
  chat models must wrap the call: `except Exception as exc: raise
  LLMProviderError(...) from exc` (ADR-011). The UI renders
  `ProfessorVGCError` only.
- Configuration errors fail **early** (Settings validators) and are
  re-checked at point of use (`require_modern_gemini_model`) — defense in depth.

### 2.6 Memory
- One cross-backend abstraction: `ConversationMemory` (load/append/clear).
- History is rendered into the prompt the same way in every backend; ADK
  uses a disposable session per run purely as plumbing. Do not switch one
  backend to framework-native memory without doing it for all (parity).
- `recurring_concepts(history, question)` is deterministic personalization —
  prefer this style over asking the LLM to "remember".

### 2.7 Retrieval (RAG) scoped correctly
- `SemanticStrategyRetriever` (ADR-027) only **narrows which prose passages**
  become `overview`. It never changes structured fields (sets, archetypes,
  numbers). Retrieval decorates a provider (Decorator pattern) and is
  optional (`PROFESSORVGC_USE_SEMANTIC_STRATEGY`).
- Composite fallback: official Smogon → Chaos (`CompositeStrategyProvider`).
  Every external knowledge source must degrade gracefully.

### 2.8 Providers & models (BYOK)
- `LLMProvider` / `EmbeddingProvider` are ports; OpenAI and Gemini adapters
  implement them; LangChain/ADK model builders live in `adapters/llm/`.
- Model ids come from `Settings`; never hardcode. Gemini ≥ 3.5 is enforced.
- Heavy SDKs are imported lazily.

### 2.9 Prompts as versioned artifacts
- Files in `src/adapters/llm/prompts/*.txt`, loaded with `load_prompt`.
- Separate the stable system prompt from backend-specific addenda
  (`explanation_agent_addendum.txt` for tool-calling backends).
- A prompt change is a behavior change: add/adjust a test asserting the
  rule is present in the rendered system prompt where practical, and
  consider a benchmark run.

### 2.10 Evaluation is part of the architecture
`scripts/faithfulness_benchmark/` = atomic-claim extraction → verification
against ground truth → LLM judge (style-blindness checked) → statistics
(Fisher's exact). Condition A (pipeline) vs B (naive baseline).
- Use it to justify orchestration/prompt changes with numbers.
- It costs real API calls: **ask the user before running it.**
- Keep fixtures and ground truth deterministic; never let the judge see
  which condition produced a claim.

---

## 3. Adding things — recipes

**New deterministic fact for the explainer**
1. Add a typed field/model in `src/domain/models.py`.
2. Compute it in a domain service (e.g. `turn_simulator.py`) with a fake-based test.
3. Add it to `build_explanation_context` (shared by all backends) and to `AnalysisResult` if the UI shows it.
4. Add one prompt line: what it means and that it must not be contradicted.

**New tool for the explanation agent**
1. Confirm the question genuinely cannot be answered from precomputed evidence.
2. Back it by an existing port (or add a port first).
3. Implement once for both `adk_tools.py` and `langchain_tools.py` with the
   same name, args and `{ok,...}` contract (better: shared core, see debt list).
4. Document it in `explanation_agent_addendum.txt`; test success + `ok:false` paths.

**New LLM vendor**
Adapter in `src/adapters/llm/` (+ LangChain/ADK builders if supported) →
register in `Container.build_llm` / `build_chat_model` / `build_adk_model`
→ settings fields → tests with mocked SDK. Services unchanged.

**New orchestration backend**
Implement `AnalysisPipeline`, reuse the shared evidence stages and
`selection_logic`, lazy-import the framework, register in
`Container.build_pipeline` and `_ORCHESTRATORS`, mirror the existing
orchestrator tests, run the benchmark comparison.

---

## 4. Anti-patterns to reject in review

- Prompt-only fixes for factual errors that code could guarantee.
- LLM computing damage %, speed order, KO attribution or Protect outcomes.
- Raw model JSON used without `parse_selection`/`sanitize_plan`.
- Unbounded agent loops; swallowing an empty answer.
- Tools that raise into the agent loop, mutate state, or embed business rules.
- SDK types (`BaseMessage`, ADK `Event`) escaping the orchestrator/adapter into DTOs.
- A feature implemented in one orchestrator only.
- Prompts as f-strings inside service code.
- RAG that rewrites structured/deterministic data.
- Mixing probabilistic (Chaos) numbers into a deterministic verdict without labeling.
