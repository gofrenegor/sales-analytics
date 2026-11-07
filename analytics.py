"""Правила списаний и сборка отчёта для сайта."""

import calendar
import csv
import json
import math
import os
import re
from datetime import date, datetime, timedelta
from html import escape
from pathlib import Path
from urllib.parse import quote
from zoneinfo import ZoneInfo

import pandas as pd
from dateutil.relativedelta import relativedelta

MODES = ("День", "Неделя", "Месяц", "Свободный")
FREE_MAX_DAYS = 366

INBOUND_STATUSES = [
    "ANV", "EPR", "IPS", "ORG", "PVP", "RGP", "RNP", "RSP", "RWP", "TWR",
    "WDC", "WWE", "WIJ", "WPT", "WPU", "SGR",
    "RNR", "SMS", "OSS",
    "MRD", "SLT",
]
OUTBOUND_STATUSES = [
    "GWS", "IKZ", "IPG", "ISB", "LAM", "LIO", "LIP", "LPG", "LSS", "OPS",
    "OSE", "OSR", "PAP", "PWT", "SAP", "SAS", "SBN", "SMC", "SPM", "SPS",
    "SSG", "WLI", "WLN", "WLR", "WLT", "WMI",
]
DEFECT_GROUP = "Брак"
DEFECT_STATUSES = ["SDL", "WSC", "WDV", "SDI", "WSR", "SGP", "DAP"]

STAGE_MAPPING = {
    "GWS": "Отгрузка", "LSS": "Отгрузка", "LIO": "Отгрузка", "LIP": "Отгрузка",
    "LPG": "Отгрузка", "SAP": "Отгрузка",
    "PAP": "Предсортировка", "PWT": "Предсортировка", "SBN": "Предсортировка",
    "SAS": "Предсортировка",
    "SMC": "Сортировка", "SPM": "Сортировка", "SPS": "Сортировка", "SSG": "Сортировка",
    "WLI": "Сортировка", "WLN": "Сортировка", "WLR": "Сортировка", "WLT": "Сортировка",
    "WMI": "Сортировка", "OPS": "Сортировка", "OSE": "Сортировка", "OSR": "Сортировка",
    "IKZ": "Сортировка", "IPG": "Сортировка", "ISB": "Сортировка",
    "LAM": "Пропуски сборки",
}
OPERKA_STATUSES = ["SMC", "SPS", "SAS", "SAP", "PAP", "LSS", "GWS", "WMI"]
ASSEMBLY_LR = ["5"]
# Лостризон важнее статуса: LR 29 всегда исход, даже на входном статусе и в браке.
OUTBOUND_LRS = ("29",)
# Кольцо блоков: в дырке и в процентах только сборка и раскладка.
BLOCK_RING_LRS = ("5", "27")
# LR 5 из выгрузки сохраняется всегда. Для остальных ORS — сборка, RNR — неразложка.
STATUS_FORCE_LR = {"ORS": "5", "RNR": "27"}
# Эти статусы не уходят в оперку и брак. Группа — название места списания.
# MRD и SLT — отставание входа: та же группа, без своей карточки.
PLACE_BY_BLOCK_ID = frozenset({"ORS", "RNR", "OSS", "MRD", "SLT"})
OPERKA_BLOCK_ID = "6800"
OUTBOUND_BLOCK = "Исход"
IGNORED_PLATFORMS = frozenset({"Рязань Тюшевское 5"})
PICKMISS_GROUP = "Пропуск сборки товара на блоке"
UNMAPPED_GROUP = "Блоки вне маппинга"
OWN_OFFICE_ID = "301760"

BLOCK_MAPPING = {
    "Рязань Тюшевское 1": "Рязань Тюшевское 1",
    "Рязань КГТ Левый": "Рязань Тюшевское 1",
    "Рязань Тюшевское 2": "Рязань Тюшевское 2",
    "Рязань Дорогостой 2": "Рязань Тюшевское 2",
    "Рязань Красота 2": "Рязань Тюшевское 2",
    "Рязань Продукты 2": "Рязань Тюшевское 2",
    "Рязань КГТ 2": "Рязань Тюшевское 2",
    "Рязань Тюшевское 3": "Рязань Тюшевское 3",
    "Рязань Тюшевское 3 КГТ": "Рязань Тюшевское 3",
    "Рязань Тюшевское 3 тест": "Рязань Тюшевское 3",
    "Рязань 3 тест": "Рязань Тюшевское 3",
    "Рязань Тюшевское 4": "Рязань Тюшевское 4",
    "Рязань Тюшевское": OUTBOUND_BLOCK,
}

# Одно КГТ блока 3 приходит двумя именами. Второе стирается до маппинга.
PLATFORM_FOLD = {
    "Рязань 3 КГТ": "Рязань Тюшевское 3 КГТ",
}

ALL_BLOCKS = list(dict.fromkeys(BLOCK_MAPPING.values()))
BLOCK_RING_BLOCKS = tuple(f"Рязань Тюшевское {number}" for number in range(1, 5))
PLATFORMS = {b: [n for n, v in BLOCK_MAPPING.items() if v == b] for b in ALL_BLOCKS}

COLS = [
    "loss_date", "barcode", "block_name", "status_block_id", "loss_sum", "loss_qty",
    "reason", "status_before_date", "status_before", "block_id", "office_id",
    "status_employee_id",
    "flow", "process_stage", "block_group",
]
# Колонка AC — сотрудник статуса перед списанием.
# На пропуске сборки это раскладчик или инвентаризатор, не сборщик.
EMPLOYEE_COL_INDEX = 28
EMPLOYEE_HEADERS = (
    "id сотрудника статуса перед списан",
    "id сотрудника статуса перед списанием",
    "id сотрудника",
)
# Пара колонок списания. AWH — блок, EMP — сотрудники.
# Сборщик — EMP, который не равен ID из колонки AC.
COUNTERPARTY_TYPE_HEADERS = (
    "тип контрагента",
    "тип контрагентов",
    "типы контрагентов",
)
COUNTERPARTY_ID_HEADERS = (
    "контрагент id",
    "контрагенты id",
    "id контрагента",
    "id контрагентов",
)
# AF — последний стикер до списания. Q — флаг оприходования (вещь получила движение).
STICKER_COL_INDEX = 31
STICKER_HEADERS = ("последний стикер до списания",)
POSTED_COL_INDEX = 16
POSTED_HEADERS = (
    "оприходование",
    "флаг оприходования",
    "признак оприходования",
    "оприходовано",
)
# МХ, с которого сборщик сделал пропуск. В деталке сборки, справа от стикера.
MX_HEADERS = (
    "мх",
    "место хранения",
    "название мх",
    "наименование мх",
    "код мх",
    "мх перед списанием",
    "место хранения перед списанием",
    "последнее мх",
    "последнее место хранения",
    "мх пропуска",
    "место пропуска",
)

GROUP_PROMPTS = {
    "stages": "Выберите этап для детализации:",
    "pickmiss": "Выберите площадку для детализации:",
    "statuses": "Выберите статус для детализации:",
    "platforms": "Выберите площадку для детализации:",
}

FLOW_IN_COLOR = "#3fb950"
FLOW_OUT_COLOR = "#4C9AFF"
# Круг сегментов по порядку блока. Семи цветов хватает на все группы склада, сосед не повторяет крупный.
BLOCK_DONUT_COLORS = (
    FLOW_OUT_COLOR,
    FLOW_IN_COLOR,
    "#c4b5fd",
    "#d29922",
    "#ff7b72",
    "#39c5cf",
    "#f778ba",
)
DONUT_CORNER = 8
# Угол дуги на экране. Истинный процент остаётся в подписи, тонкая доля всё ещё нажимается.
BLOCK_MIN_DEG = 12.0


def business_today():
    """«Сегодня» для обрезки недели и месяца. По умолчанию — календарь Москвы."""
    raw = os.environ.get("APP_TODAY", "").strip()
    if raw:
        return date.fromisoformat(raw)
    tz_name = os.environ.get("APP_TIMEZONE", "Europe/Moscow")
    return datetime.now(ZoneInfo(tz_name)).date()


