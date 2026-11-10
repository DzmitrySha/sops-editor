/* RU/EN translation layer: exact phrases + regex rules, applied to the DOM.
   Keeps the Russian source in place; English comes from this dictionary. */
(() => {
  "use strict";

  const LANG_KEY = "sops-editor-lang";
  // The standalone help page switches the language through a cookie; mirror it
  // here so the app follows when the user comes back.
  const cookieLang = (document.cookie.match(/(?:^|;\s*)sops_lang=(ru|en)/) || [])[1];
  if (cookieLang && localStorage.getItem(LANG_KEY) !== cookieLang) {
    localStorage.setItem(LANG_KEY, cookieLang);
  }
  const savedLang = localStorage.getItem(LANG_KEY);
  const browserLang = (navigator.language || "ru").toLowerCase();
  const initialLang = savedLang || (browserLang.startsWith("en") ? "en" : "ru");
  const lang = initialLang === "en" ? "en" : "ru";

  function setLang(next) {
    const value = next === "en" ? "en" : "ru";
    localStorage.setItem(LANG_KEY, value);
    document.cookie = `sops_lang=${value}; path=/; max-age=31536000; SameSite=Lax`;
    location.reload();
  }

  document.addEventListener("click", (event) => {
    const button = event.target.closest(".lang-switch [data-lang]");
    if (!button) return;
    event.preventDefault();
    setLang(button.dataset.lang);
  });

  if (lang === "ru") {
    // Nothing to translate. Still expose the API so callers do not branch.
    window.i18n = {
      lang,
      tr: (text) => text,
      setLang,
    };
    document.addEventListener("DOMContentLoaded", () => {
      document.querySelectorAll(".lang-switch [data-lang]").forEach((button) => {
        button.classList.toggle("active", button.dataset.lang === "ru");
      });
    });
    return;
  }

  const EN = {
    // ---------------------------------------------------------------- shell
    "Изменения": "Changes",
    "Сохранить": "Save",
    "Секреты без ручной возни": "Secrets without the busywork",
    "Откройте SOPS-файл из подключённого каталога или git-репозитория. Редактор расшифрует его в памяти, а сохранение зашифрует обратно с теми же получателями и правилами": "Open a SOPS file from a mounted directory or a git repository. The editor decrypts it in memory and saves it back encrypted with the same recipients and .sops.yaml rules",
    "Не знаете, с чего начать? Инструкция: подключение sops, ключи, файлы с компьютера →": "New here? Guide: connecting sops, keys, files from your computer →",
    "Файл на сервере": "Server file",
    "Из git": "From git",
    "С компьютера": "From computer",
    "Путь к SOPS-файлу": "Path to the SOPS file",
    "Обзор": "Browse",
    "Открыть": "Open",
    "Файл должен лежать внутри смонтированных каталогов. Путь можно вставить из проводника сервера или локального Docker-тома.": "The file must live inside the mounted directories. Paste a path from the server explorer or the local Docker volume.",
    "URL репозитория": "Repository URL",
    "Ветка": "Branch",
    "(необязательно)": "(optional)",
    "Клонировать в /data/repos": "Clone to /data/repos",
    "Для приватных HTTPS-репозиториев задайте": "For private HTTPS repositories set",
    "на сервере. Для SSH смонтируйте ключ или агент. После клонирования откроется обзор каталога.": "on the server. For SSH mount a key or an agent. After cloning, the directory browser opens.",
    "Выберите файл на компьютере": "Choose a file on your computer",
    "Зашифрованный файл расшифруется в памяти и откроется в редакторе; открытый YAML/JSON/dotenv/INI откроется как есть, а «Зашифровать» в редакторе создаст SOPS-версию.": "An encrypted file is decrypted in memory and opened in the editor; a plain YAML/JSON/dotenv/INI file opens as is, and “Encrypt” in the editor creates a SOPS version.",
    "Выбрать файл": "Choose file",
    "Или перетащите файл сюда. Зашифрованный откроется после ввода ключа, открытый — сразу в редакторе.": "Or drag a file here. An encrypted file opens after the key is entered; a plain one opens immediately.",
    "Не знаете, как зашифровать файл и какой ключ выбрать?": "Not sure how to encrypt a file or which key to choose?",
    "Документация: файл с компьютера": "Docs: file from your computer",
    ". Ключ расшифровки задаётся в разделе «Ключ» — серверный ключ не обязателен.": ". The decryption key is set in the “Key” section — a server key is optional.",
    "Ключ": "Key",
    "Настроить": "Configure",
    "Проверяем…": "Checking…",
    "Ключ нужен, чтобы расшифровать файл.": "A key is required to decrypt the file.",
    "Впервые здесь?": "First time here?",
    "Как подключить SOPS и age-ключ": "How to connect SOPS and an age key",
    "Недавние": "Recent",
    "Очистить": "Clear",
    "Пока пусто.": "Nothing yet.",
    "Тот же механизм, что у штатного редактора: сохраняются мастер-ключи, MAC и политика шифрования.": "The same mechanism as the stock editor: master keys, MAC and the encryption policy are preserved.",
    "Только в памяти": "Memory only",
    "Открытый текст не пишется на диск. На сервере остаются лишь зашифрованные резервные копии.": "Plaintext is never written to disk. Only encrypted backups remain on the server.",
    "Git рядом": "Git at hand",
    "Статус, коммит и push из интерфейса. Обратно в репозиторий уходит уже зашифрованный файл.": "Status, commit and push from the UI. Only the encrypted file goes back to the repository.",
    "Локальный файл": "Local file",
    "Скачать": "Download",
    "Скачать зашифрованный": "Download encrypted",
    "Склонированные репозитории": "Cloned repositories",
    "Склонированных репозиториев нет.": "There are no cloned repositories.",
    "Удалить клон?": "Delete the clone?",
    "Удалить клон с сервера": "Delete the clone from the server",
    "Клон удалён": "Clone deleted",
    "Не удалось удалить клон": "Could not delete the clone",
    "Нельзя удалить клон": "Cannot delete the clone",
    "Записать в исходный файл": "Write to source file",
    "Разрешить перезапись файла…": "Allow overwriting the file…",
    "Не git-репозиторий.": "Not a git repository.",
    "Коммит": "Commit",
    "Резервные копии": "Backups",
    "Появятся после сохранения.": "They appear after saving.",
    "Зашифровано обратно?": "Encrypted back?",
    "После сохранения файл на диске уже SOPS. Проверьте": "After saving, the file on disk is already SOPS. Check",
    "и закоммитьте.": "and commit.",
    "Зашифровать": "Encrypt",
    "Перезагрузить": "Reload",
    "Секреты": "Secrets",
    "Стр 1, стлб 1": "Line 1, Col 1",
    "Содержимое": "Contents",
    "Значения скрыты": "Values hidden",
    "клик — к строке": "click to jump to line",
    "Открываем файл…": "Opening file…",
    "0.0 с": "0.0 s",
    "Сервер": "Server",
    "Выбор файла": "Choose file",
    "Обновить": "Refresh",
    "скрытые": "hidden",
    "Искать по дереву": "Search the tree",
    "Двойной клик по папке — войти, по файлу — открыть.": "Double-click a folder to enter, a file to open.",
    "Отмена": "Cancel",
    "Зашифровать…": "Encrypt…",
    "Открыть файл": "Open file",
    "Приватный ключ": "Private key",
    "Вставьте ключ": "Paste the key",
    "Загрузить .txt": "Load .txt",
    "Забыть ключ": "Forget key",
    "Сохранить в памяти": "Save in memory",
    "Ключ хранится только в памяти процесса и привязан к cookie этой вкладки. Он не пишется на диск и не попадает в логи. Ключ вкладки имеет приоритет над серверным, поэтому после его ввода файлы, зашифрованные только на серверный ключ, перестанут открываться — нажмите «Забыть ключ», чтобы вернуться к серверному. Для постоянного ключа смонтируйте": "The key is kept only in process memory and bound to this tab’s cookie. It is never written to disk or to logs. The tab key takes precedence over the server key, so after entering it, files encrypted only for the server key stop opening — press “Forget key” to return to the server key. For a permanent key mount",
    "Открывайте утилиту по HTTPS или на localhost: ключ и открытый текст идут по сети.": "Open the tool over HTTPS or on localhost: the key and plaintext travel over the network.",
    "Сообщение": "Message",
    "запушить после коммита": "push after commit",
    "Уйдёт только этот файл. Если push требует учётных данных, настройте токен/SSH на сервере.": "Only this file is committed. If push needs credentials, configure a token/SSH on the server.",
    "Закоммитить": "Commit",
    "до / после": "before / after",
    "только шифротекст": "ciphertext only",
    "Резервные копии лежат в": "Backups are stored in",
    "и содержат исходный зашифрованный файл.": "and contain the original encrypted file.",
    "Зашифровать файл": "Encrypt file",
    "Получатели — публичные age-ключи": "Recipients — public age keys",
    "Файл шифруется на сервере и остаётся только шифротекстом: из загруженного файла открытый текст удаляется сразу. Исходный файл на компьютере не меняется.": "The file is encrypted on the server and only ciphertext remains: plaintext is removed from the uploaded file immediately. The source file on your computer is unchanged.",
    "внимание": "attention",
    "Подтверждение": "Confirmation",
    "Продолжить": "Continue",

    // -------------------------------------------------------------- attributes
    "На главную": "Home",
    "К открытию файла": "Back to file selection",
    "Есть несохранённые изменения": "Unsaved changes",
    "Источник ключа расшифровки": "Decryption key source",
    "Документация: подключение sops, ключи, файлы с компьютера": "Docs: connecting sops, keys, files from your computer",
    "Документация": "Documentation",
    "Тема": "Theme",
    "Скопировать путь": "Copy path",
    "Все копии": "All backups",
    "Показать/скрыть значения": "Show/hide values",
    "Фильтр по ключам…": "Filter by keys…",
    "Закрыть": "Close",
    "Вверх": "Up",
    "Фильтр в каталоге…": "Filter in directory…",

    // ---------------------------------------------------------------- runtime
    "Сервер недоступен": "Server unreachable",
    "Сервер недоступен. Проверьте, что контейнер запущен, и обновите страницу.": "Server unreachable. Check that the container is running and reload the page.",
    "Проверяем путь и разрешения": "Checking the path and permissions",
    "Читаем файл": "Reading the file",
    "Проверяем SOPS-метаданные": "Checking SOPS metadata",
    "Расшифровываем (sops decrypt)": "Decrypting (sops decrypt)",
    "Определяем формат и метаданные": "Detecting the format and metadata",
    "Читаем состояние git": "Reading git status",
    "Открываем в редакторе": "Opening in the editor",
    "Передаём файл на сервер": "Sending the file to the server",
    "Шифруем через sops encrypt": "Encrypting with sops encrypt",
    "Проверяем документ": "Validating the document",
    "Читаем текущий шифротекст и сверяем хеш": "Reading the ciphertext and checking the hash",
    "Расшифровываем текущую версию": "Decrypting the current version",
    "Создаём резервную копию": "Creating a backup",
    "Шифруем через sops edit": "Encrypting with sops edit",
    "Проверяем результат расшифровкой": "Verifying by decryption",
    "Обновляем git-статус": "Updating git status",
    "Сервер всё ещё работает или ответ идёт по медленному каналу. Не закрывайте вкладку.": "The server is still working or the response is on a slow link. Keep the tab open.",
    "Операция продолжается: этапы обновятся, когда сервер ответит. Задержка обычно в сети или прокси.": "The operation continues: steps update when the server replies. The delay is usually the network or a proxy.",
    "Операция не выполнена": "Operation failed",
    "Операция не завершилась за 5 минут": "The operation did not finish within 5 minutes",
    "sops недоступен": "sops unavailable",
    "Basic auth включён": "Basic auth enabled",
    "Доступ без пароля": "No password",
    " — как включить": " — how to enable",
    "HTTP Basic auth выключен: сервер отвечает без пароля. Задайте SOPS_EDITOR_AUTH_PASSWORD или SOPS_EDITOR_AUTH_MODE=header. Нажмите, чтобы открыть справку.":
      "HTTP Basic auth is disabled: the server answers without a password. Set SOPS_EDITOR_AUTH_PASSWORD or SOPS_EDITOR_AUTH_MODE=header. Click to open the docs.",
    "без пароля": "no password",
    "ключ в сессии": "session key",
    "ключ сервера": "server key",
    "ключ по умолчанию": "default key",
    "ключ из окружения": "environment key",
    "ключ не задан": "no key",
    "Ключ не задан": "No key configured",
    "Вставьте приватный age-ключ или смонтируйте /keys/age.txt.": "Paste a private age key or mount /keys/age.txt.",
    "Ключ в памяти сессии": "Key in session memory",
    "Привязан к cookie этой вкладки.": "Bound to this tab’s cookie.",
    "Серверный ключ": "Server key",
    "Ключ по умолчанию": "Default key",
    "Ключ из окружения": "Key from environment",
    "SOPS_AGE_KEY или другой провайдер ключей.": "SOPS_AGE_KEY or another key provider.",
    "Открыть каталог": "Open directory",
    "Каталог ещё не смонтирован": "Directory is not mounted yet",
    "Расшифрованное содержимое появится здесь…": "Decrypted content will appear here…",
    "Пустой YAML-документ": "Empty YAML document",
    "YAML валиден": "YAML is valid",
    "JSON валиден": "JSON is valid",
    "dotenv валиден": "dotenv is valid",
    "INI валиден": "INI is valid",
    "секреты: —": "secrets: —",
    "древовидный режим недоступен": "tree view unavailable",
    "Нет совпадений.": "No matches.",
    "значения видны": "values shown",
    "значения скрыты": "values hidden",
    "(пусто)": "(empty)",
    "Значение скрыто": "Value hidden",
    "Скопировать значение": "Copy value",
    "Скопировано": "Copied",
    "Не удалось скопировать": "Could not copy",
    "Буфер обмена недоступен": "Clipboard unavailable",
    "Не найдено": "Not found",
    "Не удалось сопоставить ключ со строкой (сложный YAML).": "Could not map the key to a line (complex YAML).",
    "есть несохранённые изменения": "unsaved changes",
    "изменений нет": "no changes",
    "Несохранённые изменения": "Unsaved changes",
    "Открыть другой файл и потерять изменения?": "Open another file and lose the changes?",
    "Открываем файл": "Opening file",
    "Загружаем в редактор": "Loading into the editor",
    "Для этого файла нужен приватный ключ.": "A private key is required for this file.",
    "Не удалось открыть файл": "Could not open the file",
    "«Зашифровать» (или «Сохранить», чтобы оставить открытым)": "“Encrypt” (or “Save” to keep it plain)",
    "«Скачать зашифрованный» или «Записать в исходный файл»": "“Download encrypted” or “Write to source file”",
    "«Скачать зашифрованный» и замена файла вручную": "“Download encrypted” and replace the file manually",
    "файл с компьютера": "file from computer",
    "Скачать файл": "Download file",
    "Чтобы писать прямо в исходный файл без выбора места при каждом сохранении, один раз разрешите перезапись: выберите тот же файл в диалоге.": "To write straight into the source file without choosing a location every time, allow overwriting once: pick the same file in the dialog.",
    "Прямая запись недоступна в этом браузере: скачайте шифротекст и замените файл вручную.": "Direct writing is unavailable in this browser: download the ciphertext and replace the file manually.",
    "не зашифрован": "not encrypted",
    "открытый текст": "plaintext",
    "Вернуться к выбору файла и потерять изменения?": "Return to file selection and lose the changes?",
    "Вернуться": "Go back",
    "Прямая запись недоступна": "Direct writing unavailable",
    "Браузер не поддерживает File System Access. Скачайте файл и замените вручную.": "The browser does not support File System Access. Download the file and replace it manually.",
    "Перезапись разрешена": "Overwrite allowed",
    "Теперь «Записать в исходный файл» заменит выбранный файл.": "Now “Write to source file” replaces the chosen file.",
    "Диалог не открылся": "The dialog did not open",
    "Окно выбора закрыто или недоступно в этом браузере.": "The picker was closed or is unavailable in this browser.",
    "Файл не зашифрован SOPS. Зашифруйте его командой sops --encrypt --in-place <файл> и выберите снова. Как подключить SOPS — в справке.": "The file is not SOPS-encrypted. Run sops --encrypt --in-place <file> and choose it again. See the guide for connecting SOPS.",
    "Формат не поддерживается: нужен YAML, JSON, dotenv или INI (не бинарный файл).": "Unsupported format: use YAML, JSON, dotenv or INI (not a binary file).",
    "Файл слишком большой": "File is too large",
    "Загружаем файл": "Uploading file",
    "Файл загружен, но не расшифрован: задайте приватный ключ в окне «Ключ».": "The file is uploaded but not decrypted: set a private key in the “Key” window.",
    "Файл загружен, но сервер не смог его расшифровать: задайте подходящий age-ключ.": "The file is uploaded but the server could not decrypt it: set a matching age key.",
    "Не удалось загрузить файл": "Could not upload the file",
    "Скачивание": "Download",
    "Зашифрованный файл сохранён браузером.": "The encrypted file was saved by the browser.",
    "файл": "file",
    "Перезаписать исходный файл?": "Overwrite the source file?",
    "Перезаписать": "Overwrite",
    "браузер не выдал разрешение на запись": "the browser did not grant write permission",
    "Записано": "Written",
    "Не удалось записать файл": "Could not write the file",
    "Пусто — взять правила .sops.yaml репозитория; если правила нет — серверный ключ.": "Empty — use the repository .sops.yaml rules; if there is no rule, the server key.",
    "Файл уже зашифрован SOPS — открывайте его напрямую.": "The file is already SOPS-encrypted — open it directly.",
    "Нет подходящего правила .sops.yaml и не задан получатель. Укажите age-ключ.": "No matching .sops.yaml rule and no recipient given. Specify an age key.",
    "SOPS не смог разобрать файл. Проверьте синтаксис YAML/JSON/INI/dotenv.": "SOPS could not parse the file. Check the YAML/JSON/INI/dotenv syntax.",
    "Целевой файл уже существует.": "The target file already exists.",
    "Шифрование…": "Encrypting…",
    "Шифруем файл": "Encrypting file",
    "правила .sops.yaml": ".sops.yaml rules",
    "Зашифровано": "Encrypted",
    "Не сохранено": "Not saved",
    "Документ пуст.": "The document is empty.",
    "Исправьте синтаксис: сервер отклонит такой документ.": "Fix the syntax: the server will reject this document.",
    "Сохраняем файл": "Saving file",
    "Без изменений": "No changes",
    "Содержимое совпадает с файлом на сервере.": "The content matches the file on the server.",
    "Сохранено": "Saved",
    "Открытый файл записан. Кнопка «Зашифровать» создаст SOPS-версию.": "The plain file is written. The “Encrypt” button creates a SOPS version.",
    "Исходный файл обновлён зашифрованной версией.": "The source file was overwritten with the encrypted version.",
    "Файл скачан — замените им локальную копию.": "The file was downloaded — replace the local copy with it.",
    "Сохранено в репозиторий": "Saved to repository",
    "Файл обновлён. Нажмите «Коммит», чтобы зафиксировать изменение.": "The file was updated. Press “Commit” to record the change.",
    "Прямая запись в исходный файл недоступна в этом браузере: при сохранении будет предложено скачивание.": "Direct writing to the source file is unavailable in this browser: saving will download the file instead.",
    "Можно записать зашифрованный файл обратно в исходный.": "You can write the encrypted file back to the source.",
    "Скачайте зашифрованный файл и замените им локальный.": "Download the encrypted file and replace the local one.",
    "Файл зашифрован обратно.": "The file is encrypted back.",
    "Формат обновлён": "Format updated",
    "SOPS нормализовал отступы/комментарии при шифровании.": "SOPS normalized indentation/comments while encrypting.",
    "Файл изменился": "The file changed",
    "На сервере другая версия файла. Перезагрузить её? Несохранённые изменения будут потеряны.": "The server has a different version. Reload it? Unsaved changes will be lost.",
    "Ключ не подходит для этого файла. Задайте другой приватный ключ.": "The key does not match this file. Set another private key.",
    "есть изменения": "modified",
    "чисто": "clean",
    "нет коммитов": "no commits",
    "Введите сообщение": "Enter a message",
    "Коммит не нужен": "Commit not needed",
    "Нет изменений в индексе.": "Nothing staged.",
    "Коммит создан": "Commit created",
    "Push выполнен": "Push completed",
    "Push не выполнен": "Push failed",
    "Проверьте доступ к remote.": "Check access to the remote.",
    "Коммит не создан": "Commit failed",
    "Сначала сохраните или отмените изменения.": "Save or discard the changes first.",
    "Выполнить fast-forward pull? Если файл изменится, он будет перезагружен.": "Run a fast-forward pull? If the file changes, it will be reloaded.",
    "Pull выполнен": "Pull completed",
    "Pull не выполнен": "Pull failed",
    "Скачать зашифрованную копию": "Download the encrypted copy",
    "Изменений нет": "No changes",
    "Документ совпадает с открытой версией.": "The document matches the opened version.",
    "Не удалось открыть каталог": "Could not open the directory",
    "Нет совпадений в каталоге.": "No matches in the directory.",
    "Каталог пуст.": "The directory is empty.",
    "Поиск": "Search",
    "Введите минимум 2 символа.": "Enter at least 2 characters.",
    "Ничего не найдено.": "Nothing found.",
    "Показаны первые 200 совпадений. Уточните запрос.": "Showing the first 200 matches. Refine the query.",
    "Вставьте AGE-SECRET-KEY-1…": "Paste AGE-SECRET-KEY-1…",
    "Ключ принят": "Key accepted",
    "Ключ не принят": "Key rejected",
    "Ключ забыт": "Key forgotten",
    "Сессионный ключ удалён из памяти.": "The session key was removed from memory.",
    "Не удалось забыть ключ": "Could not forget the key",
    "Введите URL репозитория.": "Enter the repository URL.",
    "Репозиторий склонирован": "Repository cloned",
    "Клонирование не удалось": "Clone failed",
    "Перезагрузить файл": "Reload the file",
    "Несохранённые изменения будут потеряны. Продолжить?": "Unsaved changes will be lost. Continue?",
    "Путь скопирован": "Path copied",
    "Не удалось прочитать файл.": "Could not read the file.",
    "Файл": "File",
    "Укажите путь.": "Specify a path.",
    "Требуется авторизация": "Authorization required",
    "Обновите страницу и введите логин/пароль.": "Reload the page and enter your login/password.",
    "Сервер": "Server",

    // ------------------------------------------------------- server errors
    "Файл не является SOPS-файлом": "The file is not a SOPS file",
    "Для файла нет подходящего правила .sops.yaml": "No matching .sops.yaml rule for the file",
    "SOPS отклонил изменения: проверьте синтаксис YAML/JSON": "SOPS rejected the changes: check the YAML/JSON syntax",
    "SOPS не смог разобрать содержимое файла": "SOPS could not parse the file contents",
    "Файл изменился на сервере. Перезагрузите его перед сохранением.": "The file changed on the server. Reload it before saving.",
    "Файл уже зашифрован SOPS": "The file is already SOPS-encrypted",
    "Бинарные файлы не поддерживаются: нужен YAML, JSON, dotenv или INI": "Binary files are not supported: use YAML, JSON, dotenv or INI",
    "Бинарные SOPS-файлы не поддерживаются веб-редактором": "Binary SOPS files are not supported by the web editor",
    "Утилита sops не найдена в контейнере": "The sops binary was not found in the container",
    "SOPS не завершился за отведённое время": "SOPS did not finish in time",
    "Не удалось расшифровать файл: нет подходящего приватного ключа. Задайте age-ключ или проверьте SOPS_AGE_KEY_FILE.": "Could not decrypt the file: no matching private key. Set an age key or check SOPS_AGE_KEY_FILE.",
    "Не удалось расшифровать файл": "Could not decrypt the file",
    "Путь вне разрешённых каталогов": "Path is outside the allowed directories",
    "Каталог .git недоступен": "The .git directory is not accessible",
    "Файлы в .git недоступны": ".git files are not accessible",
    "Скачивание доступно только для загруженных файлов": "Download is available for uploaded files only",
    "Некорректный запрос": "Invalid request",
    "Внутренняя ошибка сервера": "Internal server error",
    "Ошибка запроса": "Request error",
    "Требуется авторизация": "Authorization required",
    "git pull не удался (нужен fast-forward)": "git pull failed (fast-forward required)",
    "git push не удался. Проверьте remote и учётные данные.": "git push failed. Check the remote and credentials.",
    "git clone не удался. Проверьте URL, ветку и доступ.": "git clone failed. Check the URL, branch and access.",
    "git не завершился за отведённое время": "git did not finish in time",
    "Клонирование git отключено (SOPS_EDITOR_ALLOW_GIT_CLONE=0)": "git clone is disabled (SOPS_EDITOR_ALLOW_GIT_CLONE=0)",
    "Некорректный git URL": "Invalid git URL",
    "Некорректное имя репозитория": "Invalid repository name",
    "Некорректное имя ветки": "Invalid branch name",
    "Репозиторий уже склонирован": "The repository is already cloned",
    "Некорректный идентификатор резервной копии": "Invalid backup id",
    "Резервная копия не найдена": "Backup not found",
    "Не удалось прочитать файл": "Could not read the file",
    "Не удалось записать зашифрованный файл": "Could not write the encrypted file",
    "Некорректный age-ключ": "Invalid age key",
    "Не найден age-ключ вида AGE-SECRET-KEY-1…": "No AGE-SECRET-KEY-1… key found",
    "В ключе есть посторонние строки": "The key contains unexpected lines",
    "age-ключ не читается (age-keygen вернул ошибку)": "The age key cannot be read (age-keygen failed)",
    "Проверка age-ключа не завершилась": "age key validation did not finish",
    "Пустой ключ": "Empty key",
    "Ключ слишком большой": "The key is too large",
    "Пустой файл": "Empty file",
    "Пустой документ": "Empty document",
    "Не передан хеш исходного файла": "The source file hash is missing",
    "Файл не найден": "File not found",
    "Это не файл": "This is not a file",
    "Это не каталог": "This is not a directory",
    "Не удалось открыть путь": "Could not open the path",
    "Не удалось прочитать каталог": "Could not read the directory",
    "Неверный Origin": "Invalid Origin",
    "Строка поиска слишком короткая": "The search query is too short",
    "Слишком много получателей (максимум 20)": "Too many recipients (maximum 20)",
    "Укажите age-получателя: у загруженного файла нет .sops.yaml, а серверный ключ не задан": "Specify an age recipient: the uploaded file has no .sops.yaml and no server key is set",
    "Нет подходящего правила .sops.yaml и не задан получатель. Укажите age-ключ.": "No matching .sops.yaml rule and no recipient. Specify an age key.",
    "Файл слишком большой": "File is too large",
    "Проверка после шифрования не прошла: содержимое изменилось. Исходный файл восстановлен.": "Post-encryption verification failed: the content changed. The original file was restored.",
    "Ошибка сохранения и не удалось восстановить исходный файл. Используйте резервную копию.": "Saving failed and the original file could not be restored. Use the backup.",
    "Слишком много получателей (максимум 20).": "Too many recipients (maximum 20).",
    "Некорректный идентификатор операции": "Invalid operation id",
    "Ключи": "Keys",
    "Ключи файла": "File keys",
    "Обновить получателей из .sops.yaml": "Update recipients from .sops.yaml",
    "Перешифровывает только ключ данных: значения не меняются, получатели берутся из .sops.yaml.": "Re-encrypts only the data key: values stay the same, recipients come from .sops.yaml.",
    "Перевыпустить ключ данных (sops rotate)": "Rotate the data key (sops rotate)",
    "Новый ключ данных: все значения перешифровываются, MAC пересчитывается.": "New data key: every value is re-encrypted and the MAC is recalculated.",
    "Выполнить": "Run",
    "Выполняем…": "Running…",
    "Ротация ключей": "Key rotation",
    "Будет создан новый ключ данных и перешифрованы все значения. Продолжить?": "A new data key will be created and every value re-encrypted. Continue?",
    "Получатели будут пересчитаны по .sops.yaml. Данные не перешифровываются. Продолжить?": "Recipients will be recalculated from .sops.yaml. Values are not re-encrypted. Continue?",
    "Получатели уже соответствуют .sops.yaml.": "Recipients already match .sops.yaml.",
    "Ключи обновлены": "Keys updated",
    "Новый набор получателей не включает текущий ключ — открытый текст проверить не удалось.": "The new recipient set does not include the current key — plaintext could not be verified.",
    "Файл не зашифрован SOPS": "The file is not SOPS-encrypted",
    "Некорректный режим ротации ключей": "Invalid key rotation mode",
    "Некорректный age-получатель в .sops.yaml": "Invalid age recipient in .sops.yaml",
    "Проверяем файл": "Checking the file",
    "Читаем шифротекст": "Reading the ciphertext",
    "Обновляем ключи": "Updating keys",
    "Проверяем результат": "Verifying the result",
    "Обновляем получателей через sops updatekeys": "Updating recipients with sops updatekeys",
    "Перевыпускаем ключ данных через sops rotate": "Rotating the data key with sops rotate",
    "SOPS не сохранил метаданные ключей": "SOPS did not keep the key metadata",
    "Проверка после ротации не прошла: содержимое изменилось. Исходный файл восстановлен.": "Post-rotation verification failed: the content changed. The original file was restored.",
    "Ошибка ротации и не удалось восстановить исходный файл. Используйте резервную копию.": "Rotation failed and the original file could not be restored. Use the backup.",
    "Дифф с HEAD": "Diff with HEAD",
    "Файл ещё не закоммичен: вся версия новая.": "The file is not committed yet: the whole version is new.",
    "Бинарный файл — дифф недоступен.": "Binary file — diff is not available.",
    "В редакторе есть несохранённые правки — в дифф они не входят.": "The editor has unsaved changes — they are not part of the diff.",
    "Проверяем путь и git": "Checking path and git",
    "Читаем текущий файл": "Reading the current file",
    "Читаем версию из HEAD": "Reading the HEAD version",
    "Файл не входит в git-репозиторий": "The file is not inside a git repository",
    "Не удалось прочитать версию из HEAD": "Could not read the HEAD version",
    "Файл:": "File:",
    "Получатели:": "Recipients:",
    "В редакторе": "In editor",
    "С HEAD": "With HEAD",
    "Источник сравнения": "Comparison source",
    "Изменений нет — документ совпадает с открытой версией.": "No changes — the document matches the opened version.",
    "строк": "lines",
    "2 панели": "2 panes",
    "1 панель": "1 pane",
    "Показать панель изменений": "Show the changes pane",
    "Скрыть панель изменений": "Hide the changes pane",
    "Файл не входит в git-репозиторий.": "The file is not inside a git repository.",
    "age1…\nage1… (необязательно)": "age1…\nage1… (optional)",
  };

  // ------------------------------------------------------- dynamic patterns
  const RULES = [
    [/^(\d+) каталог(а|ов)?$/, (m) => `${m[1]} director${m[1] === "1" ? "y" : "ies"}`],
    [/^(\d+) совпадени(е|я|й)$/, (m) => `${m[1]} match${m[1] === "1" ? "" : "es"}`],
    [/^Стр (\d+), стлб (\d+)$/, (m) => `Line ${m[1]}, Col ${m[2]}`],
    [/^(\d+(?:\.\d+)?) с$/, (m) => `${m[1]} s`],
    [/^секретов: (\d+)$/, (m) => `secrets: ${m[1]}`],
    [/^секретов: —$/, () => "secrets: —"],
    [/^сохранено (.+)$/, (m) => `saved ${m[1]}`],
    [/^Резервная копия: (.+)$/, (m) => `Backup: ${m[1]}`],
    [/^Получатели: (.+)$/, (m) => `Recipients: ${m[1]}`],
    [/^Загружаем «(.+)» \((.+)\) и расшифровываем…$/, (m) => `Uploading “${m[1]}” (${m[2]}) and decrypting…`],
    [/^«(.+)» открыт\. После правок — «Сохранить», затем (.+)\.$/, (m) => `“${m[1]}” is open. After edits — “Save”, then ${translate(m[2])}.`],
    [/^keys: не зашифрован$/, () => "keys: not encrypted"],
    [/^source: открытый текст$/, () => "source: plaintext"],
    [/^Файл зашифрован для получателей — (.+)\. Нужен приватный ключ одного из них\.$/, (m) => `The file is encrypted for ${m[1]}. A private key of one of them is required.`],
    [/^Файл «(.+)» будет заменён зашифрованной версией\. Прежнее содержимое файла будет потеряно\.$/, (m) => `File “${m[1]}” will be replaced with the encrypted version. The previous contents will be lost.`],
    [/^(.+) · (\d+) стр\.$/, (m) => `${m[1]} · ${m[2]} lines`],
    [/^(.+) · (.+)$/, (m) => `${m[1]} · ${m[2]}`],
    [/^Файл не найден: (.+)$/, (m) => `File not found: ${m[1]}`],
    [/^Каталог не найден: (.+)$/, (m) => `Directory not found: ${m[1]}`],
    [/^Файл больше лимита (\d+) байт$/, (m) => `File exceeds the ${m[1]} byte limit`],
    [/^Документ больше лимита (\d+) байт$/, (m) => `Document exceeds the ${m[1]} byte limit`],
    [/^Некорректный age-получатель: (.+)$/, (m) => `Invalid age recipient: ${m[1]}`],
    [/^В ключе есть посторонние строки$/, () => "The key contains unexpected lines"],
    [/^Путь должен быть строкой$/, () => "The path must be a string"],
    [/^Неверный Origin$/, () => "Invalid Origin"],
    [/^Файл слишком большой$/i, () => "File is too large"],
  ];

  function translate(text) {
    if (!text) return text;
    const raw = EN[text];
    if (raw) return raw;
    const trimmed = text.replace(/\s+/g, " ").trim();
    if (!trimmed) return text;
    const exact = EN[trimmed];
    if (exact) {
      return text.replace(trimmed, exact);
    }
    for (const [pattern, build] of RULES) {
      const match = pattern.exec(trimmed);
      if (match) {
        return text.replace(trimmed, build(match));
      }
    }
    return text;
  }

  window.i18n = {
    lang,
    tr: translate,
    setLang,
  };

  const SKIP_TAGS = new Set(["SCRIPT", "STYLE", "CODE", "PRE", "TEXTAREA", "INPUT", "SELECT"]);
  const ATTRS = ["placeholder", "title", "aria-label", "alt"];

  const SKIP_CONTAINERS = ".CodeMirror, #secretsList, .diff-cols, .editor-diff-body, .lang-switch";

  function skip(node) {
    const element = node.nodeType === 1 ? node : node.parentElement;
    if (!element) return true;
    if (SKIP_TAGS.has(element.tagName)) return true;
    return Boolean(element.closest(SKIP_CONTAINERS));
  }

  // Attributes are safe to translate on INPUT/TEXTAREA (placeholders), so only
  // containers with opaque user content are excluded.
  function skipAttr(element) {
    return Boolean(element.closest(SKIP_CONTAINERS));
  }

  function translateElement(root) {
    if (root.nodeType === 3) {
      if (!skip(root)) {
        const next = translate(root.nodeValue);
        if (next !== root.nodeValue) root.nodeValue = next;
      }
      return;
    }
    if (root.nodeType !== 1) return;
    if (!skipAttr(root)) {
      for (const attr of ATTRS) {
        const value = root.getAttribute(attr);
        if (value) {
          const next = translate(value);
          if (next !== value) root.setAttribute(attr, next);
        }
      }
    }
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
      acceptNode: (node) => (skip(node) ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT),
    });
    const nodes = [];
    while (walker.nextNode()) nodes.push(walker.currentNode);
    for (const node of nodes) {
      const next = translate(node.nodeValue);
      if (next !== node.nodeValue) node.nodeValue = next;
    }
    if (root.querySelectorAll) {
      root.querySelectorAll("[placeholder],[title],[aria-label],[alt]").forEach((el) => {
        if (skipAttr(el)) return;
        for (const attr of ATTRS) {
          const value = el.getAttribute(attr);
          if (value) {
            const next = translate(value);
            if (next !== value) el.setAttribute(attr, next);
          }
        }
      });
    }
  }

  document.addEventListener("DOMContentLoaded", () => {
    document.documentElement.lang = "en";
    document.querySelectorAll(".lang-switch [data-lang]").forEach((button) => {
      button.classList.toggle("active", button.dataset.lang === "en");
    });
    translateElement(document.body);
    const observer = new MutationObserver((mutations) => {
      for (const mutation of mutations) {
        for (const node of mutation.addedNodes) translateElement(node);
        if (mutation.type === "characterData" && mutation.target.nodeValue) {
          const next = translate(mutation.target.nodeValue);
          if (next !== mutation.target.nodeValue) mutation.target.nodeValue = next;
        }
        if (mutation.type === "attributes" && mutation.target.getAttribute) {
          const element = mutation.target;
          if (!skipAttr(element)) {
            const attr = mutation.attributeName;
            const value = element.getAttribute(attr);
            if (value) {
              const next = translate(value);
              if (next !== value) element.setAttribute(attr, next);
            }
          }
        }
      }
    });
    observer.observe(document.body, {
      childList: true,
      subtree: true,
      characterData: true,
      attributes: true,
      attributeFilter: ATTRS,
    });
  });
})();
