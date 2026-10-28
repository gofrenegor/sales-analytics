"""Правила списаний и сборка отчёта для сайта."""

import calendar
import math
import os
from datetime import date, datetime, timedelta
from html import escape
from zoneinfo import ZoneInfo

import pandas as pd
from dateutil.relativedelta import relativedelta

MODES = ("День", "Неделя", "Месяц")

INBOUND_STATUSES = [
    "ANV", "EPR", "IPS", "ORG", "PVP", "RGP", "RNP", "RSP", "RWP", "TWR",
    "WDC", "WWE", "WIJ", "WPT", "WPU", "SGR",
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
OPERKA_BLOCK_ID = "6800"
PICKMISS_GROUP = "Пропуск сборки товара на блоке"
FOREIGN_GROUP = "Другие логобъекты"
UNMAPPED_GROUP = "Блоки вне маппинга"
OWN_BLOCK_RANGE = (6800, 6826)
OWN_OFFICE_ID = "301760"

BLOCK_MAPPING = {
    "Рязань Тюшевское 1": "Рязань Тюшевское 1",
    "Рязань КГТ Левый": "Рязань Тюшевское 1",
    "Рязань Дорогостой 2": "Рязань Тюшевское 2",
    "Рязань Красота 2": "Рязань Тюшевское 2",
    "Рязань Продукты 2": "Рязань Тюшевское 2",
    "Рязань КГТ 2": "Рязань Тюшевское 2",
    "Рязань Тюшевское 3": "Рязань Тюшевское 3",
    "Рязань Тюшевское 3 КГТ": "Рязань Тюшевское 3",
    "Рязань Тюшевское 3 тест": "Рязань Тюшевское 3",
    "Рязань 3 КГТ": "Рязань Тюшевское 3",
    "Рязань Тюшевское 4": "Рязань Тюшевское 4",
}

ALL_BLOCKS = list(dict.fromkeys(BLOCK_MAPPING.values()))
PLATFORMS = {b: [n for n, v in BLOCK_MAPPING.items() if v == b] for b in ALL_BLOCKS}

COLS = [
    "loss_date", "barcode", "block_name", "status_block_id", "loss_sum", "loss_qty",
    "reason", "status_before_date", "status_before", "block_id", "office_id",
    "flow", "process_stage", "block_group",
]

GROUP_PROMPTS = {
    "stages": "Выберите этап для детализации:",
    "pickmiss": "Выберите площадку для детализации:",
    "statuses": "Выберите статус для детализации:",
    "platforms": "Выберите площадку для детализации:",
}

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


def _fmt_n(value):
    return f"{value:.2f}"


def _ring_point(cx, cy, r, deg):
    rad = math.radians(deg)
    return cx + r * math.cos(rad), cy + r * math.sin(rad)


def _annulus_path(cx, cy, inner, outer, start_deg, span_deg):
    """Залитый сектор кольца. 0° — 3 часа, дальше по часовой.

    Заливка вместо толстого stroke: край остаётся ровным и не сыпется
    в радиальные полосы, когда сектор крутят перерисовкой.
    """
    if span_deg <= 0.4:
        return ""
    if span_deg >= 359.2:
        # Две полуокружности через диаметр. Точки почти в одном месте
        # раздувают дугу и рисуют кольцо не там.
        return " ".join([
            f"M {_fmt_n(cx - outer)} {_fmt_n(cy)}",
            f"A {_fmt_n(outer)} {_fmt_n(outer)} 0 1 1 {_fmt_n(cx + outer)} {_fmt_n(cy)}",
            f"A {_fmt_n(outer)} {_fmt_n(outer)} 0 1 1 {_fmt_n(cx - outer)} {_fmt_n(cy)}",
            f"M {_fmt_n(cx - inner)} {_fmt_n(cy)}",
            f"A {_fmt_n(inner)} {_fmt_n(inner)} 0 1 0 {_fmt_n(cx + inner)} {_fmt_n(cy)}",
            f"A {_fmt_n(inner)} {_fmt_n(inner)} 0 1 0 {_fmt_n(cx - inner)} {_fmt_n(cy)}",
            "Z",
        ])
    end = start_deg + span_deg
    large = 1 if span_deg > 180 else 0
    x1, y1 = _ring_point(cx, cy, outer, start_deg)
    x2, y2 = _ring_point(cx, cy, outer, end)
    x3, y3 = _ring_point(cx, cy, inner, end)
    x4, y4 = _ring_point(cx, cy, inner, start_deg)
    return (
        f"M {_fmt_n(x1)} {_fmt_n(y1)} "
        f"A {_fmt_n(outer)} {_fmt_n(outer)} 0 {large} 1 {_fmt_n(x2)} {_fmt_n(y2)} "
        f"L {_fmt_n(x3)} {_fmt_n(y3)} "
        f"A {_fmt_n(inner)} {_fmt_n(inner)} 0 {large} 0 {_fmt_n(x4)} {_fmt_n(y4)} Z"
    )


def _donut_arc(cx, cy, r, sw, start_deg, span_deg, color, css_class="loss-arc-seg"):
    """Невидимая дуга-хит. 0° — 3 часа, дальше по часовой."""
    if span_deg <= 0.4:
        return ""
    if span_deg >= 359.2:
        return (
            f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="none" stroke="{color}" '
            f'stroke-width="{sw}" class="{css_class}" />'
        )
    end = start_deg + span_deg
    large = 1 if span_deg > 180 else 0
    x1, y1 = _ring_point(cx, cy, r, start_deg)
    x2, y2 = _ring_point(cx, cy, r, end)
    return (
        f'<path d="M {x1:.2f} {y1:.2f} A {r:.2f} {r:.2f} 0 {large} 1 {x2:.2f} {y2:.2f}" '
        f'fill="none" stroke="{color}" stroke-width="{sw}" stroke-linecap="butt" class="{css_class}" />'
    )


def flow_share_labels(in_sum, out_sum):
    in_sum = max(_f(in_sum), 0.0)
    out_sum = max(_f(out_sum), 0.0)
    total = in_sum + out_sum
    in_pct = (in_sum / total * 100) if total else 0.0
    return _fmt_share(in_pct), _fmt_share(100.0 - in_pct if total else 0.0)


def build_loss_donut_svg(in_sum, out_sum, center_text):
    """Кольцо потерь: вход слева, исход справа, в центре полная сумма."""
    in_sum = max(_f(in_sum), 0.0)
    out_sum = max(_f(out_sum), 0.0)
    total = in_sum + out_sum
    in_pct = (in_sum / total * 100) if total else 0.0
    out_pct = (100.0 - in_pct) if total else 0.0

    cx, cy, r, sw = 200, 200, 112, 50
    inner, outer, outset = r - sw / 2, r + sw / 2, 8
    hit_r = (outer + outset + inner) / 2
    hit_sw = outer + outset - inner
    gap = 5.0 if in_pct > 1 and out_pct > 1 else 0.0

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
    n = len(center_text)
    sum_size = 24 if n <= 12 else 21 if n <= 14 else 17 if n <= 17 else 14
    label = escape(center_text)
    aria = escape(
        f"Диаграмма потерь. Полная сумма списаний {center_text}. "
        f"Вход {_fmt_share(in_pct)}, исход {_fmt_share(out_pct)}."
    )
    in_lbl, out_lbl = flow_share_labels(in_sum, out_sum)
    in_share, out_share = escape(in_lbl), escape(out_lbl)
    track = _annulus_path(cx, cy, inner, outer, 0, 360)
    arcs = (
        f'<path class="loss-arc loss-arc-in" fill="var(--green)" fill-rule="evenodd" d="{_annulus_path(cx, cy, inner, outer, in_start, in_span)}" />'
        + f'<path class="loss-arc loss-arc-out" fill="var(--blue)" fill-rule="evenodd" d="{_annulus_path(cx, cy, inner, outer, out_start, out_span)}" />'
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
<span class="loss-pct loss-pct-in">{in_share}</span>
<div class="loss-donut-wrap">
<svg class="loss-donut" viewBox="0 0 400 400" role="img" aria-label="{aria}">
  <g class="loss-ring" data-cx="{cx}" data-cy="{cy}" data-inner="{_fmt_n(inner)}" data-outer="{_fmt_n(outer)}" data-in-pct="{in_pct:.6f}" data-out-pct="{out_pct:.6f}" data-gap="{gap:.1f}">
    <path class="loss-track" fill="var(--donut-track)" fill-rule="evenodd" d="{track}" />
    {arcs}
    <path class="loss-arc-cover" fill="var(--donut-purple)" fill-rule="evenodd" opacity="0" d="{track}" />
  </g>
  {hits}
</svg>
<div class="loss-center" aria-hidden="true">
  <div class="loss-caption"><span>Полная сумма</span><span>списаний</span></div>
  <div class="loss-sum" style="--sum-units: {sum_size}">{label}</div>
</div>
</div>
<span class="loss-pct loss-pct-out">{out_share}</span>
</div>
</div>'''


def compute_period(mode, anchor, today):
    """Начало, конец и такой же по длине прошлый период. Неделя с понедельника."""
    if isinstance(anchor, str):
        anchor = date.fromisoformat(anchor)
    if mode not in MODES:
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
    cols_map = {
        "ШК": "barcode",
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
    }
    existing_cols = [c for c in cols_map if c in df.columns]
    df = df[existing_cols].rename(columns=cols_map)
    for col in ["loss_sum", "loss_qty"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)
    for col in ["loss_date", "status_before_date"]:
        if col in df.columns:
            df[col] = _strip_tz(clean_date_col(df[col]))
    for col in ["block_name", "reason", "status_before"]:
        if col in df.columns:
            df[col] = df[col].fillna("Не указано").astype(str).str.replace(r"\*", "", regex=True).str.strip()
            if col == "reason":
                df[col] = df[col].str.replace(r"\.0$", "", regex=True).str.strip()
    if "office_id" in df.columns:
        raw = df["office_id"]
        df["office_id"] = raw.astype(str).str.replace(r"\.0$", "", regex=True).str.strip().where(raw.notna(), None)
    return df


def prepare_upload(frames):
    """Склеивает файлы, выкидывает строки без даты и повторы ШК (остаётся последняя)."""
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
        df_new = df_new.drop_duplicates(subset=["barcode"], keep="last")
        n_dups = before - len(df_new)
    return df_new, n_no_date, n_dups


def prepare_losses(df):
    """Нормализует выгрузку из базы и раскладывает строки по потокам и блокам."""
    if df is None or len(df) == 0:
        return pd.DataFrame(columns=COLS)
    df = df.copy()
    for col in ("block_id", "office_id", "status_block_id", "reason", "block_name", "status_before"):
        if col not in df.columns:
            df[col] = None
    if "loss_sum" not in df.columns:
        df["loss_sum"] = 0
    if "loss_qty" not in df.columns:
        df["loss_qty"] = 0
    df["loss_sum"] = pd.to_numeric(df["loss_sum"], errors="coerce").fillna(0)
    df["loss_qty"] = pd.to_numeric(df["loss_qty"], errors="coerce").fillna(0)
    if "loss_date" in df.columns:
        df["loss_date"] = _strip_tz(pd.to_datetime(df["loss_date"], errors="coerce"))
    df["reason"] = df["reason"].astype(str).str.replace(r"\.0$", "", regex=True).str.strip()
    df["block_name"] = df["block_name"].astype(str).str.replace(r"\*", "", regex=True).str.strip()
    df["status_before"] = df["status_before"].fillna("").astype(str).str.upper().str.replace(r"[^A-Z]", "", regex=True)
    df.loc[df["status_before"] == "", "status_before"] = "НЕ УКАЗАНО"
    return classify_losses(df)


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


def classify_losses(df):
    """flow, process_stage, block_group.

    reason и status_before уже нормализованы (LR без «.0», статус — заглавные буквы).
    Приоритет блока, от слабого к сильному: свой блок по названию места списания,
    оперка, чужой ЛО, брак. LR сборки не попадает ни в оперку, ни в брак:
    исход и блок места потери.
    """
    df = df.copy()
    df["flow"] = "Другое"
    df.loc[df["status_before"].isin(INBOUND_STATUSES), "flow"] = "Входящий поток"
    df.loc[df["status_before"].isin(OUTBOUND_STATUSES), "flow"] = "Исходящий поток"
    is_assembly = df["reason"].isin(ASSEMBLY_LR)
    is_defect = df["status_before"].isin(DEFECT_STATUSES) & ~is_assembly
    df.loc[is_defect, "flow"] = "Входящий поток"
    df.loc[is_assembly, "flow"] = "Исходящий поток"

    df["process_stage"] = df["status_before"].map(STAGE_MAPPING).fillna("Другое")
    df.loc[is_defect, "process_stage"] = DEFECT_GROUP
    df.loc[is_assembly, "process_stage"] = "Сборка"

    own = df["block_name"].map(BLOCK_MAPPING)
    b_id = _leading_id(df["status_block_id"])
    is_operka = df["status_before"].isin(OPERKA_STATUSES) & (b_id == OPERKA_BLOCK_ID) & ~is_assembly
    loss_blk = pd.to_numeric(_leading_id(df["block_id"]), errors="coerce")
    off = df["office_id"].fillna("").astype(str).str.strip().str.replace(r"\.0$", "", regex=True)
    foreign_blk = loss_blk.notna() & ((loss_blk < OWN_BLOCK_RANGE[0]) | (loss_blk > OWN_BLOCK_RANGE[1]))
    foreign_off = (off != "") & (off.str.lower() != "nan") & (off != OWN_OFFICE_ID)
    is_foreign = foreign_blk | foreign_off | (loss_blk.isna() & (off == "") & own.isna())
    df["block_group"] = (
        own.fillna(UNMAPPED_GROUP)
        .mask(is_operka, "Опер площадь")
        .mask(is_foreign, FOREIGN_GROUP)
        .mask(is_defect, DEFECT_GROUP)
    )
    return df


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
        if growth is not None and growth >= 20:
            color = "red"
        elif growth is not None and growth <= -20:
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
                    "text": f"{arrow} {abs(growth):.1f}% · {diff:+,.0f} ₽".replace(",", " "),
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


def daily_series(frame, start, end, label):
    """Точки графика: номер дня в периоде, сумма и топы."""
    n_days = (end - start).days + 1
    if n_days < 1:
        return []
    idx = pd.date_range(pd.Timestamp(start), periods=n_days, freq="D")
    top, top_lr, top_blk_lr = {}, {}, {}
    if frame is None or len(frame) == 0:
        sums = pd.Series(0.0, index=idx)
        qty = pd.Series(0.0, index=idx)
    else:
        d = frame.reset_index(drop=True)
        day = d["loss_date"].dt.normalize().rename("day")
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
    last = pd.Timestamp(end)
    rows = []
    for i, ts in enumerate(idx):
        if ts > last:
            continue
        key = _stamp(ts)
        rows.append({
            "period": label,
            "day": i + 1,
            "date": ts.strftime("%d.%m.%Y"),
            "sum": _f(sums.iloc[i]),
            "qty": _f(qty.iloc[i]),
            "top_lr": top_lr.get(key, "—"),
            "top_block": top.get(key, "—"),
            "top_block_lr": top_blk_lr.get(key, "—"),
        })
    return rows


def _excluded_sum(other_curr, group, key, name):
    if other_curr is None or len(other_curr) == 0:
        return None
    if "block_group" not in other_curr.columns or key not in other_curr.columns:
        return None
    oc = other_curr[(other_curr["block_group"] == group) & (other_curr[key] == name)]
    if oc.empty:
        return None
    return _f(oc["loss_sum"].sum())


def make_detail(d1, d0, flow_title, group, name, key, other_curr, has_prev):
    noun = {"process_stage": "Этап", "status_before": "Статус"}.get(key, "Площадка")
    total_sum = _sum(d1, "loss_sum")
    total_qty = _sum(d1, "loss_qty")
    rows = []
    if d1 is not None and len(d1) and "reason" in d1.columns:
        lr = d1.groupby("reason")[["loss_sum", "loss_qty"]].sum().reset_index()
        total = _f(lr["loss_sum"].sum())
        lr = lr.sort_values("loss_sum", ascending=False)
        for rec in lr.itertuples(index=False):
            amount = _f(rec.loss_sum)
            rows.append({
                "reason": str(rec.reason),
                "sum": amount,
                "qty": _f(rec.loss_qty),
                "pct": (amount / total * 100) if total else 0.0,
            })
    return {
        "title": f"{noun}: {name} ({flow_title}, блок «{group}»)",
        "sum": total_sum,
        "qty": total_qty,
        "delta_text": get_delta_text(total_sum, total_qty, _sum(d0, "loss_sum"), _sum(d0, "loss_qty")) if has_prev else None,
        "excluded_sum": _excluded_sum(other_curr, group, key, name),
        "rows": rows,
    }


def leaves(grouped, dc, dp, key, flow_title, group, other_curr, has_prev):
    out = []
    for rec in pack_cards(grouped, has_prev):
        name = rec["name"]
        d1 = dc[dc[key] == name] if key in getattr(dc, "columns", []) else dc.iloc[0:0]
        d0 = dp[dp[key] == name] if key in getattr(dp, "columns", []) else dp.iloc[0:0]
        rec["detail"] = make_detail(d1, d0, flow_title, group, name, key, other_curr, has_prev)
        out.append(rec)
    return out


def group_kind(group):
    if group == "Опер площадь":
        return "stages"
    if group == PICKMISS_GROUP:
        return "pickmiss"
    if group in (FOREIGN_GROUP, DEFECT_GROUP):
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
        elif group == FOREIGN_GROUP:
            key, names = "status_before", None
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
    names = list(ALL_BLOCKS)
    names.append(DEFECT_GROUP if flow_title == "Входящий поток" else "Опер площадь")
    names.append(FOREIGN_GROUP)
    return names


def flow_cards(dc, dp, flow_title, has_prev, other_curr):
    grouped = ensure(build(dc, dp, "block_group"), flow_names(flow_title))
    cards = []
    for rec in pack_cards(grouped, has_prev):
        gname = rec["name"]
        gdc = dc[dc["block_group"] == gname] if "block_group" in dc.columns else dc.iloc[0:0]
        gdp = dp[dp["block_group"] == gname] if "block_group" in dp.columns else dp.iloc[0:0]
        rec["group_view"] = build_group_view(gdc, gdp, flow_title, gname, other_curr, has_prev)
        cards.append(rec)
    return cards


def build_quality(curr, other_curr):
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
    if len(curr) and "status_block_id" in curr.columns:
        chk = curr[
            _leading_id(curr["status_block_id"]).eq("6800")
            & (curr["block_group"] != "Опер площадь")
            & ~curr["reason"].isin(ASSEMBLY_LR)
        ]
        if not chk.empty:
            block_6800 = {
                "count": int(len(chk)),
                "statuses": sorted({str(x) for x in chk["status_before"].unique()})[:10],
                "sum": _f(chk["loss_sum"].sum()),
            }
    return {
        "all_clear": not other_rows and not unmapped and block_6800 is None,
        "operka_lrs": operka_lrs,
        "other_rows": other_rows,
        "unmapped": unmapped,
        "block_6800": block_6800,
    }


def empty_report(period):
    return {
        "empty": True,
        "has_prev": False,
        "period": public_period(period),
        "dashboard": None,
        "quality": None,
        "daily": [],
        "show_daily": False,
        "flows": None,
    }


def build_report(df_curr, df_prev, period):
    """Весь экран одним JSON: кольцо, карточки, детализация LR, график, проверка данных."""
    if df_curr is None or "flow" not in getattr(df_curr, "columns", []):
        df_curr = prepare_losses(df_curr if df_curr is not None else pd.DataFrame())
    if df_prev is None or "flow" not in getattr(df_prev, "columns", []):
        df_prev = prepare_losses(df_prev if df_prev is not None else pd.DataFrame())
    if df_curr.empty and df_prev.empty:
        return empty_report(period)

    other_curr = df_curr[df_curr["flow"] == "Другое"]
    other_prev = df_prev[df_prev["flow"] == "Другое"]
    curr = df_curr[df_curr["flow"] != "Другое"]
    prev = df_prev[df_prev["flow"] != "Другое"]
    has_prev = not prev.empty

    flows = {}
    for code, title in (("in", "Входящий поток"), ("out", "Исходящий поток")):
        dc = curr[curr["flow"] == title]
        dp = prev[prev["flow"] == title]
        flows[code] = {
            "title": title,
            "sum": _sum(dc, "loss_sum"),
            "cards": flow_cards(dc, dp, title, has_prev, other_curr),
        }

    in_sum = flows["in"]["sum"]
    out_sum = flows["out"]["sum"]
    total_sum = _sum(curr, "loss_sum")
    total_qty = _sum(curr, "loss_qty")
    prev_sum = _sum(prev, "loss_sum")
    prev_qty = _sum(prev, "loss_qty")
    other_sum = _sum(other_curr, "loss_sum")
    other_qty = _sum(other_curr, "loss_qty")
    op_sum = _sum(other_prev, "loss_sum")
    op_qty = _sum(other_prev, "loss_qty")

    show_daily = period["days"] > 1
    daily = []
    if show_daily:
        daily.extend(daily_series(curr, period["_start"], period["_end"], "Текущий период"))
        if has_prev:
            daily.extend(daily_series(prev, period["_prev_start"], period["_prev_end"], "Прошлый период"))

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
            "other": {
                "show": bool(other_sum) or not other_prev.empty,
                "has_rows": not other_curr.empty,
                "sum": other_sum,
                "qty": other_qty,
                "delta_text": get_delta_text(other_sum, other_qty, op_sum, op_qty) if not other_prev.empty else None,
            },
        },
        "quality": build_quality(curr, other_curr),
        "daily": daily,
        "show_daily": show_daily,
        "flows": flows,
    }
