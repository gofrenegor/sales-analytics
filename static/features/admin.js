function renderSummary(summary) {
  rememberYears(summary);
  const host = document.getElementById("db-summary");
  host.replaceChildren();
  if (!summary) {
    host.append(el("p", { class: "hint" }, "Нет данных о базе."));
    return;
  }
  if (!summary.total_rows) {
    host.append(el("p", { class: "box info" }, "База данных пока пуста."));
    return;
  }
  const total = el("p");
  total.append(strong("Всего строк: "), fmtInt(summary.total_rows));
  host.append(total);
  if (summary.min_date && summary.max_date) {
    const period = el("p");
    period.append(strong("Период: "), `${fmtDate(summary.min_date)} — ${fmtDate(summary.max_date)}`);
    host.append(period);
  }
  if (summary.months && summary.months.length) {
    host.append(moneyTable(["Месяц", "Строк"], summary.months.map((month) => [month.month, fmtInt(month.rows)])));
  }
}

async function parseJson(response) {
  const text = await response.text();
  const start = text.trimStart();
  if (!start || start[0] === "<") {
    if (response.status === 413) throw new Error("Файл слишком большой, прокси его не пропустил");
    throw new Error("Сервер вернул страницу ошибки вместо данных");
  }
  try {
    return JSON.parse(text);
  } catch (error) {
    throw new Error("Сервер вернул не данные");
  }
}

async function readJson(response) {
  const data = await parseJson(response);
  if (!response.ok) throw new Error(data.error || "Ошибка");
  return data;
}

function showSecret(box, input, password) {
  if (!box || !input) return;
  input.value = password || "";
  box.hidden = !password;
  if (!password) return;
  input.focus();
  input.select();
}

function wireGrant() {
  const toggle = document.getElementById("grant-toggle");
  const panel = document.getElementById("grant-panel");
  const value = document.getElementById("grant-id-value");
  const phone = document.getElementById("grant-phone");
  const email = document.getElementById("grant-email");
  const domain = document.getElementById("grant-domain");
  const submit = document.getElementById("grant-submit");
  const status = document.getElementById("grant-status");
  const secret = document.getElementById("grant-secret");
  const secretValue = document.getElementById("grant-secret-value");
  if (!toggle || !panel || !submit) return;
  const chosen = () => panel.querySelector("input[name='grant-role']:checked");
  const grantReady = () => Boolean(
    chosen()
    && phone
    && digitsOnly(phone.value).length >= 10
    && email
    && email.value.trim(),
  );
  const syncSubmit = () => {
    const ready = grantReady();
    submit.disabled = !ready;
    submit.textContent = ready ? "сгенерировать пароль для пользователя" : "Выдать";
  };
  phone?.addEventListener("input", () => {
    phone.value = phoneDraft(phone.value);
    syncSubmit();
  });
  if (email && domain) bindNick(email, domain);
  email?.addEventListener("input", syncSubmit);
  secretValue?.addEventListener("focus", () => secretValue.select());
  secretValue?.addEventListener("click", () => secretValue.select());
  const refreshId = async () => {
    const data = await readJson(await fetch("/api/admin/access/next"));
    value.textContent = String(data.id);
  };
  toggle.addEventListener("click", async () => {
    if (!panel.hidden) {
      panel.hidden = true;
      toggle.setAttribute("aria-expanded", "false");
      return;
    }
    try {
      await refreshId();
      panel.hidden = false;
      toggle.setAttribute("aria-expanded", "true");
      syncSubmit();
    } catch (error) {
      setAdminStatus(error.message);
    }
  });
  panel.addEventListener("change", syncSubmit);
  submit.addEventListener("click", async () => {
    const role = chosen()?.value;
    if (!role || !phone || !grantReady()) return;
    submit.disabled = true;
    try {
      const data = await readJson(await fetch("/api/admin/access", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          role,
          phone: phone.value,
          email: email ? email.value : "",
          domain: domain ? domain.value : "",
        }),
      }));
      showSecret(secret, secretValue, data.password);
      status.hidden = false;
      status.textContent = `Выдан ID ${data.id} · +${data.phone} · ${data.email} · ${data.role === "admin" ? "админка" : "просмотр"}`;
      phone.value = "";
      if (email) email.value = "";
      if (domain) domain.value = "gmail.com";
      await refreshId();
    } catch (error) {
      setAdminStatus(error.message);
    } finally {
      syncSubmit();
    }
  });
}

