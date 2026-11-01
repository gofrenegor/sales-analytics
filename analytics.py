"""Правила списаний и сборка отчёта для сайта."""

import calendar
import math
import os
import re
from datetime import date, datetime, timedelta
from html import escape
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
# Статус важнее цифры в лостризоне: ORS всегда сборка, RNR всегда неразложка.
STATUS_FORCE_LR = {"ORS": "5", "RNR": "27"}
# Эти статусы не уходят в оперку и брак. Группа — название места списания.
# MRD и SLT — отставание входа: та же группа, без своей карточки.
PLACE_BY_BLOCK_ID = frozenset({"ORS", "RNR", "OSS", "MRD", "SLT"})
OPERKA_BLOCK_ID = "6800"
PICKMISS_GROUP = "Пропуск сборки товара на блоке"
UNMAPPED_GROUP = "Блоки вне маппинга"
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
    "status_employee_id",
    "flow", "process_stage", "block_group",
]
# Полная выгрузка: бейджик исполнителя лежит в колонке AC.
EMPLOYEE_COL_INDEX = 28
EMPLOYEE_HEADERS = (
    "id сотрудника статуса перед списан",
    "id сотрудника статуса перед списанием",
    "id сотрудника",
)

GROUP_PROMPTS = {
    "stages": "Выберите этап для детализации:",
    "pickmiss": "Выберите площадку для детализации:",
    "statuses": "Выберите статус для детализации:",
    "platforms": "Выберите площадку для детализации:",
}

