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
from contextlib import contextmanager
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from pathlib import Path

from cache_store import BoundedCache
from features import cache as shared_cache
from request_metrics import timed

import pandas as pd
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.pool import NullPool

import analytics

log = logging.getLogger("losses")
ROOT = Path(__file__).resolve().parent

_engine = None
_cache = BoundedCache(64, 128 * 1024 * 1024, 3600)
_ready = BoundedCache(256, 32 * 1024 * 1024, 3600)
_summary = BoundedCache(1, 1024 * 1024, 60)
_build_locks = {name: [threading.RLock() for _ in range(64)]
                for name in ("frame", "json", "summary", "employees")}
SESSION_TTL = 60 * 60 * 24 * 30
_columns_cache = None
_generation = 0
_generation_checked = 0.0
_shared_read_failed = False
_cache_lock = threading.Lock()
_write_lock = threading.Lock()
CACHE_TTL = 3600
READ_COLUMNS = (
    "loss_date", "barcode", "block_name", "status_block_id", "loss_sum", "loss_qty",
    "reason", "status_before_date", "status_before", "block_id", "office_id",
    "status_employee_id", "assembly_employee_id", "last_sticker", "posted", "mx",
    "status_after",
)
WRITE_COLUMNS = READ_COLUMNS
# Главный экран не читает ШК и бейджик. Список сотрудников — бейджик, флаг
# оприходования и дата списания (окно LR 37), без стикеров. Стикер и МХ — срез по клику.
# Сборка и раскладка группируются по assembly_employee_id (столбец D).
# NULL — в заливке не было контрагента, колонку AC вместо него не подставлять.
FRAME_COLUMNS = {
    "report": (
        "loss_date", "block_name", "loss_sum", "loss_qty",
        "reason", "status_before", "block_id", "office_id", "posted", "mx",
    ),
    "employees": (
        "loss_date", "block_name", "loss_sum", "reason", "status_before",
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
    "status_after",
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
        # Пул свой у каждого воркера. 4+4 хватает одному процессу и не
        # выбирает max_connections, когда процессов несколько.
        kwargs["pool_size"] = max(1, int(os.environ.get("DB_POOL_SIZE", "4")))
        kwargs["max_overflow"] = max(0, int(os.environ.get("DB_MAX_OVERFLOW", "4")))
        kwargs["pool_timeout"] = float(os.environ.get("DB_POOL_TIMEOUT", "10"))
    _engine = create_engine(url, **kwargs)
    return _engine


def reset_engine():
    global _engine
    if _engine is not None:
        _engine.dispose()
    _engine = None
    clear_cache()


def clear_cache():
    """Сбрасывает память процесса и поколение в базе, общее для всех воркеров."""
    global _columns_cache, _generation, _generation_checked
    remote = None
    try:
        remote = shared_cache.bump_generation()
    except Exception:
        log.warning("shared cache generation was not bumped", exc_info=True)
    with _cache_lock:
        _generation = remote if remote is not None else _generation + 1
        _generation_checked = time.monotonic()
        _cache.clear()
        _ready.clear()
        _summary.clear()
        _columns_cache = None


def cache_generation():
    """Поколение базы. Повтор внутри короткого окна не открывает новое соединение."""
    global _generation, _generation_checked, _columns_cache, _shared_read_failed
    now = time.monotonic()
    with _cache_lock:
        if now - _generation_checked < shared_cache.COALESCE_SECONDS:
            return _generation
    try:
        remote = shared_cache.read_generation()
    except Exception:
        if not _shared_read_failed:
            _shared_read_failed = True
            log.warning("shared cache generation was not read", exc_info=True)
        with _cache_lock:
            return _generation
    _shared_read_failed = False
    with _cache_lock:
        if remote != _generation:
            _generation = remote
            _cache.clear()
            _ready.clear()
            _summary.clear()
            _columns_cache = None
        _generation_checked = time.monotonic()
        return _generation


def shared_build_lock(key):
    return shared_cache.shared_build_lock(key)


@contextmanager
def build_lock(key):
    """Bounded lock stripes prevent duplicate builds without holding the cache lock."""
    namespace = key[0] if isinstance(key, tuple) else "summary"
    locks = _build_locks[namespace]
    with locks[hash(key) % len(locks)]:
        yield


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
    # Report filters by date first; these covering prefixes keep the compact
    # aggregation and employee/problem slices index-backed on large tables.
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS losses_date_reason_idx ON losses (loss_date, reason)"
    ))
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS losses_date_block_idx ON losses (loss_date, block_name)"
    ))
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
        if "issued_by" not in access_cols:
            conn.execute(text("ALTER TABLE access_users ADD COLUMN issued_by INTEGER"))
        if "issued_at" not in access_cols:
            conn.execute(text("ALTER TABLE access_users ADD COLUMN issued_at BIGINT"))
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
        conn.execute(text(
            "CREATE TABLE IF NOT EXISTS access_sessions ("
            "token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL, expires_at BIGINT NOT NULL)"
        ))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS access_sessions_user_idx ON access_sessions (user_id)"
        ))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS access_sessions_expiry_idx ON access_sessions (expires_at)"
        ))
        shared_cache.ensure(conn, engine.dialect.name)
        _ensure_import_tables(conn)
        if not inspect(conn).has_table("losses"):
            return
        existing = {col["name"] for col in inspect(conn).get_columns("losses")}
        for col in (
            "office_id", "block_id", "status_employee_id", "assembly_employee_id",
            "last_sticker", "posted", "mx", "status_after",
        ):
            if col not in existing:
                conn.execute(text(f"ALTER TABLE losses ADD COLUMN {col} TEXT"))
        _ensure_loss_index(conn)


