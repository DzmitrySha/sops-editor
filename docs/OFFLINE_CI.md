# Offline / lab build

Корневой [`.gitlab-ci.yml`](../.gitlab-ci.yml) — только ai-opo GitOps
(`tag → decide → BuildKit/retag → bump`). Offline-сборка не входит в root CI.

Конфиг проекта: [`.offline-docker.json`](../.offline-docker.json).

```powershell
$od = "D:\opo\NeuroAI\offline-docker-cli\offline-docker.cmd"
& $od discover -p .
& $od download-offline-cache -p .
& $od build-offline -p .
& $od up-offline -p .
```

Образ локальной сборки — `ai-sops-editor:local` (compose). Offline-кэш —
`offline-cache/`, он gitignored. Для обновления образа на offline VM:
`git pull`, затем `build-offline`/`up-offline`.

Этот контур не связан с тегом и Argo.
