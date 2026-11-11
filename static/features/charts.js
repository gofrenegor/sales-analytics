function pairPolar(cx, cy, radius, deg) {
  const rad = (deg * Math.PI) / 180;
  return [cx + radius * Math.cos(rad), cy + radius * Math.sin(rad)];
}

function pairPush(center, point, extra) {
  const vx = point[0] - center[0];
  const vy = point[1] - center[1];
  const length = Math.hypot(vx, vy) || 1;
  const gain = (length + extra) / length;
  return [center[0] + vx * gain, center[1] + vy * gain];
}

function pairFilletsFit(span, outer, inner, rho) {
  if (rho <= 0 || outer <= inner || rho >= outer - inner) return false;
  const outerC = outer - rho;
  const innerC = inner + rho;
  if (outerC <= rho || innerC <= 0) return false;
  const dO = (Math.asin(rho / outerC) * 180) / Math.PI;
  const dI = (Math.asin(rho / innerC) * 180) / Math.PI;
  if (span <= 2 * Math.max(dO, dI) + 0.4) return false;
  const reachO = outerC * Math.cos((dO * Math.PI) / 180);
  const reachI = innerC * Math.cos((dI * Math.PI) / 180);
  return reachO > reachI + 0.8;
}

function pairRaySide(cx, cy, outer, inner, angle, rho, end) {
  const outerC = outer - rho;
  const innerC = inner + rho;
  const dO = (Math.asin(rho / outerC) * 180) / Math.PI;
  const dI = (Math.asin(rho / innerC) * 180) / Math.PI;
  const sign = end ? -1 : 1;
  const angO = angle + sign * dO;
  const angI = angle + sign * dI;
  const reachO = outerC * Math.cos((dO * Math.PI) / 180);
  const reachI = innerC * Math.cos((dI * Math.PI) / 180);
  return {
    angO,
    angI,
    cO: pairPolar(cx, cy, outerC, angO),
    cI: pairPolar(cx, cy, innerC, angI),
    o: pairPolar(cx, cy, outer, angO),
    i: pairPolar(cx, cy, inner, angI),
    radO: pairPolar(cx, cy, reachO, angle),
    radI: pairPolar(cx, cy, reachI, angle),
  };
}

function pairCenterline(cx, cy, outer, inner, a0, a1, rho0, rho1, dist) {
  const span = a1 - a0;
  if (span <= 0.4 || dist < 0 || inner - dist <= 1) return "";
  const limit = Math.min(8, Math.max(0, (outer - inner) / 2 - 1));
  const fit = (rho) => {
    const value = Math.min(Math.max(rho, 0), limit);
    if (value < 1.5) return 0;
    if (pairFilletsFit(span, outer, inner, value) && pairFilletsFit(span, outer + dist, inner - dist, value + dist)) return value;
    return 0;
  };
  rho0 = fit(rho0);
  rho1 = fit(rho1);
  if (Math.min(rho0, rho1) < 1.5) return "";
  const start = pairRaySide(cx, cy, outer, inner, a0, rho0, false);
  const end = pairRaySide(cx, cy, outer, inner, a1, rho1, true);
  const origin = [cx, cy];
  const o0 = pairPush(origin, start.o, dist);
  const o1 = pairPush(origin, end.o, dist);
  const i0 = pairPush(start.cI, start.i, dist);
  const i1 = pairPush(end.cI, end.i, dist);
  const tO0 = pairPush(start.cO, start.radO, dist);
  const tI0 = pairPush(start.cI, start.radI, dist);
  const tO1 = pairPush(end.cO, end.radO, dist);
  const tI1 = pairPush(end.cI, end.radI, dist);
  const sweepOf = (center, p0, p1) => {
    const cross = (p0[0] - center[0]) * (p1[1] - center[1]) - (p0[1] - center[1]) * (p1[0] - center[0]);
    return cross > 0 ? 1 : 0;
  };
  const num = (value) => value.toFixed(2);
  const arc = (radius, large, sweep, point) => `A ${num(radius)} ${num(radius)} 0 ${large} ${sweep} ${num(point[0])} ${num(point[1])}`;
  const pt = (point) => `${num(point[0])} ${num(point[1])}`;
  const largeO = end.angO - start.angO > 180 ? 1 : 0;
  const largeI = end.angI - start.angI > 180 ? 1 : 0;
  return [
    `M ${pt(o0)}`,
    arc(outer + dist, largeO, 1, o1),
    arc(rho1 + dist, 0, sweepOf(end.cO, o1, tO1), tO1),
    `L ${pt(tI1)}`,
    arc(rho1 + dist, 0, sweepOf(end.cI, tI1, i1), i1),
    arc(inner - dist, largeI, 0, i0),
    arc(rho0 + dist, 0, sweepOf(start.cI, i0, tI0), tI0),
    `L ${pt(tO0)}`,
    arc(rho0 + dist, 0, sweepOf(start.cO, tO0, o0), o0),
    "Z",
  ].join(" ");
}