def _f(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    if math.isnan(number) or math.isinf(number):
        return 0.0
    return number


def fmt_rub(value):
    return f"{_f(value):,.0f} ₽".replace(",", " ")


def get_delta_text(curr_sum, curr_qty, prev_sum, prev_qty):
    """Дельта в рублях, процентах и штуках."""
    curr_sum, curr_qty = _f(curr_sum), _f(curr_qty)
    prev_sum, prev_qty = _f(prev_sum), _f(prev_qty)
    diff_sum, diff_qty = curr_sum - prev_sum, curr_qty - prev_qty
    sign = "+" if diff_sum > 0 else ""
    pct = f" ({sign}{diff_sum / prev_sum * 100:.1f}%)" if prev_sum > 0 else ""
    sign_q = "+" if diff_qty > 0 else ""
    return f"{sign}{diff_sum:,.0f} ₽{pct} / {sign_q}{diff_qty:,.0f} шт".replace(",", " ")


def _fmt_share(pct):
    if pct <= 0:
        return "0%"
    if pct < 0.1:
        return "<\u00a00.1%"
    return f"{pct:.1f}%"


def _donut_arc(cx, cy, r, sw, start_deg, span_deg, color, css_class="loss-arc-seg"):
    """Дуга кольца. 0° — 3 часа, дальше по часовой."""
    if span_deg <= 0.4:
        return ""
    draw = ' pathLength="1"' if css_class.startswith("loss-arc-seg") else ""
    if span_deg >= 359.2:
        return (
            f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="none" stroke="{color}" '
            f'stroke-width="{sw}" class="{css_class}"{draw} />'
        )
    end = start_deg + span_deg
    large = 1 if span_deg > 180 else 0
    x1 = cx + r * math.cos(math.radians(start_deg))
    y1 = cy + r * math.sin(math.radians(start_deg))
    x2 = cx + r * math.cos(math.radians(end))
    y2 = cy + r * math.sin(math.radians(end))
    return (
        f'<path d="M {x1:.2f} {y1:.2f} A {r:.2f} {r:.2f} 0 {large} 1 {x2:.2f} {y2:.2f}" '
        f'fill="none" stroke="{color}" stroke-width="{sw}" stroke-linecap="butt" class="{css_class}"{draw} />'
    )


def _polar(cx, cy, radius, deg):
    rad = math.radians(deg)
    return cx + radius * math.cos(rad), cy + radius * math.sin(rad)


# Зазор между заливкой и ближайшим краем штриха — в пикселях экрана.
# Толщина штриха в CSS. Штрих по центру линии, поэтому ось пути дальше на полтолщины.
PAIR_GAP_PX = 2.0
PAIR_STROKE_PX = 1.25


def _pair_dist(scale):
    """Сдвиг оси штриха от края заливки, в единицах viewBox."""
    scale = scale if scale and scale > 0 else 1.0
    return (PAIR_GAP_PX + PAIR_STROKE_PX / 2.0) / scale


def _pair_ref_scale():
    """Пиксели экрана на единицу viewBox у широкого кольца: 56rem × 1.06."""
    return (56 * 16 * 1.06) / 400


def _push(center, point, extra):
    vx, vy = point[0] - center[0], point[1] - center[1]
    length = math.hypot(vx, vy) or 1.0
    gain = (length + extra) / length
    return (center[0] + vx * gain, center[1] + vy * gain)


def _ray_side(cx, cy, outer, inner, angle, rho, end):
    """Центры скруглений и точки касания одной радиальной стороны доли."""
    outer_c = outer - rho
    inner_c = inner + rho
    d_o = math.degrees(math.asin(rho / outer_c))
    d_i = math.degrees(math.asin(rho / inner_c))
    sign = -1 if end else 1
    ang_o = angle + sign * d_o
    ang_i = angle + sign * d_i
    reach_o = outer_c * math.cos(math.radians(d_o))
    reach_i = inner_c * math.cos(math.radians(d_i))
    return {
        "d_o": d_o,
        "d_i": d_i,
        "ang_o": ang_o,
        "ang_i": ang_i,
        "c_o": _polar(cx, cy, outer_c, ang_o),
        "c_i": _polar(cx, cy, inner_c, ang_i),
        "o": _polar(cx, cy, outer, ang_o),
        "i": _polar(cx, cy, inner, ang_i),
        "rad_o": _polar(cx, cy, reach_o, angle),
        "rad_i": _polar(cx, cy, reach_i, angle),
    }


def _pair_centerline(cx, cy, outer, inner, a0, a1, rho0, rho1, dist):
    """Ось штриха: контур пары, сдвинутый наружу на dist.

    Углы повторяют скругление доли и уходят вместе с ним, поэтому штрих
    не срезает заливку. Общей стороны между блоками нет.
    """
    span = a1 - a0
    if span <= 0.4 or dist < 0 or inner - dist <= 1:
        return ""
    limit = min(DONUT_CORNER, max(0.0, (outer - inner) / 2 - 1))
    rho0 = min(max(rho0, 0.0), limit)
    rho1 = min(max(rho1, 0.0), limit)

    def fit(rho):
        if rho < 1.5:
            return 0.0
        if _fillets_fit(span, outer, inner, rho) and _fillets_fit(span, outer + dist, inner - dist, rho + dist):
            return rho
        return 0.0

    rho0, rho1 = fit(rho0), fit(rho1)
    if min(rho0, rho1) < 1.5:
        return ""

    start = _ray_side(cx, cy, outer, inner, a0, rho0, False)
    end = _ray_side(cx, cy, outer, inner, a1, rho1, True)
    origin = (cx, cy)
    o0 = _push(origin, start["o"], dist)
    o1 = _push(origin, end["o"], dist)
    i0 = _push(start["c_i"], start["i"], dist)
    i1 = _push(end["c_i"], end["i"], dist)
    t_o0 = _push(start["c_o"], start["rad_o"], dist)
    t_i0 = _push(start["c_i"], start["rad_i"], dist)
    t_o1 = _push(end["c_o"], end["rad_o"], dist)
    t_i1 = _push(end["c_i"], end["rad_i"], dist)

    def sweep_of(center, p0, p1):
        cross = (p0[0] - center[0]) * (p1[1] - center[1]) - (p0[1] - center[1]) * (p1[0] - center[0])
        return 1 if cross > 0 else 0

    def arc(radius, large, sweep, point):
        return f"A {radius:.2f} {radius:.2f} 0 {large} {sweep} {point[0]:.2f} {point[1]:.2f}"

    def pt(point):
        return f"{point[0]:.2f} {point[1]:.2f}"

    large_o = 1 if end["ang_o"] - start["ang_o"] > 180 else 0
    large_i = 1 if end["ang_i"] - start["ang_i"] > 180 else 0
    return " ".join([
        f"M {pt(o0)}",
        arc(outer + dist, large_o, 1, o1),
        arc(rho1 + dist, 0, sweep_of(end["c_o"], o1, t_o1), t_o1),
        f"L {pt(t_i1)}",
        arc(rho1 + dist, 0, sweep_of(end["c_i"], t_i1, i1), i1),
        arc(inner - dist, large_i, 0, i0),
        arc(rho0 + dist, 0, sweep_of(start["c_i"], i0, t_i0), t_i0),
        f"L {pt(t_o0)}",
        arc(rho0 + dist, 0, sweep_of(start["c_o"], t_o0, o0), o0),
        "Z",
    ])


def _pair_outline(cx, cy, r, sw, a0, a1, rho0, rho1, color, scale):
    """Тонкая рамка двух соседних долей. Штрих снаружи, в PAIR_GAP_PX от заливки."""
    outer, inner = r + sw / 2, r - sw / 2
    dist = _pair_dist(scale)
    d = _pair_centerline(cx, cy, outer, inner, a0, a1, rho0, rho1, dist)
    if not d:
        return ""
    return (
        f'<path class="loss-pair" fill="none" pointer-events="none" stroke="{color}" '
        f'data-a0="{a0:.4f}" data-a1="{a1:.4f}" data-rho0="{rho0:.4f}" data-rho1="{rho1:.4f}" '
        f'vector-effect="non-scaling-stroke" d="{d}" />'
    )


def _fillets_fit(span_deg, outer, inner, rho):
    if rho <= 0 or outer <= inner or rho >= outer - inner:
        return False
    outer_c = outer - rho
    inner_c = inner + rho
    if outer_c <= rho or inner_c <= 0:
        return False
    try:
        d_o = math.degrees(math.asin(rho / outer_c))
        d_i = math.degrees(math.asin(rho / inner_c))
    except ValueError:
        return False
    if span_deg <= 2 * max(d_o, d_i) + 0.4:
        return False
    reach_o = outer_c * math.cos(math.radians(d_o))
    reach_i = inner_c * math.cos(math.radians(d_i))
    return reach_o > reach_i + 0.8


def _corner_radius(span_deg, outer, inner):
    """Радиус скругления угла. На короткой дуге меньше, чтобы срезы не слиплись."""
    if _fillets_fit(span_deg, outer, inner, DONUT_CORNER):
        return DONUT_CORNER
    lo, hi, best = 0.0, DONUT_CORNER, 0.0
    for _ in range(20):
        mid = (lo + hi) / 2
        if _fillets_fit(span_deg, outer, inner, mid):
            best = mid
            lo = mid
        else:
            hi = mid
    return best if best >= 1.5 else 0.0


def _rounded_segment_path(cx, cy, outer, inner, start_deg, span_deg, rho):
    outer_c = outer - rho
    inner_c = inner + rho
    d_o = math.degrees(math.asin(rho / outer_c))
    d_i = math.degrees(math.asin(rho / inner_c))
    a0 = start_deg
    a1 = start_deg + span_deg
    reach_o = outer_c * math.cos(math.radians(d_o))
    reach_i = inner_c * math.cos(math.radians(d_i))
    o_start = _polar(cx, cy, outer, a0 + d_o)
    o_end = _polar(cx, cy, outer, a1 - d_o)
    c_o_end = _polar(cx, cy, outer_c, a1 - d_o)
    rad_o_end = _polar(cx, cy, reach_o, a1)
    rad_i_end = _polar(cx, cy, reach_i, a1)
    c_i_end = _polar(cx, cy, inner_c, a1 - d_i)
    i_end = _polar(cx, cy, inner, a1 - d_i)
    i_start = _polar(cx, cy, inner, a0 + d_i)
    c_i_start = _polar(cx, cy, inner_c, a0 + d_i)
    rad_i_start = _polar(cx, cy, reach_i, a0)
    rad_o_start = _polar(cx, cy, reach_o, a0)
    c_o_start = _polar(cx, cy, outer_c, a0 + d_o)

    def sweep_of(center, start, end):
        cross = (start[0] - center[0]) * (end[1] - center[1]) - (start[1] - center[1]) * (end[0] - center[0])
        return 1 if cross > 0 else 0

    def arc(radius, large, sweep, end):
        return f"A {radius:.2f} {radius:.2f} 0 {large} {sweep} {end[0]:.2f} {end[1]:.2f}"

    large_o = 1 if span_deg - 2 * d_o > 180 else 0
    large_i = 1 if span_deg - 2 * d_i > 180 else 0
    return " ".join([
        f"M {o_start[0]:.2f} {o_start[1]:.2f}",
        arc(outer, large_o, 1, o_end),
        arc(rho, 0, sweep_of(c_o_end, o_end, rad_o_end), rad_o_end),
        f"L {rad_i_end[0]:.2f} {rad_i_end[1]:.2f}",
        arc(rho, 0, sweep_of(c_i_end, rad_i_end, i_end), i_end),
        arc(inner, large_i, 0, i_start),
        arc(rho, 0, sweep_of(c_i_start, i_start, rad_i_start), rad_i_start),
        f"L {rad_o_start[0]:.2f} {rad_o_start[1]:.2f}",
        arc(rho, 0, sweep_of(c_o_start, rad_o_start, o_start), o_start),
        "Z",
    ])


def _donut_segment(cx, cy, r, sw, start_deg, span_deg, color, key, css_class="loss-arc-seg"):
    """Дуга кольца. Углы чуть срезает скруглённый контур, прорисовка та же."""
    arc = _donut_arc(cx, cy, r, sw, start_deg, span_deg, color, css_class)
    if span_deg >= 359.2 or not arc:
        return arc
    outer, inner = r + sw / 2, r - sw / 2
    rho = _corner_radius(span_deg, outer, inner)
    path = _rounded_segment_path(cx, cy, outer, inner, start_deg, span_deg, rho) if rho else ""
    if not path:
        return arc
    clip = f"loss-clip-{key}-__DONUT__"
    arc = arc.replace("class=", f'clip-path="url(#{clip})" class=', 1)
    return (
        f'<clipPath id="{clip}" clipPathUnits="userSpaceOnUse"><path d="{path}"/></clipPath>'
        f"{arc}"
    )


def flow_share_labels(in_sum, out_sum):
    in_sum = max(_f(in_sum), 0.0)
    out_sum = max(_f(out_sum), 0.0)
    total = in_sum + out_sum
    in_pct = (in_sum / total * 100) if total else 0.0
    return _fmt_share(in_pct), _fmt_share(100.0 - in_pct if total else 0.0)


def build_loss_donut_svg(in_sum, out_sum, center_text):
    """Кольцо потерь: в дырке сумма списаний, вход слева, исход справа."""
    in_sum = max(_f(in_sum), 0.0)
    out_sum = max(_f(out_sum), 0.0)
    total = in_sum + out_sum
    in_pct = (in_sum / total * 100) if total else 0.0
    out_pct = (100.0 - in_pct) if total else 0.0

    cx, cy, r, sw = 200, 200, 112, 50
    outer, inner, outset = r + sw / 2, r - sw / 2, 8
    hit_r = (outer + outset + inner) / 2
    hit_sw = outer + outset - inner
    if in_pct <= 2 and in_pct > 0 and out_pct > 0:
        gap = 1.0
    elif in_pct > 1 and out_pct > 1:
        gap = 4.0
    else:
        gap = 0.0

    def span_of(pct):
        raw = pct / 100 * 360
        if raw <= 0:
            return 0.0
        if gap and raw > gap + 1:
            return raw - gap
        return raw

    in_span, out_span = span_of(in_pct), span_of(out_pct)
    in_start = (180 - in_span / 2) % 360
    out_start = (0 - out_span / 2) % 360
    label = escape(center_text)
    aria = escape(
        f"Диаграмма потерь. Сумма списаний {center_text}. "
        f"Вход {_fmt_share(in_pct)}, исход {_fmt_share(out_pct)}."
    )
    in_lbl, out_lbl = flow_share_labels(in_sum, out_sum)
    in_share, out_share = escape(in_lbl), escape(out_lbl)
    arcs = (
        _donut_segment(cx, cy, r, sw, in_start, in_span, FLOW_IN_COLOR, "in", "loss-arc-seg arc-in")
        + _donut_segment(cx, cy, r, sw, out_start, out_span, FLOW_OUT_COLOR, "out", "loss-arc-seg arc-out")
    )

    def _seg_link(flow, start, span):
        arc = _donut_arc(cx, cy, hit_r, hit_sw, start, span, "transparent", "loss-seg-hit")
        if not arc:
            return ""
        name = "входящий поток" if flow == "in" else "исходящий поток"
        return (
            f'<a href="#flow={flow}" data-flow="{flow}" class="loss-seg loss-seg-{flow}" '
            f'aria-label="{escape(name)}"><title>{escape(name)}</title>{arc}</a>'
        )

    hits = _seg_link("in", in_start, in_span) + _seg_link("out", out_start, out_span)
    return f'''<div class="loss-stack">
<div class="loss-figure">
<div class="loss-ring-row">
<span class="loss-pct loss-pct-in">{in_share}</span>
<div class="loss-donut-wrap">
<svg class="loss-donut" viewBox="0 0 400 400" role="img" aria-label="{aria}">
  <circle class="loss-track" cx="{cx}" cy="{cy}" r="{r}" fill="none" stroke="rgba(128,128,128,0.16)" stroke-width="{sw}"/>
  {arcs}
  {hits}
</svg>
<p class="loss-hole" aria-hidden="true">
<span class="loss-hole-label">сумма списаний</span>
<span class="loss-hole-sum">{label}</span>
</p>
</div>
<span class="loss-pct loss-pct-out">{out_share}</span>
</div>
</div>
</div>'''


def build_block_lr_donut_svg(asm_sum, lay_sum, center_text):
    """Кольцо одного блока: слева сборка (LR 5), справа раскладка (LR 27).

    Геометрия и классы те же, что у входа и исхода. Сегменты не ссылки.
    Доли считаются от суммы этих двух LR внутри блока. Нулевая доля без дуги.
    """
    asm_sum = max(_f(asm_sum), 0.0)
    lay_sum = max(_f(lay_sum), 0.0)
    total = asm_sum + lay_sum
    asm_pct = (asm_sum / total * 100) if total else 0.0
    lay_pct = (100.0 - asm_pct) if total else 0.0

    cx, cy, r, sw = 200, 200, 112, 50
    if asm_pct <= 2 and asm_pct > 0 and lay_pct > 0:
        gap = 1.0
    elif asm_pct > 1 and lay_pct > 1:
        gap = 4.0
    else:
        gap = 0.0

    def span_of(pct):
        raw = pct / 100 * 360
        if raw <= 0:
            return 0.0
        if gap and raw > gap + 1:
            return raw - gap
        return raw

    asm_span, lay_span = span_of(asm_pct), span_of(lay_pct)
    asm_start = (180 - asm_span / 2) % 360
    lay_start = (0 - lay_span / 2) % 360
    label = escape(center_text)
    asm_share, lay_share = _fmt_share(asm_pct), _fmt_share(lay_pct)
    aria = escape(
        f"Диаграмма потерь. Сумма списаний {center_text}. "
        f"Сборка {asm_share}, раскладка {lay_share}."
    )
    arcs = (
        _donut_arc(cx, cy, r, sw, asm_start, asm_span, FLOW_OUT_COLOR, "loss-arc-seg arc-out")
        + _donut_arc(cx, cy, r, sw, lay_start, lay_span, FLOW_IN_COLOR, "loss-arc-seg arc-in")
    )

    def side_label(css, name, share):
        return (
            f'<span class="loss-pct {css}">'
            f'<span class="loss-pct-name">{escape(name)}</span>{escape(share)}'
            f"</span>"
        )

    return f'''<div class="loss-stack">
<div class="loss-figure">
<div class="loss-ring-row loss-ring-lr">
{side_label("loss-pct-in", "сборка", asm_share)}
<div class="loss-donut-wrap">
<svg class="loss-donut" viewBox="0 0 400 400" role="img" aria-label="{aria}">
  <circle class="loss-track" cx="{cx}" cy="{cy}" r="{r}" fill="none" stroke="rgba(128,128,128,0.16)" stroke-width="{sw}"/>
  {arcs}
</svg>
<p class="loss-hole" aria-hidden="true">
<span class="loss-hole-label">сумма списаний</span>
<span class="loss-hole-sum">{label}</span>
</p>
</div>
{side_label("loss-pct-out", "раскладка", lay_share)}
</div>
</div>
</div>'''


def _block_donut_color(index):
    """Цвет сегмента по порядку в кольце. Соседние блоки не делят один синий."""
    return BLOCK_DONUT_COLORS[index % len(BLOCK_DONUT_COLORS)]


_RING_NUMBERS = (1, 2, 3, 4)


def _ring_number(name):
    match = re.search(r"(\d+)\s*$", str(name or ""))
    if not match:
        return None
    number = int(match.group(1))
    return number if number in _RING_NUMBERS else None


def _block_face(name):
    """Подпись на кольце и в легенде графика: «блок 1», не «Рязань Тюшевское 1»."""
    number = _ring_number(name)
    if number is not None and str(name or "") in ALL_BLOCKS:
        return f"блок {number}"
    return str(name or "")


def _arrange_ring_items(items):
    """По кругу: блок 1, затем 2, затем 3, затем 4. Остальные — после, по сумме."""
    by_number = {}
    rest = []
    for item in items or []:
        number = _ring_number(item.get("name"))
        if number is not None and str(item.get("name") or "") in ALL_BLOCKS and number not in by_number:
            by_number[number] = item
        else:
            rest.append(item)
    ordered = [by_number[number] for number in _RING_NUMBERS if number in by_number]
    rest.sort(key=lambda item: (-_f(item.get("sum")), str(item.get("name") or "")))
    return ordered + rest


def _text_side(mid):
    cos_m = math.cos(math.radians(mid))
    sin_m = math.sin(math.radians(mid))
    if cos_m >= 0.35:
        anchor = "start"
    elif cos_m <= -0.35:
        anchor = "end"
    else:
        anchor = "middle"
    return anchor, cos_m, sin_m


def _text_box(x, y_name, y_pct, anchor, face, pct_text, font):
    width = max(len(face), len(pct_text), 1) * font * 0.58
    if anchor == "start":
        left, right = x, x + width
    elif anchor == "end":
        left, right = x - width, x
    else:
        left, right = x - width / 2, x + width / 2
    top = min(y_name, y_pct) - font
    # Низ рамки держит «у» под базовой линией имени.
    bottom = max(y_name, y_pct) + font * 0.35
    return (left, top, right, bottom)


def _boxes_hit(a, b, pad=4):
    return not (a[2] + pad < b[0] or b[2] + pad < a[0] or a[3] + pad < b[1] or b[3] + pad < a[1])


def _label_svg(x, y_name, y_pct, anchor, face, pct_text, color, font, pct_font):
    """Имя цветом доли. Процент мельче и стоит строкой выше, без смены заливки."""
    return (
        f'<text class="loss-seg-label" text-anchor="{anchor}" fill="{color}" '
        f'font-size="{font}" pointer-events="none">'
        f'<tspan class="loss-seg-pct" x="{x:.2f}" y="{y_pct:.2f}" font-size="{pct_font}">'
        f"{escape(pct_text)}</tspan>"
        f'<tspan x="{x:.2f}" y="{y_name:.2f}">{escape(face)}</tspan>'
        f"</text>"
    )


def _oklab_lift(color):
    """Чуть выше по светлоте OKLab, тон не уходит в серый."""
    raw = str(color or "").strip()
    if raw.startswith("#"):
        raw = raw[1:]
    if len(raw) == 3:
        raw = "".join(ch * 2 for ch in raw)
    if len(raw) != 6 or any(ch not in "0123456789abcdefABCDEF" for ch in raw):
        return color
    rgb = tuple(int(raw[index:index + 2], 16) for index in (0, 2, 4))

    def linear(channel):
        value = channel / 255.0
        if value <= 0.04045:
            return value / 12.92
        return ((value + 0.055) / 1.055) ** 2.4

    red, green, blue = (linear(channel) for channel in rgb)
    l = 0.4122214708 * red + 0.5363325363 * green + 0.0514459929 * blue
    m = 0.2119034982 * red + 0.6806995451 * green + 0.1073969566 * blue
    s = 0.0883024619 * red + 0.2817188376 * green + 0.6299787005 * blue
    l_, m_, s_ = (math.copysign(abs(v) ** (1.0 / 3.0), v) for v in (l, m, s))
    L = 0.2104542553 * l_ + 0.7936177850 * m_ - 0.0040720468 * s_
    A = 1.9779984951 * l_ - 2.4285922050 * m_ + 0.4505937099 * s_
    B = 0.0259040371 * l_ + 0.7827717662 * m_ - 0.8086757660 * s_
    L = min(0.92, L + 0.16)
    gain = 1.15
    A *= gain
    B *= gain
    l_ = L + 0.3963377774 * A + 0.2158037573 * B
    m_ = L - 0.1055613458 * A - 0.0638541728 * B
    s_ = L - 0.0894841775 * A - 1.2914855480 * B
    l = math.copysign(abs(l_) ** 3, l_)
    m = math.copysign(abs(m_) ** 3, m_)
    s = math.copysign(abs(s_) ** 3, s_)
    out = (
        4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s,
        -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s,
        -0.0041960863 * l - 0.7034186147 * m + 1.7076147010 * s,
    )

    def encode(channel):
        channel = max(0.0, channel)
        if channel <= 0.0031308:
            encoded = 12.92 * channel
        else:
            encoded = 1.055 * (channel ** (1.0 / 2.4)) - 0.055
        return max(0, min(255, round(encoded * 255)))

    return "#" + "".join(f"{encode(channel):02x}" for channel in out)


def _brighten(color):
    """Тот же тон, светлее. Обводка линии блока на графике."""
    return _oklab_lift(color)


def _darken(color, toward=0.2):
    """Тот же тон, примерно на 20% ближе к чёрному. Штрих темнее заливки текста."""
    raw = str(color or "").strip()
    if raw.startswith("#"):
        raw = raw[1:]
    if len(raw) == 3:
        raw = "".join(ch * 2 for ch in raw)
    if len(raw) != 6 or any(ch not in "0123456789abcdefABCDEF" for ch in raw):
        return color
    scale = 1.0 - toward
    channels = []
    for index in (0, 2, 4):
        channel = int(raw[index:index + 2], 16)
        channels.append(f"{max(0, min(255, round(channel * scale))):02x}")
    return "#" + "".join(channels)


def _mix_oklab(a, b):
    """Середина двух цветов в OKLab. Среднее в RGB мутит лавандовый с янтарным."""

    def channels(color):
        raw = str(color or "").strip()
        if raw.startswith("#"):
            raw = raw[1:]
        if len(raw) == 3:
            raw = "".join(ch * 2 for ch in raw)
        if len(raw) != 6 or any(ch not in "0123456789abcdefABCDEF" for ch in raw):
            return None
        return tuple(int(raw[index:index + 2], 16) for index in (0, 2, 4))

    left, right = channels(a), channels(b)
    if left is None or right is None:
        return a

    def linear(channel):
        value = channel / 255.0
        if value <= 0.04045:
            return value / 12.92
        return ((value + 0.055) / 1.055) ** 2.4

    def oklab(rgb):
        red, green, blue = (linear(channel) for channel in rgb)
        l = 0.4122214708 * red + 0.5363325363 * green + 0.0514459929 * blue
        m = 0.2119034982 * red + 0.6806995451 * green + 0.1073969566 * blue
        s = 0.0883024619 * red + 0.2817188376 * green + 0.6299787005 * blue
        l_, m_, s_ = (math.copysign(abs(v) ** (1.0 / 3.0), v) for v in (l, m, s))
        return (
            0.2104542553 * l_ + 0.7936177850 * m_ - 0.0040720468 * s_,
            1.9779984951 * l_ - 2.4285922050 * m_ + 0.4505937099 * s_,
            0.0259040371 * l_ + 0.7827717662 * m_ - 0.8086757660 * s_,
        )

    lab_a, lab_b = oklab(left), oklab(right)
    L = (lab_a[0] + lab_b[0]) / 2
    A = (lab_a[1] + lab_b[1]) / 2
    B = (lab_a[2] + lab_b[2]) / 2
    l_ = L + 0.3963377774 * A + 0.2158037573 * B
    m_ = L - 0.1055613458 * A - 0.0638541728 * B
    s_ = L - 0.0894841775 * A - 1.2914855480 * B
    l = math.copysign(abs(l_) ** 3, l_)
    m = math.copysign(abs(m_) ** 3, m_)
    s = math.copysign(abs(s_) ** 3, s_)
    rgb = (
        4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s,
        -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s,
        -0.0041960863 * l - 0.7034186147 * m + 1.7076147010 * s,
    )

    def encode(channel):
        if channel <= 0.0031308:
            encoded = 12.92 * channel
        else:
            encoded = 1.055 * (channel ** (1.0 / 2.4)) - 0.055
        return max(0, min(255, round(encoded * 255)))

    return "#" + "".join(f"{encode(channel):02x}" for channel in rgb)


def _leader_label(cx, cy, r, sw, mid, face, pct_text, color, direction, amount=0, qty=0):
    """Имя лежит на короткой полке. До полки линия изгибается в сторону.

    Полка горизонтальная и лишь на 8px длиннее имени. Подвод короткий:
    линия начинается в 2px от дуги, уходит по касательной и закругляется
    на стыке с полкой. Концы штриха круглые.
    Отступ полки от дуги одинаковый для всех долей. direction задаёт сторону
    горизонтальной полки: соседние подписи у вершины можно развести в стороны.
    Буквы с внешней стороны штриха, линия их не режет.
    """
    font = 11 if len(face) > 12 else 13
    pct_font = 10 if font > 12 else 9
    _, cos_m, sin_m = _text_side(mid)
    run_x = float(direction)
    anchor = "start" if run_x > 0 else "end"
    outer = r + sw / 2
    text_w = max(len(face), 1) * font * 0.58
    tangent_x, tangent_y = -sin_m, cos_m
    lead = 8.0
    side = 6.0 if tangent_x * run_x >= 0 else -6.0
    overhang = 8.0
    x0 = cx + (outer + 2.0) * cos_m
    y0 = cy + (outer + 2.0) * sin_m
    shelf_x = x0 + lead * cos_m + side * tangent_x
    shelf_y = y0 + lead * sin_m + side * tangent_y
    end_x = shelf_x + (text_w + overhang) * run_x
    end_y = shelf_y
    turn = 8.0
    c1x = x0 + (shelf_x - x0) * 0.55 + side * tangent_x
    c1y = y0 + (shelf_y - y0) * 0.55 + side * tangent_y
    c2x = shelf_x - run_x * turn
    c2y = shelf_y
    # Снаружи дуги: сверху имя лежит на линии, снизу линия остаётся над именем.
    # Процент всегда дальше от кольца, чем имя.
    gap = 3.0
    if shelf_y - cy > 0:
        y_name = shelf_y + gap + font * 0.75
        y_pct = y_name + pct_font + 2.0
    else:
        y_name = shelf_y - gap - font * 0.25
        y_pct = y_name - (pct_font + 2.0)
    x = shelf_x
    path = (
        f'<path class="loss-leader" fill="none" pointer-events="none" stroke="{_darken(color)}" '
        f'stroke-linecap="round" stroke-linejoin="round" '
        f'd="M {x0:.2f} {y0:.2f} C {c1x:.2f} {c1y:.2f} {c2x:.2f} {c2y:.2f} '
        f'{shelf_x:.2f} {shelf_y:.2f}" />'
        f'<line class="loss-leader loss-leader-shelf" stroke="{_darken(color)}" '
        f'x1="{shelf_x:.2f}" y1="{shelf_y:.2f}" x2="{end_x:.2f}" y2="{end_y:.2f}" '
        f'data-width="{text_w + overhang:.2f}" '
        f'style="transform-origin: {shelf_x:.2f}px {shelf_y:.2f}px" />'
    )
    quantity = f"{_f(qty):,.0f}".replace(",", " ") + " шт."
    detail = (
        f'<text class="loss-seg-detail" text-anchor="{anchor}" fill="{color}" font-size="{font}" '
        f'aria-hidden="true"><tspan x="{x:.2f}" y="{y_name:.2f}">{escape(fmt_rub(amount))}</tspan>'
        f'<tspan x="{x:.2f}" y="{y_pct:.2f}" font-size="{pct_font}">{escape(quantity)}</tspan></text>'
    )
    svg = path + _label_svg(x, y_name, y_pct, anchor, face, pct_text, color, font, pct_font) + detail
    return svg, _text_box(x, y_name, y_pct, anchor, face, pct_text, font)


def warehouse_block_items(curr):
    """Только блоки 1–4 и LR 5/27. Сумма и доли кольца считаются по ним."""
    columns = getattr(curr, "columns", [])
    if curr is None or len(curr) == 0 or "block_group" not in columns or "reason" not in columns:
        return []
    reason = curr["reason"].astype(str).str.replace(r"\.0$", "", regex=True).str.strip()
    part = curr.loc[reason.isin(BLOCK_RING_LRS) & curr["block_group"].isin(BLOCK_RING_BLOCKS)]
    if len(part) == 0:
        return []
    grouped = part.groupby("block_group", sort=False)["loss_sum"].sum()
    quantities = part.groupby("block_group", sort=False)["loss_qty"].sum() if "loss_qty" in part else pd.Series(dtype=float)
    sums = {}
    for key, value in grouped.items():
        name = _as_name(key).strip()
        amount = _f(value)
        if not name or amount <= 0 or name == DEFECT_GROUP:
            continue
        sums[name] = sums.get(name, 0.0) + amount
    ordered = [name for name in BLOCK_RING_BLOCKS if sums.get(name, 0.0) > 0]
    total = sum(sums[name] for name in ordered)
    if total <= 0:
        return []
    return [
        {
            "name": name,
            "face": _block_face(name),
            "sum": sums[name],
            "qty": _f(quantities.get(name, 0)),
            "pct": sums[name] / total * 100,
        }
        for name in ordered
    ]


def _block_display_spans(raws):
    """Угол дуги на кольце. Мелкая доля получает не меньше BLOCK_MIN_DEG, крупная забирает остаток."""
    n = len(raws)
    if n == 0:
        return []
    if n == 1:
        return [360.0]
    min_deg = min(BLOCK_MIN_DEG, 360.0 / n * 0.7)
    reserved = n * min_deg
    if reserved >= 359.0:
        return [360.0 / n] * n
    leftover = 360.0 - reserved
    total = sum(raws) or 1.0
    return [min_deg + leftover * (max(raw, 0.0) / total) for raw in raws]


def _block_gap(allocated):
    if allocated >= 28:
        return 3.0
    if allocated >= 8:
        return 1.4
    return 0.6


def build_blocks_donut_svg(items, center_text):
    """Кольцо всех блоков. У каждой доли кривая с именем, процент строкой выше.

    Пары 1–2 и 3–4 обведены тонкой рамкой, если оба блока пары лежат на кольце.
    """
    cx, cy, r, sw = 200, 200, 112, 50
    outer, inner, outset = r + sw / 2, r - sw / 2, 8
    hit_r = (outer + outset + inner) / 2
    hit_sw = outer + outset - inner
    rows = _arrange_ring_items(items)
    raws = [max(_f(item.get("pct")), 0.0) / 100 * 360 for item in rows]
    if len(raws) == 1:
        raws = [360.0]
    elif raws and abs(sum(raws) - 360.0) < 0.05:
        raws[-1] += 360.0 - sum(raws)
    allocated = _block_display_spans(raws)

    arcs, hits, specs = [], [], []
    drawn = {}
    cursor = -90.0
    for index, (item, raw) in enumerate(zip(rows, allocated)):
        raw = max(_f(raw), 0.0)
        gap = 0.0 if len(allocated) == 1 else _block_gap(raw)
        span = raw if len(allocated) == 1 else max(raw - gap, 0.0)
        start = cursor + (raw - span) / 2
        mid = cursor + raw / 2
        cursor += raw
        face = str(item.get("face") or "")
        name = str(item.get("name") or "")
        pct = _f(item.get("pct"))
        color = _block_donut_color(index)
        if span <= 0.4:
            continue
        number = _ring_number(name)
        if number is not None and number not in drawn and name in ALL_BLOCKS:
            drawn[number] = (start, start + span, color, _corner_radius(span, r + sw / 2, r - sw / 2))
        pct_text = _fmt_share(pct)
        caption = escape(f"{face}, {pct_text}, {fmt_rub(item.get('sum'))}, {_f(item.get('qty')):g} шт.")
        href = escape("#block=" + quote(name, safe=""))
        arcs.append(
            f'<g class="loss-block-arc" data-block-arc="{escape(name)}">'
            + _donut_segment(cx, cy, r, sw, start, span, color, f"b{index}") + '</g>'
        )
        hit = _donut_arc(cx, cy, hit_r, hit_sw, start, span, "transparent", "loss-seg-hit")
        if hit:
            hits.append(
                f'<a class="loss-seg" data-block="{escape(name)}" href="{href}" aria-label="{caption}">'
                f"{hit}</a>"
            )
        specs.append({
            "mid": mid,
            "face": face,
            "pct_text": pct_text,
            "color": color,
            "name": name,
            "href": href,
            "sum": _f(item.get("sum")),
            "qty": _f(item.get("qty")),
        })

    pairs = []
    for left, right in ((1, 2), (3, 4)):
        if left not in drawn or right not in drawn:
            continue
        a0, _, color_a, rho0 = drawn[left]
        _, a1, color_b, rho1 = drawn[right]
        pairs.append(_pair_outline(
            cx, cy, r, sw, a0, a1, rho0, rho1, _mix_oklab(color_a, color_b), _pair_ref_scale(),
        ))

    labels = []
    placed = []
    for spec in sorted(specs, key=lambda item: item["mid"]):
        chosen = None
        cos_m = math.cos(math.radians(spec["mid"]))
        direction = 1 if cos_m >= 0 else -1
        directions = [direction]
        # У вершин соседние короткие доли делят одну высоту. Разводим полки
        # влево и вправо, сохраняя радиальный отступ вместо выноса вверх/вниз.
        if abs(cos_m) < 0.35:
            directions.append(-direction)
        for direction in directions:
            text, box = _leader_label(
                cx, cy, r, sw, spec["mid"], spec["face"], spec["pct_text"], spec["color"], direction,
                spec["sum"], spec["qty"],
            )
            chosen = (text, box)
            if not any(_boxes_hit(box, other) for other in placed):
                break
        placed.append(chosen[1])
        labels.append(
            f'<a class="loss-seg-label-link" data-block="{escape(spec["name"])}" href="{spec["href"]}">{chosen[0]}</a>'
        )

    label = escape(center_text)
    described = ", ".join(
        f"{item.get('face')} {_fmt_share(_f(item.get('pct')))}" for item in rows if _f(item.get("pct")) > 0
    )
    aria = escape(
        f"Диаграмма потерь по блокам. Сумма списаний {center_text}."
        + (f" {described}." if described else "")
    )
    return f'''<div class="loss-stack">
<div class="loss-figure">
<div class="loss-ring-row loss-ring-blocks">
<div class="loss-donut-wrap">
<svg class="loss-donut" viewBox="0 0 400 400" role="img" aria-label="{aria}">
  <circle class="loss-track" cx="{cx}" cy="{cy}" r="{r}" fill="none" stroke="rgba(128,128,128,0.16)" stroke-width="{sw}"/>
  {''.join(arcs)}
  {''.join(pairs)}
  {''.join(hits)}
  {''.join(labels)}
</svg>
<p class="loss-hole" aria-hidden="true">
<span class="loss-hole-label">сумма списаний</span>
<span class="loss-hole-sum">{label}</span>
</p>
</div>
</div>
</div>
</div>'''


def _parse_day(value):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        raise ValueError("Некорректная дата") from None


def compute_free_period(start, end):
    """Свой интервал включительно. База сравнения не задаётся."""
    start = _parse_day(start)
    end = _parse_day(end)
    if end < start:
        raise ValueError("Конец периода раньше начала")
    days = (end - start).days + 1
    if days > FREE_MAX_DAYS:
        raise ValueError(f"Свободный разрез длиннее {FREE_MAX_DAYS} дней")
    fmt = lambda d: d.strftime("%d.%m.%Y")
    current = fmt(start) if days == 1 else f"{fmt(start)} — {fmt(end)}"
    return {
        "mode": "Свободный",
        "anchor": start.isoformat(),
        "start": start.isoformat(),
        "end": end.isoformat(),
        "prev_start": None,
        "prev_end": None,
        "days": days,
        "compare": False,
        "note": {"type": "free", "current": current, "days": days},
        "_start": start,
        "_end": end,
        "_prev_start": start,
        "_prev_end": start,
    }


def compute_period(mode, anchor, today):
    """Начало, конец и такой же по длине прошлый период. Неделя с понедельника."""
    anchor = _parse_day(anchor)
    if mode not in MODES or mode == "Свободный":
        raise ValueError("Неизвестный разрез")
    fmt = lambda d: d.strftime("%d.%m.%Y")

    if mode == "День":
        start = end = anchor
        p_start = p_end = anchor - timedelta(days=1)
    elif mode == "Неделя":
        start = anchor - timedelta(days=anchor.weekday())
        end = min(start + timedelta(days=6), today)
        if end < start:
            end = start
        p_start = start - timedelta(days=7)
        p_end = p_start + (end - start)
    else:
        start = anchor.replace(day=1)
        last = calendar.monthrange(anchor.year, anchor.month)[1]
        end = min(anchor.replace(day=last), today)
        if end < start:
            end = start
        p_start = start - relativedelta(months=1)
        p_last = start - timedelta(days=1)
        p_end = min(p_start + (end - start), p_last)

    days = (end - start).days + 1
    if mode == "День":
        note = {"type": "day", "current": fmt(start), "previous": fmt(p_start), "days": 1}
    else:
        note = {
            "type": "range",
            "current": f"{fmt(start)} — {fmt(end)}",
            "previous": f"{fmt(p_start)} — {fmt(p_end)}",
            "days": days,
        }
    return {
        "mode": mode,
        "anchor": anchor.isoformat(),
        "start": start.isoformat(),
        "end": end.isoformat(),
        "prev_start": p_start.isoformat(),
        "prev_end": p_end.isoformat(),
        "days": days,
        "note": note,
        "_start": start,
        "_end": end,
        "_prev_start": p_start,
        "_prev_end": p_end,
    }


def public_period(period):
    return {k: v for k, v in period.items() if not str(k).startswith("_")}


def clean_date_col(series):
    if pd.api.types.is_datetime64_any_dtype(series):
        return series
    s = series.astype(str).replace(["NaT", "nan", "None"], pd.NA)
    s = s.str.replace(r"\+\d{2}:\d{2}", "", regex=True)
    s = s.str.replace(r"\.\d+", "", regex=True)
    return pd.to_datetime(s, errors="coerce")


def _strip_tz(series):
    if pd.api.types.is_datetime64_any_dtype(series) and getattr(series.dt, "tz", None) is not None:
        return series.dt.tz_localize(None)
    return series


def process_excel(file):
    df = pd.read_excel(file, engine="openpyxl")
    df.columns = df.columns.astype(str).str.strip()
    employee = _employee_raw(df)
    party_types = _counterparty_series(df, "type")
    party_ids = _counterparty_series(df, "id")
    sticker = _pick_series(df, STICKER_HEADERS, STICKER_COL_INDEX, "стикер", _looks_sticker)
    posted = _pick_series(df, POSTED_HEADERS, POSTED_COL_INDEX, "оприход", _looks_posted)
    mx = _mx_raw(df)
    barcode = _barcode_raw(df)
    cols_map = {
        "Наименование блока": "block_name",
        "ID блока": "block_id",
        "ID блока перед списанием": "status_block_id",
        "ID офиса": "office_id",
        "ID сотрудника статуса перед списан": "status_employee_id",
        "Сумма списания": "loss_sum",
        "Кол-во лог объектов для списания": "loss_qty",
        "Дата первого списания": "loss_date",
        "Лостризон первого списания": "reason",
        "Дата статуса перед списанием": "status_before_date",
        "Статус перед списанием": "status_before",
        "Статус после списания": "status_after",
    }
    existing_cols = [c for c in cols_map if c in df.columns]
    df = df[existing_cols].rename(columns=cols_map)
    for col in ["loss_sum", "loss_qty"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)
    for col in ["loss_date", "status_before_date"]:
        if col in df.columns:
            df[col] = _strip_tz(clean_date_col(df[col]))
    for col in ["block_name", "reason", "status_before", "status_after"]:
        if col in df.columns:
            df[col] = df[col].fillna("Не указано").astype(str).str.replace(r"\*", "", regex=True).str.strip()
            if col == "reason":
                df[col] = df[col].str.replace(r"\.0$", "", regex=True).str.strip()
    if "office_id" in df.columns:
        raw = df["office_id"]
        df["office_id"] = raw.astype(str).str.replace(r"\.0$", "", regex=True).str.strip().where(raw.notna(), None)
    if employee is not None:
        df["status_employee_id"] = employee.map(badge_id)
    elif "status_employee_id" in df.columns:
        df["status_employee_id"] = df["status_employee_id"].map(badge_id)
    if party_types is not None and party_ids is not None:
        scanners = (
            df["status_employee_id"].tolist()
            if "status_employee_id" in df.columns
            else [None] * len(df)
        )
        picked = [
            assembler_badge(kind, raw_id, scanner)
            for kind, raw_id, scanner in zip(
                party_types.reindex(df.index).tolist(),
                party_ids.reindex(df.index).tolist(),
                scanners,
            )
        ]
        df["assembly_employee_id"] = pd.Series(picked, index=df.index, dtype="object")
    df["last_sticker"] = sticker.map(sticker_text).to_numpy() if sticker is not None else ""
    df["posted"] = posted.map(posted_flag).to_numpy() if posted is not None else ""
    df["mx"] = mx.map(sticker_text).to_numpy() if mx is not None else ""
    if barcode is not None:
        df["barcode"] = barcode.map(sticker_text).reindex(df.index).to_numpy()
    return df


def _upload_identity(df):
    """Один ШК у разных сборщиков — разные потери. Схлопывается только тот же сотрудник."""
    keys = ["barcode"]
    for col in ("assembly_employee_id", "status_employee_id"):
        if col in df.columns:
            keys.append(col)
    return keys


def prepare_upload(frames):
    """Склеивает файлы и выкидывает строки без даты.

    Повтор одного ШК у того же сотрудника схлопывается, остаётся последняя строка.
    Тот же ШК у другого сборщика остаётся своей потерей.
    """
    if not frames:
        raise ValueError("Нет файлов")
    df_new = pd.concat(frames, ignore_index=True)
    n_all = len(df_new)
    if "loss_date" not in df_new.columns:
        raise ValueError("В файле нет колонки «Дата первого списания»")
    df_new = df_new.dropna(subset=["loss_date"])
    n_no_date = n_all - len(df_new)
    n_dups = 0
    if "barcode" in df_new.columns and not df_new.empty:
        before = len(df_new)
        df_new = df_new.drop_duplicates(subset=_upload_identity(df_new), keep="last")
        n_dups = before - len(df_new)
    return df_new, n_no_date, n_dups


def assembly_export_mask(df):
    """Строки пропуска сборки: лостризон 5 или статус ORS."""
    has_reason = "reason" in getattr(df, "columns", [])
    has_status = "status_before" in getattr(df, "columns", [])
    if not has_reason and not has_status:
        return pd.Series(True, index=df.index)
    if has_reason:
        reason = df["reason"].astype(str).str.replace(r"\.0$", "", regex=True).str.strip()
    else:
        reason = pd.Series("", index=df.index)
    if has_status:
        status = (
            df["status_before"].fillna("").astype(str).str.upper().str.replace(r"[^A-Z]", "", regex=True)
        )
    else:
        status = pd.Series("", index=df.index)
    return (reason == "5") | (status == "ORS")


def _fold_name(value):
    try:
        missing = value is None or pd.isna(value)
    except (TypeError, ValueError):
        missing = value is None
    if missing:
        return ""
    return str(value).strip().lower().replace("ё", "е")


def _header_row(raw):
    """Строка заголовков. У отчёта пропусков она ниже названия и периода."""
    limit = min(len(raw), 40)
    marks = set(MX_HEADERS) | {"вб стикер", "дата первого списания", "шк", "товар (шк)"}
    for index in range(limit):
        cells = [_fold_name(value) for value in raw.iloc[index].tolist()]
        if any(cell in marks for cell in cells):
            return index
    return None


def _series_by_names(df, names):
    folded = {_fold_name(col): col for col in df.columns}
    for name in names:
        if name in folded:
            return df[folded[name]]
    return None


def _sticker_series(df):
    named = _series_by_names(df, (
        "вб стикер",
        "стикер вб",
        "последний стикер до списания",
        "стикер",
    ))
    if named is not None:
        return named
    for key, col in ((_fold_name(col), col) for col in df.columns):
        if "стикер" in key and "шк" not in key:
            return df[col]
    return None


def _product_barcode_series(df):
    named = _series_by_names(df, ("шк", "товар (шк)", "товар(шк)", "штрихкод", "штрих-код"))
    if named is not None:
        return named
    for key, col in ((_fold_name(col), col) for col in df.columns):
        if "шк" in key and "стикер" not in key and "id" not in key:
            return df[col]
    return None


def read_twin_frame(file):
    """Отчёт пропусков: «ВБ Стикер» и «Наименование МХ».

    Первые строки — название и период, не заголовок. Даты списания в файле нет.
    """
    raw = pd.read_excel(file, header=None, engine="openpyxl")
    at = _header_row(raw)
    if at is None:
        raise ValueError("В twin не нашёл заголовки. Нужны «ВБ Стикер» и «Наименование МХ».")
    names = []
    seen = {}
    for index, value in enumerate(raw.iloc[at].tolist()):
        label = str(value).strip() if _fold_name(value) else ""
        if not label:
            label = f"__empty_{index}"
        if label in seen:
            seen[label] += 1
            label = f"{label}.{seen[label]}"
        else:
            seen[label] = 0
        names.append(label)
    data = raw.iloc[at + 1:].copy()
    data.columns = names
    data = data.dropna(how="all")
    keep = [col for col in data.columns if not str(col).startswith("__empty_")]
    data = data.loc[:, keep]
    sticker = _sticker_series(data)
    barcode = _product_barcode_series(data)
    mx = _mx_raw(data)
    out = pd.DataFrame(index=data.index)
    out["sticker"] = sticker.map(sticker_key).to_numpy() if sticker is not None else ""
    out["barcode"] = barcode.map(sticker_text).to_numpy() if barcode is not None else ""
    out["mx"] = mx.map(sticker_text).to_numpy() if mx is not None else ""
    loss_date = _series_by_names(data, ("дата первого списания",))
    if loss_date is not None:
        out["loss_date"] = _strip_tz(clean_date_col(loss_date)).to_numpy()
    reason = _series_by_names(data, ("лостризон первого списания",))
    if reason is not None:
        out["reason"] = (
            reason.fillna("Не указано").astype(str).str.replace(r"\*", "", regex=True).str.strip()
            .str.replace(r"\.0$", "", regex=True).str.strip()
            .to_numpy()
        )
    status = _series_by_names(data, ("статус перед списанием",))
    if status is not None:
        out["status_before"] = (
            status.fillna("").astype(str).str.replace(r"\*", "", regex=True).str.strip().to_numpy()
        )
    return out.reset_index(drop=True)


def prepare_twin(frames):
    """Строки twin: «Товар (ШК)» и наименование МХ. Новых потерь из них не будет.

    Стыковка со суперсетом только по ШК. Стикер в суперсете замаскирован и не сравнивается.
    Один ШК пишется каждому сборщику, у кого есть этот пропуск. Схлопываются только
    одинаковые строки файла: тот же ШК и то же место.
    Если в файле есть лостризон или статус, остаются только 5 ЛР и ORS.
    Файл без этих колонок уже является отчётом пропусков сборки.
    """
    if not frames:
        raise ValueError("Нет файлов")
    df = pd.concat(frames, ignore_index=True)
    if "barcode" not in df.columns:
        df["barcode"] = ""
    df = df.copy()
    df["barcode"] = df["barcode"].map(sticker_text)
    stats = {
        "skipped_no_date": 0,
        "skipped_no_barcode": 0,
        "skipped_no_sticker": 0,
        "skipped_no_mx": 0,
        "skipped_not_lr": 0,
        "duplicates": 0,
    }
    no_key = df["barcode"] == ""
    stats["skipped_no_barcode"] = int(no_key.sum())
    df = df.loc[~no_key]
    if df.empty:
        raise ValueError("В twin нет колонки «Товар (ШК)» или она пустая.")
    if "mx" not in df.columns or not (df["mx"].map(sticker_text) != "").any():
        raise ValueError("В twin нет колонки «Наименование МХ» или она пустая.")
    df["mx"] = df["mx"].map(sticker_text)
    blank_mx = df["mx"] == ""
    stats["skipped_no_mx"] = int(blank_mx.sum())
    df = df.loc[~blank_mx]
    if "reason" in df.columns or "status_before" in df.columns:
        keep = assembly_export_mask(df)
        stats["skipped_not_lr"] = int((~keep).sum())
        df = df.loc[keep]
    if not df.empty:
        before = len(df)
        df = df.drop_duplicates(subset=["barcode", "mx"], keep="last")
        stats["duplicates"] = before - len(df)
    return df, stats


def prepare_losses(df):
    """Нормализует выгрузку из базы и раскладывает строки по потокам и блокам."""
    if df is None or len(df) == 0:
        return pd.DataFrame(columns=COLS)
    df = df.copy()
    for col in ("block_id", "office_id", "status_block_id", "reason", "block_name", "status_before"):
        if col not in df.columns:
            df[col] = None
    if "status_employee_id" in df.columns:
        df["status_employee_id"] = df["status_employee_id"].map(badge_id)
    if "assembly_employee_id" in df.columns:
        df["assembly_employee_id"] = df["assembly_employee_id"].map(_optional_badge)
    if "loss_sum" not in df.columns:
        df["loss_sum"] = 0
    if "loss_qty" not in df.columns:
        df["loss_qty"] = 0
    df["loss_sum"] = pd.to_numeric(df["loss_sum"], errors="coerce").fillna(0)
    df["loss_qty"] = pd.to_numeric(df["loss_qty"], errors="coerce").fillna(0)
    if "loss_date" in df.columns:
        df["loss_date"] = _strip_tz(pd.to_datetime(df["loss_date"], errors="coerce"))
    df["reason"] = df["reason"].astype(str).str.replace(r"\.0$", "", regex=True).str.strip()
    df["source_reason"] = df["reason"]
    df["block_name"] = df["block_name"].astype(str).str.replace(r"\*", "", regex=True).str.strip()
    names = {name.casefold(): name for name in (*BLOCK_MAPPING, *PLATFORM_FOLD, *IGNORED_PLATFORMS)}
    df["block_name"] = df["block_name"].map(lambda name: names.get(name.casefold(), name))
    df["block_name"] = df["block_name"].replace(PLATFORM_FOLD)
    df["status_before_raw"] = df["status_before"].map(sticker_text)
    df["status_before"] = df["status_before"].fillna("").astype(str).str.upper().str.replace(r"[^A-Z]", "", regex=True)
    df.loc[df["status_before"] == "", "status_before"] = "НЕ УКАЗАНО"
    return classify_losses(df)


def _cell_missing(value):
    if isinstance(value, (list, tuple)):
        return False
    try:
        if value is None or pd.isna(value):
            return True
    except (TypeError, ValueError):
        if value is None:
            return True
    if isinstance(value, str) and value.strip().lower() in {"", "nan", "none", "nat", "<na>", "null"}:
        return True
    return False


def _split_counterparty_text(text):
    body = text.strip()
    if len(body) >= 2 and body[0] == "[" and body[-1] == "]":
        body = body[1:-1]
    sep = ";" if ";" in body and "," not in body else ","
    items = []
    for part in body.split(sep):
        item = part.strip()
        if len(item) >= 2 and item[0] == item[-1] and item[0] in "\"'":
            item = item[1:-1].strip()
        if item:
            items.append(item)
    return items


def parse_counterparty_list(value):
    """Список из ячейки «["AWH", "EMP"]» или «[6814, 797086]».

    None — ячейка пустая. Пустой список — ячейка была, значений нет.
    """
    if isinstance(value, (list, tuple)):
        return list(value)
    if _cell_missing(value):
        return None
    text = str(value).strip()
    text = (
        text.replace("\u201c", "\"").replace("\u201d", "\"")
        .replace("\u00ab", "\"").replace("\u00bb", "\"")
        .replace("\u2018", "'").replace("\u2019", "'")
    )
    if text.lower() in {"", "nan", "none", "nat", "<na>", "null"}:
        return None
    if text.startswith("[") and text.endswith("]"):
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, list):
            return parsed
    return _split_counterparty_text(text)


