import streamlit as st
import pandas as pd
import plotly.express as px
from datetime import date, timedelta
from dateutil.relativedelta import relativedelta
from sqlalchemy import create_engine
import calendar

# --- Настройка страницы ---
st.set_page_config(page_title="Аналитика потерь", page_icon="📦", layout="wide")

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

# --- Функция загрузки и очистки Excel ---
def process_excel(file):
    df = pd.read_excel(file)
    df.columns = df.columns.astype(str).str.strip()
    cols_map = {
        'ШК': 'barcode', 'Наименование блока': 'block_name', 'ID блока': 'block_id',
        'Сумма списания': 'loss_sum', 'Кол-во лог объектов для списания': 'loss_qty',
        'Дата последнего списания': 'loss_date', 'Лостризон последнего списания': 'reason'
    }
    existing_cols = [c for c in cols_map.keys() if c in df.columns]
    df = df[existing_cols].rename(columns=cols_map)
    for col in ['loss_sum', 'loss_qty']:
        if col in df.columns: df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)
    if 'loss_date' in df.columns: df['loss_date'] = pd.to_datetime(df['loss_date'], errors='coerce').dt.date
    for col in ['block_name', 'reason']:
        if col in df.columns: df[col] = df[col].fillna("Не указано").astype(str)
    return df

# --- Вспомогательная функция для расчета дельты ---
def get_delta_text(curr_sum, curr_qty, prev_sum, prev_qty):
    diff_sum = curr_sum - prev_sum
    diff_qty = curr_qty - prev_qty
    
    sign = "+" if diff_sum > 0 else ""
    sum_str = f"{sign}{diff_sum:,.0f} ₽".replace(",", " ")
    
    sign_q = "+" if diff_qty > 0 else ""
    qty_str = f"{sign_q}{diff_qty:,.0f} шт".replace(",", " ")
    
    return f"{sum_str} / {qty_str}"

# --- Боковая панель для загрузки данных ---
with st.sidebar:
    st.header("📥 Загрузка данных")
    uploaded_file = st.file_uploader("Excel файл (.xlsx)", type=["xlsx"])
    
    if uploaded_file:
        try:
            df_new = process_excel(uploaded_file)
            st.success(f"Файл прочитан: {len(df_new)} строк")
            
            if st.button("💾 Сохранить в базу", use_container_width=True):
                if engine is None:
                    st.error("Нет подключения к базе.")
                else:
                    try:
                        df_to_save = df_new.dropna(subset=['loss_date'])
                        df_to_save.to_sql('losses', engine, if_exists='append', index=False)
                        st.success("✅ Успешно сохранено!")
                        st.cache_data.clear()
                    except Exception as e:
                        st.error(f"Ошибка сохранения: {e}")
        except Exception as e:
            st.error(f"Ошибка: {e}")

# --- Инициализация состояний ---
if 'view' not in st.session_state: st.session_state.view = 'dashboard'
if 'selected_flow' not in st.session_state: st.session_state.selected_flow = None
if 'selected_lr' not in st.session_state: st.session_state.selected_lr = None

# --- Главный интерфейс ---
st.title("📦 Аналитика списаний")

# ==========================================
# ВЫБОР ПЕРИОДА (Разрезы)
# ==========================================
date_mode = st.radio(
    "Выберите разрез:", 
    ["Произвольный период", "Неделя", "Месяц"], 
    horizontal=True
)

start_date, end_date = None, None
prev_start_date, prev_end_date = None, None

if date_mode == "Произвольный период":
    col_d1, col_d2 = st.columns(2)
    with col_d1:
        start_date = st.date_input("Начало периода", value=date.today() - timedelta(days=30))
    with col_d2:
        end_date = st.date_input("Конец периода", value=date.today())
        
    if start_date > end_date:
        st.error("Дата начала не может быть позже даты окончания.")
        st.stop()
        
    duration = (end_date - start_date).days + 1
    prev_end_date = start_date - timedelta(days=1)
    prev_start_date = prev_end_date - timedelta(days=duration - 1)
    
    st.info(f"Сравнение: **{start_date.strftime('%d.%m.%Y')} - {end_date.strftime('%d.%m.%Y')}** (с прошлым периодом такой же длины)")