function layoutPairOutlines(root) {
  const nodes = [];
  if (root && root.matches && root.matches(".loss-donut")) nodes.push(root);
  else {
    const host = root && root.querySelectorAll ? root : document;
    host.querySelectorAll(".loss-ring-blocks .loss-donut").forEach((svg) => nodes.push(svg));
  }
  nodes.forEach((svg) => {
    const width = svg.getBoundingClientRect().width;
    if (width < 8) return;
    const dist = (PAIR_GAP_PX + PAIR_STROKE_PX / 2) / (width / 400);
    svg.querySelectorAll(".loss-pair").forEach((path) => {
      const d = pairCenterline(
        200, 200, 137, 87,
        Number(path.dataset.a0), Number(path.dataset.a1),
        Number(path.dataset.rho0), Number(path.dataset.rho1),
        dist,
      );
      if (d) path.setAttribute("d", d);
    });
  });
}

function watchPairOutline(svg) {
  if (!svg) return;
  if (!svg.dataset.pairWatch && typeof ResizeObserver === "function") {
    svg.dataset.pairWatch = "1";
    const observer = new ResizeObserver(() => layoutPairOutlines(svg));
    svg._pairObserver = observer;
    observer.observe(svg);
  }
  layoutPairOutlines(svg);
}

function bindDonut(host) {
  host.querySelectorAll("[data-flow]").forEach((link) => {
    link.addEventListener("click", (event) => {
      event.preventDefault();
      go({ view: "flow", flow: link.dataset.flow, group: "", block: "" });
    });
  });
  const blocks = [...host.querySelectorAll("[data-block]")];
  const measureShelf = (label) => {
    if (!label.isConnected) return;
    const shelf = label.querySelector(".loss-leader-shelf");
    if (!shelf) return;
    const widths = [...label.querySelectorAll(".loss-seg-detail tspan")].map((line) => line.getComputedTextLength());
    const width = Math.max(0, ...widths);
    if (width > 0) label.style.setProperty("--shelf-scale", String(width / Number(shelf.dataset.width)));
  };
  blocks.filter((node) => node.matches(".loss-seg-label-link")).forEach(measureShelf);
  if (document.fonts) document.fonts.ready.then(() => {
    blocks.filter((node) => node.isConnected && node.matches(".loss-seg-label-link")).forEach(measureShelf);
  });
  const paint = (name, on) => {
    host.querySelectorAll(".loss-ring-blocks .loss-donut").forEach((svg) => {
      if (!on && svg.dataset.hotBlock !== name) return;
      svg.dataset.hotBlock = on ? name : "";
      svg.classList.toggle("has-hot-block", on);
      svg.querySelectorAll("[data-block]").forEach((node) => {
        if (on && node.dataset.block === name && node.matches(".loss-seg-label-link")) measureShelf(node);
        node.classList.toggle("is-hot", on && node.dataset.block === name);
      });
      svg.querySelectorAll(".loss-block-arc").forEach((arc) => {
        arc.classList.toggle("is-hot", on && arc.dataset.blockArc === name);
      });
    });
  };
  blocks.forEach((link) => {
    link.addEventListener("click", (event) => {
      event.preventDefault();
      go({ view: "block_loss", block: link.dataset.block, slice: "blocks", flow: "", group: "" });
    });
    link.addEventListener("mouseenter", () => {
      if (window.matchMedia("(hover: hover) and (pointer: fine)").matches) paint(link.dataset.block, true);
    });
    link.addEventListener("mouseleave", (event) => {
      if (event.relatedTarget?.closest?.("[data-block]")?.dataset.block !== link.dataset.block) {
        paint(link.dataset.block, false);
      }
    });
    link.addEventListener("focus", () => paint(link.dataset.block, true));
    link.addEventListener("blur", () => paint(link.dataset.block, false));
  });
  watchPairOutline(host.querySelector(".loss-ring-blocks .loss-donut"));
}

function seriesColors() {
  const theme = document.documentElement.dataset.theme;
  const light = theme === "light" || (!theme && window.matchMedia("(prefers-color-scheme: light)").matches);
  return light
    ? { current: "#0969da", previous: "#8b95a5", grid: "rgba(15,23,42,0.12)", axis: "#667085" }
    : { current: "#4C9AFF", previous: "#8b95a5", grid: "rgba(128,128,128,0.16)", axis: "#7d8794" };
}

