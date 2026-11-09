# Деплой

## Контуры

| Контур | Как запускать | Доступ |
|---|---|---|
| Windows / localhost | `start-windows.cmd` или compose | `127.0.0.1:8787`, без auth по умолчанию |
| Linux VM / сервер без Traefik | compose + `SOPS_EDITOR_BIND=0.0.0.0` | обязательно `SOPS_EDITOR_AUTH_PASSWORD` + HTTPS-прокси |
| Общий сервер (dev) | base compose + `docker-compose.traefik.yml` | shared Traefik, `sops-editor-dev.vitebsk.energo.net`, Basic auth |
| Общий сервер (prod) | k8s/HTTPRoute | `sops-editor.vitebsk.energo.net` (без `-dev`), Basic auth |
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
   SOPS_EDITOR_FQDN=sops-editor-dev.vitebsk.energo.net
   # prod (k8s/HTTPRoute, без -dev):
   # SOPS_EDITOR_FQDN=sops-editor.vitebsk.energo.net
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
   curl -u sops:... https://sops-editor-dev.vitebsk.energo.net/api/status
   ```

Сеть `traefik_proxy` должна существовать. Service name `ai-sops-edit` префиксован и не пересекается с другими проектами. TLS — на Traefik.

### За обратным прокси

- `SOPS_EDITOR_TRUST_PROXY=true` — читать `X-Forwarded-Proto/Host`.
- `SOPS_EDITOR_COOKIE_SECURE=true` — cookie сессии только по HTTPS.
- Uvicorn запускается с `--proxy-headers --forwarded-allow-ips=*`; доверяйте только своей сети/Traefik.
- Basic auth проверяется приложением; Traefik можно дополнительно закрыть IP allowlist.

### Аутентификация

`SOPS_EDITOR_AUTH_MODE` выбирает способ аутентификации запроса.

| Режим | Что проверяется | Когда использовать |
|---|---|---|
| `basic` (по умолчанию) | `Authorization: Basic`, пользователь `SOPS_EDITOR_AUTH_USER`, пароль `SOPS_EDITOR_AUTH_PASSWORD` | прямой доступ, небольшая команда |
| `header` | заголовок `SOPS_EDITOR_AUTH_HEADER` (по умолчанию `X-Forwarded-User`) непустой | OIDC-прокси (oauth2-proxy, Authentik, Traefik forward-auth) уже аутентифицировал пользователя |
| `none` | ничего (в остальных режимах публичен только `/api/health`) | демо на loopback |

```dotenv
SOPS_EDITOR_AUTH_MODE=header
SOPS_EDITOR_AUTH_HEADER=X-Forwarded-User
SOPS_EDITOR_AUTH_TRUSTED_PROXIES=172.18.0.0/16
```

`SOPS_EDITOR_AUTH_TRUSTED_PROXIES` — список IP/CIDR через запятую. Если задан,
заголовок принимается только когда TCP-peer входит в эти сети; если пуст —
заголовок принимается с любого адреса, и контейнер пишет предупреждение при
старте. В режиме `header` ответ `401` идёт **без** `WWW-Authenticate`, поэтому
браузер не показывает окно пароля.

**Нельзя выставлять режим `header` напрямую.** Заголовок подделывается тривиально:
порт только на loopback или внутренней сети, доступ — только через прокси.

Пример oauth2-proxy + Traefik:

```yaml
ai-sops-edit:
  labels:
    - traefik.http.routers.ai-sops-edit.middlewares=oauth2-proxy@docker
  environment:
    SOPS_EDITOR_AUTH_MODE: header
    SOPS_EDITOR_AUTH_TRUSTED_PROXIES: 172.18.0.0/16
```

Проверка без браузера:

```bash
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8787/api/status              # 401
curl -s -H 'X-Forwarded-User: alice' http://127.0.0.1:8787/api/status | grep alice      # user alice
```

## Права и контейнер

- Контейнер работает от root внутри namespaces, `read_only: true`, `cap_drop: ALL`, `no-new-privileges`; запись только в `/data`, `/workspace`, `/tmp` (tmpfs).
- Bind-mount `workspace` должен быть доступен на запись пользователю, от которого запускается контейнер. В Linux это обычно root (compose по умолчанию), либо задайте `user:` и владельца каталога.
- `/keys` монтируется read-only. Файл `age.txt` должен быть читаем контейнеру.
- Git-репозиторий в `/workspace` должен иметь настроенные `user.name/email`; иначе сервер подставит `SOPS_EDITOR_GIT_AUTHOR_NAME/EMAIL`.
- Автор коммита: смонтируйте host `~/.gitconfig` read-only в `/gitconfig` (`SOPS_EDITOR_HOST_GITCONFIG=$HOME/.gitconfig`), чтобы поля имени/email предзаполнялись. Пользователь может их изменить; email должен совпадать с подтверждённым email в GitLab. В сообщение добавляется `Made-with: SOPS Editor`.
- Git-SSH: задайте `SOPS_EDITOR_HOST_SSH_DIR=$HOME/.ssh` (Compose монтирует каталог read-only в `/ssh`). Приватный `id_ed25519`/`id_ecdsa`/`id_rsa` находится автоматически; можно указать `SOPS_EDITOR_GIT_SSH_KEY=/ssh/<ключ>`. На время операции ключ копируется в файл `0600`; `known_hosts` копируется в `/data/ssh/known_hosts`. Для agent пробросьте `SSH_AUTH_SOCK` в контейнер. Для HTTPS — `SOPS_EDITOR_GIT_TOKEN`.
- Sparse-клон: необязательное поле «Папка» запускает `git clone --filter=blob:none --sparse` и оставляет только эту папку и корневые файлы вроде `.sops.yaml`.
- Зашифрованный серверный ключ: хранилище `/data/server-key/keyring.json`, мастер-ключ `SOPS_EDITOR_KEYSTORE_KEY` (Fernet). UI может только заменить ключ; API не отдаёт приватный ключ. Мастер-ключ генерируется один раз и хранится с секретами деплоя: `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`. Без него хранилище выключено, но `/keys/age.txt` продолжает работать.

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
