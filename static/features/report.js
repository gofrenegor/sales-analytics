function beginLoadSpin() {
  if (reducedMotion()) return;
  const node = document.querySelector("#screen .loss-donut-wrap");
  if (!node) return;
  node.classList.add("spin-loading");
}


let loadSeq = 0;
let employeesSeq = 0;
let empDetailSeq = 0;
let rowsSeq = 0;

const activeLoads = new Map();

function cancelLoad(name) {
  activeLoads.get(name)?.abort();
  activeLoads.delete(name);
}

function fetchLatest(name, url) {
  cancelLoad(name);
  const controller = new AbortController();
  activeLoads.set(name, controller);
  return fetch(url, { signal: controller.signal });
}

function resetEmployees() {
  rowsSeq += 1;
  for (const name of ["employees", "items", "rows"]) cancelLoad(name);
  employeesSeq += 1;
  empDetailSeq += 1;
  state.employees = null;
  state.employeesKey = "";
  state.employeesLoading = false;
  state.employeesError = "";
  state.empOpen = "";
  state.empDetail = null;
  state.empCache = {};
  state.empPage = 0;
  state.empRows = false;
  state.empLrMotion = "";
  state.blocks = null;
  state.rowsKey = "";
  state.rowsLoading = false;
  state.rowsError = "";
  state.rowsReady = true;
  state.rowBlock = "";
  state.rowFloor = "";
  state.rowOpen = "";
  state.rowPage = 0;
}

function empKey(lr, badge) {
  return `${lr}|${badge || ""}`;
}

function periodParams() {
  const params = new URLSearchParams({ mode: state.mode });
  if (state.mode === "Свободный") {
    params.set("start", state.rangeStart);
    params.set("end", state.rangeEnd);
  } else {
    params.set("anchor", state.anchor);
  }
  return params;
}

function toggleEmp(lr, badge) {
  const key = empKey(lr, badge);
  if (state.empOpen === key) {
    empDetailSeq += 1;
    cancelLoad("items");
    state.empOpen = "";
    render();
    return;
  }
  loadEmpDetail(lr, badge);
}

async function loadEmpDetail(lr, badge) {
  const key = empKey(lr, badge);
  state.empDetailLimit = 100;
  const seq = ++empDetailSeq;
  state.empOpen = key;
  const hit = state.empCache[key];
  if (hit) {
    state.empDetail = { key, loading: false, error: "", items: hit.items, ready: hit.ready };
    if (state.view === "employees") render();
    return;
  }
  state.empDetail = { key, loading: true, error: "", items: null, ready: true };
  if (state.view === "employees") render();
  try {
    const params = periodParams();
    params.set("lr", lr);
    params.set("badge", badge || "");
    const response = await fetchLatest("items", `/api/employees/items?${params.toString()}`);
    const data = await parseJson(response);
    if (seq !== empDetailSeq) return;
    if (response.status === 401) {
      state.empOpen = "";
      state.empDetail = null;
      showGate();
      return;
    }
    if (!response.ok) throw new Error(data.error || "Ошибка загрузки");
    if (String(lr) === "5") await ensureInfoCatalog();
    if (seq !== empDetailSeq) return;
    const pack = { items: data.items || [], ready: data.ready !== false };
    state.empCache[key] = pack;
    state.empDetail = { key, loading: false, error: "", items: pack.items, ready: pack.ready };
  } catch (error) {
    if (seq !== empDetailSeq) return;
    state.empDetail = { key, loading: false, error: error.message, items: null, ready: true };
  }
  if (state.view === "employees") render();
}

async function loadEmployees() {
  if (freeRangePending()) return;
  if (state.empLr === "5" && state.empRows) return;
  const params = periodParams();
  params.set("lr", state.empLr === "27" ? "27" : "5");
  params.set("page", String(state.empPage));
  params.set("sort", state.empSort);
  params.set("q", state.badgeQuery || "");
  const key = params.toString();
  if (state.employees && state.employeesKey === key) return;
  if (state.employeesLoading && state.employeesKey === key) return;
  const seq = ++employeesSeq;
  state.employeesLoading = true;
  state.employeesError = "";
  state.employees = null;
  state.employeesKey = key;
  if (state.view === "employees") render();
  try {
    const response = await fetchLatest("employees", `/api/employees?${key}`);
    const data = await parseJson(response);
    if (seq !== employeesSeq) return;
    if (response.status === 401) {
      state.employeesLoading = false;
      showGate();
      return;
    }
    if (!response.ok) throw new Error(data.error || "Ошибка загрузки");
    state.employees = data;
    state.empPage = data.page;
    params.set("page", String(data.page));
    state.employeesKey = params.toString();
    state.employeesLoading = false;
  } catch (error) {
    if (seq !== employeesSeq) return;
    state.employeesLoading = false;
    state.employeesError = error.message;
  }
  if (state.view === "employees") render();
}

