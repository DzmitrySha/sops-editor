# Offline / lab build

Сборка образа требует доступа в интернет (`sops`, `age`, pip-зависимости). Для
контура без сети образ собирают заранее и переносят.

В репозитории есть заготовка:

- [`docker-compose.offline.yml`](../docker-compose.offline.yml) — overlay с
  `pull_policy: never`;
- [`.offline-docker.json`](../.offline-docker.json) — конфиг описания сборки
  (образы, системные пакеты, compose-файлы) для внешнего офлайн-сборщика.

```bash
# на машине с интернетом
docker compose --env-file docker/.env --env-file docker/.env.secrets build
docker save ai-sops-editor:local -o ai-sops-editor.tar

# на офлайн-машине
docker load -i ai-sops-editor.tar
docker compose -f docker-compose.yml -f docker-compose.offline.yml \
  --env-file docker/.env --env-file docker/.env.secrets up -d
```

Образ локальной сборки — `ai-sops-editor:local`. Кэш офлайн-сборки — каталог
`offline-cache/` (gitignored). GitHub Actions в этом репозитории только
проверяет код и собирает образ без публикации.
