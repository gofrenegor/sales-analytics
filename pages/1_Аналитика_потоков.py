import streamlit as st
import pandas as pd
import utils

st.set_page_config(page_title="Аналитика потоков", page_icon="📊", layout="wide")
utils.inject_css()
utils.render_admin_panel()

st.title("📊 Аналитика потоков")

start_date, end_date, prev_start_date, prev_end_date = utils.render_date_selector()

df_curr = utils.load_data(start_date, end_date)
df_prev = utils.load_data(prev_start_date, prev_end_date)

if df_curr.empty and df_prev.empty:
    st.warning("⚠️ Нет данных за этот период.")
    st.stop()

if st.session_state.get('view') is None: st.session_state.view = 'dashboard'

if st.session_state.view == 'dashboard':
    total_sum = df_curr['loss_sum'].sum()
    total_qty = df_curr['loss_qty'].sum()
    prev_sum = df_prev['loss_sum'].sum() if not df_prev.empty else 0
    prev_qty = df_prev['loss_qty'].sum() if not df_prev.empty else 0
    
    st.markdown("### Общие списания")
    st.metric(label="Сумма", value=f"{total_sum:,.2f} ₽".replace(",", " "), delta=utils.get_delta_text(total_sum, total_qty, prev_sum, prev_qty), delta_color="inverse")
    
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
            
            st.metric(
                label=f"LR {lr}", 
                value=f"{lr_sum_curr:,.0f} ₽".replace(",", " "), 
                delta=utils.get_delta_text(lr_sum_curr, lr_df_curr['loss_qty'].sum() if not lr_df_curr.empty else 0, lr_sum_prev, lr_df_prev['loss_qty'].sum() if not lr_df_prev.empty else 0), 
                delta_color="inverse"
            )
            if st.button(f"Подробнее", key=f"btn_{lr}", use_container_width=True):
                st.session_state.selected_lr = lr; st.session_state.view = 'lr_detail'; st.rerun()

elif st.session_state.view == 'lr_detail':
    if st.button("⬅️ Назад"): st.session_state.view = 'flow'; st.rerun()
    lr = st.session_state.selected_lr
    df_f_curr = df_curr[df_curr['reason'] == lr] if not df_curr.empty else pd.DataFrame()
    
    st.subheader(f"Детализация по LR: {lr}")
    st.metric(label=f"Сумма", value=f"{df_f_curr['loss_sum'].sum():,.2f} ₽".replace(",", " "))
    
    if not df_f_curr.empty:
        df_grouped = df_f_curr.groupby('loss_date')['loss_sum'].sum().reset_index()
        df_grouped['time_label'] = df_grouped['loss_date'].dt.strftime('%d.%m %H:%M')
        import plotly.express as px
        fig = px.line(df_grouped, x='time_label', y='loss_sum', markers=True)
        fig.update_xaxes(type='category')
        st.plotly_chart(fig, use_container_width=True)