FLOW_IN_COLOR = "#3fb950"
FLOW_OUT_COLOR = "#4C9AFF"


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
    n = len(center_text)
    sum_size = 24 if n <= 12 else 21 if n <= 14 else 17 if n <= 17 else 14
    label = escape(center_text)
    aria = escape(
        f"Диаграмма потерь. Полная сумма списаний {center_text}. "
        f"Вход {_fmt_share(in_pct)}, исход {_fmt_share(out_pct)}."
    )
    in_lbl, out_lbl = flow_share_labels(in_sum, out_sum)
    in_share, out_share = escape(in_lbl), escape(out_lbl)
    arcs = (
        _donut_arc(cx, cy, r, sw, in_start, in_span, FLOW_IN_COLOR, "loss-arc-seg arc-in")
        + _donut_arc(cx, cy, r, sw, out_start, out_span, FLOW_OUT_COLOR, "loss-arc-seg arc-out")
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
  <circle class="loss-track" cx="{cx}" cy="{cy}" r="{r}" fill="none" stroke="rgba(128,128,128,0.16)" stroke-width="{sw}"/>
  {arcs}
  {hits}
  <text x="{cx}" y="{cy - 22}" text-anchor="middle" dominant-baseline="central" fill="currentColor" opacity="0.62" font-size="15" font-weight="600">Полная сумма</text>
  <text x="{cx}" y="{cy - 4}" text-anchor="middle" dominant-baseline="central" fill="currentColor" opacity="0.62" font-size="15" font-weight="600">списаний</text>
  <text x="{cx}" y="{cy + 24}" text-anchor="middle" dominant-baseline="central" fill="currentColor" font-size="{sum_size}" font-weight="700" style="font-variant-numeric:tabular-nums">{label}</text>
</svg>
</div>
<span class="loss-pct loss-pct-out">{out_share}</span>
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
    if employee is not None:
        df["status_employee_id"] = employee.map(badge_id)
    elif "status_employee_id" in df.columns:
        df["status_employee_id"] = df["status_employee_id"].map(badge_id)
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
    if "status_employee_id" in df.columns:
        df["status_employee_id"] = df["status_employee_id"].map(badge_id)
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
    ORS всегда LR 5: исходящий поток, этап «Сборка». RNR всегда LR 27: вход.
    SMS, OSS, MRD и SLT — вход. MRD и SLT — отставание входа, отдельной карточки нет:
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
    hit = forced_lr.notna()
    df.loc[hit, "reason"] = forced_lr.loc[hit]
    by_block_id = df["status_before"].isin(PLACE_BY_BLOCK_ID)

    df["flow"] = "Другое"
    df.loc[df["status_before"].isin(INBOUND_STATUSES), "flow"] = "Входящий поток"
    df.loc[df["status_before"].isin(OUTBOUND_STATUSES), "flow"] = "Исходящий поток"
    forced_in = df["status_before"].isin(("RNR", "SMS", "OSS", "MRD", "SLT"))
    is_assembly = (df["reason"].isin(ASSEMBLY_LR) | df["status_before"].eq("ORS")) & ~forced_in
    is_defect = df["status_before"].isin(DEFECT_STATUSES) & ~is_assembly & ~by_block_id
    df.loc[is_defect, "flow"] = "Входящий поток"
    df.loc[is_assembly, "flow"] = "Исходящий поток"

    df["process_stage"] = df["status_before"].map(STAGE_MAPPING).fillna("Другое")
    df.loc[is_defect, "process_stage"] = DEFECT_GROUP
    df.loc[is_assembly, "process_stage"] = "Сборка"

    own = df["block_name"].map(BLOCK_MAPPING)
    loss_id = _leading_id(df["block_id"])
    off = _office_key(df["office_id"])
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


def reason_rows(frame):
    """Причины LR по убыванию суммы. Длинный хвост схлопывается в «остальные»."""
    if frame is None or len(frame) == 0 or "reason" not in getattr(frame, "columns", []):
        return []
    lr = frame.groupby("reason")[["loss_sum", "loss_qty"]].sum().reset_index()
    total = _f(lr["loss_sum"].sum())
    lr = lr.sort_values(["loss_sum", "reason"], ascending=[False, True])
    rows = []
    for rec in lr.itertuples(index=False):
        amount = _f(rec.loss_sum)
        if amount == 0 and _f(rec.loss_qty) == 0:
            continue
        rows.append({
            "reason": str(rec.reason),
            "sum": amount,
            "qty": _f(rec.loss_qty),
            "pct": (amount / total * 100) if total else 0.0,
        })
    if len(rows) <= 24:
        return rows
    head = rows[:23]
    rest = rows[23:]
    rest_sum = _f(sum(item["sum"] for item in rest))
    rest_qty = _f(sum(item["qty"] for item in rest))
    head.append({
        "reason": "остальные",
        "sum": rest_sum,
        "qty": rest_qty,
        "pct": (rest_sum / total * 100) if total else 0.0,
        "rest": True,
    })
    return head


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
    names = list(ALL_BLOCKS)
    names.append(DEFECT_GROUP if flow_title == "Входящий поток" else "Опер площадь")
    return names


def flow_cards(dc, dp, flow_title, has_prev, other_curr):
    grouped = ensure(build(dc, dp, "block_group"), flow_names(flow_title))
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
    if len(curr) and "block_id" in curr.columns:
        if "office_id" in curr.columns:
            off = _office_key(curr["office_id"])
        else:
            off = pd.Series("", index=curr.index)
        chk = curr[
            _leading_id(curr["block_id"]).eq(OPERKA_BLOCK_ID)
            & _own_office_mask(off)
            & (curr["block_group"] != "Опер площадь")
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
    return {
        "all_clear": not other_rows and not unmapped and block_6800 is None,
        "operka_lrs": operka_lrs,
        "other_rows": other_rows,
        "unmapped": unmapped,
        "block_6800": block_6800,
    }


def build_employees(df):
    """LR 5 по бейджику. Сверху тот, у кого больше потерь. WMI — хоть одна потеря после этого статуса. Без списка ШК."""
    if df is None or len(df) == 0 or "reason" not in getattr(df, "columns", []):
        return []
    wanted = [col for col in ("status_employee_id", "loss_sum", "status_before") if col in df.columns]
    part = df.loc[df["reason"].astype(str) == "5", wanted].copy()
    if part.empty:
        return []
    if "status_employee_id" not in part.columns:
        part["badge"] = ""
    else:
        part["badge"] = part["status_employee_id"].map(badge_id)
    if "loss_sum" not in part.columns:
        part["loss_sum"] = 0.0
    part["loss_sum"] = pd.to_numeric(part["loss_sum"], errors="coerce").fillna(0.0)
    if "status_before" not in part.columns:
        part["wmi"] = False
    else:
        part["wmi"] = part["status_before"].astype(str) == "WMI"
    rows = []
    for badge, group in part.groupby("badge", sort=False):
        flags = group["wmi"].tolist()
        rows.append({
            "badge": str(badge or ""),
            "losses": int(len(group)),
            "sum": _f(group["loss_sum"].sum()),
            "wmi": bool(any(flags)),
        })
    rows.sort(key=lambda row: (-row["losses"], -row["sum"], row["badge"]))
    return rows


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
            "reasons": reason_rows(curr),
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
        "chart_grain": chart_grain,
        "chart_time_missing": chart_time_missing,
        "flows": flows,
    }
