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
    </style>
    """, unsafe_allow_html=True)


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
        'ID блока перед списанием': 'status_block_id', 'ID сотрудника статуса перед списан': 'status_employee_id',
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
    return df


INBOUND_STATUSES = ['ANV', 'EPR', 'IPS', 'ORG', 'PVP', 'RGP', 'RNP', 'RSP', 'RWP', 'TWR', 'WDC', 'WWE', 'WIJ', 'WPT', 'WPU']
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

COLS = ['loss_date', 'barcode', 'block_name', 'status_block_id', 'loss_sum', 'loss_qty', 'reason',
        'status_before_date', 'status_before', 'flow', 'process_stage', 'block_group']


@st.cache_data(ttl=3600, show_spinner=False)
def load_data(start, end):
    query = text("""
        SELECT loss_date, barcode, block_name, status_block_id, loss_sum, loss_qty, reason,
               status_before_date, status_before
        FROM losses WHERE loss_date >= :start AND loss_date < :end
    """)
    end_excl = (end + timedelta(days=1)).isoformat()
    try:
        df = pd.read_sql(query, engine, params={"start": start.isoformat(), "end": end_excl})
    except Exception as e:
        st.error(f"Не удалось получить данные из базы: {e}")
        return pd.DataFrame(columns=COLS)
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

    df['block_group'] = df['block_name'].map(BLOCK_MAPPING).fillna('Другое')
    b_id = df['status_block_id'].astype(str).str.strip().str.split('.').str[0]
    # Оперка = статус из OPERKA_STATUSES + блок перед списанием 6800 + не сборка (LR). Всё остальное — в обычные блоки.
    is_operka = df['status_before'].isin(OPERKA_STATUSES) & (b_id == OPERKA_BLOCK_ID) & ~df['reason'].isin(ASSEMBLY_LR)
    df.loc[is_operka, 'block_group'] = 'Опер площадь'
    df.loc[(b_id == OPERKA_BLOCK_ID) & df['reason'].isin(ASSEMBLY_LR), 'block_group'] = PICKMISS_GROUP
    return df


def get_delta_text(curr_sum, curr_qty, prev_sum, prev_qty):
    """Дельта в рублях, процентах и штуках."""
    diff_sum, diff_qty = curr_sum - prev_sum, curr_qty - prev_qty
    sign = "+" if diff_sum > 0 else ""
    pct = f" ({sign}{diff_sum / prev_sum * 100:.1f}%)" if prev_sum > 0 else ""
    sign_q = "+" if diff_qty > 0 else ""
    return f"{sign}{diff_sum:,.0f} ₽{pct} / {sign_q}{diff_qty:,.0f} шт".replace(",", " ")


def render_date_selector():
    """Возвращает: начало, конец, начало и конец периода сравнения.
    Неделя и месяц сравниваются по одинаковому числу дней (текущий период обрезается по сегодняшнюю дату)."""
    mode = st.radio("Выберите разрез:", ["День", "Неделя", "Месяц"], horizontal=True)
    today = date.today()
    fmt = lambda d: d.strftime('%d.%m.%Y')

    if mode == "День":
        t = st.date_input("Выберите дату", value=today)
        start, end = t, t
        p_start = p_end = t - timedelta(days=1)
        st.info(f"Выбран день: **{fmt(start)}** | Сравнение: **{fmt(p_start)}**")
        return start, end, p_start, p_end

    if mode == "Неделя":
        t = st.date_input("Выберите любой день нужной недели", value=today)
        start = t - timedelta(days=t.weekday())
        end = min(start + timedelta(days=6), today)
        p_start = start - timedelta(days=7)
        p_end = p_start + (end - start)
    else:
        t = st.date_input("Выберите любой день нужного месяца", value=today)
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
