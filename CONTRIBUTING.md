# Contributing to ProfessorVGC

Thanks for helping! This project explains Pokémon VGC battles with an LLM
that is only allowed to narrate **deterministic ground truth** (the battle
log + the `@smogon/calc` engine) and **probabilistic context** (Smogon usage
stats). Most of the review bar below exists to protect that guarantee.

- Architecture, invariants and extension points: [`.claude/CLAUDE.md`](.claude/CLAUDE.md)
  (written for humans and AI assistants alike).
- Why things are the way they are: [`ADR.md`](ADR.md).
- Data layout and the Chaos/Firestore pipeline: [`DATA.md`](DATA.md).

## Development setup

```bash
python -m pip install -r requirements.txt
cd node_calc && npm install && cd ..
cp .env.example .env          # bring your own provider key (never commit .env)
pytest -q                     # no network or API keys needed
mypy src                      # strict
streamlit run src/ui/app.py
```

Python 3.10+ and Node 20+. CI runs `pytest` on Python 3.10 and 3.12, the
Node smoke/unit tests, and `mypy --strict`.

## Workflow

1. Sync first: `git switch master && git pull --ff-only`.
2. Branch from `master` using `<type>/<short-kebab-description>`:

   | type | for |
   |---|---|
   | `feat/` | new behavior (`feat/terrain-aware-speed`) |
   | `fix/` | bug fixes (`fix/weather-id-translation`) |
   | `refactor/` | no behavior change (`refactor/split-showdown-log-reader`) |
   | `test/` | tests only |
   | `docs/` | documentation only |
   | `chore/` | tooling, repo hygiene, dependencies |
   | `ci/` | workflow changes |

3. Commit with [Conventional Commits](https://www.conventionalcommits.org/):
   `type(scope): imperative summary`, e.g. `fix(parser): keep cured status out of later turns`.
4. Before opening the PR, pull again (`git pull --rebase origin master`) and
   run `pytest -q` and `mypy src` locally.
5. Open a PR (the template asks for a summary and a test plan). One concern
   per PR. PRs are squash-merged once CI is green.

## Review checklist

- **Dependency rule:** `domain` imports nothing from the project; `services`
  and `ui` never import `adapters` — only `src/services/container.py` does.
- **Ground truth first:** a new fact the LLM should state is computed in code
  (a typed, frozen Pydantic field) and only *reported* by the prompt.
- **Backend parity:** evidence goes into `GroundTruthAssembler`, never into
  one orchestrator; `tests/test_backend_parity.py` must stay green.
- **Tests with fakes:** every behavior change ships with a pytest test that
  needs no network or keys (Node tests skip gracefully without Node).
- **Never edit `data/chaos/*.json`** — those are raw Smogon dumps; fix the
  code that reads them instead.
- **English** for code, comments, prompts and docs.
- **Lean comments:** state the *why* in ≤2 lines; history belongs in the commit or an ADR. CI's `lint` job (ruff + vulture) rejects unused imports, variables and dead code.
- **Architectural decisions** get an ADR entry in `ADR.md`.

## Security

Never commit secrets (`.env`, service-account JSON, API keys). Report
vulnerabilities privately — see [`SECURITY.md`](SECURITY.md).

## License

By contributing you agree that your contributions are licensed under the
[Apache License 2.0](LICENSE).
