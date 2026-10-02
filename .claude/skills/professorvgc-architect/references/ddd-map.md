# DDD map — ProfessorVGC

How Domain-Driven Design concepts map onto this codebase, and the rules
that follow from that mapping. Use it to decide **where a new concept
belongs** and **what shape it should have**.

---

## 1. Bounded contexts

| Context | Responsibility | Main models | Code | Upstream source (ACL) |
|---|---|---|---|---|
| **Battle Reconstruction** | Turn a Showdown replay into an ordered, causal, typed record of what happened | `GameState`, `SideState`, `BattleOutcome`, `BattleEvent`, `KOEvent`, `FieldConditions` | `adapters/parsers/showdown_parser.py`, `adapters/replay_url_fetcher.py` | Showdown protocol log / replay JSON / replay URL |
| **Damage & Speed Mechanics** | Exact, field-aware damage rolls and move order | `CalcRequest`, `PokemonSet`, `StatSpread`, `DamageResult`, `SpeedComparison` | `adapters/calc/smogon_calc_adapter.py`, `node_calc/` | `@smogon/calc` via Node IPC |
| **Metagame Intelligence** | Probabilistic usage stats by rating tier with regulation fallback | `MetaContext`, `PokemonMetaSummary` | `adapters/chaos/*` | Smogon Chaos JSON in Firestore |
| **Strategy Knowledge** | Narrative strategy, archetypes, teammates; optional semantic retrieval | `SmogonStrategy`, `Archetype` | `adapters/smogon/*` | `@pkmn/smogon` (Node), Chaos fallback, embeddings |
| **Coaching** (core domain) | Decide what the question is about, verify every turn, explain | `AnalysisRequest`, `SelectionPlan`, `MatchupVerdict`, `TurnCheck`, `TurnDamageCheck`, `OptimalMoveOption`, `ProtectRead`, `AgentToolInvocation`, `AnalysisResult` | `services/*` | LLM providers (ADK / LangChain / native SDKs) |
| **Conversation** | Per-session history, recurring concepts | `ChatMessage` | `adapters/memory/`, `services/concept_tracking.py` | in-memory store |
| **Replay Viewer** | Showdown-like visual battle panel | `BattleReplay`, `ReplayTurnSnapshot`, `ReplayPokemonState` | `adapters/parsers/replay_viewer_parser.py`, `domain/replay_view_models.py` | same replay log |