function wireOwnPhone() {
  const input = document.getElementById("own-phone");
  const email = document.getElementById("own-email");
  const domain = document.getElementById("own-domain");
  const button = document.getElementById("own-phone-save");
  if (!input || !button) return;
  input.addEventListener("input", () => {
    input.value = phoneDraft(input.value);
  });
  if (email && domain) bindNick(email, domain);
  button.addEventListener("click", async () => {
    button.disabled = true;
    try {
      const data = await readJson(await fetch("/api/admin/access/contact", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          phone: input.value,
          email: email ? email.value : "",
          domain: domain ? domain.value : "",
        }),
      }));
      state.user = { ...state.user, ...data, ok: true };
      fillOwnContact(data);
      setAdminStatus("Контакты сохранены");
    } catch (error) {
      setAdminStatus(error.message);
    } finally {
      button.disabled = false;
    }
  });
}

const UPLOAD_CHUNK = 256 * 1024;

function newUploadId() {
  const bytes = new Uint8Array(16);
  crypto.getRandomValues(bytes);
  return Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join("");
}

function uploadKind() {
  const picked = document.querySelector('input[name="upload-kind"]:checked');
  return picked && picked.value === "twin" ? "twin" : "superset";
}

async function sendPreview(files, hint) {
  files = Array.from(files);
  if (files.length > 20 || files.reduce((sum, file) => sum + file.size, 0) > 80 * 1024 * 1024) {
    throw new Error("Не больше 20 файлов общим размером до 80 МБ");
  }
  const batch = newUploadId();
  const ids = [];
  const kind = uploadKind();
  const pieces = files.reduce((sum, file) => sum + Math.max(1, Math.ceil(file.size / UPLOAD_CHUNK)), 0);
  let completed = 0;
  for (const file of files) {
    const uploadId = newUploadId();
    ids.push(uploadId);
    const total = Math.max(1, Math.ceil(file.size / UPLOAD_CHUNK));
    let next = 0;
    let failed = false;
    const worker = async () => {
      while (next < total && !failed) {
        const index = next++;
        try {
          for (let attempt = 0; attempt < 3; attempt += 1) {
            const body = new FormData();
            body.append("batch", batch);
            body.append("upload_id", uploadId);
            body.append("name", file.name);
            body.append("index", String(index));
            body.append("total", String(total));
            body.append("chunk", file.slice(index * UPLOAD_CHUNK, (index + 1) * UPLOAD_CHUNK), "part");
            let response;
            try {
              response = await fetch("/api/admin/preview/chunk", { method: "POST", body });
              await readJson(response);
              break;
            } catch (error) {
              if (attempt === 2 || (response && response.status < 500)) throw error;
              await new Promise((resolve) => setTimeout(resolve, 250 * (2 ** attempt)));
            }
          }
          completed += 1;
          if (hint) hint.textContent = `Загружаем файлы… ${completed} из ${pieces}`;
        } catch (error) {
          failed = true;
          throw error;
        }
      }
    };
    // All workers must finish before surfacing an error or allowing another upload.
    const results = await Promise.allSettled(Array.from({ length: Math.min(3, total) }, worker));
    const error = results.find((result) => result.status === "rejected");
    if (error) throw error.reason;
  }
  if (hint) hint.textContent = "Разбираем файлы…";
  return readJson(await fetch("/api/admin/preview/finish", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ batch, ids, kind }),
  }));
}

