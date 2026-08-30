# Pre-existing work disclosure — All Things Agentic Hackathon

Per the Contest rules' "New Projects Only" requirement ("Participants may use
standard development tools, including frameworks, libraries, starter
templates, and AI coding assistants, but must disclose any other pre-existing
code or work incorporated into the Project"), this document discloses exactly
what predates the Submission Period (August 3–31, 2026) versus what was
newly designed and built within it, with commit-level evidence.

## What predates the Submission Period

The deterministic battle-analysis core — the Showdown replay parser, the
`@smogon/calc` damage-engine integration, and the base Chaos usage-stats
lookup logic — originated in an earlier personal project, first written
**July 23, 2026**, before this Contest's Submission Period began. That
project was never itself an agentic system: it had no LLM orchestration
framework, no autonomous agent, and none of Gemini, Google ADK, or any
Google Cloud infrastructure service integrated into it.

## What was newly designed and built during the Submission Period

On **August 25, 2026**, that prior project was unlinked from its original
history and rebuilt as this repository — visible directly in this repo's own
git history, which begins with `f08c36b "Initial commit"` at
**2026-08-25 22:46:40 -03:00**, entirely inside the Submission Period.

That commit's own authored-date is, in principle, something a repo's author
could edit — so it isn't relied on alone here. The stronger, independently
verifiable fact is the **GitHub-assigned repository creation timestamp**
itself (server-side metadata, set once by GitHub when the repository is
created and never editable afterward by the owner): per the GitHub API,
this repository (`Huarada/professor-VGC`) was created **2026-08-26T01:48:57Z**
— also squarely inside the Submission Period, and this is the timestamp a
judge opening the repository on GitHub actually sees.

Every commit after the initial one — the overwhelming majority of this
project's functionality — was authored during the Submission Period. In
particular, all three of the Contest's *mandatory* technical requirements
were built new during this window, not present in the pre-existing project
at all:

| Requirement | What was built | Date | Commit |
|---|---|---|---|
| **Google Agent Framework** | `AdkAnalysisOrchestrator` — Google ADK (Agent Development Kit) `LlmAgent`s orchestrating the whole analysis pipeline, made the default backend | 2026-08-25 | [`94a074b`](https://github.com/Huarada/professor-VGC/commit/94a074b) |
| **Google Cloud infrastructure** | Google Cloud Firestore as the metagame-memory backend (Chaos usage-stat storage/retrieval) | 2026-08-26 | [`f871f80`](https://github.com/Huarada/professor-VGC/commit/f871f80) |
| **Gemini 3.5+** | `gemini-3.5-flash` enforced as the default LLM, with a `Settings` validator that refuses to start on an older model | 2026-08-27 | [`8320737`](https://github.com/Huarada/professor-VGC/commit/8320737) |
| **Google Cloud infrastructure (deploy)** | `Dockerfile` + deployment to Cloud Run (Google Cloud infrastructure, hosting) | 2026-08-28 → 2026-08-29 | [`e1e57af`](https://github.com/Huarada/professor-VGC/commit/e1e57af), [`bd9440e`](https://github.com/Huarada/professor-VGC/commit/bd9440e), [`8d6493c`](https://github.com/Huarada/professor-VGC/commit/8d6493c) |

None of these four things — the agentic orchestration layer, the Firestore
memory backend, the Gemini 3.5 requirement, or the Cloud Run deployment —
existed in the pre-existing project. They were designed, implemented, and
deployed entirely within the Submission Period, and are what transform the
prior deterministic tool into the autonomous, Gemini-3.5-orchestrated agent
this Contest asks for: `AnalysisRequest` → `AdkAnalysisOrchestrator` plans
and executes a multi-step investigation (selection, matchup evaluation,
metagame lookup via Firestore, tool-calling follow-ups) without a human
walking it through each step.

## Verifying this claim

This repository's full commit history is public (or shared with the judges
per the Contest's private-repo instructions) and untouched since the
2026-08-25 rebuild — every date and commit hash above can be checked
directly with `git log`. The repository-creation timestamp can be checked
independently via the GitHub API — this repository is private (per the
Contest's own submission instructions for private repos, access is shared
with `testing@devpost.com` and `cloudhackathons@google.com`), so the check
needs an authenticated call rather than a bare public request:

```bash
gh api repos/Huarada/professor-VGC --jq '.created_at'
# or, with any GitHub personal access token that has read access:
curl -s -H "Authorization: Bearer <token>" https://api.github.com/repos/Huarada/professor-VGC | grep created_at
```
