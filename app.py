import streamlit as st
import utils

# Настройка главной страницы
st.set_page_config(page_title="Главная", page_icon="📦", layout="wide")
utils.inject_css()
utils.render_admin_panel()

st.title("📦 Аналитика списаний")
st.success("👈 Выберите нужный раздел в левом меню, чтобы начать работу.")

# Выводим общую сводку за дефолтный период
start_date, end_date, _, _ = utils.render_date_selector()
df = utils.load_data(start_date, end_date)

if not df.empty:
    st.markdown("---")
    st.markdown("### Общая сводка за выбранный период")
    st.metric("Всего списаний на сумму", f"{df['loss_sum'].sum():,.2f} ₽".replace(",", " "))