async function loadProblemRows() {
  if (freeRangePending()) return;
  const key = periodKey();
  if (state.blocks && state.rowsKey === key) return;
  if (state.rowsLoading && state.rowsKey === key) return;
  if (state.rowsError && state.rowsKey === key) return;
  const seq = ++rowsSeq;
  state.rowsLoading = true;
  state.rowsError = "";
  state.blocks = null;
  state.rowsKey = key;
  if (state.view === "employees") render();
  try {
    const response = await fetchLatest("rows", `/api/employees/rows?${periodParams().toString()}`);
    const data = await parseJson(response);
    if (seq !== rowsSeq) return;
    if (response.status === 401) {
      state.rowsLoading = false;
      showGate();
      return;
    }
    if (!response.ok) throw new Error(data.error || "Ошибка загрузки");
    state.blocks = data.blocks || [];
    state.rowsReady = data.ready !== false;
    state.rowsLoading = false;
  } catch (error) {
    if (seq !== rowsSeq) return;
    state.rowsLoading = false;
    state.rowsError = error.message;
  }
  if (state.view === "employees") render();
}

async function loadReport(options = {}) {
  const seq = ++loadSeq;
  const key = periodKey();
  cancelLoad("report");
  resetEmployees();
  if (state.reportKey !== key) {
    state.report = null;
    state.reportKey = "";
    state.reportDetail = "";
  }
  if (state.view === "employees") {
    setStatus("");
    setBanner("");
    loadEmployees();
    return;
  }
  if (options.spin) beginLoadSpin();
  const screen = document.getElementById("screen");
  if (screen) screen.setAttribute("aria-busy", "true");
  setStatus("Обновление…");
  setBanner("");
  try {
    const params = periodParams();
    const detail = options.detail || reportDetailForView();
    params.set("detail", detail);
    const response = await fetchLatest("report", `/api/report?${params.toString()}`);
    const data = await parseJson(response);
    if (seq !== loadSeq) return;
    if (response.status === 401) {
      showGate();
      return;
    }
    if (!response.ok) throw new Error(data.error || "Ошибка загрузки");
    state.report = data;
    state.reportKey = key;
    state.reportDetail = detail;
    render();
    setStatus("");
    if (state.view === "employees") loadEmployees();
  } catch (error) {
    if (seq !== loadSeq) return;
    setBanner(error.message);
    setStatus("");
    render();
  } finally {
    if (seq === loadSeq && screen) screen.removeAttribute("aria-busy");
  }
}

function strong(text) {
  return el("strong", null, text);
}

function renderNotes() {
  const note = document.getElementById("period-note");
  const report = state.report;
  const prevText = note.textContent;
  note.replaceChildren();
  if (freeRangePending() || !report || !report.period) {
    note.hidden = true;
  } else {
    note.hidden = false;
    const item = report.period.note;
    if (item.type === "day") {
      note.append("Выбран день: ", strong(item.current), " · сравнение: ", strong(item.previous));
    } else if (item.type === "free") {
      note.append("Период: ", strong(item.current), ` (${item.days} дн.). Сравнение пока не считаем.`);
    } else {
      note.append(
        "Период: ", strong(item.current), ` (${item.days} дн.) · сравнение с теми же днями: `, strong(item.previous),
      );
    }
  }
  const compare = document.getElementById("compare-note");
  const showCompare = !freeRangePending() && report && !report.empty && !report.has_prev && report.period.compare !== false;
  compare.hidden = !showCompare;
  compare.textContent = showCompare ? "ℹ️ За период сравнения в базе данных нет — динамика не показывается." : "";
  if (prevText && note.textContent !== prevText && !reducedMotion()) {
    note.classList.remove("note-swap");
    void note.offsetWidth;
    note.classList.add("note-swap");
  }
}

function moneyTable(headers, records) {
  const wrap = el("div", { class: "table-wrap" });
  const table = el("table");
  table.append(el("thead", null, el("tr", null, ...headers.map((header, index) => el("th", { class: index ? "num" : "" }, header)))));
  const body = el("tbody");
  for (const record of records) {
    body.append(el("tr", null, ...record.map((cell, index) => el("td", { class: index ? "num" : "" }, cell))));
  }
  table.append(body);
  wrap.append(table);
  return wrap;
}

