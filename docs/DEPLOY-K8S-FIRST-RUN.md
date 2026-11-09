# Первый выкат ai-sops-editor на ai-opo

Деплой этим документом **не выполняется**. Порядок для ручного первого выката.

## Предпосылки

- [ ] GitLab project `opo/neuro-ai/ai-sops-editor`, ветка `main`.
- [ ] Runner `ai-k8s`; CI variables `GIT_PUSH_TOKEN`, `REGISTRY_PASSWORD`.
- [ ] Argo repo Secret `repo-ai-sops-editor` (deploy key).
- [ ] Registry pull secret `ai-registry-pull` в namespace `ai-sops-editor`.
- [ ] DNS + Gateway SAN `ai-sops-editor.vitebsk.energo.net`.
- [ ] SOPS age private key на админ-машине (`~/.config/sops/age/keys.txt`) и в `argocd/sops-age`.
- [ ] Продовый age-ключ SOPS для импорта в UI (не коммитить в Git).

## 1. Секрет

```bash
cp infra/k8s/ai-sops-editor-secret.example.yaml infra/k8s/ai-sops-editor-secret.enc.yaml
# заполнить AUTH_PASSWORD, KEYSTORE_KEY, при необходимости git token/SSH key
sops -e -i infra/k8s/ai-sops-editor-secret.enc.yaml
```

KSOPS расшифрует Secret при Argo Sync; CI SOPS не читает.

## 2. Bootstrap

```bash
export KUBECONFIG=~/.kube/ai
kubectl create ns ai-sops-editor --dry-run=client -o yaml | kubectl apply -f -
kubectl -n ai-sops-editor create secret docker-registry ai-registry-pull \
  --docker-server=ai-registry.vitebsk.energo.net \
  --docker-username=registry \
  --docker-password="$(cat ~/.kube/ai-registry.password)"

kubectl apply -f infra/bootstrap/ai-sops-editor.yaml
```

## 3. Tag

```bash
git tag v0.1.0
git push origin v0.1.0
```

CI: `decide-image` → `build-image` (или retag) → `bump` обновляет
`infra/k8s/kustomization.yaml`; Argo Sync применяет манифесты.

## 4. Smoke

```bash
kubectl -n ai-sops-editor get pods,pvc
kubectl -n ai-sops-editor get cm ai-sops-editor-config -o yaml
curl -fsS https://ai-sops-editor.vitebsk.energo.net/api/health
```

Открыть UI, войти Basic auth, импортировать продовый ключ в
«Ключ» → «Серверный ключ», склонировать GitLab-репозиторий и открыть SOPS-файл.

## Грабли

| Симптом | Проверить |
|---|---|
| Pod не стартует по `/ssh` | в Secret должны быть ключи `id_ed25519` и `known_hosts` (могут быть пустыми) |
| Argo OutOfSync | Sync вручную, если auto-sync выключен |
| Код не обновился | нужен git tag → новый image; restart не тянет git |
| Key store не работает | `SOPS_EDITOR_KEYSTORE_KEY` должен быть валидным Fernet key |
| SSH clone не работает | ключ в Secret, `/ssh/id_ed25519` не пустой, GitLab deploy key с записью |
| Basic auth не работает | пароль в Secret, `AUTH_MODE=basic`, HTTPS |
