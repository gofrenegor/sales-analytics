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
        
        # Находим максимальный процент, чтобы выделить самый плохой блок
        max_pct = df_blocks['pct'].max()
        
        st.markdown("---")
        st.markdown("### Выберите блок для детализации:")
        
        # Стили для нейтральных чипов и обводки карточек
        st.markdown("""
        <style>
        /* Обводки карточек */
        div[class*="card_blue"] { border: 1.5px solid #00BFFF !important; border-radius: 8px; padding: 10px; }
        div[class*="card_red"] { border: 2px solid #FF4B4B !important; border-radius: 8px; padding: 10px; box-shadow: 0 0 8px rgba(255, 75, 75, 0.3); }
        
        /* Нейтральный чип */
        .chip-neutral { 
            background-color: #e0e0e0; 
            color: #000000; 
            padding: 6px 14px; 
            border-radius: 20px; 
            font-size: 16px; 
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
                
                # Определяем, является ли этот блок самым проблемным (максимальный процент)
                is_worst = (row['pct'] == max_pct)
                card_class = "card_red" if is_worst else "card_blue"
                
                # В Streamlit ключ должен быть уникальным и валидным (только буквы и цифры)
                # Поэтому мы используем счетчик i для ключа
                with st.container(key=f"card_block_{i}", border=False):
                    # Передаем класс обводки через маркер
                    st.markdown(f"<div class='{card_class}'></div>", unsafe_allow_html=True)
                    
                    st.markdown(f"**{row['block_name']}**")
                    st.markdown(f"### {row['loss_sum']:,.0f} ₽".replace(",", " "))
                    
                    st.markdown(f"<div class='chip-neutral'>{pct_text} от потока</div>", unsafe_allow_html=True)
                    
                    if st.button(f"Подробнее", key=f"btn_block_{row['block_name']}", use_container_width=True):
                        st.session_state.selected_block = row['block_name']
                        st.session_state.view = 'block_detail'
                        st.rerun()
