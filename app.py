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

# --- Функция загрузки и очистки Excel ---
def process_excel(file):
    df = pd.read_excel(file)
    df.columns = df.columns.astype(str).str.strip()
    cols_map = {
        'ШК': 'barcode', 'Наименование блока': 'block_name', 'ID блока': 'block_id',
        'Сумма списания': 'loss_sum', 'Кол-во лог объектов для списания': 'loss_qty',
        'Дата последнего списания': 'loss_date', 'Лостризон последнего списания': 'reason',
        'Дата статуса перед списанием': 'status_before_date' # НОВАЯ КОЛОНКА
    }
    existing_cols = [c for c in cols_map.keys() if c in df.columns]
    df = df[existing_cols].rename(columns=cols_map)
    
    for col in ['loss_sum', 'loss_qty']:
        if col in df.columns: df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)
    
    # Превращаем в даты
    for col in ['loss_date', 'status_before_date']:
        if col in df.columns: 
            df[col] = pd.to_datetime(df[col], errors='coerce')
            
    for col in ['block_name', 'reason']:
        if col in df.columns: df[col] = df[col].fillna("Не указано").astype(str)
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

@st.cache_data(ttl=5)
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
            uploaded_file = st.file_uploader("Excel файл (.xlsx)", type=["xlsx"])
            if uploaded_file:
                try:
                    df_new = process_excel(uploaded_file)
                    df_new_valid = df_new.dropna(subset=['loss_date'])
                    if not df_new_valid.empty:
                        min_date = df_new_valid['loss_date'].min().date()
                        max_date = df_new_valid['loss_date'].max().date()
                        st.info(f"В файле: **{min_date.strftime('%d.%m.%Y')} — {max_date.strftime('%d.%m.%Y')}**")
                        st.warning("⚠️ Старые данные за эти даты будут заменены!")
                        if st.button("💾 Заменить данные", use_container_width=True, type="primary"):
                            if engine is not None:
                                try:
                                    with engine.begin() as conn:
                                        conn.execute(text(f"DELETE FROM losses WHERE loss_date::date >= '{min_date}' AND loss_date::date <= '{max_date}'"))
                                    df_new_valid.to_sql('losses', engine, if_exists='append', index=False)
                                    st.success("✅ Данные обновлены!")
                                    st.cache_data.clear()
                                    st.rerun()
                                except Exception as e:
                                    st.error(f"Ошибка: {e}")
                except Exception as e:
                    st.error(f"Ошибка: {e}")
            st.divider()
            st.markdown("### ⚠️ Опасная зона")
            if st.button("🗑️ Очистить базу", disabled=not st.checkbox("Я уверен, что хочу удалить всё"), use_container_width=True):
                if engine is not None:
                    try:
                        with engine.begin() as conn:
                            conn.execute(text("TRUNCATE TABLE losses RESTART IDENTITY"))
                        st.success("База очищена!")
                        st.cache_data.clear()
                        st.rerun()
                    except Exception as e:
                        st.error(f"Ошибка: {e}")

        with admin_tab_info:
            total_rows, min_date, max_date, df_months, df_weeks = get_db_summary()
            if not total_rows:
                st.info("База данных пока пуста.")
            else:
                st.markdown(f"**Всего строк:** {total_rows:,}".replace(",", " "))
                if min_date and max_date: st.markdown(f"**Период:** {min_date.strftime('%d.%m.%Y')} — {max_date.strftime('%d.%m.%Y')}")
                st.divider()
                st.markdown("**Месяцы:**")
                if df_months is not None: st.dataframe(df_months.rename(columns={'month':'Месяц','rows_count':'Строк'}), hide_index=True, use_container_width=True)
                st.markdown("**Недели:**")
                if df_weeks is not None:
                    df_weeks['week_start'] = pd.to_datetime(df_weeks['week_start']).dt.strftime('%d.%m.%Y')
                    st.dataframe(df_weeks.rename(columns={'week_start':'Неделя с','rows_count':'Строк'}), hide_index=True, use_container_width=True)

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

@st.cache_data(ttl=60)
def load_data(start, end):
    query = f"SELECT * FROM losses WHERE loss_date >= '{start}' AND loss_date < '{end + timedelta(days=1)}'"
    try:
        df = pd.read_sql(query, engine)
        if not df.empty:
            df['loss_date'] = pd.to_datetime(df['loss_date'])
            if 'status_before_date' in df.columns:
                df['status_before_date'] = pd.to_datetime(df['status_before_date'])
            df['reason'] = df['reason'].astype(str).str.strip()
            df['flow'] = df['reason'].apply(get_flow_name)
        return df
    except Exception:
        return pd.DataFrame()

df_curr = load_data(start_date, end_date)
df_prev = load_data(prev_start_date, prev_end_date)

if df_curr.empty and df_prev.empty:
    st.warning("⚠️ Нет данных за этот период.")
    st.stop()

