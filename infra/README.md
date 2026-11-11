# infra — Kubernetes и GitOps

Проект — **один контейнер** (FastAPI + статический SPA): отдельного frontend/nginx,
БД и миграций нет.

## GitOps

```text
git tag → build image → registry → bump newTag → Argo CD sync
```

- Сборка образа и проверки: [`.github/workflows/ci.yml`](../.github/workflows/ci.yml).
- Образ: `registry.example.com/ai-sops-editor/ai-sops-editor:<tag>` (заглушка,
  замените на свой registry).
- Namespace и Argo CD Application: `ai-sops-editor`.
- Манифесты: [`k8s/`](k8s/); разовый bootstrap: [`bootstrap/ai-sops-editor.yaml`](bootstrap/ai-sops-editor.yaml).

## FQDN

| Контур | Host |
|---|---|
| Compose + Traefik | `sops-editor-dev.example.com` |
| k8s HTTPRoute | `sops-editor.example.com` |

`-dev` не должен попадать в ConfigMap/HTTPRoute.

## Данные и секреты

- PVC `ai-sops-editor-data` (`local-path`, RWO, 10Gi) → `/data`; одна реплика, `Recreate`.
- В `/data` лежат клоны, uploads, backups, логи и **зашифрованный** `server-key/keyring.json`.
- Настройки — ConfigMap `ai-sops-editor-config`.
- Credentials — SOPS Secret `ai-sops-editor-secret` + KSOPS; plaintext example не коммитить.
- `SOPS_EDITOR_KEYSTORE_KEY` — Fernet master key для keyring; без него хранилище серверного ключа выключено.
- `/keys/age.txt` не обязателен: age-ключ импортируется через UI или кладётся в отдельный Secret.
- SSH deploy key и `known_hosts` при необходимости добавляются в Secret; Deployment монтирует их в `/ssh`.

## Bootstrap (один раз)

1. Git-репозиторий и deploy key → Argo CD Secret `repo-ai-sops-editor`.
2. Registry pull secret `registry-pull` в namespace.
3. Заполнить `ai-sops-editor-secret.enc.yaml` реальными credentials.
4. `kubectl apply -f infra/bootstrap/ai-sops-editor.yaml`.
5. Собрать образ с тегом, обновить `newTag` в `k8s/kustomization.yaml`, Sync Argo CD.
6. Открыть FQDN и импортировать age-ключ в «Серверный ключ».

CI не расшифровывает SOPS; это делает только KSOPS при Sync.
