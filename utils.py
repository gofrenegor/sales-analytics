import math
from html import escape

import streamlit as st
import pandas as pd
from datetime import date, timedelta
from dateutil.relativedelta import relativedelta
from sqlalchemy import create_engine, text
import calendar


@st.cache_resource
def get_db_engine():
    try:
        db_url = st.secrets["database"]["connection_string"]
        if db_url.startswith("postgres://"):
            db_url = db_url.replace("postgres://", "postgresql://", 1)
        if db_url.startswith("postgresql://"):
            db_url = db_url.replace("postgresql://", "postgresql+psycopg2://", 1)
        return create_engine(db_url, connect_args={"connect_timeout": 10})
    except Exception as e:
        st.error(f"Ошибка при чтении секретов: {e}")
        return None


engine = get_db_engine()


@st.cache_resource
def ensure_schema():
    try:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE losses ADD COLUMN IF NOT EXISTS office_id TEXT"))
    except Exception:
        pass  # таблицы ещё нет — колонка появится при первой загрузке


ensure_schema()


def inject_css():
    st.markdown("""
    <style>
    .block-container { padding-top: 2rem; max-width: 1400px; }
    h1 { font-weight: 700; letter-spacing: -0.5px; }
    div[data-testid="stMetricValue"] { font-weight: 700; }
    div[class*="st-key-card-"] { border: 1px solid rgba(128,128,128,0.25) !important; border-radius: 12px !important; transition: border-color .15s; }
    div[class*="st-key-card-"]:hover { border-color: rgba(128,128,128,0.6) !important; }
    div[class*="st-key-card-red-"] { border-color: rgba(255,75,75,0.65) !important; }
    div[class*="st-key-card-green-"] { border-color: rgba(46,157,79,0.65) !important; }
    .c-name { font-size: 14px; opacity: .75; margin-bottom: 2px; }
    .c-sum { font-size: 26px; font-weight: 700; margin-bottom: 6px; }
    .chip { display: inline-block; font-size: 12.5px; font-weight: 600; padding: 2px 10px; border-radius: 12px; }
    .chip-r { background: rgba(255,75,75,.14); color: #ff6b6b; }
    .chip-g { background: rgba(63,185,80,.14); color: #3fb950; }
    .chip-n { background: rgba(128,128,128,.15); }
    .bar { height: 4px; background: rgba(128,128,128,.2); border-radius: 2px; margin: 12px 0 4px; }
    .bar div { height: 100%; background: #4C9AFF; border-radius: 2px; }
    .c-share { font-size: 12px; opacity: .6; margin-bottom: 6px; }
    .big-sum { font-size: 34px; font-weight: 700; margin: 2px 0 0; }
    .sub { font-size: 14px; opacity: .65; }
    .st-key-btn_inbound button { background: transparent !important; color: #3fb950 !important; border: 1.5px solid #3fb950 !important; font-weight: 600; letter-spacing: .5px; }
    .st-key-btn_inbound button:hover { background: rgba(63,185,80,.12) !important; }
    .st-key-btn_outbound button { background: transparent !important; color: #4C9AFF !important; border: 1.5px solid #4C9AFF !important; font-weight: 600; letter-spacing: .5px; }
    .st-key-btn_outbound button:hover { background: rgba(76,154,255,.12) !important; }
    .loss-figure { display: flex; align-items: center; justify-content: center; gap: 2px; width: 100%; }
    .loss-figure svg { width: min(100%, 420px); height: auto; overflow: visible; display: block; flex: 0 1 auto; }
    .loss-pct { font-size: 28px; font-weight: 700; letter-spacing: -0.4px; line-height: 1; min-width: 3.4em; font-variant-numeric: tabular-nums; flex: 0 0 auto; }
    .loss-pct-in { text-align: right; color: light-dark(#2A36C0, #A8B0FF); }
    .loss-pct-out { text-align: left; color: light-dark(#9E1028, #FF8B9A); }
    .st-key-pick_in button,
    .st-key-pick_out button {
        position: relative !important;
        display: flex !important;
        align-items: center !important;
        justify-content: flex-start !important;
        min-height: 72px !important;
        padding: 14px 44px 14px 18px !important;
        border-radius: 16px !important;
        border: 1px solid rgba(128,128,128,0.22) !important;
        background: transparent !important;
        box-shadow: none !important;
        color: inherit !important;
        transition: background .16s ease, border-color .16s ease !important;
    }
    .st-key-pick_in button:hover {
        background: rgba(63, 185, 80, 0.14) !important;
        border-color: rgba(63, 185, 80, 0.55) !important;
    }
    .st-key-pick_out button:hover {
        background: rgba(76, 154, 255, 0.14) !important;
        border-color: rgba(76, 154, 255, 0.55) !important;
    }
    .st-key-pick_in button:focus, .st-key-pick_out button:focus { box-shadow: none !important; }
    .st-key-pick_in button:focus-visible { outline: 2px solid #3fb950 !important; outline-offset: 3px !important; }
    .st-key-pick_out button:focus-visible { outline: 2px solid #4C9AFF !important; outline-offset: 3px !important; }
    .st-key-pick_in button::after,
    .st-key-pick_out button::after {
        content: "→";
        position: absolute;
        right: 16px;
        top: 50%;
        transform: translateY(-50%);
        font-size: 20px;
        font-weight: 600;
        opacity: .45;
    }
    .st-key-pick_in button::after { color: #3fb950; }
    .st-key-pick_out button::after { color: #4C9AFF; }
    .st-key-pick_in button:hover::after,
    .st-key-pick_out button:hover::after { opacity: 1; }
    .st-key-pick_in button p,
    .st-key-pick_out button p {
        margin: 0 !important;
        width: 100%;
        display: flex !important;
        flex-wrap: wrap !important;
        align-items: baseline !important;
        justify-content: space-between !important;
        gap: 8px 16px !important;
        font-size: 17px !important;
        font-weight: 600 !important;
        line-height: 1.25 !important;
        text-align: left !important;
        color: inherit !important;
    }
    .st-key-pick_in button strong,
    .st-key-pick_out button strong {
        font-size: 26px !important;
        font-weight: 700 !important;
        letter-spacing: -0.5px !important;
        font-variant-numeric: tabular-nums;
        margin-left: auto !important;
    }
    .st-key-pick_out { margin-top: 8px !important; }
    /* Vega/Altair tooltip («Динамика по дням»): −24% от прежних 22px / 16px padding */
    #vg-tooltip-element {
        font-size: 17px !important;
        padding: 12px !important;
        border-radius: 6px !important;
        line-height: 1.45 !important;
    }
    #vg-tooltip-element h2 {
        font-size: 20px !important;
        margin-bottom: 15px !important;
    }
    #vg-tooltip-element table tr td {
        padding-top: 3px !important;
        padding-bottom: 3px !important;
    }
    #vg-tooltip-element table tr td.key {
        max-width: 300px !important;
        padding-right: 6px !important;
    }
    #vg-tooltip-element table tr td.value {
        max-width: 600px !important;
        max-height: 14em !important;
    }
    </style>
    """, unsafe_allow_html=True)


