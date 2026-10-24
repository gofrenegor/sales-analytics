import streamlit as st
import pandas as pd
import utils

st.set_page_config(page_title="Аналитика списаний", page_icon="📦", layout="wide")
utils.inject_css()
utils.render_admin_panel()

st.title("📦 Аналитика списаний")

start_date, end_date, prev_start_date, prev_end_date = utils.render_date_selector()
df_curr = utils.load_data(start_date, end_date)
df_prev = utils.load_data(prev_start_date, prev_end_date)

if df_curr.empty and df_prev.empty:
    st.warning("⚠️ Нет данных за этот период.")
    st.stop()

st.divider()

if 'view' not in st.session_state: st.session_state.view = 'dashboard'
if 'selected_flow' not in st.session_state: st.session_state.selected_flow = None
if 'selected_block' not in st.session_state: st.session_state.selected_block = None

# ==========================================
# 1. ГЛАВНЫЙ ЭКРАН
# ==========================================
if st.session_state.view == 'dashboard':
    total_sum = df_curr['loss_sum'].sum()
    total_qty = df_curr['loss_qty'].sum()
    prev_sum = df_prev['loss_sum'].sum() if not df_prev.empty else 0
    prev_qty = df_prev['loss_qty'].sum() if not df_prev.empty else 0
    
    st.markdown("### Общие списания за период")
    st.metric(
        label="Сумма", 
        value=f"{total_sum:,.2f} ₽".replace(",", " "), 
        delta=utils.get_delta_text(total_sum, total_qty, prev_sum, prev_qty), 
        delta_color="inverse"
    )
    
    st.markdown("---")
    st.subheader("Разделение по потокам:")
    
    sum_in = df_curr[df_curr['flow'] == 'Входящий поток']['loss_sum'].sum() if not df_curr.empty else 0
    sum_out = df_curr[df_curr['flow'] == 'Исходящий поток']['loss_sum'].sum() if not df_curr.empty else 0
    sum_other = df_curr[df_curr['flow'] == 'Другое']['loss_sum'].sum() if not df_curr.empty else 0
    
    col1, col2, col3 = st.columns(3)
    
    with col1:
        if st.button("ВХОДЯЩИЙ ПОТОК", use_container_width=True, key="btn_inbound"):
            st.session_state.selected_flow = "in"
            st.session_state.view = 'flow'
            st.rerun()
        st.metric(label="Сумма входящего потока", value=f"{sum_in:,.2f} ₽".replace(",", " "))
        
    with col2:
        if st.button("ИСХОДЯЩИЙ ПОТОК", use_container_width=True, key="btn_outbound"):
            st.session_state.selected_flow = "out"
            st.session_state.view = 'flow'
            st.rerun()
        st.metric(label="Сумма исходящего потока", value=f"{sum_out:,.2f} ₽".replace(",", " "))
        
    with col3:
        st.markdown("<div style='height: 38px;'></div>", unsafe_allow_html=True)
        st.metric(label="Прочие причины (Другое)", value=f"{sum_other:,.2f} ₽".replace(",", " "))
        
    if sum_other > 0:
        other_statuses = df_curr[df_curr['flow'] == 'Другое']['status_before'].unique()
        display_statuses = [str(s) for s in other_statuses[:10]]
        st.warning(f"⚠️ В категорию «Другое» попали статусы: **{', '.join(display_statuses)}**" + (" и др." if len(other_statuses) > 10 else ""))