elif date_mode == "Неделя":
    target_date = st.date_input("Выберите любой день нужной недели", value=date.today())
    # Находим понедельник этой недели
    start_date = target_date - timedelta(days=target_date.weekday())
    end_date = start_date + timedelta(days=6)
    # Предыдущая неделя
    prev_start_date = start_date - timedelta(days=7)
    prev_end_date = start_date - timedelta(days=1)
    
    st.info(f"Текущая неделя: **{start_date.strftime('%d.%m.%Y')} — {end_date.strftime('%d.%m.%Y')}**\n\n"
            f"Сравнение с прошлой неделей: **{prev_start_date.strftime('%d.%m.%Y')} — {prev_end_date.strftime('%d.%m.%Y')}**")

elif date_mode == "Месяц":
    target_date = st.date_input("Выберите любой день нужного месяца", value=date.today())
    start_date = target_date.replace(day=1)
    _, last_day = calendar.monthrange(target_date.year, target_date.month)
    end_date = target_date.replace(day=last_day)
    # Предыдущий месяц
    prev_start_date = (start_date - relativedelta(months=1)).replace(day=1)
    prev_end_date = start_date - timedelta(days=1)
    
    st.info(f"Текущий месяц: **{start_date.strftime('%d.%m.%Y')} — {end_date.strftime('%d.%m.%Y')}**\n\n"
            f"Сравнение с прошлым месяцем: **{prev_start_date.strftime('%d.%m.%Y')} — {prev_end_date.strftime('%d.%m.%Y')}**")

if engine is None:
    st.error("Нет подключения к базе данных.")
    st.stop()

# Загружаем данные
@st.cache_data(ttl=60)
def load_data(start, end):
    query = f"SELECT * FROM losses WHERE loss_date >= '{start}' AND loss_date <= '{end}'"
    try:
        df = pd.read_sql(query, engine)
        if not df.empty:
            df['loss_date'] = pd.to_datetime(df['loss_date'])
            df['reason'] = df['reason'].astype(str).str.strip()
        return df
    except Exception as e:
        return pd.DataFrame()

df_curr = load_data(start_date, end_date)
df_prev = load_data(prev_start_date, prev_end_date)

if df_curr.empty and df_prev.empty:
    st.info("Нет данных за выбранный или предыдущий период. Загрузите файлы в боковой панели.")
    st.stop()

st.divider()

# ==========================================
# ЭКРАН 1: ДАШБОРД
# ==========================================
if st.session_state.view == 'dashboard':
    total_sum = df_curr['loss_sum'].sum() if not df_curr.empty else 0
    total_qty = df_curr['loss_qty'].sum() if not df_curr.empty else 0
    
    prev_sum = df_prev['loss_sum'].sum() if not df_prev.empty else 0
    prev_qty = df_prev['loss_qty'].sum() if not df_prev.empty else 0
    
    st.markdown("### Общие списания за период")
    st.metric(
        label="Сумма", 
        value=f"{total_sum:,.2f} ₽".replace(",", " "),
        delta=get_delta_text(total_sum, total_qty, prev_sum, prev_qty),
        delta_color="inverse"
    )
    
    st.markdown("---")
    st.subheader("Выберите поток для детализации:")
    
    col1, col2 = st.columns(2)
    with col1:
        if st.button("⬇️ ВХОДЯЩИЙ ПОТОК", use_container_width=True):
            st.session_state.selected_flow = "in"
            st.session_state.view = 'flow'
            st.rerun()
    with col2:
        if st.button("⬆️ ИСХОДЯЩИЙ ПОТОК", use_container_width=True):
            st.session_state.selected_flow = "out"
            st.session_state.view = 'flow'
            st.rerun()

