import streamlit as st
import pandas as pd
import plotly.express as px
import utils

st.set_page_config(page_title="Графики потерь", page_icon="📈", layout="wide")
utils.inject_css()
utils.render_admin_panel()

st.title("📈 Визуальный анализ потерь")

start_date, end_date, prev_start_date, prev_end_date = utils.render_date_selector()

df_curr = utils.load_data(start_date, end_date)

if df_curr.empty:
    st.warning("⚠️ Нет данных за этот период.")
    st.stop()

# Кэшируем данные для графиков
@st.cache_data(ttl=3600)
def get_charts_data(df):
    df_flows = df.groupby('flow')[['loss_sum']].sum().reset_index()
    df_lr = df.groupby('reason')[['loss_sum']].sum().reset_index().sort_values('loss_sum', ascending=False)
    return df_flows, df_lr

df_flows, df_lr = get_charts_data(df_curr)

col_pie, col_bar = st.columns(2)
with col_pie:
    fig_pie = px.pie(df_flows, names='flow', values='loss_sum', title="Доля потоков", color='flow', color_discrete_map={'Входящий поток': '#2e7d32', 'Исходящий поток': '#1565c0', 'Другое': '#888888'}, hole=0.4)
    st.plotly_chart(fig_pie, use_container_width=True)
with col_bar:
    fig_lr = px.bar(df_lr, x='reason', y='loss_sum', title="Потери по причинам", text_auto='.2s')
    fig_lr.update_xaxes(type='category', title="Причина (LR)")
    st.plotly_chart(fig_lr, use_container_width=True)

st.divider()
all_lrs = sorted(df_curr['reason'].unique())
selected_lr_chart = st.selectbox("Выберите LR для графика:", ["Все"] + list(all_lrs))

@st.cache_data(ttl=3600)
def get_dynamic_chart_data(df, selected_lr):
    df_chart = df.copy()
    if selected_lr != "Все": 
        df_chart = df_chart[df_chart['reason'] == selected_lr]
    if df_chart.empty: return pd.DataFrame()
    df_daily = df_chart.groupby(df_chart['loss_date'].dt.date)[['loss_sum']].sum().reset_index()
    df_daily['time_label'] = pd.to_datetime(df_daily['loss_date']).dt.strftime('%d.%m.%Y')
    return df_daily

df_daily = get_dynamic_chart_data(df_curr, selected_lr_chart)

if not df_daily.empty:
    fig_daily = px.line(df_daily, x='time_label', y='loss_sum', markers=True, title="Динамика")
    fig_daily.update_xaxes(type='category')
    st.plotly_chart(fig_daily, use_container_width=True)
