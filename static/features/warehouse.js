function renderDashboard(screen) {
  if (state.slice === "writeoffs") { renderWarehouseWriteoffs(screen); return; }
  const dash = state.report && state.report.dashboard;
  const blocksMode = state.slice === "blocks";
  const donutSvg = blocksMode && dash && dash.blocks && dash.blocks.donut_svg
    ? dash.blocks.donut_svg
    : (!blocksMode && dash ? dash.donut_svg : "");
  if (!donutSvg) {
    screen.append(el("p", { class: "hint" }, "Загрузка…"));
    return;
  }
  const hero = el("div", { class: blocksMode ? "hero hero-blocks" : "hero" });
  const chart = el("div");
  state.donutToken = (state.donutToken || 0) + 1;
  chart.replaceChildren(safeSvgFragment(donutSvg.replaceAll("__DONUT__", String(state.donutToken))));
  bindDonut(chart);
  const donutCol = el("div", { class: "loss-col" });
  donutCol.append(chart);
  hero.append(donutCol);
  if (!blocksMode) {
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
    hero.append(side);
  }
  if (state.report.show_daily) {
    const hourly = state.report.chart_grain === "hour";
    const lines = blocksMode && dash.blocks && Array.isArray(dash.blocks.lines) ? dash.blocks.lines : null;
    const blockLines = lines && lines.length;
    const chartCol = el(
      "div",
      { class: "daily-chart" },
      el("h3", { class: "section-title" }, blockLines ? "Динамика потерь" : (hourly ? "Динамика по часам" : "Динамика по дням")),
    );
    if (blockLines) {
      chartCol.append(el(
        "p",
        { class: "plat-pin-hint" },
        el("kbd", null, "shift"),
        "закрепляет мультиграфик на экране",
      ));
    }
    chartCol.append(blockLines
      ? renderBlockChart(lines, state.report.chart_grain)
      : renderChart(state.report.daily, state.report.chart_grain));
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

function renderBlockLoss(screen) {
  const pages = state.report.block_pages || {};
  const page = pages[state.block];
  if (!page) {
    screen.append(missing("dashboard"));
    return;
  }
  const lr = state.blockLr === "27" ? "27" : "5";
  const series = (page.series && page.series[lr]) || [];
  const ring = el("div", { class: "loss-col" });
  ring.replaceChildren(safeSvgFragment(page.donut_svg || ""));
  const hero = el("div", { class: "hero hero-block" });
  hero.append(ring);
  const chartCol = el(
    "div",
    { class: "daily-chart" },
    el(
      "div",
      { class: "block-chart-head" },
      el("h3", null, "Динамика потерь"),
      el("button", {
        type: "button",
        class: "block-lr",
        onclick: () => {
          state.blockLr = state.blockLr === "27" ? "5" : "27";
          render();
        },
      }, lr === "27" ? "раскладка" : "сборка"),
    ),
    renderChart(series, state.report.chart_grain),
  );
  hero.append(el("div", { class: "daily-board" }, chartCol));
  screen.append(
    backButton("← Назад к блокам", "dashboard"),
    el("h2", null, page.face || state.block),
    hero,
  );
}

function renderScreen() {
  const pane = el("div", { class: "screen-pane" });
  fillScreen(pane);
  presentScreen(pane);
}

function fillScreen(screen) {
  setPlatWatch(null);
  setPlatKey(null);
  document.querySelectorAll("#screen .loss-donut").forEach((svg) => {
    svg._pairObserver?.disconnect();
  });
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
  else if (state.view === "block_loss") renderBlockLoss(screen);
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

function clearForkMotion() {
  for (const id of ["slice-blocks", "slice-flows", "slice-writeoffs"]) {
    const node = document.getElementById(id);
    if (!node) continue;
    node.style.transition = "";
    node.style.transform = "";
    node.style.opacity = "";
  }
  const svg = document.querySelector("nav.sections .wh-lines");
  if (!svg) return;
  svg.style.transition = "";
  svg.style.opacity = "";
}

function placeForkLine(line, x1, y1, x2, y2, originX, originY) {
  line.setAttribute("x1", String(x1 - originX));
  line.setAttribute("y1", String(y1 - originY));
  line.setAttribute("x2", String(x2 - originX));
  line.setAttribute("y2", String(y2 - originY));
}

function layoutFork() {
  const wh = document.querySelector("nav.sections .wh");
  if (!wh || !wh.classList.contains("is-open")) return;
  const losses = document.getElementById("section-losses");
  const blocks = document.getElementById("slice-blocks");
  const flows = document.getElementById("slice-flows");
  const fork = wh.querySelector(".wh-fork");
  const svg = wh.querySelector(".wh-lines");
  const lineBlocks = document.getElementById("slice-line-blocks");
  const lineFlows = document.getElementById("slice-line-flows");
  const writeoffs = document.getElementById("slice-writeoffs");
  const lineWriteoffs = document.getElementById("slice-line-writeoffs");
  if (!losses || !blocks || !flows || !fork || !svg || !lineBlocks || !lineFlows || !writeoffs || !lineWriteoffs) return;
  if (!fork.offsetWidth || !losses.offsetWidth) return;

  const writeoffsLeft = losses.offsetLeft + losses.offsetWidth + 10;
  writeoffs.style.left = `${writeoffsLeft}px`;
  wh.style.width = `${writeoffsLeft + writeoffs.offsetWidth}px`;

  const anchorX = losses.offsetLeft + losses.offsetWidth / 2;
  let forkLeft = anchorX - (blocks.offsetLeft + blocks.offsetWidth / 2 + flows.offsetLeft + flows.offsetWidth / 2) / 2;
  const hostLeft = wh.getBoundingClientRect().left;
  const pad = 8;
  const minLeft = pad - hostLeft;
  const maxLeft = window.innerWidth - pad - hostLeft - fork.offsetWidth;
  if (maxLeft >= minLeft) forkLeft = Math.min(Math.max(forkLeft, minLeft), maxLeft);
  else forkLeft = minLeft;
  const staff = document.getElementById("section-employees");
  if (staff) {
    const staffRect = staff.getBoundingClientRect();
    const forkTop = wh.getBoundingClientRect().top + losses.offsetTop + losses.offsetHeight + 16;
    const forkBottom = forkTop + fork.offsetHeight;
    const overlapsY = forkTop < staffRect.bottom - 1 && forkBottom > staffRect.top + 1;
    if (overlapsY) {
      const overflow = hostLeft + forkLeft + fork.offsetWidth - (staffRect.left - pad);
      if (overflow > 0) forkLeft = Math.max(minLeft, forkLeft - overflow);
    }
  }
  forkLeft = Math.round(forkLeft);
  fork.style.left = `${forkLeft}px`;
  fork.style.transform = "none";
  wh.style.marginBottom = `${16 + fork.offsetHeight}px`;

  const y0 = losses.offsetTop + losses.offsetHeight;
  const y1 = fork.offsetTop + blocks.offsetTop;
  const xBlocks = forkLeft + blocks.offsetLeft + blocks.offsetWidth / 2;
  const xFlows = forkLeft + flows.offsetLeft + flows.offsetWidth / 2;
  const xWriteoffs = writeoffs.offsetLeft;
  const yWriteoffs = writeoffs.offsetTop + writeoffs.offsetHeight / 2;
  const writeoffsOriginY = losses.offsetTop + losses.offsetHeight / 2;
  const bleed = 2;
  const minX = Math.min(anchorX, xBlocks, xFlows, xWriteoffs) - bleed;
  const minY = Math.min(y0, y1, yWriteoffs, writeoffsOriginY) - bleed;
  const width = Math.max(anchorX, xBlocks, xFlows, xWriteoffs) + bleed - minX;
  const height = Math.max(y0, y1, yWriteoffs) + bleed - minY;
  svg.style.left = `${minX}px`;
  svg.style.top = `${minY}px`;
  svg.style.width = `${width}px`;
  svg.style.height = `${height}px`;
  svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
  svg.setAttribute("width", String(width));
  svg.setAttribute("height", String(height));
  placeForkLine(lineBlocks, anchorX, y0, xBlocks, y1, minX, minY);
  placeForkLine(lineFlows, anchorX, y0, xFlows, y1, minX, minY);
  placeForkLine(lineWriteoffs, losses.offsetLeft + losses.offsetWidth, writeoffsOriginY, xWriteoffs, yWriteoffs, minX, minY);
}

function playForkEnter() {
  const losses = document.getElementById("section-losses");
  const buttons = ["slice-blocks", "slice-flows", "slice-writeoffs"].map((id) => document.getElementById(id)).filter(Boolean);
  const svg = document.querySelector("nav.sections .wh-lines");
  if (!losses || buttons.length !== 3) return;
  const origin = losses.getBoundingClientRect();
  if (!origin.width || !origin.height) return;
  const ox = origin.left + origin.width / 2;
  const oy = origin.bottom;
  const starts = [];
  for (const btn of buttons) {
    const rect = btn.getBoundingClientRect();
    if (!rect.width || !rect.height) return;
    starts.push({
      btn,
      dx: ox - (rect.left + rect.width / 2),
      dy: oy - rect.top,
    });
  }
  for (const item of starts) {
    item.btn.style.transition = "none";
    item.btn.style.opacity = "0";
    item.btn.style.transform = `translate(${item.dx}px, ${item.dy}px)`;
  }
  if (svg) {
    svg.style.transition = "none";
    svg.style.opacity = "0";
  }
  losses.getBoundingClientRect();
  const token = ++forkMotion;
  const motion = "transform 190ms var(--ease-out), opacity 190ms var(--ease-out)";
  for (const item of starts) {
    item.btn.style.transition = motion;
    item.btn.style.opacity = "1";
    item.btn.style.transform = "translate(0px, 0px)";
  }
  if (svg) {
    svg.style.transition = "opacity 190ms var(--ease-out)";
    svg.style.opacity = "1";
  }
  window.setTimeout(() => {
    if (token !== forkMotion) return;
    clearForkMotion();
  }, 230);
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
  const open = !employees;
  const blocksBtn = document.getElementById("slice-blocks");
  const flowsBtn = document.getElementById("slice-flows");
  const writeoffsBtn = document.getElementById("slice-writeoffs");
  if (writeoffsBtn) writeoffsBtn.setAttribute("aria-pressed", open && state.slice === "writeoffs" ? "true" : "false");
  if (blocksBtn) blocksBtn.setAttribute("aria-pressed", open && state.slice === "blocks" ? "true" : "false");
  if (flowsBtn) flowsBtn.setAttribute("aria-pressed", open && state.slice === "flows" ? "true" : "false");
  const wh = document.querySelector("nav.sections .wh");
  if (!wh) return;
  const appear = forkOpen === false && open && !reducedMotion();
  wh.classList.toggle("is-open", open);
  if (!open) {
    forkMotion += 1;
    clearForkMotion();
    wh.style.marginBottom = "";
    wh.style.width = "";
    const fork = wh.querySelector(".wh-fork");
    if (fork) {
      fork.style.left = "";
      fork.style.transform = "";
    }
  } else {
    layoutFork();
    if (appear) playForkEnter();
  }
  forkOpen = open;
}

function render() {
  syncSections();
  renderNotes();
  renderQuality();
  renderScreen();
}