def known_columns():
    """None — таблицы нет. Иначе множество колонок. Повторный показ не ходит в каталог."""
    global _columns_cache
    cache_generation()
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
        "issued_by": getattr(row, "issued_by", None),
        "issued_at": getattr(row, "issued_at", None),
    }


def get_access_user(user_id):
    engine = get_engine()
    try:
        with engine.connect() as conn:
            if not inspect(conn).has_table("access_users"):
                return None
            row = conn.execute(
                text("SELECT id, role, phone, email, issued_by, issued_at FROM access_users WHERE id = :id"),
                {"id": int(user_id)},
            ).one_or_none()
    except Exception as exc:
        if is_missing_table(exc):
            return None
        raise DataError(f"Не удалось прочитать доступ: {public_error(exc)}") from exc
    return _access_row(row)


def _session_hash(token):
    return hashlib.sha256(token.encode("ascii")).hexdigest()


def create_session(user_id):
    token = secrets.token_urlsafe(32)
    now = int(time.time())
    try:
        with get_engine().begin() as conn:
            conn.execute(text("DELETE FROM access_sessions WHERE expires_at <= :now"), {"now": now})
            conn.execute(text(
                "INSERT INTO access_sessions (token_hash, user_id, expires_at) VALUES (:hash, :id, :expiry)"
            ), {"hash": _session_hash(token), "id": int(user_id), "expiry": now + SESSION_TTL})
    except Exception as exc:
        raise DataError("Не удалось создать сессию") from exc
    return token


def get_session_user(token):
    if not re.fullmatch(r"[A-Za-z0-9_-]{43}", str(token or "")):
        return None
    try:
        with get_engine().connect() as conn:
            row = conn.execute(text(
                "SELECT u.id, u.role, u.phone, u.email, u.issued_by, u.issued_at FROM access_sessions s "
                "JOIN access_users u ON u.id = s.user_id "
                "WHERE s.token_hash = :hash AND s.expires_at > :now"
            ), {"hash": _session_hash(token), "now": int(time.time())}).one_or_none()
        return _access_row(row)
    except Exception as exc:
        raise DataError(f"Не удалось проверить сессию: {public_error(exc)}") from exc


def revoke_session(token):
    if not re.fullmatch(r"[A-Za-z0-9_-]{43}", str(token or "")):
        return
    with get_engine().begin() as conn:
        conn.execute(text("DELETE FROM access_sessions WHERE token_hash = :hash"),
                     {"hash": _session_hash(token)})


_PASSWORD_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789"
# New passwords use the current OWASP PBKDF2-HMAC-SHA256 work factor. Existing
# hashes keep their recorded cost and continue to verify during migration.
_PBKDF_ROUNDS = 600_000
_PASSWORD_LENGTH = 16
_MIN_ACCEPTED_PBKDF_ROUNDS = 100_000
_MAX_ACCEPTED_PBKDF_ROUNDS = 2_000_000
_DUMMY_HASH = None


def generate_password():
    return "".join(secrets.choice(_PASSWORD_ALPHABET) for _ in range(_PASSWORD_LENGTH))


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
        rounds = int(rounds)
        if not _MIN_ACCEPTED_PBKDF_ROUNDS <= rounds <= _MAX_ACCEPTED_PBKDF_ROUNDS:
            return False
        check = hashlib.pbkdf2_hmac(
            "sha256", str(password).encode("utf-8"), bytes.fromhex(salt), rounds,
        )
    except (ValueError, TypeError, OverflowError):
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


def list_access_users():
    try:
        with get_engine().connect() as conn:
            rows = conn.execute(text(
                "SELECT u.id, u.role, u.phone, u.email, u.issued_by, u.issued_at, "
                "i.phone AS issuer_phone, i.email AS issuer_email "
                "FROM access_users u LEFT JOIN access_users i ON i.id = u.issued_by ORDER BY u.id"
            )).mappings().all()
    except Exception as exc:
        raise DataError(f"Не удалось прочитать пользователей: {public_error(exc)}") from exc
    return [dict(row) for row in rows]


