/* SOPS Editor — single-page UI. No inline handlers, no external requests. */
(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const RECENT_KEY = "sops-editor-recent-v1";
  const THEME_KEY = "sops-editor-theme";
  const REVEAL_KEY = "sops-editor-reveal";
  const MAX_SECRET_ROWS = 800;

  const state = {
    status: null,
    key: null,
    file: null,
    original: "",
    editor: null,
    suppressChange: 0,
    dirty: false,
    busy: false,
    showValues: sessionStorage.getItem(REVEAL_KEY) === "1",
    secretsOpen: true,
    parsed: null,
    valid: false,
    leaves: [],
    pendingPath: null,
    pendingKeyInfo: null,
    pendingPlainFile: null,
    pendingGitDiff: null,
    encrypt: null,
    progressTimer: null,
    progressStarted: 0,
    local: null,
    picker: {
      path: null,
      parent: null,
      entries: [],
      selected: null,
      hidden: false,
      search: false,
    },
    backups: [],
    confirmResolver: null,
  };

  class ApiError extends Error {
    constructor(status, error, payload) {
      const raw = (error && error.message) || `HTTP ${status}`;
      super(window.i18n ? window.i18n.tr(raw) : raw);
      this.name = "ApiError";
      this.status = status;
      this.code = (error && error.code) || "http_error";
      this.details = (error && error.details) || {};
      this.payload = payload;
    }
  }

  async function api(path, options = {}) {
    const method = options.method || "GET";
    const headers = {
      Accept: "application/json",
      "X-SOPS-Editor": "1",
      ...(options.headers || {}),
    };
    if (options.body !== undefined) headers["Content-Type"] = "application/json";
    let response;
    try {
      response = await fetch(path, {
        method,
        headers,
        credentials: "same-origin",
        body: options.body === undefined ? undefined : JSON.stringify(options.body),
        signal: options.signal,
      });
    } catch (error) {
      if (error.name === "AbortError") throw error;
      throw new ApiError(0, { code: "network", message: "Сервер недоступен" });
    }
    let payload = null;
    const contentType = response.headers.get("content-type") || "";
    if (contentType.includes("application/json")) {
      try { payload = await response.json(); } catch { payload = null; }
    }
    if (!response.ok) {
      const error = (payload && payload.error) || {
        code: "http_" + response.status,
        message: `Ошибка ${response.status}`,
      };
      throw new ApiError(response.status, error, payload);
    }
    return payload;
  }

  function escapeHtml(value) {
    return String(value)
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#39;");
  }

  function formatBytes(bytes) {
    if (bytes === null || bytes === undefined) return "—";
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KiB`;
    return `${(bytes / (1024 * 1024)).toFixed(2)} MiB`;
  }

  function formatDate(value) {
    if (!value) return "—";
    const date = typeof value === "number"
      ? new Date(value > 1e12 ? value : value * 1000)
      : new Date(value);
    if (Number.isNaN(date.getTime())) return String(value);
    return date.toLocaleString("ru-RU", { dateStyle: "short", timeStyle: "short" });
  }

  function debounce(fn, wait) {
    let timer = null;
    return (...args) => {
      clearTimeout(timer);
      timer = setTimeout(() => fn(...args), wait);
    };
  }

  function toast(title, text = "", kind = "") {
    const holder = $("toasts");
    const node = document.createElement("div");
    node.className = `toast ${kind}`.trim();
    const icon = document.createElement("span");
    icon.className = "toast-title";
    icon.textContent = title;
    const body = document.createElement("span");
    body.className = "toast-text";
    body.textContent = text;
    const wrap = document.createElement("div");
    wrap.append(icon, body);
    node.append(wrap);
    holder.append(node);
    setTimeout(() => {
      node.style.opacity = "0";
      node.style.transform = "translateY(4px)";
      setTimeout(() => node.remove(), 220);
    }, kind === "bad" ? 7000 : 4200);
  }

  // ---------------------------------------------------------------- progress
  const OPEN_STEPS = [
    ["resolve", "Проверяем путь и разрешения"],
    ["read", "Читаем файл"],
    ["check", "Проверяем SOPS-метаданные"],
    ["decrypt", "Расшифровываем (sops decrypt)"],
    ["format", "Определяем формат и метаданные"],
    ["git", "Читаем состояние git"],
    ["apply", "Открываем в редакторе"],
  ];
  const OPEN_TAIL = [
    ["resolve", "Проверяем путь и разрешения"],
    ["read", "Читаем файл"],
    ["decrypt", "Расшифровываем (sops decrypt)"],
    ["format", "Определяем формат и метаданные"],
    ["git", "Читаем состояние git"],
    ["apply", "Открываем в редакторе"],
  ];
  const UPLOAD_STEPS = [
    ["upload", "Передаём файл на сервер"],
    ...OPEN_TAIL,
  ];
  const ENCRYPT_STEPS = [
    ["encrypt", "Шифруем через sops encrypt"],
    ...OPEN_TAIL,
  ];
  const SAVE_STEPS = [
    ["validate", "Проверяем документ"],
    ["read", "Читаем текущий шифротекст и сверяем хеш"],
    ["decrypt", "Расшифровываем текущую версию"],
    ["backup", "Создаём резервную копию"],
    ["encrypt", "Шифруем через sops edit"],
    ["verify", "Проверяем результат расшифровкой"],
    ["git", "Обновляем git-статус"],
  ];
  const ROTATE_STEPS = [
    ["resolve", "Проверяем файл"],
    ["read", "Читаем шифротекст"],
    ["decrypt", "Расшифровываем текущую версию"],
    ["backup", "Создаём резервную копию"],
    ["rekey", "Обновляем ключи"],
    ["verify", "Проверяем результат"],
    ["git", "Обновляем git-статус"],
  ];
  const GIT_DIFF_STEPS = [
    ["resolve", "Проверяем путь и git"],
    ["read", "Читаем текущий файл"],
    ["decrypt", "Расшифровываем текущую версию"],
    ["head", "Читаем версию из HEAD"],
    ["git", "Читаем состояние git"],
  ];

  function progressItem(phase, text, className) {
    const item = document.createElement("li");
    item.className = className;
    item.dataset.phase = phase;
    const dot = document.createElement("span");
    dot.className = "dot";
    const label = document.createElement("span");
    label.className = "step-text";
    label.textContent = text;
    item.append(dot, label);
    return item;
  }

  function showProgress(title, subtitle, steps) {
    $("progressTitle").textContent = title;
    $("progressFile").textContent = subtitle || "";
    const list = $("progressSteps");
    list.innerHTML = "";
    for (const [phase, text] of steps || []) {
      list.append(progressItem(phase, text, "pending"));
    }
    $("progressTime").textContent = "0.0 с";
    $("progressHint").classList.add("hidden");
    $("progressOverlay").classList.remove("hidden");
    clearInterval(state.progressTimer);
    state.progressStarted = Date.now();
    state.progressTimer = setInterval(() => {
      const seconds = (Date.now() - state.progressStarted) / 1000;
      $("progressTime").textContent = `${seconds.toFixed(seconds < 10 ? 1 : 0)} с`;
      const hint = $("progressHint");
      if (seconds >= 5) {
        hint.textContent =
          seconds >= 20
            ? "Сервер всё ещё работает или ответ идёт по медленному каналу. Не закрывайте вкладку."
            : "Операция продолжается: этапы обновятся, когда сервер ответит. Задержка обычно в сети или прокси.";
        hint.classList.remove("hidden");
      }
    }, 100);
  }

  function progressStep(text, phase) {
    if (!text) return;
    const list = $("progressSteps");
    let items = [...list.children];
    const activeIndex = items.findIndex((item) => item.classList.contains("active"));
    const pendingIndex = items.findIndex((item) => item.classList.contains("pending"));
    const current = activeIndex >= 0 ? activeIndex : pendingIndex >= 0 ? pendingIndex : items.length;
    let index = phase ? items.findIndex((item) => item.dataset.phase === phase) : activeIndex;
    if (index >= 0 && index < current) return; // late or duplicate event: never move backwards
    if (index < 0) {
      // Unexpected phase from a newer server: append it at the end.
      const item = progressItem(phase || `step-${items.length}`, text, "pending");
      list.append(item);
      items = [...list.children];
      index = items.length - 1;
    }
    items[index].querySelector(".step-text").textContent = text;
    items.forEach((item, position) => {
      item.classList.toggle("done", position < index);
      item.classList.toggle("active", position === index);
      item.classList.remove("pending");
    });
    list.scrollTop = list.scrollHeight;
  }

  function hideProgress() {
    clearInterval(state.progressTimer);
    state.progressTimer = null;
    for (const item of $("progressSteps").children) {
      item.classList.remove("active", "pending");
      item.classList.add("done");
    }
    $("progressOverlay").classList.add("hidden");
  }

  function newOpId() {
    if (window.crypto && window.crypto.randomUUID) return window.crypto.randomUUID().replaceAll("-", "");
    return `${Date.now().toString(36)}${Math.random().toString(36).slice(2, 10)}`;
  }

  // Polls the server-side operation log so steps advance even when the proxy
  // buffers the response of the main request.
  function pollOperation(opId, { onEvent, onDone, onError }) {
    let stopped = false;
    let seen = 0;
    const tick = async () => {
      if (stopped) return;
      try {
        const response = await fetch(`/api/ops/${encodeURIComponent(opId)}`, {
          headers: { "X-SOPS-Editor": "1" },
          credentials: "same-origin",
        });
        if (response.ok) {
          const state = await response.json();
          for (let index = seen; index < state.events.length; index += 1) {
            onEvent(state.events[index]);
          }
          seen = state.events.length;
          if (state.status === "done") {
            stopped = true;
            onDone(state.result);
            return;
          }
          if (state.status === "error") {
            stopped = true;
            onError(state.error);
            return;
          }
        }
      } catch {
        /* polling is best effort */
      }
      setTimeout(tick, 250);
    };
    setTimeout(tick, 120);
    return () => { stopped = true; };
  }

  async function trackedOperation({ url, method = "GET", body = null, rawBody = null, steps, title, subtitle, keepProgress = false }) {
    const opId = newOpId();
    const controller = new AbortController();
    let settled = false;
    let stop = () => {};
    let resolveFn = null;
    let rejectFn = null;
    const done = new Promise((resolve, reject) => {
      resolveFn = resolve;
      rejectFn = reject;
    });
    const settle = (fn, value) => {
      if (settled) return;
      settled = true;
      stop();
      controller.abort();
      fn(value);
    };

    if (keepProgress) {
      $("progressTitle").textContent = title;
      $("progressFile").textContent = subtitle || "";
    } else {
      showProgress(title, subtitle, steps);
    }
    stop = pollOperation(opId, {
      onEvent: (event) => progressStep(event.text, event.phase),
      onDone: (result) => settle(resolveFn, result),
      onError: (error) => settle(rejectFn, new ApiError((error && error.status) || 422, error || { code: "operation_failed", message: "Операция не выполнена" })),
    });

    const headers = { Accept: "application/json", "X-SOPS-Editor": "1" };
    let requestBody;
    if (rawBody) {
      headers["Content-Type"] = "application/octet-stream";
      requestBody = rawBody;
    } else if (body !== null) {
      headers["Content-Type"] = "application/json";
      requestBody = JSON.stringify(body);
    }
    const separator = url.includes("?") ? "&" : "?";
    const timeout = setTimeout(() => {
      settle(rejectFn, new ApiError(0, { code: "timeout", message: "Операция не завершилась за 5 минут" }));
    }, 300000);

    fetch(`${url}${separator}op=${encodeURIComponent(opId)}`, {
      method,
      headers,
      credentials: "same-origin",
      body: requestBody,
      signal: controller.signal,
    })
      .then(async (response) => {
        if (!response.ok) {
          let payload = null;
          try { payload = await response.json(); } catch { payload = null; }
          throw new ApiError(
            response.status,
            (payload && payload.error) || { code: "http_" + response.status, message: `Ошибка ${response.status}` },
            payload,
          );
        }
        return response.json();
      })
      .then((result) => settle(resolveFn, result))
      .catch((error) => {
        if (error && error.name === "AbortError") return;
        settle(rejectFn, error);
      });

    try {
      return await done;
    } finally {
      clearTimeout(timeout);
      stop();
      controller.abort();
    }
  }

  // ------------------------------------------------------------------ modals
  function openModal(id) {
    const modal = $(id);
    if (!modal) return;
    modal.classList.remove("hidden");
    const focusable = modal.querySelector("input, textarea, button");
    if (focusable) setTimeout(() => focusable.focus(), 30);
  }

  function closeModal(id) {
    const modal = $(id);
    if (!modal) return;
    modal.classList.add("hidden");
    if (id === "confirmModal" && state.confirmResolver) {
      const resolve = state.confirmResolver;
      state.confirmResolver = null;
      resolve(false);
    }
  }

  function closeTopModal() {
    const open = [...document.querySelectorAll(".modal-backdrop:not(.hidden)")].pop();
    if (open) closeModal(open.id);
  }

  function showConfirm({ title = "Подтверждение", text, okLabel = "Продолжить", danger = true }) {
    if (state.confirmResolver) {
      state.confirmResolver(false);
      state.confirmResolver = null;
    }
    $("confirmTitle").textContent = title;
    $("confirmText").textContent = text;
    $("confirmOk").textContent = okLabel;
    $("confirmOk").className = `btn ${danger ? "danger" : "primary"}`;
    openModal("confirmModal");
    return new Promise((resolve) => { state.confirmResolver = resolve; });
  }

  function bindModalDismiss() {
    document.querySelectorAll("[data-close]").forEach((button) => {
      button.addEventListener("click", () => closeModal(button.dataset.close));
    });
    document.querySelectorAll(".modal-backdrop").forEach((backdrop) => {
      backdrop.addEventListener("mousedown", (event) => {
        if (event.target === backdrop) closeModal(backdrop.id);
      });
    });
    $("confirmOk").addEventListener("click", () => {
      const resolve = state.confirmResolver;
      state.confirmResolver = null;
      closeModal("confirmModal");
      if (resolve) resolve(true);
    });
    $("confirmCancel").addEventListener("click", () => {
      const resolve = state.confirmResolver;
      state.confirmResolver = null;
      closeModal("confirmModal");
      if (resolve) resolve(false);
    });
  }

  // -------------------------------------------------------------------- theme
  function applyTheme(theme) {
    document.documentElement.dataset.theme = theme;
    $("themeGlyph").textContent = theme === "dark" ? "◐" : "☀";
    try { localStorage.setItem(THEME_KEY, theme); } catch { /* ignore */ }
    try {
      document.cookie = `sops_theme=${theme}; path=/; max-age=31536000; SameSite=Lax`;
    } catch { /* ignore */ }
    if (state.editor) setTimeout(() => state.editor.refresh(), 20);
  }

  function toggleTheme() {
    applyTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark");
  }

  // --------------------------------------------------------------- status/key
  async function loadStatus() {
    const status = await api("/api/status");
    state.status = status;
    state.key = status.key;
    renderStatus();
    renderRecent();
  }

  async function loadHealth() {
    try {
      const health = await api("/api/health");
      $("sopsVersion").textContent = health.sops || "sops";
    } catch {
      $("sopsVersion").textContent = "sops недоступен";
      $("sopsVersion").classList.add("bad");
    }
  }

  function renderStatus() {
    const status = state.status;
    if (!status) return;
    const key = state.key || status.key || {};
    $("rootCount").textContent = `${status.roots.length} каталог${status.roots.length === 1 ? "" : "а"}`;
    $("authState").textContent = status.auth_enabled ? "Basic auth включён" : "Доступ без пароля";
    $("authState").className = `pill ${status.auth_enabled ? "" : "warn"}`;
    $("authChip").hidden = status.auth_enabled;
    $("authChip").textContent = "без пароля";
    $("authChip").className = "chip warn";
    $("btnBrowse").disabled = !status.roots.length;

    const labels = {
      session: "ключ в сессии",
      server_file: "ключ сервера",
      server_default: "ключ по умолчанию",
      environment: "ключ из окружения",
      none: "ключ не задан",
    };
    const source = key.source || "none";
    $("keyChip").textContent = labels[source] || source;
    $("keyChip").className = `chip ${source === "none" ? "bad" : "ok"}`;
    $("keyChip").title = key.recipients && key.recipients.length
      ? `Получатели: ${key.recipients.join(", ")}`
      : "Источник приватного ключа";

    const title = $("keyStateTitle");
    const text = $("keyStateText");
    if (source === "none") {
      title.textContent = "Ключ не задан";
      text.textContent = "Вставьте приватный age-ключ или смонтируйте /keys/age.txt.";
    } else if (source === "session") {
      title.textContent = "Ключ в памяти сессии";
      text.textContent = key.recipients && key.recipients.length
        ? `Получатель: ${key.recipients.join(", ")}`
        : "Привязан к cookie этой вкладки.";
    } else if (source === "server_file") {
      title.textContent = "Серверный ключ";
      text.textContent = key.server_key_file || "/keys/age.txt";
    } else if (source === "server_default") {
      title.textContent = "Ключ по умолчанию";
      text.textContent = "~/.config/sops/age/keys.txt";
    } else {
      title.textContent = "Ключ из окружения";
      text.textContent = "SOPS_AGE_KEY или другой провайдер ключей.";
    }

    const rootLinks = $("rootLinks");
    rootLinks.innerHTML = "";
    for (const root of status.roots) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "root-link";
      button.textContent = root.path;
      button.title = root.exists ? "Открыть каталог" : "Каталог ещё не смонтирован";
      button.addEventListener("click", () => {
        $("pathInput").value = root.path;
        openPicker(root.path);
      });
      rootLinks.append(button);
    }
    if (!status.roots.length) {
      rootLinks.innerHTML = '<span class="hint">Каталоги не заданы: SOPS_EDITOR_ROOTS.</span>';
    }
    renderKeyChip();
  }

  function renderKeyChip() {
    if (!state.key) return;
    const source = state.key.source || "none";
    const labels = {
      session: "ключ в сессии",
      server_file: "ключ сервера",
      server_default: "ключ по умолчанию",
      environment: "ключ из окружения",
      none: "ключ не задан",
    };
    $("keyChip").textContent = labels[source] || source;
    $("keyChip").className = `chip ${source === "none" ? "bad" : "ok"}`;
  }

  async function refreshKeyStatus() {
    try {
      state.key = await api("/api/session");
      renderKeyChip();
      renderKeyModal();
    } catch (error) {
      toast("Ключ", error.message, "bad");
    }
  }

  // ----------------------------------------------------------------- recents
  function loadRecents() {
    try {
      const raw = localStorage.getItem(RECENT_KEY);
      state.recents = raw ? JSON.parse(raw) : [];
      if (!Array.isArray(state.recents)) state.recents = [];
    } catch {
      state.recents = [];
    }
    renderRecent();
  }

  function addRecent(file) {
    const item = { path: file.path, name: file.name, at: Date.now() };
    state.recents = [item, ...(state.recents || []).filter((entry) => entry.path !== item.path)].slice(0, 8);
    try { localStorage.setItem(RECENT_KEY, JSON.stringify(state.recents)); } catch { /* ignore */ }
    renderRecent();
  }

  function renderRecent() {
    const holder = $("recentList");
    if (!holder) return;
    holder.innerHTML = "";
    if (!state.recents || !state.recents.length) {
      holder.innerHTML = '<p class="muted small">Пока пусто.</p>';
      return;
    }
    for (const entry of state.recents) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "recent-item";
      button.title = entry.path;
      button.innerHTML = '<svg><use href="#i-file"/></svg><div><div class="recent-name"></div><div class="recent-path"></div></div>';
      button.querySelector(".recent-name").textContent = entry.name || entry.path.split("/").pop();
      button.querySelector(".recent-path").textContent = entry.path;
      button.addEventListener("click", () => openFile(entry.path));
      holder.append(button);
    }
  }

  // ------------------------------------------------------------------ editor
  function setupEditor() {
    state.editor = CodeMirror.fromTextArea($("editor"), {
      mode: "yaml",
      lineNumbers: true,
      lineWrapping: false,
      indentUnit: 2,
      tabSize: 2,
      smartIndent: true,
      matchBrackets: true,
      autoCloseBrackets: true,
      styleActiveLine: true,
      foldGutter: true,
      gutters: ["CodeMirror-linenumbers", "CodeMirror-foldgutter"],
      placeholder: "Расшифрованное содержимое появится здесь…",
      extraKeys: {
        "Ctrl-S": () => saveFile(),
        "Cmd-S": () => saveFile(),
        "Ctrl-F": "findPersistent",
        "Cmd-F": "findPersistent",
      },
    });
    state.editor.on("change", onEditorChange);
    state.editor.on("cursorActivity", updateCursor);
  }

  const validateSoon = debounce(() => validateCurrent(), 220);

  function onEditorChange() {
    if (state.suppressChange) return;
    if (!state.file) return;
    const value = state.editor.getValue();
    setDirty(value !== state.original);
    validateSoon();
  }

  function setDirty(dirty) {
    state.dirty = dirty;
    $("dirtyDot").classList.toggle("on", dirty);
    updateActionButtons();
    updateStatus();
  }

  function updateActionButtons() {
    const hasFile = Boolean(state.file);
    const hasGit = Boolean(state.file && state.file.git);
    $("btnSave").disabled = !hasFile || state.busy;
    $("btnDiff").disabled = !hasFile;
    $("btnCommit").disabled = !hasGit || state.busy;
    $("btnPull").disabled = !hasGit || state.busy;
    $("btnGitDiff").disabled = !hasGit || state.busy;
    $("btnRotateKeys").classList.toggle(
      "hidden",
      !hasFile || state.busy || !state.file || state.file.encrypted_file !== true,
    );
    $("btnEncryptFile").classList.toggle(
      "hidden",
      !hasFile || state.busy || !state.file || state.file.encrypted_file !== false,
    );
  }

  function modeFor(format) {
    if (format === "yaml") return "yaml";
    if (format === "json") return { name: "javascript", json: true };
    return "properties";
  }

  function applyFormat(format) {
    if (!state.editor) return;
    state.editor.setOption("mode", modeFor(format));
    $("formatBadge").textContent = (format || "text").toUpperCase();
  }

  function validateCurrent() {
    if (!state.file || !state.editor) return;
    const value = state.editor.getValue();
    const format = state.file.format;
    let parsed;
    let message;
    let ok = true;
    try {
      if (format === "yaml") {
        parsed = jsyaml.load(value);
        if (parsed === null && value.trim()) throw new Error("Пустой YAML-документ");
        message = "YAML валиден";
      } else if (format === "json") {
        parsed = JSON.parse(value);
        message = "JSON валиден";
      } else if (format === "dotenv") {
        parsed = parseDotenv(value);
        message = "dotenv валиден";
      } else {
        parsed = parseIni(value);
        message = "INI валиден";
      }
    } catch (error) {
      ok = false;
      message = formatError(error, format);
    }
    const target = $("validation");
    target.textContent = message || "—";
    target.className = `validation ${ok ? "ok" : "bad"}`;
    state.valid = ok;
    if (!ok) {
      state.parsed = null;
      state.leaves = [];
      $("secretCount").textContent = "0";
      $("secretCount2").textContent = "0";
      $("secretsList").innerHTML = '<div class="empty-state">Исправьте синтаксис, чтобы увидеть ключи.</div>';
      $("statusKeys").textContent = "секреты: —";
      return;
    }
    state.parsed = parsed;
    state.leaves = flattenLeaves(parsed);
    $("secretCount").textContent = String(state.leaves.length);
    $("secretCount2").textContent = String(state.leaves.length);
    $("statusKeys").textContent = `секретов: ${state.leaves.length}`;
    renderSecrets();
  }

  function formatError(error, format) {
    if (format === "yaml" && error && error.mark) {
      return `YAML: строка ${error.mark.line + 1}, столбец ${error.mark.column + 1}: ${error.reason || error.message}`;
    }
    if (format === "json") {
      const match = /position\s+(\d+)/i.exec(error.message || "");
      if (match) {
        const value = state.editor.getValue().slice(0, Number(match[1]));
        const lines = value.split("\n");
        return `JSON: строка ${lines.length}, столбец ${lines[lines.length - 1].length + 1}: ${error.message}`;
      }
      return `JSON: ${error.message}`;
    }
    return error.message || String(error);
  }

  function parseDotenv(text) {
    const result = {};
    const regex = /^(?:export\s+)?([A-Za-z_][A-Za-z0-9_.-]*)\s*=(.*)$/;
    text.split("\n").forEach((line, index) => {
      const trimmed = line.trim();
      if (!trimmed || trimmed.startsWith("#") || trimmed.startsWith(";")) return;
      const match = regex.exec(line);
      if (!match) throw new Error(`Строка ${index + 1}: ожидается KEY=VALUE`);
      if (Object.prototype.hasOwnProperty.call(result, match[1])) {
        throw new Error(`Строка ${index + 1}: повторяющийся ключ ${match[1]}`);
      }
      result[match[1]] = match[2];
    });
    return result;
  }

  function parseIni(text) {
    const result = {};
    let section = "";
    text.split("\n").forEach((line, index) => {
      const trimmed = line.trim();
      if (!trimmed || trimmed.startsWith("#") || trimmed.startsWith(";")) return;
      const sectionMatch = /^\[([^\]]+)\]$/.exec(trimmed);
      if (sectionMatch) {
        section = sectionMatch[1];
        result[section] = result[section] || {};
        return;
      }
      const eq = line.indexOf("=");
      if (eq < 0) throw new Error(`Строка ${index + 1}: ожидается key=value`);
      if (!section) throw new Error(`Строка ${index + 1}: ключ вне секции`);
      result[section][line.slice(0, eq).trim()] = line.slice(eq + 1).trim();
    });
    return result;
  }

  function flattenLeaves(value, prefix = [], out = []) {
    if (value && typeof value === "object" && !Array.isArray(value)) {
      const keys = Object.keys(value);
      if (!keys.length) out.push({ path: prefix, value: "" });
      for (const key of keys) flattenLeaves(value[key], [...prefix, key], out);
    } else if (Array.isArray(value)) {
      if (!value.length) out.push({ path: prefix, value: "" });
      value.forEach((item, index) => flattenLeaves(item, [...prefix, `[${index}]`], out));
    } else {
      out.push({ path: prefix, value });
    }
    return out;
  }

  function valueLabel(value, revealed) {
    if (value === null) return revealed ? "null" : "••••••";
    if (typeof value === "object") return revealed ? JSON.stringify(value) : "••••••";
    const text = String(value);
    if (!text) return "—";
    return revealed ? text : "•".repeat(Math.min(10, Math.max(5, text.length)));
  }

  function renderSecrets() {
    const holder = $("secretsList");
    const scrollTop = holder.scrollTop;
    holder.innerHTML = "";
    try {
    if (!state.file) return;
    if (!state.file.tree) {
      holder.innerHTML = '<div class="empty-state">Для формата <b>' + escapeHtml(state.file.format) +
        '</b> доступен только текстовый редактор. Секреты видны в самом файле.</div>';
      $("secretsFoot").textContent = "древовидный режим недоступен";
      return;
    }
    if (!state.leaves.length) {
      holder.innerHTML = '<div class="empty-state">В документе нет листовых значений.</div>';
      return;
    }
    const query = ($("secretSearch").value || "").trim().toLowerCase();
    if (query) {
      const matches = state.leaves.filter((leaf) => leaf.path.join(".").toLowerCase().includes(query));
      for (const leaf of matches.slice(0, MAX_SECRET_ROWS)) {
        holder.append(secretRow(leaf, leaf.path.join(".")));
      }
      if (matches.length > MAX_SECRET_ROWS) {
        holder.append(emptyNode(`Показаны первые ${MAX_SECRET_ROWS} из ${matches.length}.`));
      }
      if (!matches.length) holder.append(emptyNode("Нет совпадений."));
      $("secretsFoot").textContent = `${matches.length} совпадений`;
      return;
    }
    const tree = buildTree(state.parsed);
    let count = 0;
    const renderNodes = (nodes, parent) => {
      for (const node of nodes) {
        if (count >= MAX_SECRET_ROWS) break;
        if (node.children) {
          const details = document.createElement("details");
          details.className = "secret-group";
          details.open = true;
          const summary = document.createElement("summary");
          summary.className = "secret-group-label";
          summary.textContent = node.name;
          details.append(summary);
          renderNodes(node.children, details);
          parent.append(details);
        } else {
          count += 1;
          parent.append(secretRow({ path: node.path, value: node.value }, node.name));
        }
      }
    };
    renderNodes(tree, holder);
    if (count >= MAX_SECRET_ROWS) holder.append(emptyNode(`Показаны первые ${MAX_SECRET_ROWS} ключей.`));
    $("secretsFoot").textContent = state.showValues ? "значения видны" : "значения скрыты";
    $("btnReveal").setAttribute("aria-pressed", String(state.showValues));
    } finally {
      holder.scrollTop = scrollTop;
    }
  }

  function emptyNode(text) {
    const node = document.createElement("div");
    node.className = "empty-state";
    node.textContent = text;
    return node;
  }

  function buildTree(value) {
    const nodes = [];
    if (value === null || typeof value !== "object") {
      return [{ name: "(root)", path: [], value }];
    }
    const walk = (val, prefix) => {
      if (val && typeof val === "object" && !Array.isArray(val)) {
        const keys = Object.keys(val);
        if (!keys.length) {
          nodes.push({ name: "(пусто)", path: prefix, value: "" });
          return;
        }
        for (const key of keys) {
          const child = val[key];
          if (child && typeof child === "object") {
            const children = [];
            const stack = [{ value: child, path: [...prefix, key] }];
            while (stack.length) {
              const current = stack.pop();
              if (Array.isArray(current.value)) {
                current.value.forEach((item, index) => {
                  if (item && typeof item === "object") {
                    stack.push({ value: item, path: [...current.path, `[${index}]`] });
                  } else {
                    children.push({ name: `[${index}]`, path: [...current.path, `[${index}]`], value: item });
                  }
                });
              } else {
                for (const [k, item] of Object.entries(current.value)) {
                  if (item && typeof item === "object") {
                    stack.push({ value: item, path: [...current.path, k] });
                  } else {
                    children.push({ name: k, path: [...current.path, k], value: item });
                  }
                }
              }
            }
            children.sort((a, b) => a.path.join(".").localeCompare(b.path.join(".")));
            nodes.push({ name: key, path: [...prefix, key], children });
          } else {
            nodes.push({ name: key, path: [...prefix, key], value: child });
          }
        }
      } else if (Array.isArray(val)) {
        val.forEach((item, index) => {
          nodes.push({ name: `[${index}]`, path: [...prefix, `[${index}]`], value: item });
        });
      }
    };
    walk(value, []);
    return nodes;
  }

  function secretRow(leaf, label) {
    const row = document.createElement("div");
    row.className = "secret-row";
    const key = document.createElement("button");
    key.type = "button";
    key.className = "secret-key";
    key.textContent = label;
    key.title = leaf.path.join(".");
    key.addEventListener("click", () => jumpToKey(leaf.path));
    const value = document.createElement("span");
    value.className = "secret-value" + (state.showValues ? " revealed" : "");
    value.textContent = valueLabel(leaf.value, state.showValues);
    value.title = state.showValues ? String(leaf.value) : "Значение скрыто";
    const copy = document.createElement("button");
    copy.type = "button";
    copy.className = "icon-btn sm secret-copy";
    copy.title = "Скопировать значение";
    copy.innerHTML = '<svg><use href="#i-copy"/></svg>';
    copy.addEventListener("click", async (event) => {
      event.stopPropagation();
      const text = typeof leaf.value === "string" ? leaf.value : JSON.stringify(leaf.value);
      const ok = await copyText(text ?? "");
      toast(ok ? "Скопировано" : "Не удалось скопировать", ok ? "" : "Буфер обмена недоступен", ok ? "ok" : "bad");
    });
    row.append(key, value, copy);
    return row;
  }

  async function copyText(text) {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch {
      const area = document.createElement("textarea");
      area.value = text;
      area.style.position = "fixed";
      area.style.opacity = "0";
      document.body.append(area);
      area.select();
      let ok = false;
      try { ok = document.execCommand("copy"); } catch { ok = false; }
      area.remove();
      return ok;
    }
  }

  function jumpToKey(path) {
    if (!state.editor || !state.file) return;
    const line = findKeyLine(path);
    if (line >= 0) {
      state.editor.setCursor({ line, ch: 0 });
      state.editor.scrollIntoView({ line, ch: 0 }, 90);
      state.editor.focus();
    } else {
      toast("Не найдено", "Не удалось сопоставить ключ со строкой (сложный YAML).", "warn");
    }
  }

  function findKeyLine(path) {
    const format = state.file.format;
    const text = state.editor.getValue();
    if (format === "yaml") {
      const stack = [];
      const lines = text.split("\n");
      for (let index = 0; index < lines.length; index += 1) {
        const line = lines[index];
        if (!line.trim() || line.trim().startsWith("#")) continue;
        const match = /^(\s*)(?:-\s+)?([^#\s][^:]*?):(?:\s|$)/.exec(line);
        if (!match) continue;
        const indent = match[1].length;
        let key = match[2].trim();
        if ((key.startsWith('"') && key.endsWith('"')) || (key.startsWith("'") && key.endsWith("'"))) {
          key = key.slice(1, -1);
        }
        while (stack.length && stack[stack.length - 1].indent >= indent) stack.pop();
        stack.push({ indent, key });
        if (stack.length === path.length && stack.every((entry, idx) => entry.key === String(path[idx]))) {
          return index;
        }
      }
    } else if (format === "json") {
      const last = String(path[path.length - 1] || "");
      const needle = `"${last}"`;
      const lines = text.split("\n");
      for (let index = 0; index < lines.length; index += 1) {
        if (lines[index].includes(needle)) return index;
      }
    } else {
      const last = String(path[path.length - 1] || "");
      const lines = text.split("\n");
      for (let index = 0; index < lines.length; index += 1) {
        if (lines[index].trimStart().startsWith(last + "=") || lines[index].trimStart().startsWith(last + " =")) {
          return index;
        }
      }
    }
    return -1;
  }

  function updateCursor() {
    if (!state.editor) return;
    const cursor = state.editor.getCursor();
    $("statusCursor").textContent = `Стр ${cursor.line + 1}, стлб ${cursor.ch + 1}`;
  }

  function updateStatus() {
    if (!state.file) return;
    const value = state.editor ? state.editor.getValue() : "";
    $("statusSize").textContent = `${formatBytes(new Blob([value]).size)} · ${value.split("\n").length} стр.`;
    if (state.dirty) {
      $("statusSaved").textContent = "есть несохранённые изменения";
    } else if (state.file.savedAt) {
      $("statusSaved").textContent = `сохранено ${formatDate(state.file.savedAt)}`;
    } else {
      $("statusSaved").textContent = "изменений нет";
    }
  }

  // --------------------------------------------------------------- open file
  async function openFile(path, { force = false, keepProgress = false } = {}) {
    if (!path) return;
    if (state.local && pathKey(state.local.path) !== pathKey(path)) {
      state.local = null;
      renderLocalRail();
    }
    if (state.file && state.dirty && !force) {
      const ok = await showConfirm({
        title: "Несохранённые изменения",
        text: "Открыть другой файл и потерять изменения?",
        okLabel: "Открыть",
      });
      if (!ok) return;
    }
    closeModal("pickerModal");
    try {
      const file = await trackedOperation({
        url: `/api/file/job?path=${encodeURIComponent(path)}`,
        steps: OPEN_STEPS,
        title: "Открываем файл",
        subtitle: path,
        keepProgress,
      });
      progressStep("Загружаем в редактор", "apply");
      applyFile(file);
      $("pathInput").value = file.path;
      addRecent(file);
      const params = new URLSearchParams(location.search);
      params.set("path", file.path);
      history.replaceState({}, "", `${location.pathname}?${params.toString()}`);
    } catch (error) {
      if (error instanceof ApiError && error.code === "key_required") {
        state.pendingPath = path;
        state.pendingKeyInfo = (error.details && error.details.encrypted) || null;
        openKeyModal("Для этого файла нужен приватный ключ.");
      } else {
        toast("Не удалось открыть файл", error.message, "bad");
      }
    } finally {
      hideProgress();
    }
  }

  function applyFile(file) {
    state.file = file;
    state.pendingKeyInfo = null;
    state.original = file.content;
    state.dirty = false;
    state.parsed = null;
    state.leaves = [];
    $("openScreen").classList.add("hidden");
    $("editorScreen").classList.remove("hidden");
    if (window.matchMedia("(max-width: 980px)").matches) {
      $("secretsPanel").classList.remove("hidden");
      $("secretsPanel").classList.add("mobile-open");
    } else {
      $("secretsPanel").classList.remove("mobile-open");
      $("secretsPanel").classList.toggle("hidden", !state.secretsOpen);
    }
    $("pathText").textContent = file.path;
    $("fileName").textContent = file.name;
    $("filePath").textContent = file.path;
    $("filePath").title = file.path;
    $("commitBranch").textContent = "git";
    applyFormat(file.format);
    state.suppressChange += 1;
    state.editor.setValue(file.content);
    state.editor.clearHistory();
    state.editor.setCursor({ line: 0, ch: 0 });
    state.suppressChange -= 1;
    setDirty(false);
    validateCurrent();
    renderFileChips();
    renderLocalRail();
    if (state.local && pathKey(state.local.path) === pathKey(file.path)) {
      const next = file.encrypted_file === false
        ? "«Зашифровать» (или «Сохранить», чтобы оставить открытым)"
        : state.local.handle
          ? "«Скачать зашифрованный» или «Записать в исходный файл»"
          : "«Скачать зашифрованный» и замена файла вручную";
      setLocalStatus(
        `«${state.local.originalName || state.local.name}» открыт. После правок — «Сохранить», затем ${next}.`,
        "ok",
      );
    } else {
      setLocalStatus("");
    }
    renderGit();
    refreshBackups();
    updateStatus();
    updateActionButtons();
    $("commitMessage").value = `chore(secrets): update ${file.name}`;
    document.title = `${file.name} — SOPS Editor`;
    setTimeout(() => state.editor.refresh(), 30);
    state.editor.focus();
  }

  function pathKey(value) {
    return String(value || "")
      .replaceAll("\\", "/")
      .replace(/\/+$/, "")
      .toLowerCase();
  }

  function renderLocalRail() {
    const box = $("localRail");
    if (!state.local || !state.file || pathKey(state.local.path) !== pathKey(state.file.path)) {
      box.classList.add("hidden");
      return;
    }
    state.local.path = state.file.path;
    box.classList.remove("hidden");
    $("localRailName").textContent = state.local.originalName || state.local.name || "файл с компьютера";
    $("btnDownloadLocal").textContent =
      state.file && state.file.encrypted_file === false ? "Скачать файл" : "Скачать зашифрованный";
    const canWrite = Boolean(state.local.handle);
    const canAsk = Boolean(window.showOpenFilePicker && window.isSecureContext);
    $("btnWriteLocal").classList.toggle("hidden", !canWrite);
    $("btnAllowWrite").classList.toggle("hidden", canWrite || !canAsk);
    const hint = $("localWriteHint");
    hint.classList.toggle("hidden", canWrite);
    hint.textContent = canWrite
      ? ""
      : canAsk
        ? "Чтобы писать прямо в исходный файл без выбора места при каждом сохранении, один раз разрешите перезапись: выберите тот же файл в диалоге."
        : "Прямая запись недоступна в этом браузере: скачайте шифротекст и замените файл вручную.";
  }

  function renderFileChips() {
    const file = state.file;
    if (!file) return;
    const holder = $("fileChips");
    holder.innerHTML = "";
    const plain = file.encrypted_file === false;
    const chips = [
      ["format", (file.format || "").toUpperCase(), "accent"],
      ["size", formatBytes(file.content_size), ""],
      ["keys", plain ? "не зашифрован" : (file.encrypted && file.encrypted.key_types || []).join(", ") || "sops", plain ? "warn" : ""],
      plain ? ["source", "открытый текст", "warn"] : ["source", file.key_source || "key", ""],
    ];
    for (const [label, value, cls] of chips) {
      if (!value) continue;
      const chip = document.createElement("span");
      chip.className = `meta-chip ${cls}`.trim();
      chip.textContent = `${label}: ${value}`;
      holder.append(chip);
    }
  }

  function closeFile({ force = false } = {}) {
    if (!force && state.dirty) return;
    state.file = null;
    state.original = "";
    state.dirty = false;
    state.parsed = null;
    state.leaves = [];
    state.local = null;
    setLocalStatus("");
    renderLocalRail();
    $("editorScreen").classList.add("hidden");
    $("openScreen").classList.remove("hidden");
    $("pathText").textContent = "";
    $("dirtyDot").classList.remove("on");
    history.replaceState({}, "", location.pathname);
    document.title = "SOPS Editor";
    if (state.editor) state.editor.setValue("");
  }

  async function requestCloseFile() {
    if (state.dirty) {
      const ok = await showConfirm({
        title: "Несохранённые изменения",
        text: "Вернуться к выбору файла и потерять изменения?",
        okLabel: "Вернуться",
      });
      if (!ok) return;
    }
    closeFile({ force: true });
  }

  // -------------------------------------------------------------- local file
  function pickLocalFile() {
    // Standard dialog: synchronous, works in every browser and never blocked.
    setLocalStatus("");
    $("localFileInput").click();
  }

  async function allowWriteBack() {
    if (!state.local) return;
    if (!window.showOpenFilePicker || !window.isSecureContext) {
      toast("Прямая запись недоступна", "Браузер не поддерживает File System Access. Скачайте файл и замените вручную.", "warn");
      return;
    }
    try {
      const [handle] = await window.showOpenFilePicker({
        multiple: false,
        suggestedName: state.local.originalName || state.local.name,
      });
      if (!handle) return;
      state.local.handle = handle;
      renderLocalRail();
      toast("Перезапись разрешена", "Теперь «Записать в исходный файл» заменит выбранный файл.", "ok");
    } catch (error) {
      toast(
        "Диалог не открылся",
        error && error.name === "AbortError"
          ? "Окно выбора закрыто или недоступно в этом браузере."
          : (error && error.message) || String(error),
        "warn",
      );
    }
  }

  function setLocalStatus(text, kind = "", action = null) {
    const node = $("localStatus");
    if (!node) return;
    node.className = `local-status ${kind}`.trim();
    node.textContent = text || "";
    if (action && text) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "btn tiny primary";
      button.textContent = action.label;
      button.addEventListener("click", action.onClick);
      node.append(document.createElement("br"), button);
    }
    node.classList.toggle("hidden", !text);
  }

  function localUploadHint(error) {
    const code = error instanceof ApiError ? error.code : "";
    if (code === "not_sops") {
      return "Файл не зашифрован SOPS. Зашифруйте его командой sops --encrypt --in-place <файл> и выберите снова. Как подключить SOPS — в справке.";
    }
    if (code === "too_large") return error.message;
    if (code === "unsupported_format") {
      return "Формат не поддерживается: нужен YAML, JSON, dotenv или INI (не бинарный файл).";
    }
    if (code === "network") return "Сервер недоступен. Проверьте, что контейнер запущен, и обновите страницу.";
    return error && error.message ? error.message : String(error);
  }

  async function uploadLocalFile(file, handle = null) {
    if (!file) return;
    const limit = (state.status && state.status.max_file_bytes) || 5 * 1024 * 1024;
    if (file.size > limit) {
      const text = `Файл больше лимита ${formatBytes(limit)}.`;
      setLocalStatus(text, "bad");
      toast("Файл слишком большой", text, "bad");
      $("localFileInput").value = "";
      return;
    }
    $("btnLocalPick").disabled = true;
    setLocalStatus(`Загружаем «${file.name}» (${formatBytes(file.size)}) и расшифровываем…`, "busy");
    try {
      const payload = await trackedOperation({
        url: `/api/upload?name=${encodeURIComponent(file.name)}`,
        method: "POST",
        rawBody: file,
        steps: UPLOAD_STEPS,
        title: "Загружаем файл",
        subtitle: `${file.name} · ${formatBytes(file.size)}`,
      });
      state.local = {
        path: payload.path,
        name: payload.name,
        originalName: payload.original_name || file.name,
        downloadUrl: payload.download_url,
        handle,
      };
      await openFile(payload.path, { keepProgress: true });
      if (!state.file || pathKey(state.file.path) !== pathKey(payload.path)) {
        setLocalStatus("Файл загружен, но не расшифрован: задайте приватный ключ в окне «Ключ».", "busy");
      }
    } catch (error) {
      const text = localUploadHint(error);
      if (error instanceof ApiError && error.code === "key_required") {
        state.pendingPath = null;
        setLocalStatus("Файл загружен, но сервер не смог его расшифровать: задайте подходящий age-ключ.", "busy");
      } else {
        setLocalStatus(text, "bad");
      }
      toast("Не удалось загрузить файл", text, "bad");
    } finally {
      $("btnLocalPick").disabled = false;
      $("localFileInput").value = "";
      hideProgress();
    }
  }

  function downloadLocalFile() {
    if (!state.local) return;
    const link = document.createElement("a");
    link.href = `/api/download?path=${encodeURIComponent(state.local.path)}`;
    link.download = state.local.originalName || state.local.name || "secrets.enc.yaml";
    document.body.append(link);
    link.click();
    link.remove();
    toast("Скачивание", "Зашифрованный файл сохранён браузером.", "ok");
  }

  async function writeLocalFile() {
    if (!state.local || !state.local.handle) return;
    const name = state.local.originalName || state.local.name || "файл";
    const confirmed = await showConfirm({
      title: "Перезаписать исходный файл?",
      text: `Файл «${name}» будет заменён зашифрованной версией. Прежнее содержимое файла будет потеряно.`,
      okLabel: "Перезаписать",
    });
    if (!confirmed) return;
    $("btnWriteLocal").disabled = true;
    try {
      const handle = state.local.handle;
      if (handle.queryPermission) {
        let permission = await handle.queryPermission({ mode: "readwrite" });
        if (permission !== "granted") {
          permission = await handle.requestPermission({ mode: "readwrite" });
        }
        if (permission !== "granted") throw new Error("браузер не выдал разрешение на запись");
      }
      const response = await fetch(`/api/download?path=${encodeURIComponent(state.local.path)}`, {
        credentials: "same-origin",
        headers: { "X-SOPS-Editor": "1" },
      });
      if (!response.ok) {
        const payload = await response.json().catch(() => null);
        throw new Error(
          (payload && payload.error && payload.error.message) || `HTTP ${response.status}`,
        );
      }
      const blob = await response.blob();
      const writable = await handle.createWritable();
      await writable.write(blob);
      await writable.close();
      toast("Записано", `Исходный файл ${state.local.originalName || ""} обновлён.`, "ok");
    } catch (error) {
      toast("Не удалось записать файл", error.message || String(error), "bad");
    } finally {
      $("btnWriteLocal").disabled = false;
    }
  }

  // ----------------------------------------------------------------- encrypt
  function showEncryptError(text) {
    const node = $("encryptError");
    node.textContent = text;
    node.classList.toggle("hidden", !text);
  }

  function openEncryptModal(target) {
    if (!target || !target.path) return;
    state.encrypt = target;
    $("encryptTarget").innerHTML = `<p><strong>Файл:</strong> <span class="mono">${escapeHtml(target.path)}</span></p>`;
    $("encryptRecipients").value = "";
    showEncryptError("");
    const defaults = (state.key && state.key.recipients) || [];
    $("encryptDefault").textContent = defaults.length
      ? `Пусто — получатели из .sops.yaml, иначе серверный ключ: ${defaults.join(", ")}.`
      : "Пусто — взять правила .sops.yaml репозитория; если правила нет — серверный ключ.";
    openModal("encryptModal");
  }

  function encryptHint(error) {
    const code = error instanceof ApiError ? error.code : "";
    if (code === "already_encrypted") return "Файл уже зашифрован SOPS — открывайте его напрямую.";
    if (code === "no_creation_rule") {
      return "Нет подходящего правила .sops.yaml и не задан получатель. Укажите age-ключ.";
    }
    if (code === "summary_failed" || code === "invalid_content") {
      return "SOPS не смог разобрать файл. Проверьте синтаксис YAML/JSON/INI/dotenv.";
    }
    if (code === "exists") return (error.details && error.details.path) || "Целевой файл уже существует.";
    return error && error.message ? error.message : String(error);
  }

  async function runEncrypt() {
    const target = state.encrypt;
    if (!target) return;
    const raw = $("encryptRecipients").value.trim();
    const recipients = raw ? raw.split(/[\s,]+/).filter(Boolean) : [];
    const invalid = recipients.find((item) => !/^age1[0-9a-z]{20,}$/.test(item));
    if (invalid) {
      showEncryptError(`Некорректный age-получатель: ${invalid}`);
      return;
    }
    $("btnEncryptRun").disabled = true;
    $("btnEncryptRun").textContent = "Шифрование…";
    showEncryptError("");
    const targetName = target.path.split(/[\\/]/).pop();
    try {
      const result = await trackedOperation({
        url: "/api/encrypt",
        method: "POST",
        body: { path: target.path, recipients },
        steps: ENCRYPT_STEPS,
        title: "Шифруем файл",
        subtitle: targetName,
      });
      state.pendingPlainFile = null;
      closeModal("encryptModal");
      closeModal("pickerModal");
      progressStep(`Зашифровано: ${result.name}`, "encrypt");
      const who = (result.recipients || []).join(", ") || "правила .sops.yaml";
      toast("Зашифровано", `${result.name} → ${who}`, "ok");
      if (state.local && pathKey(state.local.path) === pathKey(target.path || "")) {
        state.local = {
          path: result.path,
          name: result.name,
          originalName: state.local.originalName,
          downloadUrl: result.download_url,
          handle: state.local.handle,
        };
      }
      await openFile(result.path, { keepProgress: true });
    } catch (error) {
      showEncryptError(encryptHint(error));
    } finally {
      hideProgress();
      $("btnEncryptRun").disabled = false;
      $("btnEncryptRun").textContent = "Зашифровать";
    }
  }

  // ------------------------------------------------------------------ rotate
  function showRotateError(text) {
    const node = $("rotateError");
    node.textContent = text;
    node.classList.toggle("hidden", !text);
  }

  function openRotateModal() {
    if (!state.file || state.file.encrypted_file !== true) return;
    $("rotateTarget").innerHTML =
      `<p><strong>Файл:</strong> <span class="mono">${escapeHtml(state.file.path)}</span></p>` +
      `<p><strong>Получатели:</strong> ${escapeHtml(((state.file.encrypted || {}).age_recipients || []).join(", ") || "—")}</p>`;
    document.querySelector('input[name="rotateMode"][value="updatekeys"]').checked = true;
    $("rotateResult").textContent = "";
    $("rotateResult").classList.add("hidden");
    $("rotateWarning").classList.add("hidden");
    showRotateError("");
    $("btnRotateRun").classList.remove("hidden");
    $("btnRotateRun").disabled = false;
    $("btnRotateRun").textContent = "Выполнить";
    openModal("rotateModal");
  }

  async function runRotate() {
    if (!state.file || state.busy) return;
    const mode = document.querySelector('input[name="rotateMode"]:checked').value;
    const ok = await showConfirm({
      title: "Ротация ключей",
      text: mode === "rotate"
        ? "Будет создан новый ключ данных и перешифрованы все значения. Продолжить?"
        : "Получатели будут пересчитаны по .sops.yaml. Данные не перешифровываются. Продолжить?",
      okLabel: "Выполнить",
    });
    if (!ok) return;
    state.busy = true;
    $("btnRotateRun").disabled = true;
    $("btnRotateRun").textContent = "Выполняем…";
    showRotateError("");
    updateActionButtons();
    try {
      const result = await trackedOperation({
        url: "/api/keys/rotate",
        method: "POST",
        body: { path: state.file.path, mode },
        steps: ROTATE_STEPS,
        title: "Ротация ключей",
        subtitle: state.file.name,
      });
      state.file = {
        ...state.file,
        sha256: result.sha256,
        size: result.size,
        mtime: result.mtime,
        encrypted: result.encrypted,
        encrypted_file: true,
        git: result.git,
      };
      renderFileChips();
      renderGit();
      updateStatus();
      updateActionButtons();
      if (!result.changed) {
        toast("Без изменений", "Получатели уже соответствуют .sops.yaml.", "ok");
        closeModal("rotateModal");
        return;
      }
      $("btnRotateRun").classList.add("hidden");
      const output = $("rotateResult");
      output.textContent = (result.output || []).join("\n") || "sops: ok";
      output.classList.remove("hidden");
      const warning = $("rotateWarning");
      warning.textContent = result.warning || "";
      warning.classList.toggle("hidden", !result.warning);
      const who = (result.encrypted.age_recipients || []).join(", ") || "—";
      toast("Ключи обновлены", who, "ok");
    } catch (error) {
      if (error instanceof ApiError && error.code === "key_required") {
        state.pendingKeyInfo = (error.details && error.details.encrypted) || null;
        closeModal("rotateModal");
        openKeyModal("Ключ не подходит для этого файла. Задайте другой приватный ключ.");
      } else {
        showRotateError(error.message || String(error));
      }
    } finally {
      hideProgress();
      state.busy = false;
      $("btnRotateRun").disabled = false;
      $("btnRotateRun").textContent = "Выполнить";
      updateActionButtons();
    }
  }

  // -------------------------------------------------------------------- save
  async function saveFile() {
    if (!state.file || state.busy) return;
    if (!state.editor) return;
    const content = state.editor.getValue();
    if (!content.trim()) {
      toast("Не сохранено", "Документ пуст.", "bad");
      return;
    }
    validateCurrent();
    if (!state.valid) {
      toast("Не сохранено", "Исправьте синтаксис: сервер отклонит такой документ.", "bad");
      return;
    }
    state.busy = true;
    $("btnSave").disabled = true;
    $("btnSave").setAttribute("aria-busy", "true");
    setSaveLabel("Шифрование…");
    updateActionButtons();
    try {
      const result = await trackedOperation({
        url: "/api/file/job",
        method: "POST",
        body: {
          path: state.file.path,
          content,
          format: state.file.format,
          expected_sha256: state.file.sha256,
        },
        steps: SAVE_STEPS,
        title: "Сохраняем файл",
        subtitle: state.file.path,
      });
      if (!result.saved) {
        toast("Без изменений", "Содержимое совпадает с файлом на сервере.", "ok");
        setDirty(false);
        return;
      }
      const cursor = state.editor.getCursor();
      const scroll = state.editor.getScrollInfo().top;
      state.file = {
        ...state.file,
        sha256: result.sha256,
        content: result.content,
        size: result.size,
        content_size: result.content_size,
        mtime: result.mtime,
        encrypted: result.encrypted,
        encrypted_file: result.encrypted_file ?? state.file.encrypted_file,
        git: result.git,
        savedAt: Date.now(),
      };
      state.original = result.content;
      state.suppressChange += 1;
      state.editor.setValue(result.content);
      state.editor.setCursor(cursor);
      state.editor.scrollTo(null, scroll);
      state.suppressChange -= 1;
      setDirty(false);
      validateCurrent();
      renderFileChips();
      renderLocalRail();
      renderGit();
      refreshBackups();
      updateStatus();
      if (state.file.encrypted_file === false) {
        toast("Сохранено", "Открытый файл записан. Кнопка «Зашифровать» создаст SOPS-версию.", "ok");
      } else if (state.local) {
        toast(
          "Сохранено",
          state.local.handle
            ? "Можно записать зашифрованный файл обратно в исходный."
            : "Скачайте зашифрованный файл и замените им локальный.",
          "ok",
        );
      } else {
        toast("Сохранено", result.backup ? `Резервная копия: ${result.backup.id}` : "Файл зашифрован обратно.", "ok");
      }
      if (result.content !== content) {
        toast("Формат обновлён", "SOPS нормализовал отступы/комментарии при шифровании.", "");
      }
    } catch (error) {
      if (error instanceof ApiError && error.code === "conflict") {
        const ok = await showConfirm({
          title: "Файл изменился",
          text: "На сервере другая версия файла. Перезагрузить её? Несохранённые изменения будут потеряны.",
          okLabel: "Перезагрузить",
        });
        if (ok) openFile(state.file.path, { force: true });
      } else if (error instanceof ApiError && error.code === "key_required") {
        state.pendingPath = state.file.path;
        state.pendingKeyInfo = (error.details && error.details.encrypted) || state.pendingKeyInfo;
        openKeyModal("Ключ не подходит для этого файла. Задайте другой приватный ключ.");
      } else {
        toast("Не сохранено", error.message, "bad");
      }
    } finally {
      hideProgress();
      state.busy = false;
      $("btnSave").removeAttribute("aria-busy");
      setSaveLabel("Сохранить");
      updateActionButtons();
    }
  }

  function setSaveLabel(text) {
    const label = $("btnSave") && $("btnSave").querySelector("span");
    if (label) label.textContent = text;
  }

  // --------------------------------------------------------------------- git
  function renderGit() {
    const info = state.file && state.file.git;
    const holder = $("gitBox");
    holder.innerHTML = "";
    updateActionButtons();
    if (!info) {
      holder.innerHTML = '<p class="muted small">Не git-репозиторий.</p>';
      return;
    }
    const branch = document.createElement("div");
    branch.className = "git-branch";
    branch.innerHTML = '<svg><use href="#i-git"/></svg>';
    const branchText = document.createElement("span");
    branchText.textContent = info.branch || "?";
    branch.append(branchText);
    const status = document.createElement("div");
    status.className = `git-status ${info.dirty ? "dirty" : ""}`;
    status.textContent = info.dirty ? "есть изменения" : "чисто";
    const commit = document.createElement("div");
    commit.className = "git-commit";
    commit.textContent = info.last_commit
      ? `${info.last_commit.hash} · ${info.last_commit.subject}`
      : "нет коммитов";
    commit.title = info.remote || "";
    holder.append(branch, status, commit);
  }

  function openCommitModal() {
    if (!state.file || !state.file.git) return;
    $("commitBranch").textContent = state.file.git.branch || "git";
    $("commitMessage").value = `chore(secrets): update ${state.file.name}`;
    $("commitPush").checked = false;
    openModal("commitModal");
    setTimeout(() => $("commitMessage").select(), 30);
  }

  async function runCommit() {
    if (!state.file) return;
    const message = $("commitMessage").value.trim();
    if (!message) { toast("Коммит", "Введите сообщение", "warn"); return; }
    const push = $("commitPush").checked;
    state.busy = true;
    $("btnCommitRun").disabled = true;
    updateActionButtons();
    try {
      const result = await api("/api/git/commit", {
        method: "POST",
        body: { path: state.file.path, message, push },
      });
      closeModal("commitModal");
      if (!result.committed) {
        toast("Коммит не нужен", "Нет изменений в индексе.", "warn");
      } else {
        toast("Коммит создан", result.hash || message, "ok");
        if (result.push) {
          if (result.push.ok) toast("Push выполнен", "", "ok");
          else toast("Push не выполнен", result.push.error || "Проверьте доступ к remote.", "warn");
        }
      }
      state.file.git = result.git || state.file.git;
      renderGit();
    } catch (error) {
      toast("Коммит не создан", error.message, "bad");
    } finally {
      state.busy = false;
      $("btnCommitRun").disabled = false;
      updateActionButtons();
    }
  }

  async function pullRepo() {
    if (!state.file || !state.file.git) return;
    if (state.dirty) {
      toast("Pull", "Сначала сохраните или отмените изменения.", "warn");
      return;
    }
    const ok = await showConfirm({
      title: "git pull",
      text: "Выполнить fast-forward pull? Если файл изменится, он будет перезагружен.",
      okLabel: "Pull",
      danger: false,
    });
    if (!ok) return;
    try {
      const result = await api("/api/git/pull", {
        method: "POST",
        body: { path: state.file.path },
      });
      toast("Pull выполнен", result.output || "", "ok");
      openFile(state.file.path, { force: true });
    } catch (error) {
      toast("Pull не выполнен", error.message, "bad");
    }
  }

  // ----------------------------------------------------------------- backups
  async function refreshBackups() {
    if (!state.file) return;
    try {
      const result = await api(`/api/backups?path=${encodeURIComponent(state.file.path)}`);
      state.backups = result.backups || [];
      renderBackupBox();
    } catch {
      state.backups = [];
      renderBackupBox();
    }
  }

  function backupUrl(id) {
    return `/api/backups/${encodeURIComponent(id)}?path=${encodeURIComponent(state.file.path)}`;
  }

  function renderBackupBox() {
    const holder = $("backupBox");
    holder.innerHTML = "";
    if (!state.backups.length) {
      holder.innerHTML = '<p class="muted small">Появятся после сохранения.</p>';
      return;
    }
    for (const item of state.backups.slice(0, 3)) {
      const row = document.createElement("div");
      row.className = "backup-item";
      const link = document.createElement("a");
      link.href = backupUrl(item.id);
      link.textContent = item.id;
      link.title = "Скачать зашифрованную копию";
      const size = document.createElement("span");
      size.textContent = formatBytes(item.size);
      row.append(link, size);
      holder.append(row);
    }
  }

  function openBackupsModal() {
    if (!state.file) return;
    const holder = $("backupList");
    holder.innerHTML = "";
    if (!state.backups.length) {
      holder.innerHTML = '<p class="muted small">Резервных копий пока нет.</p>';
    } else {
      for (const item of state.backups) {
        const row = document.createElement("div");
        row.className = "backup-full";
        const link = document.createElement("a");
        link.href = backupUrl(item.id);
        link.className = "mono";
        link.textContent = item.id;
        const date = document.createElement("span");
        date.className = "muted";
        date.textContent = `${formatDate(item.created)} · ${formatBytes(item.size)}`;
        row.append(link, date);
        holder.append(row);
      }
    }
    openModal("backupsModal");
  }

  // -------------------------------------------------------------------- diff
  function openDiff() {
    if (!state.file || !state.editor) return;
    const current = state.editor.getValue();
    if (current === state.original) {
      toast("Изменений нет", "Документ совпадает с открытой версией.", "");
      return;
    }
    const parts = window.Diff ? Diff.diffLines(state.original, current) : [{ value: current, added: true }];
    let added = 0;
    let removed = 0;
    const view = $("diffView");
    view.innerHTML = "";
    for (const part of parts) {
      const lines = part.value.replace(/\n$/, "").split("\n");
      if (part.added) added += lines.length;
      if (part.removed) removed += lines.length;
      for (const line of lines) {
        const span = document.createElement("span");
        span.className = `diff-line ${part.added ? "add" : part.removed ? "del" : "ctx"}`;
        span.textContent = `${part.added ? "+" : part.removed ? "-" : " "} ${line}`;
        view.append(span);
      }
    }
    $("diffSummary").innerHTML = `<span class="add">+${added}</span> / <span class="del">−${removed}</span> · строк`;
    openModal("diffModal");
  }

  function gitDiffNote() {
    return state.dirty
      ? "В редакторе есть несохранённые правки — в дифф они не входят."
      : "";
  }

  function openGitDiff() {
    if (!state.file || !state.file.git) return;
    const holder = $("gitDiffView");
    holder.innerHTML = '<div class="empty-state"><span class="spinner"></span></div>';
    $("gitDiffSummary").textContent = "";
    $("gitDiffNote").textContent = gitDiffNote();
    openModal("gitDiffModal");
    trackedOperation({
      url: `/api/git/diff?path=${encodeURIComponent(state.file.path)}`,
      steps: GIT_DIFF_STEPS,
      title: "Дифф с HEAD",
      subtitle: state.file.name,
    })
      .then((data) => renderGitDiff(data))
      .catch((error) => {
        if (error instanceof ApiError && error.code === "key_required") {
          state.pendingGitDiff = state.file.path;
          state.pendingKeyInfo = (error.details && error.details.encrypted) || null;
          closeModal("gitDiffModal");
          openKeyModal("Ключ не подходит для этого файла. Задайте другой приватный ключ.");
          return;
        }
        $("gitDiffView").innerHTML = "";
        $("gitDiffView").append(emptyNode(error.message || String(error)));
      })
      .finally(() => hideProgress());
  }

  function renderGitDiff(data) {
    const head = data.head;
    const view = $("gitDiffView");
    view.innerHTML = "";
    $("gitDiffNote").textContent = head === null
      ? "Файл ещё не закоммичен: вся версия новая."
      : gitDiffNote();
    if (head && head.binary) {
      view.append(emptyNode("Бинарный файл — дифф недоступен."));
      $("gitDiffSummary").textContent = "";
      return;
    }
    const left = head ? head.content : "";
    const right = data.current.content || "";
    const parts = window.Diff ? Diff.diffLines(left, right) : [{ value: right, added: true }];
    let added = 0;
    let removed = 0;
    let ln = 0;
    let rn = 0;
    for (const part of parts) {
      const lines = part.value.replace(/\n$/, "").split("\n");
      if (part.added) added += lines.length;
      if (part.removed) removed += lines.length;
      for (const line of lines) {
        const row = document.createElement("div");
        row.className = "diff-row";
        const leftNum = document.createElement("span");
        const leftCell = document.createElement("span");
        const rightNum = document.createElement("span");
        const rightCell = document.createElement("span");
        leftNum.className = "diff-num";
        rightNum.className = "diff-num";
        leftCell.className = "diff-cell";
        rightCell.className = "diff-cell";
        if (part.removed) {
          ln += 1;
          leftNum.textContent = ln;
          leftCell.textContent = line;
          leftCell.classList.add("del");
        } else if (part.added) {
          rn += 1;
          rightNum.textContent = rn;
          rightCell.textContent = line;
          rightCell.classList.add("add");
        } else {
          ln += 1;
          rn += 1;
          leftNum.textContent = ln;
          rightNum.textContent = rn;
          leftCell.textContent = line;
          rightCell.textContent = line;
        }
        row.append(leftNum, leftCell, rightNum, rightCell);
        view.append(row);
      }
    }
    const commit = head && head.commit;
    $("gitDiffSummary").innerHTML =
      `<span class="add">+${added}</span> / <span class="del">−${removed}</span> · строк` +
      (commit
        ? ` · HEAD ${escapeHtml(commit.hash)} · ${escapeHtml(commit.subject)}`
        : "");
  }

  // -------------------------------------------------------------------- picker
  function openPicker(path) {
    state.picker.search = false;
    openModal("pickerModal");
    loadBrowse(path || $("pathInput").value || (state.status && state.status.roots[0] && state.status.roots[0].path));
  }

  async function loadBrowse(path, { retry = true } = {}) {
    if (!path) return;
    $("pickerList").innerHTML = '<div class="empty-state"><span class="spinner"></span></div>';
    try {
      const result = await api(`/api/browse?path=${encodeURIComponent(path)}&hidden=${state.picker.hidden}`);
      state.picker.path = result.path;
      state.picker.parent = result.parent;
      state.picker.entries = result.entries || [];
      state.picker.selected = null;
      state.picker.search = false;
      $("pickerPath").textContent = result.path;
      $("pickerUp").disabled = !result.parent;
      $("pickerChoose").disabled = true;
      $("pickerEncrypt").disabled = true;
      $("pickerFilter").value = "";
      renderPickerRoots();
      renderPickerEntries();
    } catch (error) {
      if (retry && error instanceof ApiError && error.status === 404) {
        const parent = path.replace(/[\\/][^\\/]+[\\/]?$/, "");
        if (parent && parent !== path) {
          return loadBrowse(parent, { retry: false });
        }
      }
      $("pickerList").innerHTML = "";
      $("pickerList").append(emptyNode(error.message));
      toast("Не удалось открыть каталог", error.message, "bad");
    }
  }

  function renderPickerRoots() {
    const holder = $("pickerRoots");
    holder.innerHTML = "";
    for (const root of (state.status && state.status.roots) || []) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "root-link";
      button.textContent = root.path;
      button.addEventListener("click", () => loadBrowse(root.path));
      holder.append(button);
    }
  }

  function renderPickerEntries() {
    const holder = $("pickerList");
    holder.innerHTML = "";
    const filter = $("pickerFilter").value.trim().toLowerCase();
    let entries = state.picker.entries;
    if (filter) entries = entries.filter((entry) => entry.name.toLowerCase().includes(filter));
    if (!entries.length) {
      holder.append(emptyNode(filter ? "Нет совпадений в каталоге." : "Каталог пуст."));
      return;
    }
    for (const entry of entries) {
      const row = document.createElement("div");
      row.className = `picker-row ${entry.type === "dir" ? "dir" : "file"}`;
      row.innerHTML = `<svg><use href="${entry.type === "dir" ? "#i-folder" : "#i-file"}"/></svg><span class="name"></span>`;
      row.querySelector(".name").textContent = entry.name;
      if (entry.likely_sops) {
        const badge = document.createElement("span");
        badge.className = "badge";
        badge.textContent = "sops";
        row.append(badge);
      }
      const size = document.createElement("span");
      size.className = "size";
      size.textContent = entry.type === "dir" ? "" : formatBytes(entry.size);
      row.append(size);
      row.addEventListener("click", () => {
        holder.querySelectorAll(".picker-row.selected").forEach((el) => el.classList.remove("selected"));
        row.classList.add("selected");
        state.picker.selected = entry;
        $("pickerChoose").disabled = entry.type !== "file";
        $("pickerEncrypt").disabled = entry.type !== "file";
      });
      row.addEventListener("dblclick", () => {
        if (entry.type === "dir") loadBrowse(entry.path);
        else choosePickerFile(entry);
      });
      holder.append(row);
    }
  }

  function choosePickerFile(entry) {
    if (!entry || entry.type !== "file") return;
    closeModal("pickerModal");
    openFile(entry.path);
  }

  async function runPickerSearch() {
    const query = $("pickerFilter").value.trim();
    if (query.length < 2) {
      toast("Поиск", "Введите минимум 2 символа.", "warn");
      return;
    }
    $("pickerList").innerHTML = '<div class="empty-state"><span class="spinner"></span></div>';
    try {
      const result = await api(
        `/api/search?q=${encodeURIComponent(query)}&path=${encodeURIComponent(state.picker.path)}&hidden=${state.picker.hidden}`
      );
      state.picker.search = true;
      renderSearchResults(result.matches || [], result.truncated);
    } catch (error) {
      $("pickerList").innerHTML = "";
      $("pickerList").append(emptyNode(error.message));
    }
  }

  function renderSearchResults(matches, truncated) {
    const holder = $("pickerList");
    holder.innerHTML = "";
    if (!matches.length) {
      holder.append(emptyNode("Ничего не найдено."));
      return;
    }
    for (const entry of matches) {
      const row = document.createElement("div");
      row.className = "picker-row file";
      row.innerHTML = '<svg><use href="#i-file"/></svg><span class="name"></span>';
      row.querySelector(".name").textContent = entry.name;
      if (entry.likely_sops) {
        const badge = document.createElement("span");
        badge.className = "badge";
        badge.textContent = "sops";
        row.append(badge);
      }
      const rel = document.createElement("span");
      rel.className = "rel";
      rel.textContent = entry.relative || entry.path;
      row.append(rel);
      row.addEventListener("click", () => {
        state.picker.selected = entry;
        $("pickerChoose").disabled = false;
        $("pickerEncrypt").disabled = false;
        holder.querySelectorAll(".picker-row.selected").forEach((el) => el.classList.remove("selected"));
        row.classList.add("selected");
      });
      row.addEventListener("dblclick", () => choosePickerFile(entry));
      holder.append(row);
    }
    if (truncated) {
      holder.append(emptyNode("Показаны первые 200 совпадений. Уточните запрос."));
    }
  }

  // --------------------------------------------------------------------- key
  function openKeyModal(reason) {
    renderKeyModal(reason);
    openModal("keyModal");
  }

  function requiredKeyText(info) {
    if (!info) return "";
    const lines = [];
    const age = (info.age_recipients || []).filter(Boolean);
    if (age.length) lines.push(`age: ${age.join(", ")}`);
    const pgp = (info.pgp_fingerprints || []).filter(Boolean);
    if (pgp.length) lines.push(`PGP: ${pgp.join(", ")}`);
    const others = (info.key_types || []).filter((type) => type !== "age" && type !== "pgp");
    if (others.length) lines.push(`провайдер: ${others.join(", ")}`);
    if (!lines.length) return "";
    return `Файл зашифрован для получателей — ${lines.join("; ")}. Нужен приватный ключ одного из них.`;
  }

  function renderKeyModal(reason) {
    const holder = $("keyCurrent");
    const key = state.key;
    const needed = requiredKeyText(state.pendingKeyInfo);
    const parts = [];
    if (reason) parts.push(`<p class="key-reason">${escapeHtml(reason)}</p>`);
    if (needed) parts.push(`<p class="key-needed">${escapeHtml(needed)}</p>`);
    if (!key) {
      parts.push("<p>Статус ключа неизвестен.</p>");
    } else if (key.source === "session") {
      parts.push(`<p><strong>Ключ в сессии.</strong> Получатели: ${escapeHtml((key.recipients || []).join(", ") || "—")}</p>`);
    } else if (key.source === "server_file" || key.source === "server_default") {
      parts.push(`<p><strong>Используется серверный ключ.</strong> ${escapeHtml(key.server_key_file || "~/.config/sops/age/keys.txt")}</p>`);
    } else if (key.source === "environment") {
      parts.push("<p><strong>Ключ из окружения сервера.</strong> Можно дополнительно задать ключ для этой вкладки.</p>");
    } else {
      parts.push("<p><strong>Ключ не задан.</strong> Вставьте приватный age-ключ ниже.</p>");
    }
    holder.innerHTML = parts.join("");
  }

  async function saveKey() {
    const value = $("keyInput").value.trim();
    if (!value) {
      toast("Ключ", "Вставьте AGE-SECRET-KEY-1…", "warn");
      return;
    }
    $("btnSaveKey").disabled = true;
    try {
      const result = await api("/api/session/key", { method: "POST", body: { key: value } });
      state.key = {
        source: "session",
        configured: true,
        session: true,
        recipients: result.recipients || [],
        server_key_file: state.key ? state.key.server_key_file : null,
        auth_enabled: state.status ? state.status.auth_enabled : false,
      };
      $("keyInput").value = "";
      state.pendingKeyInfo = null;
      renderKeyChip();
      renderStatus();
      closeModal("keyModal");
      toast("Ключ принят", `Получатели: ${(result.recipients || []).join(", ") || "—"}`, "ok");
      if (state.pendingPath) {
        const path = state.pendingPath;
        state.pendingPath = null;
        await openFile(path, { force: true });
      }
      if (state.pendingGitDiff) {
        state.pendingGitDiff = null;
        openGitDiff();
      }
    } catch (error) {
      toast("Ключ не принят", error.message, "bad");
    } finally {
      $("btnSaveKey").disabled = false;
    }
  }

  async function forgetKey() {
    try {
      const result = await api("/api/session/key", { method: "DELETE" });
      state.key = result.key;
      $("keyInput").value = "";
      renderKeyChip();
      renderStatus();
      renderKeyModal();
      toast("Ключ забыт", "Сессионный ключ удалён из памяти.", "ok");
    } catch (error) {
      toast("Не удалось забыть ключ", error.message, "bad");
    }
  }

  // ------------------------------------------------------------------- clone
  async function cloneRepo() {
    const url = $("gitUrl").value.trim();
    if (!url) {
      toast("Git", "Введите URL репозитория.", "warn");
      return;
    }
    const branch = $("gitBranch").value.trim() || null;
    $("btnClone").disabled = true;
    try {
      const result = await api("/api/git/clone", {
        method: "POST",
        body: { url, branch },
      });
      toast("Репозиторий склонирован", result.path, "ok");
      openPicker(result.path);
    } catch (error) {
      toast("Клонирование не удалось", error.message, "bad");
    } finally {
      $("btnClone").disabled = false;
    }
  }

  // ------------------------------------------------------------------ events
  function bindEvents() {
    $("brandBtn").addEventListener("click", requestCloseFile);
    $("btnBack").addEventListener("click", requestCloseFile);
    $("btnTheme").addEventListener("click", toggleTheme);
    $("btnSave").addEventListener("click", saveFile);
    $("btnDiff").addEventListener("click", openDiff);
    $("btnReload").addEventListener("click", async () => {
      if (!state.file) return;
      if (state.dirty) {
        const ok = await showConfirm({
          title: "Перезагрузить файл",
          text: "Несохранённые изменения будут потеряны. Продолжить?",
          okLabel: "Перезагрузить",
        });
        if (!ok) return;
      }
      openFile(state.file.path, { force: true });
    });
    $("btnToggleSecrets").addEventListener("click", () => {
      state.secretsOpen = !state.secretsOpen;
      if (window.matchMedia("(max-width: 980px)").matches) {
        $("secretsPanel").classList.toggle("mobile-open", state.secretsOpen);
      } else {
        $("secretsPanel").classList.toggle("hidden", !state.secretsOpen);
      }
      $("btnToggleSecrets").setAttribute("aria-pressed", String(state.secretsOpen));
      if (state.editor) setTimeout(() => state.editor.refresh(), 30);
    });
    $("btnReveal").addEventListener("click", () => {
      state.showValues = !state.showValues;
      sessionStorage.setItem(REVEAL_KEY, state.showValues ? "1" : "0");
      renderSecrets();
    });
    $("secretSearch").addEventListener("input", debounce(renderSecrets, 160));
    $("filePath").addEventListener("click", async () => {
      if (!state.file) return;
      const ok = await copyText(state.file.path);
      toast(ok ? "Путь скопирован" : "Не удалось скопировать", ok ? "" : "Буфер обмена недоступен", ok ? "ok" : "bad");
    });
    $("btnCommit").addEventListener("click", openCommitModal);
    $("btnCommitRun").addEventListener("click", runCommit);
    $("btnPull").addEventListener("click", pullRepo);
    $("btnBackups").addEventListener("click", openBackupsModal);
    $("btnKeyOpen").addEventListener("click", () => openKeyModal());
    $("btnSaveKey").addEventListener("click", saveKey);
    $("btnForgetKey").addEventListener("click", forgetKey);
    $("keyFile").addEventListener("change", async (event) => {
      const file = event.target.files && event.target.files[0];
      if (!file) return;
      try {
        $("keyInput").value = await file.text();
      } catch {
        toast("Ключ", "Не удалось прочитать файл.", "bad");
      }
    });
    $("btnBrowse").addEventListener("click", () => openPicker($("pathInput").value || undefined));
    $("btnOpenPath").addEventListener("click", () => {
      const path = $("pathInput").value.trim();
      if (!path) { toast("Файл", "Укажите путь.", "warn"); return; }
      openFile(path);
    });
    $("pathInput").addEventListener("keydown", (event) => {
      if (event.key === "Enter") $("btnOpenPath").click();
    });
    $("btnClone").addEventListener("click", cloneRepo);
    $("gitUrl").addEventListener("keydown", (event) => {
      if (event.key === "Enter") cloneRepo();
    });
    $("btnLocalPick").addEventListener("click", pickLocalFile);
    $("localFileInput").addEventListener("change", (event) => {
      const file = event.target.files && event.target.files[0];
      if (file) uploadLocalFile(file);
    });
    $("btnEncryptFile").addEventListener("click", () => {
      if (state.file) openEncryptModal({ path: state.file.path, name: state.file.name });
    });
    $("btnAllowWrite").addEventListener("click", allowWriteBack);
    $("pickerEncrypt").addEventListener("click", () => {
      const entry = state.picker.selected;
      if (entry && entry.type === "file") openEncryptModal({ path: entry.path, name: entry.name });
    });
    $("btnEncryptRun").addEventListener("click", runEncrypt);
    $("btnRotateKeys").addEventListener("click", openRotateModal);
    $("btnRotateRun").addEventListener("click", runRotate);
    $("btnGitDiff").addEventListener("click", openGitDiff);
    $("btnDownloadLocal").addEventListener("click", downloadLocalFile);
    $("btnWriteLocal").addEventListener("click", writeLocalFile);
    const dropZone = $("localDrop");
    ["dragenter", "dragover"].forEach((name) => {
      dropZone.addEventListener(name, (event) => {
        event.preventDefault();
        dropZone.classList.add("drag");
      });
    });
    ["dragleave", "drop"].forEach((name) => {
      dropZone.addEventListener(name, (event) => {
        event.preventDefault();
        dropZone.classList.remove("drag");
      });
    });
    dropZone.addEventListener("drop", async (event) => {
      const items = event.dataTransfer && event.dataTransfer.items;
      const item = items && items[0];
      if (item && item.getAsFileSystemHandle) {
        try {
          const handle = await item.getAsFileSystemHandle();
          if (handle && handle.kind === "file") {
            await uploadLocalFile(await handle.getFile(), handle);
            return;
          }
        } catch {
          /* fall through to a plain file */
        }
      }
      const file = event.dataTransfer && event.dataTransfer.files && event.dataTransfer.files[0];
      if (file) uploadLocalFile(file);
    });
    dropZone.addEventListener("click", (event) => {
      if (event.target.closest("button, input")) return;
      pickLocalFile();
    });
    $("btnClearRecent").addEventListener("click", () => {
      state.recents = [];
      try { localStorage.removeItem(RECENT_KEY); } catch { /* ignore */ }
      renderRecent();
    });
    document.querySelectorAll(".tab").forEach((tab) => {
      tab.addEventListener("click", () => {
        document.querySelectorAll(".tab").forEach((item) => item.classList.toggle("active", item === tab));
        document.querySelectorAll(".tab-panel").forEach((panel) => panel.classList.add("hidden"));
        const panelId = tab.dataset.tab === "path" ? "tabPath" : tab.dataset.tab === "git" ? "tabGit" : "tabLocal";
        $(panelId).classList.remove("hidden");
      });
    });

    // picker
    $("pickerUp").addEventListener("click", () => {
      if (state.picker.parent) loadBrowse(state.picker.parent);
    });
    $("pickerRefresh").addEventListener("click", () => loadBrowse(state.picker.path));
    $("pickerHidden").addEventListener("change", (event) => {
      state.picker.hidden = event.target.checked;
      if (state.picker.search) runPickerSearch();
      else loadBrowse(state.picker.path);
    });
    $("pickerFilter").addEventListener("input", () => {
      if (!state.picker.search) renderPickerEntries();
    });
    $("pickerFilter").addEventListener("keydown", (event) => {
      if (event.key === "Enter") runPickerSearch();
    });
    $("pickerSearchBtn").addEventListener("click", runPickerSearch);
    $("pickerChoose").addEventListener("click", () => choosePickerFile(state.picker.selected));

    document.addEventListener("keydown", (event) => {
      const mod = event.ctrlKey || event.metaKey;
      if (mod && event.key.toLowerCase() === "s") {
        event.preventDefault();
        saveFile();
      } else if (event.key === "Escape") {
        closeTopModal();
      }
    });

    window.addEventListener("beforeunload", (event) => {
      if (state.file && state.dirty) {
        event.preventDefault();
        event.returnValue = "";
      }
    });

    window.addEventListener("popstate", () => {
      const params = new URLSearchParams(location.search);
      const path = params.get("path");
      if (path) openFile(path, { force: true });
      else closeFile({ force: true });
    });
  }

  // --------------------------------------------------------------------- init
  async function init() {
    applyTheme(localStorage.getItem(THEME_KEY) || "dark");
    bindModalDismiss();
    setupEditor();
    bindEvents();
    loadRecents();
    try {
      await loadStatus();
    } catch (error) {
      if (error instanceof ApiError && error.status === 401) {
        toast("Требуется авторизация", "Обновите страницу и введите логин/пароль.", "bad");
      } else {
        toast("Сервер", error.message, "bad");
      }
    }
    loadHealth();
    const params = new URLSearchParams(location.search);
    const path = params.get("path");
    if (path) openFile(path);
  }

  document.addEventListener("DOMContentLoaded", init);
})();
