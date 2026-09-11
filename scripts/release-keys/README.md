# scripts/release-keys/

Public trust bundle for the desktop auto-update pipeline.

## What lives here

| File | Tracked? | Purpose |
|------|----------|---------|
| `update.pub` | yes | Trust anchor bundled into client builds via `client/package.json` `extraResources`. The runner verifier ([client/main/runner/updater.ts](../../client/main/runner/updater.ts)) calls `crypto.verify('SHA512', ...)` against this key to confirm release manifests were signed by the matching private key. |
| (private key) | **no** | Released-signing private key — used by `scripts/lib/UpdateManifest.ps1` `Sign-Manifest` at build time only. Never stored in this repo. |

The matching private key is sourced from `$env:SPIRITAGENT_UPDATE_SIGNING_KEY`
(path to a PEM key file) or, when that variable is unset, from the per-user
default `$HOME/.spiritagent/update.key`. The release pipeline refuses to build
without one.

For why the private key lives off-repo, and the current trust model, see
[SECURITY.md](../../docs/SECURITY.md) (includes the 2026-08-21 signing-key incident
and rotation history).
