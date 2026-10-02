# Architecture debt — known gaps and the intended fix

Last reviewed 2026-10-02 (after ADR-030/031). Verify each item still exists
(`grep`) before acting — some may already be fixed. These are
**refactoring targets**, not tasks to do unprompted: fix one when the user
asks for cleanup, or when you are already changing that code and the fix is
small and safe. Every refactor must keep `pytest -q` and `mypy src` green
and keep all three orchestrators behaviorally identical.

Priority: **P1** = structural, causes divergence bugs; **P2** = rule
violation, low risk to fix; **P3** = hygiene.

---

Paid down in ADR-030 (2026-10-02): the triplicated evidence stage
(`GroundTruthAssembler`), the duplicated agent tools (`EvidenceTools`),
services→adapters imports (`PromptRepository`, injected tools), the
misplaced `SmogonSuggestionSource` port, provider-resolution repetition
(`Container._resolve_provider`), `_noop()`, mutable value objects,
`BattleEvent.kind` as a plain `str`, and the monolithic `ui/app.py`.

### P3 — Stringly-typed player ids
`player`/`actor_player` fields are plain `str` ("p1"/"p2"); a `Literal` would
make invalid states unrepresentable. Tighten incrementally (many call sites
and fixtures build these from parsed text).

### P3 — `ChaosRepositoryLike` lives in an adapter module
Fine while only adapters use it (it is an adapter-internal port). Move it to
`domain/interfaces.py` if a service ever needs it.

### P3 — Long modules that are still cohesive
`ui/battle_panel.py` (~550 lines; the log reader was split into the
`showdown_log` package in ADR-032). Split further only along a real seam
(e.g. the field/status/item ledger into its own class) — not by line count.

---

## Refactoring protocol

1. Characterize first: make sure existing tests cover the behavior you will
   move; add a parity test (same fake inputs → equal `AnalysisResult`
   across native/LangChain/ADK with fakes) before a P1 refactor.
2. Move in small, behavior-preserving steps; run tests after each.
3. No behavior change mixed into a refactor.
4. Record structural decisions as a new ADR in `ADR.md`; update `CLAUDE.md`
   §3/§4/§9 if layering or extension points change.
5. Remove the item from this file once done.
