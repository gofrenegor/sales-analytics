function renderWarehouseWriteoffs(screen) {
  if (state.reportDetail !== "writeoffs" && state.reportDetail !== "full") {
    screen.append(el("p", { class: "hint" }, "Загрузка…"));
    return;
  }
  const report = state.report.warehouse_writeoffs;
  if (!report?.total) {
    screen.append(el("p", { class: "hint" }, "Нет списаний за выбранный период."));
    return;
  }
  const total = report.total;
  const percent = (value) => value == null ? "—" : new Intl.NumberFormat("ru-RU", { minimumFractionDigits: 5, maximumFractionDigits: 5 }).format(value) + "%";
  const period = state.report.period;
  const periodName = state.mode === "Месяц"
    ? new Intl.DateTimeFormat("ru-RU", { month: "long", year: "numeric", timeZone: "UTC" }).format(new Date(`${period.start}T12:00:00Z`))
    : `${period.start} — ${period.end}`;
  const headings = ["Названия строк", ...WRITEOFF_COLUMNS.map(([, title]) => state.mode === "Месяц" ? title.replace("(период)", "(месяц)") : title)];
  const card = el("section", { class: "writeoff-card", "aria-label": "Списания склад" });
  let diagnostics = null;
  screen.append(card);
  const csv = [];
  const collect = (items, path = []) => {
    for (const item of items) {
      const names = [...path, item.name];
      csv.push([names.join(" / "), ...WRITEOFF_COLUMNS.map(([key]) => item[key])]);
      collect(item.children || [], names);
    }
  };
  collect(report.groups);
  csv.push([total.name, ...WRITEOFF_COLUMNS.map(([key]) => total[key])]);
  card.append(el("div", { class: "writeoff-card-head" },
    el("h2", null, "Списания склад"),
    downloadButton(exportName("spisaniya-sklad"), headings, csv),
  ));
  if (total.unknown_qty || total.unknown_sum) {
    screen.append(el("p", { class: "box warn" }, `Флаг оприходования не указан: ${fmtInt(total.unknown_qty)} шт · ${fmtRub(total.unknown_sum)}. Эти строки входят в общий итог, но не в показатели «Оприходовано» и «Без оприходования».`));
  }
  if (report.unmapped?.length) {
    const headers = ["Причина", "Исходный статус", "Код статуса", "LR", "МХ", "Площадка", "Количество, шт", "Сумма, ₽"];
    const rows = report.unmapped.map((item) => {
      const raw = String(item.status_raw || "").trim();
      const statusMissing = !raw || /^(не указано|none|null|nan)$/i.test(raw);
      const reason = statusMissing ? "Не указан статус" : item.status === "НЕ УКАЗАНО" ? "Не распознан код статуса" : "Статуса нет в маппинге";
      const value = (text) => !text || /^(не указано|none|null|nan)$/i.test(String(text)) ? "Не указано" : text;
      return [reason, raw || "Пусто в базе", item.status === "НЕ УКАЗАНО" ? "Нет кода" : item.status,
        value(item.reason), value(item.mx), value(item.platform), item.qty, item.sum];
    });
    const table = moneyTable(headers, rows.map((row) => row.map((value, index) => index >= 6 ? fmtInt(value) : value)));
    table.classList.add("writeoff-diagnostics");
    const panel = el("section", { class: "writeoff-issues", "aria-label": "Строки вне маппинга" },
      el("div", { class: "writeoff-head" }, el("h3", null, "Что не попало в маппинг"), downloadButton(exportName("bez-mappinga"), headers, rows)),
      el("p", { class: "hint" }, "Здесь показаны исходный статус из базы, LR до правил классификации, МХ и площадка. Пустое поле отмечено явно. МХ доступно, если оно было загружено для этой потери."),
      table,
    );
    diagnostics = panel;
  }
  const table = el("table", { class: "writeoff-table" });
  table.append(el("caption", { class: "sr" }, "Детализация складских списаний по новому маппингу"));
  table.append(el("thead", null, el("tr", null, headings.map((title) => el("th", { scope: "col" }, title)))));
  const body = el("tbody");
  const periodRow = el("tr", { class: "writeoff-period-row" }, el("th", { scope: "row" }, state.mode === "Месяц" ? "Месяц" : "Период"),
    el("th", { colspan: String(WRITEOFF_COLUMNS.length), scope: "colgroup" }, periodName));
  table.querySelector("thead").prepend(periodRow);
  const cells = (item, row) => {
    for (const [key] of WRITEOFF_COLUMNS) {
      const text = key.includes("share") ? percent(item[key]) : Number(item[key]) === 0 ? "—" : fmtInt(item[key]);
      const props = key.includes("turnover") ? { title: "Для расчёта нужен оборот за выбранный период" } : {};
      row.append(el("td", props, text));
    }
  };
  let sequence = 0;
  const appendRows = (items, depth = 0, ancestors = []) => {
    for (const item of items) {
      const id = `writeoff-${++sequence}`;
      const children = item.children || [];
      const row = el("tr", { class: `writeoff-level-${depth}` });
      row.hidden = depth > 1;
      const cell = el("th", { scope: "row" });
      cell.style.setProperty("--depth", depth);
      if (children.length) {
        // Временно отключено раскрытие детализации складских списаний.
        const button = el("button", { type: "button", "aria-disabled": "true", "aria-expanded": depth === 0 ? "true" : "false" },
          el("span", { class: "writeoff-chevron", "aria-hidden": "true" }, "+"), item.name);
        button.querySelector(".writeoff-chevron").textContent = depth === 0 ? "−" : "+";
        cell.append(button);
      } else cell.append(el("span", { class: "writeoff-leaf" }, item.name));
      row.dataset.writeoff = id;
      row._ancestors = ancestors;
      row.append(cell);
      cells(item, row);
      body.append(row);
      appendRows(children, depth + 1, [...ancestors, id]);
    }
  };
  const order = ["Исходящий поток", "Входящий поток", "Дефектация", "Внескладское списание", "Релиз продаж"];
  const rank = (item) => { const index = order.indexOf(item.name); return index < 0 ? order.length : index; };
  appendRows([...report.groups].sort((a, b) => rank(a) - rank(b)));
  table.append(body);
  const foot = el("tr", null, el("th", { scope: "row" }, "Общий итог"));
  cells(total, foot);
  table.append(el("tfoot", null, foot));
  card.append(el("div", { class: "writeoff-scroll", tabindex: "0", role: "region", "aria-label": "Таблица списаний, прокрутка по горизонтали" }, table));
  if (diagnostics) screen.append(diagnostics);
}

