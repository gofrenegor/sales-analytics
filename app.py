import streamlit as st
import pandas as pd
import plotly.express as px
from datetime import date, timedelta
from dateutil.relativedelta import relativedelta
from sqlalchemy import create_engine, text
import calendar

# --- Настройка страницы ---
st.set_page_config(page_title="Аналитика потерь", page_icon="📦", layout="wide", initial_sidebar_state="collapsed")

# --- CSS Стили для кнопок ---
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

# --- Функция загрузки и очистки одного Excel ---
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
        if col in df.columns:
            df[col] = df[col].astype(str).str.replace(r'\+\d{2}:\d{2}', '', regex=True).str.strip()
            df[col] = pd.to_datetime(df[col], errors='coerce')
            
    for col in ['block_name', 'reason', 'status_before']:
        if col in df.columns: 
            df[col] = df[col].fillna("Не указано").astype(str)
            if col == 'reason':
                df[col] = df[col].str.replace(r'\.0$', '', regex=True).str.strip()
                
    return df

# --- Вспомогательные функции ---
def get_delta_text(curr_sum, curr_qty, prev_sum, prev_qty):
    diff_sum = curr_sum - prev_sum
    diff_qty = curr_qty - prev_qty
    sign = "+" if diff_sum > 0 else ""
    sum_str = f"{sign}{diff_sum:,.0f} ₽".replace(",", " ")
    sign_q = "+" if diff_qty > 0 else ""
    qty_str = f"{sign_q}{diff_qty:,.0f} шт".replace(",", " ")
    return f"{sum_str} / {qty_str}"

def get_flow_name(reason):
    if reason in ['36', '46', '47']: return 'Входящий поток'
    elif reason in ['26', '5']: return 'Исходящий поток'
    return 'Другое'

def get_db_summary():
    try:
        with engine.connect() as conn:
            res_total = conn.execute(text("SELECT COUNT(*), MIN(loss_date), MAX(loss_date) FROM losses")).fetchone()
            total_rows, min_date, max_date = res_total
            if total_rows == 0: return None, None, None, None, None
            
            df_months = pd.read_sql("SELECT TO_CHAR(loss_date, 'YYYY-MM') AS month, COUNT(*) as rows_count FROM losses GROUP BY TO_CHAR(loss_date, 'YYYY-MM') ORDER BY month DESC", conn)
            df_weeks = pd.read_sql("SELECT TO_CHAR(DATE_TRUNC('week', loss_date), 'YYYY-MM-DD') AS week_start, COUNT(*) as rows_count FROM losses GROUP BY DATE_TRUNC('week', loss_date) ORDER BY week_start DESC", conn)
            return total_rows, min_date, max_date, df_months, df_weeks
    except Exception:
        return None, None, None, None, None

# ==========================================
# КЭШИРУЕМЫЕ ФУНКЦИИ ДЛЯ ТЯЖЕЛЫХ ВЫЧИСЛЕНИЙ
# ==========================================

@st.cache_data(ttl=3600)
def load_data(start, end):
    # ОПТИМИЗАЦИЯ: Запрашиваем только нужные колонки, а не всё подряд
    cols = "barcode, block_name, reason, loss_date, status_before_date, loss_qty, loss_sum, status_before"
    query = f"SELECT {cols} FROM losses WHERE loss_date >= '{start}' AND loss_date < '{end + timedelta(days=1)}'"
    try:
        df = pd.read_sql(query, engine)
        if not df.empty:
            # Оптимизация памяти: превращаем строки в категории
            df['reason'] = df['reason'].astype(str).str.replace(r'\.0$', '', regex=True).str.strip().astype('category')
            df['block_name'] = df['block_name'].astype('category')
            df['loss_date'] = pd.to_datetime(df['loss_date'])
            if 'status_before_date' in df.columns:
                df['status_before_date'] = pd.to_datetime(df['status_before_date'])
            df['flow'] = df['reason'].apply(get_flow_name)
        return df
    except Exception as e:
        return pd.DataFrame()

@st.cache_data(ttl=3600)
def get_lr5_investigation_data(df_curr):
    if 'status_before_date' not in df_curr.columns: 
        return pd.DataFrame(), {}
    
    df_lr5_raw = df_curr[df_curr['reason'] == '5'].copy()
    total_lr5 = len(df_lr5_raw)
    
    # 1. Отсекаем пустые даты
    df_lr5_clean = df_lr5_raw.dropna(subset=['status_before_date', 'loss_date'])
    dropped_dates = total_lr5 - len(df_lr5_clean)
    
    if df_lr5_clean.empty: 
        return pd.DataFrame(), {"total": total_lr5, "dropped_dates": dropped_dates, "dropped_neg": 0}
    
    # 2. Считаем разницу
    df_lr5_clean['diff_hours'] = (df_lr5_clean['loss_date'] - df_lr5_clean['status_before_date']).dt.total_seconds() / 3600
    
    # 3. Отсекаем отрицательные значения
    df_lr5_positive = df_lr5_clean[df_lr5_clean['diff_hours'] >= 0].copy()
    dropped_neg = len(df_lr5_clean) - len(df_lr5_positive)
    
    # ОПТИМИЗАЦИЯ: Используем pd.cut вместо apply (работает в 10 раз быстрее)
    bins = [-1, 24, 168, 720, float('inf')]
    labels = ['< 24 часов', '1-7 дней', '7-30 дней', '> 30 дней']
    df_lr5_positive['time_category'] = pd.cut(df_lr5_positive['diff_hours'], bins=bins, labels=labels)
    
    stats = {
        "total": total_lr5,
        "dropped_dates": dropped_dates,
        "dropped_neg": dropped_neg,
        "valid": len(df_lr5_positive)
    }
    
    return df_lr5_positive, stats