# Цвета со скетча главной: синий — вход, бордо — исход. Чуть ярче, чтобы кольцо читалось на тёмной теме.
FLOW_IN_COLOR = "#4453E8"
FLOW_OUT_COLOR = "#C41634"


def _fmt_share(pct):
    if pct <= 0:
        return "0%"
    if pct < 0.1:
        return "<\u00a00.1%"
    return f"{pct:.1f}%"


def _donut_arc(cx, cy, r, sw, start_deg, span_deg, color):
    """Дуга кольца. 0° — 3 часа, дальше по часовой. Путь, не dasharray: так доля не съезжает."""
    if span_deg <= 0.4:
        return ""
    if span_deg >= 359.2:
        return (
            f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="none" stroke="{color}" '
            f'stroke-width="{sw}" />'
        )
    end = start_deg + span_deg
    large = 1 if span_deg > 180 else 0
    x1 = cx + r * math.cos(math.radians(start_deg))
    y1 = cy + r * math.sin(math.radians(start_deg))
    x2 = cx + r * math.cos(math.radians(end))
    y2 = cy + r * math.sin(math.radians(end))
    return (
        f'<path d="M {x1:.2f} {y1:.2f} A {r:.2f} {r:.2f} 0 {large} 1 {x2:.2f} {y2:.2f}" '
        f'fill="none" stroke="{color}" stroke-width="{sw}" stroke-linecap="butt" />'
    )


