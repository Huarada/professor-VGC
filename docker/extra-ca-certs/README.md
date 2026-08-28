# Optional extra CA certificate for `docker build`

Some local dev networks intercept outbound TLS with a root certificate that
isn't in Node's bundled trust store (observed on this project: Avast's
Web/Mail Shield, the same root that already required a combined CA bundle
for grpc/Firestore — see `PROFESSORVGC_FIRESTORE_GRPC_CA_BUNDLE_PATH` in
`.env.example`). Without it, the Dockerfile's `npm ci` step fails with
`UNABLE_TO_VERIFY_LEAF_SIGNATURE` against `registry.npmjs.org`, even though
the exact same install works fine outside a container.

If you hit that, drop a `.pem` or `.crt` file in this directory (any name)
containing the intercepting root cert — or your combined bundle, if you
already built one for another tool. `docker build` picks it up
automatically (see the Dockerfile's `node-deps` stage) and adds it to
Node's trusted set for that build only; nothing else changes, and the
final runtime image never sees this directory or file.

This is genuinely optional: on a normal network (CI included), this
directory stays empty (only this README) and the Dockerfile behaves as if
the mechanism weren't there at all — `npm ci` just uses Node's own default
trust store. Certificate files dropped here are git-ignored on purpose
(see this directory's own `.gitignore`) — never commit one.

## The same problem shows up again at RUNTIME, not just at build time

`npm ci`/`pip install` are build-time only — this directory's mechanism
above doesn't help a already-running container, and the app itself makes
its own outbound HTTPS calls at runtime (fetching a pasted Showdown replay
URL, and every LLM/Firestore call). If those fail with the same
`CERTIFICATE_VERIFY_FAILED`/`unable to get local issuer certificate`
shape, mount the same bundle into the container and point Python's own
trust-store env vars at it — a `docker run` flag, deliberately NOT baked
into the image for the same reason as above (Cloud Run's network never
needs this):

```bash
docker run --rm -p 8080:8080 --env-file .env \
  -v "$(pwd)/docker/extra-ca-certs/<your-bundle>.pem:/tmp/local-ca-bundle.pem:ro" \
  -e REQUESTS_CA_BUNDLE=/tmp/local-ca-bundle.pem \
  -e SSL_CERT_FILE=/tmp/local-ca-bundle.pem \
  professorvgc:latest
```

`REQUESTS_CA_BUNDLE` covers the `requests` library (the replay-URL
fetcher); `SSL_CERT_FILE` covers anything going through Python's stdlib
`ssl` module directly. Both are no-ops when unset, so this is exactly as
optional at runtime as the build-time mechanism above is at build time.
