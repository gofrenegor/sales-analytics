import streamlit as st
import pandas as pd
from datetime import date, timedelta
from dateutil.relativedelta import relativedelta
from sqlalchemy import create_engine, text
import calendar

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

def inject_css():
    st.markdown("""
        <style>
        /* Стили для кнопок потоков */
        .st-key-btn_inbound button {
            background-color: #2e7d32 !important; color: white !important; border: 1px solid #2e7d32 !important;
        }
        .st-key-btn_inbound button:hover { background-color: #1b5e20 !important; border: 1px solid #1b5e20 !important; }
        .st-key-btn_outbound button {
            background-color: #1565c0 !important; color: white !important; border: 1px solid #1565c0 !important;
        }
        .st-key-btn_outbound button:hover { background-color: #0d47a1 !important; border: 1px solid #0d47a1 !important; }
        
        /* Базовые стили для карточек-чипов (контейнеров) */
        div[data-testid="stVerticalBlock"] > div[class*="st-key-card_"] {
            background-color: #1e1e1e;
            border-radius: 12px;
            padding: 15px;
            margin-bottom: 10px;
        }
        
        /* Динамические классы обводки */
        .border-green { border: 2px solid #2e7d32 !important; box-shadow: 0 0 10px rgba(46, 125, 50, 0.2); }
        .border-red { border: 2px solid #FF4B4B !important; box-shadow: 0 0 10px rgba(255, 75, 75, 0.2); }
        </style>
    """, unsafe_allow_html=True)

# ... (функции clean_date_col, process_excel, get_flow_name, load_data, get_delta_text, render_date_selector, get_db_summary остаются без изменений, я их не трогаю) ...