def assembler_badge(types, ids, scanner):
    """Бейдж сборщика по паре «тип контрагента» / «контрагент id».

    None — обеих ячеек нет: строка остаётся на сотруднике перед списанием.
    Пустая строка — контрагенты разобраны, отдельного сборщика нет.
    Иначе один EMP, который не равен ID сотрудника перед списанием.
    AWH — блок. Совпавший EMP — раскладчик или инвентаризатор.
    """
    type_list = parse_counterparty_list(types)
    id_list = parse_counterparty_list(ids)
    if type_list is None and id_list is None:
        return None
    found = []
    scanner_badge = badge_id(scanner)
    for kind, raw in zip(type_list or [], id_list or []):
        if str(kind).strip().upper() != "EMP":
            continue
        emp = badge_id(raw)
        if not emp or emp == scanner_badge or emp in found:
            continue
        found.append(emp)
    if len(found) == 1:
        return found[0]
    return ""


def _stored_missing(value):
    """NULL в базе. Пустая строка — отдельного сборщика нет, это не пропуск значения."""
    if isinstance(value, str):
        return False
    try:
        return value is None or bool(pd.isna(value))
    except (TypeError, ValueError):
        return value is None


def _optional_badge(value):
    """Цифры бейджа. NULL остаётся None, чтобы сборка взяла колонку AC."""
    if _stored_missing(value):
        return None
    return badge_id(value)