st.divider()

# ==========================================
# ВКЛАДКИ (Аналитика, Графики и РАЗБОР ПОЛЕТОВ)
# ==========================================
tab_analytics, tab_charts, tab_investigation = st.tabs(["📊 Аналитика потерь", "📈 Графики потерь", "🕵️ Потери < 24ч (LR 5)"])

with tab_analytics:
    if st.session_state.get('view') is None: st.session_state.view = 'dashboard'
    if st.session_state.view == 'dashboard':
        total_sum = df_curr['loss_sum'].sum()
        total_qty = df_curr['loss_qty'].sum()
        prev_sum = df_prev['loss_sum'].sum() if not df_prev.empty else 0
        prev_qty = df_prev['loss_qty'].sum() if not df_prev.empty else 0
        st.markdown("### Общие списания за период")
        st.metric(label="Сумма", value=f"{total_sum:,.2f} ₽".replace(",", " "), delta=get_delta_text(total_sum, total_qty, prev_sum, prev_qty), delta_color="inverse")
        st.markdown("---")
        col1, col2 = st.columns(2)
        with col1:
            if st.button("ВХОДЯЩИЙ ПОТОК", use_container_width=True, key="btn_inbound"):
                st.session_state.selected_flow = "in"; st.session_state.view = 'flow'; st.rerun()
        with col2:
            if st.button("ИСХОДЯЩИЙ ПОТОК", use_container_width=True, key="btn_outbound"):
                st.session_state.selected_flow = "out"; st.session_state.view = 'flow'; st.rerun()
    elif st.session_state.view == 'flow':
        if st.button("⬅️ Назад на главную"): st.session_state.view = 'dashboard'; st.rerun()
        flow_name = "Входящий поток" if st.session_state.selected_flow == "in" else "Исходящий поток"
        st.subheader(f"Выбран: {flow_name}")
        available_lrs = ['36', '46', '47'] if st.session_state.selected_flow == "in" else ['26', '5']
        cols = st.columns(len(available_lrs))
        for i, lr in enumerate(available_lrs):
            with cols[i]:
                lr_df_curr = df_curr[df_curr['reason'] == lr] if not df_curr.empty else pd.DataFrame()
                lr_df_prev = df_prev[df_prev['reason'] == lr] if not df_prev.empty else pd.DataFrame()
                lr_sum_curr = lr_df_curr['loss_sum'].sum() if not lr_df_curr.empty else 0
                lr_sum_prev = lr_df_prev['loss_sum'].sum() if not lr_df_prev.empty else 0
                st.metric(label=f"LR {lr}", value=f"{lr_sum_curr:,.0f} ₽".replace(",", " "), delta=get_delta_text(lr_sum_curr, lr_df_curr['loss_qty'].sum() if not lr_df_curr.empty else 0, lr_sum_prev, lr_df_prev['loss_qty'].sum() if not lr_df_prev.empty else 0), delta_color="inverse")
                if st.button(f"Подробнее", key=f"btn_{lr}", use_container_width=True):
                    st.session_state.selected_lr = lr; st.session_state.view = 'lr_detail'; st.rerun()
    elif st.session_state.view == 'lr_detail':
        if st.button("⬅️ Назад к выбору LR"): st.session_state.view = 'flow'; st.rerun()
        lr = st.session_state.selected_lr
        df_f_curr = df_curr[df_curr['reason'] == lr] if not df_curr.empty else pd.DataFrame()
        st.subheader(f"Детализация по LR: {lr}")
        st.metric(label=f"Сумма", value=f"{df_f_curr['loss_sum'].sum():,.2f} ₽".replace(",", " "))
        if not df_f_curr.empty:
            df_grouped = df_f_curr.groupby('loss_date')['loss_sum'].sum().reset_index()
            df_grouped['time_label'] = df_grouped['loss_date'].dt.strftime('%d.%m %H:%M')
            fig = px.line(df_grouped, x='time_label', y='loss_sum', markers=True)
            fig.update_xaxes(type='category')
            st.plotly_chart(fig, use_container_width=True)
            df_blocks = df_f_curr.groupby('block_name')[['loss_sum', 'loss_qty']].sum().reset_index().sort_values('loss_sum', ascending=False)
            st.dataframe(df_blocks.rename(columns={'block_name':'Блок', 'loss_sum':'Сумма (₽)', 'loss_qty':'Кол-во'}), use_container_width=True, hide_index=True)

