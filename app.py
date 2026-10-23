import streamlit as st
import pandas as pd
import numpy as np
from datetime import date, timedelta
import plotly.express as px

st.set_page_config(page_title="Аналитика продаж", page_icon="📊", layout="wide")


# ---------- Генерация выдуманных данных ----------
@st.cache_data
def generate_demo_data(days: int = 180, seed: int = 42) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    end = date.today() - timedelta(days=1)   # «вчера» = последний полный день
    start = end - timedelta(days=days - 1)
    dates = pd.date_range(start, end, freq="D")

    base = {"Электроника": 80_000, "Одежда": 45_000, "Продукты": 30_000,
            "Книги": 12_000, "Спорт": 20_000}
    avg_check = {"Электроника": 8_500, "Одежда": 2_500, "Продукты": 800,
                 "Книги": 600, "Спорт": 3_000}

    rows = []
    for i, d in enumerate(dates):
        trend = 1 + i * 0.0015                 # лёгкий рост
        dow = 1.25 if d.weekday() >= 5 else 1.0  # выходные выше
        for cat, b in base.items():
            revenue = max(b * trend * dow * rng.normal(1.0, 0.08), 1000)
            orders = max(int(revenue / avg_check[cat] * rng.normal(1.0, 0.05)), 1)
            rows.append({"date": d, "category": cat,
                         "revenue": round(revenue, 2), "orders": orders})

    df = pd.DataFrame(rows)
    df["avg_check"] = df["revenue"] / df["orders"]
    return df


# ---------- Загрузка своего CSV ----------
@st.cache_data
def load_uploaded(file) -> pd.DataFrame:
    df = pd.read_csv(file)
    df.columns = [c.strip().lower() for c in df.columns]
    required = {"date", "category", "revenue", "orders"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"Нет колонок: {', '.join(sorted(missing))}")
    df["date"] = pd.to_datetime(df["date"]).dt.normalize()
    df["revenue"] = pd.to_numeric(df["revenue"])
    df["orders"] = pd.to_numeric(df["orders"])
    df["avg_check"] = df["revenue"] / df["orders"].replace(0, np.nan)
    return df


# ---------- Утилиты ----------
def sum_period(df, start, end, col="revenue"):
    m = (df["date"] >= pd.Timestamp(start)) & (df["date"] <= pd.Timestamp(end))
    return float(df.loc[m, col].sum())


def compare(df, days, col="revenue"):
    end = df["date"].max().date()
    cur_start = end - timedelta(days=days - 1)
    prev_end = cur_start - timedelta(days=1)
    prev_start = prev_end - timedelta(days=days - 1)

    cur = sum_period(df, cur_start, end, col)
    prev = sum_period(df, prev_start, prev_end, col)
    diff = cur - prev
    pct = (diff / prev * 100) if prev else float("nan")
    return {"cur": cur, "prev": prev, "diff": diff, "pct": pct,
            "cur_start": cur_start, "prev_start": prev_start}


def money(x): return f"{x:,.0f} ₽".replace(",", " ")
def pct_s(p): return "—" if pd.isna(p) else f"{p:+.1f}%"


# ---------- Боковая панель ----------
st.sidebar.header("Данные")
uploaded = st.sidebar.file_uploader(
    "Загрузить CSV (колонки: date, category, revenue, orders)", type=["csv"]
)

if uploaded:
    try:
        raw = load_uploaded(uploaded)
        st.sidebar.success(f"Загружено строк: {len(raw)}")
    except Exception as e:
        st.sidebar.error(f"Ошибка чтения файла: {e}")
        raw = generate_demo_data()
else:
    raw = generate_demo_data()
    st.sidebar.info("Используются демо-данные за 180 дней")

cats = sorted(raw["category"].unique())
sel_cats = st.sidebar.multiselect("Категории", cats, default=cats)
df = raw[raw["category"].isin(sel_cats)].copy()

if df.empty:
    st.warning("Нет данных по выбранным фильтрам")
    st.stop()


# ---------- Заголовок ----------
st.title("📊 Аналитика продаж")
st.caption(
    f"Период данных: {df['date'].min().date()} — {df['date'].max().date()} · "
    f"«вчера» = последний полный день в данных"
)


# ---------- KPI ----------
c1, c2, c3 = st.columns(3)
for col, (label, days) in zip(
    [c1, c2, c3],
    [("Вчера", 1), ("Неделя (7 дней)", 7), ("Месяц (30 дней)", 30)],
):
    r = compare(df, days)
    with col:
        st.metric(
            label=f"{label}: выручка",
            value=money(r["cur"]),
            delta=f"{pct_s(r['pct'])} · {r['diff']:+,.0f} ₽".replace(",", " "),
        )
        st.caption(f"Предыдущий период: {money(r['prev'])}")


# ---------- Сводная таблица ----------
st.subheader("Сравнение периодов")
rows = []
for label, days in [("День", 1), ("Неделя", 7), ("Месяц", 30)]:
    r = compare(df, days)
    rows.append({
        "Период": label,
        "Текущий": money(r["cur"]),
        "Предыдущий": money(r["prev"]),
        "Δ, ₽": f"{r['diff']:+,.0f}".replace(",", " "),
        "Δ, %": pct_s(r["pct"]),
    })
st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)


# ---------- Динамика ----------
st.subheader("Динамика выручки по дням")
daily = df.groupby("date", as_index=False)["revenue"].sum().sort_values("date")
daily["ma7"] = daily["revenue"].rolling(7, min_periods=1).mean()

fig = px.line(
    daily, x="date", y=["revenue", "ma7"],
    labels={"value": "Выручка, ₽", "date": "Дата", "variable": "Ряд"},
)
fig.for_each_trace(lambda t: t.update(name={"revenue": "Выручка",
                                             "ma7": "Среднее за 7 дней"}[t.name]))
fig.update_layout(hovermode="x unified", legend_title="")
st.plotly_chart(fig, use_container_width=True)


# ---------- По категориям ----------
left, right = st.columns(2)

with left:
    st.subheader("Выручка по категориям")
    by_cat = (df.groupby("category", as_index=False)["revenue"].sum()
                .sort_values("revenue", ascending=False))
    fig2 = px.bar(by_cat, x="category", y="revenue",
                  labels={"category": "Категория", "revenue": "Выручка, ₽"})
    st.plotly_chart(fig2, use_container_width=True)

with right:
    st.subheader("Δ по категориям")
    per_cat = []
    for cat in sel_cats:
        sub = df[df["category"] == cat]
        row = {"Категория": cat}
        for label, days in [("День", 1), ("Неделя", 7), ("Месяц", 30)]:
            r = compare(sub, days)
            row[f"{label} Δ%"] = pct_s(r["pct"])
        per_cat.append(row)
    st.dataframe(pd.DataFrame(per_cat), use_container_width=True, hide_index=True)


# ---------- Сырые данные ----------
with st.expander("Показать исходные данные"):
    st.dataframe(df.sort_values(["date", "category"], ascending=[False, True]),
                 use_container_width=True, hide_index=True)
    st.download_button(
        "Скачать CSV",
        df.to_csv(index=False).encode("utf-8"),
        file_name="data.csv",
        mime="text/csv",
    )