function renderQuality() {
  const panel = document.getElementById("quality");
  const body = document.getElementById("quality-body");
  const quality = !freeRangePending() && state.report && !state.report.empty ? state.report.quality : null;
  panel.hidden = !quality;
  body.replaceChildren();
  if (!quality) return;
  if (quality.operka_lrs.length) {
    body.append(el("p", { class: "box info" }, "LR внутри «Опер площадь». Если какой-то из них на деле сборка — добавьте его в ASSEMBLY_LR (analytics.py):"));
    body.append(moneyTable(["LR", "Сумма (₽)"], quality.operka_lrs.map((row) => [row.reason, fmtRub(row.sum)])));
  }
  if (quality.all_clear) body.append(el("p", { class: "box ok" }, "Все статусы и блоки распознаны."));
  if (quality.other_rows.length) {
    body.append(el("p", { class: "box warn" }, "Статусы вне потоков — временно исключены из итогов:"));
    body.append(moneyTable(
      ["Статус", "LR", "Сумма (₽)"],
      quality.other_rows.map((row) => [row.status, row.reason, fmtRub(row.sum)]),
    ));
  }
  if (quality.unmapped.length) {
    body.append(el("p", { class: "box warn" }, "Блоки нашего ЛО без маппинга (учтены в группе «Блоки вне маппинга»; топ-15). Добавьте их в BLOCK_MAPPING (analytics.py):"));
    body.append(moneyTable(["Блок", "Сумма (₽)"], quality.unmapped.map((row) => [row.name, fmtRub(row.sum)])));
  }
  if (quality.ignored?.length) {
    body.append(el("p", { class: "box info" }, "Площадки, исключённые из расчётов и отчётов:"));
    body.append(moneyTable(["Площадка", "Сумма (₽)"], quality.ignored.map((row) => [row.name, fmtRub(row.sum)])));
  }
  if (quality.block_6800) {
    const item = quality.block_6800;
    body.append(el(
      "p",
      { class: "box info" },
      `Для сверки: ${item.count} строк своего офиса с ID блока 6800 не попали в оперку (статусы: ${item.statuses.join(", ")}), сумма ${fmtRub(item.sum)}`,
    ));
  }
}

function backButton(label, view) {
  return el("button", { type: "button", class: "back", onclick: () => goBack(view) }, label);
}

function cardTone(card) {
  const prev = Number(card.prev_sum) || 0;
  const loss = Number(card.loss_sum) || 0;
  if (!state.report || !state.report.has_prev || prev <= 0) return "plain";
  if (loss > prev) return "red";
  if (loss < prev) return "green";
  return "plain";
}

function renderCards(cards, shareLabel, onOpen) {
  const grid = el("div", { class: "cards" });
  cards.forEach((card, index) => {
    const chip = card.chip ? el("span", { class: `chip chip-${card.chip.kind}` }, card.chip.text) : null;
    const bar = el("div", { class: "bar" }, el("div", { style: `width:${Math.min(card.pct, 100).toFixed(1)}%` }));
    grid.append(el(
      "article",
      { class: `card ${cardTone(card)}` },
      el("div", { class: "c-name", title: card.name }, card.name),
      el("div", { class: "c-sum" }, fmtRub(card.loss_sum)),
      chip || el("span", { class: "chip chip-slot", "aria-hidden": "true" }, "\u00a0"),
      card.spark ? sparkline(card.spark) : el("span", { class: "spark spark-slot", "aria-hidden": "true" }),
      bar,
      el("div", { class: "c-share" }, shareText(card.pct, shareLabel)),
      el("button", { type: "button", onclick: () => onOpen(card, index) }, "Открыть →"),
    ));
  });
  return grid;
}

function deltaNode(text) {
  if (!text) return null;
  const kind = text.startsWith("+") ? "up" : text.startsWith("-") ? "down" : "flat";
  return el("div", { class: `delta ${kind}` }, text);
}

function tipDayDeltaChip(sum, prevSum) {
  if (prevSum == null) return null;
  const diff = sum - prevSum;
  if (diff === 0) return el("span", { class: "chip chip-n tip-day-chip" }, "0 ₽ •");
  const kind = diff > 0 ? "r" : "g";
  const arrow = diff > 0 ? "▲" : "▼";
  const sign = diff > 0 ? "+" : "-";
  return el("span", { class: `chip chip-${kind} tip-day-chip` }, `${sign}${fmtInt(Math.abs(diff))} ₽ ${arrow}`);
}

const TIP_PERIOD_ORDER = { "Прошлый период": 0, "Текущий период": 1 };

function tipBlockName(value) {
  return String(value ?? "—").replace(/Рязань\s+Тюшевское/gi, "РЗТ");
}