def grant_access(role, phone, email, domain="", issuer_id=None):
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
                        "INSERT INTO access_users (id, role, phone, email, password_hash, issued_by, issued_at) "
                        "VALUES (:id, :role, :phone, :email, :password_hash, :issued_by, :issued_at)"
                    ),
                    {
                        "id": next_id,
                        "role": role,
                        "phone": normalized,
                        "email": mail,
                        "password_hash": hashed,
                        "issued_by": int(issuer_id) if issuer_id is not None else None,
                        "issued_at": int(time.time()),
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
    if len(plain) < 6 or len(plain) > 64:
        raise ValueError("Пароль от 6 до 64 символов")
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
                conn.execute(text("DELETE FROM access_sessions WHERE user_id = :id"),
                             {"id": int(user_id)})
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
                conn.execute(text("DELETE FROM access_sessions WHERE user_id = :id"),
                             {"id": int(user_id)})
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


@timed("load")
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
    if tuple(columns) == FRAME_COLUMNS["employees"]:
        return analytics.prepare_employee_losses(df)
    return analytics.prepare_losses(df)


def _employee_filter_sql(badge, lr, columns):
    """Сборка и раскладка ищут EMP из столбца D. Колонка AC в выборку не входит."""
    del lr
    if "assembly_employee_id" not in columns:
        return "AND 1 = 0"
    if badge:
        return "AND assembly_employee_id = :badge"
    return "AND (assembly_employee_id IS NULL OR assembly_employee_id = '')"


def load_employee_slice(start, end, badge, lr="5"):
    """Строки одного бейджика за окно. В общий кэш кадра не кладётся.

    ready — в таблице уже есть стикер и флаг. Иначе старая заливка, колонки пустые.
    Бейджик и у сборки, и у раскладки — EMP из столбца D.
    """
    existing = known_columns()
    ready = bool(existing) and "last_sticker" in existing and "posted" in existing
    empty = analytics.prepare_losses(pd.DataFrame())
    if not existing or "loss_date" not in existing or "assembly_employee_id" not in existing:
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
    """Read-only frames, bounded by memory and shared across simultaneous readers."""
    key = (kind, start.isoformat(), end.isoformat())
    with build_lock(("frame", key)):
        generation = cache_generation()
        with _cache_lock:
            hit = _cache.get(key, generation)
        if hit is not None:
            return hit
        df = load_data(start, end, FRAME_COLUMNS[kind])
        size = int(df.memory_usage(index=True, deep=True).sum())
        with _cache_lock:
            if generation == _generation:
                _cache.put(key, df, generation, size)
        return df


def load_employee_summary(start, end):
    """Общий компактный список для страниц, поиска и сортировки; кэш инвалидируется импортом."""
    key = ("employee-summary-v4", start.isoformat(), end.isoformat())
    with build_lock(("employees", key)):
        generation = cache_generation()
        with _cache_lock:
            hit = _cache.get(key, generation)
        if hit is not None:
            return hit
        summary = analytics.build_employees(load_cached(start, end, "employees"))
        # Включает запас под Python-объекты, чтобы соблюдать бюджет памяти кэша.
        size = len(json.dumps(summary, ensure_ascii=False).encode("utf-8")) * 6
        with _cache_lock:
            if generation == _generation:
                _cache.put(key, summary, generation, size)
        return summary


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
    generation = cache_generation()
    with _cache_lock:
        hit = _ready.get(key, generation)
    if hit is not None:
        return hit
    try:
        body = shared_cache.load_ready(key, generation)
    except Exception:
        log.warning("shared ready cache was not read", exc_info=True)
        return None
    if not body:
        return None
    with _cache_lock:
        if _generation == generation:
            _ready.put(key, body, generation, len(body))
    return body


def put_ready(key, payload, generation):
    from fastapi.encoders import jsonable_encoder
    body = json.dumps(
        jsonable_encoder(payload),
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    try:
        shared_cache.store_ready(key, generation, body)
    except Exception:
        log.warning("shared ready cache was not stored", exc_info=True)
    with _cache_lock:
        if generation == _generation:
            _ready.put(key, body, generation, len(body))
    return body


def empty_summary():
    return {"total_rows": 0, "min_date": None, "max_date": None, "months": [], "last_import": None}


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


@timed("summary")
def get_summary():
    with build_lock("summary"):
        generation = cache_generation()
        with _cache_lock:
            hit = _summary.get("summary", generation)
        if hit is not None:
            return hit
        result = _read_summary()
        with _cache_lock:
            if generation == _generation:
                _summary.put("summary", result, generation, len(json.dumps(result).encode("utf-8")))
        return result


def _read_summary():
    engine = get_engine()
    try:
        with engine.connect() as conn:
            if not inspect(conn).has_table("losses"):
                summary = empty_summary()
                summary["last_import"] = _last_import(conn)
                return summary
            total, min_date, max_date = conn.execute(
                text("SELECT COUNT(*), MIN(loss_date), MAX(loss_date) FROM losses")
            ).one()
            if not total:
                summary = empty_summary()
                summary["last_import"] = _last_import(conn)
                return summary
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
            last_import = _last_import(conn)
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
        "last_import": last_import,
    }


def _period_where():
    return "loss_date >= :s AND loss_date < :e"


def _period_params(start, end):
    return {"s": start.isoformat(), "e": (end + timedelta(days=1)).isoformat()}


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
        params=_period_params(d_min, d_max),
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
        params = _period_params(d_min, d_max)
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


def apply_twin(frame, meta=None):
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
        params = _period_params(d_min, d_max)
    engine = get_engine()
    updated = 0
    unknown = False
    snaps = []
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
                if meta is not None:
                    snaps.extend(_twin_snap(conn, batch, assembly_sql, period_sql, params, names))
                result = conn.execute(
                    text(_patch_update_sql(len(batch), assembly_sql, period_sql)),
                    bind,
                )
                count = result.rowcount
                if count is None or count < 0:
                    unknown = True
                else:
                    updated += int(count)
            if meta is not None and snaps:
                _write_import(conn, meta, [("mx", _mx_payload(row)) for row in snaps], len(snaps), *(
                    _frame_span(frame)
                ))
        plan["updated"] = updated
        if updated or unknown:
            clear_cache()
    return plan


def merge_losses(df, meta=None):
    """Новая строка заменяет тот же ШК у тех же сотрудников, остальные остаются.

    Замена и добавление атомарны. Пустое МХ сохраняется из прежней строки по ШК.
    meta запоминает выгрузку, чтобы её можно было снять, пока нет следующей.
    """
    if df is None or df.empty:
        return
    if "barcode" not in df.columns or df["barcode"].map(analytics.sticker_text).eq("").any():
        raise DataError("Для обновления потерь нужна заполненная колонка «ШК» в каждой строке Superset.")
    frame = df.loc[:, [col for col in df.columns if col in WRITE_COLUMNS]].copy()
    frame["barcode"] = frame["barcode"].map(analytics.sticker_text)
    # A barcode can legitimately have separate loss records for different LR
    # reasons. Include reason in the identity so an LR 27 row cannot replace an
    # LR 5 row for the same barcode and employee pair.
    keys = ("barcode", "assembly_employee_id", "status_employee_id", "reason")
    for col in keys[1:]:
        if col not in frame:
            frame[col] = None
    normalized_keys = frame.loc[:, list(keys)].fillna("").astype(str)
    frame = frame.loc[~normalized_keys.duplicated(keep="last")]
    with _write_lock:
        engine = get_engine()
        with engine.begin() as conn:
            if engine.dialect.name == "postgresql":
                # Чужой замок не держит кнопку «Загрузка...». Тяжёлый запрос
                # обрывается, а не занимает единственный воркер до перезапуска.
                conn.execute(text("SET LOCAL lock_timeout = '30s'"))
                conn.execute(text("SET LOCAL statement_timeout = '300s'"))
            if not inspect(conn).has_table("losses"):
                frame.to_sql("losses", conn, if_exists="append", index=False, chunksize=1000)
                if meta is not None:
                    ops = [("drop", _key_payload(dict(zip(keys, key)))) for key in _frame_key_tuples(frame, keys)]
                    d_min, d_max = _frame_span(frame)
                    _write_import(conn, meta, ops, len(frame), d_min, d_max)
            else:
                existing = _add_missing_columns(conn, frame, engine.dialect.name)
                cols = list(frame.columns)
                if "mx" in existing and "mx" not in cols:
                    frame["mx"] = ""
                    cols.append("mx")
                columns_sql = ", ".join(cols)
                stage = "loss_import_" + secrets.token_hex(8)
                conn.execute(text(
                    f"CREATE TEMP TABLE {stage} AS SELECT {columns_sql} FROM losses WHERE 1=0"
                ))
                frame.to_sql(stage, conn, if_exists="append", index=False, chunksize=1000)
                conn.execute(text(f"CREATE INDEX {stage}_barcode_idx ON {stage} (barcode)"))
                if "mx" in cols:
                    _fill_stage_mx(conn, engine.dialect.name, stage)
                matches = f"losses.barcode = {stage}.barcode AND " + " AND ".join(
                    f"COALESCE(CAST(losses.{col} AS TEXT), '') = "
                    f"COALESCE(CAST({stage}.{col} AS TEXT), '')" for col in keys[1:]
                )
                ops = []
                snapshotted = False
                if meta is not None and engine.dialect.name == "postgresql":
                    d_min, d_max = _frame_span(frame)
                    snapshotted = _snapshot_superset_import(
                        conn, meta, stage, matches, cols, len(frame), d_min, d_max,
                    )
                elif meta is not None:
                    ops = _merge_ops(conn, stage, matches, cols, frame, keys)
                if engine.dialect.name == "postgresql":
                    conn.execute(text(f"DELETE FROM losses USING {stage} WHERE {matches}"))
                else:
                    conn.execute(text(
                        f"DELETE FROM losses WHERE barcode IN (SELECT barcode FROM {stage}) "
                        f"AND EXISTS (SELECT 1 FROM {stage} WHERE {matches})"
                    ))
                conn.execute(text(
                    f"INSERT INTO losses ({columns_sql}) SELECT {columns_sql} FROM {stage}"
                ))
                conn.execute(text(f"DROP TABLE {stage}"))
                if ops and not snapshotted:
                    d_min, d_max = _frame_span(frame)
                    _write_import(conn, meta, ops, len(frame), d_min, d_max)
        try:
            with engine.begin() as conn:
                if engine.dialect.name == "postgresql":
                    conn.execute(text("SET LOCAL lock_timeout = '5s'"))
                _ensure_loss_index(conn)
        except Exception:
            log.warning("loss indexes were not refreshed", exc_info=True)
        clear_cache()


def replace_period(df, d_min, d_max):
    """Совместимый вход для старых вызовов: обновление по ключу, без удаления периода."""
    merge_losses(df)


def truncate_losses():
    with _write_lock:
        engine = get_engine()
        with engine.begin() as conn:
            _clear_imports(conn)
            if inspect(conn).has_table("losses"):
                if engine.dialect.name == "postgresql":
                    conn.execute(text("TRUNCATE TABLE losses RESTART IDENTITY"))
                else:
                    conn.execute(text("DELETE FROM losses"))
        clear_cache()


_IMPORT_KEYS = ("barcode", "assembly_employee_id", "status_employee_id", "reason")
_MOSCOW = ZoneInfo("Europe/Moscow")


def _ensure_import_tables(conn):
    conn.execute(text(
        "CREATE TABLE IF NOT EXISTS loss_imports ("
        "id INTEGER PRIMARY KEY, kind TEXT NOT NULL, created_at BIGINT NOT NULL, "
        "actor_id INTEGER, files TEXT NOT NULL, row_count INTEGER NOT NULL, "
        "date_min TEXT, date_max TEXT)"
    ))
    conn.execute(text(
        "CREATE TABLE IF NOT EXISTS loss_import_ops ("
        "import_id INTEGER NOT NULL, op TEXT NOT NULL, payload TEXT NOT NULL)"
    ))
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS loss_import_ops_import_idx ON loss_import_ops (import_id)"
    ))


