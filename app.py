import streamlit as st
import pandas as pd
import plotly.express as px
from datetime import date, timedelta
from sqlalchemy import create_engine

# --- Настройка страницы ---
st.set_page_config(page_title="Аналитика потерь", page_icon="📦", layout="wide")

# --- Подключение к базе данных Supabase ---
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
        'ШК': 'barcode',
        'Наименование блока': 'block_name',
        'ID блока': 'block_id',
        'Сумма списания': 'loss_sum',
        'Кол-во лог объектов для списания': 'loss_qty',
        'Дата последнего списания': 'loss_date',
        'Лостризон последнего списания': 'reason'
    }
    
    existing_cols = [c for c in cols_map.keys() if c in df.columns]
    
    if not existing_cols:
        raise ValueError(f"Не найдены нужные колонки! Колонки в файле: {df.columns.tolist()}")
        
    df = df[existing_cols].rename(columns=cols_map)
    
    for col in ['loss_sum', 'loss_qty']:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)
            
    if 'loss_date' in df.columns:
        df['loss_date'] = pd.to_datetime(df['loss_date'], errors='coerce').dt.date
        
    for col in ['block_name', 'reason']:
        if col in df.columns:
            df[col] = df[col].fillna("Не указано").astype(str)
            
    return df

# --- Интерфейс (Вкладки) ---
tab1, tab2 = st.tabs(["📥 Загрузить данные", "📊 Аналитика"])

# ================= ВКЛАДКА 1: ЗАГРУЗКА =================
with tab1:
    st.header("Загрузка данных в базу")
    st.markdown("Загрузите отчет по списаниям. Даты возьмутся прямо из колонки «Дата последнего списания».")
    
    uploaded_file = st.file_uploader("Выберите Excel файл (.xlsx)", type=["xlsx"])
        
    if uploaded_file:
        try:
            df_new = process_excel(uploaded_file)
            
            st.write("Предпросмотр данных (первые 10 строк):")
            st.dataframe(df_new.head(10), use_container_width=True)
            
            if st.button("💾 Сохранить в базу данных"):
                if engine is None:
                    st.error("Нет подключения к базе.")
                else:
                    try:
                        df_to_save = df_new.dropna(subset=['loss_date'])
                        df_to_save.to_sql('losses', engine, if_exists='append', index=False)
                        st.success(f"✅ Успешно загружено {len(df_to_save)} строк!")
                        st.cache_data.clear()
                    except Exception as e:
                        st.error(f"Ошибка при сохранении в базу: {e}")
        except Exception as e:
            st.error(f"Ошибка при чтении файла: {e}")

