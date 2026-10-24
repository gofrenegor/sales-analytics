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
    
    # ТЕПЕРЬ МЫ ЧИТАЕМ ДАТУ И ВРЕМЯ
    if 'loss_date' in df.columns: 
        df['loss_date'] = pd.to_datetime(df['loss_date'], errors='coerce')
        
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

def get_flow_name(reason):
    if reason in ['36', '46', '47']: return 'Входящий поток'
    elif reason in ['26', '5']: return 'Исходящий поток'
    return 'Другое'

# --- Боковая панель ---
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

date_mode = st.radio("Выберите разрез:", ["День", "Неделя", "Месяц"], horizontal=True)

start_date, end_date = None, None
prev_start_date, prev_end_date = None, None

if date_mode == "День":
    target_date = st.date_input("Выберите дату", value=date.today())
    start_date, end_date = target_date, target_date
    prev_start_date = target_date - timedelta(days=1)
    prev_end_date = prev_start_date
    st.info(f"Выбран день: **{start_date.strftime('%d.%m.%Y')}** | Сравнение с предыдущим днем: **{prev_start_date.strftime('%d.%m.%Y')}**")

elif date_mode == "Неделя":
    target_date = st.date_input("Выберите любой день нужной недели", value=date.today())
    start_date = target_date - timedelta(days=target_date.weekday())
    end_date = start_date + timedelta(days=6)
    prev_start_date = start_date - timedelta(days=7)
    prev_end_date = start_date - timedelta(days=1)
    st.info(f"Текущая неделя: **{start_date.strftime('%d.%m.%Y')} — {end_date.strftime('%d.%m.%Y')}** | Сравнение: **{prev_start_date.strftime('%d.%m.%Y')} — {prev_end_date.strftime('%d.%m.%Y')}**")

elif date_mode == "Месяц":
    target_date = st.date_input("Выберите любой день нужного месяца", value=date.today())
    start_date = target_date.replace(day=1)
    _, last_day = calendar.monthrange(target_date.year, target_date.month)
    end_date = target_date.replace(day=last_day)
    prev_start_date = (start_date - relativedelta(months=1)).replace(day=1)
    prev_end_date = start_date - timedelta(days=1)
    st.info(f"Текущий месяц: **{start_date.strftime('%d.%m.%Y')} — {end_date.strftime('%d.%m.%Y')}** | Сравнение: **{prev_start_date.strftime('%d.%m.%Y')} — {prev_end_date.strftime('%d.%m.%Y')}**")

if engine is None:
    st.error("Нет подключения к базе данных.")
    st.stop()

@st.cache_data(ttl=60)
def load_data(start, end):
    # Мы используем loss_date >= start и < end + 1 день, чтобы захватить все время
    query = f"SELECT * FROM losses WHERE loss_date >= '{start}' AND loss_date < '{end + timedelta(days=1)}'"
    try:
        df = pd.read_sql(query, engine)
        if not df.empty:
            df['loss_date'] = pd.to_datetime(df['loss_date'])
            df['reason'] = df['reason'].astype(str).str.strip()
            df['flow'] = df['reason'].apply(get_flow_name)
        return df
    except Exception as e:
        return pd.DataFrame()

df_curr = load_data(start_date, end_date)
df_prev = load_data(prev_start_date, prev_end_date)

if df_curr.empty and df_prev.empty:
    st.info("Нет данных за выбранный или предыдущий период.")
    st.stop()

st.divider()

tab_analytics, tab_charts = st.tabs(["📊 Аналитика потерь", "📈 Графики потерь"])