def build_loss_donut_svg(in_sum, out_sum, center_text):
    """Кольцо потерь: вход слева, исход справа, в центре полная сумма, доли чуть сбоку."""
    in_sum = max(float(in_sum or 0), 0.0)
    out_sum = max(float(out_sum or 0), 0.0)
    total = in_sum + out_sum
    in_pct = (in_sum / total * 100) if total else 0.0
    out_pct = (100.0 - in_pct) if total else 0.0

    cx, cy, r, sw = 200, 200, 112, 50
    gap = 5.0 if in_pct > 1 and out_pct > 1 else 0.0

    def span_of(pct):
        raw = pct / 100 * 360
        if raw <= 0:
            return 0.0
        if gap and raw > gap + 1:
            return raw - gap
        return raw

    in_span, out_span = span_of(in_pct), span_of(out_pct)
    # Синий вход стоит слева (центр 180°), бордо исход — справа (центр 0°).
    in_start = (180 - in_span / 2) % 360
    out_start = (0 - out_span / 2) % 360

    n = len(center_text)
    sum_size = 24 if n <= 12 else 21 if n <= 14 else 17 if n <= 17 else 14
    label = escape(center_text)
    aria = escape(
        f"Диаграмма потерь. Полная сумма списаний {center_text}. "
        f"Вход {_fmt_share(in_pct)}, исход {_fmt_share(out_pct)}."
    )
    arcs = (
        _donut_arc(cx, cy, r, sw, in_start, in_span, FLOW_IN_COLOR)
        + _donut_arc(cx, cy, r, sw, out_start, out_span, FLOW_OUT_COLOR)
    )
    return f'''<div class="loss-figure">
<div class="loss-pct loss-pct-in">{escape(_fmt_share(in_pct))}</div>
<svg class="loss-donut" viewBox="0 0 400 400" role="img" aria-label="{aria}">
  <circle cx="{cx}" cy="{cy}" r="{r}" fill="none" stroke="rgba(128,128,128,0.16)" stroke-width="{sw}"/>
  {arcs}
  <text x="{cx}" y="{cy - 22}" text-anchor="middle" dominant-baseline="central" fill="currentColor" opacity="0.62" font-size="15" font-weight="600">Полная сумма</text>
  <text x="{cx}" y="{cy - 4}" text-anchor="middle" dominant-baseline="central" fill="currentColor" opacity="0.62" font-size="15" font-weight="600">списаний</text>
  <text x="{cx}" y="{cy + 24}" text-anchor="middle" dominant-baseline="central" fill="currentColor" font-size="{sum_size}" font-weight="700">{label}</text>
</svg>
<div class="loss-pct loss-pct-out">{escape(_fmt_share(out_pct))}</div>
</div>'''


def clean_date_col(series):
    if pd.api.types.is_datetime64_any_dtype(series):
        return series
    s = series.astype(str).replace(['NaT', 'nan', 'None'], pd.NA)
    s = s.str.replace(r'\+\d{2}:\d{2}', '', regex=True)
    s = s.str.replace(r'\.\d+', '', regex=True)
    return pd.to_datetime(s, errors='coerce')


