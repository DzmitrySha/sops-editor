# Деплой

## Контуры

| Контур | Как запускать | Доступ |
|---|---|---|
| Windows / localhost | `start-windows.cmd` или compose | `127.0.0.1:8787`, без auth по умолчанию |
| Linux VM / сервер без Traefik | compose + `SOPS_EDITOR_BIND=0.0.0.0` | обязательно `SOPS_EDITOR_AUTH_PASSWORD` + HTTPS-прокси |
| Общий сервер (dev) | base compose + `docker-compose.traefik.yml` | shared Traefik, `sops-dev.example.com`, Basic auth |
| Общий сервер (prod) | k8s/HTTPRoute | `sops.example.com` (без `-dev`), Basic auth/OIDC |
| Офлайн | не поддержано «из коробки» | образ нужно собрать заранее и перенести |

Приложение — **один контейнер**: FastAPI отдаёт API и статический frontend. Отдельный nginx/frontend не нужен; это осознанное отклонение от шаблона `ai-template`.

## Подготовка каталогов

```bash
mkdir -p workspace keys storage
cp docker/.env.example docker/.env
cp docker/.env.secrets.example docker/.env.secrets
chmod 700 keys
# age-ключ (если нужен серверный):
cp ~/.config/sops/age/keys.txt keys/age.txt
chmod 600 keys/age.txt
```

`SOPS_EDITOR_WORKSPACE` — путь к git-чекауту с SOPS-файлами. Внутри контейнера он виден как `/workspace`. Клонированные репозитории — `/data/repos`, файлы, загруженные из браузера, — `/data/uploads` (только шифротекст, чистятся через 7 дней).

Если репозиторий уже склонирован на компьютере пользователя, mount не обязателен: вкладка «С компьютера» загружает выбранный файл, а после правок его можно скачать обратно или записать через File System Access API (Chrome/Edge).

## Локально (Linux)

```bash
docker compose --env-file docker/.env --env-file docker/.env.secrets up -d --build
curl -fsS http://127.0.0.1:8787/api/health
```

Порт меняется `SOPS_EDITOR_PORT`. Привязка — `SOPS_EDITOR_BIND` (по умолчанию `127.0.0.1`).

## Сервер за Traefik

1. В `docker/.env`:
   ```dotenv
   SOPS_EDITOR_WORKSPACE=/srv/secrets/repo
   # dev / work:
   SOPS_EDITOR_FQDN=sops-dev.example.com
   # prod (k8s/HTTPRoute, без -dev):
   # SOPS_EDITOR_FQDN=sops.example.com
   SOPS_EDITOR_TRUST_PROXY=true
   SOPS_EDITOR_COOKIE_SECURE=true
   SOPS_EDITOR_AUTH_USER=sops
   ```
2. В `docker/.env.secrets`:
   ```dotenv
   SOPS_EDITOR_AUTH_PASSWORD=<длинный случайный пароль>
   SOPS_EDITOR_GIT_TOKEN=<если нужен HTTPS git>
   ```
3. Запуск:
   ```bash
   docker compose -f docker-compose.yml -f docker-compose.traefik.yml \
     --env-file docker/.env --env-file docker/.env.secrets up -d --build
   ```
4. Проверка:
   ```bash
   curl -fsS http://127.0.0.1:8787/api/health   # local port остаётся loopback
   curl -u sops:... https://sops-dev.example.com/api/status
   ```

Сеть `traefik_proxy` должна существовать. Service name `ai-sops-edit` префиксован и не пересекается с другими проектами. TLS — на Traefik.

### За обратным прокси

- `SOPS_EDITOR_TRUST_PROXY=true` — читать `X-Forwarded-Proto/Host`.
- `SOPS_EDITOR_COOKIE_SECURE=true` — cookie сессии только по HTTPS.
- Uvicorn запускается с `--proxy-headers --forwarded-allow-ips=*`; доверяйте только своей сети/Traefik.
- Basic auth проверяется приложением; Traefik можно дополнительно закрыть IP allowlist.

## Права и контейнер

- Контейнер работает от root внутри namespaces, `read_only: true`, `cap_drop: ALL`, `no-new-privileges`; запись только в `/data`, `/workspace`, `/tmp` (tmpfs).
- Bind-mount `workspace` должен быть доступен на запись пользователю, от которого запускается контейнер. В Linux это обычно root (compose по умолчанию), либо задайте `user:` и владельца каталога.
- `/keys` монтируется read-only. Файл `age.txt` должен быть читаем контейнеру.
- Git-репозиторий в `/workspace` должен иметь настроенные `user.name/email`; иначе сервер подставит `SOPS_EDITOR_GIT_AUTHOR_NAME/EMAIL`.
- Для SSH-git смонтируйте ключ/agent и `known_hosts`; для HTTPS — `SOPS_EDITOR_GIT_TOKEN`.

## Обновление

```bash
git pull
docker compose ... up -d --build
```

Проверка после обновления:

```bash
curl -fsS http://127.0.0.1:8787/api/health
docker compose ... logs --tail=100 ai-sops-edit
```

## Резервные копии

- `/data/backups/<sha256(path)>/<timestamp>-<id>-<name>` — **шифротекст** исходного файла перед сохранением.
- Хранится `SOPS_EDITOR_BACKUP_KEEP` последних версий на файл (по умолчанию 10).
- Скачать можно из UI (карточка «Резервные копии»).
- `storage/` (локально) или volume `/data` (сервер) должны попадать в вашу политику бэкапов.

## Диагностика

| Симптом | Что проверить |
|---|---|
| 401 в браузере | `SOPS_EDITOR_AUTH_PASSWORD`, пользователь, заголовок `WWW-Authenticate` |
| `key_required` | `/keys/age.txt` смонтирован/читаем, либо ключ задан в UI |
| `not_sops` | файл действительно зашифрован (`sops filestatus file`) |
| `no_creation_rule` | не должен появляться при `sops edit`; если появился — файл не SOPS/повреждён |
| `conflict` | файл изменился на диске; перезагрузите в UI |
| `git clone` fail | `SOPS_EDITOR_GIT_TOKEN`, allowlist хостов, сеть, ветка |
| `git push` fail | remote, токен/SSH, права; коммит при этом уже создан |
| контейнер unhealthy | `docker compose logs`, наличие `sops`/`age-keygen` в образе |
| `failed to execute bake` при `compose build` | fallback: `docker build -t ai-sops-edit:local .` затем `docker compose ... up -d` (в `start-windows.cmd` уже есть) |
