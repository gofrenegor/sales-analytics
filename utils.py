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
        .st-key-btn_inbound button {
            background-color: #2e7d32 !important; color: white !important; border: 1px solid #2e7d32 !important;
        }
        .st-key-btn_inbound button:hover { background-color: #1b5e20 !important; border: 1px solid #1b5e20 !important; }
        .st-key-btn_outbound button {
            background-color: #1565c0 !important; color: white !important; border: 1px solid #1565c0 !important;
        }
        .st-key-btn_outbound button:hover { background-color: #0d47a1 !important; border: 1px solid #0d47a1 !important; }
        </style>
    """, unsafe_allow_html=True)

def clean_date_col(series):
    if pd.api.types.is_datetime64_any_dtype(series): return series
    s = series.astype(str).replace(['NaT', 'nan', 'None'], pd.NA)
    s = s.str.replace(r'\+\d{2}:\d{2}', '', regex=True)
    s = s.str.replace(r'\.\d+', '', regex=True)
    return pd.to_datetime(s, errors='coerce')

def process_excel(file):
    df = pd.read_excel(file)
    df.columns = df.columns.astype(str).str.strip()
    cols_map = {
        'ШК': 'barcode', 'Наименование блока': 'block_name', 'ID блока': 'block_id',
        'Сумма списания': 'loss_sum', 'Кол-во лог объектов для списания': 'loss_qty',
        'Дата первого списания': 'loss_date', 'Лостризон первого списания': 'reason',        
        'Дата статуса перед списанием': 'status_before_date', 'Статус перед списанием': 'status_before'
    }
    existing_cols = [c for c in cols_map.keys() if c in df.columns]
    df = df[existing_cols].rename(columns=cols_map)
    
    for col in ['loss_sum', 'loss_qty']:
        if col in df.columns: df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)
    
    for col in ['loss_date', 'status_before_date']:
        if col in df.columns: df[col] = clean_date_col(df[col])
            
    for col in ['block_name', 'reason', 'status_before']:
        if col in df.columns: 
            df[col] = df[col].fillna("Не указано").astype(str)
            if col == 'reason': df[col] = df[col].str.replace(r'\.0$', '', regex=True).str.strip()
    return df

def get_flow_name(status_before):
    status = str(status_before).strip().upper()
    if status == "НЕ УКАЗАНО" or status == "" or status == "NAN":
        return 'Другое'
    
    inbound = ['ANV', 'EPR', 'IPS', 'ORG', 'PVP', 'RGP', 'RNP', 'RSP', 'RWP', 'TWR', 'WDC', 'WWE', 'WIJ', 'WPT', 'WPU']
    outbound = ['GWS', 'IKZ', 'IPG', 'ISB', 'LAM', 'LIO', 'LIP', 'LPG', 'LSS', 'OPS', 'OSE', 'OSR', 'PAP', 'PWT', 'SAP', 'SAS', 'SBN', 'SMC', 'SPM', 'SPS', 'SSG', 'WLI', 'WLN', 'WLR', 'WLT', 'WMI']
    
    if status in inbound: return 'Входящий поток'
    elif status in outbound: return 'Исходящий поток'
    else: return 'Другое'

BLOCK_MAPPING = {
    "Рязань Тюшевское 1": "Рязань Тюшевское 1",
    "Рязань КГТ Левый": "Рязань Тюшевское 1",
    "Рязань Дорогостой 2": "Рязань Тюшевское 2",
    "Рязань Красота 2": "Рязань Тюшевское 2",
    "Рязань Продукты 2": "Рязань Тюшевское 2",
    "Рязань КГТ 2": "Рязань Тюшевское 2",
    "Рязань Тюшевское 3": "Рязань Тюшевское 3",
    "Рязань Тюшевское 3 КГТ": "Рязань Тюшевское 3",
    "Рязань Тюшевское 3 тест": "Рязань Тюшевское 3",
    "Рязань Тюшевское 4": "Рязань Тюшевское 4"
}

@st.cache_data(ttl=3600)
def load_data(start, end):
    query = f"SELECT * FROM losses WHERE loss_date >= '{start}' AND loss_date < '{end + timedelta(days=1)}'"
    try:
        df = pd.read_sql(query, engine)
        if not df.empty:
            df['loss_date'] = pd.to_datetime(df['loss_date'])
            if 'status_before_date' in df.columns:
                df['status_before_date'] = pd.to_datetime(df['status_before_date'])
            df['reason'] = df['reason'].astype(str).str.replace(r'\.0$', '', regex=True).str.strip()
            
            if 'status_before' in df.columns:
                df['status_before'] = df['status_before'].fillna('').astype(str)
                df['status_before'] = df['status_before'].str.upper().str.replace(r'[^A-Z]', '', regex=True)
                df.loc[df['status_before'] == '', 'status_before'] = 'НЕ УКАЗАНО'
                df['flow'] = df['status_before'].apply(get_flow_name)
            else:
                df['flow'] = 'Другое'
                
            # --- ИСПРАВЛЕННАЯ ЛОГИКА РАСПРЕДЕЛЕНИЯ ПО БЛОКАМ ---
            def get_block_group(row):
                # Берем ID, чистим от пробелов и отрезаем ".0" (если Pandas сделал float)
                b_id = str(row.get('block_id', '')).strip().replace('.0', '')
                b_name = str(row.get('block_name', '')).strip()
                
                # Если ID равен 6800, это Опер площадь
                if b_id == '6800':
                    return 'Опер площадь'
                
                return BLOCK_MAPPING.get(b_name, 'Другое')
                
            df['block_group'] = df.apply(get_block_group, axis=1)
            
        return df
    except Exception:
        return pd.DataFrame()
        
# ... (остальные функции get_delta_text, render_date_selector, get_db_summary, render_admin_panel без изменений) ...