# ==========================================
# 2. ЭКРАН ПОТОКА (Блоки)
# ==========================================
elif st.session_state.view == 'flow':
    if st.button("⬅️ Назад на главную"):
        st.session_state.view = 'dashboard'
        st.rerun()
        
    flow_name = "Входящий поток" if st.session_state.selected_flow == "in" else "Исходящий поток"
    st.subheader(f"Выбран: {flow_name}")
    
    df_flow = df_curr[df_curr['flow'] == flow_name] if not df_curr.empty else pd.DataFrame()
    
    if df_flow.empty:
        st.info("Нет данных по этому потоку за выбранный период.")
    else:
        flow_total_sum = df_flow['loss_sum'].sum()
        st.markdown(f"**Общая сумма списаний по потоку:** `{flow_total_sum:,.2f} ₽`".replace(",", " "))
        
        df_blocks = df_flow.groupby('block_name')[['loss_sum', 'loss_qty']].sum().reset_index()
        df_blocks['pct'] = (df_blocks['loss_sum'] / flow_total_sum) * 100 if flow_total_sum > 0 else 0
        df_blocks = df_blocks.sort_values('loss_sum', ascending=False)
        
        st.markdown("---")
        st.markdown("### Выберите блок для детализации:")
        
        # Стили для нейтральных чипов (пока без цвета) и голубой обводки карточек
        st.markdown("""
        <style>
        /* Голубая обводка для контейнеров карточек */
        div[class*="st-key-card_"] {
            border: 1.5px solid #00BFFF !important; 
            border-radius: 8px;
            padding: 10px;
        }
        
        /* Нейтральный чип (серый фон, черный/белый текст) */
        .chip-neutral { 
            background-color: #e0e0e0; 
            color: #000000; 
            padding: 6px 14px; 
            border-radius: 20px; 
            font-size: 16px; /* Увеличили шрифт */
            font-weight: 600; 
            display: inline-block; 
            margin-bottom: 10px; 
        }
        </style>
        """, unsafe_allow_html=True)
        
        cols = st.columns(4)
        for i, row in df_blocks.iterrows():
            with cols[i % 4]:
                # Форматируем процент
                if row['pct'] < 0.1 and row['pct'] > 0:
                    pct_text = "< 0.1%"
                else:
                    pct_text = f"{row['pct']:.1f}%"
                
                # Убрали логику цветов, теперь все карточки просто с ключом card_block
                with st.container(key=f"card_block_{i}", border=False):
                    st.markdown(f"**{row['block_name']}**")
                    st.markdown(f"### {row['loss_sum']:,.0f} ₽".replace(",", " "))
                    
                    # Чип с увеличенным шрифтом, пока нейтрального цвета
                    st.markdown(f"<div class='chip-neutral'>{pct_text} от потока</div>", unsafe_allow_html=True)
                    
                    if st.button(f"Подробнее", key=f"btn_block_{row['block_name']}", use_container_width=True):
                        st.session_state.selected_block = row['block_name']
                        st.session_state.view = 'block_detail'
                        st.rerun()

# ==========================================
# 3. ЭКРАН БЛОКА
# ==========================================
elif st.session_state.view == 'block_detail':
    if st.button("⬅️ Назад к выбору блока"):
        st.session_state.view = 'flow'
        st.rerun()
        
    flow_name = "Входящий поток" if st.session_state.selected_flow == "in" else "Исходящий поток"
    block = st.session_state.selected_block
    
    df_block = df_curr[(df_curr['flow'] == flow_name) & (df_curr['block_name'] == block)]
    
    st.subheader(f"Блок: {block} ({flow_name})")
    
    block_total = df_block['loss_sum'].sum()
    block_qty = df_block['loss_qty'].sum()
    st.metric(label="Сумма списаний по блоку", value=f"{block_total:,.2f} ₽".replace(",", " "), delta=f"{block_qty:,.0f} шт", delta_color="off")
    
    st.markdown("---")
    st.markdown("#### Детализация по причинам (LR) внутри блока")
    
    df_lr = df_block.groupby('reason')[['loss_sum', 'loss_qty']].sum().reset_index()
    df_lr.columns = ['Причина (LR)', 'Сумма (₽)', 'Кол-во (шт)']
    df_lr = df_lr.sort_values('Сумма (₽)', ascending=False)
    
    st.dataframe(df_lr, use_container_width=True, hide_index=True)
