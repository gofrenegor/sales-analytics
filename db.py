"""Postgres (боевая база) и SQLite (проверка). Схема таблицы не пересоздаётся."""

import hashlib
import hmac
import json
import logging
import os
import re
import secrets
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
    "status_employee_id", "assembly_employee_id", "last_sticker", "posted", "mx",
)
WRITE_COLUMNS = READ_COLUMNS
# Главный экран не читает ШК и бейджик. Список сотрудников — бейджик и флаг
# оприходования (процент найденного), без стикеров. Стикер и МХ — срез по клику.
# Сборка группируется по assembly_employee_id. NULL в этой колонке — старая
# строка: бейдж как в status_employee_id.
FRAME_COLUMNS = {
    "report": (
        "loss_date", "block_name", "loss_sum", "loss_qty",
        "reason", "status_before", "block_id", "office_id",
    ),
    "employees": (
        "loss_date", "block_name", "loss_sum",
        "reason", "status_before", "block_id", "office_id",
        "status_employee_id", "assembly_employee_id", "posted",
    ),
    # Проблемные ряды сборки: МХ и сумма. Бейджик и стикер не читаются.
    # status_before нужен, чтобы ORS попал в LR 5 так же, как в списке сотрудников.
    "rows": (
        "loss_date", "block_name", "loss_sum",
        "reason", "status_before", "block_id", "office_id", "mx",
    ),
}
DETAIL_COLUMNS = (
    "loss_date", "block_name", "loss_sum", "loss_qty",
    "reason", "status_before", "block_id", "office_id",
    "status_employee_id", "assembly_employee_id", "last_sticker", "posted", "mx",
)


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
    if not inspect(conn).has_table("losses"):
        return
    conn.execute(text("CREATE INDEX IF NOT EXISTS losses_loss_date_idx ON losses (loss_date)"))
    names = {col["name"] for col in inspect(conn).get_columns("losses")}
    if "status_employee_id" in names:
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS losses_employee_date_idx "
            "ON losses (status_employee_id, loss_date)"
        ))
    if "assembly_employee_id" in names:
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS losses_assembly_date_idx "
            "ON losses (assembly_employee_id, loss_date)"
        ))
    if "barcode" in names:
        conn.execute(text("CREATE INDEX IF NOT EXISTS losses_barcode_idx ON losses (barcode)"))


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
        if "email" not in access_cols:
            conn.execute(text("ALTER TABLE access_users ADD COLUMN email TEXT"))
        if "password_hash" not in access_cols:
            conn.execute(text("ALTER TABLE access_users ADD COLUMN password_hash TEXT"))
        conn.execute(text(
            "CREATE UNIQUE INDEX IF NOT EXISTS access_users_phone_idx ON access_users (phone)"
        ))
        conn.execute(text(
            "CREATE UNIQUE INDEX IF NOT EXISTS access_users_email_idx ON access_users (email)"
        ))
        conn.execute(text(
            "INSERT INTO access_users (id, role) "
            "SELECT 1, 'admin' WHERE NOT EXISTS (SELECT 1 FROM access_users WHERE id = 1)"
        ))
        if not inspect(conn).has_table("losses"):
            return
        existing = {col["name"] for col in inspect(conn).get_columns("losses")}
        for col in (
            "office_id", "block_id", "status_employee_id", "assembly_employee_id",
            "last_sticker", "posted", "mx",
        ):
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


MAIL_DOMAINS = ("gmail.com", "mail.ru")


def normalize_phone(value):
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())
    if len(digits) == 11 and digits.startswith("8"):
        digits = "7" + digits[1:]
    elif len(digits) == 10:
        digits = "7" + digits
    if len(digits) != 11 or not digits.startswith("7"):
        raise ValueError("Некорректный номер")
    return digits


def contact_kind(value):
    text_value = str(value or "").strip()
    if not text_value:
        return ""
    if "@" in text_value or text_value[0].isalpha():
        return "email"
    if text_value[0] in "+789":
        return "phone"
    return ""