# ==========================================
# ЭКРАН 2: ВЫБОР ПРИЧИНЫ (LR)
# ==========================================
elif st.session_state.view == 'flow':
    if st.button("⬅️ Назад на главную"):
        st.session_state.view = 'dashboard'
        st.rerun()
        
    flow_name = "Входящий поток" if st.session_state.selected_flow == "in" else "Исходящий поток"
    st.subheader(f"Выбран: {flow_name}")
    st.markdown("Выберите причину списания (LR):")
    
    if st.session_state.selected_flow == "in":
        available_lrs = ['36', '46', '47']
    else:
        available_lrs = ['26', '5']
        
    cols = st.columns(len(available_lrs))
    for i, lr in enumerate(available_lrs):
        with cols[i]:
            lr_df_curr = df_curr[df_curr['reason'] == lr] if not df_curr.empty else pd.DataFrame()
            lr_sum_curr = lr_df_curr['loss_sum'].sum() if not lr_df_curr.empty else 0
            lr_qty_curr = lr_df_curr['loss_qty'].sum() if not lr_df_curr.empty else 0
            
            lr_df_prev = df_prev[df_prev['reason'] == lr] if not df_prev.empty else pd.DataFrame()
            lr_sum_prev = lr_df_prev['loss_sum'].sum() if not lr_df_prev.empty else 0
            lr_qty_prev = lr_df_prev['loss_qty'].sum() if not lr_df_prev.empty else 0
            
            delta_text = get_delta_text(lr_sum_curr, lr_qty_curr, lr_sum_prev, lr_qty_prev)
            
            st.metric(
                label=f"LR {lr}",
                value=f"{lr_sum_curr:,.0f} ₽".replace(",", " "),
                delta=delta_text,
                delta_color="inverse"
            )
            
            if st.button(f"Подробнее", key=f"btn_{lr}", use_container_width=True):
                st.session_state.selected_lr = lr
                st.session_state.view = 'lr_detail'
                st.rerun()

# ==========================================
# ЭКРАН 3: ДЕТАЛЬНАЯ АНАЛИТИКА ПО LR
# ==========================================
elif st.session_state.view == 'lr_detail':
    if st.button("⬅️ Назад к выбору LR"):
        st.session_state.view = 'flow'
        st.rerun()
        
    lr = st.session_state.selected_lr
    df_filtered_curr = df_curr[df_curr['reason'] == lr] if not df_curr.empty else pd.DataFrame()
    df_filtered_prev = df_prev[df_prev['reason'] == lr] if not df_prev.empty else pd.DataFrame()
    
    st.subheader(f"Детализация по причине (LR): {lr}")
    
    lr_sum = df_filtered_curr['loss_sum'].sum() if not df_filtered_curr.empty else 0
    lr_qty = df_filtered_curr['loss_qty'].sum() if not df_filtered_curr.empty else 0
    prev_sum = df_filtered_prev['loss_sum'].sum() if not df_filtered_prev.empty else 0
    prev_qty = df_filtered_prev['loss_qty'].sum() if not df_filtered_prev.empty else 0
    
    st.metric(
        label=f"Сумма по LR {lr}", 
        value=f"{lr_sum:,.2f} ₽".replace(",", " "),
        delta=get_delta_text(lr_sum, lr_qty, prev_sum, prev_qty),
        delta_color="inverse"
    )
    
    if df_filtered_curr.empty:
        st.warning(f"По причине (LR) {lr} нет данных за текущий период.")
        st.stop()
    
    st.markdown("---")
    
    st.markdown("#### Динамика списаний")
    df_grouped = df_filtered_curr.groupby('loss_date')['loss_sum'].sum().reset_index()
    
    fig = px.line(df_grouped, x='loss_date', y='loss_sum', markers=True,
                  labels={'loss_sum': 'Сумма (₽)', 'loss_date': 'Дата'})
    st.plotly_chart(fig, use_container_width=True)
    
    st.markdown("#### Сводка по блокам")
    if 'block_name' in df_filtered_curr:
        df_blocks = df_filtered_curr.groupby('block_name')[['loss_sum', 'loss_qty']].sum().reset_index()
        df_blocks = df_blocks.sort_values('loss_sum', ascending=False)
        df_blocks.columns = ['Блок', 'Сумма потерь (₽)', 'Кол-во (шт)']
        st.dataframe(df_blocks, use_container_width=True, hide_index=True, height=300)