def _clear_imports(conn):
    if inspect(conn).has_table("loss_import_ops"):
        conn.execute(text("DELETE FROM loss_import_ops"))
    if inspect(conn).has_table("loss_imports"):
        conn.execute(text("DELETE FROM loss_imports"))


def _import_key_text(value):
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except TypeError:
        pass
    text_value = str(value).strip()
    if text_value.lower() in {"nan", "none", "<na>", "nat", "null"}:
        return ""
    return text_value


def _jsonable(value):
    if value is None:
        return None
    if isinstance(value, datetime):
        return None if pd.isna(value) else value.isoformat()
    if isinstance(value, pd.Timestamp):
        return None if pd.isna(value) else value.isoformat()
    try:
        if pd.isna(value):
            return None
    except TypeError:
        pass
    if hasattr(value, "item"):
        try:
            value = value.item()
        except Exception:
            pass
    return value


def _key_payload(record):
    return {col: _import_key_text(record.get(col)) for col in _IMPORT_KEYS}


def _row_payload(record):
    payload = {}
    for col in WRITE_COLUMNS:
        if col in record:
            payload[col] = _jsonable(record.get(col))
    for col in _IMPORT_KEYS:
        payload.setdefault(col, _import_key_text(record.get(col)))
    return payload


def _mx_payload(record):
    payload = _key_payload(record)
    payload["mx"] = _jsonable(record.get("mx"))
    return payload