def process_excel(file):
    df = pd.read_excel(file)
    df.columns = df.columns.astype(str).str.strip()
    cols_map = {
        'ШК': 'barcode', 'Наименование блока': 'block_name', 'ID блока': 'block_id',
        'ID блока перед списанием': 'status_block_id', 'ID офиса': 'office_id', 'ID сотрудника статуса перед списан': 'status_employee_id',
        'Сумма списания': 'loss_sum', 'Кол-во лог объектов для списания': 'loss_qty',
        'Дата первого списания': 'loss_date', 'Лостризон первого списания': 'reason',
        'Дата статуса перед списанием': 'status_before_date', 'Статус перед списанием': 'status_before'
    }
    existing_cols = [c for c in cols_map if c in df.columns]
    df = df[existing_cols].rename(columns=cols_map)
    for col in ['loss_sum', 'loss_qty']:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)
    for col in ['loss_date', 'status_before_date']:
        if col in df.columns:
            df[col] = clean_date_col(df[col])
    for col in ['block_name', 'reason', 'status_before']:
        if col in df.columns:
            df[col] = df[col].fillna("Не указано").astype(str).str.replace(r'\*', '', regex=True).str.strip()
            if col == 'reason':
                df[col] = df[col].str.replace(r'\.0$', '', regex=True).str.strip()
    if 'office_id' in df.columns:
        raw = df['office_id']
        df['office_id'] = raw.astype(str).str.replace(r'\.0$', '', regex=True).str.strip().where(raw.notna(), None)
    return df


INBOUND_STATUSES = ['ANV', 'EPR', 'IPS', 'ORG', 'PVP', 'RGP', 'RNP', 'RSP', 'RWP', 'TWR', 'WDC', 'WWE', 'WIJ', 'WPT', 'WPU', 'SGR']
OUTBOUND_STATUSES = ['GWS', 'IKZ', 'IPG', 'ISB', 'LAM', 'LIO', 'LIP', 'LPG', 'LSS', 'OPS', 'OSE', 'OSR', 'PAP', 'PWT', 'SAP', 'SAS', 'SBN', 'SMC', 'SPM', 'SPS', 'SSG', 'WLI', 'WLN', 'WLR', 'WLT', 'WMI']

STAGE_MAPPING = {
    'GWS': 'Отгрузка', 'LSS': 'Отгрузка', 'LIO': 'Отгрузка', 'LIP': 'Отгрузка', 'LPG': 'Отгрузка', 'SAP': 'Отгрузка',
    'PAP': 'Предсортировка', 'PWT': 'Предсортировка', 'SBN': 'Предсортировка', 'SAS': 'Предсортировка',
    'SMC': 'Сортировка', 'SPM': 'Сортировка', 'SPS': 'Сортировка', 'SSG': 'Сортировка', 'WLI': 'Сортировка',
    'WLN': 'Сортировка', 'WLR': 'Сортировка', 'WLT': 'Сортировка', 'WMI': 'Сортировка', 'OPS': 'Сортировка',
    'OSE': 'Сортировка', 'OSR': 'Сортировка', 'IKZ': 'Сортировка', 'IPG': 'Сортировка', 'ISB': 'Сортировка',
    'LAM': 'Пропуски сборки'
}
# Оперплощадь = списание после сортировки, предсортировки или отгрузки. Всё остальное — в блок.
OPERKA_STATUSES = ['SMC', 'SPS', 'SAS', 'SAP', 'PAP', 'LSS', 'GWS', 'WMI']
# LR (лостризоны), которые относятся к сборке (мезонин): такие списания ВСЕГДА идут в блок, даже если статус из оперки.
ASSEMBLY_LR = ['5']
# ID блока, где расположена оперплощадь (берётся из «ID блока перед списанием»)
OPERKA_BLOCK_ID = '6800'
# Пропуск сборки (LR из ASSEMBLY_LR) у товара, получившего статус на блоке 6800, — отдельная группа
PICKMISS_GROUP = 'Пропуск сборки товара на блоке'
# Списания других логобъектов (блоков нет в BLOCK_MAPPING) — отдельная группа, внутри делятся по статусам
FOREIGN_GROUP = 'Другие логобъекты'
UNMAPPED_GROUP = 'Блоки вне маппинга'  # наше списание, но названия блока нет в BLOCK_MAPPING
# Наш логобъект: блоки 6800–6826 (по «ID блока» МЕСТА списания) и офис 301760. Списание на другом ЛО — в группу «Другие логобъекты».
OWN_BLOCK_RANGE = (6800, 6826)
OWN_OFFICE_ID = '301760'

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
    "Рязань Тюшевское 4": "Рязань Тюшевское 4"
}