# ================= ВКЛАДКА 2: АНАЛИТИКА =================
with tab2:
    st.header("Аналитика потерь")
    
    if engine is None:
        st.error("Нет подключения к базе данных.")
    else:
        # 1. Выбор периода
        col_d1, col_d2 = st.columns(2)
        with col_d1:
            start_date = st.date_input("Начало периода", value=date.today() - timedelta(days=30))
        with col_d2:
            end_date = st.date_input("Конец периода", value=date.today())
            
        if start_date > end_date:
            st.error("Дата начала не может быть позже даты окончания.")
        else:
            # Загружаем данные из базы
            query = f"""
                SELECT * FROM losses 
                WHERE loss_date >= '{start_date}' AND loss_date <= '{end_date}'
            """
            try:
                df = pd.read_sql(query, engine)
            except Exception as e:
                st.error(f"Ошибка чтения из базы: {e}")
                df = pd.DataFrame()
                
            if df.empty:
                st.info("Нет данных за выбранный период. Загрузите файлы на первой вкладке.")
            else:
                # Приводим типы данных в порядок
                df['loss_date'] = pd.to_datetime(df['loss_date'])
                df['reason'] = df['reason'].astype(str).str.strip()
                
                # ==========================================
                # БЛОК 1: ОБЩИЕ СПИСАНИЯ ЗА ПЕРИОД
                # ==========================================
                total_sum = df['loss_sum'].sum()
                total_qty = df['loss_qty'].sum()
                
                st.markdown("### Общие списания за период")
                st.metric(
                    label="Сумма", 
                    value=f"{total_sum:,.2f} ₽".replace(",", " "),
                    delta=f"{total_qty:,.0f} шт".replace(",", " "),
                    delta_color="off"
                )
                
                st.divider()

                # ==========================================
                # БЛОК 2: ВЫБОР ПОТОКА И ПРИЧИНЫ (LR)
                # ==========================================
                col_flow, col_lr = st.columns(2)
                
                with col_flow:
                    # Выбор потока
                    flow = st.radio(
                        "Выберите поток:", 
                        ["Входящий поток", "Исходящий поток"], 
                        horizontal=True
                    )
                
                # Определяем доступные LR в зависимости от потока
                if flow == "Входящий поток":
                    available_lrs = ['36', '46', '47']
                else:
                    available_lrs = ['26', '5']
                
                with col_lr:
                    # Выбор LR
                    selected_lr = st.selectbox(
                        f"Причина списания (LR) для {flow.lower()}:", 
                        available_lrs
                    )
                
                # Фильтруем данные по выбранному LR
                df_filtered = df[df['reason'] == selected_lr]
                
                # ==========================================
                # БЛОК 3: АНАЛИТИКА ПО ВЫБРАННОМУ LR
                # ==========================================
                if df_filtered.empty:
                    st.warning(f"По причине (LR) {selected_lr} нет данных за выбранный период.")
                else:
                    lr_sum = df_filtered['loss_sum'].sum()
                    lr_qty = df_filtered['loss_qty'].sum()
                    
                    st.markdown(f"#### Списания по причине (LR): {selected_lr}")
                    st.metric(
                        label="Сумма по выбранному LR", 
                        value=f"{lr_sum:,.2f} ₽".replace(",", " "),
                        delta=f"{lr_qty:,.0f} шт".replace(",", " "),
                        delta_color="off"
                    )
                    
                    st.markdown("---")
                    
                    # График динамики
                    st.subheader("Динамика списаний")
                    time_cut = st.radio("Разрез:", ["По дням", "По неделям", "По месяцам"], horizontal=True)
                    
                    if time_cut == "По дням":
                        df_grouped = df_filtered.groupby('loss_date')['loss_sum'].sum().reset_index()
                    elif time_cut == "По неделям":
                        df_filtered['period'] = df_filtered['loss_date'].dt.to_period('W').dt.start_time
                        df_grouped = df_filtered.groupby('period')['loss_sum'].sum().reset_index()
                        df_grouped.rename(columns={'period': 'loss_date'}, inplace=True)
                    else:
                        df_filtered['period'] = df_filtered['loss_date'].dt.to_period('M').dt.start_time
                        df_grouped = df_filtered.groupby('period')['loss_sum'].sum().reset_index()
                        df_grouped.rename(columns={'period': 'loss_date'}, inplace=True)
                    
                    fig = px.line(
                        df_grouped, x='loss_date', y='loss_sum', 
                        labels={'loss_sum': 'Сумма потерь (₽)', 'loss_date': 'Дата'},
                        markers=True
                    )
                    st.plotly_chart(fig, use_container_width=True)
                    
                    # Таблица по блокам
                    st.subheader("Сводка по блокам (для выбранного LR)")
                    if 'block_name' in df_filtered:
                        df_blocks = df_filtered.groupby('block_name')[['loss_sum', 'loss_qty']].sum().reset_index()
                        df_blocks = df_blocks.sort_values('loss_sum', ascending=False)
                        df_blocks.columns = ['Блок', 'Сумма потерь (₽)', 'Кол-во (шт)']
                        st.dataframe(df_blocks, use_container_width=True, hide_index=True, height=300)
