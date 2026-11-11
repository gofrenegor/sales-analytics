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
  state.slice = ["blocks", "writeoffs"].includes(params.get("slice")) ? params.get("slice") : "flows";
  if (state.view === "employees") {
    state.flow = "";
    state.group = "";
    state.block = "";
  }
  if (!params.get("view") && (state.flow === "in" || state.flow === "out" || state.flow === "pre")) state.view = "flow";
  if (state.flow === "pre" && state.view !== "dashboard" && state.view !== "employees" && state.view !== "flow") {
    state.view = "flow";
    state.group = "";
    state.block = "";
  }
  if ((state.view === "flow" || state.view === "group" || state.view === "block_detail") && !["in", "out", "pre"].includes(state.flow)) {
    state.view = "dashboard";
  }
  if (state.view === "group" && !state.group) state.view = "flow";
  if (state.view === "block_detail" && (!state.group || !state.block)) state.view = state.group ? "group" : "flow";
  if (state.view === "block_loss" && !state.block) {
    state.view = "dashboard";
    state.slice = "blocks";
  } else if (state.view === "block_loss") {
    state.slice = "blocks";
  }
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
  if (["blocks", "writeoffs"].includes(state.slice)) params.set("slice", state.slice);
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
  const prevBlock = state.block;
  Object.assign(state, patch);
  if (state.view === "employees") {
    state.flow = "";
    state.group = "";
    state.block = "";
  }
  if (state.block !== prevBlock && !("blockLr" in patch)) state.blockLr = "5";
  if (viewKey() !== prevKey) {
    const nextDepth = VIEW_DEPTH[state.view] ?? 0;
    if (nextDepth < prevDepth) screenMotion = "back";
    else if (nextDepth > prevDepth) screenMotion = "forward";
    else screenMotion = "";
  }
  writeUrl(push);
  render();
  if (state.view === "employees") loadEmployees();
  else if (state.reportKey !== periodKey() || !reportHasDetail(reportDetailForView())) loadReport();
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

function reportDetailForView() {
  if (state.slice === "writeoffs") return "writeoffs";
  if (state.slice === "blocks" || state.view === "block_loss") return "blocks";
  if (state.view === "flow" || state.view === "group" || state.view === "block_detail") return "flows";
  return "summary";
}

function reportHasDetail(detail) {
  if (!state.report) return false;
  if (state.reportDetail === "full" || state.reportDetail === detail) return true;
  const dash = state.report.dashboard;
  return detail === "summary" && state.reportDetail === "flows" && Boolean(dash && dash.donut_svg);
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
    loadSeq += 1;
    cancelLoad("report");
    resetEmployees();
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