# Структура: 4 блока и их площадки (из BLOCK_MAPPING) — показываются всегда, даже без потерь
ALL_BLOCKS = list(dict.fromkeys(BLOCK_MAPPING.values()))
PLATFORMS = {b: [n for n, v in BLOCK_MAPPING.items() if v == b] for b in ALL_BLOCKS}

COLS = ['loss_date', 'barcode', 'block_name', 'status_block_id', 'loss_sum', 'loss_qty', 'reason',
        'status_before_date', 'status_before', 'block_id', 'office_id', 'flow', 'process_stage', 'block_group']


@st.cache_data(ttl=3600, show_spinner=False)
def load_data(start, end):
    base = ("loss_date, barcode, block_name, status_block_id, loss_sum, loss_qty, reason, "
            "status_before_date, status_before")
    end_excl = (end + timedelta(days=1)).isoformat()
    df, err = None, None
    for cols in (base + ", block_id, office_id", base + ", block_id", base):  # если колонки office_id в базе ещё нет — читаем без неё
        try:
            df = pd.read_sql(text(f"SELECT {cols} FROM losses WHERE loss_date >= :start AND loss_date < :end"),
                             engine, params={"start": start.isoformat(), "end": end_excl})
            break
        except Exception as e:
            err = e
    if df is None:
        st.error(f"Не удалось получить данные из базы: {err}")
        return pd.DataFrame(columns=COLS)
    for c in ('block_id', 'office_id'):
        if c not in df.columns:
            df[c] = None
    if df.empty:
        return pd.DataFrame(columns=COLS)

    df['loss_date'] = pd.to_datetime(df['loss_date'])
    df['reason'] = df['reason'].astype(str).str.replace(r'\.0$', '', regex=True).str.strip()
    df['block_name'] = df['block_name'].astype(str).str.replace(r'\*', '', regex=True).str.strip()
    df['status_before'] = df['status_before'].fillna('').astype(str).str.upper().str.replace(r'[^A-Z]', '', regex=True)
    df.loc[df['status_before'] == '', 'status_before'] = 'НЕ УКАЗАНО'

    df['flow'] = 'Другое'
    df.loc[df['status_before'].isin(INBOUND_STATUSES), 'flow'] = 'Входящий поток'
    df.loc[df['status_before'].isin(OUTBOUND_STATUSES), 'flow'] = 'Исходящий поток'
    df['process_stage'] = df['status_before'].map(STAGE_MAPPING).fillna('Другое')

    own = df['block_name'].map(BLOCK_MAPPING)
    b_id = df['status_block_id'].astype(str).str.strip().str.split('.').str[0]
    is_pickmiss = (b_id == OPERKA_BLOCK_ID) & df['reason'].isin(ASSEMBLY_LR)
    is_operka = df['status_before'].isin(OPERKA_STATUSES) & (b_id == OPERKA_BLOCK_ID) & ~df['reason'].isin(ASSEMBLY_LR)
    # Правила идут по приоритету, срабатывает ПОСЛЕДНЕЕ в цепочке (сильнейшее): так они не конфликтуют.
    # слабейшее: блок вне маппинга -> наш блок по названию -> оперка -> пропуск сборки на 6800 -> чужой ЛО (по блоку/офису)
    # Чужой ЛО определяется по МЕСТУ списания, а не по тому, где был последний статус:
    # блок списания вне 6800–6826 или офис не наш. Если места списания в данных нет — судим по названию блока.
    loss_blk = pd.to_numeric(df['block_id'].astype(str).str.split('.').str[0], errors='coerce')
    off = df['office_id'].fillna('').astype(str).str.strip().str.replace(r'\.0$', '', regex=True)
    foreign_blk = loss_blk.notna() & ((loss_blk < OWN_BLOCK_RANGE[0]) | (loss_blk > OWN_BLOCK_RANGE[1]))
    foreign_off = (off != '') & (off.str.lower() != 'nan') & (off != OWN_OFFICE_ID)
    is_foreign = foreign_blk | foreign_off | (loss_blk.isna() & (off == '') & own.isna())
    df['block_group'] = (own.fillna(UNMAPPED_GROUP).mask(is_operka, 'Опер площадь')
                         .mask(is_pickmiss, PICKMISS_GROUP).mask(is_foreign, FOREIGN_GROUP))
    return df