def _frame_key_tuples(frame, keys):
    columns = []
    for col in keys:
        if col in frame.columns:
            columns.append(frame[col].tolist())
        else:
            columns.append([None] * len(frame))
    return [tuple(_import_key_text(part) for part in parts) for parts in zip(*columns)]


def _frame_span(frame):
    if frame is None or "loss_date" not in getattr(frame, "columns", []):
        return None, None
    dates = pd.to_datetime(frame["loss_date"], errors="coerce").dropna()
    if dates.empty:
        return None, None
    return dates.min().date().isoformat(), dates.max().date().isoformat()


def _file_label(meta):
    names = [str(name).strip() for name in (meta.get("files") or []) if str(name).strip()]
    return (", ".join(names) if names else "файл")[:500]


def _fill_stage_mx(conn, dialect, stage):
    """Пустое МХ суперсета берётся из последней непустой записи того же ШК."""
    if dialect == "postgresql":
        conn.execute(text(
            f"UPDATE {stage} AS s SET mx = src.mx FROM ("
            "SELECT DISTINCT ON (barcode) barcode, mx FROM losses "
            f"WHERE barcode IN (SELECT barcode FROM {stage}) "
            "AND COALESCE(mx, '') <> '' "
            "ORDER BY barcode, loss_date DESC"
            f") AS src WHERE s.barcode = src.barcode AND COALESCE(s.mx, '') = ''"
        ))
        return
    conn.execute(text(
        f"UPDATE {stage} SET mx = COALESCE(("
        f"SELECT losses.mx FROM losses WHERE losses.barcode = {stage}.barcode "
        "AND COALESCE(losses.mx, '') <> '' ORDER BY loss_date DESC LIMIT 1), '') "
        "WHERE COALESCE(mx, '') = ''"
    ))


def _trim_imports(conn):
    keep = [item[0] for item in conn.execute(text(
        "SELECT id FROM loss_imports ORDER BY id DESC LIMIT 30"
    )).all()]
    if not keep:
        return
    marks = ", ".join(f":k{index}" for index in range(len(keep)))
    params = {f"k{index}": value for index, value in enumerate(keep)}
    conn.execute(text(f"DELETE FROM loss_imports WHERE id NOT IN ({marks})"), params)