def assembly_badge_value(stored, scanner):
    """Бейдж пропуска сборки. NULL — старая строка, бейдж сотрудника перед списанием."""
    if _stored_missing(stored):
        return badge_id(scanner)
    return badge_id(stored)


def _assembly_badges(part):
    if "status_employee_id" in part.columns:
        scanner = part["status_employee_id"].map(badge_id)
    else:
        scanner = pd.Series("", index=part.index)
    if "assembly_employee_id" not in part.columns:
        return scanner
    return pd.Series(
        [
            assembly_badge_value(stored, scan)
            for stored, scan in zip(part["assembly_employee_id"].tolist(), scanner.tolist())
        ],
        index=part.index,
    )


def badge_id(value):
    """Цифры бейджика. Пустое, NaN и «12345.0» из Excel -> «12345» или «»."""
    try:
        missing = value is None or pd.isna(value)
    except (TypeError, ValueError):
        missing = value is None
    if missing:
        return ""
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return ""
        if value.is_integer():
            return str(int(value))
    text = str(value).strip()
    if text.lower() in {"", "nan", "none", "nat", "<na>"}:
        return ""
    if re.fullmatch(r"-?\d+\.0+", text):
        text = text.split(".")[0]
    return re.sub(r"\D", "", text)


def sticker_text(value):
    """Текст стикера как в выгрузке, со звёздами. Пустое и NaN -> «»."""
    try:
        missing = value is None or pd.isna(value)
    except (TypeError, ValueError):
        missing = value is None
    if missing:
        return ""
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return ""
        if value.is_integer():
            return str(int(value))
    text = str(value).strip()
    if text.lower() in {"", "nan", "none", "nat", "<na>"}:
        return ""
    if re.fullmatch(r"-?\d+\.0+", text):
        text = text.split(".")[0]
    return text