const TIP_FIELDS = [
  ["Дата", (row) => row.date],
  ["Топ-1 LR", (row) => row.top_lr],
  ["Сумма, ₽", (row) => fmtInt(row.sum)],
  ["Штуки, шт", (row) => fmtInt(row.qty)],
  ["Топ-1 блок", (row) => tipBlockName(row.top_block)],
  ["Топ-1 LR в блоке", (row) => row.top_block_lr],
];

function tipCellLine(label, value) {
  const numeric = /₽|шт/.test(label) || label.startsWith("Топ-1 LR");
  return el("div", { class: numeric ? "tip-line tip-num" : "tip-line" }, el("span", null, label), el("b", null, value));
}

function tipDayDeltaSlot(chip) {
  const slot = el("div", { class: "tip-day-delta" });
  slot.append(chip || el("span", { class: "tip-day-delta-spacer", "aria-hidden": "true" }, "0 ₽ •"));
  return slot;
}

function buildTipBlock(row, dayDeltaChip) {
  const block = el("div", { class: "tip-block" }, el("div", { class: "tip-title" }, row.period));
  if (dayDeltaChip) block.append(el("div", { class: "tip-day-delta" }, dayDeltaChip));
  for (const [key, pick] of TIP_FIELDS) block.append(tipCellLine(key, pick(row)));
  return block;
}

function dayTip(rows, day) {
  const list = rows.filter((row) => row.day === day).slice().sort(
    (a, b) => (TIP_PERIOD_ORDER[a.period] ?? 0) - (TIP_PERIOD_ORDER[b.period] ?? 0),
  );
  const current = rows.filter((row) => row.period === "Текущий период");
  const prevDayCur = current.find((row) => row.day === day - 1);
  return buildCompareTip(list, prevDayCur);
}

function buildCompareTip(sortedRows, prevDayCur) {
  const prev = sortedRows.find((row) => row.period === "Прошлый период");
  const cur = sortedRows.find((row) => row.period === "Текущий период");
  if (!prev || !cur) {
    const row = sortedRows[0];
    if (!row) return null;
    const chip = row.period === "Текущий период" && prevDayCur
      ? tipDayDeltaChip(row.sum, prevDayCur.sum)
      : null;
    return buildTipBlock(row, chip);
  }
  const chip = prevDayCur ? tipDayDeltaChip(cur.sum, prevDayCur.sum) : null;
  const grid = el("div", { class: "tip-compare" });
  for (const [row, delta] of [[prev, null], [cur, chip]]) {
    const cell = el("div", { class: "tip-compare-cell tip-compare-head" });
    cell.append(el("div", { class: "tip-title" }, row.period), tipDayDeltaSlot(delta));
    grid.append(cell);
  }
  for (const [label, pick] of TIP_FIELDS) {
    for (const row of [prev, cur]) grid.append(el("div", { class: "tip-compare-cell" }, tipCellLine(label, pick(row))));
  }
  return grid;
}

function lrTable(rows) {
  const wrap = el("div", { class: "table-wrap" });
  const table = el("table");
  table.append(el("thead", null, el("tr", null, ...["Причина (LR)", "Сумма (₽)", "Кол-во (шт)", "Доля (%)"].map((header, index) => (
    el("th", { class: index ? "num" : "" }, header)
  )))));
  const body = el("tbody");
  if (!rows.length) {
    body.append(el("tr", null, el("td", { colspan: "4" }, "Нет списаний")));
  }
  for (const row of rows) {
    const share = el(
      "div",
      { class: "share-line" },
      el("div", { class: "bar" }, el("div", { style: `width:${Math.min(row.pct, 100).toFixed(1)}%` })),
      el("span", { class: "share-num" }, `${Number(row.pct).toFixed(1)}%`),
    );
    body.append(el("tr", null,
      el("td", null, row.reason),
      el("td", { class: "num" }, fmtRub(row.sum)),
      el("td", { class: "num" }, fmtInt(row.qty)),
      el("td", { class: "num" }, share),
    ));
  }
  table.append(body);
  wrap.append(table);
  return wrap;
}

function flowByCode(code) {
  return state.report && state.report.flows && state.report.flows[code];
}

function groupCard(code, name) {
  const flow = flowByCode(code);
  return flow && flow.cards.find((card) => card.name === name);
}

function detailCard(code, group, block) {
  const card = groupCard(code, group);
  if (!card) return null;
  for (const section of card.group_view.sections) {
    const found = section.cards.find((item) => item.name === block);
    if (found) return found;
  }
  return null;
}

function missing(view) {
  return el("section", null, backButton("← Назад", view), el("p", { class: "box warn" }, "Этого разреза нет в выбранном периоде."));
}

const PAIR_GAP_PX = 2;
const PAIR_STROKE_PX = 1.25;