function wireAdmin() {
  wireGrant();
  wireOwnPhone();
  wireOwnerTools();
  document.getElementById("cache-btn").addEventListener("click", async () => {
    try {
      const data = await readJson(await fetch("/api/admin/cache/clear", { method: "POST" }));
      setAdminStatus(data.message);
      if (state.mode === "Свободный" ? state.rangeStart && state.rangeEnd : state.anchor) loadReport();
    } catch (error) {
      setAdminStatus(error.message);
    }
  });

  document.getElementById("files").addEventListener("change", async (event) => {
    const preview = document.getElementById("upload-preview");
    const files = event.target.files;
    preview.replaceChildren();
    if (!files.length) return;
    const hint = el("p", { class: "hint" }, "Читаем файлы…");
    preview.append(hint);
    try {
      const data = await sendPreview(files, hint);
      preview.replaceChildren();
      const twin = data.kind === "twin";
      if (!data.rows) {
        const empty = twin
          ? (data.skipped_not_lr ? "В twin нет пропусков 5 ЛР с наименованием МХ." : "В twin нет строк с наименованием МХ.")
          : "Нет строк с датой — нечего загружать.";
        preview.append(el("p", { class: "box warn" }, empty));
        if (data.skipped_no_date) preview.append(el("p", { class: "box warn" }, `Пропущено строк без даты: ${data.skipped_no_date}`));
        if (data.skipped_not_lr) preview.append(el("p", { class: "box warn" }, `Пропущено не 5 ЛР: ${data.skipped_not_lr}`));
        if (data.skipped_no_mx) preview.append(el("p", { class: "box warn" }, `Пропущено без МХ: ${data.skipped_no_mx}`));
        return;
      }
      preview.append(el("p", { class: "box ok" }, `Прочитано файлов: ${data.files}, строк к загрузке: ${data.rows}`));
      if (data.start) preview.append(el("p", { class: "box info" }, `Период: ${fmtDate(data.start)} — ${fmtDate(data.end)}`));
      if (data.skipped_no_date) preview.append(el("p", { class: "box warn" }, `Пропущено строк без даты: ${data.skipped_no_date}`));
      if (data.skipped_no_barcode) preview.append(el("p", { class: "box warn" }, `Пропущено без ШК: ${data.skipped_no_barcode}`));
      if (data.skipped_no_mx) preview.append(el("p", { class: "box warn" }, `Пропущено без наименования МХ: ${data.skipped_no_mx}`));
      if (data.skipped_not_lr) preview.append(el("p", { class: "box warn" }, `Пропущено не 5 ЛР: ${data.skipped_not_lr}`));
      if (data.duplicates) {
        const dupText = twin
          ? `Одинаковых строк файла: ${fmtInt(data.duplicates)}. Сборщики с одним ШК остаются.`
          : `Повтор одного сотрудника и ШК: ${fmtInt(data.duplicates)}`;
        preview.append(el("p", { class: "box warn" }, dupText));
      }
      const saveLabel = twin ? "Дописать МХ" : "💾 ВЫГРУЗИТЬ В БАЗУ";
      if (twin) {
        preview.append(el("p", { class: "box info" }, `Совпало по ШК: ${fmtInt(data.matched || 0)}`));
        if (data.unmatched) preview.append(el("p", { class: "box warn" }, `ШК не найден в суперсете: ${fmtInt(data.unmatched)}`));
        preview.append(el("p", { class: "hint" }, "Наименование МХ встанет каждому сборщику с этим ШК, справа от стикера. Потери не удаляются. Суммы не меняются."));
        if (!data.token) {
          preview.append(el("p", { class: "box warn" }, "В суперсете нет этих ШК. Сначала загрузите суперсет."));
          return;
        }
      } else {
        if (!data.mx_rows) {
          preview.append(el("p", { class: "hint" }, "В суперсете нет МХ пропуска. После сохранения загрузите Twin."));
        }
        preview.append(el("p", { class: "hint" }, "Совпавшие ШК у тех же сотрудников обновятся, включая флаг оприходования и статус после списания. Новые потери добавятся, остальные сохранятся. Уже дописанное МХ по тому же ШК сохранится. Для обновления старых потерь включите их даты в выгрузку."));
      }
      const button = el("button", { type: "button", class: "wide primary" }, saveLabel);
      button.addEventListener("click", async () => {
        button.disabled = true;
        button.textContent = "Загрузка...";
        try {
          const saved = await readJson(await fetch("/api/admin/commit", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ token: data.token }),
          }));
          setAdminStatus(`🎉 ${saved.message}`);
          renderSummary(saved.summary);
          preview.replaceChildren();
          event.target.value = "";
          if (!state.anchor) state.anchor = saved.summary.max_date || state.anchor;
          syncForm(true);
          writeUrl(false);
          loadReport();
        } catch (error) {
          button.disabled = false;
          button.textContent = saveLabel;
          preview.append(el("p", { class: "box bad" }, error.message));
        }
      });
      preview.append(button);
    } catch (error) {
      preview.replaceChildren(el("p", { class: "box bad" }, error.message));
    }
  });

  document.getElementById("sure").addEventListener("change", (event) => {
    document.getElementById("wipe").disabled = !event.target.checked;
    const tools = document.getElementById("owner-tools");
    const owner = state.user && Number(state.user.id) === 1 && state.user.role === "admin";
    if (tools) tools.hidden = !(event.target.checked && owner);
    if (event.target.checked && owner) loadOwnerUsers();
  });
  document.getElementById("wipe").addEventListener("click", async () => {
    try {
      const data = await readJson(await fetch("/api/admin/truncate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ confirm: true }),
      }));
      setAdminStatus(data.message);
      renderSummary(data.summary);
      document.getElementById("sure").checked = false;
      document.getElementById("wipe").disabled = true;
      loadReport();
    } catch (error) {
      setAdminStatus(error.message);
    }
  });
}