@st.cache_data(ttl=3600)
def get_charts_data(df_curr):
    if df_curr.empty: return pd.DataFrame(), pd.DataFrame()
    df_flows = df_curr.groupby('flow', observed=True)[['loss_sum']].sum().reset_index()
    df_lr = df_curr.groupby('reason', observed=True)[['loss_sum']].sum().reset_index().sort_values('loss_sum', ascending=False)
    return df_flows, df_lr

@st.cache_data(ttl=3600)
def get_dynamic_chart_data(df_curr, selected_lr="Все"):
    if df_curr.empty: return pd.DataFrame()
    df_chart = df_curr.copy()
    if selected_lr != "Все": 
        df_chart = df_chart[df_chart['reason'] == selected_lr]
    df_daily = df_chart.groupby(df_chart['loss_date'].dt.date, observed=True)[['loss_sum']].sum().reset_index()
    df_daily['time_label'] = pd.to_datetime(df_daily['loss_date']).dt.strftime('%d.%m.%Y')
    return df_daily

# ==========================================
# ШАПКА САЙТА И АДМИН-ПАНЕЛЬ
# ==========================================
col_title, col_admin = st.columns([8, 1])

with col_title:
    st.title("📦 Аналитика списаний")

with col_admin:
    st.write("") 
    with st.popover("👤", use_container_width=True):
        admin_tab_upload, admin_tab_info = st.tabs(["📥 Загрузка", "ℹ️ Что в базе"])
        
        with admin_tab_upload:
            uploaded_files = st.file_uploader("Excel файлы (.xlsx)", type=["xlsx"], accept_multiple_files=True)
            
            if uploaded_files:
                try:
                    all_dfs = [process_excel(file) for file in uploaded_files]
                    df_new = pd.concat(all_dfs, ignore_index=True)
                    
                    if 'loss_date' not in df_new.columns:
                        st.error("❌ Ошибка: В файлах нет колонки 'Дата первого списания'.")
                    else:
                        df_new_valid = df_new.dropna(subset=['loss_date'])
                        
                        if df_new_valid.empty:
                            st.error("❌ Ошибка: Не удалось распознать даты.")
                        else:
                            min_date = df_new_valid['loss_date'].min().date()
                            max_date = df_new_valid['loss_date'].max().date()
                            
                            st.success(f"✅ Прочитано файлов: {len(uploaded_files)}")
                            st.info(f"Период: **{min_date.strftime('%d.%m.%Y')} — {max_date.strftime('%d.%m.%Y')}**")
                            
                            if st.button("💾 ВЫГРУЗИТЬ В БАЗУ", use_container_width=True, type="primary"):
                                if engine is not None:
                                    try:
                                        with st.spinner("Загрузка..."):
                                            with engine.begin() as conn:
                                                conn.execute(text(f"DELETE FROM losses WHERE loss_date::date >= '{min_date}' AND loss_date::date <= '{max_date}'"))
                                                df_new_valid.to_sql('losses', con=conn, if_exists='append', index=False, chunksize=5000)
                                        
                                        st.cache_data.clear()
                                        st.success("🎉 Данные сохранены!")
                                        st.rerun()
                                    except Exception as e:
                                        st.error(f"Ошибка: {e}")
                except Exception as e:
                    st.error(f"Ошибка чтения: {e}")
            
            st.divider()
            if st.button("🗑️ Очистить базу", disabled=not st.checkbox("Удалить всё"), use_container_width=True):
                if engine is not None:
                    try:
                        with engine.begin() as conn:
                            conn.execute(text("TRUNCATE TABLE losses RESTART IDENTITY"))
                        st.cache_data.clear()
                        st.success("База очищена!")
                        st.rerun()
                    except Exception as e:
                        st.error(f"Ошибка: {e}")

        with admin_tab_info:
            total_rows, min_date, max_date, df_months, df_weeks = get_db_summary()
            if not total_rows:
                st.info("База пуста.")
            else:
                st.markdown(f"**Всего строк:** {total_rows:,}".replace(",", " "))
                if min_date and max_date: st.markdown(f"**Период:** {min_date.strftime('%d.%m.%Y')} — {max_date.strftime('%d.%m.%Y')}")
                st.divider()
                st.markdown("**Месяцы:**")
                if df_months is not None: st.dataframe(df_months.rename(columns={'month':'Месяц','rows_count':'Строк'}), hide_index=True, use_container_width=True)

# ==========================================
# ОСНОВНОЙ ИНТЕРФЕЙС (Даты)
# ==========================================
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

if engine is None: st.stop()

df_curr = load_data(start_date, end_date)
df_prev = load_data(prev_start_date, prev_end_date)

if df_curr.empty and df_prev.empty:
    st.warning("⚠️ Нет данных за этот период.")
    st.stop()

st.divider()

# ==========================================
# ВКЛАДКИ
# ==========================================
tab_analytics, tab_charts, tab_investigation = st.tabs(["📊 Аналитика потерь", "📈 Графики потерь", "🕵️ Потери < 24ч (LR 
