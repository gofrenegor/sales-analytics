import streamlit as st
import pandas as pd
import plotly.express as px
import utils

st.set_page_config(page_title="Разбор полетов LR5", page_icon="🕵️", layout="wide")
utils.inject_css()
utils.render_admin_panel()

st.title("🕵️ Разбор полетов: Быстрые потери (LR 5)")
st.markdown("Анализ времени от **«Даты статуса перед списанием»** до **«Даты первого списания»**.")

start_date, end_date, prev_start_date, prev_end_date = utils.render_date_selector()

df_curr = utils.load_data(start_date, end_date)

if df_curr.empty:
    st.warning("⚠️ Нет данных за этот период.")
    st.stop()

@st.cache_data(ttl=3600)
def get_lr5_data(df):
    if 'status_before_date' not in df.columns: return pd.DataFrame(), {}
    
    df_lr5_raw = df[df['reason'] == '5'].copy()
    total_lr5 = len(df_lr5_raw)
    
    df_lr5_clean = df_lr5_raw.dropna(subset=['status_before_date', 'loss_date'])
    dropped_dates = total_lr5 - len(df_lr5_clean)
    
    if df_lr5_clean.empty: 
        return pd.DataFrame(), {"total": total_lr5, "dropped_dates": dropped_dates, "dropped_neg": 0, "valid": 0}
    
    df_lr5_clean['diff_hours'] = (df_lr5_clean['loss_date'] - df_lr5_clean['status_before_date']).dt.total_seconds() / 3600
    df_lr5_positive = df_lr5_clean[df_lr5_clean['diff_hours'] >= 0].copy()
    dropped_neg = len(df_lr5_clean) - len(df_lr5_positive)
    
    bins = [-1, 24, 168, 720, float('inf')]
    labels = ['< 24 часов', '1-7 дней', '7-30 дней', '> 30 дней']
    df_lr5_positive['time_category'] = pd.cut(df_lr5_positive['diff_hours'], bins=bins, labels=labels)
    
    stats = {"total": total_lr5, "dropped_dates": dropped_dates, "dropped_neg": dropped_neg, "valid": len(df_lr5_positive)}
    return df_lr5_positive, stats

df_lr5, stats = get_lr5_data(df_curr)

with st.expander("🔍 Диагностика данных", expanded=True):
    st.markdown(f"""
    *   **Всего строк с LR 5:** `{stats.get('total', 0):,}`.replace(",", " ")
    *   **⚠️ Отброшено из-за пустой даты:** `{stats.get('dropped_dates', 0):,}`.replace(",", " ")
    *   **Отброшено из-за отрицательной разницы:** `{stats.get('dropped_neg', 0):,}`.replace(",", " ")
    *   **Осталось для анализа:** `{stats.get('valid', 0):,}`.replace(",", " ")
    """)

if df_lr5.empty:
    st.warning("Нет валидных данных для LR 5.")
else:
    cat_order = ["< 24 часов", "1-7 дней", "7-30 дней", "> 30 дней"]
    df_cat_summary = df_lr5.groupby('time_category', observed=True)[['loss_qty']].sum().reset_index()
    
    missing_cats = [c for c in cat_order if c not in df_cat_summary['time_category'].values]
    for cat in missing_cats:
        df_cat_summary = pd.concat([df_cat_summary, pd.DataFrame({'time_category': [cat], 'loss_qty': [0]})], ignore_index=True)
        
    df_cat_summary['time_category'] = pd.Categorical(df_cat_summary['time_category'], categories=cat_order, ordered=True)
    df_cat_summary = df_cat_summary.sort_values('time_category')
    
    custom_colors = {'< 24 часов': '#FF4B4B', '1-7 дней': '#FFA15A', '7-30 дней': '#19D3F3', '> 30 дней': '#888888'}
    
    st.markdown("### Распределение потерь по времени")
    fig_cat = px.bar(df_cat_summary, x='time_category', y='loss_qty', title="Количество потерянных ШК", text_auto=True, color='time_category', color_discrete_map=custom_colors)
    st.plotly_chart(fig_cat, use_container_width=True)
    
    st.divider()
    st.markdown("### Детальный список")
    time_options = ["Все", "Меньше 24 часов", "От 1 до 7 дней", "От 7 до 30 дней", "Больше месяца (>30 дней)"]
    selected_time_label = st.radio("Выберите разрез:", time_options, horizontal=True)

    if selected_time_label == "Все": df_filtered = df_lr5
    elif selected_time_label == "Меньше 24 часов": df_filtered = df_lr5[df_lr5['time_category'] == "< 24 часов"]
    elif selected_time_label == "От 1 до 7 дней": df_filtered = df_lr5[df_lr5['time_category'] == "1-7 дней"]
    elif selected_time_label == "От 7 до 30 дней": df_filtered = df_lr5[df_lr5['time_category'] == "7-30 дней"]
    else: df_filtered = df_lr5[df_lr5['time_category'] == "> 30 дней"]

    col_m1, col_m2 = st.columns(2)
    col_m1.metric(label="Списано (шт)", value=f"{df_filtered['loss_qty'].sum():,.0f}".replace(",", " "))
    col_m2.metric(label="Сумма потерь (₽)", value=f"{df_filtered['loss_sum'].sum():,.2f} ₽".replace(",", " "))
    
    if not df_filtered.empty:
        cols_to_show = ['barcode', 'block_name', 'status_before', 'status_before_date', 'loss_date', 'diff_hours', 'loss_qty', 'loss_sum']
        if 'status_before' not in df_filtered.columns:
            cols_to_show.remove('status_before')
            df_display = df_filtered[cols_to_show].copy()
            df_display.columns = ['ШК', 'Блок', 'Дата статуса', 'Дата списания', 'Прошло часов', 'Кол-во', 'Сумма (₽)']
        else:
            df_display = df_filtered[cols_to_show].copy()
            df_display.columns = ['ШК', 'Блок', 'Статус', 'Дата статуса', 'Дата списания', 'Прошло часов', 'Кол-во', 'Сумма (₽)']
        
        df_display['Дата статуса'] = df_display['Дата статуса'].dt.strftime('%d.%m.%Y %H:%M')
        df_display['Дата списания'] = df_display['Дата списания'].dt.strftime('%d.%m.%Y %H:%M')
        df_display['Прошло часов'] = df_display['Прошло часов'].round(2)
        st.dataframe(df_display, use_container_width=True, hide_index=True, height=400)
