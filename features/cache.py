"""Поколение кэша и готовый JSON, общие для воркеров одного сервера.

Кадры pandas остаются в памяти процесса. Готовый ответ лежит в базе, поэтому
второй воркер не считает тот же период заново. Импорт увеличивает поколение,
и чужой процесс перестаёт отдавать старый JSON.
"""

import hashlib
import json
import logging
import time
from contextlib import contextmanager

from sqlalchemy import text

log = logging.getLogger("losses")

# Пачка вызовов внутри одного расчёта не открывает соединение на каждый шаг.
COALESCE_SECONDS = 0.05


def ready_id(key):
    return json.dumps(key, ensure_ascii=False, separators=(",", ":"), default=_json_default)


def _json_default(value):
    if isinstance(value, tuple):
        return list(value)
    raise TypeError(type(value).__name__)


def ensure(conn, dialect):
    blob = "BYTEA" if dialect == "postgresql" else "BLOB"
    conn.execute(text(
        "CREATE TABLE IF NOT EXISTS app_state ("
        "id INTEGER PRIMARY KEY, cache_generation BIGINT NOT NULL)"
    ))
    conn.execute(text(
        "INSERT INTO app_state (id, cache_generation) "
        "SELECT 1, 1 WHERE NOT EXISTS (SELECT 1 FROM app_state WHERE id = 1)"
    ))
    conn.execute(text(
        "CREATE TABLE IF NOT EXISTS ready_cache ("
        "cache_key TEXT PRIMARY KEY, generation BIGINT NOT NULL, "
        f"body {blob} NOT NULL, created_at BIGINT NOT NULL)"
    ))


def _db():
    import db
    return db


def read_generation():
    database = _db()
    engine = database.get_engine()
    with engine.connect() as conn:
        value = conn.execute(text(
            "SELECT cache_generation FROM app_state WHERE id = 1"
        )).scalar()
    return int(value or 1)


def bump_generation():
    database = _db()
    engine = database.get_engine()
    with engine.begin() as conn:
        ensure(conn, engine.dialect.name)
        conn.execute(text(
            "UPDATE app_state SET cache_generation = cache_generation + 1 WHERE id = 1"
        ))
        conn.execute(text("DELETE FROM ready_cache"))
        value = conn.execute(text(
            "SELECT cache_generation FROM app_state WHERE id = 1"
        )).scalar()
    return int(value or 1)


def load_ready(key, generation):
    database = _db()
    engine = database.get_engine()
    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT body FROM ready_cache "
                "WHERE cache_key = :key AND generation = :generation"
            ),
            {"key": ready_id(key), "generation": int(generation)},
        ).first()
    if row is None or row[0] is None:
        return None
    body = row[0]
    if isinstance(body, memoryview):
        return body.tobytes()
    if isinstance(body, str):
        return body.encode("utf-8")
    return bytes(body)


def store_ready(key, generation, body):
    if len(body) > 32 * 1024 * 1024:
        return False
    database = _db()
    engine = database.get_engine()
    with engine.begin() as conn:
        current = conn.execute(text(
            "SELECT cache_generation FROM app_state WHERE id = 1"
        )).scalar()
        if int(current or 0) != int(generation):
            return False
        conn.execute(text(
            "INSERT INTO ready_cache (cache_key, generation, body, created_at) "
            "VALUES (:key, :generation, :body, :created) "
            "ON CONFLICT (cache_key) DO UPDATE SET "
            "generation = excluded.generation, body = excluded.body, created_at = excluded.created_at"
        ), {
            "key": ready_id(key),
            "generation": int(generation),
            "body": body,
            "created": int(time.time()),
        })
    return True


@contextmanager
def shared_build_lock(key):
    """Один расчёт ключа на все процессы Postgres. SQLite остаётся на локальном замке."""
    database = _db()
    try:
        if database._dialect() != "postgresql":
            yield
            return
        engine = database.get_engine()
    except Exception:
        log.warning("shared build lock unavailable", exc_info=True)
        yield
        return
    digest = hashlib.sha256(ready_id(key).encode("utf-8")).digest()
    first = int.from_bytes(digest[:4], "big", signed=True)
    second = int.from_bytes(digest[4:8], "big", signed=True)
    conn = engine.connect()
    locked = False
    try:
        conn.execute(
            text("SELECT pg_advisory_lock(:first, :second)"),
            {"first": first, "second": second},
        )
        conn.commit()
        locked = True
        yield
    finally:
        try:
            if locked:
                conn.execute(
                    text("SELECT pg_advisory_unlock(:first, :second)"),
                    {"first": first, "second": second},
                )
                conn.commit()
        finally:
            conn.close()
