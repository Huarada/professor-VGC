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

## Gemini internal server errors

`Gemini request failed: 500 Internal error encountered` means a model request
returned a provider-side error. Successful replay import does not establish that
the subsequent BO3 analysis completed. This error is distinct from the numerical
investment narration guard and does not establish interference from another project.

The shared native Gemini adapter (also used for BO3 series synthesis) makes at
most three attempts on typed 500/503/504 errors, waits one then two seconds, and
sets a 30-second request timeout with nested SDK retries disabled. Invalid requests
and permission failures are not retried. Exhaustion still raises `LLMProviderError`;
the application does not present an invented successful analysis. This change does
not alter ADK's separate model retry policy or refund a consumed daily slot.

Restart the local application after updating the code. Offline regression tests
cover transient recovery, persistent failure, permanent failure and empty output;
they do not prove current live provider availability.

## OpenAI/ADK reports a missing MCP module

With LiteLLM 1.106.4, an OpenAI completion containing ordinary function tools
imports the optional LiteLLM MCP proxy before checking whether any MCP tool
exists. Without the optional `mcp` package this becomes
`APIConnectionError: OpenAIException - No module named 'mcp'`, before the OpenAI
HTTP request. A clean `pip check` does not detect this optional import path.

The ProfessorVGC ADK OpenAI factory passes `_skip_mcp_handler=True` to LiteLLM:
this application exposes local function tools, not MCP gateway tools. The private
LiteLLM option is consumed locally and is not sent to OpenAI. A regression test
uses the installed LiteLLM dispatcher, blocks MCP imports, and supplies an offline
HTTP response while verifying that the damage-calculator function declaration
still reaches the request. Recheck this compatibility option when upgrading LiteLLM.

The inspected source and isolated interpreter paths contain no FinStatement
references. This reproduction does not require that project or an MCP server.
Restart Streamlit after the fix to rebuild cached model instances. No global
Python packages or other project environments need to be changed.