def sticker_key(value):
    """Стикер для стыковки twin и суперсета. Звёзды вокруг номера не мешают."""
    return sticker_text(value).replace("*", "").strip()


def barcode_forms(value):
    """Как ШК мог лечь в базу: «12345» и хвост «.0» из Excel."""
    forms = []
    text = sticker_text(value)
    if text:
        forms.append(text)
    if isinstance(value, str):
        raw = value.strip()
        if raw and raw not in forms and raw.lower() not in {"nan", "none", "nat", "<na>"}:
            forms.append(raw)
    elif isinstance(value, float) and not math.isnan(value) and not math.isinf(value) and value.is_integer():
        whole = str(int(value))
        dotted = whole + ".0"
        if whole not in forms:
            forms.append(whole)
        if dotted not in forms:
            forms.append(dotted)
    return forms


def posted_flag(value):
    """Флаг оприходования: «1» — ИСТИНА, «0» — ЛОЖЬ, «» — пусто или не разобрали."""
    if isinstance(value, bool):
        return "1" if value else "0"
    try:
        if value is None or pd.isna(value):
            return ""
    except (TypeError, ValueError):
        if value is None:
            return ""
    if isinstance(value, (int, float)):
        if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
            return ""
        number = float(value)
        if number == 1:
            return "1"
        if number == 0:
            return "0"
        return ""
    if hasattr(value, "item") and not isinstance(value, (str, bytes)):
        try:
            native = value.item()
        except Exception:
            native = None
        if native is not None and native is not value:
            return posted_flag(native)
    text = str(value).strip().lower()
    if text in {"", "nan", "none", "nat", "<na>"}:
        return ""
    if text in {"истина", "true", "1", "1.0", "да", "yes"}:
        return "1"
    if text in {"ложь", "false", "0", "0.0", "нет", "no"}:
        return "0"
    return ""


def _sample_values(series):
    values = []
    for value in series.tolist():
        try:
            missing = value is None or pd.isna(value)
        except (TypeError, ValueError):
            missing = value is None
        if missing:
            continue
        values.append(value)
        if len(values) >= 40:
            break
    return values


def _looks_posted(series):
    sample = _sample_values(series)
    if not sample:
        return False
    hits = sum(posted_flag(value) in {"1", "0"} for value in sample)
    return hits * 2 >= len(sample)


def _looks_sticker(series):
    sample = _sample_values(series)
    if not sample:
        return False
    hits = sum("*" in sticker_text(value) for value in sample)
    return hits * 2 >= len(sample)


_MX_WORD = re.compile(r"(?<![0-9a-zа-яё])мх(?![0-9a-zа-яё])")
_MX_ID = re.compile(r"(?<![0-9a-zа-яё])(?:id|ид)(?![0-9a-zа-яё])")


def _barcode_raw(df):
    """Серия ШК до обрезки колонок. Колонка «ШК» важнее «Штрихкод»."""
    if "ШК" in df.columns:
        return df["ШК"]
    folded = {str(col).strip().lower().replace("ё", "е"): col for col in df.columns}
    for name in ("штрихкод", "штрих-код", "штрих код"):
        if name in folded:
            return df[folded[name]]
    return None


def _mx_raw(df):
    """Серия МХ до обрезки колонок. Имя места важнее колонки «ID МХ»."""
    folded = [(str(col).strip().lower().replace("ё", "е"), col) for col in df.columns]
    by_name = {key: col for key, col in folded}
    for name in MX_HEADERS:
        if name in by_name:
            return df[by_name[name]]
    named = []
    ids = []
    for key, col in folded:
        if "место хран" not in key and _MX_WORD.search(key) is None:
            continue
        if _MX_ID.search(key):
            ids.append(col)
        else:
            named.append(col)
    if named:
        return df[named[0]]
    if ids:
        return df[ids[0]]
    return None


def _pick_series(df, headers, index, token, accept):
    """Колонка по заголовку. Иначе — буква выгрузки, если заголовок или значения сходятся."""
    folded = {str(col).strip().lower(): col for col in df.columns}
    named = next((folded[name] for name in headers if name in folded), None)
    if named is None and token:
        named = next((col for key, col in folded.items() if token in key), None)
    if named is not None:
        return df[named]
    if df.shape[1] <= index:
        return None
    series = df.iloc[:, index]
    title = str(df.columns[index]).strip().lower()
    if title in headers or (token and token in title) or accept(series):
        return series
    return None


def _employee_raw(df):
    """Серия бейджика до обрезки колонок.

    В полной выгрузке бейджик — колонка AC. Если заголовок AC про сотрудника
    или отдельного заголовка нет, берём AC. Иначе — известный заголовок.
    """
    folded = {str(col).strip().lower(): col for col in df.columns}
    named = next((folded[name] for name in EMPLOYEE_HEADERS if name in folded), None)
    if df.shape[1] > EMPLOYEE_COL_INDEX:
        ac_name = str(df.columns[EMPLOYEE_COL_INDEX]).strip().lower()
        if named is None or ac_name in EMPLOYEE_HEADERS or "сотруд" in ac_name:
            return df.iloc[:, EMPLOYEE_COL_INDEX]
    if named is not None:
        return df[named]
    return None


def _header_key(name):
    return " ".join(str(name).strip().lower().replace("ё", "е").replace("\xa0", " ").split())


def _counterparty_series(df, kind):
    """Колонка контрагентов до обрезки листа.

    kind «type» — «Тип контрагента», «id» — «Контрагент ID».
    """
    folded = [(_header_key(col), col) for col in df.columns]
    if kind == "type":
        headers = COUNTERPARTY_TYPE_HEADERS
        def accept(key):
            return "тип" in key and "контрагент" in key
    else:
        headers = COUNTERPARTY_ID_HEADERS
        def accept(key):
            return "контрагент" in key and "id" in key and "тип" not in key
    by_name = {key: col for key, col in folded}
    for name in headers:
        if name in by_name:
            return df[by_name[name]]
    for key, col in folded:
        if accept(key):
            return df[col]
    return None


def _leading_id(series):
    """Первая часть ID до точки. Пустые и NaN не роняют разбор на pandas 3."""
    def one(value):
        try:
            missing = value is None or pd.isna(value)
        except TypeError:
            missing = value is None
        if missing:
            return ""
        return str(value).strip().split(".")[0]

    return series.map(one)


def _office_key(series):
    """Офис текстом без хвоста «.0». Пустое и NaN — пустая строка."""
    return series.fillna("").astype(str).str.strip().str.replace(r"\.0$", "", regex=True)


def _own_office_mask(off):
    """Свой офис 301760. Пустой офис — старые строки, та же логика своих блоков."""
    blank = (off == "") | (off.str.lower() == "nan")
    return blank | (off == OWN_OFFICE_ID)


def classify_losses(df):
    """flow, process_stage, block_group.

    reason и status_before уже нормализованы (LR без «.0», статус — заглавные буквы).
    Исходный LR 5 всегда сохраняется: исходящий поток, этап «Сборка».
    Для остальных ORS задаёт LR 5, RNR — LR 27.
    LR 29 всегда исходящий поток: входной статус и брак поток не перебивают.
    Группа блока при этом прежняя. Без LR 5 SMS, OSS, MRD и SLT — вход. MRD и SLT — отставание входа, отдельной карточки нет:
    группа — название места списания, как у OSS. Оперка и брак их не забирают.
    Оперка у остальных только у своего офиса и только когда ID блока места потери — 6800,
    статус из OPERKA_STATUSES и это не LR сборки. ID блока перед списанием не решает.
    Чужой офис в оперку не попадает и остаётся на названии места.
    Приоритет блока, от слабого к сильному: свой блок по названию
    места списания, оперка, брак. LR сборки не попадает ни в оперку,
    ни в брак: исход и блок места потери.
    """
    df = df.copy()
    forced_lr = df["status_before"].map(STATUS_FORCE_LR)
    hit = forced_lr.notna() & ~df["reason"].eq("5")
    df.loc[hit, "reason"] = forced_lr.loc[hit]
    by_block_id = df["status_before"].isin(PLACE_BY_BLOCK_ID)

    df["flow"] = "Другое"
    df.loc[df["status_before"].isin(INBOUND_STATUSES), "flow"] = "Входящий поток"
    df.loc[df["status_before"].isin(OUTBOUND_STATUSES), "flow"] = "Исходящий поток"
    is_assembly = df["reason"].isin(ASSEMBLY_LR) | df["status_before"].eq("ORS")
    is_defect = df["status_before"].isin(DEFECT_STATUSES) & ~is_assembly & ~by_block_id
    df.loc[is_defect, "flow"] = "Входящий поток"
    df.loc[is_assembly, "flow"] = "Исходящий поток"
    df.loc[df["reason"].isin(OUTBOUND_LRS), "flow"] = "Исходящий поток"

    df["process_stage"] = df["status_before"].map(STAGE_MAPPING).fillna("Другое")
    df.loc[is_defect, "process_stage"] = DEFECT_GROUP
    df.loc[is_assembly, "process_stage"] = "Сборка"

    own = df["block_name"].map(BLOCK_MAPPING)
    loss_id = _leading_id(df["block_id"])
    off = _office_key(df["office_id"])
    # Неизвестное имя своего блока 6800 тоже относится к отдельному «Исходу».
    own = own.mask(own.isna() & loss_id.eq(OPERKA_BLOCK_ID) & _own_office_mask(off), OUTBOUND_BLOCK)
    is_operka = (
        df["status_before"].isin(OPERKA_STATUSES)
        & (loss_id == OPERKA_BLOCK_ID)
        & _own_office_mask(off)
        & ~is_assembly
        & ~by_block_id
    )
    df["block_group"] = (
        own.fillna(UNMAPPED_GROUP)
        .mask(is_operka, "Опер площадь")
        .mask(is_defect, DEFECT_GROUP)
    )
    return df


def _ignored_platform_mask(df):
    if "block_name" not in df.columns:
        return pd.Series(False, index=df.index)
    names = df["block_name"].fillna("").astype(str).str.replace("*", "", regex=False).str.strip().str.casefold()
    return names.isin({name.casefold() for name in IGNORED_PLATFORMS})


def _visible_losses(df):
    return df.loc[~_ignored_platform_mask(df)] if df is not None else df


def _sum(df, col):
    if df is None or len(df) == 0 or col not in df.columns:
        return 0.0
    return _f(df[col].sum())


def build(dc, dp, key):
    """Сумма сейчас, сумма раньше и доля. Включает позиции, которые были только в прошлом периоде."""
    parts = []
    for frame, col in ((dc, "loss_sum"), (dp, "prev_sum")):
        if frame is None or len(frame) == 0 or key not in frame.columns:
            parts.append(pd.Series(dtype="float64", name=col))
        else:
            parts.append(frame.groupby(key)["loss_sum"].sum().rename(col))
    g = pd.concat(parts, axis=1)
    for col in ("loss_sum", "prev_sum"):
        if col not in g.columns:
            g[col] = 0.0
    g = g.fillna(0.0)
    g.index.name = "name"
    g = g.reset_index()
    if g.empty:
        return pd.DataFrame(columns=["name", "loss_sum", "prev_sum", "pct"])
    total = float(g["loss_sum"].sum())
    g["pct"] = g["loss_sum"] / total * 100 if total > 0 else 0.0
    return g.sort_values("loss_sum", ascending=False).reset_index(drop=True)


def ensure(g, names):
    """Нулевые карточки для позиций, которых нет в данных, — структура видна всегда."""
    miss = [n for n in names if n not in set(g["name"])]
    if miss:
        extra = pd.DataFrame({"name": miss, "loss_sum": 0.0, "prev_sum": 0.0, "pct": 0.0})
        g = pd.concat([g, extra], ignore_index=True)
    return g.sort_values("loss_sum", ascending=False, kind="stable").reset_index(drop=True)


