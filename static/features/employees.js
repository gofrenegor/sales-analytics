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
  loadEmployees().then(() => {
    const list = document.querySelector(".emp-list");
    if (list) list.scrollIntoView({ block: "start", behavior: reducedMotion() ? "auto" : "smooth" });
  });
}

function bindEmpPageCatalogDismiss() {
  if (bindEmpPageCatalogDismiss.ready) return;
  bindEmpPageCatalogDismiss.ready = true;
  const close = (restoreFocus) => {
    const catalog = document.getElementById("emp-page-catalog");
    if (!catalog || catalog.hidden) return;
    catalog.hidden = true;
    const trigger = document.getElementById("emp-page-catalog-toggle");
    trigger?.setAttribute("aria-expanded", "false");
    if (restoreFocus) trigger?.focus();
  };
  document.addEventListener("pointerdown", (event) => {
    if (!event.target.closest?.(".emp-page-picker")) close(false);
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") close(true);
  });
  document.addEventListener("focusin", (event) => {
    if (!event.target.closest?.(".emp-page-picker")) close(false);
  });
}

function empPageCatalog(pageIndex, totalPages) {
  bindEmpPageCatalogDismiss();
  let batch = Math.floor(pageIndex / 50);
  const panel = el("div", {
    id: "emp-page-catalog",
    class: "emp-page-catalog",
    hidden: true,
    role: "region",
    "aria-label": "Каталог страниц сотрудников",
  });
  const icon = svgEl("svg", { viewBox: "0 0 24 24", width: "20", height: "20", "aria-hidden": "true" });
  icon.append(svgEl("path", {
    fill: "none", stroke: "currentColor", "stroke-width": "1.7", "stroke-linejoin": "round",
    d: "M3 3h7v7H3z M14 3h7v7h-7z M3 14h7v7H3z M14 14h7v7h-7z",
  }));
  const trigger = el("button", {
    id: "emp-page-catalog-toggle",
    type: "button",
    class: "emp-page-catalog-toggle",
    title: "Каталог страниц",
    "aria-label": "Каталог страниц",
    "aria-expanded": "false",
    "aria-controls": "emp-page-catalog",
    onclick: () => {
      panel.hidden = !panel.hidden;
      trigger.setAttribute("aria-expanded", String(!panel.hidden));
      if (!panel.hidden) {
        batch = Math.floor(pageIndex / 50);
        paint();
        panel.querySelector('[aria-current="page"]')?.focus();
      }
    },
  }, icon);
  function paint(focusDirection) {
    const start = batch * 50;
    const end = Math.min(start + 50, totalPages);
    const grid = el("div", { class: "emp-page-catalog-grid" });
    for (let index = start; index < end; index += 1) {
      grid.append(el("button", {
        type: "button",
        class: "emp-page-choice",
        "aria-label": `Страница ${index + 1}`,
        "aria-current": index === pageIndex ? "page" : null,
        onclick: () => {
          panel.hidden = true;
          trigger.setAttribute("aria-expanded", "false");
          trigger.focus();
          showEmpPage(index);
        },
      }, String(index + 1)));
    }
    const move = (direction) => el("button", {
      type: "button",
      class: "emp-page-arrow",
      "data-direction": direction,
      "aria-label": direction < 0 ? "Предыдущие 50 страниц" : "Следующие 50 страниц",
      disabled: direction < 0 ? batch === 0 : end === totalPages,
      onclick: () => { batch += direction; paint(direction); },
    }, direction < 0 ? "←" : "→");
    panel.replaceChildren(
      grid,
      el("div", { class: "emp-page-catalog-nav" },
        move(-1),
        el("span", { "aria-live": "polite", "aria-atomic": "true" }, `${start + 1}–${end} из ${totalPages}`),
        move(1),
      ),
    );
    if (focusDirection) {
      const next = panel.querySelector(`[data-direction="${focusDirection}"]`);
      (next && !next.disabled ? next : grid.querySelector("button"))?.focus();
    }
  }
  return el("div", { class: "emp-page-picker" },
    panel,
    trigger,
    el("span", { class: "emp-page-tab", "aria-current": "page" }, String(pageIndex + 1)),
  );
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
  const sortMode = ["sum", "found", "found_share"].includes(state.empSort) ? state.empSort : "qty";
  const menu = el("div", {
    class: "emp-sort-menu",
    role: "menu",
    hidden: state.empSortOpen ? null : true,
  });
  for (const item of [["qty", "штуки"], ["sum", "рубли"], ["found", "найденные товары"], ["found_share", "процент находов"]]) {
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
        if (same) render();
        else loadEmployees();
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

function measureEmpOps(lrs) {
  const buttons = [...lrs.querySelectorAll("button")];
  for (const button of buttons) button.style.width = "max-content";
  const width = Math.ceil(Math.max(0, ...buttons.map((button) => button.getBoundingClientRect().width)));
  for (const button of buttons) button.style.width = "";
  if (width) lrs.style.setProperty("--emp-op", `${width}px`);
  lrs.classList.add("is-measured");
}

function settleEmpOps(lrs) {
  const motion = state.empLrMotion;
  state.empLrMotion = "";
  if (motion) lrs.classList.add("is-instant");
  measureEmpOps(lrs);
  if (!motion) return;
  requestAnimationFrame(() => {
    lrs.classList.remove("is-instant");
    requestAnimationFrame(() => {
      lrs.classList.remove("from-layout", "from-assembly");
      lrs.classList.add(motion === "to-assembly" ? "is-assembly" : "is-layout");
    });
  });
}

function pickEmpLr(code) {
  if (code === "5" && state.empLr === "5") {
    if (!state.empRows) return;
    state.empRows = false;
    state.rowBlock = "";
    state.rowFloor = "";
    state.rowOpen = "";
    render();
    loadEmployees();
    return;
  }
  if (code === "27" && state.empLr === "27") return;
  state.empLr = code;
  state.empRows = false;
  state.rowBlock = "";
  state.rowFloor = "";
  state.rowOpen = "";
  state.empPage = 0;
  state.empSortOpen = false;
  state.empOpen = "";
  state.empLrMotion = code === "5" ? "to-assembly" : "to-layout";
  loadEmployees();
}

function openProblemRows() {
  if (state.empLr !== "5") return;
  state.empRows = true;
  state.empOpen = "";
  state.empSortOpen = false;
  state.rowBlock = "";
  state.rowFloor = "";
  state.rowOpen = "";
  state.rowPage = 0;
  render();
  loadProblemRows();
}

function renderEmpOps(lr) {
  const motion = state.empLrMotion;
  let pose = lr === "5" ? "is-assembly" : "is-layout";
  if (motion === "to-assembly") pose = "from-layout";
  if (motion === "to-layout") pose = "from-assembly";
  const rowsOn = lr === "5" && state.empRows;
  const lrs = el("div", {
    class: `emp-lrs ${pose}`,
    role: "group",
    "aria-label": "Операция",
  });
  lrs.append(el("button", {
    type: "button",
    class: "emp-asm",
    "aria-pressed": lr === "5" ? "true" : "false",
    onclick: () => pickEmpLr("5"),
  }, "сборка"));
  const swap = el("div", { class: "emp-swap" });
  swap.append(el("button", {
    type: "button",
    class: "emp-rows",
    "aria-pressed": rowsOn ? "true" : "false",
    "aria-hidden": lr === "5" ? "false" : "true",
    tabindex: lr === "5" ? "0" : "-1",
    onclick: () => openProblemRows(),
  }, "проблемные ряды"));
  swap.append(el("button", {
    type: "button",
    class: "emp-lay",
    "aria-pressed": lr === "27" ? "true" : "false",
    onclick: () => pickEmpLr("27"),
  }, "раскладка"));
  lrs.append(swap);
  return lrs;
}

function problemLine(name, count, sum, tag) {
  const title = name || "не указан";
  return el(tag, { class: "prob-line", title: `${title} - ${fmtInt(count)} ${skipLabel(count)} - ${fmtRub(sum)}` },
    el("span", { class: "prob-name" }, title),
    el("span", { class: "prob-sep", "aria-hidden": "true" }, "-"),
    el("span", { class: "prob-count" }, `${fmtInt(count)} ${skipLabel(count)}`),
    el("span", { class: "prob-sep", "aria-hidden": "true" }, "-"),
    el("span", { class: "prob-sum" }, fmtRub(sum)),
  );
}

function problemTotals(items) {
  return (items || []).reduce((acc, item) => {
    acc.skips += Number(item.skips) || 0;
    acc.sum += Number(item.sum) || 0;
    return acc;
  }, { skips: 0, sum: 0 });
}

function problemHead(screen, sub, skips, sum) {
  if (sub) screen.append(el("div", { class: "sub" }, sub));
  screen.append(
    el("div", { class: "big-sum" }, fmtRub(sum)),
    el("p", { class: "hint prob-total" }, `${fmtInt(skips)} ${skipLabel(skips)}`),
  );
}

function problemBack(onclick) {
  return el("button", { type: "button", class: "back prob-back", onclick }, "← Назад");
}

function problemButton(name, count, sum, onclick) {
  const line = problemLine(name, count, sum, "button");
  line.type = "button";
  line.onclick = onclick;
  return line;
}

function problemMxBlank(blocks) {
  if (!state.rowsReady) return true;
  let named = 0;
  let blank = 0;
  for (const block of blocks || []) {
    for (const floor of block.floors || []) {
      for (const row of floor.rows || []) {
        named += 1;
        if (row.row === "не указан") blank += 1;
      }
    }
  }
  return named > 0 && blank === named;
}

function renderProblemRows(screen) {
  if (freeRangePending()) {
    screen.append(el("p", { class: "hint" }, "Выберите дату «По». Пока она пустая, данные не грузим."));
    return;
  }
  if (state.rowsError) {
    screen.append(el("p", { class: "box bad" }, state.rowsError));
    screen.append(el("button", {
      type: "button", class: "ghost",
      onclick: () => { state.rowsError = ""; loadProblemRows(); },
    }, "Повторить загрузку"));
    return;
  }
  if (state.rowsLoading || state.blocks == null) {
    screen.append(el("p", { class: "hint" }, "Загрузка рядов…"));
    return;
  }
  const blocks = state.blocks;
  const all = problemTotals(blocks);
  if (!blocks.length || all.skips <= 0) {
    screen.append(el("p", { class: "box warn" }, "Нет пропусков сборки за этот период."));
    return;
  }
  let block = blocks.find((item) => item.block === state.rowBlock);
  if (state.rowBlock && !block) {
    state.rowBlock = "";
    state.rowFloor = "";
    block = null;
  }
  const floor = block ? (block.floors || []).find((item) => item.floor === state.rowFloor) : null;
  if (state.rowFloor && !floor) state.rowFloor = "";
  if (!block) {
    problemHead(screen, "", all.skips, all.sum);
    if (problemMxBlank(blocks)) {
      screen.append(el("p", { class: "hint" }, "МХ пустое. В выгрузке нужна колонка «МХ» или «Место хранения» — загрузите период ещё раз."));
    }
    const list = el("div", { class: "prob-list" });
    for (const item of blocks) {
      list.append(problemButton(item.face || "не указан", item.skips, item.sum, () => {
        state.rowBlock = item.block;
        state.rowFloor = "";
        state.rowOpen = "";
        state.rowPage = 0;
        render();
      }));
    }
    screen.append(list);
    return;
  }
  if (!floor) {
    problemHead(screen, block.face || "не указан", block.skips, block.sum);
    screen.append(problemBack(() => {
      state.rowBlock = "";
      state.rowFloor = "";
      state.rowOpen = "";
      state.rowPage = 0;
      render();
    }));
    const list = el("div", { class: "prob-list" });
    for (const item of block.floors || []) {
      list.append(problemButton(item.face || "не указан", item.skips, item.sum, () => {
        state.rowFloor = item.floor;
        state.rowOpen = "";
        state.rowPage = 0;
        render();
      }));
    }
    screen.append(list);
    return;
  }
  const rows = floor.rows || [];
  problemHead(screen, floor.face || "не указан", floor.skips, floor.sum);
  screen.append(problemBack(() => {
    state.rowFloor = "";
    state.rowOpen = "";
    state.rowPage = 0;
    render();
  }));
  if (!rows.length) {
    screen.append(el("p", { class: "box warn" }, "Нет пропусков на этом этаже."));
    return;
  }
  const pages = empPages(rows.length);
  let pageIndex = state.rowPage;
  if (pageIndex >= pages.length) pageIndex = 0;
  if (pageIndex !== state.rowPage) state.rowPage = pageIndex;
  const page = pages[pageIndex];
  const list = el("div", { class: "prob-list" });
  for (const row of rows.slice(page.start, page.end)) {
    const name = String(row.row || "");
    const aisle = name.split(".")[2];
    const label = /^\d+$/.test(aisle || "") ? `ряд ${Number(aisle)}` : name;
    const open = state.rowOpen === name;
    const card = el("div", { class: open ? "prob-card is-open" : "prob-card" });
    const line = problemLine(label, row.skips, row.sum, "button");
    line.type = "button";
    line.setAttribute("aria-expanded", open ? "true" : "false");
    line.onclick = () => {
      state.rowOpen = open ? "" : name;
      render();
    };
    card.append(line);
    if (open) {
      const detail = el("div", { class: "prob-detail" });
      for (const place of row.places || []) {
        detail.append(problemLine(place.mx, place.skips, place.sum, "div"));
      }
      card.append(detail);
    }
    list.append(card);
  }
  screen.append(list);
  if (pages.length > 1) {
    const nav = el("nav", { class: "emp-pages", "aria-label": "Страницы рядов" });
    if (pageIndex > 0) {
      nav.append(el("button", {
        type: "button",
        class: "emp-page-arrow",
        "aria-label": "Предыдущая страница",
        onclick: () => showRowPage(pageIndex - 1),
      }, "←"));
    }
    nav.append(el("span", { class: "emp-page-tab", "aria-current": "page" }, String(pageIndex + 1)));
    if (pageIndex < pages.length - 1) {
      nav.append(el("button", {
        type: "button",
        class: "emp-page-arrow",
        "aria-label": "Следующая страница",
        onclick: () => showRowPage(pageIndex + 1),
      }, "→"));
    }
    screen.append(nav);
  }
}

function showRowPage(index) {
  if (state.rowPage === index) return;
  state.rowPage = index;
  state.rowOpen = "";
  render();
}

function renderEmployees(screen) {
  const pack = state.employees;
  const lr = state.empLr === "27" ? "27" : "5";
  const rowsMode = lr === "5" && state.empRows;
  const rows = pack ? (pack.rows || []) : [];
  const lrs = renderEmpOps(lr);
  const query = String(state.badgeQuery || "").replace(/\D/g, "");
  const shownLosses = pack ? pack.losses : 0;
  const shownSum = pack ? pack.sum : 0;
  const input = el("input", {
    id: "badge-search",
    type: "search",
    maxlength: "64",
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
      loadEmployees();
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
        loadEmployees();
      },
    }, "Сброс"));
  }
  screen.append(
    el("div", { class: "screen-head" }, el("h2", null, "Операция")),
    lrs,
  );
  if (!rowsMode) screen.append(form);
  settleEmpOps(lrs);
  if (rowsMode) {
    renderProblemRows(screen);
    loadProblemRows();
    return;
  }
  if (freeRangePending()) {
    screen.append(el("p", { class: "hint" }, "Выберите дату «По». Пока она пустая, данные не грузим."));
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
    el("div", { class: "sub" }, query ? `Найдено сотрудников: ${fmtInt(pack.total)}` : `Сотрудников: ${fmtInt(pack.total)}`),
    el("div", { class: "big-sum" }, fmtRub(shownSum)),
    el("div", { class: "emp-toolbar" },
      el("p", { class: "hint" }, `${fmtInt(shownLosses)} ${lossLabel(shownLosses)}`),
      empSortControl(),
    ),
  );
  if (!pack.operation_total) {
    screen.append(el("p", { class: "box warn" }, `Нет потерь LR ${lr} за этот период.`));
    return;
  }
  if (pack.badges_missing) {
    screen.append(el("p", { class: "hint" }, "Бейджики пустые. ID сотрудника берётся из колонки AC выгрузки. Если файл заливали без этой колонки — загрузите период ещё раз."));
  }
  if (!rows.length) {
    screen.append(el("p", { class: "box warn" }, "Такой бейджик не найден."));
    return;
  }
  const pageIndex = pack.page;
  const list = el("div", { class: "emp-list" });
  for (const row of rows) {
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
        el("span", {
          class: foundShareClass(row),
          title: foundShareTitle(row),
        }, row.found == null
          ? foundShareText(foundShare(row))
          : `${foundShareText(foundShare(row))} · ${fmtInt(row.found)} шт.`),
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
      card.append(renderEmpDetail(detail, lr));
    }
    list.append(card);
  }
  screen.append(list);
  if (pack.pages > 1) {
    const nav = el("nav", { class: "emp-pages has-catalog", "aria-label": "Страницы сотрудников" });
    if (pageIndex > 0) {
      nav.append(el("button", {
        type: "button",
        class: "emp-page-arrow",
        "aria-label": "Предыдущая страница",
        onclick: () => showEmpPage(pageIndex - 1),
      }, "←"));
    }
    nav.append(empPageCatalog(pageIndex, pack.pages));
    if (pageIndex < pack.pages - 1) {
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

function empMxNode(mx) {
  const text = String(mx || "").trim();
  return el(
    "span",
    { class: "emp-mx", title: text ? `МХ ${text}` : "МХ" },
    el("span", { class: "emp-mx-k" }, "мх"),
    el("span", { class: "emp-mx-v" }, text || "—"),
  );
}

function empOperationNode(status, label = "Статус перед списанием") {
  const code = String(status || "").trim().toUpperCase();
  return el("div", { class: "emp-operation", title: label },
    code ? el("button", {
      type: "button",
      class: "emp-operation-code",
      "aria-label": `${label}: ${code}. Открыть расшифровку`,
      "aria-controls": "info-statuses",
      onclick: () => openStatusInfo(code),
    }, code) : el("span", { title: "Нет данных в выгрузке" }, "—"),
  );
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

function renderEmpDetail(detail, lr) {
  const showMx = String(lr) === "5";
  const box = el("div", { class: showMx ? "emp-detail has-mx" : "emp-detail" });
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
  const missingMx = showMx && detail.items.every((item) => !String(item.mx || "").trim());
  for (const item of detail.items.slice(0, state.empDetailLimit)) {
    const row = el(
      "div",
      { class: "emp-item" },
      el("span", { class: "emp-sticker" }, item.sticker || "—"),
    );
    if (showMx) row.append(empMxNode(item.mx));
    row.append(item.found === true
      ? empOperationNode(item.status_after, "Статус после списания")
      : empOperationNode(showMx ? item.status_before : ""));
    row.append(empFoundNode(item.found), el("span", { class: "emp-sum" }, fmtRub(item.sum)));
    box.append(row);
  }
  if (detail.items.length > state.empDetailLimit) {
    box.append(el("button", {
      type: "button", class: "ghost",
      onclick: () => { state.empDetailLimit += 100; render(); },
    }, `Показать ещё · осталось ${detail.items.length - state.empDetailLimit}`));
  }
  if (missingMx) {
    box.append(el(
      "p",
      { class: "hint emp-mx-hint" },
      "МХ пустое. В выгрузке нужна колонка «МХ» или «Место хранения» — загрузите период ещё раз.",
    ));
  }
  return box;
}

const WRITEOFF_COLUMNS = [
  ["qty", "К-во все"], ["unposted_qty", "К-во без оприхода"],
  ["posted_qty", "Количество оприходованного товара в отчётном периоде"],
  ["sum", "Все списания"], ["unposted_sum", "Сумма без оприхода"],
  ["posted_sum", "Сумма оприходованного товара в отчётном периоде"],
  ["turnover_share", "Доля всех потерь (период), %"],
  ["posted_share", "Доля оприходованного товара в отчётном периоде, %"],
  ["net_turnover_share", "Доля за вычетом оприходованного товара в отчётном периоде, %"],
];

