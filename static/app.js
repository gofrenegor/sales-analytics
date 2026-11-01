const state = {
  mode: "Месяц",
  anchor: "",
  rangeStart: "",
  rangeEnd: "",
  view: "dashboard",
  flow: "",
  group: "",
  block: "",
  report: null,
};

const MODES = ["День", "Неделя", "Месяц", "Свободный"];
const DEFAULT_YEAR = "2026";

const VIEW_DEPTH = { dashboard: 0, flow: 1, group: 2, block_detail: 3 };
let screenMotion = "";
let animating = 0;

const ANCHOR_LABELS = {
  "День": "Выберите дату",
  "Неделя": "Выберите любой день нужной недели",
  "Месяц": "Выберите любой день нужного месяца",
};

const NS = "http://www.w3.org/2000/svg";

function el(tag, props, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props || {})) {
    if (value == null || value === false) continue;
    if (key === "class") node.className = value;
    else if (key.startsWith("on") && typeof value === "function") node.addEventListener(key.slice(2).toLowerCase(), value);
    else node.setAttribute(key, value === true ? "" : value);
  }
  for (const child of children.flat()) {
    if (child == null || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

function fmtInt(value) {
  const number = Math.round(Number(value) || 0);
  const sign = number < 0 ? "-" : "";
  return sign + Math.abs(number).toString().replace(/\B(?=(\d{3})+(?!\d))/g, " ");
}

function fmtRub(value) {
  return `${fmtInt(value)} ₽`;
}

function fmtDate(iso) {
  if (!iso) return "";
  const [year, month, day] = iso.slice(0, 10).split("-");
  return `${day}.${month}.${year}`;
}

function shareText(pct, label) {
  const text = pct > 0 && pct < 0.1 ? "< 0.1%" : `${Number(pct).toFixed(1)}%`;
  return `${text} ${label}`;
}

function setBanner(text) {
  const banner = document.getElementById("banner");
  banner.hidden = !text;
  banner.textContent = text || "";
}

function setStatus(text) {
  const status = document.getElementById("status");
  status.hidden = !text;
  status.textContent = text || "";
}

function setAdminStatus(text) {
  const node = document.getElementById("admin-status");
  node.hidden = !text;
  node.textContent = text || "";
}

function isoDay(value) {
  return value && /^\d{4}-\d{2}-\d{2}$/.test(value) ? value : "";
}

function readUrl() {
  const params = new URLSearchParams(location.search);
  const mode = params.get("mode");
  if (MODES.includes(mode)) state.mode = mode;
  const anchor = isoDay(params.get("anchor"));
  if (anchor) state.anchor = anchor;
  const rangeStart = isoDay(params.get("start"));
  const rangeEnd = isoDay(params.get("end"));
  if (rangeStart) state.rangeStart = rangeStart;
  if (rangeEnd) state.rangeEnd = rangeEnd;
  const view = params.get("view");
  state.view = ["dashboard", "flow", "group", "block_detail"].includes(view) ? view : "dashboard";
  state.flow = params.get("flow") || "";
  state.group = params.get("group") || "";
  state.block = params.get("block") || "";
  if (!params.get("view") && (state.flow === "in" || state.flow === "out")) state.view = "flow";
  if ((state.view === "flow" || state.view === "group" || state.view === "block_detail") && !["in", "out"].includes(state.flow)) {
    state.view = "dashboard";
  }
  if (state.view === "group" && !state.group) state.view = "flow";
  if (state.view === "block_detail" && (!state.group || !state.block)) state.view = state.group ? "group" : "flow";
}

function writeUrl(push) {
  const params = new URLSearchParams();
  params.set("mode", state.mode);
  if (state.mode === "Свободный") {
    if (state.rangeStart) params.set("start", state.rangeStart);
    if (state.rangeEnd) params.set("end", state.rangeEnd);
  } else if (state.anchor) {
    params.set("anchor", state.anchor);
  }
  if (state.view && state.view !== "dashboard") params.set("view", state.view);
  if (state.flow) params.set("flow", state.flow);
  if (state.group) params.set("group", state.group);
  if (state.block) params.set("block", state.block);
  const url = `/?${params.toString()}`;
  if (push) history.pushState(null, "", url);
  else history.replaceState(null, "", url);
}

function viewKey() {
  return [state.view, state.flow, state.group, state.block].join("\0");
}

function reducedMotion() {
  return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

function go(patch, push = true) {
  const prevDepth = VIEW_DEPTH[state.view] ?? 0;
  const prevKey = viewKey();
  Object.assign(state, patch);
  if (viewKey() !== prevKey) {
    const nextDepth = VIEW_DEPTH[state.view] ?? 0;
    screenMotion = nextDepth < prevDepth ? "back" : "forward";
  }
  writeUrl(push);
  render();
}

function goBack(view) {
  const patch = { view };
  if (view === "dashboard") Object.assign(patch, { flow: "", group: "", block: "" });
  else if (view === "flow") Object.assign(patch, { group: "", block: "" });
  else if (view === "group") patch.block = "";
  go(patch);
}

function periodKey() {
  return state.mode === "Свободный"
    ? `${state.mode}|${state.rangeStart}|${state.rangeEnd}`
    : `${state.mode}|${state.anchor}`;
}

function freeRangePending() {
  return state.mode === "Свободный" && !(state.rangeStart && state.rangeEnd);
}

function digitsOnly(value) {
  return String(value || "").replace(/\D/g, "");
}

function partEl(prefix, part) {
  return document.getElementById(`${prefix}-${part}`);
}

function readParts(prefix) {
  return {
    day: digitsOnly(partEl(prefix, "day").value),
    month: digitsOnly(partEl(prefix, "month").value),
    year: digitsOnly(partEl(prefix, "year").value),
  };
}

function partsUntouched(parts) {
  return !parts.day && !parts.month && (!parts.year || parts.year === DEFAULT_YEAR);
}

function isoFromParts(parts) {
  const day = Number(parts.day);
  const month = Number(parts.month);
  const year = parts.year;
  if (!day || !month || year.length !== 4) return "";
  const y = Number(year);
  if (month < 1 || month > 12 || day < 1 || day > 31) return "";
  const dt = new Date(Date.UTC(y, month - 1, day));
  if (dt.getUTCFullYear() !== y || dt.getUTCMonth() !== month - 1 || dt.getUTCDate() !== day) return "";
  return `${year}-${String(month).padStart(2, "0")}-${String(day).padStart(2, "0")}`;
}

function writeParts(prefix, iso, force) {
  const day = partEl(prefix, "day");
  const month = partEl(prefix, "month");
  const year = partEl(prefix, "year");
  const parts = readParts(prefix);
  const current = isoFromParts(parts);
  if (!force) {
    if (current === (iso || "")) return;
    if ([day, month, year].includes(document.activeElement)) return;
    if (!partsUntouched(parts)) return;
  }
  if (iso && /^\d{4}-\d{2}-\d{2}$/.test(iso)) {
    const [y, m, d] = iso.split("-");
    day.value = d;
    month.value = m;
    year.value = y;
    return;
  }
  day.value = "";
  month.value = "";
  year.value = DEFAULT_YEAR;
}

function committedOrKeep(prefix, previous) {
  const parts = readParts(prefix);
  const iso = isoFromParts(parts);
  if (iso) return iso;
  if (partsUntouched(parts)) return "";
  if (parts.day && parts.month && parts.year.length > 0 && parts.year.length < 4) return previous || "";
  return "";
}

function syncNative(prefix, iso) {
  const native = document.getElementById(prefix);
  if (native.dataset.seed === "1") return;
  native.value = iso || "";
}

function syncForm(force) {
  const radio = document.querySelector(`input[name=mode][value="${state.mode}"]`);
  if (radio) radio.checked = true;
  const free = state.mode === "Свободный";
  const anchorWrap = document.getElementById("anchor-wrap");
  const rangeWrap = document.getElementById("range-wrap");
  const anchor = document.getElementById("anchor");
  anchorWrap.hidden = free;
  rangeWrap.hidden = !free;
  anchor.required = !free;
  anchor.value = state.anchor || "";
  writeParts("range-start", state.rangeStart, force);
  writeParts("range-end", state.rangeEnd, force);
  syncNative("range-start", state.rangeStart);
  syncNative("range-end", state.rangeEnd);
  document.getElementById("anchor-label").textContent = ANCHOR_LABELS[state.mode] || ANCHOR_LABELS["Месяц"];
}

function prepareRangePicker(prefix) {
  const native = document.getElementById(prefix);
  const current = isoFromParts(readParts(prefix));
  if (current) {
    delete native.dataset.seed;
    native.value = current;
    return;
  }
  const parts = readParts(prefix);
  const year = parts.year.length === 4 ? parts.year : DEFAULT_YEAR;
  let month = parts.month;
  if ((!month || Number(month) < 1 || Number(month) > 12) && prefix === "range-end") {
    const start = isoFromParts(readParts("range-start")) || state.rangeStart;
    if (start) month = start.slice(5, 7);
  }
  const monthNum = Number(month);
  const mm = monthNum >= 1 && monthNum <= 12 ? String(monthNum).padStart(2, "0") : "01";
  // Day near the edge makes the Android calendar open on the next month. The 15th keeps «По» on the «С» month.
  native.dataset.seed = "1";
  native.value = `${year}-${mm}-15`;
}

function orderedRange(start, end) {
  if (start && end && end < start) return [end, start];
  return [start, end];
}

function onControlsChange() {
  const mode = document.querySelector("input[name=mode]:checked").value;
  let anchor = document.getElementById("anchor").value;
  if (mode !== "Свободный") {
    if (state.mode === "Свободный" && state.rangeEnd) anchor = state.rangeEnd;
    document.getElementById("anchor-label").textContent = ANCHOR_LABELS[mode];
    if (!anchor || (mode === state.mode && anchor === state.anchor)) {
      if (mode !== state.mode) syncForm(true);
      return;
    }
    state.mode = mode;
    state.anchor = anchor;
    syncForm(true);
    writeUrl(false);
    loadReport({ spin: true });
    return;
  }
  let rangeStart = committedOrKeep("range-start", state.rangeStart);
  let rangeEnd = committedOrKeep("range-end", state.rangeEnd);
  if (state.mode !== "Свободный") {
    const period = state.report && state.report.period;
    if (!rangeStart) {
      if (period && period.start) rangeStart = period.start;
      else rangeStart = state.rangeStart || state.anchor;
    }
    rangeEnd = "";
    state.mode = mode;
    state.rangeStart = rangeStart;
    state.rangeEnd = rangeEnd;
    syncForm(true);
    writeUrl(false);
    setStatus("");
    setBanner("");
    render();
    return;
  }
  const ordered = orderedRange(rangeStart, rangeEnd);
  const swapped = ordered[0] !== rangeStart || ordered[1] !== rangeEnd;
  [rangeStart, rangeEnd] = ordered;
  if (!rangeStart || !rangeEnd) {
    const changed = mode !== state.mode || rangeStart !== state.rangeStart || rangeEnd !== state.rangeEnd;
    state.mode = mode;
    state.rangeStart = rangeStart;
    state.rangeEnd = rangeEnd;
    syncForm(swapped);
    if (!changed) return;
    writeUrl(false);
    setStatus("");
    setBanner("");
    render();
    return;
  }
  const same = mode === state.mode && rangeStart === state.rangeStart && rangeEnd === state.rangeEnd;
  state.mode = mode;
  state.rangeStart = rangeStart;
  state.rangeEnd = rangeEnd;
  syncForm(swapped);
  if (same) return;
  writeUrl(false);
  loadReport({ spin: true });
}

function beginLoadSpin() {
  if (reducedMotion()) return;
  const node = document.querySelector("#screen .loss-donut-wrap");
  if (!node) return;
  node.classList.add("spin-loading");
}

async function waitAnimating() {
  const start = performance.now();
  while (animating > 0 && performance.now() - start < 1400) {
    await new Promise((resolve) => window.setTimeout(resolve, 32));
  }
}

let loadSeq = 0;
async function loadReport(options = {}) {
  const seq = ++loadSeq;
  if (options.spin) beginLoadSpin();
  const screen = document.getElementById("screen");
  if (screen) screen.setAttribute("aria-busy", "true");
  setStatus("Обновление…");
  setBanner("");
  try {
    const params = new URLSearchParams({ mode: state.mode });
    if (state.mode === "Свободный") {
      params.set("start", state.rangeStart);
      params.set("end", state.rangeEnd);
    } else {
      params.set("anchor", state.anchor);
    }
    const response = await fetch(`/api/report?${params.toString()}`);
    const data = await response.json();
    if (seq !== loadSeq) return;
    if (!response.ok) throw new Error(data.error || "Ошибка загрузки");
    await waitAnimating();
    if (seq !== loadSeq) return;
    state.report = data;
    render();
    setStatus("");
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

function renderCards(cards, shareLabel, onOpen) {
  const grid = el("div", { class: "cards" });
  cards.forEach((card, index) => {
    const chip = card.chip ? el("span", { class: `chip chip-${card.chip.kind}` }, card.chip.text) : null;
    const bar = el("div", { class: "bar" }, el("div", { style: `width:${Math.min(card.pct, 100).toFixed(1)}%` }));
    grid.append(el(
      "article",
      { class: `card ${card.color}` },
      el("div", { class: "c-name" }, card.name),
      el("div", { class: "c-sum" }, fmtRub(card.loss_sum)),
      chip,
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

const TIP_FIELDS = [
  ["Дата", (row) => row.date],
  ["Топ-1 LR", (row) => row.top_lr],
  ["Сумма, ₽", (row) => fmtInt(row.sum)],
  ["Штуки, шт", (row) => fmtInt(row.qty)],
  ["Топ-1 блок", (row) => row.top_block],
  ["Топ-1 LR в блоке", (row) => row.top_block_lr],
];

function tipCellLine(label, value) {
  return el("div", { class: "tip-line" }, el("span", null, label), el("b", null, value));
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
  const head = el("div", { class: "tip-compare-row tip-compare-head" });
  for (const [row, delta] of [[prev, null], [cur, chip]]) {
    const cell = el("div", { class: "tip-compare-cell" });
    cell.append(el("div", { class: "tip-title" }, row.period), tipDayDeltaSlot(delta));
    head.append(cell);
  }
  grid.append(head);
  for (const [label, pick] of TIP_FIELDS) {
    const rowEl = el("div", { class: "tip-compare-row" });
    for (const row of [prev, cur]) rowEl.append(el("div", { class: "tip-compare-cell" }, tipCellLine(label, pick(row))));
    grid.append(rowEl);
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
    const share = el("div", { class: "share-line" }, `${Number(row.pct).toFixed(1)}%`, el("div", { class: "bar" }, el("div", { style: `width:${Math.min(row.pct, 100).toFixed(1)}%` })));
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

function bindDonut(host) {
  host.querySelectorAll("[data-flow]").forEach((link) => {
    link.addEventListener("click", (event) => {
      event.preventDefault();
      go({ view: "flow", flow: link.dataset.flow, group: "", block: "" });
    });
  });
}

function seriesColors() {
  const theme = document.documentElement.dataset.theme;
  const light = theme === "light" || (!theme && window.matchMedia("(prefers-color-scheme: light)").matches);
  return light
    ? { current: "#0969da", previous: "#8b95a5", grid: "rgba(15,23,42,0.12)", axis: "#667085" }
    : { current: "#4C9AFF", previous: "#8b95a5", grid: "rgba(128,128,128,0.16)", axis: "#7d8794" };
}

function niceMax(value) {
  if (value <= 0) return 1;
  const pow = 10 ** Math.floor(Math.log10(value));
  const normalized = value / pow;
  const step = normalized <= 1 ? 1 : normalized <= 2 ? 2 : normalized <= 5 ? 5 : 10;
  return step * pow;
}

function svgEl(name, attrs) {
  const node = document.createElementNS(NS, name);
  for (const [key, value] of Object.entries(attrs || {})) node.setAttribute(key, value);
  return node;
}

function renderChart(rows) {
  const colors = seriesColors();
  const width = 860;
  const height = 400;
  const pad = { l: 72, r: 16, t: 16, b: 36 };
  const innerW = width - pad.l - pad.r;
  const innerH = height - pad.t - pad.b;
  const maxDay = Math.max(1, ...rows.map((row) => row.day));
  const maxY = niceMax(Math.max(0, ...rows.map((row) => row.sum)));
  const x = (day) => pad.l + (maxDay === 1 ? innerW / 2 : ((day - 1) / (maxDay - 1)) * innerW);
  const y = (value) => pad.t + innerH - (value / maxY) * innerH;
  const current = rows.filter((row) => row.period === "Текущий период").sort((a, b) => a.day - b.day);
  const previous = rows.filter((row) => row.period === "Прошлый период").sort((a, b) => a.day - b.day);

  const svg = svgEl("svg", { viewBox: `0 0 ${width} ${height}`, class: "chart", role: "img" });
  svg.setAttribute("aria-label", "Динамика списаний по дням");
  for (let i = 0; i <= 4; i += 1) {
    const value = maxY * i / 4;
    const yy = y(value);
    svg.append(svgEl("line", { x1: pad.l, x2: width - pad.r, y1: yy, y2: yy, stroke: colors.grid, "stroke-dasharray": i === 0 ? "0" : "3 4" }));
    const label = svgEl("text", { x: pad.l - 8, y: yy + 4, "text-anchor": "end" });
    label.textContent = fmtInt(value);
    svg.append(label);
  }
  const step = maxDay > 24 ? 3 : maxDay > 16 ? 2 : 1;
  for (let day = 1; day <= maxDay; day += step) {
    const label = svgEl("text", { x: x(day), y: height - 12, "text-anchor": "middle" });
    label.textContent = String(day);
    svg.append(label);
  }

  function points(series) {
    return series.map((row) => ({ x: x(row.day), y: y(row.sum), row }));
  }
  function pathOf(pts) {
    return pts.map((point, index) => `${index ? "L" : "M"}${point.x.toFixed(1)} ${point.y.toFixed(1)}`).join(" ");
  }
  const currentPts = points(current);
  const previousPts = points(previous);
  if (currentPts.length) {
    const area = `${pathOf(currentPts)} L${currentPts[currentPts.length - 1].x.toFixed(1)} ${y(0).toFixed(1)} L${currentPts[0].x.toFixed(1)} ${y(0).toFixed(1)} Z`;
    svg.append(svgEl("path", { d: area, class: "chart-area", fill: colors.current, opacity: "0.13" }));
  }
  function line(pts, color, dash) {
    if (!pts.length) return;
    const attrs = {
      d: pathOf(pts),
      class: dash ? "chart-line prev" : "chart-line cur",
      fill: "none",
      stroke: color,
      "stroke-width": "2.25",
      "stroke-linejoin": "round",
      "stroke-linecap": "round",
    };
    if (dash) attrs["stroke-dasharray"] = dash;
    svg.append(svgEl("path", attrs));
    for (const point of pts) svg.append(svgEl("circle", { cx: point.x, cy: point.y, r: "3.6", fill: color }));
  }
  line(previousPts, colors.previous, "5 4");
  line(currentPts, colors.current, "");

  const byDay = new Map();
  for (const row of rows) {
    if (!byDay.has(row.day)) byDay.set(row.day, []);
    byDay.get(row.day).push(row);
  }
  const hit = svgEl("rect", { x: pad.l, y: pad.t, width: innerW, height: innerH, fill: "transparent" });
  hit.style.cursor = "crosshair";
  svg.append(hit);

  const tip = el("div", { class: "chart-tip" });
  tip.hidden = true;
  const wrap = el("div", { class: "chart-wrap" });
  wrap.append(svg, tip);

  function showTip(event) {
    const point = svg.createSVGPoint();
    point.x = event.clientX;
    point.y = event.clientY;
    const local = point.matrixTransform(svg.getScreenCTM().inverse());
    let best = 1;
    let bestDist = Infinity;
    for (let day = 1; day <= maxDay; day += 1) {
      const dist = Math.abs(x(day) - local.x);
      if (dist < bestDist) {
        best = day;
        bestDist = dist;
      }
    }
    const list = (byDay.get(best) || []).slice().sort(
      (a, b) => (TIP_PERIOD_ORDER[a.period] ?? 0) - (TIP_PERIOD_ORDER[b.period] ?? 0),
    );
    const prevDayCur = current.find((row) => row.day === best - 1);
    tip.replaceChildren();
    const body = buildCompareTip(list, prevDayCur);
    if (body) tip.append(body);
    const wasHidden = tip.hidden;
    tip.hidden = false;
    if (wasHidden && !reducedMotion()) {
      tip.classList.remove("tip-in");
      void tip.offsetWidth;
      tip.classList.add("tip-in");
    }
    const host = wrap.getBoundingClientRect();
    const anchor = svg.createSVGPoint();
    anchor.x = x(best);
    anchor.y = y((list[0] && list[0].sum) || 0);
    const screen = anchor.matrixTransform(svg.getScreenCTM());
    let left = screen.x - host.left + 14;
    let top = screen.y - host.top + 14;
    tip.style.left = "0px";
    tip.style.top = "0px";
    if (left + tip.offsetWidth > host.width) left = Math.max(8, left - tip.offsetWidth - 28);
    if (top + tip.offsetHeight > host.height) top = Math.max(8, top - tip.offsetHeight - 28);
    tip.style.left = `${left}px`;
    tip.style.top = `${top}px`;
  }
  hit.addEventListener("pointermove", showTip);
  hit.addEventListener("pointerleave", () => { tip.hidden = true; });

  const legend = el("div", { class: "legend" }, el("span", { class: "lg cur" }, "Текущий период"));
  if (previous.length) legend.append(el("span", { class: "lg prev" }, "Прошлый период"));
  return el("div", null, wrap, legend);
}

function renderDashboard(screen) {
  const dash = state.report.dashboard;
  const hero = el("div", { class: "hero" });
  const chart = el("div");
  chart.innerHTML = dash.donut_svg;
  bindDonut(chart);
  const side = el("div");
  for (const [code, title, amount] of [
    ["in", "входящий поток", dash.in_sum],
    ["out", "исходящий поток", dash.out_sum],
  ]) {
    const help = code === "in" ? "Открыть блоки входящего потока" : "Открыть блоки исходящего потока";
    side.append(el(
      "button",
      {
        type: "button",
        class: `flow-btn ${code}`,
        title: help,
        onclick: () => go({ view: "flow", flow: code, group: "", block: "" }),
      },
      el("span", null, title),
      el("strong", null, fmtRub(amount)),
    ));
  }
  side.className = "hero-side";
  side.append(el("p", { class: "hint" }, "Клик по сегменту кольца или строке потока откроет выбор блоков."));
  const donutCol = el("div", { class: "loss-col" });
  donutCol.append(chart);
  hero.append(donutCol, side);
  if (state.report.show_daily) {
    hero.append(el(
      "div",
      { class: "daily-chart" },
      el("h3", { class: "section-title" }, "Динамика по дням"),
      renderChart(state.report.daily),
      el("p", { class: "hint" }, "По горизонтали — номер дня в периоде. Наведите курсор на точку: топ-1 LR за день, сумма, штуки, топ-1 блок и топ-1 LR внутри этого блока."),
    ));
  }
  screen.append(hero);
  if (dash.delta_text) screen.append(el("p", { class: "delta-line" }, `К прошлому периоду · ${dash.delta_text}`));
  if (dash.other && dash.other.show) {
    const box = el("div", { class: "aside-metric" });
    const label = el("div", { class: "aside-label" }, "Вне потоков ", el("span", null, "· не входит в диаграмму"));
    box.append(label, el("div", { class: "aside-value" }, fmtRub(dash.other.sum)));
    if (dash.other.delta_text) box.append(el("div", { class: "aside-delta" }, dash.other.delta_text));
    screen.append(box);
  }
  if (dash.other && dash.other.has_rows) {
    screen.append(el("p", { class: "hint" }, "Статусы вне потоков временно исключены из диаграммы и итогов — список в «Проверке данных» выше."));
  }
}

function renderFlow(screen) {
  const flow = flowByCode(state.flow);
  if (!flow) {
    screen.append(missing("dashboard"));
    return;
  }
  screen.append(
    backButton("← Назад на главную", "dashboard"),
    el("h2", null, `Выбран: ${flow.title}`),
    el("div", { class: "sub" }, "Общая сумма списаний по потоку:"),
    el("div", { class: "big-sum" }, fmtRub(flow.sum)),
    el("hr"),
    el("h3", null, "Выберите блок для детализации:"),
    renderCards(flow.cards, "от потока", (card) => go({ view: "group", group: card.name, block: "" })),
  );
}

function renderGroup(screen) {
  const card = groupCard(state.flow, state.group);
  const flow = flowByCode(state.flow);
  if (!card || !flow) {
    screen.append(missing("flow"));
    return;
  }
  const view = card.group_view;
  screen.append(
    backButton("← Назад к потокам", "flow"),
    el("h2", null, `Блок: ${card.name} (${flow.title})`),
    el("div", { class: "sub" }, "Общая сумма списаний по блоку:"),
    el("div", { class: "big-sum" }, fmtRub(view.sum)),
    el("hr"),
    el("h3", null, view.prompt),
  );
  for (const section of view.sections) {
    if (section.title) screen.append(el("h4", null, section.title));
    if (section.cards.length) {
      screen.append(renderCards(section.cards, "от блока", (item) => go({
        view: "block_detail",
        block: item.name,
      })));
    }
  }
}

function renderDetail(screen) {
  const card = detailCard(state.flow, state.group, state.block);
  if (!card) {
    screen.append(missing("group"));
    return;
  }
  const detail = card.detail;
  screen.append(
    backButton("← Назад к площадкам", "group"),
    el("h2", null, detail.title),
    el("div", { class: "metric-label" }, "Сумма списаний по площадке"),
    el("div", { class: "metric-value" }, fmtRub(detail.sum)),
  );
  const delta = deltaNode(detail.delta_text);
  if (delta) screen.append(delta);
  screen.append(el("hr"));
  if (detail.excluded_sum != null) {
    screen.append(el("p", { class: "hint" }, `ℹ️ Не включено: ${fmtRub(detail.excluded_sum)} со статусами вне потоков (какие именно — в «Проверке данных» вверху страницы).`));
  }
  screen.append(el("h3", null, "Детализация по причинам (LR)"), lrTable(detail.rows));
}

function renderScreen() {
  const pane = el("div", { class: "screen-pane" });
  fillScreen(pane);
  presentScreen(pane);
}

function fillScreen(screen) {
  const report = state.report;
  if (freeRangePending()) {
    screen.append(el("p", { class: "hint" }, "Выберите дату «По». Пока она пустая, данные не грузим."));
    return;
  }
  if (!report) {
    screen.append(el("p", { class: "hint" }, "Загрузка…"));
    return;
  }
  if (report.empty) {
    screen.append(el("p", { class: "box warn" }, "⚠️ Нет данных за этот период."));
    return;
  }
  if (state.view === "flow") renderFlow(screen);
  else if (state.view === "group") renderGroup(screen);
  else if (state.view === "block_detail") renderDetail(screen);
  else renderDashboard(screen);
}

function presentScreen(pane) {
  const host = document.getElementById("screen");
  const motion = screenMotion;
  screenMotion = "";
  const stale = [...host.querySelectorAll(":scope > .screen-pane")];
  const outgoing = stale.pop() || null;
  stale.forEach((node) => node.remove());
  const reduced = reducedMotion();
  const kind = outgoing && !reduced ? motion : "";
  if (!kind) {
    if (motion === "settle" && !reduced) {
      pane.classList.add("reveal");
      pane.querySelectorAll(".chart-wrap").forEach((node) => node.classList.add("draw-once"));
    }
    if (!reduced && !motion && !host.dataset.arrived && state.report) {
      pane.classList.add("arrive");
      host.dataset.arrived = "1";
    }
    host.replaceChildren(pane);
    host.style.minHeight = "";
    host.classList.remove("moving");
    return;
  }
  const outClass = kind === "forward" ? "leave-left" : kind === "back" ? "leave-right" : "leave-fade";
  const inClass = kind === "forward" ? "enter-right" : kind === "back" ? "enter-left" : "reveal";
  const inName = kind === "forward" ? "pane-enter-right" : kind === "back" ? "pane-enter-left" : "pane-reveal";
  outgoing.classList.remove("enter-right", "enter-left", "reveal", "leave-left", "leave-right", "leave-fade");
  outgoing.classList.add(outClass);
  pane.classList.add(inClass);
  host.classList.add("moving");
  if (kind === "settle") pane.querySelectorAll(".chart-wrap").forEach((node) => node.classList.add("draw-once"));
  host.append(pane);
  host.style.minHeight = `${Math.max(outgoing.offsetHeight, pane.offsetHeight)}px`;
  animating += 1;
  let closed = false;
  const close = () => {
    if (closed) return;
    closed = true;
    animating = Math.max(0, animating - 1);
    if (!pane.isConnected || host.lastElementChild !== pane) return;
    pane.classList.remove(inClass);
    host.replaceChildren(pane);
    host.style.minHeight = "";
    host.classList.remove("moving");
  };
  pane.addEventListener("animationend", (event) => {
    if (event.target === pane && event.animationName === inName) close();
  });
  window.setTimeout(close, kind === "settle" ? 480 : 560);
  if (kind !== "settle" && host.getBoundingClientRect().top < 8) {
    host.scrollIntoView({ behavior: "smooth", block: "start" });
  }
}

function render() {
  renderNotes();
  renderQuality();
  renderScreen();
}

function renderSummary(summary) {
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

async function readJson(response) {
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || "Ошибка");
  return data;
}

function wireAdmin() {
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
    preview.append(el("p", { class: "hint" }, "Читаем файлы…"));
    const body = new FormData();
    for (const file of files) body.append("files", file);
    try {
      const data = await readJson(await fetch("/api/admin/preview", { method: "POST", body }));
      preview.replaceChildren();
      if (!data.rows) {
        preview.append(el("p", { class: "box warn" }, "Нет строк с датой — нечего загружать."));
        if (data.skipped_no_date) preview.append(el("p", { class: "box warn" }, `Пропущено строк без даты: ${data.skipped_no_date}`));
        return;
      }
      preview.append(el("p", { class: "box ok" }, `Прочитано файлов: ${data.files}, строк к загрузке: ${data.rows}`));
      preview.append(el("p", { class: "box info" }, `Период: ${fmtDate(data.start)} — ${fmtDate(data.end)}`));
      if (data.skipped_no_date) preview.append(el("p", { class: "box warn" }, `Пропущено строк без даты: ${data.skipped_no_date}`));
      if (data.duplicates) preview.append(el("p", { class: "box warn" }, `Удалено дублей по ШК: ${data.duplicates}`));
      preview.append(el("p", { class: "hint" }, "⚠️ Загрузка заменит ВСЕ данные в этом периоде. Файл должен покрывать период полностью."));
      const button = el("button", { type: "button", class: "wide primary" }, "💾 ВЫГРУЗИТЬ В БАЗУ");
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
          button.textContent = "💾 ВЫГРУЗИТЬ В БАЗУ";
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

function setAdminOpen(open) {
  const sidebar = document.getElementById("sidebar");
  const backdrop = document.getElementById("backdrop");
  const toggle = document.getElementById("admin-toggle");
  sidebar?.classList.toggle("open", open);
  backdrop?.classList.toggle("show", open);
  toggle?.setAttribute("aria-expanded", open ? "true" : "false");
}

function wireAdminPanel() {
  const sidebar = document.getElementById("sidebar");
  const backdrop = document.getElementById("backdrop");
  if (!sidebar || !backdrop) return;
  const toggle = document.getElementById("admin-toggle");
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
    sidebar.classList.remove("open");
    backdrop.classList.remove("show");
  });
}

function wireDigits(input, size, next, replaceFull) {
  input.addEventListener("focus", () => input.select());
  input.addEventListener("keydown", (event) => {
    if (event.key === "Backspace" && !input.value && input.dataset.prev) {
      const prev = document.getElementById(input.dataset.prev);
      if (prev) prev.focus();
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
    }
  });
  input.addEventListener("input", () => {
    const clean = digitsOnly(input.value).slice(0, size);
    if (input.value !== clean) input.value = clean;
    if (clean.length === size && next && document.activeElement === input) next.focus();
  });
}

function pasteDate(event, prefix) {
  const text = event.clipboardData ? event.clipboardData.getData("text") : "";
  const match = String(text || "").trim().match(/^(\d{1,2})[.\-/](\d{1,2})[.\-/](\d{4})$/);
  if (!match) return;
  event.preventDefault();
  writeParts(prefix, `${match[3]}-${match[2].padStart(2, "0")}-${match[1].padStart(2, "0")}`, true);
  onControlsChange();
}

function bindRangeField(prefix) {
  const day = partEl(prefix, "day");
  const month = partEl(prefix, "month");
  const year = partEl(prefix, "year");
  month.dataset.prev = `${prefix}-day`;
  year.dataset.prev = `${prefix}-month`;
  wireDigits(day, 2, month, false);
  wireDigits(month, 2, null, false);
  wireDigits(year, 4, null, true);
  const padOnBlur = (input) => {
    input.addEventListener("blur", () => {
      const n = digitsOnly(input.value);
      if (n) input.value = n.padStart(2, "0");
    });
  };
  padOnBlur(day);
  padOnBlur(month);
  year.addEventListener("blur", () => {
    if (!digitsOnly(year.value)) year.value = DEFAULT_YEAR;
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

function wireChrome() {
  wireAdminPanel();
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
  for (const prefix of ["range-start", "range-end"]) bindRangeField(prefix);
  document.getElementById("dates").addEventListener("change", onControlsChange);
  window.addEventListener("popstate", () => {
    const prevDepth = VIEW_DEPTH[state.view] ?? 0;
    const prevKey = viewKey();
    const period = periodKey();
    readUrl();
    syncForm(true);
    if (viewKey() !== prevKey) {
      const nextDepth = VIEW_DEPTH[state.view] ?? 0;
      screenMotion = nextDepth < prevDepth ? "back" : nextDepth > prevDepth ? "forward" : "back";
    }
    if (period !== periodKey()) {
      if (freeRangePending()) {
        setStatus("");
        setBanner("");
        render();
      } else loadReport({ spin: true });
    } else render();
  });
}

async function boot() {
  readUrl();
  syncForm(true);
  setStatus("Загрузка…");
  try {
    const response = await fetch("/api/bootstrap");
    const data = await response.json();
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
    setBanner("Не удалось открыть сервис");
    setStatus("");
  }
}

wireChrome();
wireAdmin();
boot();
