## Summary

<!-- What changes and why. Link the issue / ADR if there is one. -->

## Type

- [ ] feat · [ ] fix · [ ] refactor (no behavior change) · [ ] test · [ ] docs · [ ] chore/ci

## Checklist

- [ ] Branch is `<type>/<short-description>` and up to date with `master` (`git pull --rebase origin master`)
- [ ] `pytest -q` and `mypy src` pass locally
- [ ] New behavior has a test that needs no network or API keys
- [ ] `services/` and `ui/` don't import `adapters/`; evidence changes go through `GroundTruthAssembler`
- [ ] No secrets, `.env` or credential files; `data/chaos/` untouched
- [ ] ADR / `.claude/CLAUDE.md` updated if an invariant or architectural decision changed

## Test plan

<!-- How you verified it. -->
