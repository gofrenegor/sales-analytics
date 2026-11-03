const state = {
  mode: "Месяц",
  anchor: "",
  rangeStart: "",
  rangeEnd: "",
  today: "",
  boundYears: [],
  view: "dashboard",
  flow: "",
  group: "",
  block: "",
  report: null,
  employees: null,
  employeesKey: "",
  employeesLoading: false,
  employeesError: "",
  badgeDraft: "",
  badgeQuery: "",
  empLr: "5",
  empSort: "qty",
  empSortOpen: false,
  empPage: 0,
  empOpen: "",
  empDetail: null,
  empCache: {},
  user: null,
};

const MODES = ["День", "Неделя", "Месяц", "Свободный"];
const DEFAULT_YEAR = "2026";
const VIEWS = ["dashboard", "flow", "group", "block_detail", "employees"];

const VIEW_DEPTH = { dashboard: 0, employees: 0, flow: 1, group: 2, block_detail: 3 };
let screenMotion = "";
let animating = 0;

const ANCHOR_LABELS = {
  "День": "Выберите день",
  "Неделя": "Выберите неделю",
  "Месяц": "Выберите месяц",
};
const MONTHS = ["январь", "февраль", "март", "апрель", "май", "июнь", "июль", "август", "сентябрь", "октябрь", "ноябрь", "декабрь"];
const WEEKDAYS = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"];
let openPickerKind = "";

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