function infoWide() {
  const info = document.getElementById("admin-info");
  if (!info?.open) return false;
  return ["info-lr", "info-statuses"].some((id) => document.getElementById(id)?.open);
}

function syncInfoWidth() {
  const sidebar = document.getElementById("sidebar");
  if (!sidebar?.classList.contains("open")) return;
  sidebar.classList.toggle("wide", infoWide());
}

function setAdminOpen(open) {
  const sidebar = document.getElementById("sidebar");
  const backdrop = document.getElementById("backdrop");
  const toggle = document.getElementById("admin-toggle");
  sidebar?.classList.toggle("open", open);
  backdrop?.classList.toggle("show", open);
  toggle?.setAttribute("aria-expanded", open ? "true" : "false");
  if (!sidebar) return;
  if (!open) sidebar.classList.remove("wide");
  else if (infoWide()) sidebar.classList.add("wide");
}

let infoScriptLoading = null;

async function openStatusInfo(code) {
  const info = document.getElementById("admin-info");
  const statuses = document.getElementById("info-statuses");
  const search = document.getElementById("status-search");
  if (!info || !statuses || !search) return;
  info.open = true;
  statuses.open = true;
  search.value = code;
  setAdminOpen(true);
  search.focus({ preventScroll: true });
  statuses.scrollIntoView({ block: "nearest" });
  const list = document.getElementById("info-status-list");
  list?.replaceChildren(el("p", { class: "st-empty" }, "Загрузка…"));
  try {
    await ensureInfoCatalog();
    renderStatuses();
  } catch (error) {
    list?.replaceChildren(el("p", { class: "box bad", role: "alert" }, error.message));
  }
}

function ensureInfoCatalog() {
  if (infoScriptLoading) return infoScriptLoading;
  infoScriptLoading = new Promise((resolve, reject) => {
    const script = document.createElement("script");
    const version = document.querySelector('script[src*="/static/app.js"]')?.src.split("?")[1] || "";
    script.src = `/static/info-copy.js${version ? `?${version}` : ""}`;
    script.onload = resolve;
    script.onerror = () => {
      script.remove();
      infoScriptLoading = null;
      reject(new Error("Не удалось загрузить справочник. Откройте раздел ещё раз."));
    };
    document.body.append(script);
  });
  return infoScriptLoading;
}

function wireInfoWidth() {
  const sidebar = document.getElementById("sidebar");
  if (!sidebar) return;
  for (const id of ["admin-info", "info-lr", "info-statuses"]) {
    document.getElementById(id)?.addEventListener("toggle", (event) => {
      syncInfoWidth();
      if (event.target.open) ensureInfoCatalog().catch((error) => setAdminStatus(error.message));
    });
  }
}

function wireAdminPanel() {
  const sidebar = document.getElementById("sidebar");
  const backdrop = document.getElementById("backdrop");
  if (!sidebar || !backdrop) return;
  const toggle = document.getElementById("admin-toggle");
  wireInfoWidth();
  if (toggle) {
    toggle.addEventListener("click", () => setAdminOpen(!sidebar.classList.contains("open")));
    backdrop.addEventListener("click", () => setAdminOpen(false));
    return;
  }
  const menu = document.getElementById("menu");
  if (!menu) return;
  menu.addEventListener("click", () => {
    sidebar.classList.toggle("open");
    backdrop.classList.toggle("show");
  });
  backdrop.addEventListener("click", () => {
    sidebar.classList.remove("open", "wide");
    backdrop.classList.remove("show");
  });
}

function wireDigits(input, size, next, replaceFull, prev, prefix) {
  const afterEdit = () => {
    let clean = digitsOnly(input.value).slice(0, size);
    if (size === 2 && clean.length === 1) {
      const n = Number(clean);
      const jump = input.classList.contains("ymd-day") ? n >= 4 : input.classList.contains("ymd-month") && n >= 2;
      if (jump) clean = `0${clean}`;
    }
    if (input.value !== clean) input.value = clean;
    const invalidDay = input.classList.contains("ymd-day") && clean.length === 2 && (+clean < 1 || +clean > 31);
    if (clean.length === size && next && document.activeElement === input && !invalidDay) next.focus();
    refreshDate(prefix);
  };
  input.addEventListener("focus", () => input.select());
  input.addEventListener("keydown", (event) => {
    if (event.key === "ArrowRight" || event.key === "ArrowLeft") {
      const yearField = input.classList.contains("ymd-year");
      if (event.key === "ArrowRight") {
        if (yearField) return;
        event.preventDefault();
        if (next) next.focus();
        return;
      }
      if (yearField) {
        const start = input.selectionStart ?? 0;
        const end = input.selectionEnd ?? 0;
        if (start !== 0 || end !== 0) return;
      }
      event.preventDefault();
      if (prev) prev.focus();
      return;
    }
    if (event.key === "Backspace" && !input.value && prev) {
      prev.focus();
      return;
    }
    if (event.key.length === 1 && !/\d/.test(event.key) && !event.ctrlKey && !event.metaKey && !event.altKey) {
      event.preventDefault();
      return;
    }
    if (!/^\d$/.test(event.key)) return;
    const start = input.selectionStart ?? 0;
    const end = input.selectionEnd ?? 0;
    const collapsedAtEnd = end === start && start === input.value.length;
    if (input.value.length >= size && end === start && (replaceFull || collapsedAtEnd)) {
      event.preventDefault();
      input.value = event.key;
      input.setSelectionRange(1, 1);
      afterEdit();
    }
  });
  input.addEventListener("input", afterEdit);
}