def normalize_email(nick, domain=""):
    raw = str(nick or "").strip().lower()
    dom = str(domain or "").strip().lower()
    if "@" in raw:
        local, _, found = raw.partition("@")
        raw = local.strip()
        if found.strip():
            dom = found.strip()
    if (
        not raw
        or raw.startswith(".")
        or raw.endswith(".")
        or ".." in raw
        or not re.fullmatch(r"[a-z0-9._-]{1,64}", raw)
    ):
        raise ValueError("Некорректная почта")
    if dom not in MAIL_DOMAINS:
        raise ValueError("Домен только gmail.com или mail.ru")
    return f"{raw}@{dom}"


def _access_row(row):
    if row is None or row.role not in ACCESS_ROLES:
        return None
    return {
        "id": int(row.id),
        "role": row.role,
        "phone": row.phone or None,
        "email": row.email or None,
    }


def get_access_user(user_id):
    engine = get_engine()
    try:
        with engine.connect() as conn:
            if not inspect(conn).has_table("access_users"):
                return None
            row = conn.execute(
                text("SELECT id, role, phone, email FROM access_users WHERE id = :id"),
                {"id": int(user_id)},
            ).one_or_none()
    except Exception as exc:
        if is_missing_table(exc):
            return None
        raise DataError(f"Не удалось прочитать доступ: {public_error(exc)}") from exc
    return _access_row(row)


_PASSWORD_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789"
_PBKDF_ROUNDS = 120_000
_DUMMY_HASH = None


def generate_password():
    return "".join(secrets.choice(_PASSWORD_ALPHABET) for _ in range(8))


def hash_password(password):
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", str(password).encode("utf-8"), bytes.fromhex(salt), _PBKDF_ROUNDS,
    )
    return f"pbkdf2${_PBKDF_ROUNDS}${salt}${digest.hex()}"