const LOSS_STEP = 50000;

function niceMax(value) {
  const amount = Math.max(0, Math.round(Number(value) || 0));
  if (amount <= 0) return LOSS_STEP;
  return Math.ceil(amount / LOSS_STEP) * LOSS_STEP;
}

function seriesPeak(rows) {
  let peak = 0;
  for (const row of Array.isArray(rows) ? rows : []) {
    const sum = Number(row && row.sum) || 0;
    if (sum > peak) peak = sum;
  }
  return peak;
}

function drawLossAxis(svg, maxY, y, x1, x2, color, anchorX, font) {
  for (const mark of [0, 0.5, 1]) {
    const value = maxY * mark;
    const yy = y(value);
    svg.append(svgEl("line", {
      x1,
      x2,
      y1: yy,
      y2: yy,
      stroke: color,
      "stroke-dasharray": mark === 0 ? "0" : "3 4",
    }));
    if (mark === 0.5) continue;
    const label = svgEl("text", {
      x: anchorX,
      y: yy,
      "text-anchor": "end",
      "dominant-baseline": "middle",
      "font-size": String(font),
    });
    label.textContent = fmtInt(value);
    svg.append(label);
  }
}

function svgEl(name, attrs) {
  const node = document.createElementNS(NS, name);
  for (const [key, value] of Object.entries(attrs || {})) node.setAttribute(key, value);
  return node;
}