function pasteDate(event, prefix) {
  const text = event.clipboardData ? event.clipboardData.getData("text") : "";
  const match = String(text || "").trim().match(/^(\d{1,2})[.\-/](\d{1,2})[.\-/](\d{4})$/);
  if (!match) return;
  event.preventDefault();
  writeParts(prefix, `${match[3]}-${match[2].padStart(2, "0")}-${match[1].padStart(2, "0")}`, true);
  refreshDate(prefix);
}

function bindRangeField(prefix) {
  const day = partEl(prefix, "day");
  const month = partEl(prefix, "month");
  const year = partEl(prefix, "year");
  month.dataset.prev = `${prefix}-day`;
  year.dataset.prev = `${prefix}-month`;
  wireDigits(day, 2, month, false, null, prefix);
  wireDigits(month, 2, null, false, day, prefix);
  wireDigits(year, 4, null, true, month, prefix);
  const padOnBlur = (input) => {
    input.addEventListener("blur", () => {
      const n = digitsOnly(input.value);
      if (n.length === 1) input.value = n.padStart(2, "0");
      refreshDate(prefix);
    });
  };
  padOnBlur(day);
  padOnBlur(month);
  year.addEventListener("blur", () => {
    if (!digitsOnly(year.value)) year.value = DEFAULT_YEAR;
    refreshDate(prefix);
  });
  for (const input of [day, month, year]) input.addEventListener("paste", (event) => pasteDate(event, prefix));
  const native = document.getElementById(prefix);
  native.addEventListener("pointerdown", () => prepareRangePicker(prefix), true);
  native.addEventListener("change", () => {
    const value = isoDay(native.value);
    delete native.dataset.seed;
    if (value) writeParts(prefix, value, true);
  }, true);
  native.addEventListener("cancel", () => {
    delete native.dataset.seed;
    native.value = isoFromParts(readParts(prefix)) || "";
  });
}

function wireSearch() {
  const input = document.getElementById("search");
  const hits = document.getElementById("search-hits");
  if (!input || !hits) return;
  let items = [];
  const hide = () => {
    hits.hidden = true;
    hits.replaceChildren();
    items = [];
  };
  const paint = () => {
    items = searchHits(input.value);
    hits.replaceChildren();
    if (!input.value.trim()) {
      hits.hidden = true;
      return;
    }
    hits.hidden = false;
    if (!items.length) {
      hits.append(el("p", { class: "search-empty" }, "Ничего не найдено"));
      return;
    }
    for (const item of items) {
      hits.append(el(
        "button",
        {
          type: "button",
          class: "search-hit",
          onclick: () => {
            const target = item.go;
            input.value = "";
            hide();
            go(target);
          },
        },
        el("span", { class: "search-kind" }, item.kind),
        el("span", { class: "search-main" }, el("b", null, item.title), el("span", null, item.meta)),
        el("span", { class: "search-sum" }, fmtRub(item.sum)),
      ));
    }
  };
  input.addEventListener("input", paint);
  input.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      input.value = "";
      hide();
      return;
    }
    if (event.key === "Enter" && items[0]) {
      event.preventDefault();
      const target = items[0].go;
      input.value = "";
      hide();
      go(target);
    }
  });
  document.addEventListener("click", (event) => {
    if (!event.target.closest(".search")) hide();
  });
}

