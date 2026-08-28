# ProfessorVGC — the real app (Streamlit UI + Python orchestration core +
# the Node @smogon/calc / @pkmn/smogon IPC subsystem it shells out to).
# Build from the REPO ROOT:
#
#   docker build -t professorvgc .
#   docker run --rm -p 8080:8080 --env-file .env professorvgc
#
# (--env-file is a RUNTIME flag, not a build one — .env never enters an
# image layer; see .dockerignore. On Cloud Run, secrets come from
# --set-secrets / --set-env-vars at `gcloud run deploy` time instead.)
#
# Two-stage build: the app needs BOTH a Python runtime (the orchestration
# core, Streamlit) and a Node runtime (node_calc's calc/dex IPC workers,
# spawned as subprocesses by src/adapters/node_ipc.py — see that module's
# docstring). Stage 1 builds node_calc's node_modules with `npm ci` for a
# reproducible, correct-for-this-platform install; stage 2 is the actual
# Python runtime image, which copies in ONLY the built node_modules and the
# `node` binary itself — npm is a build-time tool, never invoked at
# runtime (calc_server.js/smogon_dex_server.js are run directly via
# `node <script>`), so it's deliberately not carried into the final image.
# Both stages pin the same Debian release (bookworm) so the `node` binary
# copied across stages links against libraries actually present in stage 2.

FROM node:20-bookworm-slim AS node-deps
WORKDIR /build/node_calc
COPY node_calc/package.json node_calc/package-lock.json ./
# Optional: trust an extra CA cert during `npm ci` if one was dropped into
# docker/extra-ca-certs/ (see that directory's own README) — needed on
# networks that intercept TLS to the npm registry with a root not in
# Node's bundled trust store (observed here: Avast's Web/Mail Shield). A
# bind mount (build-time only, BuildKit) rather than COPY: the cert never
# becomes part of any image layer, so there's nothing to clean up
# afterward and nothing that could later leak via layer history. On every
# other network (CI included) this directory holds only its README, no
# .pem/.crt matches, and npm's default trust store is used completely
# unchanged — this never affects a normal build.
RUN --mount=type=bind,source=docker/extra-ca-certs,target=/tmp/extra-ca-certs \
    bundle="$(find /tmp/extra-ca-certs -maxdepth 1 \( -name '*.pem' -o -name '*.crt' \) | head -n1)"; \
    if [ -n "$bundle" ]; then export NODE_EXTRA_CA_CERTS="$bundle"; fi; \
    npm ci --omit=dev

FROM python:3.12-slim-bookworm AS runtime
WORKDIR /app

# --- Node runtime, copied from the build stage (see header comment) ---
COPY --from=node-deps /usr/local/bin/node /usr/local/bin/node
COPY --from=node-deps /build/node_calc/node_modules /app/node_calc/node_modules

# --- Python dependencies (layer cached independently of app code below) ---
COPY requirements.txt .
# Same optional extra-CA mechanism as the node-deps stage above, this time
# for pip against PyPI (a TLS-intercepting network breaks both identically).
RUN --mount=type=bind,source=docker/extra-ca-certs,target=/tmp/extra-ca-certs \
    bundle="$(find /tmp/extra-ca-certs -maxdepth 1 \( -name '*.pem' -o -name '*.crt' \) | head -n1)"; \
    if [ -n "$bundle" ]; then export PIP_CERT="$bundle"; fi; \
    pip install --no-cache-dir -r requirements.txt

# --- App code. src/config.py's _PROJECT_ROOT resolves to two directories
# up from itself (src/config.py -> src/ -> repo root), so it lands on
# WORKDIR here as long as the layout below is preserved. ---
COPY src/ src/
COPY node_calc/*.js node_calc/
COPY node_calc/src/ node_calc/src/
COPY .streamlit/ .streamlit/
COPY pyproject.toml .

# Un-privileged runtime user — this app only ever reads its own code and
# talks outbound (Gemini/OpenAI/Firestore/Node subprocess over stdio); it
# never needs root.
RUN useradd --create-home --uid 1000 appuser \
    && chown -R appuser:appuser /app
USER appuser

# Cloud Run injects $PORT (defaults to 8080 in every doc/example, but the
# platform is the source of truth — never hardcode it as the only value);
# the shell form of CMD lets ${PORT} expand at container start — this is
# deliberate, not an oversight (`docker build` warns that JSON-array/exec
# form is usually preferable for signal handling, but exec form never runs
# a shell, so it can't expand ${PORT} at all; shell form is the only one of
# the two that can satisfy this specific requirement). Headless + bound to
# 0.0.0.0 is required for Streamlit to be reachable at all from outside the
# container; CORS/XSRF stay on Streamlit's own defaults since Cloud Run's
# front-end proxy sits in front of exactly one deployed origin.
EXPOSE 8080
ENV PROFESSORVGC_NODE_BINARY=node
CMD streamlit run src/ui/app.py \
    --server.port=${PORT:-8080} \
    --server.address=0.0.0.0 \
    --server.headless=true
