# Security Policy

## Reporting a vulnerability

Please **do not open a public issue** for security problems. Use GitHub's
private reporting instead: **Security → Report a vulnerability** on this
repository. Include steps to reproduce and the affected version/commit. You
should get a first response within a few days.

## Supported versions

Only the latest `master` is supported; fixes are not backported.

## How the project handles secrets

- **Bring your own key.** Provider keys (`PROFESSORVGC_OPENAI_API_KEY`,
  `PROFESSORVGC_GEMINI_API_KEY`) are read from the environment or a local
  `.env` file, which is git-ignored. They are never logged, stored or sent
  anywhere except the selected provider.
- **Firestore credentials** are read from Application Default Credentials or
  `PROFESSORVGC_FIRESTORE_CREDENTIALS_PATH`, which must point outside the
  repository; common service-account file names are git-ignored and
  docker-ignored as a safety net.
- **Secret scanning with push protection** is enabled on this repository.
- The Docker image runs as a non-root user and only copies `src/`,
  `node_calc/` and runtime config.

## Untrusted input

Replay text, replay URLs and questions are untrusted user input:

- Replay URLs are only fetched when they match Showdown's own replay hosts
  (`src/adapters/replay_url_fetcher.py`), with a timeout.
- Battle logs are parsed as data; nothing from a log is executed.
- The LLM only receives read-only tools over the deterministic ports; it
  cannot write data or call arbitrary URLs.
