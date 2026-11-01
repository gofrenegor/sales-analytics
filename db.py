"""Postgres (боевая база) и SQLite (проверка). Схема таблицы не пересоздаётся."""

import json
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
_ready = {}
_columns_cache = None
_generation = 0
_cache_lock = threading.Lock()
_write_lock = threading.Lock()
CACHE_TTL = 3600
READ_COLUMNS = (
    "loss_date", "barcode", "block_name", "status_block_id", "loss_sum", "loss_qty",
    "reason", "status_before_date", "status_before", "block_id", "office_id",
    "status_employee_id",
)
WRITE_COLUMNS = READ_COLUMNS
# Главный экран не читает ШК и бейджик. Сотрудники читают бейджик, без списка ШК.
FRAME_COLUMNS = {
    "report": (
        "loss_date", "block_name", "loss_sum", "loss_qty",
        "reason", "status_before", "block_id", "office_id",
    ),
    "employees": (
        "loss_date", "block_name", "loss_sum",
        "reason", "status_before", "block_id", "office_id",
        "status_employee_id",
    ),
}


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
    import tomllib
    for path in (ROOT / "secrets.toml", ROOT / ".streamlit" / "secrets.toml"):
        if not path.exists():
            continue
        with path.open("rb") as handle:
            data = tomllib.load(handle)
        url = str((data.get("database") or {}).get("connection_string") or "").strip()
        if url:
            return url
    return ""


def database_url():
    url = os.environ.get("DATABASE_URL", "").strip() or read_secrets_url()
    if not url:
        raise ConfigError(
            "Не задана строка подключения. Укажите DATABASE_URL или secrets.toml "
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
    global _columns_cache, _generation
    with _cache_lock:
        _generation += 1
        _cache.clear()
        _ready.clear()
        _columns_cache = None


def cache_generation():
    with _cache_lock:
        return _generation


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


ACCESS_ROLES = ("view", "admin")


def _ensure_loss_index(conn):
    if inspect(conn).has_table("losses"):
        conn.execute(text("CREATE INDEX IF NOT EXISTS losses_loss_date_idx ON losses (loss_date)"))


def ensure_schema():
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE TABLE IF NOT EXISTS access_users ("
            "id INTEGER PRIMARY KEY, role TEXT NOT NULL, phone TEXT)"
        ))
        access_cols = {col["name"] for col in inspect(conn).get_columns("access_users")}
        if "phone" not in access_cols:
            conn.execute(text("ALTER TABLE access_users ADD COLUMN phone TEXT"))
        conn.execute(text(
            "CREATE UNIQUE INDEX IF NOT EXISTS access_users_phone_idx ON access_users (phone)"
        ))
        conn.execute(text(
            "INSERT INTO access_users (id, role) "
            "SELECT 1, 'admin' WHERE NOT EXISTS (SELECT 1 FROM access_users WHERE id = 1)"
        ))
        if not inspect(conn).has_table("losses"):
            return
        existing = {col["name"] for col in inspect(conn).get_columns("losses")}
        for col in ("office_id", "block_id", "status_employee_id"):
            if col not in existing:
                conn.execute(text(f"ALTER TABLE losses ADD COLUMN {col} TEXT"))
        _ensure_loss_index(conn)


def known_columns():
    """None — таблицы нет. Иначе множество колонок. Повторный показ не ходит в каталог."""
    global _columns_cache
    with _cache_lock:
        if _columns_cache is not None and _columns_cache[0] == _generation:
            found = _columns_cache[1]
            return None if found == () else found
        generation = _generation
    engine = get_engine()
    try:
        with engine.connect() as conn:
            if not inspect(conn).has_table("losses"):
                found = ()
            else:
                found = frozenset(col["name"] for col in inspect(conn).get_columns("losses"))
    except Exception as exc:
        if is_missing_table(exc):
            found = ()
        else:
            raise DataError(f"Не удалось получить данные из базы: {public_error(exc)}") from exc
    with _cache_lock:
        if _generation == generation:
            _columns_cache = (generation, found)
        elif _columns_cache is not None and _columns_cache[0] == _generation:
            found = _columns_cache[1]
    return None if found == () else found


def normalize_phone(value):
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())
    if len(digits) == 11 and digits.startswith("8"):
        digits = "7" + digits[1:]
    elif len(digits) == 10:
        digits = "7" + digits
    if len(digits) != 11 or not digits.startswith("7"):
        raise ValueError("Некорректный номер")
    return digits


def _access_row(row):
    if row is None or row.role not in ACCESS_ROLES:
        return None
    phone = row.phone or None
    return {"id": int(row.id), "role": row.role, "phone": phone}


def get_access_user(user_id):
    engine = get_engine()
    try:
        with engine.connect() as conn:
            if not inspect(conn).has_table("access_users"):
                return None
            row = conn.execute(
                text("SELECT id, role, phone FROM access_users WHERE id = :id"),
                {"id": int(user_id)},
            ).one_or_none()
    except Exception as exc:
        if is_missing_table(exc):
            return None
        raise DataError(f"Не удалось прочитать доступ: {public_error(exc)}") from exc
    return _access_row(row)


