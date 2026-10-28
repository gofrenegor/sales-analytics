"""Postgres (боевая база) и SQLite (проверка). Схема таблицы не пересоздаётся."""

import logging
import os
import threading
import time
from datetime import timedelta
from pathlib import Path

import pandas as pd
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.pool import NullPool

import analytics

log = logging.getLogger("losses")
ROOT = Path(__file__).resolve().parent

_engine = None
_cache = {}
_cache_lock = threading.Lock()
_write_lock = threading.Lock()
CACHE_TTL = 3600
READ_COLUMNS = (
    "loss_date", "barcode", "block_name", "status_block_id", "loss_sum", "loss_qty",
    "reason", "status_before_date", "status_before", "block_id", "office_id",
)
WRITE_COLUMNS = READ_COLUMNS + ("status_employee_id",)


class ConfigError(Exception):
    pass


class DataError(Exception):
    pass


def public_error(exc):
    import re
    text_value = str(exc)
    return re.sub(r"://[^@\s/]+@", "://***@", text_value)


def normalize_db_url(url):
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg2://" + url[len("postgresql://"):]
    return url


def read_secrets_url():
    path = ROOT / ".streamlit" / "secrets.toml"
    if not path.exists():
        return ""
    import tomllib
    with path.open("rb") as handle:
        data = tomllib.load(handle)
    return str((data.get("database") or {}).get("connection_string") or "").strip()


def database_url():
    url = os.environ.get("DATABASE_URL", "").strip() or read_secrets_url()
    if not url:
        raise ConfigError(
            "Не задана строка подключения. Укажите DATABASE_URL или .streamlit/secrets.toml "
            "([database] connection_string)."
        )
    return normalize_db_url(url)


def get_engine():
    global _engine
    if _engine is not None:
        return _engine
    url = database_url()
    kwargs = {"pool_pre_ping": True}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
        kwargs["poolclass"] = NullPool
    else:
        kwargs["connect_args"] = {"connect_timeout": 10}
    _engine = create_engine(url, **kwargs)
    return _engine


def reset_engine():
    global _engine
    if _engine is not None:
        _engine.dispose()
    _engine = None
    clear_cache()


def clear_cache():
    with _cache_lock:
        _cache.clear()


def is_missing_table(exc):
    msg = str(exc).lower()
    return "no such table" in msg or ("relation" in msg and "does not exist" in msg)


def _dialect():
    return get_engine().dialect.name


def _column_type(name, dialect):
    if name in ("loss_sum", "loss_qty"):
        return "DOUBLE PRECISION" if dialect == "postgresql" else "REAL"
    if name in ("loss_date", "status_before_date"):
        return "TIMESTAMP"
    return "TEXT"


def _add_missing_columns(conn, df, dialect):
    existing = {col["name"] for col in inspect(conn).get_columns("losses")}
    for col in df.columns:
        if col in WRITE_COLUMNS and col not in existing:
            conn.execute(text(f"ALTER TABLE losses ADD COLUMN {col} {_column_type(col, dialect)}"))
            existing.add(col)
    return existing


def ensure_schema():
    engine = get_engine()
    with engine.begin() as conn:
        if not inspect(conn).has_table("losses"):
            return
        existing = {col["name"] for col in inspect(conn).get_columns("losses")}
        for col in ("office_id", "block_id", "status_employee_id"):
            if col not in existing:
                conn.execute(text(f"ALTER TABLE losses ADD COLUMN {col} TEXT"))