def _snapshot_superset_import(conn, meta, stage, matches, cols, row_count, date_min, date_max):
    """Откат пишется в Postgres одним JOIN. Строки losses в процесс не забираются.

    Предыдущий откат стирается только если новый снимок не пуст, и только
    в этой же транзакции: ошибка сохранения его не трогает.
    """
    _ensure_import_tables(conn)
    import_id = int(conn.execute(text(
        "SELECT COALESCE(MAX(id), 0) + 1 FROM loss_imports"
    )).scalar() or 1)
    col_sql = ", ".join(f"losses.{col}" for col in cols)
    conn.execute(text(
        "INSERT INTO loss_import_ops (import_id, op, payload) "
        f"SELECT :id, 'put', row_to_json(old)::text FROM ("
        f"SELECT {col_sql} FROM {stage} JOIN losses ON {matches}"
        ") AS old"
    ), {"id": import_id})
    key_bits = ", ".join(
        f"'{col}', COALESCE(CAST({stage}.{col} AS TEXT), '')" for col in _IMPORT_KEYS
    )
    conn.execute(text(
        "INSERT INTO loss_import_ops (import_id, op, payload) "
        f"SELECT :id, 'drop', json_build_object({key_bits})::text "
        f"FROM {stage} WHERE NOT EXISTS (SELECT 1 FROM losses WHERE {matches})"
    ), {"id": import_id})
    count = int(conn.execute(
        text("SELECT COUNT(*) FROM loss_import_ops WHERE import_id = :id"),
        {"id": import_id},
    ).scalar() or 0)
    if not count:
        return False
    conn.execute(text("DELETE FROM loss_import_ops WHERE import_id <> :id"), {"id": import_id})
    conn.execute(text(
        "INSERT INTO loss_imports (id, kind, created_at, actor_id, files, row_count, date_min, date_max) "
        "VALUES (:id, :kind, :created_at, :actor_id, :files, :row_count, :date_min, :date_max)"
    ), {
        "id": import_id,
        "kind": meta.get("kind") or "superset",
        "created_at": int(time.time()),
        "actor_id": meta.get("actor_id"),
        "files": _file_label(meta),
        "row_count": int(row_count),
        "date_min": date_min,
        "date_max": date_max,
    })
    _trim_imports(conn)
    return True


def _write_import(conn, meta, ops, row_count, date_min, date_max):
    if not ops:
        return
    _ensure_import_tables(conn)
    conn.execute(text("DELETE FROM loss_import_ops"))
    import_id = int(conn.execute(text("SELECT COALESCE(MAX(id), 0) + 1 FROM loss_imports")).scalar() or 1)
    conn.execute(text(
        "INSERT INTO loss_imports (id, kind, created_at, actor_id, files, row_count, date_min, date_max) "
        "VALUES (:id, :kind, :created_at, :actor_id, :files, :row_count, :date_min, :date_max)"
    ), {
        "id": import_id,
        "kind": meta.get("kind") or "superset",
        "created_at": int(time.time()),
        "actor_id": meta.get("actor_id"),
        "files": _file_label(meta),
        "row_count": int(row_count),
        "date_min": date_min,
        "date_max": date_max,
    })
    statement = text(
        "INSERT INTO loss_import_ops (import_id, op, payload) VALUES (:import_id, :op, :payload)"
    )
    rows = [
        {"import_id": import_id, "op": op, "payload": json.dumps(payload, ensure_ascii=False)}
        for op, payload in ops
    ]
    for offset in range(0, len(rows), 200):
        conn.execute(statement, rows[offset:offset + 200])
    _trim_imports(conn)


def _last_import(conn):
    if not inspect(conn).has_table("loss_imports"):
        return None
    row = conn.execute(text(
        "SELECT id, kind, created_at, files, row_count, date_min, date_max "
        "FROM loss_imports ORDER BY id DESC LIMIT 1"
    )).mappings().first()
    if not row:
        return None
    undo = conn.execute(
        text("SELECT 1 FROM loss_import_ops WHERE import_id = :id LIMIT 1"),
        {"id": row["id"]},
    ).first()
    created = int(row["created_at"] or 0)
    when = datetime.fromtimestamp(created, _MOSCOW).strftime("%d.%m.%Y %H:%M")
    return {
        "id": int(row["id"]),
        "kind": row["kind"] or "superset",
        "files": row["files"] or "файл",
        "rows": int(row["row_count"] or 0),
        "date_min": row["date_min"],
        "date_max": row["date_max"],
        "when": when,
        "undo": undo is not None,
    }


def _merge_ops(conn, stage, matches, cols, frame, keys):
    col_sql = ", ".join(f"losses.{col}" for col in cols)
    found = pd.read_sql(text(
        f"SELECT {col_sql} FROM losses WHERE barcode IN (SELECT barcode FROM {stage}) "
        f"AND EXISTS (SELECT 1 FROM {stage} WHERE {matches})"
    ), conn)
    old_keys = set()
    ops = []
    if len(found):
        for record in found.to_dict("records"):
            payload = _row_payload(record)
            ops.append(("put", payload))
            old_keys.add(tuple(_import_key_text(payload.get(col)) for col in keys))
    for key in _frame_key_tuples(frame, keys):
        if key not in old_keys:
            ops.append(("drop", _key_payload(dict(zip(keys, key)))))
    return ops