def pack_cards(g, has_prev):
    rows = []
    if g is None or g.empty:
        return rows
    for rec in g.itertuples(index=False):
        loss = _f(rec.loss_sum)
        prev = _f(rec.prev_sum)
        diff = loss - prev
        growth = diff / prev * 100 if prev > 0 else None
        if growth is not None and growth > 0:
            color = "red"
        elif growth is not None and growth < 0:
            color = "green"
        else:
            color = "plain"
        chip = None
        if has_prev:
            if growth is None:
                text = "нет списаний" if loss == 0 else "нет данных для сравнения"
                chip = {"kind": "n", "text": text}
            else:
                if growth > 0:
                    kind, arrow = "r", "▲"
                elif growth < 0:
                    kind, arrow = "g", "▼"
                else:
                    kind, arrow = "n", "•"
                chip = {
                    "kind": kind,
                    "text": f"{arrow} {abs(growth):.1f}% {diff:+,.0f} ₽".replace(",", " "),
                }
        rows.append({
            "name": "" if rec.name is None else str(rec.name),
            "loss_sum": loss,
            "prev_sum": prev,
            "pct": _f(rec.pct),
            "color": color,
            "chip": chip,
        })
    return rows


def _stamp(value):
    ts = pd.Timestamp(value)
    if ts.tzinfo is not None:
        ts = ts.tz_localize(None)
    return ts


def _has_clock_time(frame):
    """В loss_date есть время суток, не только полночь."""
    if frame is None or len(frame) == 0 or "loss_date" not in getattr(frame, "columns", []):
        return False
    ts = frame["loss_date"].dropna()
    if ts.empty or not pd.api.types.is_datetime64_any_dtype(ts):
        return False
    clock = (ts.dt.hour != 0) | (ts.dt.minute != 0) | (ts.dt.second != 0) | (ts.dt.microsecond != 0)
    return bool(clock.any())


def _chart_series(frame, idx, label, bucket_of, date_of, last=None):
    """Точки графика: корзина idx, сумма и топы. bucket_of(frame) -> метка корзины."""
    top, top_lr, top_blk_lr = {}, {}, {}
    if frame is None or len(frame) == 0:
        sums = pd.Series(0.0, index=idx)
        qty = pd.Series(0.0, index=idx)
    else:
        d = frame.reset_index(drop=True)
        day = bucket_of(d).rename("day")
        sums = d.groupby(day)["loss_sum"].sum().reindex(idx, fill_value=0)
        qty = d.groupby(day)["loss_qty"].sum().reindex(idx, fill_value=0)
        by_blk = d.groupby([day, d["block_group"]])["loss_sum"].sum().sort_values(ascending=False)
        top_b = by_blk.groupby(level=0).head(1)
        top = {_stamp(k[0]): f"{k[1]} · {fmt_rub(v)}" for k, v in top_b.items()}
        by_lr = d.groupby([day, d["reason"]])["loss_sum"].sum().sort_values(ascending=False)
        top_lr = {_stamp(k[0]): f"LR {k[1]} · {fmt_rub(v)}" for k, v in by_lr.groupby(level=0).head(1).items()}
        gl = d.groupby([day, d["block_group"], d["reason"]])["loss_sum"].sum().reset_index()
        gl = gl.merge(top_b.reset_index()[["day", "block_group"]], on=["day", "block_group"])
        gl = gl.sort_values("loss_sum", ascending=False).groupby("day").head(1)
        top_blk_lr = {_stamp(r.day): f"LR {r.reason} · {fmt_rub(r.loss_sum)}" for r in gl.itertuples()}
    rows = []
    for i, ts in enumerate(idx):
        if last is not None and ts > last:
            continue
        key = _stamp(ts)
        rows.append({
            "period": label,
            "day": i + 1,
            "date": date_of(ts),
            "sum": _f(sums.iloc[i]),
            "qty": _f(qty.iloc[i]),
            "top_lr": top_lr.get(key, "—"),
            "top_block": top.get(key, "—"),
            "top_block_lr": top_blk_lr.get(key, "—"),
        })
    return rows


def daily_series(frame, start, end, label):
    """Точки графика: номер дня в периоде, сумма и топы."""
    n_days = (end - start).days + 1
    if n_days < 1:
        return []
    idx = pd.date_range(pd.Timestamp(start), periods=n_days, freq="D")
    return _chart_series(
        frame, idx, label,
        lambda d: d["loss_date"].dt.normalize(),
        lambda ts: ts.strftime("%d.%m.%Y"),
        pd.Timestamp(end),
    )


def hourly_series(frame, day, label):
    """24 точки: час 00–23 суток day. Время берётся из loss_date."""
    idx = pd.date_range(pd.Timestamp(day), periods=24, freq="h")
    return _chart_series(
        frame, idx, label,
        lambda d: d["loss_date"].dt.floor("h"),
        lambda ts: ts.strftime("%d.%m %H:%M"),
    )


def spark_by_group(frame, start, end):
    """Дневные суммы по блоку. Ключ — имя группы, значение — сумма каждого дня периода."""
    n_days = (end - start).days + 1
    if n_days < 2:
        return {}
    if frame is None or len(frame) == 0 or "block_group" not in getattr(frame, "columns", []):
        return {}
    if "loss_date" not in frame.columns:
        return {}
    day = frame["loss_date"].dt.normalize()
    ok = day.notna()
    if not bool(ok.any()):
        return {}
    grouped = frame.loc[ok].groupby([day.loc[ok], frame.loc[ok, "block_group"]])["loss_sum"].sum()
    origin = pd.Timestamp(start)
    out = {}
    for (ts, name), value in grouped.items():
        pos = (_stamp(ts) - origin).days
        if pos < 0 or pos >= n_days:
            continue
        key = "" if name is None else str(name)
        bucket = out.setdefault(key, [0.0] * n_days)
        bucket[pos] = _f(value)
    return out


def _as_name(value):
    try:
        missing = value is None or pd.isna(value)
    except (TypeError, ValueError):
        missing = value is None
    if missing:
        return ""
    return str(value)


def _split(frame, key):
    """Один groupby вместо маски на каждую карточку."""
    if frame is None or len(frame) == 0 or key not in getattr(frame, "columns", []):
        return {}
    return {_as_name(name): part for name, part in frame.groupby(key, sort=False)}


def _details_by_name(frame, key):
    """LR по имени площадки/этапа одним проходом по агрегату, не по сырым строкам."""
    if frame is None or len(frame) == 0 or key not in getattr(frame, "columns", []):
        return {}
    if "reason" not in frame.columns:
        return {}
    agg = frame.groupby([key, "reason"], sort=False)[["loss_sum", "loss_qty"]].sum().reset_index()
    buckets = {}
    for rec in agg.itertuples(index=False):
        name = _as_name(getattr(rec, key))
        amount = _f(rec.loss_sum)
        qty = _f(rec.loss_qty)
        bucket = buckets.get(name)
        if bucket is None:
            bucket = {"sum": 0.0, "qty": 0.0, "rows": []}
            buckets[name] = bucket
        bucket["sum"] += amount
        bucket["qty"] += qty
        bucket["rows"].append({"reason": str(rec.reason), "sum": amount, "qty": qty})
    for bucket in buckets.values():
        total = bucket["sum"]
        bucket["rows"].sort(key=lambda row: row["sum"], reverse=True)
        for row in bucket["rows"]:
            row["pct"] = (row["sum"] / total * 100) if total else 0.0
    return buckets


def _excluded_by_name(other_curr, group, key):
    if other_curr is None or len(other_curr) == 0:
        return {}
    if "block_group" not in other_curr.columns or key not in other_curr.columns:
        return {}
    part = other_curr[other_curr["block_group"] == group]
    if part.empty:
        return {}
    series = part.groupby(key, sort=False)["loss_sum"].sum()
    return {_as_name(i): _f(v) for i, v in series.items()}


def leaves(grouped, dc, dp, key, flow_title, group, other_curr, has_prev):
    current = _details_by_name(dc, key)
    previous = _details_by_name(dp, key)
    excluded = _excluded_by_name(other_curr, group, key)
    noun = {"process_stage": "Этап", "status_before": "Статус"}.get(key, "Площадка")
    out = []
    for rec in pack_cards(grouped, has_prev):
        name = rec["name"]
        d1 = current.get(name) or {"sum": 0.0, "qty": 0.0, "rows": []}
        d0 = previous.get(name) or {"sum": 0.0, "qty": 0.0, "rows": []}
        rec["detail"] = {
            "title": f"{noun}: {name} ({flow_title}, блок «{group}»)",
            "sum": d1["sum"],
            "qty": d1["qty"],
            "delta_text": get_delta_text(d1["sum"], d1["qty"], d0["sum"], d0["qty"]) if has_prev else None,
            "excluded_sum": excluded.get(name),
            "rows": d1["rows"],
        }
        out.append(rec)
    return out


def group_kind(group):
    if group == "Опер площадь":
        return "stages"
    if group == PICKMISS_GROUP:
        return "pickmiss"
    if group == DEFECT_GROUP:
        return "statuses"
    return "platforms"


def build_group_view(dc, dp, flow_title, group, other_curr, has_prev):
    kind = group_kind(group)
    if kind == "pickmiss":
        sections = []
        g_all = build(dc, dp, "block_name")
        known = set()
        for block in ALL_BLOCKS:
            known.update(PLATFORMS[block])
            sub = g_all[g_all["name"].isin(PLATFORMS[block])] if not g_all.empty else g_all
            sub = ensure(sub.copy() if not sub.empty else build(dc.iloc[0:0], dp.iloc[0:0], "block_name"), PLATFORMS[block])
            sections.append({
                "title": block,
                "cards": leaves(sub, dc, dp, "block_name", flow_title, group, other_curr, has_prev),
            })
        if not g_all.empty:
            rest = g_all[~g_all["name"].isin(known)].reset_index(drop=True)
            if not rest.empty:
                sections.append({
                    "title": "Другое",
                    "cards": leaves(rest, dc, dp, "block_name", flow_title, group, other_curr, has_prev),
                })
    else:
        if kind == "stages":
            key, names = "process_stage", ["Сортировка", "Предсортировка", "Отгрузка"]
        elif group == DEFECT_GROUP:
            key, names = "status_before", DEFECT_STATUSES
        else:
            key, names = "block_name", PLATFORMS.get(group, [])
        grouped = build(dc, dp, key)
        if names is not None:
            grouped = ensure(grouped, names)
        sections = [{
            "title": None,
            "cards": leaves(grouped, dc, dp, key, flow_title, group, other_curr, has_prev),
        }]
    return {
        "sum": _sum(dc, "loss_sum"),
        "prompt": GROUP_PROMPTS[kind],
        "sections": sections,
    }


def flow_names(flow_title):
    names = [name for name in ALL_BLOCKS if name != OUTBOUND_BLOCK]
    names.append(DEFECT_GROUP if flow_title == "Входящий поток" else "Опер площадь")
    return names


def flow_cards(dc, dp, flow_title, has_prev, other_curr):
    grouped = build(dc, dp, "block_group")
    if "name" in grouped.columns:
        grouped = grouped.loc[grouped["name"] != OUTBOUND_BLOCK].reset_index(drop=True)
        total = float(grouped["loss_sum"].sum()) if not grouped.empty else 0.0
        grouped["pct"] = grouped["loss_sum"] / total * 100 if total > 0 else 0.0
    grouped = ensure(grouped, flow_names(flow_title))
    dc_parts = _split(dc, "block_group")
    dp_parts = _split(dp, "block_group")
    blank_c = dc.iloc[0:0]
    blank_p = dp.iloc[0:0]
    cards = []
    for rec in pack_cards(grouped, has_prev):
        gname = rec["name"]
        rec["group_view"] = build_group_view(
            dc_parts.get(gname, blank_c),
            dp_parts.get(gname, blank_p),
            flow_title,
            gname,
            other_curr,
            has_prev,
        )
        cards.append(rec)
    return cards


def build_quality(curr, other_curr, ignored_curr=None):
    if len(curr) and "block_group" in curr.columns:
        operka = curr[curr["block_group"] == "Опер площадь"]
    else:
        operka = curr.iloc[0:0]
    if len(operka):
        series = operka.groupby("reason")["loss_sum"].sum().sort_values(ascending=False)
        operka_lrs = [{"reason": str(i), "sum": _f(v)} for i, v in series.items()]
    else:
        operka_lrs = []

    if len(other_curr):
        tbl = (
            other_curr.groupby(["status_before", "reason"])["loss_sum"]
            .sum().sort_values(ascending=False).head(15)
        )
        other_rows = [
            {"status": str(i[0]), "reason": str(i[1]), "sum": _f(v)}
            for i, v in tbl.items()
        ]
    else:
        other_rows = []

    if len(curr) and "block_group" in curr.columns:
        unmapped_df = curr[curr["block_group"] == UNMAPPED_GROUP]
    else:
        unmapped_df = curr.iloc[0:0]
    if len(unmapped_df):
        series = unmapped_df.groupby("block_name")["loss_sum"].sum().sort_values(ascending=False).head(15)
        unmapped = [{"name": str(i), "sum": _f(v)} for i, v in series.items()]
    else:
        unmapped = []

    block_6800 = None
    if len(curr) and "block_id" in curr.columns:
        if "office_id" in curr.columns:
            off = _office_key(curr["office_id"])
        else:
            off = pd.Series("", index=curr.index)
        chk = curr[
            _leading_id(curr["block_id"]).eq(OPERKA_BLOCK_ID)
            & _own_office_mask(off)
            & (curr["block_group"] != "Опер площадь")
            & (curr["block_group"] != OUTBOUND_BLOCK)
            & (curr["block_group"] != DEFECT_GROUP)
            & ~curr["status_before"].isin(PLACE_BY_BLOCK_ID)
            & ~curr["reason"].isin(ASSEMBLY_LR)
        ]
        if not chk.empty:
            block_6800 = {
                "count": int(len(chk)),
                "statuses": sorted({str(x) for x in chk["status_before"].unique()})[:10],
                "sum": _f(chk["loss_sum"].sum()),
            }
    ignored = []
    if ignored_curr is not None and not ignored_curr.empty:
        series = ignored_curr.groupby("block_name")["loss_sum"].sum().sort_values(ascending=False)
        ignored = [{"name": str(name), "sum": _f(amount)} for name, amount in series.items()]
    return {
        "all_clear": not other_rows and not unmapped and not ignored and block_6800 is None,
        "operka_lrs": operka_lrs,
        "other_rows": other_rows,
        "unmapped": unmapped,
        "block_6800": block_6800,
        "ignored": ignored,
    }


EMPLOYEE_LRS = ("5", "27")


def _found_count(group):
    """Сколько потерь с оприходованием. None — флага в этих строках нет."""
    if "posted" not in group.columns:
        return None
    flags = group["posted"].map(posted_flag)
    if not flags.isin(["1", "0"]).any():
        return None
    return int((flags == "1").sum())


def _employee_badges(part):
    part = part.copy()
    flags = part["posted"].map(posted_flag) if "posted" in part.columns else pd.Series("", index=part.index)
    part["found_count"] = flags.eq("1")
    part["has_posted"] = flags.isin(["1", "0"])
    grouped = part.groupby("badge", sort=False).agg(
        losses=("badge", "size"), amount=("loss_sum", "sum"),
        wmi=("wmi", "any"), found=("found_count", "sum"),
        has_posted=("has_posted", "any"),
    )
    rows = [{
        "badge": str(badge or ""), "losses": int(row.losses),
        "sum": _f(row.amount), "wmi": bool(row.wmi),
        "found": int(row.found) if row.has_posted else None,
    } for badge, row in grouped.iterrows()]
    rows.sort(key=lambda row: (-row["losses"], -row["sum"], row["badge"]))
    return rows