def load_data(start, end):
    engine = get_engine()
    try:
        with engine.connect() as conn:
            if not inspect(conn).has_table("losses"):
                return analytics.prepare_losses(pd.DataFrame())
            existing = {col["name"] for col in inspect(conn).get_columns("losses")}
    except Exception as exc:
        if is_missing_table(exc):
            return analytics.prepare_losses(pd.DataFrame())
        raise DataError(f"Не удалось получить данные из базы: {public_error(exc)}") from exc
    cols = [col for col in READ_COLUMNS if col in existing]
    if "loss_date" not in cols:
        raise DataError("В таблице losses нет колонки loss_date")
    params = {"start": start.isoformat(), "end": (end + timedelta(days=1)).isoformat()}
    sql = text(
        "SELECT " + ", ".join(cols) + " FROM losses WHERE loss_date >= :start AND loss_date < :end"
    )
    try:
        df = pd.read_sql(sql, engine, params=params)
    except Exception as exc:
        if is_missing_table(exc):
            return analytics.prepare_losses(pd.DataFrame())
        raise DataError(f"Не удалось получить данные из базы: {public_error(exc)}") from exc
    return analytics.prepare_losses(df)


def load_cached(start, end):
    key = (start.isoformat(), end.isoformat())
    now = time.monotonic()
    with _cache_lock:
        hit = _cache.get(key)
        if hit and now - hit[0] < CACHE_TTL:
            return hit[1].copy()
    df = load_data(start, end)
    with _cache_lock:
        _cache[key] = (time.monotonic(), df)
    return df.copy()


def empty_summary():
    return {"total_rows": 0, "min_date": None, "max_date": None, "months": []}


def _as_date(value):
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    try:
        if pd.isna(value):
            return None
    except TypeError:
        pass
    ts = pd.Timestamp(value)
    if pd.isna(ts):
        return None
    return ts.date().isoformat()


def get_summary():
    engine = get_engine()
    try:
        with engine.connect() as conn:
            if not inspect(conn).has_table("losses"):
                return empty_summary()
            total, min_date, max_date = conn.execute(
                text("SELECT COUNT(*), MIN(loss_date), MAX(loss_date) FROM losses")
            ).one()
            if not total:
                return empty_summary()
            if engine.dialect.name == "postgresql":
                month_sql = (
                    "SELECT TO_CHAR(loss_date, 'YYYY-MM') AS month, COUNT(*) AS rows_count "
                    "FROM losses GROUP BY 1 ORDER BY 1 DESC"
                )
            else:
                month_sql = (
                    "SELECT strftime('%Y-%m', loss_date) AS month, COUNT(*) AS rows_count "
                    "FROM losses GROUP BY 1 ORDER BY 1 DESC"
                )
            months = pd.read_sql(text(month_sql), conn)
    except Exception as exc:
        if is_missing_table(exc):
            return empty_summary()
        raise DataError(f"Не удалось прочитать сводку базы: {public_error(exc)}") from exc
    return {
        "total_rows": int(total),
        "min_date": _as_date(min_date),
        "max_date": _as_date(max_date),
        "months": [
            {"month": str(row.month), "rows": int(row.rows_count)}
            for row in months.itertuples(index=False)
        ],
    }


def _delete_sql():
    if _dialect() == "postgresql":
        return "DELETE FROM losses WHERE loss_date::date >= :s AND loss_date::date <= :e"
    return "DELETE FROM losses WHERE date(loss_date) >= :s AND date(loss_date) <= :e"


def replace_period(df, d_min, d_max):
    """Удаляет строки периода и записывает файл вместо них."""
    with _write_lock:
        engine = get_engine()
        with engine.begin() as conn:
            if inspect(conn).has_table("losses"):
                existing = _add_missing_columns(conn, df, engine.dialect.name)
                use = [col for col in df.columns if col in existing]
                conn.execute(text(_delete_sql()), {"s": d_min.isoformat(), "e": d_max.isoformat()})
                if use and len(df):
                    df.loc[:, use].to_sql("losses", conn, if_exists="append", index=False, chunksize=1000)
            elif len(df):
                df.to_sql("losses", conn, if_exists="append", index=False, chunksize=1000)
        clear_cache()


def truncate_losses():
    with _write_lock:
        engine = get_engine()
        with engine.begin() as conn:
            if not inspect(conn).has_table("losses"):
                clear_cache()
                return
            if engine.dialect.name == "postgresql":
                conn.execute(text("TRUNCATE TABLE losses RESTART IDENTITY"))
            else:
                conn.execute(text("DELETE FROM losses"))
        clear_cache()
