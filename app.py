import streamlit as st
import pandas as pd
import plotly.express as px
from datetime import date, timedelta
from sqlalchemy import create_engine

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

# --- Инициализация состояний (для навигации) ---
if 'view' not in st.session_state:
    st.session_state.view = 'dashboard'
if 'selected_flow' not in st.session_state:
    st.session_state.selected_flow = None
if 'selected_lr' not in st.session_state:
    st.session_state.selected_lr = None

# --- Главный интерфейс ---
st.title("📦 Аналитика списаний")

# Фильтры по датам (всегда наверху)
col_d1, col_d2 = st.columns(2)
with col_d1:
    start_date = st.date_input("Начало периода", value=date.today() - timedelta(days=30))
with col_d2:
    end_date = st.date_input("Конец периода", value=date.today())

if start_date > end_date:
    st.error("Дата начала не может быть позже даты окончания.")
    st.stop()

if engine is None:
    st.error("Нет подключения к базе данных.")
    st.stop()

# Загружаем данные за период
@st.cache_data(ttl=600)
def load_data(start, end):
    query = f"SELECT * FROM losses WHERE loss_date >= '{start}' AND loss_date <= '{end}'"
    try:
        df = pd.read_sql(query, engine)
        if not df.empty:
            df['loss_date'] = pd.to_datetime(df['loss_date'])
            df['reason'] = df['reason'].astype(str).str.strip()
        return df
    except Exception as e:
        st.error(f"Ошибка БД: {e}")
        return pd.DataFrame()

df = load_data(start_date, end_date)

if df.empty:
    st.info("Нет данных за выбранный период. Загрузите файлы в боковой панели.")
    st.stop()

st.divider()

# ==========================================
# ЭКРАН 1: ДАШБОРД (Главная)
# ==========================================
if st.session_state.view == 'dashboard':
    total_sum = df['loss_sum'].sum()
    total_qty = df['loss_qty'].sum()
    
    st.markdown("### Общие списания за период")
    st.metric(
        label="Сумма", 
        value=f"{total_sum:,.2f} ₽".replace(",", " "),
        delta=f"{total_qty:,.0f} шт".replace(",", " "),
        delta_color="off"
    )
    
    st.markdown("---")
    st.subheader("Выберите поток для детализации:")
    
    col1, col2 = st.columns(2)
    with col1:
        # Делаем кнопку-чип
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
    # Кнопка Назад
    if st.button("⬅️ Назад на главную"):
        st.session_state.view = 'dashboard'
        st.rerun()
        
    flow_name = "Входящий поток" if st.session_state.selected_flow == "in" else "Исходящий поток"
    st.subheader(f"Выбран: {flow_name}")
    st.markdown("Выберите причину списания (LR):")
    
    # Определяем доступные LR
    if st.session_state.selected_flow == "in":
        available_lrs = ['36', '46', '47']
    else:
        available_lrs = ['26', '5']
        
    # Делаем кнопки-чипы для LR в 3 колонки
    cols = st.columns(len(available_lrs))
    for i, lr in enumerate(available_lrs):
        with cols[i]:
            # Считаем сумму по этому LR для отображения на кнопке (опционально)
            lr_sum = df[df['reason'] == lr]['loss_sum'].sum()
            lr_sum_str = f"{lr_sum:,.0f} ₽".replace(",", " ")
            
            if st.button(f"LR {lr}\n({lr_sum_str})", use_container_width=True):
                st.session_state.selected_lr = lr
                st.session_state.view = 'lr_detail'
                st.rerun()

# ==========================================
# ЭКРАН 3: ДЕТАЛЬНАЯ АНАЛИТИКА ПО LR
# ==========================================
elif st.session_state.view == 'lr_detail':
    # Кнопка Назад
    if st.button("⬅️ Назад к выбору LR"):
        st.session_state.view = 'flow'
        st.rerun()
        
    lr = st.session_state.selected_lr
    df_filtered = df[df['reason'] == lr]
    
    st.subheader(f"Детализация по причине (LR): {lr}")
    
    lr_sum = df_filtered['loss_sum'].sum()
    lr_qty = df_filtered['loss_qty'].sum()
    
    st.metric(
        label="Сумма по выбранному LR", 
        value=f"{lr_sum:,.2f} ₽".replace(",", " "),
        delta=f"{lr_qty:,.0f} шт".replace(",", " "),
        delta_color="off"
    )
    
    st.markdown("---")
    
    # График
    st.markdown("#### Динамика списаний")
    time_cut = st.radio("Разрез:", ["По дням", "По неделям", "По месяцам"], horizontal=True)
    
    if time_cut == "По дням":
        df_grouped = df_filtered.groupby('loss_date')['loss_sum'].sum().reset_index()
    elif time_cut == "По неделям":
        df_filtered['period'] = df_filtered['loss_date'].dt.to_period('W').dt.start_time
        df_grouped = df_filtered.groupby('period')['loss_sum'].sum().reset_index().rename(columns={'period': 'loss_date'})
    else:
        df_filtered['period'] = df_filtered['loss_date'].dt.to_period('M').dt.start_time
        df_grouped = df_filtered.groupby('period')['loss_sum'].sum().reset_index().rename(columns={'period': 'loss_date'})
    
    fig = px.line(df_grouped, x='loss_date', y='loss_sum', markers=True,
                  labels={'loss_sum': 'Сумма (₽)', 'loss_date': 'Дата'})
    st.plotly_chart(fig, use_container_width=True)
    
    # Таблица
    st.markdown("#### Сводка по блокам")
    if 'block_name' in df_filtered:
        df_blocks = df_filtered.groupby('block_name')[['loss_sum', 'loss_qty']].sum().reset_index()
        df_blocks = df_blocks.sort_values('loss_sum', ascending=False)
        df_blocks.columns = ['Блок', 'Сумма потерь (₽)', 'Кол-во (шт)']
        st.dataframe(df_blocks, use_container_width=True, hide_index=True, height=300)
