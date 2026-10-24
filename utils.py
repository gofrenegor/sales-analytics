import streamlit as st
import pandas as pd
from datetime import date, timedelta
from dateutil.relativedelta import relativedelta
from sqlalchemy import create_engine, text
import calendar

# --- Подключение к базе данных ---
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

# --- CSS Стили ---
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

# --- Парсер дат (убирает часовые пояса) ---
def clean_date_col(series):
    if pd.api.types.is_datetime64_any_dtype(series): return series
    s = series.astype(str).replace(['NaT', 'nan', 'None'], pd.NA)
    s = s.str.replace(r'\+\d{2}:\d{2}', '', regex=True) # убираем +03:00
    s = s.str.replace(r'\.\d+', '', regex=True)       # убираем микросекунды
    return pd.to_datetime(s, errors='coerce')

# --- Функция очистки Excel ---
def process_excel(file):
    df = pd.read_excel(file)
    df.columns = df.columns.astype(str).str.strip()
    cols_map = {
        'ШК': 'barcode', 'Наименование блока': 'block_name', 'ID блока': 'block_id',
        'Сумма списания': 'loss_sum', 'Кол-во лог объектов для списания': 'loss_qty',
        'Дата первого списания': 'loss_date', 'Лостризон первого списания': 'reason',        
        'Дата статуса перед списанием': 'status_before_date', 'Статус перед списанием': 'status_before'
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
            if col == 'reason': df[col] = df[col].str.replace(r'\.0$', '', regex=True).str.strip()
    return df

# --- Загрузка данных из БД ---
@st.cache_data(ttl=3600)
def load_data(start, end):
    query = f"SELECT * FROM losses WHERE loss_date >= '{start}' AND loss_date < '{end + timedelta(days=1)}'"
    try:
        df = pd.read_sql(query, engine)
        if not df.empty:
            df['loss_date'] = pd.to_datetime(df['loss_date'])
            if 'status_before_date' in df.columns:
                df['status_before_date'] = pd.to_datetime(df['status_before_date'])
            df['reason'] = df['reason'].astype(str).str.replace(r'\.0$', '', regex=True).str.strip()
            df['flow'] = df['reason'].apply(get_flow_name)
        return df
    except Exception:
        return pd.DataFrame()

# --- Вспомогательные функции ---
def get_flow_name(reason):
    if reason in ['36', '46', '47']: return 'Входящий поток'
    elif reason in ['26', '5']: return 'Исходящий поток'
    return 'Другое'

def get_delta_text(curr_sum, curr_qty, prev_sum, prev_qty):
    diff_sum = curr_sum - prev_sum
    diff_qty = curr_qty - prev_qty
    sign = "+" if diff_sum > 0 else ""
    sum_str = f"{sign}{diff_sum:,.0f} ₽".replace(",", " ")
    sign_q = "+" if diff_qty > 0 else ""
    qty_str = f"{sign_q}{diff_qty:,.0f} шт".replace(",", " ")
    return f"{sum_str} / {qty_str}"

# --- Глобальный выбор дат (синхронизирует все страницы) ---
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

# --- Админ-панель (доступна везде) ---
def render_admin_panel():
    with st.sidebar:
        st.header("👤 Админ-панель")
        with st.expander("📥 Загрузка данных"):
            uploaded_files = st.file_uploader("Excel файлы (.xlsx)", type=["xlsx"], accept_multiple_files=True)
            if uploaded_files:
                all_dfs = [process_excel(file) for file in uploaded_files]
                df_new = pd.concat(all_dfs, ignore_index=True)
                df_new_valid = df_new.dropna(subset=['loss_date'])
                
                if not df_new_valid.empty:
                    min_date = df_new_valid['loss_date'].min().date()
                    max_date = df_new_valid['loss_date'].max().date()
                    st.info(f"Период: **{min_date.strftime('%d.%m.%Y')} — {max_date.strftime('%d.%m.%Y')}**")
                    if st.button("💾 ВЫГРУЗИТЬ", use_container_width=True, type="primary"):
                        with st.spinner("Загрузка..."):
                            with engine.begin() as conn:
                                conn.execute(text(f"DELETE FROM losses WHERE loss_date::date >= '{min_date}' AND loss_date::date <= '{max_date}'"))
                                df_new_valid.to_sql('losses', con=conn, if_exists='append', index=False, chunksize=5000)
                        st.cache_data.clear()
                        st.rerun()
            if st.button("🗑️ Очистить базу", disabled=not st.checkbox("Удалить всё")):
                with engine.begin() as conn:
                    conn.execute(text("TRUNCATE TABLE losses RESTART IDENTITY"))
                st.cache_data.clear()
                st.rerun()