def _twin_snap(conn, batch, assembly_sql, period_sql, params, names):
    binds = {}
    marks = []
    for index, row in enumerate(batch):
        binds[f"snap{index}"] = row["barcode"]
        marks.append(f":snap{index}")
    cols = [col for col in ("barcode", "assembly_employee_id", "status_employee_id", "reason", "mx") if col in names]
    found = pd.read_sql(text(
        "SELECT " + ", ".join(cols) + " FROM losses WHERE barcode IN (" + ", ".join(marks) + ")"
        + assembly_sql + period_sql
    ), conn, params={**params, **binds})
    wanted = {row["barcode"]: row["mx"] for row in batch}
    snaps = []
    if found.empty:
        return snaps
    for record in found.to_dict("records"):
        barcode = record.get("barcode")
        new_mx = wanted.get(barcode)
        if new_mx is None:
            new_mx = wanted.get(_import_key_text(barcode))
        if new_mx is None:
            continue
        if analytics.sticker_text(record.get("mx")) == analytics.sticker_text(new_mx):
            continue
        snaps.append(record)
    return snaps


def _temp_keys(conn, name, rows, columns):
    conn.execute(text(f"DROP TABLE IF EXISTS {name}"))
    definitions = ", ".join(f"{col} TEXT" for col in columns)
    conn.execute(text(f"CREATE TEMP TABLE {name} ({definitions})"))
    if not rows:
        return
    statement = text(
        f"INSERT INTO {name} (" + ", ".join(columns) + ") VALUES ("
        + ", ".join(f":{col}" for col in columns) + ")"
    )
    for offset in range(0, len(rows), 200):
        conn.execute(statement, rows[offset:offset + 200])


def _drop_temp(conn, name):
    conn.execute(text(f"DROP TABLE IF EXISTS {name}"))


def _delete_keys(conn, keys):
    unique = list(dict.fromkeys(keys))
    if not unique:
        return 0
    columns = list(_IMPORT_KEYS)
    rows = [dict(zip(columns, key)) for key in unique]
    _temp_keys(conn, "loss_undo_keys", rows, columns)
    result = conn.execute(text(
        "DELETE FROM losses WHERE EXISTS ("
        "SELECT 1 FROM loss_undo_keys k WHERE "
        "COALESCE(CAST(losses.barcode AS TEXT), '') = k.barcode AND "
        "COALESCE(CAST(losses.assembly_employee_id AS TEXT), '') = k.assembly_employee_id AND "
        "COALESCE(CAST(losses.status_employee_id AS TEXT), '') = k.status_employee_id AND "
        "COALESCE(CAST(losses.reason AS TEXT), '') = k.reason)"
    ))
    _drop_temp(conn, "loss_undo_keys")
    return int(result.rowcount or 0)


def _undo_superset(conn, dialect, ops):
    puts = []
    keys = []
    for op, payload in ops:
        data = json.loads(payload)
        keys.append(tuple(_import_key_text(data.get(col)) for col in _IMPORT_KEYS))
        if op == "put":
            puts.append(data)
    removed = _delete_keys(conn, keys) if inspect(conn).has_table("losses") else 0
    if not puts:
        return removed
    frame = pd.DataFrame(puts)
    for col in ("loss_date", "status_before_date"):
        if col in frame.columns:
            frame[col] = pd.to_datetime(frame[col], errors="coerce")
    keep = [col for col in WRITE_COLUMNS if col in frame.columns]
    frame = frame.loc[:, keep]
    _add_missing_columns(conn, frame, dialect)
    frame.to_sql("losses", conn, if_exists="append", index=False, chunksize=1000)
    return removed


def _undo_mx(conn, ops):
    rows = []
    seen = set()
    for _op, payload in ops:
        data = json.loads(payload)
        key = tuple(_import_key_text(data.get(col)) for col in _IMPORT_KEYS)
        if key in seen:
            continue
        seen.add(key)
        row = dict(zip(_IMPORT_KEYS, key))
        row["mx"] = data.get("mx")
        rows.append(row)
    if not rows or not inspect(conn).has_table("losses"):
        return 0
    columns = list(_IMPORT_KEYS) + ["mx"]
    _temp_keys(conn, "loss_undo_mx", rows, columns)
    result = conn.execute(text(
        "UPDATE losses SET mx = k.mx FROM loss_undo_mx AS k WHERE "
        "COALESCE(CAST(losses.barcode AS TEXT), '') = k.barcode AND "
        "COALESCE(CAST(losses.assembly_employee_id AS TEXT), '') = k.assembly_employee_id AND "
        "COALESCE(CAST(losses.status_employee_id AS TEXT), '') = k.status_employee_id AND "
        "COALESCE(CAST(losses.reason AS TEXT), '') = k.reason"
    ))
    _drop_temp(conn, "loss_undo_mx")
    return int(result.rowcount or 0)


