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
        
        # Заменяем устаревший префикс, если он есть
        if db_url.startswith("postgres://"):
            db_url = db_url.replace("postgres://", "postgresql://", 1)
            
        # Принудительно указываем драйвер psycopg2, чтобы избежать ошибки 'no pq wrapper available'
        if db_url.startswith("postgresql://"):
            db_url = db_url.replace("postgresql://", "postgresql+psycopg2://", 1)
            
        return create_engine(db_url, connect_args={"connect_timeout": 10})
    except Exception as e:
        st.error(f"Ошибка при чтении секретов или инициализации движка: {e}")
        return None

engine = get_db_engine()

# --- Функция загрузки и очистки Excel ---
def process_excel(file):
    df = pd.read_excel(file)
    
    cols_map = {
        'Блок сборки': 'assembly_block',
        'Баркод сборки': 'assembly_barcode',
        'Кол-во ШК на сборке': 'assembly_qty',
        'Блок сортировки': 'sorting_block',
        'Баркод сортировки': 'sorting_barcode',
        'Кол-во ШК на сортировке': 'sorting_qty'
    }
    
    existing_cols = [c for c in cols_map.keys() if c in df.columns]
    df = df[existing_cols].rename(columns=cols_map)
    
    for col in ['assembly_qty', 'sorting_qty']:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)
            
    for col in ['assembly_block', 'sorting_block']:
        if col in df.columns:
            df[col] = df[col].fillna("Не указан")
            
    return df

# --- Интерфейс (Вкладки) ---
tab1, tab2 = st.tabs(["📥 Загрузить данные", "📊 Аналитика"])

# ================= ВКЛАДКА 1: ЗАГРУЗКА =================
with tab1:
    st.header("Загрузка данных в базу")
    st.markdown("Загрузите Excel-файл с потерями, укажите дату (например, конец недели) и сохраните в базу.")
    
    col1, col2 = st.columns(2)
    with col1:
        report_date = st.date_input("За какую дату эта выгрузка?", value=date.today())
    with col2:
        uploaded_file = st.file_uploader("Выберите Excel файл (.xlsx)", type=["xlsx"])
        
    if uploaded_file:
        df_new = process_excel(uploaded_file)
        st.write("Предпросмотр данных (первые 10 строк):")
        st.dataframe(df_new.head(10), use_container_width=True)
        
        if st.button("💾 Сохранить в базу данных"):
            if engine is None:
                st.error("Нет подключения к базе данных. Проверьте настройки Secrets.")
            else:
                try:
                    df_new['report_date'] = report_date
                    df_new.to_sql('losses', engine, if_exists='append', index=False)
                    st.success(f"✅ Успешно загружено {len(df_new)} строк за {report_date}!")
                    st.cache_data.clear()
                except Exception as e:
                    st.error(f"Ошибка при сохранении: {e}")

# ================= ВКЛАДКА 2: АНАЛИТИКА =================
with tab2:
    st.header("Аналитика и фильтры")
    
    col_d1, col_d2 = st.columns(2)
    with col_d1:
        start_date = st.date_input("Начало периода", value=date.today() - timedelta(days=30))
    with col_d2:
        end_date = st.date_input("Конец периода", value=date.today())
        
    if start_date > end_date:
        st.error("Дата начала не может быть позже даты окончания.")
    else:
        if engine is None:
            st.error("Нет подключения к базе данных.")
        else:
            query = f"""
                SELECT * FROM losses 
                WHERE report_date >= '{start_date}' AND report_date <= '{end_date}'
            """
            try:
                df = pd.read_sql(query, engine)
            except Exception as e:
                st.error(f"Ошибка чтения из базы (возможно, таблица еще не создана): {e}")
                df = pd.DataFrame()
                
            if df.empty:
                st.info("Нет данных за выбранный период. Загрузите файлы на первой вкладке.")
            else:
                total_assembly = df['assembly_qty'].sum() if 'assembly_qty' in df else 0
                total_sorting = df['sorting_qty'].sum() if 'sorting_qty' in df else 0
                total_losses = total_assembly + total_sorting
                
                kpi1, kpi2, kpi3 = st.columns(3)
                kpi1.metric("ОБЩИЕ ПОТЕРИ (шт)", f"{total_losses:,.0f}".replace(",", " "))
                kpi2.metric("Потери на СБОРКЕ (шт)", f"{total_assembly:,.0f}".replace(",", " "))
                kpi3.metric("Потери на СОРТИРОВКЕ (шт)", f"{total_sorting:,.0f}".replace(",", " "))
                
                st.markdown("---")
                st.subheader("Динамика потерь по датам выгрузок")
                
                if 'report_date' in df:
                    df_daily = df.groupby('report_date')[['assembly_qty', 'sorting_qty']].sum().reset_index()
                    fig = px.line(
                        df_daily, x='report_date', y=['assembly_qty', 'sorting_qty'], 
                        labels={'value': 'Потери (шт)', 'report_date': 'Дата', 'variable': 'Тип потерь'},
                        markers=True
                    )
                    fig.for_each_trace(lambda t: t.update(name={'assembly_qty': 'Сборка', 'sorting_qty': 'Сортировка'}[t.name]))
                    st.plotly_chart(fig, use_container_width=True)
                
                st.subheader("Сводка по блокам за период")
                if 'assembly_block' in df:
                    df_blocks = df.groupby('assembly_block')[['assembly_qty', 'sorting_qty']].sum().reset_index()
                    df_blocks['total'] = df_blocks['assembly_qty'] + df_blocks['sorting_qty']
                    df_blocks = df_blocks.sort_values('total', ascending=False)
                    df_blocks.columns = ['Блок', 'Потери на сборке', 'Потери на сортировке', 'Всего']
                    st.dataframe(df_blocks, use_container_width=True, hide_index=True, height=400)
