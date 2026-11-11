# Первый выкат на Kubernetes

Деплой этим документом **не выполняется**. Порядок для ручного первого выката
манифестов из `infra/k8s` (Kustomize + Argo CD).

## Предпосылки

- [ ] Git-репозиторий с `main` и доступным образом в вашем registry.
- [ ] Argo CD установлен; repo Secret `repo-ai-sops-editor` (deploy key) создаётся
      при подключении приватного репозитория.
- [ ] Registry pull secret `registry-pull` в namespace `ai-sops-editor`.
- [ ] DNS + Gateway SAN `sops-editor.example.com` (замените на свой домен).
- [ ] SOPS age private key на админ-машине (`~/.config/sops/age/keys.txt`) и в
      Argo CD (KSOPS).
- [ ] age-ключ SOPS для импорта в UI (не коммитить в Git).

## 1. Секрет

```bash
cp infra/k8s/ai-sops-editor-secret.example.yaml infra/k8s/ai-sops-editor-secret.enc.yaml
# заполнить AUTH_PASSWORD, KEYSTORE_KEY, при необходимости git token/SSH key
sops -e -i infra/k8s/ai-sops-editor-secret.enc.yaml
```

KSOPS расшифрует Secret при Sync; обычный CI SOPS не читает.

## 2. Bootstrap

```bash
kubectl create ns ai-sops-editor --dry-run=client -o yaml | kubectl apply -f -
kubectl -n ai-sops-editor create secret docker-registry registry-pull \
  --docker-server=registry.example.com \
  --docker-username=registry \
  --docker-password=<registry-password>

kubectl apply -f infra/bootstrap/ai-sops-editor.yaml
```

## 3. Образ и тег

Соберите образ и запушьте его в свой registry, затем обновите
`infra/k8s/kustomization.yaml`:

```yaml
images:
  - name: registry.example.com/ai-sops-editor/ai-sops-editor
    newTag: v0.1.0
```

```bash
git tag v0.1.0 && git push origin v0.1.0
```

Argo CD Sync применяет манифесты.

## 4. Smoke

```bash
kubectl -n ai-sops-editor get pods,pvc
kubectl -n ai-sops-editor get cm ai-sops-editor-config -o yaml
curl -fsS https://sops-editor.example.com/api/health
```

Открыть UI, войти Basic auth, импортировать age-ключ в «Ключ» → «Серверный
ключ», склонировать репозиторий и открыть SOPS-файл.

## Грабли

| Симптом | Проверить |
|---|---|
| Pod не стартует по `/ssh` | в Secret должны быть ключи `id_ed25519` и `known_hosts` (могут быть пустыми) |
| Argo OutOfSync | Sync вручную, если auto-sync выключен |
| Код не обновился | нужен новый image tag; restart не тянет git |
| Key store не работает | `SOPS_EDITOR_KEYSTORE_KEY` должен быть валидным Fernet key |
| SSH clone не работает | ключ в Secret, `/ssh/id_ed25519` не пустой, deploy key с записью |
| Basic auth не работает | пароль в Secret, `AUTH_MODE=basic`, HTTPS |
