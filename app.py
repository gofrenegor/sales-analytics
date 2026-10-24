# ... (начало файла app.py без изменений) ...

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
        
        # Выводим по 4 колонки
        cols = st.columns(4)
        for i, row in df_blocks.iterrows():
            with cols[i % 4]:
                # Определяем цвет обводки и текста
                is_red = row['pct'] > 10
                color_class = "border-red" if is_red else "border-green"
                text_color = "#FF4B4B" if is_red else "#2e7d32"
                
                # Создаем контейнер с уникальным ключом и классом (inject_css его подхватит)
                with st.container(key=f"card_{row['block_name']}"):
                    
                    # Внедряем динамический класс обводки через HTML-маркер (Streamlit его распознает)
                    st.markdown(f"<div class='{color_class}'></div>", unsafe_allow_html=True)
                    
                    st.markdown(f"**{row['block_name']}**")
                    st.markdown(f"### {row['loss_sum']:,.0f} ₽".replace(",", " "))
                    st.markdown(f"<p style='color: {text_color}; font-size: 14px; margin-bottom: 15px;'>{row['pct']:.1f}% от потока</p>", unsafe_allow_html=True)
                    
                    if st.button(f"Подробнее", key=f"btn_block_{row['block_name']}", use_container_width=True):
                        st.session_state.selected_block = row['block_name']
                        st.session_state.view = 'block_detail'
                        st.rerun()

# ... (экран блока остается без изменений) ...
