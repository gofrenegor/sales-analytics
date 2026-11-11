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

function foundShare(row) {
  const losses = Number(row && row.losses) || 0;
  if (!row || row.found == null || losses <= 0) return null;
  return (Number(row.found) / losses) * 100;
}

function foundShareShown(pct) {
  if (pct == null || Number.isNaN(pct)) return null;
  if (pct > 0 && pct < 0.5) return 0;
  return Math.round(pct);
}

function foundShareText(pct) {
  const shown = foundShareShown(pct);
  if (shown == null) return "—";
  if (pct > 0 && pct < 0.5) return "<1%";
  return `${shown}%`;
}

function foundShareClass(row) {
  const shown = foundShareShown(foundShare(row));
  if (shown == null) return "emp-found-share is-empty";
  if (shown >= 70) return "emp-found-share is-green";
  if (shown >= 30) return "emp-found-share is-yellow";
  return "emp-found-share is-red";
}

function foundShareTitle(row) {
  if (!row || row.found == null) return "нет флага оприходования";
  return `найдено ${fmtInt(row.found)} из ${fmtInt(row.losses)}`;
}

function skipLabel(count) {
  const n = Math.abs(Number(count) || 0) % 100;
  const n1 = n % 10;
  if (n > 10 && n < 20) return "пропусков";
  if (n1 === 1) return "пропуск";
  if (n1 >= 2 && n1 <= 4) return "пропуска";
  return "пропусков";
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