def get_delta_text(curr_sum, curr_qty, prev_sum, prev_qty):
    """Дельта в рублях, процентах и штуках."""
    diff_sum, diff_qty = curr_sum - prev_sum, curr_qty - prev_qty
    sign = "+" if diff_sum > 0 else ""
    pct = f" ({sign}{diff_sum / prev_sum * 100:.1f}%)" if prev_sum > 0 else ""
    sign_q = "+" if diff_qty > 0 else ""
    return f"{sign}{diff_sum:,.0f} ₽{pct} / {sign_q}{diff_qty:,.0f} шт".replace(",", " ")


@st.cache_data(ttl=600, show_spinner=False)
def get_max_loss_date():
    """Последняя дата списания в базе — для стартового периода при первом заходе."""
    if engine is None:
        return None
    try:
        with engine.connect() as conn:
            max_date = conn.execute(text("SELECT MAX(loss_date) FROM losses")).scalar()
        if max_date is None:
            return None
        return pd.Timestamp(max_date).date()
    except Exception:
        return None


def _init_date_selector_defaults():
    if st.session_state.get("_date_selector_initialized"):
        return
    latest = get_max_loss_date()
    st.session_state.date_mode = "Месяц"
    st.session_state.date_anchor = latest or date.today()
    st.session_state._date_selector_initialized = True


def render_date_selector():
    """Возвращает: начало, конец, начало и конец периода сравнения.
    Неделя и месяц сравниваются по одинаковому числу дней (текущий период обрезается по сегодняшнюю дату).
    При первом заходе: разрез «Месяц» и дата последней выгрузки в базе."""
    _init_date_selector_defaults()
    today = date.today()
    fmt = lambda d: d.strftime('%d.%m.%Y')
    mode = st.radio("Выберите разрез:", ["День", "Неделя", "Месяц"], horizontal=True, key="date_mode")

    if mode == "День":
        t = st.date_input("Выберите дату", key="date_anchor")
        start, end = t, t
        p_start = p_end = t - timedelta(days=1)
        st.info(f"Выбран день: **{fmt(start)}** | Сравнение: **{fmt(p_start)}**")
        return start, end, p_start, p_end

    if mode == "Неделя":
        t = st.date_input("Выберите любой день нужной недели", key="date_anchor")
        start = t - timedelta(days=t.weekday())
        end = min(start + timedelta(days=6), today)
        p_start = start - timedelta(days=7)
        p_end = p_start + (end - start)
    else:
        t = st.date_input("Выберите любой день нужного месяца", key="date_anchor")
        start = t.replace(day=1)
        end = min(t.replace(day=calendar.monthrange(t.year, t.month)[1]), today)
        p_start = start - relativedelta(months=1)
        p_last = start - timedelta(days=1)
        p_end = min(p_start + (end - start), p_last)

    days = (end - start).days + 1
    st.info(f"Период: **{fmt(start)} — {fmt(end)}** ({days} дн.) | Сравнение с теми же днями: **{fmt(p_start)} — {fmt(p_end)}**")
    return start, end, p_start, p_end


