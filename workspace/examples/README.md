# Примеры для ручного теста

Все зашифрованные файлы сделаны для демо-ключа из `keys/age.txt`
(публичный получатель: см. ниже). Это **учебный** ключ, не для продакшена.

```
age18d5wxjdagq0zw82ph7q57pf0s0jgrzpfcujfxsskg7up96d8w47qn8vvr4
```

## Что внутри

| Файл | Формат | Что проверить |
|---|---|---|
| `secrets/app.enc.yaml` | YAML | `encrypted_regex`: `metadata` открыта, `stringData` зашифрован; правка и сохранение через `sops edit` |
| `secrets/db.enc.yaml` | YAML | вложенные ключи, полное шифрование |
| `config.enc.json` | JSON | JSON-редактор, дерево секретов |
| `.env.enc` | dotenv | переменные окружения, `filestatus --input-type dotenv` |
| `app.enc.ini` | INI | секции и значения |
| `plain/app.yaml`, `plain/db.yaml`, `plain/config.json`, `plain/.env`, `plain/app.ini` | открытый текст | шифрование «с нуля» через интерфейс |

`.sops.yaml` описывает правила для примеров (включая `encrypted_regex` для
k8s-подобного `app.enc.yaml`).

## Быстрая проверка из терминала

```bash
# в контейнере (пути контейнера):
docker exec -it ai-sops-edit sops -d /workspace/examples/secrets/app.enc.yaml
docker exec -it ai-sops-edit sops filestatus /workspace/examples/.env.enc

# на хосте с локальными бинарниками:
.tools/sops.exe -d workspace/examples/secrets/db.enc.yaml
SOPS_AGE_KEY_FILE=keys/age.txt .tools/sops.exe filestatus workspace/examples/config.enc.json
```

## Сценарий 1. Расшифровать, изменить, сохранить

1. Вкладка **«Файл на сервере»** → `Обзор` → `examples` → `secrets` → `app.enc.yaml` → «Открыть».
2. Правка `stringData.password` → **Ctrl+S** («Сохранить»).
3. Справа «Изменения» — diff; в левой панели появится резервная копия.
4. `docker exec -it ai-sops-edit sops -d /workspace/examples/secrets/app.enc.yaml` — новый пароль.

## Сценарий 2. Зашифровать файл с компьютера

1. Вкладка **«С компьютера»** → **«Зашифровать обычный файл…»**.
2. Выберите `examples/plain/config.json`.
3. Получатели: пусто = серверный ключ (подсказка покажет `age1…`), либо вставьте свой.
4. «Зашифровать» → файл открывается уже как SOPS; в левой панели —
   «Скачать зашифрованный» / «Записать в исходный файл».

Альтернатива: перетащите обычный файл в область выбора — утилита ответит
«Файл не зашифрован SOPS» и предложит кнопку «Зашифровать этот файл…».

## Сценарий 3. Зашифровать файл на сервере

1. **«Файл на сервере»** → `Обзор` → `examples/plain` → выберите `db.yaml`.
2. Внизу окна выбора — **«Зашифровать…»**.
3. Оставьте получателей пустыми: сработает правило `.sops.yaml` (`(secrets|plain)/*.enc.yaml`).
4. Появится `plain/db.enc.yaml`, исходный `db.yaml` не изменится.

## Пересоздать примеры

```bash
PUB=$(grep -o 'age1[a-z0-9]*' keys/age.txt | head -1)
cd workspace/examples
for pair in "plain/app.yaml:secrets/app.enc.yaml:yaml" \
            "plain/db.yaml:secrets/db.enc.yaml:yaml" \
            "plain/config.json:config.enc.json:json" \
            "plain/.env:.env.enc:dotenv" \
            "plain/app.ini:app.enc.ini:ini"; do
  src="${pair%%:*}"; rest="${pair#*:}"; dst="${rest%%:*}"; fmt="${rest##*:}"
  ../../.tools/sops.exe --config .sops.yaml encrypt --input-type "$fmt" --output-type "$fmt" \
    --filename-override "$(pwd)/$dst" --output "$dst" "$src"
done
```