function wireChrome() {
  wireAdminPanel();
  wireSearch();
  document.getElementById("section-losses")?.addEventListener("click", () => {
    if (state.view !== "employees") return;
    go({ view: "dashboard", flow: "", group: "", block: "" });
  });
  document.getElementById("section-employees")?.addEventListener("click", () => {
    if (state.view === "employees") return;
    go({ view: "employees", flow: "", group: "", block: "" });
  });
  document.getElementById("slice-blocks")?.addEventListener("click", () => {
    if (state.view === "dashboard" && state.slice === "blocks" && !state.flow && !state.group && !state.block) return;
    go({ view: "dashboard", slice: "blocks", flow: "", group: "", block: "" });
  });
  document.getElementById("slice-flows")?.addEventListener("click", () => {
    if (state.view === "dashboard" && state.slice === "flows" && !state.flow && !state.group && !state.block) return;
    go({ view: "dashboard", slice: "flows", flow: "", group: "", block: "" });
  });
  document.getElementById("slice-writeoffs")?.addEventListener("click", () => {
    go({ view: "dashboard", slice: "writeoffs", flow: "", group: "", block: "" });
  });
  window.addEventListener("resize", () => {
    layoutFork();
    layoutPairOutlines();
  });
  if (document.fonts) document.fonts.ready.then(() => layoutFork());
  document.getElementById("theme")?.addEventListener("click", () => {
    const current = document.documentElement.dataset.theme || "system";
    const next = current === "dark" ? "light" : current === "light" ? "system" : "dark";
    if (next === "system") {
      delete document.documentElement.dataset.theme;
      localStorage.removeItem("theme");
    } else {
      document.documentElement.dataset.theme = next;
      localStorage.setItem("theme", next);
    }
    render();
  });
  document.getElementById("dates").addEventListener("submit", (event) => event.preventDefault());
  wirePickers();
  for (const prefix of ["anchor", "range-start", "range-end"]) bindRangeField(prefix);
  document.getElementById("dates").addEventListener("change", onControlsChange);
  window.addEventListener("popstate", () => {
    const prevDepth = VIEW_DEPTH[state.view] ?? 0;
    const prevKey = viewKey();
    const period = periodKey();
    readUrl();
    syncForm(true);
    if (viewKey() !== prevKey) {
      const nextDepth = VIEW_DEPTH[state.view] ?? 0;
      if (nextDepth < prevDepth) screenMotion = "back";
      else if (nextDepth > prevDepth) screenMotion = "forward";
      else screenMotion = "";
    }
    if (period !== periodKey()) {
      if (freeRangePending()) {
        loadSeq += 1;
        cancelLoad("report");
        resetEmployees();
        setStatus("");
        setBanner("");
        render();
      } else loadReport({ spin: true });
    } else {
      render();
      if (state.view === "employees") loadEmployees();
      else if (!freeRangePending() && !reportHasDetail(reportDetailForView())) loadReport();
    }
  });
}

function showGate() {
  document.getElementById("screen")?.removeAttribute("aria-busy");
  loadSeq += 1;
  cancelLoad("report");
  resetEmployees();
  state.report = null;
  state.reportKey = "";
  state.reportDetail = "";
  state.user = null;
  const gate = document.getElementById("gate");
  const layout = document.querySelector(".layout");
  const toggle = document.getElementById("admin-toggle");
  if (gate) gate.hidden = false;
  if (layout) layout.hidden = true;
  if (toggle) toggle.hidden = true;
  paintAccount(null);
  const fold = document.getElementById("password-fold");
  if (fold) fold.open = false;
  const next = document.getElementById("password-next");
  if (next) next.value = "";
  setPasswordNote("");
  const sure = document.getElementById("sure");
  if (sure) sure.checked = false;
  const ownerTools = document.getElementById("owner-tools");
  if (ownerTools) ownerTools.hidden = true;
  setAdminOpen(false);
  document.getElementById("gate-contact")?.focus();
}

function phoneDraft(value) {
  const cleaned = String(value || "").replace(/[^\d+]/g, "");
  const plus = cleaned.startsWith("+") ? "+" : "";
  return (plus + cleaned.replace(/\+/g, "")).slice(0, 16);
}

function contactMode(value) {
  const text = String(value || "").trim();
  if (!text) return "";
  if (text.includes("@") || /[a-zа-яё]/i.test(text[0])) return "email";
  if ("+789".includes(text[0])) return "phone";
  return "";
}

function nickDraft(value) {
  const pasted = String(value || "").trim().match(/^([^@\s]+)@([a-z0-9.-]+)$/i);
  return {
    nick: (pasted ? pasted[1] : value).toLowerCase().replace(/[^a-z0-9._-]/g, "").slice(0, 64),
    domain: pasted ? pasted[2].toLowerCase() : "",
  };
}