def prepare_employee_losses(df):
    """Только поля списка сотрудников, без классификации склада и графиков."""
    if df is None or df.empty:
        return pd.DataFrame()
    part = df.copy()
    if "assembly_employee_id" in part.columns:
        part["assembly_employee_id"] = part["assembly_employee_id"].map(_optional_badge)
    reason = part["reason"] if "reason" in part else pd.Series("", index=part.index)
    part["reason"] = reason.astype(str).str.replace(r"\.0$", "", regex=True).str.strip()
    status = part["status_before"] if "status_before" in part else pd.Series("", index=part.index)
    part["status_before"] = status.fillna("").astype(str).str.upper().str.replace(r"[^A-Z]", "", regex=True)
    forced = part["status_before"].map(STATUS_FORCE_LR)
    hit = forced.notna() & ~part["reason"].eq("5")
    part.loc[hit, "reason"] = forced.loc[hit]
    return part.loc[part["reason"].isin(EMPLOYEE_LRS)]


def build_employees(df):
    """LR 5 и LR 27 по бейджику одним ответом. Сверху тот, у кого больше потерь. WMI — хоть одна потеря после этого статуса. found — сколько оприходовано. Без списка ШК.

    Раскладка — сотрудник перед списанием. Сборка — сборщик из контрагентов.
    """
    df = _visible_losses(df)
    empty = {lr: [] for lr in EMPLOYEE_LRS}
    if df is None or len(df) == 0 or "reason" not in getattr(df, "columns", []):
        return empty
    wanted = [
        col for col in (
            "reason", "status_employee_id", "assembly_employee_id",
            "loss_sum", "status_before", "posted",
        )
        if col in df.columns
    ]
    part = df.loc[df["reason"].astype(str).isin(EMPLOYEE_LRS), wanted].copy()
    if part.empty:
        return empty
    part["lr"] = part["reason"].astype(str)
    if "status_employee_id" not in part.columns:
        part["badge"] = ""
    else:
        part["badge"] = part["status_employee_id"].map(badge_id)
    assembly = part["lr"].eq("5")
    if assembly.any():
        part.loc[assembly, "badge"] = _assembly_badges(part.loc[assembly]).to_numpy()
        if "assembly_employee_id" in part.columns:
            # Контрагенты разобраны, сборщика нет: не оставлять пропуск на раскладчике
            # и не рисовать пустой бейдж.
            unnamed = assembly & part["assembly_employee_id"].map(
                lambda value: isinstance(value, str) and not badge_id(value)
            )
            part = part.loc[~unnamed]
    if "loss_sum" not in part.columns:
        part["loss_sum"] = 0.0
    part["loss_sum"] = pd.to_numeric(part["loss_sum"], errors="coerce").fillna(0.0)
    if "status_before" not in part.columns:
        part["wmi"] = False
    else:
        part["wmi"] = part["status_before"].astype(str) == "WMI"
    out = dict(empty)
    for lr, group in part.groupby("lr", sort=False):
        key = str(lr)
        if key in out:
            out[key] = _employee_badges(group)
    return out


def build_employee_items(df, badge, lr):
    """Стикеры одного бейджика и LR. Каждая потеря — строка. Без списка ШК.

    found: True — оприходование ИСТИНА (вещь нашли, было движение),
    False — ЛОЖЬ, None — флага нет.
    mx — место хранения, с которого был пропуск. Пусто, если колонки не было.
    status_before — статус перед списанием из Superset, только у сборки.
    status_after — статус после списания из Superset, для отображения находов.
    """
    df = _visible_losses(df)
    code = str(lr)
    if code not in EMPLOYEE_LRS:
        return []
    if df is None or len(df) == 0 or "reason" not in getattr(df, "columns", []):
        return []
    part = df.loc[df["reason"].astype(str) == code]
    if part.empty or "status_employee_id" not in part.columns:
        return []
    if code == "5":
        badges = _assembly_badges(part)
    else:
        badges = part["status_employee_id"].map(badge_id)
    part = part.loc[badges == (badge or "")]
    if part.empty:
        return []
    stickers = part["last_sticker"] if "last_sticker" in part.columns else pd.Series("", index=part.index)
    flags = part["posted"] if "posted" in part.columns else pd.Series("", index=part.index)
    places = part["mx"] if "mx" in part.columns else pd.Series("", index=part.index)
    statuses = part["status_before"] if "status_before" in part.columns else pd.Series("", index=part.index)
    after_statuses = part["status_after"] if "status_after" in part.columns else pd.Series("", index=part.index)
    if "loss_sum" in part.columns:
        sums = pd.to_numeric(part["loss_sum"], errors="coerce").fillna(0.0)
    else:
        sums = pd.Series(0.0, index=part.index)
    items = []
    for sticker, flag, amount, place, status, after_status in zip(
        stickers.tolist(), flags.tolist(), sums.tolist(), places.tolist(), statuses.tolist(),
        after_statuses.tolist(),
    ):
        parsed = posted_flag(flag)
        if parsed == "1":
            found = True
        elif parsed == "0":
            found = False
        else:
            found = None
        item = {
            "sticker": sticker_text(sticker),
            "found": found,
            "sum": _f(amount),
            "mx": sticker_text(place),
        }
        value = sticker_text(after_status).upper()
        item["status_after"] = "" if value == "НЕ УКАЗАНО" else value
        if code == "5":
            value = sticker_text(status).upper()
            item["status_before"] = "" if value == "НЕ УКАЗАНО" else value
        items.append(item)
    items.sort(key=lambda row: (-row["sum"], row["sticker"]))
    return items


FLOOR_COUNT = 7
_FLOOR_WORD = re.compile(r"этаж\s*0*(\d+)", re.IGNORECASE)


def aisle_name(value):
    """Ряд из кода МХ: блок.этаж.ряд. «Р3T1.04.41.144.01.02» → «Р3T1.04.41»."""
    text = sticker_text(value)
    if not text:
        return ""
    parts = [part for part in text.split(".") if part]
    if len(parts) >= 3:
        return ".".join(parts[:3])
    return text


def floor_of(value):
    """Этаж 1–7 из МХ. «Этаж 7» или второй кусок «РЗТК2.2.…» → 2. Иначе None."""
    text = sticker_text(value)
    if not text:
        return None
    named = _FLOOR_WORD.search(text)
    if named:
        number = int(named.group(1))
        return number if 1 <= number <= FLOOR_COUNT else None
    parts = [part for part in text.split(".") if part]
    if len(parts) < 2 or not re.fullmatch(r"0*\d+", parts[1].strip()):
        return None
    number = int(parts[1])
    return number if 1 <= number <= FLOOR_COUNT else None


def _known_block(value):
    text = str(value or "").strip()
    return text if text in ALL_BLOCKS else ""


def _problem_places(group):
    """Ряды одного этажа: код ряда, пропуски, сумма, внутри полные МХ."""
    if group is None or len(group) == 0:
        return []
    rows = []
    for name, row_group in group.groupby("row", sort=False):
        spots = []
        for mx, spot in row_group.groupby("mx", sort=False):
            spots.append({
                "mx": str(mx),
                "skips": int(len(spot)),
                "sum": _f(spot["sum"].sum()),
            })
        spots.sort(key=lambda item: (-item["skips"], -item["sum"], item["mx"]))
        rows.append({
            "row": str(name),
            "skips": int(len(row_group)),
            "sum": _f(row_group["sum"].sum()),
            "places": spots,
        })
    rows.sort(key=lambda item: (-item["skips"], -item["sum"], item["row"]))
    return rows


def _problem_floors(group):
    """Семь этажей блока по порядку. Этаж вне 1–7 — «не указан», только если есть пропуски."""
    floors = []
    for number in range(1, FLOOR_COUNT + 1):
        chunk = group.loc[group["floor"] == number] if len(group) else group
        floors.append({
            "floor": str(number),
            "face": f"этаж {number}",
            "skips": int(len(chunk)),
            "sum": _f(chunk["sum"].sum()) if len(chunk) else 0.0,
            "rows": _problem_places(chunk),
        })
    if len(group) == 0:
        return floors
    unknown = group.loc[group["floor"] == 0]
    if len(unknown):
        floors.append({
            "floor": "0",
            "face": "не указан",
            "skips": int(len(unknown)),
            "sum": _f(unknown["sum"].sum()),
            "rows": _problem_places(unknown),
        })
    return floors


def build_problem_blocks(df):
    """Пропуски сборки (LR 5): блоки склада, в каждом 7 этажей, внутри ряды МХ.

    Пустой период — пустой список. Если пропуски есть, все блоки на месте,
    даже с нулём. Чужой блок — строка «не указан» в конце.
    """
    df = _visible_losses(df)
    if df is None or len(df) == 0 or "reason" not in getattr(df, "columns", []):
        return []
    part = df.loc[df["reason"].astype(str) == "5"]
    if part.empty:
        return []
    places = part["mx"].map(sticker_text) if "mx" in part.columns else pd.Series("", index=part.index)
    if "loss_sum" in part.columns:
        sums = pd.to_numeric(part["loss_sum"], errors="coerce").fillna(0.0)
    else:
        sums = pd.Series(0.0, index=part.index)
    if "block_group" in part.columns:
        blocks = part["block_group"].map(_known_block)
    else:
        blocks = pd.Series("", index=part.index)
    frame = pd.DataFrame({
        "mx": places.to_numpy(),
        "sum": sums.to_numpy(),
        "block": blocks.to_numpy(),
        "floor": places.map(lambda value: floor_of(value) or 0).to_numpy(),
    }, index=part.index)
    frame["row"] = frame["mx"].map(lambda value: aisle_name(value) or "не указан")
    frame.loc[frame["mx"] == "", "mx"] = "не указан"
    result = []
    for name in ALL_BLOCKS:
        chunk = frame.loc[frame["block"] == name]
        result.append({
            "block": name,
            "face": _block_face(name),
            "skips": int(len(chunk)),
            "sum": _f(chunk["sum"].sum()) if len(chunk) else 0.0,
            "floors": _problem_floors(chunk),
        })
    other = frame.loc[frame["block"] == ""]
    if len(other):
        result.append({
            "block": "не указан",
            "face": "не указан",
            "skips": int(len(other)),
            "sum": _f(other["sum"].sum()),
            "floors": _problem_floors(other),
        })
    return result


def _flow_code(title):
    if title == "Входящий поток":
        return "in"
    if title == "Исходящий поток":
        return "out"
    return ""


def reason_moves(curr, prev):
    """Топ-1 LR по росту суммы и топ-1 по падению. Блок — где этот LR сдвинулся сильнее всего в ту же сторону."""
    empty = {"up": None, "down": None}
    cols = ["reason", "block_group", "flow", "loss_sum"]

    def piece(frame):
        if frame is None or len(frame) == 0 or not set(cols).issubset(getattr(frame, "columns", [])):
            return pd.DataFrame(columns=cols)
        part = frame[frame["flow"].isin(("Входящий поток", "Исходящий поток"))]
        part = part[part["block_group"] != OUTBOUND_BLOCK]
        if part.empty:
            return pd.DataFrame(columns=cols)
        return part.groupby(["reason", "block_group", "flow"], sort=False)["loss_sum"].sum().reset_index()

    cur = piece(curr).rename(columns={"loss_sum": "sum"})
    old = piece(prev).rename(columns={"loss_sum": "prev"})
    if cur.empty and old.empty:
        return empty
    merged = cur.merge(old, on=["reason", "block_group", "flow"], how="outer")
    merged["sum"] = pd.to_numeric(merged["sum"], errors="coerce").fillna(0.0)
    merged["prev"] = pd.to_numeric(merged["prev"], errors="coerce").fillna(0.0)
    merged["reason"] = merged["reason"].map(lambda value: _as_name(value).strip())
    merged["block_group"] = merged["block_group"].map(_as_name)
    merged = merged[~merged["reason"].isin(("", "nan", "None"))]
    if merged.empty:
        return empty
    merged["diff"] = merged["sum"] - merged["prev"]
    totals = merged.groupby("reason", sort=False)[["sum", "prev", "diff"]].sum()

    def pick(kind):
        series = totals["diff"]
        winner = series.idxmax() if kind == "up" else series.idxmin()
        diff = _f(series.loc[winner])
        if kind == "up" and diff <= 0:
            return None
        if kind == "down" and diff >= 0:
            return None
        same = merged[(merged["reason"] == winner) & ((merged["diff"] > 0) if kind == "up" else (merged["diff"] < 0))]
        if same.empty:
            return None
        spot = same.loc[same["diff"].idxmax() if kind == "up" else same["diff"].idxmin()]
        return {
            "reason": str(winner),
            "diff": diff,
            "sum": _f(totals.loc[winner, "sum"]),
            "prev": _f(totals.loc[winner, "prev"]),
            "block": str(spot["block_group"]),
            "flow": _flow_code(spot["flow"]),
        }

    return {"up": pick("up"), "down": pick("down")}


def _text_key(series):
    return series.map(lambda value: _as_name(value).strip())


def _block_reason_part(frame, blocks, reasons, name, code):
    if frame is None or len(frame) == 0 or blocks is None or reasons is None:
        if frame is None or not hasattr(frame, "iloc"):
            return frame
        return frame.iloc[0:0]
    return frame.loc[(blocks == name) & (reasons == code)]


def _block_named(frame, blocks, name):
    if frame is None or not hasattr(frame, "iloc"):
        return frame
    if blocks is None or len(frame) == 0:
        return frame.iloc[0:0]
    return frame.loc[blocks == name]


def _block_points(frame, period, label, grain):
    if grain == "hour":
        day = period["_start"] if label == "Текущий период" else period["_prev_start"]
        return hourly_series(frame, day, label)
    if label == "Текущий период":
        return daily_series(frame, period["_start"], period["_end"], label)
    return daily_series(frame, period["_prev_start"], period["_prev_end"], label)


def _frame_keys(frame):
    if frame is None or len(frame) == 0:
        return None, None
    columns = getattr(frame, "columns", [])
    if "block_group" not in columns or "reason" not in columns:
        return None, None
    return _text_key(frame["block_group"]), _text_key(frame["reason"])


def _flat_points(frame, period, label, grain):
    """Сумма по часам или дням, без топов. Ось та же, что у линии блока."""
    if grain == "hour":
        day = period["_start"] if label == "Текущий период" else period["_prev_start"]
        idx = pd.date_range(pd.Timestamp(day), periods=24, freq="h")
        last = None

        def bucket(frame_):
            return frame_["loss_date"].dt.floor("h")

        def date_of(ts):
            return ts.strftime("%d.%m %H:%M")
    else:
        if label == "Текущий период":
            start, end = period["_start"], period["_end"]
        else:
            start, end = period["_prev_start"], period["_prev_end"]
        n_days = (end - start).days + 1
        if n_days < 1:
            return []
        idx = pd.date_range(pd.Timestamp(start), periods=n_days, freq="D")
        last = pd.Timestamp(end)

        def bucket(frame_):
            return frame_["loss_date"].dt.normalize()

        def date_of(ts):
            return ts.strftime("%d.%m.%Y")
    columns = getattr(frame, "columns", [])
    if frame is None or len(frame) == 0 or "loss_sum" not in columns or "loss_date" not in columns:
        sums = pd.Series(0.0, index=idx)
    else:
        sums = frame.groupby(bucket(frame), sort=False)["loss_sum"].sum().reindex(idx, fill_value=0)
    rows = []
    for i, ts in enumerate(idx):
        if last is not None and ts > last:
            continue
        rows.append({
            "period": label,
            "day": i + 1,
            "date": date_of(ts),
            "sum": _f(sums.iloc[i]),
        })
    return rows


def _platform_lr(frame, blocks, reasons, block, platform):
    """Строки площадки внутри блока: только LR 5 и LR 27, как линия блока."""
    if frame is None or not hasattr(frame, "iloc"):
        return frame
    if (
        blocks is None
        or reasons is None
        or len(frame) == 0
        or "block_name" not in getattr(frame, "columns", [])
    ):
        return frame.iloc[0:0]
    names = _text_key(frame["block_name"])
    mask = (blocks == block) & reasons.isin(("5", "27")) & (names == platform)
    return frame.loc[mask]