# ==========================================
# ВКЛАДКА 1: АНАЛИТИКА
# ==========================================
with tab_analytics:
    if st.session_state.view == 'dashboard':
        total_sum = df_curr['loss_sum'].sum()
        total_qty = df_curr['loss_qty'].sum()
        prev_sum = df_prev['loss_sum'].sum() if not df_prev.empty else 0
        prev_qty = df_prev['loss_qty'].sum() if not df_prev.empty else 0
        
        st.markdown("### Общие списания за период")
        st.metric(label="Сумма", value=f"{total_sum:,.2f} ₽".replace(",", " "), delta=get_delta_text(total_sum, total_qty, prev_sum, prev_qty), delta_color="inverse")
        
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

    elif st.session_state.view == 'flow':
        if st.button("⬅️ Назад на главную"):
            st.session_state.view = 'dashboard'
            st.rerun()
        flow_name = "Входящий поток" if st.session_state.selected_flow == "in" else "Исходящий поток"
        st.subheader(f"Выбран: {flow_name}")
        
        available_lrs = ['36', '46', '47'] if st.session_state.selected_flow == "in" else ['26', '5']
        cols = st.columns(len(available_lrs))
        for i, lr in enumerate(available_lrs):
            with cols[i]:
                lr_df_curr = df_curr[df_curr['reason'] == lr] if not df_curr.empty else pd.DataFrame()
                lr_sum_curr = lr_df_curr['loss_sum'].sum() if not lr_df_curr.empty else 0
                lr_qty_curr = lr_df_curr['loss_qty'].sum() if not lr_df_curr.empty else 0
                lr_df_prev = df_prev[df_prev['reason'] == lr] if not df_prev.empty else pd.DataFrame()
                lr_sum_prev = lr_df_prev['loss_sum'].sum() if not lr_df_prev.empty else 0
                lr_qty_prev = lr_df_prev['loss_qty'].sum() if not lr_df_prev.empty else 0
                
                st.metric(label=f"LR {lr}", value=f"{lr_sum_curr:,.0f} ₽".replace(",", " "), delta=get_delta_text(lr_sum_curr, lr_qty_curr, lr_sum_prev, lr_qty_prev), delta_color="inverse")
                if st.button(f"Подробнее", key=f"btn_{lr}", use_container_width=True):
                    st.session_state.selected_lr = lr
                    st.session_state.view = 'lr_detail'
                    st.rerun()

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
        
        st.metric(label=f"Сумма по LR {lr}", value=f"{lr_sum:,.2f} ₽".replace(",", " "), delta=get_delta_text(lr_sum, lr_qty, prev_sum, prev_qty), delta_color="inverse")
        
        if df_filtered_curr.empty:
            st.warning(f"По причине (LR) {lr} нет данных за текущий период.")
        else:
            st.markdown("---")
            st.markdown("#### Динамика списаний")
            df_grouped = df_filtered_curr.groupby('loss_date')['loss_sum'].sum().reset_index()
            
            fig = px.line(df_grouped, x='loss_date', y='loss_sum', markers=True, labels={'loss_sum': 'Сумма (₽)', 'loss_date': 'Дата'})
            fig.update_traces(hovertemplate="<b>Дата:</b> %{x|%d.%m.%Y %H:%M}<br><b>Сумма:</b> %{y:,.2f} ₽<extra></extra>")
            fig.update_xaxes(type='category') # Защита от микросекунд
            st.plotly_chart(fig, use_container_width=True)
            
            st.markdown("#### Сводка по блокам")
            if 'block_name' in df_filtered_curr:
                df_blocks = df_filtered_curr.groupby('block_name')[['loss_sum', 'loss_qty']].sum().reset_index()
                df_blocks = df_blocks.sort_values('loss_sum', ascending=False)
                df_blocks.columns = ['Блок', 'Сумма потерь (₽)', 'Кол-во (шт)']
                st.dataframe(df_blocks, use_container_width=True, hide_index=True, height=300)