function bindNick(input, select) {
  input.addEventListener("input", () => {
    const draft = nickDraft(input.value);
    if (draft.domain && [...select.options].some((opt) => opt.value === draft.domain)) {
      select.value = draft.domain;
    }
    if (input.value !== draft.nick) input.value = draft.nick;
  });
}

function syncGateContact(input, select, at, line) {
  const draft = nickDraft(input.value);
  let value = input.value;
  if (draft.domain) {
    value = draft.nick;
    if ([...select.options].some((opt) => opt.value === draft.domain)) select.value = draft.domain;
  }
  const mode = contactMode(value.trim());
  const email = mode === "email";
  if (mode === "phone") value = phoneDraft(value);
  else if (email) value = nickDraft(value).nick;
  select.hidden = !email;
  if (at) at.hidden = !email;
  line?.classList.toggle("email", email);
  input.placeholder = email ? "ник" : mode === "phone" ? "телефон" : "телефон или почта";
  input.setAttribute("inputmode", email ? "email" : "tel");
  input.setAttribute("aria-label", email ? "Ник почты" : "Телефон или ник почты");
  if (input.value !== value) input.value = value;
}

function fillOwnContact(user) {
  const phone = document.getElementById("own-phone");
  const email = document.getElementById("own-email");
  const domain = document.getElementById("own-domain");
  if (phone && document.activeElement !== phone) phone.value = user.phone ? `+${user.phone}` : "";
  if (!email || document.activeElement === email) return;
  const parts = String(user.email || "").split("@");
  email.value = parts[0] || "";
  if (domain && parts[1] && [...domain.options].some((opt) => opt.value === parts[1])) {
    domain.value = parts[1];
  }
}

function ownerDate(value) {
  if (!value) return "не указано";
  try { return new Date(Number(value) * 1000).toLocaleString("ru-RU"); } catch (error) { return "не указано"; }
}

async function loadOwnerUsers() {
  const host = document.getElementById("owner-users");
  if (!host) return;
  host.replaceChildren(el("p", { class: "hint" }, "Загрузка списка…"));
  try {
    const data = await readJson(await fetch("/api/admin/owner/users"));
    host.replaceChildren();
    for (const user of data.users || []) {
      const issuer = user.issued_by ? `выдал ID ${user.issued_by}${user.issuer_email ? ` · ${user.issuer_email}` : ""}` : "источник не указан";
      const card = el("article", { class: "owner-user" },
        el("div", { class: "owner-user-main" }, el("strong", {}, `ID ${user.id} · ${user.role === "admin" ? "админка" : "просмотр"}`), el("span", {}, user.email || user.phone || "нет контактов")),
        el("div", { class: "owner-user-meta" }, `${issuer} · ${ownerDate(user.issued_at)}`),
      );
      if (Number(user.id) !== 1) {
        const input = el("input", { type: "password", minlength: "6", maxlength: "64", placeholder: "новый пароль", autocomplete: "new-password" });
        const button = el("button", { type: "button", class: "ghost" }, "сменить пароль");
        const note = el("span", { class: "owner-user-meta" });
        button.addEventListener("click", async () => {
          const password = input.value.trim();
          if (password.length < 6 || password.length > 64) { note.textContent = "Пароль от 6 до 64 символов"; return; }
          button.disabled = true;
          try {
            await readJson(await fetch("/api/admin/owner/password", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ user_id: user.id, password }) }));
            input.value = "";
            note.textContent = "пароль обновлён, прежние сессии закрыты";
          } catch (error) { note.textContent = error.message; }
          finally { button.disabled = false; }
        });
        card.append(el("div", { class: "owner-password" }, input, button), note);
      }
      host.append(card);
    }
    if (!host.children.length) host.append(el("p", { class: "hint" }, "Пользователей пока нет."));
  } catch (error) { host.replaceChildren(el("p", { class: "box bad" }, error.message)); }
}

function wireOwnerTools() {
  document.getElementById("owner-refresh")?.addEventListener("click", loadOwnerUsers);
}

function paintAccount(user) {
  const sidebar = document.getElementById("sidebar");
  const title = document.getElementById("sidebar-title");
  const toggle = document.getElementById("admin-toggle");
  const whoId = document.getElementById("who-id");
  const admin = Boolean(user && user.role === "admin");
  if (sidebar) {
    if (user) sidebar.dataset.role = admin ? "admin" : "view";
    else sidebar.removeAttribute("data-role");
  }
  if (title) title.textContent = user && !admin ? "👤 Панель" : "👤 Админ-панель";
  if (toggle && user) toggle.setAttribute("aria-label", admin ? "Админ-панель" : "Панель");
  if (whoId) whoId.textContent = user ? String(user.id) : "—";
}