def _zip_lr_points(asm_rows, lay_rows):
    """Один день: сумма линии — только LR 5 и LR 27, доли лежат рядом."""
    count = max(len(asm_rows), len(lay_rows))
    rows = []
    for index in range(count):
        left = asm_rows[index] if index < len(asm_rows) else None
        right = lay_rows[index] if index < len(lay_rows) else None
        base = left or right
        if not base:
            continue
        asm = _f(left.get("sum")) if left else 0.0
        lay = _f(right.get("sum")) if right else 0.0
        rows.append({
            "period": base.get("period"),
            "day": base.get("day"),
            "date": base.get("date"),
            "sum": asm + lay,
            "asm": asm,
            "lay": lay,
        })
    return rows


class _BlockSeries:
    """Reuse classified block subsets and expensive chart tops within one report."""
    def __init__(self, curr, prev, period, grain):
        self.period = period
        self.grain = grain
        self.parts = {}
        self.platforms = {}
        self.points = {}
        self.empty = {}
        for label, frame in (("Текущий период", curr), ("Прошлый период", prev)):
            self.empty[label] = frame.iloc[0:0]
            if frame.empty:
                continue
            blocks, reasons = _frame_keys(frame)
            mask = reasons.isin(("5", "27"))
            columns = ["loss_date", "loss_sum", "loss_qty", "block_group", "reason"]
            grouped = frame.loc[mask, columns].groupby(
                [blocks.loc[mask], reasons.loc[mask]], sort=False,
            )
            self.parts.update({(label, str(block), str(reason)): group
                               for (block, reason), group in grouped})
            names = _text_key(frame["block_name"])
            grouped = frame.loc[mask, ["loss_date", "loss_sum"]].groupby(
                [blocks.loc[mask], names.loc[mask]], sort=False,
            )
            self.platforms.update({(label, str(block), str(name)): group
                                   for (block, name), group in grouped})

    def part(self, label, block, reason):
        return self.parts.get((label, block, reason), self.empty[label])

    def series(self, label, block, reason):
        key = (label, block, reason)
        if key not in self.points:
            self.points[key] = _block_points(self.part(*key), self.period, label, self.grain)
        return self.points[key]

    def platform(self, label, block, name):
        return self.platforms.get((label, block, name), self.empty[label])


def build_block_chart(curr, prev, period, has_prev, grain, series=None):
    """Линии среза «блоки». В сумме только LR 5 и LR 27, по дням отдельно.

    У линии есть площадки блока (`PLATFORMS`): тот же LR 5+27, графики площадок
    ложатся на поле этого графика.
    """
    series = series or _BlockSeries(curr, prev, period, grain)
    lines = []
    for index, name in enumerate(ALL_BLOCKS):
        color = _block_donut_color(index)
        asm = series.part("Текущий период", name, "5")
        lay = series.part("Текущий период", name, "27")
        points = _zip_lr_points(
            series.series("Текущий период", name, "5"),
            series.series("Текущий период", name, "27"),
        )
        if has_prev:
            points = points + _zip_lr_points(
                series.series("Прошлый период", name, "5"),
                series.series("Прошлый период", name, "27"),
            )
        platforms = []
        for platform in PLATFORMS.get(name, []):
            part = series.platform("Текущий период", name, platform)
            plat_points = _flat_points(part, period, "Текущий период", grain)
            if has_prev:
                old = series.platform("Прошлый период", name, platform)
                plat_points = plat_points + _flat_points(old, period, "Прошлый период", grain)
            platforms.append({
                "name": platform,
                "sum": _sum(part, "loss_sum"),
                "points": plat_points,
            })
        lines.append({
            "name": name,
            "face": _block_face(name),
            "color": color,
            "hot": _brighten(color),
            "asm": _sum(asm, "loss_sum"),
            "lay": _sum(lay, "loss_sum"),
            "points": points,
            "platforms": platforms,
        })
    return lines


def build_block_pages(curr, prev, period, items, has_prev, grain, series=None):
    """Страницы блоков: лицо, кольцо LR 5 и 27, точки графика по имени block_group."""
    series = series or _BlockSeries(curr, prev, period, grain)
    pages = {}
    for item in items or []:
        name = str(item.get("name") or "")
        if not name:
            continue
        chart_series = {}
        sums = {}
        for code in ("5", "27"):
            part = series.part("Текущий период", name, code)
            points = series.series("Текущий период", name, code)
            if has_prev:
                points = points + series.series("Прошлый период", name, code)
            chart_series[code] = points
            sums[code] = _sum(part, "loss_sum")
        total = sums["5"] + sums["27"]
        pages[name] = {
            "face": name,
            "donut_svg": build_block_lr_donut_svg(sums["5"], sums["27"], fmt_rub(total)),
            "series": chart_series,
        }
    return pages


def _writeoff_reference(filename, key):
    with (Path(__file__).parent / "data" / filename).open(encoding="utf-8-sig", newline="") as mapping_file:
        return {
            row[key].strip(): {field: value.strip() for field, value in row.items()}
            for row in csv.DictReader(mapping_file, delimiter="\t")
        }


# Пользовательский справочник имеет приоритет над дополнениями из панели «Инфо».
WRITEOFF_MAPPING = _writeoff_reference("warehouse_writeoffs_extra.tsv", "Статус")
WRITEOFF_MAPPING.update(_writeoff_reference("warehouse_writeoffs.tsv", "Статус"))
WRITEOFF_REASONS = _writeoff_reference("warehouse_writeoff_reasons.tsv", "LR")


def _writeoff_status(value):
    text = sticker_text(value).strip().upper()
    if text in WRITEOFF_MAPPING:
        return text
    # В выгрузке может находиться «LSS · Сортировка…». Не угадываем код
    # по произвольной подстроке и не выбираем между несколькими статусами.
    codes = {code for code in re.findall(r"(?<![A-ZА-ЯЁ0-9])[A-Z0-9]{3}(?![A-ZА-ЯЁ0-9])", text)
             if code in WRITEOFF_MAPPING}
    return next(iter(codes)) if len(codes) == 1 else text


def build_warehouse_writeoffs(frame):
    """Поток → зона → тип → статус. LR 5 важнее статуса, прочие LR — резерв."""
    if frame.empty:
        return {"total": None, "groups": [], "unmapped": []}
    part = frame.copy()
    statuses = part["status_before"].fillna("").map(_writeoff_status)
    # prepare_losses меняет reason по ORS/RNR. Для этого разреза нужен
    # исходный LR: ORS с другим LR остаётся релизом из справочника.
    reasons = part.get("source_reason", part.get("reason", pd.Series("", index=part.index)))
    reasons = reasons.fillna("").astype(str).str.strip().str.replace(r"\.0$", "", regex=True)
    mapped = statuses.map(WRITEOFF_MAPPING)
    raw_statuses = part.get("status_before_raw", part["status_before"]).map(_writeoff_status)
    mapped = mapped.where(mapped.notna(), raw_statuses.map(WRITEOFF_MAPPING))
    by_reason = reasons.map(WRITEOFF_REASONS)
    use_reason = reasons.eq("5") | (mapped.isna() & by_reason.notna())
    mapped = mapped.where(~use_reason, by_reason)
    levels = (("writeoff_flow", "Наименование потока"), ("writeoff_zone", "Зона списания"),
              ("writeoff_type", "Тип потери"))
    for col, field in levels:
        part[col] = mapped.map(lambda row: row[field] if isinstance(row, dict) else "Нет в маппинге")
    labels = []
    for status, raw_status, row, reason, from_reason in zip(statuses, raw_statuses, mapped, reasons, use_reason):
        code = raw_status if raw_status in WRITEOFF_MAPPING else status
        if not isinstance(row, dict):
            labels.append(str(code))
            continue
        description = WRITEOFF_MAPPING.get(code, row)["Расшифровка"]
        label = f"{code} · {description}"
        if from_reason:
            label += f" (ЛР {reason})"
        labels.append(label)
    part["writeoff_status"] = labels
    flags = part["posted"].map(posted_flag) if "posted" in part else pd.Series("", index=part.index)
    for label, flag in (("posted", "1"), ("unposted", "0"), ("unknown", "")):
        part[f"{label}_qty"] = part["loss_qty"].where(flags == flag, 0)
        part[f"{label}_sum"] = part["loss_sum"].where(flags == flag, 0)
    denominator = _sum(part, "loss_sum")

    def metrics(group, name):
        result = {"name": name, "qty": _sum(group, "loss_qty"), "sum": _sum(group, "loss_sum")}
        for label in ("posted", "unposted", "unknown"):
            for kind in ("qty", "sum"):
                result[f"{label}_{kind}"] = _sum(group, f"{label}_{kind}")
        result["share"] = result["sum"] / denominator * 100 if denominator else None
        result["posted_share"] = result["posted_sum"] / result["sum"] * 100 if result["sum"] else None
        result["net_share"] = result["unposted_sum"] / denominator * 100 if denominator else None
        return result

    columns = [col for col, _ in levels] + ["writeoff_status"]

    def tree(group, depth=0):
        rows = []
        for name, sub in group.groupby(columns[depth], sort=False):
            item = metrics(sub, str(name))
            item["children"] = tree(sub, depth + 1) if depth + 1 < len(columns) else []
            rows.append(item)
        return sorted(rows, key=lambda item: (-item["sum"], item["name"]))

    # Диагностика содержит только строки без сопоставления и по статусу, и по LR.
    missing = part.loc[mapped.isna()].copy()
    fields = (("status_raw", "status_before_raw"), ("status", "status_before"),
              ("reason", "source_reason"), ("mx", "mx"), ("platform", "block_name"))
    for target, source in fields:
        fallback = "reason" if source == "source_reason" else "status_before" if source == "status_before_raw" else source
        values = missing[source] if source in missing else missing.get(fallback, pd.Series("", index=missing.index))
        missing[target] = values.map(sticker_text)
    issues = []
    keys = [target for target, _ in fields]
    for values, group in missing.groupby(keys, sort=False, dropna=False):
        item = dict(zip(keys, values))
        item.update(qty=_sum(group, "loss_qty"), sum=_sum(group, "loss_sum"), rows=len(group))
        issues.append(item)
    issues.sort(key=lambda item: (-item["sum"], item["status"], item["reason"], item["mx"]))
    return {"total": metrics(part, "Общий итог"), "groups": tree(part), "unmapped": issues}


def empty_report(period):
    return {
        "empty": True,
        "has_prev": False,
        "period": public_period(period),
        "dashboard": None,
        "quality": None,
        "daily": [],
        "show_daily": False,
        "chart_grain": "day",
        "chart_time_missing": False,
        "flows": None,
        "moves": {"up": None, "down": None},
        "block_pages": {},
        "warehouse_writeoffs": {"total": None, "groups": [], "unmapped": []},
    }


def build_report(df_curr, df_prev, period):
    """Весь экран одним JSON: кольцо, карточки, детализация LR, график, проверка данных."""
    if df_curr is None or "flow" not in getattr(df_curr, "columns", []):
        df_curr = prepare_losses(df_curr if df_curr is not None else pd.DataFrame())
    if df_prev is None or "flow" not in getattr(df_prev, "columns", []):
        df_prev = prepare_losses(df_prev if df_prev is not None else pd.DataFrame())
    if df_curr.empty and df_prev.empty:
        return empty_report(period)

    ignored_curr = df_curr.loc[_ignored_platform_mask(df_curr)]
    df_curr = _visible_losses(df_curr)
    df_prev = _visible_losses(df_prev)
    other_curr = df_curr[df_curr["flow"] == "Другое"]
    other_prev = df_prev[df_prev["flow"] == "Другое"]
    curr = df_curr[df_curr["flow"] != "Другое"]
    prev = df_prev[df_prev["flow"] != "Другое"]
    has_prev = not prev.empty

    flows = {}
    for code, title in (("in", "Входящий поток"), ("out", "Исходящий поток")):
        dc = curr[curr["flow"] == title]
        dp = prev[prev["flow"] == title]
        cards = flow_cards(dc, dp, title, has_prev, other_curr)
        if period["days"] > 1:
            sparks = spark_by_group(dc, period["_start"], period["_end"])
            for card in cards:
                series = sparks.get(card["name"])
                if series and any(series):
                    card["spark"] = series
        flows[code] = {
            "title": title,
            "sum": _sum(dc, "loss_sum"),
            "qty": _sum(dc, "loss_qty"),
            "prev_sum": _sum(dp, "loss_sum"),
            "prev_qty": _sum(dp, "loss_qty"),
            "cards": cards,
        }

    in_sum = flows["in"]["sum"]
    out_sum = flows["out"]["sum"]
    flow_total = in_sum + out_sum
    for code in ("in", "out"):
        item = flows[code]
        item["share"] = (item["sum"] / flow_total * 100) if flow_total else 0.0
        item["delta_text"] = (
            get_delta_text(item["sum"], item["qty"], item["prev_sum"], item["prev_qty"]) if has_prev else None
        )
    total_sum = _sum(curr, "loss_sum")
    total_qty = _sum(curr, "loss_qty")
    block_items = warehouse_block_items(curr)
    block_sum = sum(item["sum"] for item in block_items)
    prev_sum = _sum(prev, "loss_sum")
    prev_qty = _sum(prev, "loss_qty")
    other_sum = _sum(other_curr, "loss_sum")
    other_qty = _sum(other_curr, "loss_qty")
    op_sum = _sum(other_prev, "loss_sum")
    op_qty = _sum(other_prev, "loss_qty")

    hour_mode = period.get("mode") == "День"
    show_daily = period["days"] > 1 or hour_mode
    chart_grain = "hour" if hour_mode else "day"
    daily = []
    chart_time_missing = False
    if show_daily and chart_grain == "hour":
        parts = [curr]
        if has_prev:
            parts.append(prev)
        chart_time_missing = any(len(part) for part in parts) and not any(_has_clock_time(part) for part in parts)
        daily.extend(hourly_series(curr, period["_start"], "Текущий период"))
        if has_prev:
            daily.extend(hourly_series(prev, period["_prev_start"], "Прошлый период"))
    elif show_daily:
        daily.extend(daily_series(curr, period["_start"], period["_end"], "Текущий период"))
        if has_prev:
            daily.extend(daily_series(prev, period["_prev_start"], period["_prev_end"], "Прошлый период"))

    block_series = _BlockSeries(curr, prev, period, chart_grain)
    return {
        "empty": False,
        "has_prev": has_prev,
        "period": public_period(period),
        "dashboard": {
            "in_sum": in_sum,
            "out_sum": out_sum,
            "total_sum": total_sum,
            "total_qty": total_qty,
            "prev_sum": prev_sum,
            "prev_qty": prev_qty,
            "delta_text": get_delta_text(total_sum, total_qty, prev_sum, prev_qty) if has_prev else None,
            "donut_svg": build_loss_donut_svg(in_sum, out_sum, fmt_rub(in_sum + out_sum)),
            "blocks": {
                "total": block_sum,
                "donut_svg": build_blocks_donut_svg(block_items, fmt_rub(block_sum)),
                "items": block_items,
                "lines": build_block_chart(curr, prev, period, has_prev, chart_grain, block_series),
            },
            "other": {
                "show": bool(other_sum) or not other_prev.empty,
                "has_rows": not other_curr.empty,
                "sum": other_sum,
                "qty": other_qty,
                "delta_text": get_delta_text(other_sum, other_qty, op_sum, op_qty) if not other_prev.empty else None,
            },
        },
        "warehouse_writeoffs": build_warehouse_writeoffs(df_curr),
        "quality": build_quality(curr, other_curr, ignored_curr),
        "daily": daily,
        "show_daily": show_daily,
        "chart_grain": chart_grain,
        "chart_time_missing": chart_time_missing,
        "flows": flows,
        "moves": reason_moves(curr, prev) if has_prev else {"up": None, "down": None},
        "block_pages": build_block_pages(
            curr, prev, period, block_items + [{"name": OUTBOUND_BLOCK}], has_prev, chart_grain, block_series,
        ),
    }

