# infra — sops-editor на ai-opo

Проект — **один контейнер** (FastAPI + статический SPA). Это осознанное отклонение
от двухсервисного `ai-template`: отдельного frontend/nginx, БД и миграций нет.

## GitOps

```text
git tag → decide-image → BuildKit/retag → ai-registry → bump newTag → Argo sync
```

- CI: корневой [`.gitlab-ci.yml`](../.gitlab-ci.yml), runner `ai-k8s`.
- Образ: `ai-registry.vitebsk.energo.net/sops-editor/sops-editor:<tag>`.
- Namespace и Argo Application: `sops-editor`.
- Манифесты: [`k8s/`](k8s/); разовый bootstrap: [`bootstrap/sops-editor.yaml`](bootstrap/sops-editor.yaml).
- CI-переменные: `GIT_PUSH_TOKEN`, `REGISTRY_PASSWORD`.
- Override авто-выбора: `BUILD_IMAGES=all|sops-editor`.

## FQDN

| Контур | Host |
|---|---|
| lab/work Compose + Traefik | `sops-editor-dev.vitebsk.energo.net` |
| prod k8s HTTPRoute | `sops-editor.vitebsk.energo.net` |

`-dev` не должен попадать в ConfigMap/HTTPRoute.

## Данные и секреты

- PVC `sops-editor-data` (`local-path`, RWO, 10Gi) → `/data`; одна реплика, `Recreate`.
- В `/data` лежат клоны, uploads, backups, логи и **зашифрованный** `server-key/keyring.json`.
- Настройки — ConfigMap `sops-editor-config`.
- Credentials — SOPS Secret `sops-editor-secret` + KSOPS; plaintext example не коммитить.
- `SOPS_EDITOR_KEYSTORE_KEY` — Fernet master key для keyring; без него хранилище серверного ключа выключено.
- `/keys/age.txt` не обязателен: продовый age-ключ импортируется через UI или кладётся в отдельный Secret.
- SSH deploy key и `known_hosts` при необходимости добавляются в Secret; Deployment монтирует их в `/ssh`.

## Bootstrap (один раз)

1. GitLab project, deploy key → Argo Secret `repo-sops-editor`.
2. Registry pull secret `ai-registry-pull` в namespace.
3. Заполнить `sops-editor-secret.enc.yaml` реальными credentials.
4. `kubectl apply -f infra/bootstrap/sops-editor.yaml`.
5. `git tag v0.1.0 && git push origin v0.1.0`; дождаться bump; Sync Argo.
6. Открыть FQDN и импортировать продовый age-ключ в «Серверный ключ».

CI не расшифровывает SOPS; это делает только KSOPS при Argo Sync.