function applySession() {
  const user = state.user;
  const gate = document.getElementById("gate");
  const layout = document.querySelector(".layout");
  const toggle = document.getElementById("admin-toggle");
  if (gate) gate.hidden = true;
  if (layout) layout.hidden = false;
  const admin = user && user.role === "admin";
  if (toggle) toggle.hidden = !user;
  if (!admin) setAdminOpen(false);
  paintAccount(user);
  fillOwnContact(user);
}

const PASSWORD_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789";

function randomPassword() {
  const result = [];
  const limit = 256 - (256 % PASSWORD_ALPHABET.length);
  while (result.length < 16) {
    const bytes = new Uint8Array(32);
    crypto.getRandomValues(bytes);
    for (const byte of bytes) {
      if (byte >= limit) continue;
      result.push(PASSWORD_ALPHABET[byte % PASSWORD_ALPHABET.length]);
      if (result.length === 16) break;
    }
  }
  return result.join("");
}

function setPasswordNote(text, bad) {
  const note = document.getElementById("password-note");
  if (!note) return;
  note.textContent = text || "";
  note.hidden = !text;
  note.classList.toggle("is-error", Boolean(bad));
}

function wirePassword() {
  const panel = document.getElementById("password-panel");
  const input = document.getElementById("password-next");
  const random = document.getElementById("password-random");
  const logoutBtn = document.getElementById("logout-btn");
  if (!panel || !input) return;
  random?.addEventListener("click", () => {
    input.value = randomPassword();
    input.focus();
    input.select();
    setPasswordNote("");
  });
  input.addEventListener("input", () => setPasswordNote(""));
  panel.addEventListener("submit", async (event) => {
    event.preventDefault();
    const password = input.value.trim();
    if (password.length < 6 || password.length > 64) {
      setPasswordNote("Пароль от 6 до 64 символов", true);
      return;
    }
    const save = panel.querySelector("button[type='submit']");
    if (save) save.disabled = true;
    try {
      await readJson(await fetch("/api/auth/password", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ password }),
      }));
      setPasswordNote("пароль обновлён");
    } catch (error) {
      setPasswordNote(error.message || "Пароль от 6 до 64 символов", true);
    } finally {
      if (save) save.disabled = false;
    }
  });
  logoutBtn?.addEventListener("click", () => logout());
}

async function logout() {
  try {
    await fetch("/api/auth/logout", { method: "POST" });
  } catch (error) {}
  state.report = null;
  showGate();
}

function wireAuth() {
  const form = document.getElementById("gate-form");
  const contact = document.getElementById("gate-contact");
  const domain = document.getElementById("gate-domain");
  const at = document.getElementById("gate-at");
  const line = document.getElementById("gate-line");
  const password = document.getElementById("gate-password");
  const errorEl = document.getElementById("gate-error");
  if (!form || !password || !contact || !domain) return;
  const showGateError = (text) => {
    if (!errorEl) return;
    errorEl.textContent = text || "";
    errorEl.hidden = !text;
  };
  contact.addEventListener("input", () => {
    syncGateContact(contact, domain, at, line);
    showGateError("");
  });
  password.addEventListener("input", () => showGateError(""));
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    showGateError("");
    try {
      const data = await readJson(await fetch("/api/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          contact: contact.value,
          domain: domain.hidden ? "" : domain.value,
          password: password.value,
        }),
      }));
      state.user = data;
      password.value = "";
      applySession();
      await openApp();
    } catch (error) {
      showGateError(error.message || "некорректный логин или пароль");
    }
  });
}

async function boot() {
  await openApp();
}

async function openApp() {
  readUrl();
  syncSections();
  syncForm(true);
  setStatus("Загрузка…");
  try {
    const response = await fetch("/api/bootstrap");
    const data = await parseJson(response);
    if (response.status === 401) {
      showGate();
      return;
    }
    if (data.user) state.user = data.user;
    if (state.user) applySession();
    state.today = data.today || "";
    if (!state.anchor) state.anchor = data.max_date || data.today;
    if (!state.rangeStart) state.rangeStart = state.anchor;
    syncForm(true);
    renderSummary(data.summary);
    if (!data.ok) {
      setBanner(data.error || "Нет соединения с базой");
      setStatus("");
      render();
      return;
    }
    writeUrl(false);
    if (freeRangePending()) {
      setStatus("");
      render();
      return;
    }
    await loadReport();
  } catch (error) {
    if (!state.user) showGate();
    setBanner("Не удалось открыть сервис");
    setStatus("");
  }
}

wireChrome();
wireAdmin();
wireAuth();
wirePassword();
boot();