# ==========================================
# ВКЛАДКА 2: ГРАФИКИ
# ==========================================
with tab_charts:
    st.subheader("Визуальный анализ потерь")
    st.markdown("Наведите мышкой на график, чтобы увидеть точные цифры.")
    
    col_pie, col_bar_flow = st.columns(2)
    with col_pie:
        df_flows = df_curr.groupby('flow')[['loss_sum', 'loss_qty']].sum().reset_index()
        fig_pie = px.pie(df_flows, names='flow', values='loss_sum', title="Доля потоков в общей сумме", color='flow', color_discrete_map={'Входящий поток': '#FF4B4B', 'Исходящий поток': '#00CC96', 'Другое': '#888888'}, hole=0.4)
        fig_pie.update_traces(hovertemplate="<b>%{label}</b><br>Сумма: %{value:,.2f} ₽<br>Доля: %{percent}<extra></extra>")
        st.plotly_chart(fig_pie, use_container_width=True)

    with col_bar_flow:
        # Исправление: сортируем по сумме, чтобы самые большие были слева
        df_lr = df_curr.groupby('reason')[['loss_sum', 'loss_qty']].sum().reset_index().sort_values('loss_sum', ascending=False)
        
        fig_lr = px.bar(df_lr, x='reason', y='loss_sum', title="Сумма потерь по причинам (LR)", labels={'reason': 'LR', 'loss_sum': 'Сумма (₽)'}, text_auto='.2s')
        fig_lr.update_traces(hovertemplate="<b>LR:</b> %{x}<br><b>Сумма:</b> %{y:,.2f} ₽<extra></extra>", marker_color='#1f77b4')
        
        # ГЛАВНОЕ ИСПРАВЛЕНИЕ: Принудительно делаем ось X категориальной (текстовой), чтобы не было 0-50
        fig_lr.update_xaxes(type='category', title="Причина (LR)")
        st.plotly_chart(fig_lr, use_container_width=True)

    st.divider()

    # 3. Динамика по дням или часам
    st.markdown("#### Динамика потерь")
    
    if len(df_curr) > 0:
        # Проверяем, есть ли в данных время (часы/минуты), отличное от нуля
        has_time = (df_curr['loss_date'].dt.hour != 0).any() or (df_curr['loss_date'].dt.minute != 0).any()
        
        if date_mode == "День" and has_time:
            # Если выбран день и есть время — строим по часам!
            df_curr_daily = df_curr.set_index('loss_date').resample('h')[['loss_sum', 'loss_qty']].sum().reset_index()
            title_text = "Сумма потерь по часам"
            hover_format = "%{x|%H:%M}"
        else:
            # Иначе строим по дням
            df_curr_daily = df_curr.groupby(df_curr['loss_date'].dt.date)[['loss_sum', 'loss_qty']].sum().reset_index()
            df_curr_daily.rename(columns={'loss_date': 'date_only'}, inplace=True)
            title_text = "Сумма потерь по дням"
            hover_format = "%{x|%d.%m.%Y}"

        fig_daily = px.line(df_curr_daily, x=df_curr_daily.columns[0], y='loss_sum', markers=True, title=title_text, labels={df_curr_daily.columns[0]: 'Время', 'loss_sum': 'Сумма (₽)'})
        fig_daily.update_traces(hovertemplate=f"<b>Время:</b> {hover_format}<br><b>Сумма:</b> %{{y:,.2f}} ₽<extra></extra>", line_color='#FF4B4B')
        
        # Принудительно ставим категориальную ось, чтобы Plotly не писал микросекунды, если точка всего одна
        fig_daily.update_xaxes(type='category') 
        st.plotly_chart(fig_daily, use_container_width=True)
        
        if date_mode == "День" and not has_time:
            st.caption("⚠️ В ваших данных нет времени (только дата), поэтому график по часам не построить. Добавьте время в Excel.")
    
    # 4. Топ-10 блоков
    st.markdown("#### Топ-10 блоков по сумме потерь")
    if 'block_name' in df_curr:
        df_top_blocks = df_curr.groupby('block_name')[['loss_sum', 'loss_qty']].sum().reset_index().sort_values('loss_sum', ascending=False).head(10)
        fig_blocks = px.bar(df_top_blocks, x='loss_sum', y='block_name', orientation='h', title="Топ-10 блоков (₽)", labels={'loss_sum': 'Сумма (₽)', 'block_name': 'Блок'}, text_auto='.2s')
        fig_blocks.update_layout(yaxis={'categoryorder':'total ascending'}) 
        fig_blocks.update_traces(hovertemplate="<b>Блок:</b> %{y}<br><b>Сумма:</b> %{x:,.2f} ₽<extra></extra>", marker_color='#FFA15A')
        st.plotly_chart(fig_blocks, use_container_width=True)
