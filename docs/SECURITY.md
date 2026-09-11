# Security

## Vulnerability disclosure

Report suspected vulnerabilities by emailing the maintainer directly
(GitHub: [@yangjinming1062](https://github.com/yangjinming1062)). Do not
file public issues for security-sensitive findings.

## Past incidents

### 2026-08-21 — Release-signing key committed to version control

**Status:** Resolved. Project has never been publicly deployed; no users were
affected. The leaked keypair was retired before any release went out.

The original commit history contained `scripts/secrets/update.key` — an
RSA-4096 private key used to sign release manifests. It was committed in the
initial import and remained in `git ls-files` until this security work
landed, exposing the key to anyone with read access to the repository
(forks, mirrors, archives, CI caches).

**What was done:**

- New ECDSA P-256 keypair generated off-repo at `~/.spiritagent/update.{key,pub}`.
- Trust anchor relocated to `scripts/release-keys/update.pub` and committed.
- `scripts/secrets/update.key` removed from HEAD and excised from history
  by `git filter-repo`. All commit SHAs prior to the rewrite were
  invalidated; `main` was force-pushed to the rewritten tip.
- `Resolve-UpdateSigningKey` rewritten to source the private key from
  `$env:SPIRITAGENT_UPDATE_SIGNING_KEY` or `~/.spiritagent/update.key`.
  The repo can no longer locate a signing key on its own.
- `.gitignore` extended with `*.key`, `*.p12`, `*.pfx`.

The retired RSA key MUST be treated as permanently untrusted. Existing
forks and mirrors retain the original blob; future tooling must not
reintroduce it.

## Current trust model

| Role | Location | Notes |
|------|----------|-------|
| Private signing key | `$HOME/.spiritagent/update.key` (or `$env:SPIRITAGENT_UPDATE_SIGNING_KEY` for CI) | Never stored in the repo. ECDSA P-256. |
| Public trust anchor | `scripts/release-keys/update.pub` | Bundled into client builds by `client/package.json` `extraResources`; verified by the runner updater at runtime. |
| Build pipeline | `scripts/lib/UpdateManifest.ps1` `Sign-Manifest` | Refuses to build if no signing key is reachable via the paths above. |