// The API supplies server-rendered SVG for the donut charts. Keep the
// renderer, but sanitize the markup before it crosses into the live DOM.
function safeSvgFragment(markup) {
  const template = document.createElement("template");
  template.innerHTML = String(markup || "");
  const allowed = new Set([
    "svg", "g", "path", "line", "circle", "text", "tspan", "polyline", "polygon", "rect", "title", "clippath", "clipPath",
    "a", "div", "span", "p",
  ]);
  const attrs = /^(aria-hidden|aria-label|class|clip-path|clippathunits|cx|cy|d|fill|fill-opacity|font-size|height|href|id|opacity|pathlength|pointer-events|preserveaspectratio|r|role|stroke|stroke-dasharray|stroke-dashoffset|stroke-linecap|stroke-linejoin|stroke-width|style|text-anchor|transform|viewbox|visibility|width|x|x1|x2|y|y1|y2|data-[a-z0-9_-]+)$/i;
  for (const node of [...template.content.querySelectorAll("*")]) {
    if (!node.isConnected) continue;
    if (!allowed.has(node.localName)) {
      node.replaceWith(...node.childNodes);
      continue;
    }
    for (const attribute of [...node.attributes]) {
      const name = attribute.name.toLowerCase();
      const value = attribute.value;
      const unsafe = !attrs.test(name)
        || name.startsWith("on")
        || /(?:javascript:|data:text\/html)/i.test(value)
        || (/url\s*\(/i.test(value) && !/url\s*\(\s*#/i.test(value));
      const badHref = name === "href" && !/^#[^"'<>\s]*$/.test(value);
      const badStyle = name === "style" && !/^transform-origin:\s*[\d.]+px\s+[\d.]+px;?$/i.test(value.trim());
      if (unsafe || badHref || badStyle) node.removeAttribute(attribute.name);
    }
  }
  return template.content;
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
  drawLossAxis(svg, maxY, y, pad.l, width - pad.r, colors.grid, pad.l - 10, 12);
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

  let tipDay = null;
  let tipFrame = 0;
  let pointer = null;
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
    if (tipDay !== best) {
      tipDay = best;
      tip.replaceChildren();
      const body = dayTip(rows, best);
      if (body) tip.append(body);
    }
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
  hit.addEventListener("pointermove", (event) => {
    pointer = { clientX: event.clientX, clientY: event.clientY };
    if (!tipFrame) tipFrame = requestAnimationFrame(() => {
      tipFrame = 0;
      if (svg.isConnected) showTip(pointer);
    });
  });
  hit.addEventListener("pointerleave", () => {
    cancelAnimationFrame(tipFrame);
    tipFrame = 0;
    tip.hidden = true;
    hideGuide();
  });

  const legend = el("div", { class: "legend" }, el("span", { class: "lg cur" }, "Текущий период"));
  if (previous.length) legend.append(el("span", { class: "lg prev" }, "Прошлый период"));
  return el("div", null, wrap, legend);
}

function blockTipNode() {
  const name = el("div", { class: "block-tip-name" });
  const when = el("div", { class: "block-tip-date" });
  const asmSum = el("b", { class: "block-tip-sum" });
  const laySum = el("b", { class: "block-tip-sum" });
  const tip = el(
    "div",
    { class: "chart-tip block-tip", role: "tooltip" },
    name,
    when,
    el(
      "div",
      { class: "block-tip-split" },
      el(
        "div",
        { class: "block-tip-side" },
        el("span", { class: "block-tip-k" }, "сборка"),
        el("span", { class: "block-tip-lr" }, "5 LR"),
        asmSum,
      ),
      el(
        "div",
        { class: "block-tip-side" },
        el("span", { class: "block-tip-k" }, "раскладка"),
        el("span", { class: "block-tip-lr" }, "27 LR"),
        laySum,
      ),
    ),
  );
  tip._fill = (line, point) => {
    name.textContent = line.face || line.name;
    when.textContent = point && point.date ? point.date : "";
    asmSum.textContent = fmtRub(point ? point.asm : 0);
    laySum.textContent = fmtRub(point ? point.lay : 0);
  };
  return tip;
}

let platPointerWatch = null;

function setPlatWatch(fn) {
  if (platPointerWatch) window.removeEventListener("pointermove", platPointerWatch);
  platPointerWatch = fn || null;
  if (platPointerWatch) window.addEventListener("pointermove", platPointerWatch);
}

let platKeyWatch = null;

function setPlatKey(fn) {
  if (platKeyWatch) window.removeEventListener("keydown", platKeyWatch);
  platKeyWatch = fn || null;
  if (platKeyWatch) window.addEventListener("keydown", platKeyWatch);
}

function splitChartPeriods(points) {
  const rows = Array.isArray(points) ? points : [];
  const byDay = (a, b) => a.day - b.day;
  return {
    current: rows.filter((row) => row.period === "Текущий период").sort(byDay),
    previous: rows.filter((row) => row.period === "Прошлый период").sort(byDay),
  };
}

function platTicks(maxDay) {
  const count = maxDay <= 8 ? maxDay : 5;
  if (count === maxDay) {
    const days = [];
    for (let day = 1; day <= maxDay; day += 1) days.push(day);
    return days;
  }
  const days = [];
  const last = count - 1;
  for (let i = 0; i < count; i += 1) days.push(1 + Math.round(((maxDay - 1) * i) / last));
  return days;
}

function renderPlatformChart(platform, grain, color, maxDay) {
  const hourly = grain === "hour";
  const axis = seriesColors();
  const height = 168;
  const width = 560;
  const pad = { l: 84, r: 10, t: 14, b: 22 };
  const innerW = width - pad.l - pad.r;
  const innerH = height - pad.t - pad.b;
  const maxY = niceMax(Math.max(seriesPeak(platform.current), seriesPeak(platform.previous)));
  const span = Math.max(maxDay - 1, 1);
  const x = (day) => pad.l + (maxDay === 1 ? innerW / 2 : ((day - 1) / span) * innerW);
  const y = (value) => pad.t + innerH - (value / maxY) * innerH;
  const svg = svgEl("svg", { viewBox: `0 0 ${width} ${height}`, class: "chart plat-chart", role: "img" });
  svg.setAttribute("aria-label", platform.name);
  drawLossAxis(svg, maxY, y, pad.l, width - pad.r, axis.grid, pad.l - 8, 12);
  const mid = svgEl("text", {
    x: pad.l - 8,
    y: y(maxY / 2),
    "text-anchor": "end",
    "dominant-baseline": "middle",
    "font-size": "12",
  });
  mid.textContent = fmtInt(maxY / 2);
  svg.append(mid);
  for (const day of platTicks(maxDay)) {
    const label = svgEl("text", {
      x: x(day),
      y: height - 6,
      "text-anchor": "middle",
      "font-size": "11",
    });
    label.textContent = hourly ? String(day - 1).padStart(2, "0") : String(day);
    svg.append(label);
  }
  const pathOf = (rows) => rows.map((row, index) => {
    const command = index ? "L" : "M";
    return `${command}${x(row.day).toFixed(1)} ${y(Number(row.sum) || 0).toFixed(1)}`;
  }).join(" ");
  if (platform.previous.length) {
    svg.append(svgEl("path", {
      d: pathOf(platform.previous),
      class: "plat-prev",
      fill: "none",
      stroke: "#9c9892",
      "stroke-width": "2.25",
      "stroke-linejoin": "round",
      "stroke-linecap": "round",
      "stroke-dasharray": "6 5",
    }));
  }
  const current = platform.current.length ? platform.current : [{ day: 1, sum: 0 }];
  if (!platform.current.length && maxDay > 1) {
    current.length = 0;
    for (let day = 1; day <= maxDay; day += 1) current.push({ day, sum: 0 });
  }
  svg.append(svgEl("path", {
    d: pathOf(current),
    class: "plat-now",
    fill: "none",
    stroke: color,
    "stroke-width": "2.25",
    "stroke-linejoin": "round",
    "stroke-linecap": "round",
  }));
  return svg;
}

function renderBlockChart(lines, grain) {
  setPlatWatch(null);
  setPlatKey(null);
  const hourly = grain === "hour";
  const axis = seriesColors();
  const height = 400;
  const width = 1280;
  const pad = { l: 96, r: 28, t: 18, b: 40 };
  const innerW = width - pad.l - pad.r;
  const innerH = height - pad.t - pad.b;
  const prepared = (lines || []).map((line) => {
    const points = Array.isArray(line.points) ? line.points : [];
    const parts = splitChartPeriods(points);
    const platforms = (Array.isArray(line.platforms) ? line.platforms : []).map((platform) => {
      const site = splitChartPeriods(platform.points);
      return {
        name: String(platform.name || ""),
        sum: Number(platform.sum) || 0,
        current: site.current,
        previous: site.previous,
      };
    }).filter((platform) => platform.name);
    return {
      name: String(line.name || ""),
      face: String(line.face || line.name || ""),
      color: line.color || axis.current,
      hot: line.hot || line.color || axis.current,
      asm: Number(line.asm) || 0,
      lay: Number(line.lay) || 0,
      current: parts.current,
      previous: parts.previous,
      platforms,
    };
  }).filter((line) => line.name && line.current.length);
  const days = prepared.flatMap((line) => [
    ...line.current.map((row) => row.day),
    ...line.previous.map((row) => row.day),
  ]);
  const maxDay = Math.max(1, ...days);
  const ticks = chartTickDays(maxDay);
  let minStep = ticks.length > 1 ? ticks[1] - ticks[0] : 1;
  for (let i = 2; i < ticks.length; i += 1) minStep = Math.min(minStep, ticks[i] - ticks[i - 1]);
  const span = Math.max(maxDay - 1, 1);
  const spacing = (innerW / span) * minStep;
  const digits = hourly ? 2 : String(maxDay).length;
  const fontPx = Math.min(12, (spacing * 0.84) / (digits * 0.72));
  const off = state.blockChartOff || {};
  const sums = prepared.filter((line) => !off[line.name]).flatMap((line) => [
    ...line.current.map((row) => row.sum),
    ...line.previous.map((row) => row.sum),
  ]);
  const maxY = niceMax(Math.max(0, ...sums));
  const x = (day) => pad.l + (maxDay === 1 ? innerW / 2 : ((day - 1) / (maxDay - 1)) * innerW);
  const y = (value) => pad.t + innerH - (value / maxY) * innerH;

  const svg = svgEl("svg", { viewBox: `0 0 ${width} ${height}`, class: "chart block-chart", role: "img" });
  svg.setAttribute("aria-label", hourly ? "Динамика потерь по блокам, часы" : "Динамика потерь по блокам");
  drawLossAxis(svg, maxY, y, pad.l, width - pad.r, axis.grid, pad.l - 10, 12);
  const mid = svgEl("text", {
    x: pad.l - 10,
    y: y(maxY / 2),
    "text-anchor": "end",
    "dominant-baseline": "middle",
    "font-size": "12",
  });
  mid.textContent = fmtInt(maxY / 2);
  svg.append(mid);
  for (const day of ticks) {
    const label = svgEl("text", { x: x(day), y: height - 12, "text-anchor": "middle", "font-size": fontPx.toFixed(2) });
    label.textContent = hourly ? String(day - 1).padStart(2, "0") : String(day);
    svg.append(label);
  }

  function toPts(series) {
    return series.map((row) => ({
      x: x(row.day),
      y: y(row.sum),
      day: row.day,
      date: row.date || "",
      asm: Number(row.asm) || 0,
      lay: Number(row.lay) || 0,
    }));
  }
  function pathOf(pts) {
    return pts.map((point, index) => `${index ? "L" : "M"}${point.x.toFixed(1)} ${point.y.toFixed(1)}`).join(" ");
  }
  const drawn = prepared.map((line) => ({
    ...line,
    currentPts: toPts(line.current),
    previousPts: toPts(line.previous),
  }));
  const shown = drawn.filter((line) => !off[line.name]);

  for (const line of shown) {
    if (!line.previousPts.length) continue;
    svg.append(svgEl("path", {
      d: pathOf(line.previousPts),
      class: "chart-line block-prev",
      "data-block": line.name,
      fill: "none",
      stroke: "#9c9892",
      "stroke-width": "2.25",
      "stroke-linejoin": "round",
      "stroke-linecap": "round",
      "stroke-dasharray": "6 5",
    }));
  }
  for (const line of shown) {
    if (!line.currentPts.length) continue;
    svg.append(svgEl("path", {
      d: pathOf(line.currentPts),
      class: "chart-line-glow",
      "data-block": line.name,
      fill: "none",
      stroke: line.hot,
      "stroke-width": "8",
      "stroke-linejoin": "round",
      "stroke-linecap": "round",
      opacity: "0",
    }));
  }
  for (const line of shown) {
    if (!line.currentPts.length) continue;
    const d = pathOf(line.currentPts);
    svg.append(svgEl("path", {
      d,
      class: "chart-line block-now",
      "data-block": line.name,
      fill: "none",
      stroke: line.color,
      "stroke-width": "2.25",
      "stroke-linejoin": "round",
      "stroke-linecap": "round",
    }));
    const dots = svgEl("g", { class: "chart-dots", "data-block": line.name });
    for (const point of line.currentPts) {
      dots.append(svgEl("circle", { cx: point.x, cy: point.y, r: "3.2", fill: line.color }));
    }
    svg.append(dots);
  }

  const hit = svgEl("rect", { x: pad.l, y: pad.t, width: innerW, height: innerH, fill: "transparent" });
  svg.append(hit);
  const tip = blockTipNode();
  const platRow = el("div", { class: "block-plats-row" });
  const periodName = el("span", { class: "block-period-name" });
  const periodSum = el("b", { class: "block-period-sum" });
  const period = el("div", { class: "block-period" }, periodName, periodSum);
  const platPanel = el(
    "div",
    { class: "block-plats", "aria-hidden": "true" },
    el("div", { class: "block-plats-clip" }, platRow),
    period,
  );
  const pinMark = el(
    "div",
    { class: "plat-pin-mark", "aria-hidden": "true" },
    (() => {
      const svgMark = svgEl("svg", { viewBox: "0 0 24 24", "aria-hidden": "true" });
      svgMark.append(svgEl("path", {
        d: "M6.2 12.5 10.1 16.3 17.8 8",
        fill: "none",
        stroke: "#fff",
        "stroke-width": "2.4",
        "stroke-linecap": "round",
        "stroke-linejoin": "round",
      }));
      return svgMark;
    })(),
  );
  const wrap = el("div", { class: "chart-wrap block-chart" });
  wrap.append(svg, platPanel, pinMark, tip);
  const legend = el("div", { class: "legend" });
  const box = el("div", { class: "block-chart-box" }, wrap, legend);
  for (const line of drawn) {
    const gone = Boolean(off[line.name]);
    const item = el("button", {
      type: "button",
      class: gone ? "lg block is-off" : "lg block",
      "data-block": line.name,
      "aria-pressed": gone ? "false" : "true",
      title: gone ? "Вернуть на график" : "Убрать с графика",
      onclick: () => {
        if (!state.blockChartOff) state.blockChartOff = {};
        if (state.blockChartOff[line.name]) delete state.blockChartOff[line.name];
        else state.blockChartOff[line.name] = true;
        box.replaceWith(renderBlockChart(lines, grain));
      },
    }, line.face || line.name);
    item.style.setProperty("--swatch", line.color);
    legend.append(item);
  }

  let hotName = "";
  let platArm = true;
  let platOpen = null;
  let platPinned = false;
  let pointerXY = null;
  const stroke = 2.25;
  const lineEnterPx = 4;
  const focusAwayPx = 22;
  const platAwayPx = 40;

  function nearestLine(line, local) {
    const pts = line.currentPts;
    if (!pts.length) return null;
    if (pts.length === 1) {
      return { dist: Math.hypot(local.x - pts[0].x, local.y - pts[0].y), at: pts[0] };
    }
    let bestDist = Infinity;
    let bestAt = pts[0];
    for (let i = 0; i < pts.length - 1; i += 1) {
      const a = pts[i];
      const b = pts[i + 1];
      const dx = b.x - a.x;
      const dy = b.y - a.y;
      const len2 = dx * dx + dy * dy || 1;
      let t = ((local.x - a.x) * dx + (local.y - a.y) * dy) / len2;
      t = Math.max(0, Math.min(1, t));
      const px = a.x + t * dx;
      const py = a.y + t * dy;
      const dist = Math.hypot(local.x - px, local.y - py);
      if (dist < bestDist) {
        bestDist = dist;
        bestAt = { x: px, y: py };
      }
    }
    return { dist: bestDist, at: bestAt };
  }

  function nearest(local) {
    let best = null;
    let bestDist = Infinity;
    let bestAt = null;
    for (const line of shown) {
      const hit = nearestLine(line, local);
      if (!hit || hit.dist >= bestDist) continue;
      best = line;
      bestDist = hit.dist;
      bestAt = hit.at;
    }
    return { line: best, dist: bestDist, at: bestAt };
  }

  function syncWatch() {
    if (platOpen || hotName) setPlatWatch(onChartPointer);
    else setPlatWatch(null);
  }

  function paint(name) {
    const prev = hotName;
    hotName = name || "";
    if (prev !== hotName) syncWatch();
    box.classList.toggle("is-focus", Boolean(hotName));
    const nodes = [
      ...svg.querySelectorAll("[data-block]"),
      ...legend.querySelectorAll("[data-block]"),
    ];
    for (const node of nodes) {
      const on = node.dataset.block === hotName;
      node.classList.toggle("is-hot", on && !node.classList.contains("block-prev"));
      node.classList.toggle("is-on", on && node.classList.contains("block-prev"));
    }
  }

  let tipAt = null;

  function placeTip(at) {
    if (at) tipAt = at;
    if (!tipAt) return;
    const host = wrap.getBoundingClientRect();
    const anchor = svg.createSVGPoint();
    anchor.x = tipAt.x;
    anchor.y = tipAt.y;
    const screen = anchor.matrixTransform(svg.getScreenCTM());
    const width = tip.offsetWidth;
    const height = tip.offsetHeight;
    let left = screen.x - host.left + 14;
    if (left + width > host.width) left = Math.max(8, host.width - width - 8);
    if (left < 8) left = 8;
    const hero = wrap.closest(".hero");
    const donut = hero ? hero.querySelector(".loss-donut") : null;
    const donutBox = donut ? donut.getBoundingClientRect() : null;
    const lift = platPanel.getAttribute("aria-hidden") === "false" ? period.offsetHeight + 16 : 0;
    const ringBottom = donutBox ? donutBox.top + donutBox.height * 0.86 - lift : host.top - height - 36 - lift;
    let topAbs = host.top - height - 18 - lift;
    if (topAbs < ringBottom + 8) topAbs = ringBottom + 8;
    if (topAbs < 8) topAbs = 8;
    tip.style.left = `${left}px`;
    tip.style.top = `${topAbs - host.top}px`;
  }

  function nearestDate(line, local) {
    const pts = line.currentPts;
    let best = pts[0];
    let bestDist = Math.abs(local.x - best.x);
    for (let i = 1; i < pts.length; i += 1) {
      const dist = Math.abs(local.x - pts[i].x);
      if (dist < bestDist) {
        best = pts[i];
        bestDist = dist;
      }
    }
    return best;
  }

  function showLine(line, local) {
    const point = nearestDate(line, local);
    const opening = !tip.classList.contains("is-in") || hotName !== line.name;
    tip._fill(line, point);
    tip.hidden = false;
    if (opening && !reducedMotion()) {
      tip.classList.remove("is-in");
      void tip.offsetWidth;
    }
    placeTip(point);
    tip.classList.add("is-in");
    if (hotName !== line.name) paint(line.name);
  }

  function hideLine() {
    tip.classList.remove("is-in");
    paint("");
  }

  function localOf(event) {
    const ctm = svg.getScreenCTM();
    if (!ctm) return null;
    const point = svg.createSVGPoint();
    point.x = event.clientX;
    point.y = event.clientY;
    return { local: point.matrixTransform(ctm.inverse()), scale: Math.hypot(ctm.a, ctm.b) || 1 };
  }

  function lineAt(event) {
    const mapped = localOf(event);
    if (!mapped) return null;
    const found = nearest(mapped.local);
    const enter = stroke / 2 + lineEnterPx / mapped.scale;
    if (!found.line || found.dist > enter) return null;
    return found.line;
  }

  function fillPlats(line) {
    const list = line.platforms || [];
    platRow.dataset.count = String(list.length);
    platRow.replaceChildren();
    periodName.textContent = line.face || line.name;
    periodSum.textContent = fmtRub((Number(line.asm) || 0) + (Number(line.lay) || 0));
    period.style.setProperty("--swatch", line.color);
    for (const platform of list) {
      const card = el(
        "figure",
        { class: "block-plat" },
        el(
          "figcaption",
          { class: "block-plat-name" },
          el("span", null, platform.name),
        ),
        el(
          "div",
          { class: "block-plat-plot" },
          el("b", { class: "block-plat-sum" }, fmtRub(platform.sum)),
          renderPlatformChart(platform, grain, line.color, maxDay),
        ),
      );
      card.style.setProperty("--swatch", line.color);
      platRow.append(card);
    }
  }

  function shutPlats(event) {
    const closed = platOpen ? platOpen.name : "";
    platOpen = null;
    platPinned = false;
    syncWatch();
    platPanel.classList.remove("is-in");
    platPanel.setAttribute("aria-hidden", "true");
    placeTip();
    const next = event ? lineAt(event) : null;
    platArm = !next || next.name !== closed;
  }

  function openPlats(line, event) {
    if (!line.platforms || !line.platforms.length) return;
    if (platOpen && platOpen.name === line.name && platPanel.classList.contains("is-in")) return;
    platOpen = { name: line.name, x: event.clientX, y: event.clientY };
    platArm = false;
    fillPlats(line);
    platPanel.setAttribute("aria-hidden", "false");
    syncWatch();
    placeTip();
    if (reducedMotion()) {
      platPanel.classList.add("is-in");
      return;
    }
    platPanel.classList.remove("is-in");
    void platPanel.offsetWidth;
    platPanel.classList.add("is-in");
  }

  function onPlatMove(event) {
    if (!platOpen || platPinned) return;
    const away = Math.hypot(event.clientX - platOpen.x, event.clientY - platOpen.y);
    if (away >= platAwayPx) shutPlats(event);
  }

  function dropPinIfAway() {
    if (platPinned || !platOpen || !pointerXY) return;
    const away = Math.hypot(pointerXY.x - platOpen.x, pointerXY.y - platOpen.y);
    if (away >= platAwayPx) shutPlats({ clientX: pointerXY.x, clientY: pointerXY.y });
  }

  function playPinMark(away) {
    pinMark.classList.remove("is-in", "is-away");
    if (reducedMotion()) return;
    void pinMark.offsetWidth;
    pinMark.classList.add(away ? "is-away" : "is-in");
  }

  function typingField() {
    const node = document.activeElement;
    if (!node) return false;
    if (node.isContentEditable) return true;
    return node.tagName === "INPUT" || node.tagName === "TEXTAREA" || node.tagName === "SELECT";
  }

  function onPinKey(event) {
    if (!box.isConnected) {
      setPlatKey(null);
      return;
    }
    if (event.repeat || event.key !== "Shift") return;
    if (event.metaKey || event.ctrlKey || event.altKey) return;
    if (!platOpen || typingField()) return;
    platPinned = !platPinned;
    playPinMark(!platPinned);
    if (!platPinned) dropPinIfAway();
  }

  function releaseFocus(event) {
    if (!hotName) return;
    const mapped = localOf(event);
    if (!mapped) return;
    const hot = shown.find((line) => line.name === hotName);
    const still = hot ? nearestLine(hot, mapped.local) : null;
    const leave = stroke / 2 + focusAwayPx / mapped.scale;
    if (still && still.dist <= leave) return;
    if (lineAt(event)) return;
    hideLine();
  }

  function onChartPointer(event) {
    pointerXY = { x: event.clientX, y: event.clientY };
    onPlatMove(event);
    releaseFocus(event);
  }

  function onMove(event) {
    const mapped = localOf(event);
    if (!mapped) return;
    onChartPointer(event);
    const found = nearest(mapped.local);
    const enter = stroke / 2 + lineEnterPx / mapped.scale;
    const leave = stroke / 2 + focusAwayPx / mapped.scale;
    const inZone = Boolean(found.line && found.dist <= enter);
    if (!inZone && !platOpen) platArm = true;
    if (inZone) {
      if (!platOpen && platArm) openPlats(found.line, event);
      else if (platOpen && platOpen.name !== found.line.name) openPlats(found.line, event);
      showLine(found.line, mapped.local);
      return;
    }
    if (hotName) {
      const hot = shown.find((line) => line.name === hotName);
      const still = hot ? nearestLine(hot, mapped.local) : null;
      if (still && still.dist <= leave) {
        showLine(hot, mapped.local);
        return;
      }
      hideLine();
    }
  }

  const fine = window.matchMedia("(hover: hover) and (pointer: fine)").matches;
  if (fine) {
    wrap.addEventListener("pointermove", onMove);
  } else {
    wrap.addEventListener("pointerup", (event) => {
      const mapped = localOf(event);
      if (!mapped) return;
      const found = nearest(mapped.local);
      const reach = stroke / 2 + 24 / mapped.scale;
      if (!found.line || found.dist > reach || found.line.name === hotName) {
        hideLine();
        shutPlats(event);
        platArm = true;
        return;
      }
      openPlats(found.line, event);
      showLine(found.line, mapped.local);
    });
  }
  setPlatKey(onPinKey);

  return box;
}

