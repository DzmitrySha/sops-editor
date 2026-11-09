# Nuances — sops-editor на NeuroAI

Канон: [`ai-template/docs/NUANCES.md`](../../ai-template/docs/NUANCES.md).
Ниже — только отличия и важные детали этого приложения.

## FQDN и Traefik

```text
lab/work: https://sops-editor-dev.vitebsk.energo.net
prod:     https://sops-editor.vitebsk.energo.net   (без -dev)
```

Для Windows hosts:

```text
127.0.0.1  sops-editor-dev.vitebsk.energo.net
127.0.0.1  traefik.vitebsk.energo.net
```

Compose использует `docker-compose.traefik.yml`; сеть `traefik_proxy` общая,
service name `ai-sops-edit` уникален. Traefik router/service называются
`sops-editor`; FQDN задаётся `SOPS_EDITOR_FQDN`.

## Один контейнер

FastAPI отдаёт и API, и статический frontend. Нет nginx, Node-сборки, БД, Alembic
и OIDC login. Поэтому нет `migrate` oneshot/initContainer и `/api/setup`.

## Key store

- `/data/server-key/keyring.json` — зашифрованный age-ключ сервера.
- `SOPS_EDITOR_KEYSTORE_KEY` — Fernet master key из Secret.
- UI позволяет только заменить ключ; API не отдаёт приватный ключ.
- Потеря Fernet key = потеря keyring. Бэкапить отдельно.
- Если keyring не задан, используется `/keys/age.txt` или ключ вкладки; приоритет:
  сессия → keyring → `/keys` → env.

## Git

- Клонирование по HTTPS/SSH; необязательная папка — sparse checkout.
- SSH deploy key и known_hosts монтируются из Secret в `/ssh`.
- Коммит: author/committer задаются в UI; email должен совпадать с GitLab.
- В сообщение добавляется `Made-with: SOPS Editor`.
- Push идёт в `origin`; GitHub-зеркало настраивается отдельно, не в приложении.

## Tag

Любой git tag запускает CI. CI собирает/retag один образ и делает bump `newTag`.
Argo Sync обязателен, если auto-sync выключен. Offline CI не входит в root pipeline.