def verify_password(password, stored):
    try:
        kind, rounds, salt, digest = str(stored or "").split("$", 3)
        if kind != "pbkdf2":
            return False
        check = hashlib.pbkdf2_hmac(
            "sha256", str(password).encode("utf-8"), bytes.fromhex(salt), int(rounds),
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(check.hex(), digest)


def _dummy_hash():
    global _DUMMY_HASH
    if _DUMMY_HASH is None:
        _DUMMY_HASH = hash_password("dummy-password")
    return _DUMMY_HASH


def _access_by_contact(conn, contact, domain):
    raw = str(contact or "").strip()
    kind = contact_kind(raw)
    if kind == "phone":
        column, value = "phone", normalize_phone(raw)
    elif kind == "email":
        column, value = "email", normalize_email(raw, domain)
    else:
        return None
    return conn.execute(
        text(
            "SELECT id, role, phone, email, password_hash FROM access_users "
            f"WHERE {column} = :value"
        ),
        {"value": value},
    ).one_or_none()


def match_login(contact, password, domain=""):
    """Телефон или почта + пароль. ID снаружи не нужен."""
    secret = str(password or "")
    row = None
    if secret:
        engine = get_engine()
        try:
            with engine.connect() as conn:
                if inspect(conn).has_table("access_users"):
                    row = _access_by_contact(conn, contact, domain)
        except ValueError:
            row = None
        except Exception as exc:
            if not is_missing_table(exc):
                raise DataError(f"Не удалось прочитать доступ: {public_error(exc)}") from exc
    stored = row.password_hash if row is not None and row.password_hash else _dummy_hash()
    if not secret or not verify_password(secret, stored):
        return None
    if row is None or not row.password_hash:
        return None
    return _access_row(row)


def next_access_id():
    engine = get_engine()
    try:
        with engine.connect() as conn:
            value = conn.execute(text("SELECT COALESCE(MAX(id), 0) + 1 FROM access_users")).scalar()
    except Exception as exc:
        raise DataError(f"Не удалось прочитать доступ: {public_error(exc)}") from exc
    return int(value or 1)


def _value_taken(conn, column, value, user_id):
    row = conn.execute(
        text(f"SELECT id FROM access_users WHERE {column} = :value AND id != :id"),
        {"value": value, "id": int(user_id)},
    ).one_or_none()
    return row is not None


def _require_contacts(phone, email, domain):
    if not str(phone or "").strip() or not str(email or "").strip():
        raise ValueError("Нужны телефон и почта")
    return normalize_phone(phone), normalize_email(email, domain)


def grant_access(role, phone, email, domain=""):
    if role not in ACCESS_ROLES:
        raise ValueError("Выберите права")
    normalized, mail = _require_contacts(phone, email, domain)
    plain = generate_password()
    hashed = hash_password(plain)
    with _write_lock:
        engine = get_engine()
        try:
            with engine.begin() as conn:
                if _value_taken(conn, "phone", normalized, 0):
                    raise ValueError("Номер уже выдан")
                if _value_taken(conn, "email", mail, 0):
                    raise ValueError("Почта уже выдана")
                next_id = int(conn.execute(
                    text("SELECT COALESCE(MAX(id), 0) + 1 FROM access_users")
                ).scalar() or 1)
                conn.execute(
                    text(
                        "INSERT INTO access_users (id, role, phone, email, password_hash) "
                        "VALUES (:id, :role, :phone, :email, :password_hash)"
                    ),
                    {
                        "id": next_id,
                        "role": role,
                        "phone": normalized,
                        "email": mail,
                        "password_hash": hashed,
                    },
                )
        except ValueError:
            raise
        except Exception as exc:
            raise DataError(f"Не удалось выдать доступ: {public_error(exc)}") from exc
    return {
        "id": next_id,
        "role": role,
        "phone": normalized,
        "email": mail,
        "password": plain,
    }


def set_access_password_value(user_id, password):
    plain = str(password or "").strip()
    if len(plain) < 8 or len(plain) > 64:
        raise ValueError("Пароль от 8 до 64 символов")
    hashed = hash_password(plain)
    with _write_lock:
        engine = get_engine()
        try:
            with engine.begin() as conn:
                result = conn.execute(
                    text("UPDATE access_users SET password_hash = :password_hash WHERE id = :id"),
                    {"password_hash": hashed, "id": int(user_id)},
                )
                if not result.rowcount:
                    raise ValueError("Нет такого ID")
        except ValueError:
            raise
        except Exception as exc:
            raise DataError(f"Не удалось записать пароль: {public_error(exc)}") from exc


def set_access_password(user_id):
    plain = generate_password()
    hashed = hash_password(plain)
    with _write_lock:
        engine = get_engine()
        try:
            with engine.begin() as conn:
                result = conn.execute(
                    text("UPDATE access_users SET password_hash = :password_hash WHERE id = :id"),
                    {"password_hash": hashed, "id": int(user_id)},
                )
                if not result.rowcount:
                    raise ValueError("Нет такого ID")
        except ValueError:
            raise
        except Exception as exc:
            raise DataError(f"Не удалось записать пароль: {public_error(exc)}") from exc
    return plain


def set_access_contact(user_id, phone, email, domain=""):
    normalized, mail = _require_contacts(phone, email, domain)
    with _write_lock:
        engine = get_engine()
        try:
            with engine.begin() as conn:
                if _value_taken(conn, "phone", normalized, user_id):
                    raise ValueError("Номер уже выдан")
                if _value_taken(conn, "email", mail, user_id):
                    raise ValueError("Почта уже выдана")
                result = conn.execute(
                    text("UPDATE access_users SET phone = :phone, email = :email WHERE id = :id"),
                    {"phone": normalized, "email": mail, "id": int(user_id)},
                )
                if not result.rowcount:
                    raise ValueError("Нет такого ID")
        except ValueError:
            raise
        except Exception as exc:
            raise DataError(f"Не удалось записать контакт: {public_error(exc)}") from exc
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


def _employee_filter_sql(badge, lr, columns):
    """Сборка ищет сборщика. Пустой assembly_employee_id в базе — старая строка, колонка AC."""
    use_assembly = str(lr) == "5" and "assembly_employee_id" in columns
    if not use_assembly:
        if badge:
            return "AND status_employee_id = :badge"
        return "AND (status_employee_id IS NULL OR status_employee_id = '')"
    if badge:
        return (
            "AND (assembly_employee_id = :badge "
            "OR (assembly_employee_id IS NULL AND status_employee_id = :badge))"
        )
    return (
        "AND (assembly_employee_id = '' "
        "OR (assembly_employee_id IS NULL "
        "AND (status_employee_id IS NULL OR status_employee_id = '')))"
    )


def load_employee_slice(start, end, badge, lr="5"):
    """Строки одного бейджика за окно. В общий кэш кадра не кладётся.

    ready — в таблице уже есть стикер и флаг. Иначе старая заливка, колонки пустые.
    На сборке бейджик — сборщик. На раскладке — сотрудник перед списанием.
    """
    existing = known_columns()
    ready = bool(existing) and "last_sticker" in existing and "posted" in existing
    empty = analytics.prepare_losses(pd.DataFrame())
    if not existing or "loss_date" not in existing or "status_employee_id" not in existing:
        return empty, ready
    cols = [col for col in DETAIL_COLUMNS if col in existing]
    params = {
        "start": start.isoformat(),
        "end": (end + timedelta(days=1)).isoformat(),
        "badge": badge or "",
    }
    emp_sql = _employee_filter_sql(badge, lr, existing)
    sql = text(
        "SELECT " + ", ".join(cols)
        + " FROM losses WHERE loss_date >= :start AND loss_date < :end "
        + emp_sql
    )
    try:
        frame = pd.read_sql(sql, get_engine(), params=params)
    except Exception as exc:
        if is_missing_table(exc):
            return empty, ready
        raise DataError(f"Не удалось получить данные из базы: {public_error(exc)}") from exc
    return analytics.prepare_losses(frame), ready


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


def _period_where():
    if _dialect() == "postgresql":
        return "loss_date::date >= :s AND loss_date::date <= :e"
    return "date(loss_date) >= :s AND date(loss_date) <= :e"


def _delete_sql():
    return "DELETE FROM losses WHERE " + _period_where()


def _carry_mx(conn, df, d_min, d_max):
    """Пустое МХ суперсета не затирает место, которое twin уже записал по тому же ШК."""
    if df is None or len(df) == 0 or "barcode" not in df.columns:
        return df
    if not inspect(conn).has_table("losses"):
        return df
    names = {col["name"] for col in inspect(conn).get_columns("losses")}
    if "barcode" not in names or "mx" not in names:
        return df
    old = pd.read_sql(
        text("SELECT barcode, mx FROM losses WHERE " + _period_where()),
        conn,
        params={"s": d_min.isoformat(), "e": d_max.isoformat()},
    )
    if old.empty:
        return df
    old["key"] = old["barcode"].map(analytics.sticker_text)
    old["place"] = old["mx"].map(analytics.sticker_text)
    old = old.loc[(old["key"] != "") & (old["place"] != "")]
    if old.empty:
        return df
    lookup = dict(zip(old["key"].tolist(), old["place"].tolist()))
    df = df.copy()
    if "mx" not in df.columns:
        df["mx"] = ""
    current = df["mx"].map(analytics.sticker_text)
    filled = df["barcode"].map(analytics.sticker_text).map(lookup).fillna("")
    df["mx"] = [cur or prev for cur, prev in zip(current.tolist(), filled.tolist())]
    return df


def _twin_window(frame):
    """Окно дат только если в twin есть «Дата первого списания». Иначе ШК ищется по всей базе."""
    if frame is None or "loss_date" not in getattr(frame, "columns", []):
        return None, None
    dates = pd.to_datetime(frame["loss_date"], errors="coerce").dropna()
    if dates.empty:
        return None, None
    return dates.min().date(), dates.max().date()


def _assembly_rows(conn, d_min, d_max):
    if not inspect(conn).has_table("losses"):
        return pd.DataFrame(columns=["barcode", "last_sticker", "reason", "status_before"])
    names = {col["name"] for col in inspect(conn).get_columns("losses")}
    if "barcode" not in names:
        return pd.DataFrame(columns=["barcode", "last_sticker", "reason", "status_before"])
    cols = ["barcode"]
    for col in ("last_sticker", "reason", "status_before"):
        if col in names:
            cols.append(col)
    where = []
    params = {}
    if d_min is not None:
        where.append(_period_where())
        params = {"s": d_min.isoformat(), "e": d_max.isoformat()}
    pieces = []
    if "reason" in names:
        pieces.append("reason = '5'")
    if "status_before" in names:
        pieces.append("UPPER(status_before) = 'ORS'")
    if pieces:
        where.append("(" + " OR ".join(pieces) + ")")
    sql = "SELECT " + ", ".join(cols) + " FROM losses"
    if where:
        sql += " WHERE " + " AND ".join(where)
    return pd.read_sql(text(sql), conn, params=params)


def plan_twin(frame):
    """Сколько строк twin находят пропуск сборки по ШК. Стикер не сравнивается. Ничего не пишет."""
    d_min, d_max = _twin_window(frame)
    engine = get_engine()
    with engine.connect() as conn:
        existing = _assembly_rows(conn, d_min, d_max)
    by_barcode = {}
    if len(existing):
        for raw_bar in existing["barcode"].tolist():
            codes = analytics.barcode_forms(raw_bar)
            if not codes:
                continue
            code_set = set(codes)
            for form in codes:
                by_barcode.setdefault(form, set()).update(code_set)
    barcodes = frame["barcode"].tolist() if "barcode" in frame.columns else [""] * len(frame)
    places = frame["mx"].map(analytics.sticker_text).tolist()
    by_code = {}
    matched = 0
    for barcode, place in zip(barcodes, places):
        found = by_barcode.get(analytics.sticker_text(barcode), set()) if barcode else set()
        if not found:
            continue
        matched += 1
        for code in found:
            by_code[code] = place
    return {
        "matched": matched,
        "unmatched": int(len(frame) - matched),
        "updates": [{"barcode": code, "mx": place} for code, place in by_code.items()],
        "updated": 0,
        "start": d_min.isoformat() if d_min else None,
        "end": d_max.isoformat() if d_max else None,
    }


def _mx_patch_rows(frame):
    """Пары ШК → место из twin. Последняя строка файла побеждает, если места разные."""
    if frame is None or len(frame) == 0 or "barcode" not in getattr(frame, "columns", []):
        return []
    if "mx" in frame.columns:
        places = frame["mx"].map(analytics.sticker_text).tolist()
    else:
        places = [""] * len(frame)
    chosen = {}
    for barcode, place in zip(frame["barcode"].tolist(), places):
        if not place:
            continue
        forms = list(analytics.barcode_forms(barcode))
        text_form = analytics.sticker_text(barcode)
        if text_form.isdigit():
            dotted = text_form + ".0"
            if dotted not in forms:
                forms.append(dotted)
        for form in forms:
            if form:
                chosen[form] = place
    return [{"barcode": code, "mx": place} for code, place in chosen.items()]


def _assembly_predicate(names, alias=""):
    """Только пропуск сборки: лостризон 5 или статус ORS. Пусто — колонок нет, фильтр не ставим."""
    prefix = f"{alias}." if alias else ""
    pieces = []
    if "reason" in names:
        pieces.append(f"{prefix}reason IN ('5', '5.0')")
    if "status_before" in names:
        pieces.append(f"UPPER({prefix}status_before) = 'ORS'")
    if not pieces:
        return ""
    return " AND (" + " OR ".join(pieces) + ")"


# Пачка короче лимита параметров Postgres. Маленький список ШК планировщик
# стыкует с losses по индексу, а не полным просмотром таблицы.
TWIN_PATCH_CHUNK = 200


def _barcode_index_state(conn, dialect):
    if dialect == "postgresql":
        row = conn.execute(text(
            "SELECT i.indisvalid FROM pg_class c "
            "JOIN pg_index i ON i.indexrelid = c.oid "
            "JOIN pg_class t ON t.oid = i.indrelid "
            "WHERE c.relname = 'losses_barcode_idx' AND t.relname = 'losses'"
        )).first()
        if row is None:
            return "missing"
        return "valid" if row[0] else "invalid"
    row = conn.execute(text(
        "SELECT 1 FROM sqlite_master WHERE type = 'index' AND name = 'losses_barcode_idx'"
    )).first()
    return "valid" if row else "missing"


def _ensure_barcode_index(engine):
    """Индекс по ШК до записи. На Postgres — CONCURRENTLY, не внутри транзакции сохранения.

    Обычный CREATE INDEX в той же транзакции, что и UPDATE, ждёт каждое чтение отчёта
    и кнопка «Загрузка...» не отпускается.
    """
    dialect = engine.dialect.name
    with engine.connect() as conn:
        if not inspect(conn).has_table("losses"):
            return
        names = {col["name"] for col in inspect(conn).get_columns("losses")}
        if "barcode" not in names:
            return
        state = _barcode_index_state(conn, dialect)
    if state == "valid":
        return
    if dialect == "postgresql":
        with engine.connect() as conn:
            conn.execution_options(isolation_level="AUTOCOMMIT")
            if state == "invalid":
                conn.execute(text("DROP INDEX CONCURRENTLY IF EXISTS losses_barcode_idx"))
            conn.execute(text(
                "CREATE INDEX CONCURRENTLY IF NOT EXISTS losses_barcode_idx ON losses (barcode)"
            ))
        return
    with engine.begin() as conn:
        conn.execute(text("CREATE INDEX IF NOT EXISTS losses_barcode_idx ON losses (barcode)"))


def _patch_update_sql(count, assembly_sql, period_sql):
    parts = ["SELECT :b0 AS barcode, :m0 AS mx"]
    for index in range(1, count):
        parts.append(f"SELECT :b{index}, :m{index}")
    return (
        "UPDATE losses SET mx = p.mx FROM (" + " UNION ALL ".join(parts) + ") AS p "
        "WHERE losses.barcode = p.barcode"
        + assembly_sql
        + period_sql
        + " AND losses.mx IS DISTINCT FROM p.mx"
    )


def apply_twin(frame):
    """Пишет наименование МХ на каждую строку сборки с этим ШК.

    Сборщики не схлопываются: один ШК обновляет все такие потери.
    Пачки идут по индексу ШК. Повтор той же загрузки не переписывает уже стоящее место.
    """
    d_min, d_max = _twin_window(frame)
    rows = _mx_patch_rows(frame)
    plan = {
        "matched": 0,
        "unmatched": 0,
        "updates": rows,
        "updated": 0,
        "start": d_min.isoformat() if d_min else None,
        "end": d_max.isoformat() if d_max else None,
    }
    if not rows:
        return plan
    period_sql = ""
    params = {}
    if d_min is not None:
        period_sql = " AND " + _period_where()
        params = {"s": d_min.isoformat(), "e": d_max.isoformat()}
    engine = get_engine()
    updated = 0
    unknown = False
    with _write_lock:
        _ensure_barcode_index(engine)
        with engine.begin() as conn:
            if not inspect(conn).has_table("losses"):
                return plan
            names = {col["name"] for col in inspect(conn).get_columns("losses")}
            if "barcode" not in names or "mx" not in names:
                return plan
            assembly_sql = _assembly_predicate(names, "losses")
            if engine.dialect.name == "postgresql":
                # Не ждать чужую блокировку бесконечно и не выбирать полный просмотр losses.
                conn.execute(text("SET LOCAL lock_timeout = '30s'"))
                conn.execute(text("SET LOCAL enable_seqscan = off"))
            for offset in range(0, len(rows), TWIN_PATCH_CHUNK):
                batch = rows[offset:offset + TWIN_PATCH_CHUNK]
                bind = dict(params)
                for index, row in enumerate(batch):
                    bind[f"b{index}"] = row["barcode"]
                    bind[f"m{index}"] = row["mx"]
                result = conn.execute(
                    text(_patch_update_sql(len(batch), assembly_sql, period_sql)),
                    bind,
                )
                count = result.rowcount
                if count is None or count < 0:
                    unknown = True
                else:
                    updated += int(count)
        plan["updated"] = updated
        if updated or unknown:
            clear_cache()
    return plan


def replace_period(df, d_min, d_max):
    """Удаляет строки периода и записывает файл вместо них.

    Пустое МХ в новой выгрузке не стирает место, которое twin уже записал по тому же ШК.
    """
    with _write_lock:
        engine = get_engine()
        with engine.begin() as conn:
            if inspect(conn).has_table("losses"):
                df = _carry_mx(conn, df, d_min, d_max)
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
