# ENV layers — sops-editor

| Слой | Compose | K8s |
|---|---|---|
| Settings | `docker/.env` | ConfigMap `sops-editor-config` |
| Credentials | `docker/.env.secrets` | SOPS Secret `sops-editor-secret` |
| Defaults | `backend/app/config.py` | same |
| Data | `./storage:/data` | PVC `sops-editor-data` → `/data` |

## Secret (только credentials)

- `SOPS_EDITOR_AUTH_PASSWORD` — Basic auth.
- `SOPS_EDITOR_KEYSTORE_KEY` — Fernet master key for `server-key/keyring.json`.
- `SOPS_EDITOR_GIT_TOKEN` — optional HTTPS token.
- `id_ed25519` — optional SSH deploy key; mounted at `/ssh`.
- `known_hosts` — optional SSH pins.

Нет DB/Keycloak credentials: приложение не использует БД и OIDC login. Basic auth —
осознанное отклонение от OIDC-канона; при появлении OIDC-прокси можно перейти на
`SOPS_EDITOR_AUTH_MODE=header`.

## ConfigMap

`SOPS_EDITOR_STORAGE=/data`, `SOPS_EDITOR_ROOTS=/data/repos`, basic auth,
`TRUST_PROXY=true`, `COOKIE_SECURE=true`, Git allowlist, SSH mode, limits.

## PVC

`/data` — 10Gi, `local-path`, RWO. Поэтому `replicas: 1`, `strategy: Recreate`.
Ключ `server-key/keyring.json` шифруется Fernet-мастер-ключом из Secret; PVC
содержит только шифротекст. Бэкап PVC и мастер-ключ хранить отдельно.
