import streamlit as st
import pandas as pd
from datetime import date, datetime, timedelta
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
        .st-key-btn_inbound button {
            background-color: #2e7d32 !important; color: white !important; border: 1px solid #2e7d32 !important;
        }
        .st-key-btn_inbound button:hover { background-color: #1b5e20 !important; border: 1px solid #1b5e20 !important; }
        .st-key-btn_outbound button {
            background-color: #1565c0 !important; color: white !important; border: 1px solid #1565c0 !important;
        }
        .st-key-btn_outbound button:hover { background-color: #0d47a1 !important; border: 1px solid #0d47a1 !important; }
        </style>
    """, unsafe_allow_html=True)


def clean_date_col(series):
    if pd.api.types.is_datetime64_any_dtype(series): return series
    s = series.astype(str).replace(['NaT', 'nan', 'None'], pd.NA)
    s = s.str.replace(r'\+\d{2}:\d{2}', '', regex=True)
    s = s.str.replace(r'\.\d+', '', regex=True)
    return pd.to_datetime(s, errors='coerce')


def process_excel(file):
    df = pd.read_excel(file)
    df.columns = df.columns.astype(str).str.strip()
    cols_map = {
        'ШК': 'barcode',
        'Наименование блока': 'block_name',
        'ID блока': 'block_id',
        'ID блока перед списанием': 'status_block_id',
        'ID сотрудника статуса перед списан': 'status_employee_id',
        'Сумма списания': 'loss_sum',
        'Кол-во лог объектов для списания': 'loss_qty',
        'Дата первого списания': 'loss_date',
        'Лостризон первого списания': 'reason',
        'Дата статуса перед списанием': 'status_before_date',
        'Статус перед списанием': 'status_before'
    }
    existing_cols = [c for c in cols_map.keys() if c in df.columns]
    df = df[existing_cols].rename(columns=cols_map)

    for col in ['loss_sum', 'loss_qty']:
        if col in df.columns: df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)

    for col in ['loss_date', 'status_before_date']:
        if col in df.columns: df[col] = clean_date_col(df[col])

    for col in ['block_name', 'reason', 'status_before']:
        if col in df.columns:
            df[col] = df[col].fillna("Не указано").astype(str)
            # Очищаем от звездочек и пробелов
            df[col] = df[col].str.replace(r'\*', '', regex=True).str.strip()
            if col == 'reason': df[col] = df[col].str.replace(r'\.0$', '', regex=True).str.strip()
    return df


# ВАШИ ТОЧНЫЕ СПИСКИ
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

OPERKA_STATUSES = ['GWS', 'LSS', 'PAP', 'PWT', 'SAP', 'SAS', 'SMC', 'SPS']

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
    "Рязань 3 КГТ": "Рязань Тюшевское 3",  # <--- ДОБАВЛЕНО
    "Рязань Тюшевское 4": "Рязань Тюшевское 4"
}


def get_flow_name(status_before):
    status = str(status_before).strip().upper()
    if status in ('НЕ УКАЗАНО', '', 'NAN'):
        return 'Другое'
    if status in INBOUND_STATUSES:
        return 'Входящий поток'
    if status in OUTBOUND_STATUSES:
        return 'Исходящий поток'
    return 'Другое'


def _as_date_str(d):
    try:
        if isinstance(d, str):
            return d[:10]
        return f"{d.year:04d}-{d.month:02d}-{d.day:02d}"
    except (AttributeError, ValueError, TypeError):
        return str(d)[:10]


def _next_day_str(d):
    try:
        if isinstance(d, str):
            base = date.fromisoformat(d[:10])
        else:
            base = date(d.year, d.month, d.day)
    except (AttributeError, ValueError, TypeError):
        base = date.fromisoformat(str(d)[:10])
    return (base + timedelta(days=1)).isoformat()


@st.cache_data(ttl=3600, show_spinner=False)
def load_data(start, end, _v="v4"):  # _v="v4" принудительно сбросит старый кэш
    start_str = _as_date_str(start)
    end_excl_str = _next_day_str(end)

    query = text("""
        SELECT loss_date, barcode, block_name, status_block_id,
               loss_sum, loss_qty, reason,
               status_before_date, status_before
        FROM losses
        WHERE loss_date >= :start AND loss_date < :end
    """)

    try:
        df = pd.read_sql(query, engine, params={"start": start_str, "end": end_excl_str})
    except Exception:
        return pd.DataFrame()

    if df.empty:
        return df

    df['loss_date'] = pd.to_datetime(df['loss_date'])
    if 'status_before_date' in df.columns:
        df['status_before_date'] = pd.to_datetime(df['status_before_date'])
    
    # Очищаем reason и block_name от лишних символов
    df['reason'] = df['reason'].astype(str).str.replace(r'\.0$', '', regex=True).str.strip()
    df['block_name'] = df['block_name'].astype(str).str.replace(r'\*', '', regex=True).str.strip()

    if 'status_before' in df.columns:
        df['status_before'] = df['status_before'].fillna('').astype(str)
        df['status_before'] = df['status_before'].str.upper().str.replace(r'[^A-Z]', '', regex=True)
        df.loc[df['status_before'] == '', 'status_before'] = 'НЕ УКАЗАНО'

        # Векторизованный аналог get_flow_name
        df['flow'] = 'Другое'
        df.loc[df['status_before'].isin(INBOUND_STATUSES), 'flow'] = 'Входящий поток'
        df.loc[df['status_before'].isin(OUTBOUND_STATUSES), 'flow'] = 'Исходящий поток'

        # Векторизованный аналог STAGE_MAPPING
        df['process_stage'] = df['status_before'].map(STAGE_MAPPING).fillna('Другое')

        # Векторизованный аналог get_block_group
        # Теперь block_name уже очищен от звездочек
        mapped = df['block_name'].map(BLOCK_MAPPING).fillna('Другое')

        if 'status_block_id' in df.columns:
            status_b_id = df['status_block_id'].astype(str).str.strip().str.split('.').str[0]
            is_operka_6800 = status_b_id == '6800'
        else:
            is_operka_6800 = pd.Series(False, index=df.index)

        is_lam = df['status_before'] == 'LAM'
        is_operka = (~is_lam) & (is_operka_6800 | df['status_before'].isin(OPERKA_STATUSES))

        df['block_group'] = mapped.where(~is_operka, 'Опер площадь')
    else:
        df['flow'] = 'Другое'
        df['process_stage'] = 'Другое'
        df['block_group'] = df['block_name'].map(BLOCK_MAPPING).fillna('Другое') if 'block_name' in df.columns else 'Другое'

    return df


def get_delta_text(curr_sum, curr_qty, prev_sum, prev_qty):
    diff_sum = curr_sum - prev_sum
    diff_qty = curr_qty - prev_qty
    sign = "+" if diff_sum > 0 else ""
    sum_str = f"{sign}{diff_sum:,.0f} ₽".replace(",", " ")
    sign_q = "+" if diff_qty > 0 else ""
    qty_str = f"{sign_q}{diff_qty:,.0f} шт".replace(",", " ")
    return f"{sum_str} / {qty_str}"


def render_date_selector():
    date_mode = st.radio("Выберите разрез:", ["День", "Неделя", "Месяц"], horizontal=True)
    start_date, end_date, prev_start_date, prev_end_date = None, None, None, None

    if date_mode == "День":
        target_date = st.date_input("Выберите дату", value=date.today())
        start_date, end_date = target_date, target_date
        prev_start_date = target_date - timedelta(days=1)
        prev_end_date = prev_start_date
        st.info(f"Выбран день: **{start_date.strftime('%d.%m.%Y')}** | Сравнение: **{prev_start_date.strftime('%d.%m.%Y')}**")
    elif date_mode == "Неделя":
        target_date = st.date_input("Выберите любой день нужной недели", value=date.today())
        start_date = target_date - timedelta(days=target_date.weekday())
        end_date = start_date + timedelta(days=6)
        prev_start_date = start_date - timedelta(days=7)
        prev_end_date = start_date - timedelta(days=1)
        st.info(f"Текущая неделя: **{start_date.strftime('%d.%m.%Y')} — {end_date.strftime('%d.%m.%Y')}**")
    elif date_mode == "Месяц":
        target_date = st.date_input("Выберите любой день нужного месяца", value=date.today())
        start_date = target_date.replace(day=1)
        end_date = target_date.replace(day=calendar.monthrange(target_date.year, target_date.month)[1])
        prev_start_date = (start_date - relativedelta(months=1)).replace(day=1)
        prev_end_date = start_date - timedelta(days=1)
        st.info(f"Текущий месяц: **{start_date.strftime('%d.%m.%Y')} — {end_date.strftime('%d.%m.%Y')}**")

    return start_date, end_date, prev_start_date, prev_end_date


@st.cache_data(ttl=600, show_spinner=False)
def get_db_summary():
    try:
        with engine.connect() as conn:
            res_total = conn.execute(text("SELECT COUNT(*), MIN(loss_date), MAX(loss_date) FROM losses")).fetchone()
            total_rows, min_date, max_date = res_total
            if total_rows == 0:
                return None, None, None, None
            df_months = pd.read_sql(
                "SELECT TO_CHAR(loss_date, 'YYYY-MM') AS month, COUNT(*) as rows_count "
                "FROM losses GROUP BY TO_CHAR(loss_date, 'YYYY-MM') ORDER BY month DESC",
                conn,
            )
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
            uploaded_files = st.file_uploader("Excel файлы (.xlsx)", type=["xlsx"], accept_multiple_files=True, label_visibility="collapsed")
            if uploaded_files:
                all_dfs = [process_excel(file) for file in uploaded_files]
                df_new = pd.concat(all_dfs, ignore_index=True).dropna(subset=['loss_date'])
                if not df_new.empty:
                    min_date = df_new['loss_date'].min().date()
                    max_date = df_new['loss_date'].max().date()
                    st.success(f"Прочитано файлов: {len(uploaded_files)}")
                    st.info(f"Период: {min_date.strftime('%d.%m.%Y')} — {max_date.strftime('%d.%m.%Y')}")
                    if st.button("💾 ВЫГРУЗИТЬ В БАЗУ", use_container_width=True, type="primary"):
                        with st.spinner("Загрузка..."):
                            with engine.begin() as conn:
                                conn.execute(text(
                                    "DELETE FROM losses WHERE loss_date::date >= :s AND loss_date::date <= :e"
                                ), {"s": min_date, "e": max_date})
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
