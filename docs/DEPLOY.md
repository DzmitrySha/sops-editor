# Deployment

Russian version: [ru/DEPLOY.md](ru/DEPLOY.md).

## Setups

| Setup | How to run | Access |
|---|---|---|
| Windows / localhost | `start-windows.cmd` or Compose | `127.0.0.1:8787`, no auth by default |
| Linux VM / server without Traefik | Compose + `SOPS_EDITOR_BIND=0.0.0.0` | `SOPS_EDITOR_AUTH_PASSWORD` is mandatory + HTTPS proxy |
| Shared server (dev) | base Compose + `docker-compose.traefik.yml` | shared Traefik, `sops-dev.example.com`, Basic auth |
| Shared server (prod) | k8s/HTTPRoute | `sops.example.com`, Basic auth/OIDC |
| Offline | not supported out of the box | build the image in advance and copy it |

The application is a **single container**: FastAPI serves both the API and the
static frontend. No separate nginx/frontend is needed.

## Preparing directories

```bash
mkdir -p workspace keys storage
cp docker/.env.example docker/.env
cp docker/.env.secrets.example docker/.env.secrets
chmod 700 keys
# server-side age key (optional):
cp ~/.config/sops/age/keys.txt keys/age.txt
chmod 600 keys/age.txt
```

`SOPS_EDITOR_WORKSPACE` is a path to a git checkout with SOPS files. Inside the
container it is visible as `/workspace`. Cloned repositories go to `/data/repos`,
files uploaded from the browser go to `/data/uploads` (ciphertext only, removed
after 7 days).

If a repository is already cloned on the user's machine, no mount is required:
the "From computer" tab uploads the chosen file and lets you download it back or
write it in place through the File System Access API (Chrome/Edge).

## Local (Linux)

```bash
docker compose --env-file docker/.env --env-file docker/.env.secrets up -d --build
curl -fsS http://127.0.0.1:8787/api/health
```

Port: `SOPS_EDITOR_PORT`. Bind address: `SOPS_EDITOR_BIND` (default `127.0.0.1`).

## Server behind Traefik

1. In `docker/.env`:
   ```dotenv
   SOPS_EDITOR_WORKSPACE=/srv/secrets/repo
   # dev / work:
   SOPS_EDITOR_FQDN=sops-dev.example.com
   # prod (k8s/HTTPRoute, without -dev):
   # SOPS_EDITOR_FQDN=sops.example.com
   SOPS_EDITOR_TRUST_PROXY=true
   SOPS_EDITOR_COOKIE_SECURE=true
   SOPS_EDITOR_AUTH_USER=sops
   ```
2. In `docker/.env.secrets`:
   ```dotenv
   SOPS_EDITOR_AUTH_PASSWORD=<long random password>
   SOPS_EDITOR_GIT_TOKEN=<if HTTPS git is needed>
   ```
3. Start:
   ```bash
   docker compose -f docker-compose.yml -f docker-compose.traefik.yml \
     --env-file docker/.env --env-file docker/.env.secrets up -d --build
   ```
4. Check:
   ```bash
   curl -fsS http://127.0.0.1:8787/api/health   # the local port stays loopback
   curl -u sops:... https://sops-dev.example.com/api/status
   ```

The `traefik_proxy` network must exist. The service name `ai-sops-edit` is
prefixed and does not clash with other projects. TLS terminates on Traefik.

### Behind a reverse proxy

- `SOPS_EDITOR_TRUST_PROXY=true` — read `X-Forwarded-Proto/Host`.
- `SOPS_EDITOR_COOKIE_SECURE=true` — session cookies over HTTPS only.
- Uvicorn runs with `--proxy-headers --forwarded-allow-ips=*`; trust only your
  own network/Traefik.
- Basic auth is checked by the application; Traefik can additionally enforce an
  IP allowlist.

## Permissions and the container

- The container runs as root inside its namespaces with `read_only: true`,
  `cap_drop: ALL`, `no-new-privileges`; writes are limited to `/data`,
  `/workspace` and `/tmp` (tmpfs).
- The `workspace` bind mount must be writable for the container user. On Linux
  that is usually root (Compose default); otherwise set `user:` and the directory
  owner.
- `/keys` is mounted read-only. `age.txt` must be readable by the container.
- The git repository in `/workspace` should have `user.name/email` configured;
  otherwise the server falls back to `SOPS_EDITOR_GIT_AUTHOR_NAME/EMAIL`.
- For SSH git, mount the key/agent and `known_hosts`; for HTTPS use
  `SOPS_EDITOR_GIT_TOKEN`.

## Updating

```bash
git pull
docker compose ... up -d --build
```

Post-update checks:

```bash
curl -fsS http://127.0.0.1:8787/api/health
docker compose ... logs --tail=100 ai-sops-edit
```

## Backups

- `/data/backups/<sha256(path)>/<timestamp>-<id>-<name>` — **ciphertext** of the
  file as it was before saving.
- `SOPS_EDITOR_BACKUP_KEEP` (default 10) versions are kept per file.
- Downloadable from the UI ("Backups" card).
- `storage/` (local) or the `/data` volume (server) should be covered by your
  backup policy.

## Troubleshooting

| Symptom | What to check |
|---|---|
| 401 in the browser | `SOPS_EDITOR_AUTH_PASSWORD`, the user, the `WWW-Authenticate` header |
| `key_required` | `/keys/age.txt` is mounted/readable, or a key was provided in the UI |
| `not_sops` | the file really is encrypted (`sops filestatus file`) |
| `no_creation_rule` | should not appear on `sops edit`; if it does, the file is not SOPS or is corrupted |
| `conflict` | the file changed on disk; reload it in the UI |
| `git clone` fails | `SOPS_EDITOR_GIT_TOKEN`, the host allowlist, network, branch |
| `git push` fails | remote, token/SSH, permissions; the commit itself was already created |
| container unhealthy | `docker compose logs`, `sops`/`age-keygen` present in the image |
| `failed to execute bake` on `compose build` | fallback: `docker build -t ai-sops-edit:local .`, then `docker compose ... up -d` (built into `start-windows.cmd`) |