**Core domain** = Coaching (that is the product's differentiator).
Battle Reconstruction and Damage & Speed are **supporting** domains whose
correctness the core depends on. Metagame/Strategy are **generic-ish**
(external data, wrapped).

**Replay Viewer is intentionally a separate model** (ADR-014): it parses the
same log for a different purpose (visual snapshots). Do not "unify" its
models with `GameState` — two contexts may model the same reality
differently. Share only true primitives (e.g. species normalization).

### Context relationships
- Coaching is **customer** of every other context; it consumes their
  published models and never their raw sources.
- Damage & Speed is a **conformist** to `@smogon/calc` semantics but guarded
  by an ACL (the adapter maps JSON → `DamageResult`, sends `gameType: Doubles`
  (ADR-007), observed Mega forme (ADR-009), stat stages (ADR-021)).
- Metagame Intelligence publishes `MetaContext`; storage (Firestore vs local
  files) is hidden behind `ChaosRepositoryLike` + `ChaosTierIndex`.

---

## 2. Tactical patterns

### Aggregate: `GameState`
- **Root of one battle.** Its consistency boundary includes sides, rosters,
  outcome/timeline, field conditions, rating and format.
- Invariants it owns (established once by the parser, consumed everywhere):
  - Bench-only Pokémon (team preview but never switched in) are **not in play** (ADR-001).
  - The ordered `outcome.events` timeline **is** the move order; a Pokémon with
    no `move` event before fainting did not act.
  - Moves are never fabricated; move history survives Mega Evolution (ADR-002).
  - Forfeit is captured as ground truth (ADR-012).
- Derived queries (`brought()`, in-play rosters, `tailwind_active(player, turn)`)
  live **on the model** as methods — keep behavior next to data rather than
  re-deriving it in services.
- Services never mutate a `GameState`; they read it and produce new evidence.

### Value objects
`StatSpread` (already `frozen=True`), `PokemonSet`, `FieldConditions`,
`CalcRequest`, `DamageResult`, `SpeedComparison`, `MatchupVerdict`,
`TurnCheck`, `ProtectRead`, `SelectionPlan`, `ChatMessage`.
- Defined by their values, no identity. **New value objects must be
  `ConfigDict(frozen=True)`**; when you touch an existing one and it's safe
  (no in-place mutation in callers — grep first), freeze it.
- Validate at construction (`field_validator` / `model_validator`) so an
  invalid instance cannot exist. Prefer `Enum`/`Literal` over magic strings —
  e.g. `BattleEvent.kind` and `player: "p1" | "p2"` are currently `str`;
  tightening to `Literal[...]` is a welcome, low-risk improvement.

### Domain services (stateless, deterministic)
- `MatchupEvaluator` — field-aware damage + speed verdicts for selected matchups.
- `TurnReplaySimulator` — per-turn projected-vs-actual, optimal alternatives, Protect reads.
- `selection_logic` — parse/sanitize selection, **cross-side-only guardrail**, fallback plan.
- `battle_context` — candidate/context species, rosters, outcome summary.
Depend only on ports (`CalcEngineAdapter`) and domain models. Pure enough to
test with `FakeCalcEngine`.

### Application services (use cases)
The `AnalysisPipeline` implementations (`AnalysisService`,
`LangChainAnalysisOrchestrator`, `AdkAnalysisOrchestrator`). They coordinate:
load memory → parse → select → gather evidence → explain → persist memory →
return DTO. They should contain **sequencing, not rules**.

### Repositories
`ChaosRepositoryLike` (Firestore in the app; local files only in tooling/tests)
and `ConversationMemory`. Repositories return domain-meaningful data and
hide storage layout (one doc per species, tier index…).

### Anti-corruption layers
Every adapter is an ACL. The rule: **foreign vocabulary stops at the adapter.**
- Showdown `|move|p1a: X|...` lines → `BattleEvent`.
- `@smogon/calc` JSON → `DamageResult` / `SpeedComparison`.
- Firestore documents → Chaos dicts consumed only inside `adapters/chaos`.
- SDK messages/events (LangChain `BaseMessage`, ADK `Event`) → `AgentToolInvocation`, plain `str`.
- Species spellings → `species_normalize` (case/forme-insensitive, progressive forme trimming).

### Domain events
`BattleEvent`/`KOEvent` are **observed facts** from the log (event-sourced
reconstruction of the battle), not in-process pub/sub. Treat them as
immutable history; never synthesize an event the log did not contain.

### Factories
`Container` is the factory/composition root for object graphs; the parser is
effectively the factory for `GameState`. Don't construct aggregates ad hoc
elsewhere except in tests.

---

## 3. Ubiquitous language (use these words, consistently)

| Term | Meaning in this codebase |
|---|---|
| side / player | `p1` / `p2` |
| roster / team | the 6 (or fewer) declared Pokémon of a side |
| brought / in-play | Pokémon that actually entered the field |
| bench-only | seen in preview, never switched in — not in play |
| lead | Pokémon active at turn 1 |
| timeline | ordered `BattleEvent` list; authority for causality |
| matchup | a **cross-side** (attacker, defender) pair |
| verdict | deterministic damage + speed result for a matchup |
| turn check | per-turn projected-vs-actual verification |
| Protect read | classification of a Protect-family use (ADR-006/008) |
| tier / cutoff | Chaos rating bracket (`-0`, `-1500`, `-1630`, `-1760`) |
| ideal tier | highest cutoff; current tier = bracket containing match rating |
| regulation fallback | older regs of the **same game family**, nearest first, depth ≤ 3 |
| archetype | strategic role (Trick Room, Tailwind, weather…) |
| ground truth | deterministic evidence (log + engine) |
| approximation | any assumption in evidence (default EVs, base forme); must be surfaced |

If a new concept appears, name it once in the domain model, document it in
the docstring, and use the same word in prompts, UI labels and tests.

---

## 4. Placement decision tree

```
Is it a fact about what happened in the battle?   → Battle Reconstruction (parser + GameState)
Is it a deterministic computation over facts?      → domain service in src/services (pure)
Is it data from outside (API, file, DB, Node)?     → Protocol in domain/interfaces.py + adapter + container wiring
Is it a probability / likely set?                  → Metagame / Strategy context, labeled as probabilistic
Is it about phrasing / explaining?                 → prompt file + explanation context, never logic
Is it about display?                               → src/ui only, from AnalysisResult fields
```