function lossLabel(count) {
  const n = Math.abs(Number(count) || 0) % 100;
  const n1 = n % 10;
  if (n > 10 && n < 20) return "потерь";
  if (n1 === 1) return "потеря";
  if (n1 >= 2 && n1 <= 4) return "потери";
  return "потерь";
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

function pctLabel(pct) {
  const value = Number(pct) || 0;
  if (value > 0 && value < 0.1) return "< 0.1%";
  return `${value.toFixed(1)}%`;
}

function csvCell(value) {
  const text = value == null ? "" : String(value);
  if (/[;"\n]/.test(text)) return `"${text.replace(/"/g, '""')}"`;
  return text;
}

function downloadCsv(filename, header, rows) {
  const lines = [header, ...rows].map((row) => row.map(csvCell).join(";"));
  const blob = new Blob([`\uFEFF${lines.join("\r\n")}`], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.click();
  URL.revokeObjectURL(url);
}

function exportName(suffix) {
  const start = (state.report && state.report.period && state.report.period.start) || "period";
  return `spisaniya-${suffix}-${start}.csv`;
}

function downloadButton(filename, header, rows) {
  return el("button", {
    type: "button",
    class: "quiet",
    onclick: () => downloadCsv(filename, header, rows),
  }, "Скачать");
}

function sparkline(values) {
  if (!values || values.length < 2) return null;
  const width = 120;
  const height = 28;
  const padX = 0.6;
  const padY = 2;
  const max = Math.max(...values, 0);
  const span = max || 1;
  const step = (width - padX * 2) / (values.length - 1);
  const pts = values.map((value, index) => {
    const x = padX + index * step;
    const y = padY + (height - padY * 2) * (1 - (Number(value) || 0) / span);
    return [x, y];
  });
  const line = pts.map((point, index) => `${index ? "L" : "M"}${point[0].toFixed(1)} ${point[1].toFixed(1)}`).join(" ");
  const area = `${line} L${pts[pts.length - 1][0].toFixed(1)} ${(height - padY).toFixed(1)} L${pts[0][0].toFixed(1)} ${(height - padY).toFixed(1)} Z`;
  const svg = svgEl("svg", {
    viewBox: `0 0 ${width} ${height}`,
    preserveAspectRatio: "none",
    class: "spark",
    "aria-hidden": "true",
  });
  svg.append(svgEl("path", { d: area, class: "spark-area" }));
  svg.append(svgEl("path", { d: line, class: "spark-line", fill: "none" }));
  return svg;
}

function moverCard(item, kind) {
  const title = kind === "up" ? "Взлёт" : "Падение";
  if (!item || !item.reason) {
    return el("div", { class: `mover ${kind} idle` }, el("span", { class: "mover-kicker" }, title), el("span", { class: "mover-where" }, kind === "up" ? "Нет взлёта" : "Нет падения"));
  }
  const money = kind === "up" ? `+${fmtRub(item.diff)}` : fmtRub(item.diff);
  const flowTitle = item.flow === "in" ? "входящий" : item.flow === "out" ? "исходящий" : "";
  const flowClass = item.flow === "in" || item.flow === "out" ? ` flow-${item.flow}` : "";
  const where = [item.block, flowTitle].filter(Boolean).join(" · ");
  return el(
    "button",
    {
      type: "button",
      class: `mover ${kind}${flowClass}`,
      onclick: () => {
        if (!item.flow || !item.block) return;
        go({ view: "group", flow: item.flow, group: item.block, block: "" });
      },
    },
    el("span", { class: "mover-kicker" }, title),
    el(
      "span",
      { class: "mover-line" },
      el("span", { class: "mover-lr" }, `LR ${item.reason}`),
      el("span", { class: "mover-delta" }, money),
    ),
    where ? el("span", { class: "mover-where" }, where) : null,
  );
}

function blockMoves() {
  const report = state.report;
  if (!report || !report.has_prev || !report.flows) return { up: [], down: [] };
  const items = [];
  for (const code of ["in", "out"]) {
    const flow = report.flows[code];
    if (!flow) continue;
    for (const card of flow.cards || []) {
      const sum = Number(card.loss_sum) || 0;
      const prev = Number(card.prev_sum) || 0;
      const diff = sum - prev;
      if (!diff) continue;
      items.push({
        name: card.name,
        flow: code,
        flowTitle: code === "in" ? "входящий" : "исходящий",
        diff,
      });
    }
  }
  return {
    up: items.filter((item) => item.diff > 0).sort((a, b) => b.diff - a.diff).slice(0, 5),
    down: items.filter((item) => item.diff < 0).sort((a, b) => a.diff - b.diff).slice(0, 5),
  };
}

function moverBlockRow(item, kind, index) {
  const money = kind === "up" ? `+${fmtRub(item.diff)}` : fmtRub(item.diff);
  return el(
    "button",
    {
      type: "button",
      class: `mover-block${item.flow === "in" || item.flow === "out" ? ` flow-${item.flow}` : ""}`,
      onclick: () => go({ view: "group", flow: item.flow, group: item.name, block: "" }),
    },
    el("span", { class: "mover-block-rank" }, String(index + 1)),
    el("span", { class: "mover-block-name" }, item.name),
    el("span", { class: "mover-block-meta" }, item.flowTitle),
    el("span", { class: "mover-block-delta" }, money),
  );
}

function moverColumn(item, kind, blocks) {
  const col = el("div", { class: `mover-col ${kind}` });
  col.append(moverCard(item, kind));
  if (!blocks.length) return col;
  const fold = el("div", { class: "mover-fold", inert: "" });
  const inner = el("div", { class: "mover-fold-inner" });
  blocks.forEach((block, index) => inner.append(moverBlockRow(block, kind, index)));
  fold.append(inner);
  col.append(fold);
  return col;
}

function bindMoverCols(panel) {
  const fine = window.matchMedia("(hover: hover) and (pointer: fine)").matches;
  panel.querySelectorAll(".mover-col").forEach((col) => {
    const fold = col.querySelector(".mover-fold");
    if (!fold) return;
    const setOpen = (open) => {
      col.classList.toggle("is-open", open);
      col.setAttribute("aria-expanded", open ? "true" : "false");
      if (open) fold.removeAttribute("inert");
      else fold.setAttribute("inert", "");
    };
    if (!fine) {
      setOpen(true);
      return;
    }
    setOpen(false);
    col.addEventListener("pointerenter", () => setOpen(true));
    col.addEventListener("pointerleave", () => {
      const active = document.activeElement;
      if (active && col.contains(active)) return;
      setOpen(false);
    });
    col.addEventListener("focusin", () => setOpen(true));
    col.addEventListener("focusout", (event) => {
      if (!col.contains(event.relatedTarget)) setOpen(false);
    });
  });
}

function renderMovers() {
  const panel = el("section", { class: "movers", "aria-label": "Взлёты и падения" });
  if (!state.report || !state.report.has_prev) {
    panel.append(el("p", { class: "mover-empty" }, "Для этого разреза нет прошлого периода."));
    return panel;
  }
  const moves = state.report.moves || {};
  const blocks = blockMoves();
  panel.append(moverColumn(moves.up, "up", blocks.up), moverColumn(moves.down, "down", blocks.down));
  bindMoverCols(panel);
  return panel;
}

function queryBare(query) {
  return String(query || "").trim().toLowerCase().replace(/^lr\s+/, "");
}

let searchIndexReport = null;
let searchIndexRows = null;

function searchIndex() {
  const report = state.report;
  if (searchIndexReport === report && searchIndexRows) return searchIndexRows;
  const rows = [];
  if (report && !report.empty && report.flows) {
    for (const code of ["in", "out"]) {
      const flow = report.flows[code];
      if (!flow) continue;
      for (const card of flow.cards || []) {
        rows.push({
          kind: "Блок",
          name: String(card.name).toLowerCase(),
          title: card.name,
          meta: flow.title,
          sum: card.loss_sum,
          go: { view: "group", flow: code, group: card.name, block: "" },
        });
        for (const section of (card.group_view && card.group_view.sections) || []) {
          for (const item of section.cards || []) {
            rows.push({
              kind: "Площадка",
              name: String(item.name).toLowerCase(),
              title: item.name,
              meta: `${flow.title} · ${card.name}`,
              sum: item.loss_sum,
              go: { view: "block_detail", flow: code, group: card.name, block: item.name },
            });
            for (const row of (item.detail && item.detail.rows) || []) {
              rows.push({
                kind: "LR",
                lr: true,
                name: String(row.reason).toLowerCase(),
                title: `LR ${row.reason}`,
                meta: `${flow.title} · ${card.name} · ${item.name}`,
                sum: row.sum,
                go: { view: "block_detail", flow: code, group: card.name, block: item.name },
              });
            }
          }
        }
      }
    }
  }
  searchIndexReport = report;
  searchIndexRows = rows;
  return rows;
}

function searchHits(query) {
  const bare = queryBare(query);
  if (!bare || !state.report || state.report.empty || !state.report.flows) return [];
  const numeric = /^\d+$/.test(bare);
  const hits = [];
  for (const row of searchIndex()) {
    if (row.lr) {
      if (!(numeric ? row.name === bare : row.name.includes(bare))) continue;
    } else if (numeric || !row.name.includes(bare)) {
      continue;
    }
    hits.push(row);
  }
  hits.sort((a, b) => (Number(b.sum) || 0) - (Number(a.sum) || 0));
  return hits.slice(0, 12);
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
  state.view = VIEWS.includes(view) ? view : "dashboard";
  state.flow = params.get("flow") || "";
  state.group = params.get("group") || "";
  state.block = params.get("block") || "";
  if (state.view === "employees") {
    state.flow = "";
    state.group = "";
    state.block = "";
  }
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
  if (state.view === "employees") {
    params.set("view", "employees");
  } else {
    if (state.view && state.view !== "dashboard") params.set("view", state.view);
    if (state.flow) params.set("flow", state.flow);
    if (state.group) params.set("group", state.group);
    if (state.block) params.set("block", state.block);
  }
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
  if (state.view === "employees") {
    state.flow = "";
    state.group = "";
    state.block = "";
  }
  if (viewKey() !== prevKey) {
    const nextDepth = VIEW_DEPTH[state.view] ?? 0;
    if (nextDepth < prevDepth) screenMotion = "back";
    else if (nextDepth > prevDepth) screenMotion = "forward";
    else screenMotion = "";
  }
  writeUrl(push);
  render();
  if (state.view === "employees") loadEmployees();
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

function dateKind(parts) {
  if (!parts.day && !parts.month) return "empty";
  if (parts.day.length === 2 && (+parts.day < 1 || +parts.day > 31)) return "invalid";
  if (parts.month.length === 2 && (+parts.month < 1 || +parts.month > 12)) return "invalid";
  if (parts.day.length < 2 || parts.month.length < 2 || parts.year.length !== 4) return "partial";
  if (!isoFromParts(parts)) return "invalid";
  return "ok";
}

function setDateError(prefix, on) {
  const box = partEl(prefix, "day").closest(".ymd");
  const error = document.getElementById(`${prefix}-error`);
  if (box) box.classList.toggle("bad", !!on);
  if (error) error.hidden = !on;
}

function assignPart(input, value) {
  if (input.value !== value) input.value = value;
}

function writeParts(prefix, iso, force) {
  const day = partEl(prefix, "day");
  const month = partEl(prefix, "month");
  const year = partEl(prefix, "year");
  const parts = readParts(prefix);
  const current = isoFromParts(parts);
  if (!force) {
    if (current === (iso || "")) {
      if (dateKind(parts) !== "invalid") setDateError(prefix, false);
      return;
    }
    if ([day, month, year].includes(document.activeElement)) return;
    if (!partsUntouched(parts)) return;
  }
  if (iso && /^\d{4}-\d{2}-\d{2}$/.test(iso)) {
    const [y, m, d] = iso.split("-");
    assignPart(day, d);
    assignPart(month, m);
    assignPart(year, y);
    setDateError(prefix, false);
    return;
  }
  assignPart(day, "");
  assignPart(month, "");
  assignPart(year, DEFAULT_YEAR);
  setDateError(prefix, false);
}

function storedIso(prefix) {
  if (prefix === "anchor") return state.anchor;
  if (prefix === "range-start") return state.rangeStart;
  return state.rangeEnd;
}

function refreshDate(prefix) {
  const kind = dateKind(readParts(prefix));
  setDateError(prefix, kind === "invalid");
  if (kind !== "ok") return;
  const iso = isoFromParts(readParts(prefix));
  const native = document.getElementById(prefix);
  delete native.dataset.seed;
  if (native.value === iso && storedIso(prefix) === iso) return;
  native.value = iso;
  onControlsChange();
}

function committedOrKeep(prefix, previous) {
  const parts = readParts(prefix);
  if (dateKind(parts) === "invalid") return previous || "";
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
  anchorWrap.hidden = true;
  rangeWrap.hidden = !free;
  writeParts("anchor", state.anchor, force);
  writeParts("range-start", state.rangeStart, force);
  writeParts("range-end", state.rangeEnd, force);
  syncNative("anchor", state.anchor);
  syncNative("range-start", state.rangeStart);
  syncNative("range-end", state.rangeEnd);
  const label = ANCHOR_LABELS[state.mode];
  if (label) document.getElementById("anchor-label").textContent = label;
  syncPickers();
}

function partsOf(iso) {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso || "");
  if (!match) return null;
  return { year: Number(match[1]), month: Number(match[2]), day: Number(match[3]) };
}

function isoParts(year, month, day) {
  return `${year}-${String(month).padStart(2, "0")}-${String(day).padStart(2, "0")}`;
}

function monthLength(year, month) {
  return new Date(Date.UTC(year, month, 0)).getUTCDate();
}

function isoClamped(year, month, day) {
  const last = monthLength(year, month);
  return isoParts(year, month, Math.min(Math.max(day, 1), last));
}

function utcDate(iso) {
  const { year, month, day } = partsOf(iso);
  return new Date(Date.UTC(year, month - 1, day));
}

function shiftDays(iso, days) {
  const dt = utcDate(iso);
  dt.setUTCDate(dt.getUTCDate() + days);
  return isoParts(dt.getUTCFullYear(), dt.getUTCMonth() + 1, dt.getUTCDate());
}

function mondayOf(iso) {
  return shiftDays(iso, -((utcDate(iso).getUTCDay() + 6) % 7));
}

function monthTitle(month) {
  const name = MONTHS[month - 1] || "";
  return name ? name.charAt(0).toUpperCase() + name.slice(1) : "";
}

function ddmm(iso, withYear) {
  const { year, month, day } = partsOf(iso);
  const core = `${String(day).padStart(2, "0")}.${String(month).padStart(2, "0")}`;
  return withYear ? `${core}.${year}` : core;
}

function weeksOf(year, month) {
  const first = isoParts(year, month, 1);
  const last = isoParts(year, month, monthLength(year, month));
  const weeks = [];
  let monday = mondayOf(first);
  while (monday <= last) {
    const sunday = shiftDays(monday, 6);
    weeks.push({ monday, sunday, anchor: monday < first ? first : monday });
    monday = shiftDays(monday, 7);
  }
  return weeks;
}

function weekIndex(iso) {
  const parts = partsOf(iso);
  const monday = mondayOf(iso);
  const index = weeksOf(parts.year, parts.month).findIndex((week) => week.monday === monday);
  return index < 0 ? 0 : index;
}

function yearChoices(iso) {
  const anchorYear = partsOf(iso)?.year || Number(DEFAULT_YEAR);
  const todayYear = partsOf(state.today)?.year || anchorYear;
  const bounds = state.boundYears || [];
  const min = Math.min(2024, anchorYear, todayYear, ...(bounds.length ? bounds : [anchorYear]));
  const max = Math.max(Number(DEFAULT_YEAR), anchorYear, todayYear, ...(bounds.length ? bounds : [anchorYear])) + 1;
  const years = [];
  for (let year = min; year <= max; year += 1) years.push(year);
  return years;
}

function rememberYears(summary) {
  const years = [];
  if (summary) {
    for (const key of ["min_date", "max_date"]) {
      const year = partsOf(summary[key])?.year;
      if (year) years.push(year);
    }
  }
  state.boundYears = years;
}

function menuHead(title, sub) {
  return el(
    "div",
    { class: "picker-head" },
    el("span", null, title),
    sub ? el("span", { class: "picker-head-sub" }, sub) : null,
  );
}

function pickerOption(selected, className, onPick, ...children) {
  return el("button", {
    type: "button",
    class: className,
    role: "option",
    "aria-selected": selected ? "true" : "false",
    tabindex: "-1",
    onclick: onPick,
  }, ...children);
}

function weekRange(week, year, month) {
  const cross = week.monday.slice(0, 4) !== week.sunday.slice(0, 4);
  const start = partsOf(week.monday);
  const end = partsOf(week.sunday);
  return el(
    "span",
    { class: "picker-week-range" },
    el("span", { class: start.year !== year || start.month !== month ? "is-out" : "" }, ddmm(week.monday, cross)),
    " — ",
    el("span", { class: end.year !== year || end.month !== month ? "is-out" : "" }, ddmm(week.sunday, cross)),
  );
}

function fillDayMenu(menu, parts) {
  const { year, month, day } = parts;
  const grid = el("div", { class: "picker-days" });
  WEEKDAYS.forEach((name, index) => {
    grid.append(el("span", { class: index >= 5 ? "picker-dow is-weekend" : "picker-dow" }, name));
  });
  const offset = (utcDate(isoParts(year, month, 1)).getUTCDay() + 6) % 7;
  for (let i = 0; i < offset; i += 1) grid.append(el("span", { class: "picker-pad" }));
  const total = monthLength(year, month);
  for (let number = 1; number <= total; number += 1) {
    const iso = isoParts(year, month, number);
    const classes = ["picker-opt"];
    if ((offset + number - 1) % 7 >= 5) classes.push("is-weekend");
    if (iso === state.today) classes.push("is-today");
    grid.append(pickerOption(number === day, classes.join(" "), () => applyPicker("day", number), String(number)));
  }
  menu.replaceChildren(menuHead(`${monthTitle(month)} ${year}`), grid);
}

function fillWeekMenu(menu, parts) {
  const list = el("div", { class: "picker-list" });
  const current = mondayOf(state.anchor);
  for (const week of weeksOf(parts.year, parts.month)) {
    const now = !!(state.today && state.today >= week.monday && state.today <= week.sunday);
    const classes = ["picker-opt", "picker-week"];
    if (now) classes.push("is-now");
    list.append(pickerOption(
      week.monday === current,
      classes.join(" "),
      () => applyPicker("week", week.anchor),
      weekRange(week, parts.year, parts.month),
      now ? el("span", { class: "picker-week-meta" }, "сейчас") : null,
    ));
  }
  menu.replaceChildren(menuHead(`${monthTitle(parts.month)} ${parts.year}`, "пн — вс"), list);
}

function fillMonthMenu(menu, parts) {
  const grid = el("div", { class: "picker-months" });
  MONTHS.forEach((name, index) => {
    const value = index + 1;
    const label = name.charAt(0).toUpperCase() + name.slice(1);
    grid.append(pickerOption(value === parts.month, "picker-opt picker-month", () => applyPicker("month", value), label));
  });
  menu.replaceChildren(menuHead(String(parts.year)), grid);
}

function fillYearMenu(menu, parts) {
  const list = el("div", { class: "picker-list picker-years" });
  for (const year of yearChoices(state.anchor)) {
    list.append(pickerOption(year === parts.year, "picker-opt picker-year", () => applyPicker("year", year), String(year)));
  }
  menu.replaceChildren(menuHead("Год"), list);
}

function fillMenu(kind, menu) {
  const parts = partsOf(state.anchor);
  menu.replaceChildren();
  if (!parts) return;
  if (kind === "day") fillDayMenu(menu, parts);
  else if (kind === "week") fillWeekMenu(menu, parts);
  else if (kind === "month") fillMonthMenu(menu, parts);
  else fillYearMenu(menu, parts);
}

function closePickers(restoreFocus) {
  const kind = openPickerKind;
  openPickerKind = "";
  for (const picker of document.querySelectorAll("#pickers .picker")) {
    picker.classList.remove("is-open");
    const menu = picker.querySelector(".picker-menu");
    const btn = picker.querySelector(".picker-btn");
    if (menu) {
      menu.hidden = true;
      menu.classList.remove("up");
    }
    if (btn) btn.setAttribute("aria-expanded", "false");
  }
  if (restoreFocus && kind) document.getElementById(`picker-${kind}-btn`)?.focus({ preventScroll: true });
}

function placeMenu(picker, menu) {
  menu.hidden = false;
  menu.classList.remove("up");
  menu.style.left = "0";
  menu.style.right = "auto";
  menu.style.top = "calc(100% + 8px)";
  menu.style.bottom = "auto";
  const host = picker.getBoundingClientRect();
  let rect = menu.getBoundingClientRect();
  if (rect.right > window.innerWidth - 8) {
    menu.style.left = "auto";
    menu.style.right = "0";
    rect = menu.getBoundingClientRect();
  }
  if (rect.left < 8) {
    menu.style.left = "0";
    menu.style.right = "auto";
  }
  rect = menu.getBoundingClientRect();
  if (rect.bottom > window.innerHeight - 8 && host.top > rect.height + 16) {
    menu.classList.add("up");
    menu.style.top = "auto";
    menu.style.bottom = "calc(100% + 8px)";
  }
}

function revealSelected(menu) {
  const selected = menu.querySelector('[aria-selected="true"]');
  const scroller = selected?.closest(".picker-list");
  if (!selected || !scroller) return;
  const sel = selected.getBoundingClientRect();
  const box = scroller.getBoundingClientRect();
  if (sel.top < box.top) scroller.scrollTop -= box.top - sel.top;
  else if (sel.bottom > box.bottom) scroller.scrollTop += sel.bottom - box.bottom;
}

function openPicker(kind) {
  if (!partsOf(state.anchor)) return;
  if (openPickerKind === kind) {
    closePickers(false);
    return;
  }
  closePickers(false);
  const picker = document.querySelector(`#pickers [data-picker="${kind}"]`);
  const menu = document.getElementById(`picker-${kind}-menu`);
  fillMenu(kind, menu);
  openPickerKind = kind;
  picker.classList.add("is-open");
  picker.querySelector(".picker-btn").setAttribute("aria-expanded", "true");
  placeMenu(picker, menu);
  revealSelected(menu);
  const selected = menu.querySelector('[aria-selected="true"]') || menu.querySelector('[role="option"]');
  selected?.focus({ preventScroll: true });
}

function applyAnchor(iso) {
  if (!iso || iso === state.anchor) return;
  const native = document.getElementById("anchor");
  delete native.dataset.seed;
  native.value = iso;
  native.dispatchEvent(new Event("change", { bubbles: true }));
}

function applyPicker(kind, value) {
  const parts = partsOf(state.anchor);
  if (!parts) {
    closePickers(false);
    return;
  }
  const { year, month, day } = parts;
  if (kind === "day" && value === day) { closePickers(true); return; }
  if (kind === "month" && value === month) { closePickers(true); return; }
  if (kind === "year" && value === year) { closePickers(true); return; }
  if (kind === "week" && mondayOf(state.anchor) === mondayOf(value)) { closePickers(true); return; }
  let next = state.anchor;
  if (state.mode === "День") {
    if (kind === "day") next = isoParts(year, month, value);
    if (kind === "month") next = isoClamped(year, value, day);
    if (kind === "year") next = isoClamped(value, month, day);
  } else if (state.mode === "Неделя") {
    if (kind === "week") next = value;
    if (kind === "month" || kind === "year") {
      const nextYear = kind === "year" ? value : year;
      const nextMonth = kind === "month" ? value : month;
      const weeks = weeksOf(nextYear, nextMonth);
      if (weeks.length) next = weeks[Math.min(weekIndex(state.anchor), weeks.length - 1)].anchor;
    }
  } else if (kind === "month") next = isoClamped(year, value, day);
  else if (kind === "year") next = isoClamped(value, month, day);
  closePickers(true);
  applyAnchor(next);
}

function syncPickers() {
  const root = document.getElementById("pickers");
  if (!root) return;
  closePickers(false);
  const free = state.mode === "Свободный";
  root.hidden = free;
  document.querySelector('#pickers [data-picker="day"]').hidden = state.mode !== "День";
  document.querySelector('#pickers [data-picker="week"]').hidden = state.mode !== "Неделя";
  const parts = partsOf(state.anchor);
  const dayBtn = document.getElementById("picker-day-btn");
  const weekBtn = document.getElementById("picker-week-btn");
  const monthBtn = document.getElementById("picker-month-btn");
  const yearBtn = document.getElementById("picker-year-btn");
  for (const btn of [dayBtn, weekBtn, monthBtn, yearBtn]) btn.disabled = !parts;
  const dayValue = document.getElementById("picker-day-value");
  const weekValue = document.getElementById("picker-week-value");
  const monthValue = document.getElementById("picker-month-value");
  const yearValue = document.getElementById("picker-year-value");
  const kicker = document.getElementById("picker-day-kicker");
  if (!parts) {
    dayValue.textContent = "—";
    weekValue.textContent = "—";
    monthValue.textContent = "—";
    yearValue.textContent = "—";
    kicker.textContent = "";
    dayBtn.classList.remove("is-weekend");
    dayBtn.setAttribute("aria-label", "День");
    weekBtn.setAttribute("aria-label", "Неделя");
    monthBtn.setAttribute("aria-label", "Месяц");
    yearBtn.setAttribute("aria-label", "Год");
    return;
  }
  const dow = (utcDate(state.anchor).getUTCDay() + 6) % 7;
  kicker.textContent = WEEKDAYS[dow];
  dayBtn.classList.toggle("is-weekend", dow >= 5);
  dayValue.textContent = String(parts.day);
  monthValue.textContent = monthTitle(parts.month);
  yearValue.textContent = String(parts.year);
  const monday = mondayOf(state.anchor);
  const sunday = shiftDays(monday, 6);
  const cross = monday.slice(0, 4) !== sunday.slice(0, 4);
  const weekLabel = `${ddmm(monday, cross)} — ${ddmm(sunday, cross)}`;
  weekValue.textContent = weekLabel;
  dayBtn.setAttribute("aria-label", `День ${parts.day}`);
  weekBtn.setAttribute("aria-label", `Неделя ${weekLabel}`);
  monthBtn.setAttribute("aria-label", `Месяц ${monthValue.textContent}`);
  yearBtn.setAttribute("aria-label", `Год ${parts.year}`);
}

function onMenuKey(event, kind) {
  const menu = document.getElementById(`picker-${kind}-menu`);
  const opts = [...menu.querySelectorAll('[role="option"]')];
  const index = opts.indexOf(document.activeElement);
  if (event.key === "Escape") {
    event.preventDefault();
    closePickers(true);
    return;
  }
  if (event.key === "Tab") {
    closePickers(false);
    return;
  }
  if (event.key === "Home") {
    event.preventDefault();
    opts[0]?.focus({ preventScroll: true });
    return;
  }
  if (event.key === "End") {
    event.preventDefault();
    opts[opts.length - 1]?.focus({ preventScroll: true });
    return;
  }
  const focusAt = (node) => node?.focus({ preventScroll: true });
  if (kind === "day") {
    const day = index >= 0 ? Number(document.activeElement.textContent) : 0;
    const delta = { ArrowRight: 1, ArrowLeft: -1, ArrowDown: 7, ArrowUp: -7 }[event.key];
    if (!delta) return;
    event.preventDefault();
    if (!day) { focusAt(opts[0]); return; }
    focusAt(opts.find((opt) => Number(opt.textContent) === day + delta));
    return;
  }
  const step = (kind === "month"
    ? { ArrowRight: 1, ArrowLeft: -1, ArrowDown: 3, ArrowUp: -3 }
    : { ArrowDown: 1, ArrowUp: -1 })[event.key];
  if (!step) return;
  event.preventDefault();
  const next = index < 0 ? 0 : index + step;
  if (next >= 0 && next < opts.length) focusAt(opts[next]);
}

function wirePickers() {
  for (const kind of ["day", "week", "month", "year"]) {
    const btn = document.getElementById(`picker-${kind}-btn`);
    const menu = document.getElementById(`picker-${kind}-menu`);
    btn.addEventListener("click", () => openPicker(kind));
    btn.addEventListener("keydown", (event) => {
      if ((event.key === "ArrowDown" || event.key === "ArrowUp") && openPickerKind !== kind) {
        event.preventDefault();
        openPicker(kind);
      }
    });
    menu.addEventListener("keydown", (event) => onMenuKey(event, kind));
  }
  document.addEventListener("pointerdown", (event) => {
    if (!openPickerKind) return;
    if (event.target.closest("#pickers .picker")) return;
    closePickers(false);
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && openPickerKind) {
      event.preventDefault();
      closePickers(true);
    }
  });
  window.addEventListener("resize", () => {
    if (!openPickerKind) return;
    const picker = document.querySelector(`#pickers [data-picker="${openPickerKind}"]`);
    const menu = document.getElementById(`picker-${openPickerKind}-menu`);
    if (picker && menu) placeMenu(picker, menu);
  });
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
let employeesSeq = 0;
let empDetailSeq = 0;

function resetEmployees() {
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
    state.empOpen = "";
    render();
    return;
  }
  loadEmpDetail(lr, badge);
}

async function loadEmpDetail(lr, badge) {
  const key = empKey(lr, badge);
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
    const response = await fetch(`/api/employees/items?${params.toString()}`);
    const data = await parseJson(response);
    if (seq !== empDetailSeq) return;
    if (response.status === 401) {
      state.empOpen = "";
      state.empDetail = null;
      showGate();
      return;
    }
    if (!response.ok) throw new Error(data.error || "Ошибка загрузки");
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
  if (freeRangePending() || !state.report || state.report.empty) return;
  const key = periodKey();
  if (state.employees && state.employeesKey === key) return;
  if (state.employeesLoading && state.employeesKey === key) return;
  const seq = ++employeesSeq;
  state.employeesLoading = true;
  state.employeesError = "";
  state.employees = null;
  state.employeesKey = key;
  if (state.view === "employees") render();
  try {
    const response = await fetch(`/api/employees?${periodParams().toString()}`);
    const data = await parseJson(response);
    if (seq !== employeesSeq) return;
    if (response.status === 401) {
      state.employeesLoading = false;
      showGate();
      return;
    }
    if (!response.ok) throw new Error(data.error || "Ошибка загрузки");
    const pack = data.employees;
    state.employees = pack && !Array.isArray(pack) ? pack : { "5": pack || [], "27": [] };
    state.employeesLoading = false;
  } catch (error) {
    if (seq !== employeesSeq) return;
    state.employeesLoading = false;
    state.employeesError = error.message;
  }
  if (state.view === "employees") render();
}

async function loadReport(options = {}) {
  const seq = ++loadSeq;
  resetEmployees();
  if (options.spin) beginLoadSpin();
  const screen = document.getElementById("screen");
  if (screen) screen.setAttribute("aria-busy", "true");
  setStatus("Обновление…");
  setBanner("");
  try {
    const response = await fetch(`/api/report?${periodParams().toString()}`);
    const data = await parseJson(response);
    if (seq !== loadSeq) return;
    if (response.status === 401) {
      showGate();
      return;
    }
    if (!response.ok) throw new Error(data.error || "Ошибка загрузки");
    await waitAnimating();
    if (seq !== loadSeq) return;
    state.report = data;
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

function chartTickDays(maxDay) {
  const limit = 60;
  if (maxDay <= limit) {
    const days = [];
    for (let day = 1; day <= maxDay; day += 1) days.push(day);
    return days;
  }
  const days = [];
  const last = limit - 1;
  for (let i = 0; i < limit; i += 1) days.push(1 + Math.round(((maxDay - 1) * i) / last));
  return days;
}

function renderChart(rows, grain) {
  const hourly = grain === "hour";
  const colors = seriesColors();
  const height = 400;
  const width = 1280;
  const pad = { l: 96, r: 28, t: 18, b: 40 };
  const innerW = width - pad.l - pad.r;
  const innerH = height - pad.t - pad.b;
  const maxDay = Math.max(1, ...rows.map((row) => row.day));
  const ticks = chartTickDays(maxDay);
  let minStep = ticks.length > 1 ? ticks[1] - ticks[0] : 1;
  for (let i = 2; i < ticks.length; i += 1) minStep = Math.min(minStep, ticks[i] - ticks[i - 1]);
  const span = Math.max(maxDay - 1, 1);
  const spacing = (innerW / span) * minStep;
  const digits = hourly ? 2 : String(maxDay).length;
  const fontPx = Math.min(12, (spacing * 0.84) / (digits * 0.72));
  const maxY = niceMax(Math.max(0, ...rows.map((row) => row.sum)));
  const x = (day) => pad.l + (maxDay === 1 ? innerW / 2 : ((day - 1) / (maxDay - 1)) * innerW);
  const y = (value) => pad.t + innerH - (value / maxY) * innerH;
  const current = rows.filter((row) => row.period === "Текущий период").sort((a, b) => a.day - b.day);
  const previous = rows.filter((row) => row.period === "Прошлый период").sort((a, b) => a.day - b.day);

  const svg = svgEl("svg", { viewBox: `0 0 ${width} ${height}`, class: "chart", role: "img" });
  svg.setAttribute("aria-label", hourly ? "Динамика списаний по часам" : "Динамика списаний по дням");
  for (let i = 0; i <= 4; i += 1) {
    const value = maxY * i / 4;
    const yy = y(value);
    svg.append(svgEl("line", { x1: pad.l, x2: width - pad.r, y1: yy, y2: yy, stroke: colors.grid, "stroke-dasharray": i === 0 ? "0" : "3 4" }));
    const label = svgEl("text", { x: pad.l - 10, y: yy, "text-anchor": "end", "dominant-baseline": "middle", "font-size": "12" });
    label.textContent = fmtInt(value);
    svg.append(label);
  }
  for (const day of ticks) {
    const label = svgEl("text", { x: x(day), y: height - 12, "text-anchor": "middle", "font-size": fontPx.toFixed(2) });
    label.textContent = hourly ? String(day - 1).padStart(2, "0") : String(day);
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

  const guide = svgEl("line", {
    class: "chart-guide",
    x1: pad.l,
    x2: pad.l,
    y1: 1,
    y2: height - 1,
    visibility: "hidden",
  });
  svg.append(guide);
  function focusDot(color) {
    const dot = svgEl("circle", { class: "chart-focus", r: "3.6", fill: color, visibility: "hidden" });
    svg.append(dot);
    return dot;
  }
  const focusPrev = previousPts.length ? focusDot(colors.previous) : null;
  const focusCur = focusDot(colors.current);
  function placeFocus(dot, pts, day) {
    const point = pts.find((item) => item.row.day === day);
    if (!point) {
      dot.setAttribute("visibility", "hidden");
      return;
    }
    dot.setAttribute("cx", point.x);
    dot.setAttribute("cy", point.y);
    dot.setAttribute("visibility", "visible");
  }
  function hideGuide() {
    guide.setAttribute("visibility", "hidden");
    focusCur.setAttribute("visibility", "hidden");
    if (focusPrev) focusPrev.setAttribute("visibility", "hidden");
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
    const gx = x(best);
    guide.setAttribute("x1", gx);
    guide.setAttribute("x2", gx);
    guide.setAttribute("visibility", "visible");
    placeFocus(focusCur, currentPts, best);
    if (focusPrev) placeFocus(focusPrev, previousPts, best);
    tip.replaceChildren();
    const body = dayTip(rows, best);
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
    const today = current.find((row) => row.day === best);
    anchor.x = x(best);
    anchor.y = y((today && today.sum) || 0);
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
  hit.addEventListener("pointerleave", () => {
    tip.hidden = true;
    hideGuide();
  });

  const legend = el("div", { class: "legend" }, el("span", { class: "lg cur" }, "Текущий период"));
  if (previous.length) legend.append(el("span", { class: "lg prev" }, "Прошлый период"));
  return el("div", null, wrap, legend);
}

function empPack() {
  const data = state.employees;
  if (!data) return null;
  if (Array.isArray(data)) return { "5": data, "27": [] };
  return data;
}

function sortEmployees(rows, mode) {
  const list = rows.slice();
  const byQty = mode !== "sum";
  list.sort((a, b) => {
    const lossA = Number(a.losses) || 0;
    const lossB = Number(b.losses) || 0;
    const sumA = Number(a.sum) || 0;
    const sumB = Number(b.sum) || 0;
    const primary = byQty ? lossB - lossA : sumB - sumA;
    if (primary) return primary;
    const secondary = byQty ? sumB - sumA : lossB - lossA;
    if (secondary) return secondary;
    return String(a.badge || "").localeCompare(String(b.badge || ""), "ru", { numeric: true });
  });
  return list;
}

function empPages(total) {
  const pages = [];
  let start = 0;
  let index = 0;
  while (start < total) {
    const size = index === 0 ? 25 : index === 1 ? 50 : 100;
    const end = Math.min(total, start + size);
    pages.push({ start, end });
    start = end;
    index += 1;
  }
  return pages;
}

function showEmpPage(index) {
  if (state.empPage === index) return;
  state.empPage = index;
  render();
  const list = document.querySelector(".emp-list");
  if (list) list.scrollIntoView({ block: "start", behavior: reducedMotion() ? "auto" : "smooth" });
}

function sortMark() {
  const svg = svgEl("svg", { viewBox: "0 0 24 24", width: "18", height: "18", "aria-hidden": "true" });
  svg.append(svgEl("path", {
    fill: "currentColor",
    d: "M3 18h6v-2H3v2zm0-5h12v-2H3v2zm0-7v2h18V6H3z",
  }));
  return svg;
}

function bindEmpSortDismiss() {
  if (bindEmpSortDismiss.ready) return;
  bindEmpSortDismiss.ready = true;
  document.addEventListener("pointerdown", (event) => {
    if (!state.empSortOpen) return;
    if (event.target.closest && event.target.closest(".emp-sort")) return;
    state.empSortOpen = false;
    render();
  });
  document.addEventListener("keydown", (event) => {
    if (!state.empSortOpen || event.key !== "Escape") return;
    state.empSortOpen = false;
    render();
  });
}

function empSortControl() {
  bindEmpSortDismiss();
  const sortMode = state.empSort === "sum" ? "sum" : "qty";
  const menu = el("div", {
    class: "emp-sort-menu",
    role: "menu",
    hidden: state.empSortOpen ? null : true,
  });
  for (const item of [["qty", "штуки"], ["sum", "рубли"]]) {
    const code = item[0];
    menu.append(el("button", {
      type: "button",
      role: "menuitemradio",
      "aria-checked": sortMode === code ? "true" : "false",
      onclick: () => {
        const same = state.empSort === code;
        state.empSortOpen = false;
        if (!same) {
          state.empSort = code;
          state.empPage = 0;
        }
        render();
      },
    }, item[1]));
  }
  const button = el("button", {
    type: "button",
    class: "emp-sort-btn",
    "aria-label": "Сортировка",
    "aria-haspopup": "menu",
    "aria-expanded": state.empSortOpen ? "true" : "false",
    title: "Сортировка",
    onclick: () => {
      state.empSortOpen = !state.empSortOpen;
      render();
    },
  }, sortMark());
  return el("div", { class: "emp-sort" }, button, menu);
}

function renderEmployees(screen) {
  const report = state.report;
  const pack = empPack();
  const lr = state.empLr === "27" ? "27" : "5";
  const all = pack ? (pack[lr] || []) : [];
  const lrs = el("div", { class: "emp-lrs", role: "group", "aria-label": "Операция" });
  for (const [code, label] of [["5", "сборка"], ["27", "раскладка"]]) {
    lrs.append(el("button", {
      type: "button",
      "aria-pressed": lr === code ? "true" : "false",
      onclick: () => {
        if (state.empLr === code) return;
        state.empLr = code;
        state.empPage = 0;
        state.empSortOpen = false;
        render();
      },
    }, label));
  }
  const query = String(state.badgeQuery || "").replace(/\D/g, "");
  const matched = query ? all.filter((row) => String(row.badge || "").includes(query)) : all;
  const rows = sortEmployees(matched, state.empSort);
  const shownLosses = rows.reduce((sum, row) => sum + (Number(row.losses) || 0), 0);
  const shownSum = rows.reduce((sum, row) => sum + (Number(row.sum) || 0), 0);
  const input = el("input", {
    id: "badge-search",
    type: "search",
    inputmode: "numeric",
    placeholder: "Бейджик",
    autocomplete: "off",
    enterkeyhint: "search",
    value: state.badgeDraft || "",
    "aria-label": "Поиск по бейджику",
    oninput: (event) => { state.badgeDraft = event.target.value; },
  });
  const form = el("form", {
    class: "badge-search",
    onsubmit: (event) => {
      event.preventDefault();
      state.badgeQuery = String(state.badgeDraft || "").replace(/\D/g, "");
      state.badgeDraft = state.badgeQuery;
      state.empPage = 0;
      render();
    },
  }, input, el("button", { type: "submit", class: "primary" }, "Найти"));
  if (state.badgeQuery) {
    form.append(el("button", {
      type: "button",
      class: "ghost",
      onclick: () => {
        state.badgeQuery = "";
        state.badgeDraft = "";
        state.empPage = 0;
        render();
      },
    }, "Сброс"));
  }
  screen.append(
    el("div", { class: "screen-head" }, el("h2", null, "Операция")),
    lrs,
    form,
  );
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
  if (state.employeesError) {
    screen.append(el("p", { class: "box bad" }, state.employeesError));
    return;
  }
  if (state.employeesLoading || state.employees == null) {
    screen.append(el("p", { class: "hint" }, "Загрузка сотрудников…"));
    return;
  }
  screen.append(
    el("div", { class: "sub" }, query ? `Найдено сотрудников: ${fmtInt(rows.length)}` : `Сотрудников: ${fmtInt(rows.length)}`),
    el("div", { class: "big-sum" }, fmtRub(shownSum)),
    el("div", { class: "emp-toolbar" },
      el("p", { class: "hint" }, `${fmtInt(shownLosses)} ${lossLabel(shownLosses)}`),
      empSortControl(),
    ),
  );
  if (!all.length) {
    screen.append(el("p", { class: "box warn" }, `Нет потерь LR ${lr} за этот период.`));
    return;
  }
  if (all.length && all.every((row) => !row.badge)) {
    screen.append(el("p", { class: "hint" }, "Бейджики пустые. ID сотрудника берётся из колонки AC выгрузки. Если файл заливали без этой колонки — загрузите период ещё раз."));
  }
  if (!rows.length) {
    screen.append(el("p", { class: "box warn" }, "Такой бейджик не найден."));
    return;
  }
  const pages = empPages(rows.length);
  let pageIndex = state.empPage;
  if (pageIndex >= pages.length) pageIndex = 0;
  if (pageIndex !== state.empPage) state.empPage = pageIndex;
  const page = pages[pageIndex];
  const list = el("div", { class: "emp-list" });
  for (const row of rows.slice(page.start, page.end)) {
    const badge = String(row.badge || "");
    const label = badge || "не указан";
    const key = empKey(lr, badge);
    const open = state.empOpen === key;
    const card = el("div", { class: open ? "emp-card is-open" : "emp-card" });
    card.append(el(
      "button",
      {
        type: "button",
        class: "emp-row",
        "aria-expanded": open ? "true" : "false",
        onclick: () => toggleEmp(lr, badge),
      },
      el("span", { class: "emp-badge" }, label),
      el("span", { class: "emp-losses" },
        el("span", { class: "emp-qty" }, fmtInt(row.losses)),
        el("span", { class: "emp-loss-word" }, lossLabel(row.losses)),
      ),
      el("span", { class: "emp-sum" }, fmtRub(row.sum)),
      row.wmi
        ? el("span", { class: "emp-bang", title: "Есть потеря после статуса WMI" }, "!")
        : el("span", { class: "emp-bang idle", "aria-hidden": "true" }, "!"),
    ));
    if (open) {
      const detail = state.empDetail && state.empDetail.key === key
        ? state.empDetail
        : { key, loading: true, error: "", items: null, ready: true };
      card.append(renderEmpDetail(detail));
    }
    list.append(card);
  }
  screen.append(list);
  if (pages.length > 1) {
    const nav = el("nav", { class: "emp-pages", "aria-label": "Страницы сотрудников" });
    if (pageIndex > 0) {
      nav.append(el("button", {
        type: "button",
        class: "emp-page-arrow",
        "aria-label": "Предыдущая страница",
        onclick: () => showEmpPage(pageIndex - 1),
      }, "←"));
    }
    nav.append(el("span", { class: "emp-page-tab", "aria-current": "page" }, String(pageIndex + 1)));
    if (pageIndex < pages.length - 1) {
      nav.append(el("button", {
        type: "button",
        class: "emp-page-arrow",
        "aria-label": "Следующая страница",
        onclick: () => showEmpPage(pageIndex + 1),
      }, "→"));
    }
    screen.append(nav);
  }
}

function empFoundNode(found) {
  if (found === true) {
    return el(
      "span",
      { class: "emp-found yes" },
      "найдено",
      el("span", { class: "emp-mark", "aria-hidden": "true" }, "✓"),
    );
  }
  if (found === false) {
    return el(
      "span",
      { class: "emp-found no" },
      "не найдено",
      el("span", { class: "emp-mark", "aria-hidden": "true" }, "✕"),
    );
  }
  return el("span", { class: "emp-found unknown" }, "нет флага");
}

function renderEmpDetail(detail) {
  const box = el("div", { class: "emp-detail" });
  if (!detail || detail.loading || detail.items == null) {
    box.append(el("p", { class: "hint" }, "Загрузка стикеров…"));
    return box;
  }
  if (detail.error) {
    box.append(el("p", { class: "box bad" }, detail.error));
    return box;
  }
  if (!detail.ready) {
    box.append(el("p", { class: "hint" }, "В этих строках нет стикера и флага оприходования. Если период заливали без колонок AF и Q — загрузите его ещё раз."));
    return box;
  }
  if (!detail.items.length) {
    box.append(el("p", { class: "hint" }, "Нет строк за этот период."));
    return box;
  }
  for (const item of detail.items) {
    box.append(el(
      "div",
      { class: "emp-item" },
      el("span", { class: "emp-sticker" }, item.sticker || "—"),
      empFoundNode(item.found),
      el("span", { class: "emp-sum" }, fmtRub(item.sum)),
    ));
  }
  return box;
}

function renderDashboard(screen) {
  const dash = state.report.dashboard;
  const hero = el("div", { class: "hero" });
  const chart = el("div");
  chart.innerHTML = dash.donut_svg;
  bindDonut(chart);
  const side = el("div");
  for (const code of ["out", "in"]) {
    const flow = state.report.flows[code];
    const title = code === "in" ? "входящий поток" : "исходящий поток";
    const help = code === "in" ? "Открыть блоки входящего потока" : "Открыть блоки исходящего потока";
    const copy = el(
      "span",
      { class: "flow-copy" },
      el("span", { class: "flow-name" }, title),
      el("span", { class: "flow-meta" }, `${pctLabel(flow.share)} · ${fmtInt(flow.qty)} шт`),
    );
    if (flow.delta_text) {
      const kind = flow.delta_text.startsWith("+") ? "r" : flow.delta_text.startsWith("-") ? "g" : "n";
      copy.append(el("span", { class: `chip chip-${kind}` }, flow.delta_text));
    }
    side.append(el(
      "button",
      {
        type: "button",
        class: `flow-btn ${code}`,
        title: help,
        onclick: () => go({ view: "flow", flow: code, group: "", block: "" }),
      },
      copy,
      el("strong", null, fmtRub(flow.sum)),
    ));
  }
  side.className = "hero-side";
  side.append(renderMovers());
  const donutCol = el("div", { class: "loss-col" });
  donutCol.append(chart);
  hero.append(donutCol, side);
  if (state.report.show_daily) {
    const hourly = state.report.chart_grain === "hour";
    const chartCol = el(
      "div",
      { class: "daily-chart" },
      el("h3", { class: "section-title" }, hourly ? "Динамика по часам" : "Динамика по дням"),
      renderChart(state.report.daily, state.report.chart_grain),
    );
    hero.append(el("div", { class: "daily-board" }, chartCol));
  }
  screen.append(hero);
  if (dash.other && dash.other.show) {
    const box = el("div", { class: "aside-metric" });
    const label = el("div", { class: "aside-label" }, "Вне потоков ", el("span", null, "· не входит в диаграмму"));
    box.append(label, el("div", { class: "aside-value" }, fmtRub(dash.other.sum)));
    if (dash.other.delta_text) box.append(el("div", { class: "aside-delta" }, dash.other.delta_text));
    screen.append(box);
  }
  if (dash.other && dash.other.has_rows) {
    screen.append(el("p", { class: "hint" }, "Статусы вне потоков временно исключены из диаграммы и итогов — список в админке, вкладка «Инфо»."));
  }
}

function renderFlow(screen) {
  const flow = flowByCode(state.flow);
  if (!flow) {
    screen.append(missing("dashboard"));
    return;
  }
  const csvRows = flow.cards.map((card) => [
    card.name,
    Math.round(card.loss_sum || 0),
    Number(card.pct || 0).toFixed(1),
    Math.round(card.prev_sum || 0),
    Math.round((card.loss_sum || 0) - (card.prev_sum || 0)),
  ]);
  screen.append(
    backButton("← Назад на главную", "dashboard"),
    el("div", { class: "screen-head" },
      el("h2", null, `Выбран: ${flow.title}`),
      downloadButton(exportName(`potok-${state.flow}`), ["Блок", "Сумма (₽)", "Доля (%)", "Прошлый период (₽)", "Изменение (₽)"], csvRows),
    ),
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
  const csvRows = [];
  for (const section of view.sections) {
    for (const item of section.cards) {
      csvRows.push([
        section.title || "",
        item.name,
        Math.round(item.loss_sum || 0),
        Number(item.pct || 0).toFixed(1),
        Math.round(item.prev_sum || 0),
      ]);
    }
  }
  screen.append(
    backButton("← Назад к потокам", "flow"),
    el("div", { class: "screen-head" },
      el("h2", null, `Блок: ${card.name} (${flow.title})`),
      downloadButton(exportName("blok"), ["Раздел", "Площадка", "Сумма (₽)", "Доля (%)", "Прошлый период (₽)"], csvRows),
    ),
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
  const csvRows = detail.rows.map((row) => [
    row.reason,
    Math.round(row.sum || 0),
    Math.round(row.qty || 0),
    Number(row.pct || 0).toFixed(1),
  ]);
  screen.append(
    backButton("← Назад к площадкам", "group"),
    el("div", { class: "screen-head" },
      el("h2", null, detail.title),
      downloadButton(exportName("lr"), ["Причина (LR)", "Сумма (₽)", "Кол-во (шт)", "Доля (%)"], csvRows),
    ),
    el("div", { class: "metric-label" }, "Сумма списаний по площадке"),
    el("div", { class: "metric-value" }, fmtRub(detail.sum)),
  );
  const delta = deltaNode(detail.delta_text);
  if (delta) screen.append(delta);
  screen.append(el("hr"));
  if (detail.excluded_sum != null) {
    screen.append(el("p", { class: "hint" }, `ℹ️ Не включено: ${fmtRub(detail.excluded_sum)} со статусами вне потоков (какие именно — в админке, вкладка «Инфо»).`));
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
  if (state.view === "employees") {
    renderEmployees(screen);
    return;
  }
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
  window.setTimeout(close, 360);
  if (kind !== "settle" && host.getBoundingClientRect().top < 8) {
    host.scrollIntoView({ behavior: "smooth", block: "start" });
  }
}

function syncSections() {
  const employees = state.view === "employees";
  const losses = document.getElementById("section-losses");
  const staff = document.getElementById("section-employees");
  if (losses) losses.setAttribute("aria-pressed", employees ? "false" : "true");
  if (staff) staff.setAttribute("aria-pressed", employees ? "true" : "false");
  const search = document.querySelector("header .search");
  if (search) search.hidden = employees;
  const title = document.querySelector("header.top h1");
  if (title) title.textContent = employees ? "📦 аналитика потерь сотрудников" : "📦 Аналитика списаний";
}

function render() {
  syncSections();
  renderNotes();
  renderQuality();
  renderScreen();
}

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
  const ownPassword = document.getElementById("own-password");
  const ownSecret = document.getElementById("own-secret");
  const ownSecretValue = document.getElementById("own-secret-value");
  ownSecretValue?.addEventListener("focus", () => ownSecretValue.select());
  ownSecretValue?.addEventListener("click", () => ownSecretValue.select());
  ownPassword?.addEventListener("click", async () => {
    ownPassword.disabled = true;
    try {
      const data = await readJson(await fetch("/api/admin/access/password", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          phone: input.value,
          email: email ? email.value : "",
          domain: domain ? domain.value : "",
        }),
      }));
      state.user = { ...state.user, ...data, ok: true };
      delete state.user.password;
      fillOwnContact(data);
      showSecret(ownSecret, ownSecretValue, data.password);
      setAdminStatus("Пароль готов");
    } catch (error) {
      setAdminStatus(error.message);
    } finally {
      ownPassword.disabled = false;
    }
  });
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

async function sendPreview(files, hint) {
  const batch = newUploadId();
  const ids = [];
  let step = 0;
  let pieces = 0;
  for (const file of files) pieces += Math.max(1, Math.ceil(file.size / UPLOAD_CHUNK));
  for (const file of files) {
    const uploadId = newUploadId();
    ids.push(uploadId);
    const total = Math.max(1, Math.ceil(file.size / UPLOAD_CHUNK));
    for (let index = 0; index < total; index += 1) {
      step += 1;
      if (hint) hint.textContent = pieces > 1 ? `Читаем файлы… ${step} из ${pieces}` : "Читаем файлы…";
      const body = new FormData();
      body.append("batch", batch);
      body.append("upload_id", uploadId);
      body.append("name", file.name);
      body.append("index", String(index));
      body.append("total", String(total));
      body.append("chunk", file.slice(index * UPLOAD_CHUNK, (index + 1) * UPLOAD_CHUNK), "part");
      await readJson(await fetch("/api/admin/preview/chunk", { method: "POST", body }));
    }
  }
  return readJson(await fetch("/api/admin/preview/finish", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ batch, ids }),
  }));
}

function wireAdmin() {
  wireGrant();
  wireOwnPhone();
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
        setStatus("");
        setBanner("");
        render();
      } else loadReport({ spin: true });
    } else {
      render();
      if (state.view === "employees") loadEmployees();
    }
  });
}

function showGate() {
  state.user = null;
  const gate = document.getElementById("gate");
  const layout = document.querySelector(".layout");
  const toggle = document.getElementById("admin-toggle");
  const who = document.getElementById("who");
  if (gate) gate.hidden = false;
  if (layout) layout.hidden = true;
  if (toggle) toggle.hidden = true;
  if (who) who.hidden = true;
  const passwordPanel = document.getElementById("password-panel");
  if (passwordPanel) passwordPanel.hidden = true;
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

function applySession() {
  const user = state.user;
  const gate = document.getElementById("gate");
  const layout = document.querySelector(".layout");
  const toggle = document.getElementById("admin-toggle");
  const who = document.getElementById("who");
  if (gate) gate.hidden = true;
  if (layout) layout.hidden = false;
  const admin = user && user.role === "admin";
  if (toggle) toggle.hidden = !admin;
  if (!admin) setAdminOpen(false);
  fillOwnContact(user);
  if (!who) return;
  who.hidden = false;
  who.replaceChildren(
    document.createTextNode(`ID ${user.id}`),
    el("button", { type: "button", onclick: () => togglePassword() }, "пароль"),
    el("button", { type: "button", onclick: () => logout() }, "выйти"),
  );
}

const PASSWORD_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789";

function randomPassword() {
  const bytes = new Uint8Array(8);
  crypto.getRandomValues(bytes);
  return Array.from(bytes, (byte) => PASSWORD_ALPHABET[byte % PASSWORD_ALPHABET.length]).join("");
}

function setPasswordNote(text, bad) {
  const note = document.getElementById("password-note");
  if (!note) return;
  note.textContent = text || "";
  note.hidden = !text;
  note.classList.toggle("is-error", Boolean(bad));
}

function togglePassword() {
  const panel = document.getElementById("password-panel");
  const input = document.getElementById("password-next");
  if (!panel) return;
  panel.hidden = !panel.hidden;
  if (panel.hidden) return;
  setPasswordNote("");
  input?.focus();
}

function wirePassword() {
  const panel = document.getElementById("password-panel");
  const input = document.getElementById("password-next");
  const random = document.getElementById("password-random");
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
    if (password.length < 8 || password.length > 64) {
      setPasswordNote("Пароль от 8 до 64 символов", true);
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
      setPasswordNote(error.message || "Пароль от 8 до 64 символов", true);
    } finally {
      if (save) save.disabled = false;
    }
  });
  document.addEventListener("click", (event) => {
    if (panel.hidden) return;
    if (document.getElementById("account")?.contains(event.target)) return;
    panel.hidden = true;
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !panel.hidden) panel.hidden = true;
  });
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
  try {
    const data = await readJson(await fetch("/api/auth/me"));
    if (!data.ok) {
      showGate();
      return;
    }
    state.user = data;
    applySession();
    await openApp();
  } catch (error) {
    showGate();
  }
}

async function openApp() {
  readUrl();
  syncForm(true);
  setStatus("Загрузка…");
  try {
    const response = await fetch("/api/bootstrap");
    const data = await parseJson(response);
    if (response.status === 401) {
      showGate();
      return;
    }
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
    setBanner("Не удалось открыть сервис");
    setStatus("");
  }
}

wireChrome();
wireAdmin();
wireAuth();
wirePassword();
boot();