def match_login(user_id, phone_raw):
    """Пара ID + телефон. Пока номер не записан, пускает по одному ID."""
    user = get_access_user(user_id)
    if not user:
        return None
    if not user.get("phone"):
        return user
    try:
        phone = normalize_phone(phone_raw)
    except ValueError:
        return None
    if phone != user["phone"]:
        return None
    return user


def next_access_id():
    engine = get_engine()
    try:
        with engine.connect() as conn:
            value = conn.execute(text("SELECT COALESCE(MAX(id), 0) + 1 FROM access_users")).scalar()
    except Exception as exc:
        raise DataError(f"Не удалось прочитать доступ: {public_error(exc)}") from exc
    return int(value or 1)


def _phone_taken(conn, phone, user_id):
    row = conn.execute(
        text("SELECT id FROM access_users WHERE phone = :phone AND id != :id"),
        {"phone": phone, "id": int(user_id)},
    ).one_or_none()
    return row is not None


def grant_access(role, phone):
    if role not in ACCESS_ROLES:
        raise ValueError("Выберите права")
    normalized = normalize_phone(phone)
    with _write_lock:
        engine = get_engine()
        try:
            with engine.begin() as conn:
                if _phone_taken(conn, normalized, 0):
                    raise ValueError("Номер уже выдан")
                next_id = int(conn.execute(
                    text("SELECT COALESCE(MAX(id), 0) + 1 FROM access_users")
                ).scalar() or 1)
                conn.execute(
                    text("INSERT INTO access_users (id, role, phone) VALUES (:id, :role, :phone)"),
                    {"id": next_id, "role": role, "phone": normalized},
                )
        except ValueError:
            raise
        except Exception as exc:
            raise DataError(f"Не удалось выдать доступ: {public_error(exc)}") from exc
    return {"id": next_id, "role": role, "phone": normalized}


def set_access_phone(user_id, phone):
    normalized = normalize_phone(phone)
    with _write_lock:
        engine = get_engine()
        try:
            with engine.begin() as conn:
                if _phone_taken(conn, normalized, user_id):
                    raise ValueError("Номер уже выдан")
                result = conn.execute(
                    text("UPDATE access_users SET phone = :phone WHERE id = :id"),
                    {"phone": normalized, "id": int(user_id)},
                )
                if not result.rowcount:
                    raise ValueError("Нет такого ID")
        except ValueError:
            raise
        except Exception as exc:
            raise DataError(f"Не удалось записать номер: {public_error(exc)}") from exc
    return get_access_user(user_id)


def load_data(start, end, columns):
    existing = known_columns()
    if not existing:
        return analytics.prepare_losses(pd.DataFrame())
    if "loss_date" not in existing:
        raise DataError("В таблице losses нет колонки loss_date")
    cols = [col for col in columns if col in existing]
    params = {"start": start.isoformat(), "end": (end + timedelta(days=1)).isoformat()}
    sql = text(
        "SELECT " + ", ".join(cols) + " FROM losses WHERE loss_date >= :start AND loss_date < :end"
    )
    try:
        df = pd.read_sql(sql, get_engine(), params=params)
    except Exception as exc:
        if is_missing_table(exc):
            return analytics.prepare_losses(pd.DataFrame())
        raise DataError(f"Не удалось получить данные из базы: {public_error(exc)}") from exc
    return analytics.prepare_losses(df)


def load_cached(start, end, kind="report"):
    """Кадр только для чтения: отчёт его не копирует и не меняет."""
    columns = FRAME_COLUMNS[kind]
    key = (kind, start.isoformat(), end.isoformat())
    now = time.monotonic()
    generation = cache_generation()
    with _cache_lock:
        hit = _cache.get(key)
        if hit and now - hit[0] < CACHE_TTL and hit[2] == generation:
            return hit[1]
    df = load_data(start, end, columns)
    with _cache_lock:
        if generation == _generation:
            _cache[key] = (time.monotonic(), df, generation)
    return df


def ready_key(kind, period):
    prev_start = period.get("_prev_start")
    prev_end = period.get("_prev_end")
    return (
        kind,
        period.get("mode") or "",
        period.get("compare", True) is not False,
        period["_start"].isoformat(),
        period["_end"].isoformat(),
        prev_start.isoformat() if prev_start else "",
        prev_end.isoformat() if prev_end else "",
    )


def get_ready(key):
    now = time.monotonic()
    with _cache_lock:
        hit = _ready.get(key)
        if hit and now - hit[0] < CACHE_TTL and hit[2] == _generation:
            return hit[1]
        if hit:
            _ready.pop(key, None)
    return None


def put_ready(key, payload, generation):
    from fastapi.encoders import jsonable_encoder
    body = json.dumps(
        jsonable_encoder(payload),
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    with _cache_lock:
        if generation == _generation:
            _ready[key] = (time.monotonic(), body, generation)
    return body


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
            _ensure_loss_index(conn)
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