def undo_last_import():
    """Снимает последнюю выгрузку. Новые строки удаляются, заменённые возвращаются.

    У Twin возвращается прежнее МХ. Следующая выгрузка стирает этот откат.
    """
    with _write_lock:
        engine = get_engine()
        with engine.begin() as conn:
            _ensure_import_tables(conn)
            row = conn.execute(text(
                "SELECT id, kind, files FROM loss_imports ORDER BY id DESC LIMIT 1"
            )).mappings().first()
            if not row:
                raise ValueError("Нет выгрузки, которую можно снять")
            stored = conn.execute(
                text("SELECT op, payload FROM loss_import_ops WHERE import_id = :id"),
                {"id": row["id"]},
            ).all()
            if not stored:
                raise ValueError("Эту выгрузку уже нельзя снять")
            ops = [(item[0], item[1]) for item in stored]
            if row["kind"] == "twin":
                _undo_mx(conn, ops)
                message = "МХ последней выгрузки Twin возвращены"
            else:
                _undo_superset(conn, engine.dialect.name, ops)
                message = "Последняя выгрузка снята"
            conn.execute(text("DELETE FROM loss_import_ops WHERE import_id = :id"), {"id": row["id"]})
            conn.execute(text("DELETE FROM loss_imports WHERE id = :id"), {"id": row["id"]})
        clear_cache()
    return {"message": message, "files": row["files"], "kind": row["kind"]}


def _purge_named():
    named = {str(index): name for index, name in enumerate(analytics.BLOCK_RING_BLOCKS, start=1)}
    named["out"] = analytics.OUTBOUND_BLOCK
    return named


def _parse_purge(start, end, blocks):
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(start or "")) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(end or "")):
        raise ValueError("Некорректная дата")
    d_min = datetime.strptime(start, "%Y-%m-%d").date()
    d_max = datetime.strptime(end, "%Y-%m-%d").date()
    if d_max < d_min:
        raise ValueError("Дата «По» раньше даты «С»")
    if (d_max - d_min).days + 1 > analytics.FREE_MAX_DAYS:
        raise ValueError("Период длиннее 366 дней")
    named = _purge_named()
    chosen = []
    for block in blocks or []:
        token = str(block or "").strip()
        if token not in named and token != "other":
            raise ValueError("Неизвестный блок")
        if token not in chosen:
            chosen.append(token)
    if not chosen:
        raise ValueError("Выберите хотя бы один блок")
    return d_min, d_max, chosen, named


def _purge_frame(conn, dialect, d_min, d_max):
    if not inspect(conn).has_table("losses"):
        return pd.DataFrame()
    names = {col["name"] for col in inspect(conn).get_columns("losses")}
    if "loss_date" not in names:
        raise DataError("В таблице losses нет колонки loss_date")
    rid = "ctid::text" if dialect == "postgresql" else "rowid"
    wanted = ["loss_date", "block_name", "loss_sum", "reason", "status_before", "block_id", "office_id"]
    cols = [col for col in wanted if col in names]
    sql = "SELECT " + rid + " AS _rid, " + ", ".join(cols) + " FROM losses WHERE " + _period_where()
    return pd.read_sql(text(sql), conn, params=_period_params(d_min, d_max))


def _purge_mask(frame, chosen, named):
    groups = {named[block] for block in chosen if block in named}
    other = "other" in chosen
    prepared = analytics.prepare_losses(frame.drop(columns=["_rid"], errors="ignore"))
    mask = prepared["block_group"].isin(groups) if groups else pd.Series(False, index=prepared.index)
    if other:
        mask = mask | ~prepared["block_group"].isin(set(named.values()))
    return mask.fillna(False).to_numpy()


def purge_period(start, end, blocks, confirm=False):
    """Считает или удаляет строки дней в выбранных блоках. confirm пишет удаление."""
    d_min, d_max, chosen, named = _parse_purge(start, end, blocks)
    engine = get_engine()
    if not confirm:
        with engine.connect() as conn:
            frame = _purge_frame(conn, engine.dialect.name, d_min, d_max)
        if frame.empty:
            return {"rows": 0, "sum": 0}
        mask = _purge_mask(frame, chosen, named)
        if len(mask) != len(frame):
            raise DataError("Не удалось сопоставить блоки")
        part = frame[mask]
        total = float(pd.to_numeric(part["loss_sum"], errors="coerce").fillna(0).sum()) if "loss_sum" in part.columns else 0.0
        return {"rows": int(mask.sum()), "sum": total}
    with _write_lock:
        with engine.begin() as conn:
            frame = _purge_frame(conn, engine.dialect.name, d_min, d_max)
            if frame.empty:
                removed = 0
            else:
                mask = _purge_mask(frame, chosen, named)
                if len(mask) != len(frame):
                    raise DataError("Не удалось сопоставить блоки")
                rids = frame[mask]["_rid"].tolist()
                removed = _delete_rids(conn, engine.dialect.name, rids)
                if removed:
                    if inspect(conn).has_table("loss_import_ops"):
                        conn.execute(text("DELETE FROM loss_import_ops"))
        clear_cache()
    return {"rows": int(removed), "sum": None}


def _delete_rids(conn, dialect, rids):
    if not rids:
        return 0
    column = "ctid::text" if dialect == "postgresql" else "rowid"
    removed = 0
    for offset in range(0, len(rids), 400):
        chunk = rids[offset:offset + 400]
        clause = ", ".join(f":r{index}" for index in range(len(chunk)))
        params = {}
        for index, rid in enumerate(chunk):
            params[f"r{index}"] = rid if dialect == "postgresql" else int(rid)
        result = conn.execute(text(f"DELETE FROM losses WHERE {column} IN ({clause})"), params)
        removed += int(result.rowcount or 0)
    return removed

