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
    # Читаем файл
    df = pd.read_excel(file)
    df.columns = df.columns.astype(str).str.strip()
    
    # Нужные колонки из твоего нового файла
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
    
    # Превращаем суммы и количества в числа
    for col in ['loss_sum', 'loss_qty']:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)
            
    # Превращаем дату в правильный формат
    if 'loss_date' in df.columns:
        df['loss_date'] = pd.to_datetime(df['loss_date'], errors='coerce').dt.date
        
    # Заполняем пустые текстовые блоки
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
                        # Очищаем от строк, где нет даты (иначе база ругнется)
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
    st.header("Аналитика и фильтры")
    
    if engine is None:
        st.error("Нет подключения к базе данных.")
    else:
        # 1. Выбор периода
        col_d1, col_d2 = st.columns(2)
        with col_d1:
            start_date = st.date_input("Начало периода", value=date.today() - timedelta(days=30))
        with col_d2:
            end_date = st.date_input("Конец периода", value=date.today())
            
        # 2. Выбор разреза
        time_cut = st.radio("Разрез:", ["По дням", "По неделям", "По месяцам"], horizontal=True)
        
        if start_date > end_date:
            st.error("Дата начала не может быть позже даты окончания.")
        else:
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
                df['loss_date'] = pd.to_datetime(df['loss_date'])
                
                # Итоговые KPI
                total_sum = df['loss_sum'].sum() if 'loss_sum' in df else 0
                total_qty = df['loss_qty'].sum() if 'loss_qty' in df else 0
                
                kpi1, kpi2 = st.columns(2)
                kpi1.metric("ОБЩАЯ СУММА ПОТЕРЬ (₽)", f"{total_sum:,.0f} ₽".replace(",", " "))
                kpi2.metric("КОЛИЧЕСТВО ПОТЕРЬ (шт)", f"{total_qty:,.0f}".replace(",", " "))
                
                st.markdown("---")
                st.subheader(f"Динамика суммы потерь ({time_cut.lower()})")
                
                # 3. Группировка
                if time_cut == "По дням":
                    df_grouped = df.groupby('loss_date')['loss_sum'].sum().reset_index()
                elif time_cut == "По неделям":
                    df['period'] = df['loss_date'].dt.to_period('W').dt.start_time
                    df_grouped = df.groupby('period')['loss_sum'].sum().reset_index()
                    df_grouped.rename(columns={'period': 'loss_date'}, inplace=True)
                else: # По месяцам
                    df['period'] = df['loss_date'].dt.to_period('M').dt.start_time
                    df_grouped = df.groupby('period')['loss_sum'].sum().reset_index()
                    df_grouped.rename(columns={'period': 'loss_date'}, inplace=True)
                
                # 4. График
                fig = px.line(
                    df_grouped, x='loss_date', y='loss_sum', 
                    labels={'loss_sum': 'Сумма потерь (₽)', 'loss_date': 'Дата'},
                    markers=True
                )
                st.plotly_chart(fig, use_container_width=True)
                
                st.markdown("---")
                
                # 5. Топ блоков и причин
                col_b1, col_b2 = st.columns(2)
                
                with col_b1:
                    st.subheader("Топ блоков по сумме потерь")
                    if 'block_name' in df:
                        df_blocks = df.groupby('block_name')['loss_sum'].sum().reset_index()
                        df_blocks = df_blocks.sort_values('loss_sum', ascending=False).head(10)
                        df_blocks.columns = ['Блок', 'Сумма потерь (₽)']
                        st.dataframe(df_blocks, use_container_width=True, hide_index=True)
                        
                with col_b2:
                    st.subheader("Топ причин списания")
                    if 'reason' in df:
                        df_reasons = df.groupby('reason')['loss_sum'].sum().reset_index()
                        df_reasons = df_reasons.sort_values('loss_sum', ascending=False).head(10)
                        df_reasons.columns = ['Причина', 'Сумма потерь (₽)']
                        st.dataframe(df_reasons, use_container_width=True, hide_index=True)