@st.cache_data(ttl=600, show_spinner=False)
def get_db_summary():
    try:
        with engine.connect() as conn:
            total_rows, min_date, max_date = conn.execute(
                text("SELECT COUNT(*), MIN(loss_date), MAX(loss_date) FROM losses")).fetchone()
            if total_rows == 0:
                return None, None, None, None
            df_months = pd.read_sql(
                "SELECT TO_CHAR(loss_date, 'YYYY-MM') AS month, COUNT(*) as rows_count "
                "FROM losses GROUP BY TO_CHAR(loss_date, 'YYYY-MM') ORDER BY month DESC", conn)
            return total_rows, min_date, max_date, df_months
    except Exception:
        return None, None, None, None


def render_admin_panel():
    with st.sidebar:
        st.header("👤 Админ-панель")
        if st.button("🔄 Сбросить кэш данных", use_container_width=True):
            st.cache_data.clear()
            st.success("Кэш сброшен!")
            st.rerun()

        with st.expander("📥 Загрузка данных", expanded=True):
            files = st.file_uploader("Excel файлы (.xlsx)", type=["xlsx"], accept_multiple_files=True, label_visibility="collapsed")
            if files:
                df_new = pd.concat([process_excel(f) for f in files], ignore_index=True)
                n_all = len(df_new)
                df_new = df_new.dropna(subset=['loss_date'])
                n_no_date = n_all - len(df_new)
                n_dups = 0
                if 'barcode' in df_new.columns:
                    before = len(df_new)
                    df_new = df_new.drop_duplicates(subset=['barcode'], keep='last')
                    n_dups = before - len(df_new)
                if not df_new.empty:
                    d_min, d_max = df_new['loss_date'].min().date(), df_new['loss_date'].max().date()
                    st.success(f"Прочитано файлов: {len(files)}, строк к загрузке: {len(df_new)}")
                    st.info(f"Период: {d_min.strftime('%d.%m.%Y')} — {d_max.strftime('%d.%m.%Y')}")
                    if n_no_date:
                        st.warning(f"Пропущено строк без даты: {n_no_date}")
                    if n_dups:
                        st.warning(f"Удалено дублей по ШК: {n_dups}")
                    st.caption("⚠️ Загрузка заменит ВСЕ данные в этом периоде. Файл должен покрывать период полностью.")
                    if st.button("💾 ВЫГРУЗИТЬ В БАЗУ", use_container_width=True, type="primary"):
                        with st.spinner("Загрузка..."):
                            with engine.begin() as conn:
                                conn.execute(text("DELETE FROM losses WHERE loss_date::date >= :s AND loss_date::date <= :e"),
                                             {"s": d_min, "e": d_max})
                                df_new.to_sql('losses', conn, if_exists='append', index=False, chunksize=5000)
                            st.cache_data.clear()
                            st.success("🎉 Данные сохранены!")
                            st.rerun()

        with st.expander("ℹ️ Что в базе"):
            total_rows, min_date, max_date, df_months = get_db_summary()
            if not total_rows:
                st.info("База данных пока пуста.")
            else:
                st.markdown(f"**Всего строк:** {total_rows:,}".replace(",", " "))
                if min_date and max_date:
                    st.markdown(f"**Период:** {min_date.strftime('%d.%m.%Y')} — {max_date.strftime('%d.%m.%Y')}")
                if df_months is not None:
                    df_months.columns = ['Месяц', 'Строк']
                    st.dataframe(df_months, hide_index=True, use_container_width=True)
            st.divider()
            if st.checkbox("Я уверен, что хочу удалить всё"):
                if st.button("🗑️ Очистить базу", use_container_width=True):
                    with engine.begin() as conn:
                        conn.execute(text("TRUNCATE TABLE losses RESTART IDENTITY"))
                    st.cache_data.clear()
                    st.success("База очищена!")
                    st.rerun()