with tab_charts:
    st.subheader("Визуальный анализ")
    col_pie, col_bar = st.columns(2)
    with col_pie:
        df_flows = df_curr.groupby('flow')[['loss_sum']].sum().reset_index()
        fig_pie = px.pie(df_flows, names='flow', values='loss_sum', title="Доля потоков", color='flow', color_discrete_map={'Входящий поток': '#2e7d32', 'Исходящий поток': '#1565c0', 'Другое': '#888888'}, hole=0.4)
        st.plotly_chart(fig_pie, use_container_width=True)
    with col_bar:
        df_lr = df_curr.groupby('reason')[['loss_sum']].sum().reset_index().sort_values('loss_sum', ascending=False)
        fig_lr = px.bar(df_lr, x='reason', y='loss_sum', title="Потери по причинам", text_auto='.2s')
        fig_lr.update_xaxes(type='category', title="Причина (LR)")
        st.plotly_chart(fig_lr, use_container_width=True)
    st.divider()
    all_lrs = sorted(df_curr['reason'].unique())
    selected_lr_chart = st.selectbox("Выберите LR для графика:", ["Все"] + list(all_lrs))
    df_chart = df_curr.copy()
    if selected_lr_chart != "Все": df_chart = df_chart[df_chart['reason'] == selected_lr_chart]
    if not df_chart.empty:
        df_daily = df_chart.groupby(df_chart['loss_date'].dt.date)[['loss_sum']].sum().reset_index()
        df_daily['time_label'] = pd.to_datetime(df_daily['loss_date']).dt.strftime('%d.%m.%Y')
        fig_daily = px.line(df_daily, x='time_label', y='loss_sum', markers=True, title="Динамика")
        fig_daily.update_xaxes(type='category')
        st.plotly_chart(fig_daily, use_container_width=True)

# ==========================================
# НОВАЯ ВКЛАДКА: РАЗБОР ПОЛЕТОВ (LR 5)
# ==========================================
with tab_investigation:
    st.subheader("🕵️ Разбор полетов: Товары, списанные < 24 часов после раскладки")
    st.markdown("Анализ для **LR 5 (Сборка)**. Показывает, сколько товаров было потеряно почти сразу после того, как их положили на полку.")
    
    if 'status_before_date' not in df_curr.columns:
        st.error("⚠️ В данных нет колонки 'Дата статуса перед списанием'. Пожалуйста, убедитесь, что в загруженном Excel-файле есть эта колонка, и перезагрузите данные.")
    else:
        # Берем только LR 5 и убираем пустые даты
        df_lr5 = df_curr[df_curr['reason'] == '5'].copy()
        df_lr5 = df_lr5.dropna(subset=['status_before_date', 'loss_date'])
        
        if df_lr5.empty:
            st.info("Нет данных с указанными датами статуса для LR 5 за выбранный период.")
        else:
            # Считаем разницу в часах
            df_lr5['diff_hours'] = (df_lr5['loss_date'] - df_lr5['status_before_date']).dt.total_seconds() / 3600
            
            # Фильтруем те, что пропали меньше чем за 24 часа (и больше 0, чтобы убрать ошибки данных)
            df_fast_loss = df_lr5[(df_lr5['diff_hours'] < 24) & (df_lr5['diff_hours'] >= 0)]
            
            # Считаем метрики
            fast_qty = df_fast_loss['loss_qty'].sum()
            fast_sum = df_fast_loss['loss_sum'].sum()
            
            # Выводим красивые карточки
            col_m1, col_m2 = st.columns(2)
            col_m1.metric(label="Списано < 24 часов (шт)", value=f"{fast_qty:,.0f}".replace(",", " "))
            col_m2.metric(label="Сумма этих потерь (₽)", value=f"{fast_sum:,.2f} ₽".replace(",", " "))
            
            st.markdown("---")
            
            # График распределения по часам
            if not df_fast_loss.empty:
                # Группируем по часам (округленным)
                df_fast_loss['hours_bucket'] = df_fast_loss['diff_hours'].astype(int)
                df_hist = df_fast_loss.groupby('hours_bucket')[['loss_qty']].sum().reset_index()
                
                fig_hist = px.bar(df_hist, x='hours_bucket', y='loss_qty', 
                                  title="Через сколько часов теряется товар?",
                                  labels={'hours_bucket': 'Часов прошло после раскладки', 'loss_qty': 'Кол-во потерянных ШК'})
                st.plotly_chart(fig_hist, use_container_width=True)
                
                # Таблица деталей
                st.markdown("#### Список проблемных товаров")
                df_display = df_fast_loss[['barcode', 'block_name', 'status_before_date', 'loss_date', 'diff_hours', 'loss_qty', 'loss_sum']].copy()
                df_display.columns = ['ШК', 'Блок', 'Время раскладки', 'Время списания', 'Прошло часов', 'Кол-во', 'Сумма (₽)']
                
                # Форматируем даты для красоты
                df_display['Время раскладки'] = df_display['Время раскладки'].dt.strftime('%d.%m.%Y %H:%M')
                df_display['Время списания'] = df_display['Время списания'].dt.strftime('%d.%m.%Y %H:%M')
                df_display['Прошло часов'] = df_display['Прошло часов'].round(1)
                
                st.dataframe(df_display, use_container_width=True, hide_index=True, height=400)
