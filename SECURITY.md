# Security policy

Russian version: [docs/ru/SECURITY.md](docs/ru/SECURITY.md).

This tool works with private keys and plaintext secrets. The threat model is
"internal server, trusted developers, HTTPS access".

## What is protected

- **Plaintext is never written to disk.** Decryption happens in process and
  browser memory. The temporary payload for `sops edit` is a `0600` file in a
  `0700` directory, removed in `finally`.
- **Backups are ciphertext only.** `/data/backups` never contains plaintext.
- **Computer uploads are ciphertext only.** Files land in
  `/data/uploads/<session>/<token>/`, are decrypted in memory and re-encrypted on
  save. Downloads only serve SOPS-encrypted files from `uploads`.
- **The UI key is not persisted.** It lives in process memory, bound to an
  `HttpOnly` cookie, sliding TTL 8 h. It never reaches localStorage, logs or API
  responses.
- **Paths are limited to allowed roots.** `realpath` + containment check;
  symlinks pointing outside are rejected; `.git` is inaccessible.
- **Saving is atomic and verified.** SHA-256 of the source, backup, `sops edit`,
  re-decrypt and compare; on failure the original is restored.
- **The SOPS policy is not changed.** `sops edit` uses the master keys and
  `encrypted_regex` of the original file instead of rebuilding it from the
  current `.sops.yaml`.
- **Container:** `read_only`, `cap_drop: ALL`, `no-new-privileges`, writes only to
  `/data`, `/workspace`, `/tmp`.
- **Web:** CSP without inline scripts/CDNs, `X-Frame-Options: DENY`, `nosniff`,
  `no-referrer`, `no-store` for the API, SameSite=Strict cookies, Origin check on
  mutating requests.

## Required measures for a server

1. **HTTPS** (Traefik) — otherwise the key and plaintext travel in clear.
2. **Authentication** is mandatory once the port leaves loopback. Either Basic
   auth with a long random `SOPS_EDITOR_AUTH_PASSWORD`, or
   `SOPS_EDITOR_AUTH_MODE=header` behind an OIDC proxy. With
   `SOPS_EDITOR_TRUST_PROXY=true` or `SOPS_EDITOR_COOKIE_SECURE=true` the
   container **refuses to start** without one of them. `SOPS_EDITOR_AUTH_MODE=none`
   is for loopback only.
3. **Network access**: do not publish the port to the internet; Traefik/allowlist
   only.
4. **Keys**: `keys/age.txt` with mode `600`, a dedicated key for the tool when
   the policy allows it.
5. **Backups**: `/data/backups` is ciphertext, but restrict access anyway.
6. **Git token**: read/write to the needed repositories only (least privilege),
   never a personal admin token.
7. **Logs**: do not enable DEBUG in production without need; logs contain paths
   and sizes, not values.

## Residual risks

- **The server sees plaintext** while a request is processed. That is inherent to
  server-side SOPS. If unacceptable, run the tool locally on the workstation.
- **Basic auth is not SSO**; use `SOPS_EDITOR_AUTH_MODE=header` behind an OIDC
  proxy. In that mode the identity header is trusted — an attacker who can reach
  the container directly can impersonate any user, so restrict the network.
- **XSS**: all dynamic text is rendered via `textContent`/escaping; no third-party
  CDNs. Review the CSP when adding libraries.
- **CSRF**: Basic auth + Origin check + SameSite. Mutating requests accept JSON;
  a cross-site form cannot produce them.
- **DoS**: 5 MiB file limit, listing limits, sops/git timeouts. One uvicorn
  process; for larger teams use several replicas with shared `/data` (RWO) or
  external storage.
- **Supply chain**: sops/age are downloaded at build time with pinned versions and
  SHA-256; Python dependencies are pinned in `pyproject.toml`. Rebuild the image
  on updates.
- **Backups are not encrypted separately**; they are SOPS ciphertext with the same
  recipients as the original.
- **Git push runs with the server's credentials**; if the container is
  compromised, an attacker gets access to the mounted repositories/keys. Limit
  mounts and the token scope.

## Incident

1. Stop the container: `docker compose ... down`.
2. Revoke/rotate the age key, git token and Basic auth password.
3. Inspect `/data/backups` and the git history for unauthorized changes.
4. Re-issue secrets if plaintext may have leaked (logs/dumps/memory).

## Reporting a vulnerability

Please do not open a public issue. Use GitHub's private vulnerability reporting
("Security" → "Report a vulnerability") or email the maintainer listed in the
repository profile. Expect an acknowledgement within a few days.
