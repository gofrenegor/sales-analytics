import streamlit as st
import pandas as pd
import plotly.express as px
import utils

# Настройка главной страницы
st.set_page_config(page_title="Аналитика списаний", page_icon="📦", layout="wide")

# Применяем стили (зеленые/синие кнопки) и рисуем Админ-панель слева
utils.inject_css()
utils.render_admin_panel()

# Заголовок
st.title("📦 Аналитика списаний")

# Выбор дат (общий для всех страниц)
start_date, end_date, prev_start_date, prev_end_date = utils.render_date_selector()

# Загружаем данные
df_curr = utils.load_data(start_date, end_date)
df_prev = utils.load_data(prev_start_date, prev_end_date)

if df_curr.empty and df_prev.empty:
    st.warning("⚠️ Нет данных за этот период.")
    st.stop()

st.divider()

# --- Инициализация состояний для навигации ---
if 'view' not in st.session_state: st.session_state.view = 'dashboard'

# ==========================================
# РЕНДЕР ГЛАВНОГО ЭКРАНА (Дашборд)
# ==========================================
if st.session_state.view == 'dashboard':
    total_sum = df_curr['loss_sum'].sum()
    total_qty = df_curr['loss_qty'].sum()
    prev_sum = df_prev['loss_sum'].sum() if not df_prev.empty else 0
    prev_qty = df_prev['loss_qty'].sum() if not df_prev.empty else 0
    
    st.markdown("### Общие списания")
    st.metric(
        label="Сумма", 
        value=f"{total_sum:,.2f} ₽".replace(",", " "), 
        delta=utils.get_delta_text(total_sum, total_qty, prev_sum, prev_qty), 
        delta_color="inverse"
    )
    
    st.markdown("---")
    st.subheader("Выберите поток для детализации:")
    
    col1, col2 = st.columns(2)
    with col1:
        if st.button("ВХОДЯЩИЙ ПОТОК", use_container_width=True, key="btn_inbound"):
            st.session_state.selected_flow = "in"
            st.session_state.view = 'flow'
            st.rerun()
    with col2:
        if st.button("ИСХОДЯЩИЙ ПОТОК", use_container_width=True, key="btn_outbound"):
            st.session_state.selected_flow = "out"
            st.session_state.view = 'flow'
            st.rerun()

# ==========================================
# РЕНДЕР ЭКРАНА ВЫБОРА LR (Внутри потока)
# ==========================================
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
            lr_df_prev = df_prev[df_prev['reason'] == lr] if not df_prev.empty else pd.DataFrame()
            lr_sum_curr = lr_df_curr['loss_sum'].sum() if not lr_df_curr.empty else 0
            lr_sum_prev = lr_df_prev['loss_sum'].sum() if not lr_df_prev.empty else 0
            
            st.metric(
                label=f"LR {lr}", 
                value=f"{lr_sum_curr:,.0f} ₽".replace(",", " "), 
                delta=utils.get_delta_text(
                    lr_sum_curr, 
                    lr_df_curr['loss_qty'].sum() if not lr_df_curr.empty else 0, 
                    lr_sum_prev, 
                    lr_df_prev['loss_qty'].sum() if not lr_df_prev.empty else 0
                ), 
                delta_color="inverse"
            )
            if st.button(f"Подробнее", key=f"btn_{lr}", use_container_width=True):
                st.session_state.selected_lr = lr
                st.session_state.view = 'lr_detail'
                st.rerun()

# ==========================================
# РЕНДЕР ДЕТАЛИЗАЦИИ ПО КОНКРЕТНОМУ LR
# ==========================================
elif st.session_state.view == 'lr_detail':
    if st.button("⬅️ Назад к выбору LR"):
        st.session_state.view = 'flow'
        st.rerun()
        
    lr = st.session_state.selected_lr
    df_f_curr = df_curr[df_curr['reason'] == lr] if not df_curr.empty else pd.DataFrame()
    
    st.subheader(f"Детализация по LR: {lr}")
    st.metric(label=f"Сумма", value=f"{df_f_curr['loss_sum'].sum():,.2f} ₽".replace(",", " "))
    
    if not df_f_curr.empty:
        df_grouped = df_f_curr.groupby('loss_date')['loss_sum'].sum().reset_index()
        df_grouped['time_label'] = df_grouped['loss_date'].dt.strftime('%d.%m %H:%M')
        
        fig = px.line(df_grouped, x='time_label', y='loss_sum', markers=True, title="Динамика списаний")
        fig.update_xaxes(type='category')
        st.plotly_chart(fig, use_container_width=True)
        
        st.markdown("#### Сводка по блокам")
        df_blocks = df_f_curr.groupby('block_name')[['loss_sum', 'loss_qty']].sum().reset_index().sort_values('loss_sum', ascending=False)
        df_blocks.columns = ['Блок', 'Сумма потерь (₽)', 'Кол-во (шт)']
        st.dataframe(df_blocks, use_container_width=True, hide_index=True)
