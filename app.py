import streamlit as st
import pandas as pd
import plotly.express as px

st.set_page_config(page_title="Аналитика потерь", page_icon="📦", layout="wide")

# ---------- Функция загрузки и очистки данных ----------
@st.cache_data
def load_data(file):
    # Читаем Excel
    df = pd.read_excel(file)
    
    # Оставляем только нужные колонки
    cols = [
        'Блок сборки', 'Кол-во ШК на сборке', 
        'Блок сортировки', 'Кол-во ШК на сортировке'
    ]
    # Убедимся, что колонки существуют (на случай если файл битый)
    existing_cols = [c for c in cols if c in df.columns]
    df = df[existing_cols].copy()
    
    # Переименовываем для удобства
    rename_dict = {
        'Блок сборки': 'assembly_block',
        'Кол-во ШК на сборке': 'assembly_qty',
        'Блок сортировки': 'sorting_block',
        'Кол-во ШК на сортировке': 'sorting_qty'
    }
    df = df.rename(columns=rename_dict)
    
    # Превращаем количества в числа (ошибки -> 0)
    if 'assembly_qty' in df.columns:
        df['assembly_qty'] = pd.to_numeric(df['assembly_qty'], errors='coerce').fillna(0)
    if 'sorting_qty' in df.columns:
        df['sorting_qty'] = pd.to_numeric(df['sorting_qty'], errors='coerce').fillna(0)
        
    return df

def format_num(x):
    return f"{x:,.0f}".replace(",", " ")

# ---------- Интерфейс ----------
st.title("📦 Аналитика потерь на складе")
st.markdown("Загрузите два файла: текущую неделю и прошлую для сравнения.")

col1, col2 = st.columns(2)
with col1:
    file_curr = st.file_uploader("Загрузить ТЕКУЩУЮ неделю (напр. 19.10-24.10)", type=["xlsx"])
with col2:
    file_prev = st.file_uploader("Загрузить ПРОШЛУЮ неделю (напр. 12.10-18.10)", type=["xlsx"])

if file_curr and file_prev:
    try:
        df_curr = load_data(file_curr)
        df_prev = load_data(file_prev)
        
        # Считаем общие суммы
        curr_as = df_curr['assembly_qty'].sum() if 'assembly_qty' in df_curr else 0
        prev_as = df_prev['assembly_qty'].sum() if 'assembly_qty' in df_prev else 0
        
        curr_sr = df_curr['sorting_qty'].sum() if 'sorting_qty' in df_curr else 0
        prev_sr = df_prev['sorting_qty'].sum() if 'sorting_qty' in df_prev else 0
        
        # Считаем дельты
        def calc_delta(curr, prev):
            diff = curr - prev
            pct = (diff / prev * 100) if prev > 0 else 0
            return diff, pct

        # ---------- KPI ----------
        st.markdown("---")
        kpi1, kpi2, kpi3 = st.columns(3)
        
        diff_as, pct_as = calc_delta(curr_as, prev_as)
        kpi1.metric(
            "Потери на СБОРКЕ (шт)", 
            format_num(curr_as), 
            f"{pct_as:+.1f}% ({format_num(diff_as)} шт)"
        )
        
        diff_sr, pct_sr = calc_delta(curr_sr, prev_sr)
        kpi2.metric(
            "Потери на СОРТИРОВКЕ (шт)", 
            format_num(curr_sr), 
            f"{pct_sr:+.1f}% ({format_num(diff_sr)} шт)"
        )
        
        diff_total = diff_as + diff_sr
        pct_total = (diff_total / (prev_as + prev_sr) * 100) if (prev_as + prev_sr) > 0 else 0
        kpi3.metric(
            "ОБЩИЕ ПОТЕРИ (шт)", 
            format_num(curr_as + curr_sr), 
            f"{pct_total:+.1f}% ({format_num(diff_total)} шт)"
        )

        # ---------- Таблицы по блокам ----------
        st.markdown("---")
        st.subheader("Детализация по блокам")
        
        tab1, tab2 = st.tabs(["Сборка", "Сортировка"])
        
        with tab1:
            if 'assembly_block' in df_curr and 'assembly_block' in df_prev:
                # Группируем
                c_grp = df_curr.groupby('assembly_block')['assembly_qty'].sum().reset_index()
                p_grp = df_prev.groupby('assembly_block')['assembly_qty'].sum().reset_index()
                
                # Объединяем
                merged_as = pd.merge(c_grp, p_grp, on='assembly_block', how='outer', suffixes=('_curr', '_prev')).fillna(0)
                merged_as['delta'] = merged_as['assembly_qty_curr'] - merged_as['assembly_qty_prev']
                merged_as['delta_pct'] = (merged_as['delta'] / merged_as['assembly_qty_prev'].replace(0, 1)) * 100
                
                # Сортируем по текущим потерям
                merged_as = merged_as.sort_values('assembly_qty_curr', ascending=False)
                
                # Переименовываем для красоты
                merged_as.columns = ['Блок', 'Текущая неделя', 'Прошлая неделя', 'Δ (шт)', 'Δ (%)']
                
                # График
                fig = px.bar(merged_as.head(10), x='Блок', y=['Текущая неделя', 'Прошлая неделя'], 
                             barmode='group', title='Топ-10 блоков по потерям на сборке')
                st.plotly_chart(fig, use_container_width=True)
                
                st.dataframe(merged_as, use_container_width=True, hide_index=True)

        with tab2:
            if 'sorting_block' in df_curr and 'sorting_block' in df_prev:
                # Группируем
                c_grp = df_curr.groupby('sorting_block')['sorting_qty'].sum().reset_index()
                p_grp = df_prev.groupby('sorting_block')['sorting_qty'].sum().reset_index()
                
                # Объединяем
                merged_sr = pd.merge(c_grp, p_grp, on='sorting_block', how='outer', suffixes=('_curr', '_prev')).fillna(0)
                merged_sr['delta'] = merged_sr['sorting_qty_curr'] - merged_sr['sorting_qty_prev']
                
                merged_sr = merged_sr.sort_values('sorting_qty_curr', ascending=False)
                merged_sr.columns = ['Блок', 'Текущая неделя', 'Прошлая неделя', 'Δ (шт)']
                
                # График
                fig = px.bar(merged_sr.head(10), x='Блок', y=['Текущая неделя', 'Прошлая неделя'], 
                             barmode='group', title='Топ-10 блоков по потерям на сортировке')
                st.plotly_chart(fig, use_container_width=True)
                
                st.dataframe(merged_sr, use_container_width=True, hide_index=True)

    except Exception as e:
        st.error(f"Ошибка при обработке файлов: {e}")
else:
    st.info("👆 Пожалуйста, загрузите оба файла (Текущую и Прошлую неделю) в панели выше, чтобы увидеть аналитику.")
