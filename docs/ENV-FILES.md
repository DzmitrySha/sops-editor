# ENV-файлы sops-editor

| Файл | Назначение |
|---|---|
| `docker/.env.example` → `.env` | settings: workspace, storage, auth user, FQDN, limits |
| `docker/.env.secrets.example` → `.env.secrets` | Basic password, git token, Fernet keystore key |
| K8s ConfigMap `sops-editor-config` | settings prod |
| K8s SOPS Secret `sops-editor-secret` | credentials + optional SSH keys |
| `./storage` ↔ PVC `sops-editor-data` | `/data` |

## Compose

```bash
cp docker/.env.example docker/.env
cp docker/.env.secrets.example docker/.env.secrets
docker compose --env-file docker/.env --env-file docker/.env.secrets up -d --build
```

Оба `--env-file` обязательны для интерполяции `${...}`.

Lab/work Traefik:

```bash
docker compose -f docker-compose.yml -f docker-compose.traefik.yml \
  --env-file docker/.env --env-file docker/.env.secrets up -d --build
```

## K8s

- Настройки (URL, FQDN, флаги, `SOPS_EDITOR_ROOTS`, limits) — ConfigMap.
- Только credentials (пароль, Fernet master key, git token, SSH key) — SOPS Secret.
- Не дублировать ключ в ConfigMap и Secret: Secret перекрывает ConfigMap.
- `SOPS_EDITOR_KEYSTORE_KEY` без значения отключает серверное хранилище; `/keys/age.txt`
  при этом продолжает работать.
- `SOPS_EDITOR_GIT_SSH_STRICT_HOST_KEY_CHECKING=accept-new`: known_hosts пишется в
  `/data/ssh`; для строгого режима положите `known_hosts` в Secret.

## Профили

OIDC/Keycloak-профилей нет. `NEUROAI_PROFILE` не используется. Для будущего OIDC
можно включить `SOPS_EDITOR_AUTH_MODE=header` за доверенным прокси.
