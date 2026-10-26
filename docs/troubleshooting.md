# BO3 narration and local environment troubleshooting

## Revalidation: 2026-10-25

The reported message, `The BO3 explanation repeatedly included numerical investment
prose`, originated in the shared output guard. It is not an error emitted by
FinStatement, Firestore or the damage engine. The former behavior aborted the
whole series after the narrator repeated an allocation despite one correction.

The revised BO3 flow omits numerical investment from its coaching input, while
retaining full calculated ranges in typed result tables. If the model still
returns allocations after one correction, its entire narrative is replaced by
an explicit notice. Deterministic game/series results remain available. Invalid
allocations are never accepted or silently converted. Foreign mechanics still
fail closed. A real model-provider failure remains an error.

The installed global Python had unrelated dependency conflicts involving
`awsebcli`, `gensim` and `opencv-python`. No FinStatement imports or foreign project
entries were found in the inspected project import paths. That does not diagnose
other running applications; it explains why shared global packages should not be
used to establish this project's environment.

The existing `.venv` was a WSL/Linux environment, without a Windows Python
executable. It was preserved. The isolated `.venv-windows` environment installed
the declared requirements and passed `pip check` without broken dependencies.

## Start on Windows

From the repository root:

```powershell
python -m venv .venv-windows
.\.venv-windows\Scripts\python.exe -m pip install -r requirements.txt
python scripts/run_local.py
```

If a previous ProfessorVGC server is running, stop that terminal with Ctrl+C
before restarting. To use a separate port:

```powershell
python scripts/run_local.py --server.port 8502
```

The launcher selects the project interpreter explicitly, sets the child working
directory and forwards Streamlit arguments. It does not activate an environment
in the caller's shell or modify globally installed packages. On Linux/macOS it
uses `.venv/bin/python`; create that environment separately.

Windows runtime bootstrap loads `truststore` before network SDKs so operator-installed
Windows roots are honored with TLS verification enabled. An optional existing
`docker/extra-ca-certs/local-avast-bundle.pem` can additionally be supplied to the
child's Requests/Node/gRPC trust settings. Explicit operator settings are retained.
No certificate is downloaded, added to the OS or committed. Environment changes
are child-only. No `verify=False` or TLS-verification bypass is used.

## Checks performed

| Check | Result |
|---|---|
| Complete suite in isolated Windows environment and system trust profile | **658 passed**, no skips |
| Node engine suite and smoke calculation | **17 passed**, smoke succeeded |
| Offline inference corpus | **26/26** series, **78** synthetic games |
| Independent analytic oracle | **17/17** cases |
| Strict mypy, Ruff F and Vulture | Passed |
| Isolated dependency consistency | No broken requirements |
| Domain/services/UI import boundaries | No violations found in the inspected source tree |
| Local Streamlit startup and health endpoint | Passed on a temporary free port; test server terminated |
| Verified public replay HTTPS with Windows system trust | HTTP 200 |

Without Windows system trust, the live replay integration check was skipped for
certificate verification failure. After enabling verified system trust in the
isolated child, the entire suite passed without skips. The local certificate
setup is installation-specific; a Linux/Cloud Run deployment retains its normal
trust configuration.

Regression coverage includes rejected allocation recovery, disabled inference,
unchanged BO1 context, source-evidence immutability, numeric-table preservation,
mechanical HP/speed quantities, bounded calls and logging without raw model text.

No paid model requests or production database writes were used for this audit.
Live provider narrative quality and current Firestore credentials/IAM were not
revalidated by making production requests. Synthetic retention remains a
consistency/search measure, not real-game spread-identification accuracy.
