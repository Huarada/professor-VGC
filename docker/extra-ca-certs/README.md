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
