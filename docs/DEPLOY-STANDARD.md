# Деплой sops-editor в контуре NeuroAI

Канон: [`ai-template/docs/DEPLOY-STANDARD.md`](../../ai-template/docs/DEPLOY-STANDARD.md).
Здесь зафиксированы контракт проекта и отклонения.

## Контуры

| Контур | Как | FQDN |
|---|---|---|
| Local Docker | base Compose + `.env` + `.env.secrets` | `http://localhost:8787` |
| Lab/work Compose | `docker-compose.yml` + `docker-compose.traefik.yml` + dual env | `sops-editor-dev.vitebsk.energo.net` |
| ai-opo k8s | git tag → GitLab CI → registry → bump → Argo | `sops-editor.vitebsk.energo.net` |
| Offline VM | `offline-docker-cli` + `.offline-docker.json` (не root CI) | — |

## Что совпадает с каноном

- dual env: `docker/.env` + `docker/.env.secrets`;
- Kustomize + Argo bootstrap + SOPS/KSOPS;
- PVC `/data`, RWO, 1 replica, `Recreate`;
- tag-pipeline: decide → BuildKit/retag → bump `newTag`;
- registry `ai-registry.vitebsk.energo.net/sops-editor/sops-editor`;
- FQDN lab/work с `-dev`, prod без;
- offline config не включён в root CI;
- loguru и `{}`-логи уже используются.

## Осознанные отклонения

| Канон ai-template | Здесь | Почему |
|---|---|---|
| backend + frontend/nginx, два образа | **один image** FastAPI + static SPA | приложение статическое, отдельный nginx не нужен |
| PostgreSQL + migrate | **нет БД и миграций** | нет persistent SQL-модели; `/data` — файлы |
| OIDC / Keycloak | **Basic auth** (`SOPS_EDITOR_AUTH_MODE=basic`) | OIDC login в приложении пока не реализован; можно перейти на `header` за OIDC-прокси |
| `docker-compose.lab.yml` / `.env.lab.example` | **не нужны** | нет Keycloak/IdP-профиля |
| Pre-auth SPA + `/api/setup` | **нет** | нет OIDC/БД; вход Basic auth |
| `GET /health` | `/api/health` | исторический API приложения |

Эти отклонения не меняют контракт GitOps, PVC, SOPS и tag-pipeline.

## Проверка перед тегом

```bash
python -m pytest backend/tests -q
python -c "import yaml, pathlib; ..."   # или kustomize build
docker compose ... config
```

Деплой/тег выполняются отдельно: `git tag vX.Y.Z && git push origin vX.Y.Z`.
