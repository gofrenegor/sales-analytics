"""Аналитика списаний. Запуск: python -m uvicorn server:app --host 0.0.0.0 --port 8501"""

import logging
import re
import secrets
import threading
import time
from contextlib import asynccontextmanager
from datetime import date
from io import BytesIO
from pathlib import Path

import pandas as pd
from fastapi import Cookie, Depends, FastAPI, File, Form, UploadFile
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import analytics
import db

log = logging.getLogger("losses")
ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "static"
PENDING_TTL = 1800

pending = {}
pending_lock = threading.Lock()
# Кусок меньше типичного лимита прокси (1 МБ). Целый xlsx прокси режет и отдаёт HTML.
UPLOAD_CHUNK = 256 * 1024
UPLOAD_MAX_BYTES = 80 * 1024 * 1024
_UPLOAD_ID = re.compile(r"^[A-Za-z0-9_-]{8,80}$")
uploads = {}
uploads_lock = threading.Lock()


class TokenIn(BaseModel):
    token: str


class BatchIn(BaseModel):
    batch: str
    ids: list[str]


class ConfirmIn(BaseModel):
    confirm: bool = False


class JsonBody(JSONResponse):
    """Русский текст остаётся буквами, без пробелов между полями."""

    def render(self, content):
        import json
        return json.dumps(content, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


class LoginIn(BaseModel):
    contact: str = ""
    phone: str = ""
    domain: str = ""
    password: str = ""


class GrantIn(BaseModel):
    role: str
    phone: str = ""
    email: str = ""
    domain: str = ""


class ContactIn(BaseModel):
    phone: str = ""
    email: str = ""
    domain: str = ""


class PasswordIn(BaseModel):
    password: str = ""


def fail(status, message):
    return JsonBody(status_code=status, content={"error": message})


def _ready_response(body):
    """Тело без gzip. Прокси перед сайтом на уже сжатом ответе отдаёт HTML-страницу ошибки."""
    return Response(content=body, media_type="application/json")


def actor(access_id: str | None = Cookie(default=None)):
    if not access_id or not str(access_id).isdigit():
        return None
    try:
        return db.get_access_user(int(access_id))
    except (db.DataError, db.ConfigError):
        return None


def denied(user, admin=False):
    if not user:
        return fail(401, "вы не зарегистрированы")
    if admin and user.get("role") != "admin":
        return fail(403, "Недостаточно прав")
    return None


def _auth_cookie(response, user_id):
    response.set_cookie(
        "access_id",
        str(user_id),
        httponly=True,
        samesite="lax",
        max_age=60 * 60 * 24 * 365,
        path="/",
    )
    return response


def sweep_pending():
    now = time.time()
    with pending_lock:
        dead = [key for key, item in pending.items() if now - item["ts"] > PENDING_TTL]
        for key in dead:
            pending.pop(key, None)
    with uploads_lock:
        dead = [key for key, item in uploads.items() if now - item["ts"] > PENDING_TTL]
        for key in dead:
            uploads.pop(key, None)


def _preview_from_blobs(blobs):
    """Список (имя, байты xlsx) -> JSON превью. Ошибки разбора — 400, не страница прокси."""
    sweep_pending()
    frames = []
    for name, payload in blobs:
        name = name or "файл"
        if not str(name).lower().endswith(".xlsx"):
            return fail(400, f"Нужен файл .xlsx: {name}")
        try:
            frames.append(analytics.process_excel(BytesIO(payload)))
        except Exception as exc:
            return fail(400, f"Не удалось прочитать {name}: {exc}")
    try:
        frame, skipped, dups = analytics.prepare_upload(frames)
    except ValueError as exc:
        return fail(400, str(exc))
    except Exception as exc:
        log.exception("preview")
        return fail(400, f"Не удалось разобрать файл: {exc}")
    token = None
    if len(frame):
        token = secrets.token_urlsafe(18)
        with pending_lock:
            pending[token] = {"df": frame, "ts": time.time()}
    start = end = None
    if len(frame):
        try:
            dates = pd.to_datetime(frame["loss_date"], errors="coerce")
            if getattr(dates, "ndim", 1) != 1:
                return fail(400, "В файле две колонки «Дата первого списания»")
            dates = dates.dropna()
            if dates.empty:
                return fail(400, "В файле нет дат списания")
            start = dates.min().date().isoformat()
            end = dates.max().date().isoformat()
        except Exception as exc:
            log.exception("preview dates")
            return fail(400, f"Не удалось разобрать даты: {exc}")
    return {
        "files": len(blobs),
        "rows": int(len(frame)),
        "skipped_no_date": int(skipped),
        "duplicates": int(dups),
        "start": start,
        "end": end,
        "token": token,
    }


@asynccontextmanager
async def lifespan(_app):
    try:
        db.ensure_schema()
    except Exception:
        log.exception("Не удалось проверить схему базы при старте")
    yield


app = FastAPI(title="Аналитика списаний", lifespan=lifespan, default_response_class=JsonBody)


@app.get("/api/health")
def health():
    return {"ok": True}


@app.get("/api/auth/me")
def auth_me(user=Depends(actor)):
    if not user:
        return {"ok": False, "id": None, "role": None, "phone": None, "email": None}
    return {
        "ok": True,
        "id": user["id"],
        "role": user["role"],
        "phone": user.get("phone"),
        "email": user.get("email"),
    }


@app.post("/api/auth/login")
def auth_login(body: LoginIn):
    try:
        contact = body.contact or body.phone
        user = db.match_login(contact, body.password, body.domain)
    except (db.DataError, db.ConfigError) as exc:
        return fail(503, str(exc))
    if not user:
        return fail(401, "некорректный логин или пароль")
    response = JsonBody({
        "ok": True,
        "id": user["id"],
        "role": user["role"],
        "phone": user.get("phone"),
        "email": user.get("email"),
    })
    return _auth_cookie(response, user["id"])


@app.post("/api/auth/password")
def auth_password(body: PasswordIn, user=Depends(actor)):
    blocked = denied(user)
    if blocked:
        return blocked
    try:
        db.set_access_password_value(user["id"], body.password)
    except ValueError as exc:
        return fail(400, str(exc))
    except (db.DataError, db.ConfigError) as exc:
        return fail(503, str(exc))
    return {"ok": True}


@app.post("/api/auth/logout")
def auth_logout():
    response = JsonBody({"ok": True})
    response.delete_cookie("access_id", path="/")
    return response


@app.get("/api/bootstrap")
def bootstrap(user=Depends(actor)):
    blocked = denied(user)
    if blocked:
        return blocked
    today = analytics.business_today().isoformat()
    try:
        summary = db.get_summary()
        return {
            "ok": True,
            "error": None,
            "today": today,
            "max_date": summary.get("max_date"),
            "summary": summary,
        }
    except (db.DataError, db.ConfigError) as exc:
        return {"ok": False, "error": str(exc), "today": today, "max_date": None, "summary": None}
    except Exception as exc:
        log.exception("bootstrap")
        return {
            "ok": False,
            "error": db.public_error(exc),
            "today": today,
            "max_date": None,
            "summary": None,
        }


def _period(mode, anchor, start, end):
    """(period, None) либо (None, ответ с ошибкой)."""
    if mode not in analytics.MODES:
        return None, fail(400, "Неизвестный разрез")
    try:
        today = analytics.business_today()
        if mode == "Свободный":
            if not start or not end:
                return None, fail(400, "Укажите начало и конец периода")
            return analytics.compute_free_period(start, end), None
        if anchor:
            anchor_day = date.fromisoformat(anchor)
        else:
            summary = db.get_summary()
            anchor_day = date.fromisoformat(summary["max_date"]) if summary.get("max_date") else today
        return analytics.compute_period(mode, anchor_day, today), None
    except (db.DataError, db.ConfigError) as exc:
        return None, fail(503, str(exc))
    except ValueError as exc:
        msg = str(exc)
        if any(part in msg for part in ("дата", "период", "разрез", "дней")):
            return None, fail(400, msg)
        return None, fail(400, "Некорректная дата")
    except Exception as exc:
        log.exception("period")
        return None, fail(500, db.public_error(exc))


def _cached_json(kind, period, build):
    key = db.ready_key(kind, period)
    cached = db.get_ready(key)
    if cached is not None:
        return _ready_response(cached)
    generation = db.cache_generation()
    try:
        payload = build(period)
        body = db.put_ready(key, payload, generation)
    except (db.DataError, db.ConfigError) as exc:
        return fail(503, str(exc))
    except Exception as exc:
        log.exception(kind)
        return fail(500, db.public_error(exc))
    return _ready_response(body)


def _report_payload(period):
    current = db.load_cached(period["_start"], period["_end"], "report")
    if period.get("compare") is False:
        previous = pd.DataFrame()
    else:
        previous = db.load_cached(period["_prev_start"], period["_prev_end"], "report")
    return analytics.build_report(current, previous, period)


def _employees_payload(period):
    current = db.load_cached(period["_start"], period["_end"], "employees")
    return {"employees": analytics.build_employees(current)}


def _problem_rows_payload(period):
    current = db.load_cached(period["_start"], period["_end"], "rows")
    existing = db.known_columns() or ()
    return {
        "ready": "mx" in existing,
        "rows": analytics.build_problem_rows(current),
    }


def _employee_items_payload(period, badge, lr):
    frame, columns_ready = db.load_employee_slice(period["_start"], period["_end"], badge)
    items = analytics.build_employee_items(frame, badge, lr)
    filled = any(row["sticker"] or row["found"] is not None for row in items)
    return {
        "badge": badge,
        "lr": lr,
        "ready": bool(columns_ready and (not items or filled)),
        "items": items,
    }


@app.get("/api/report")
def report(
    mode: str = "Месяц",
    anchor: str | None = None,
    start: str | None = None,
    end: str | None = None,
    user=Depends(actor),
):
    blocked = denied(user)
    if blocked:
        return blocked
    period, error = _period(mode, anchor, start, end)
    if error is not None:
        return error
    return _cached_json("report-v2", period, _report_payload)


@app.get("/api/employees")
def employees(
    mode: str = "Месяц",
    anchor: str | None = None,
    start: str | None = None,
    end: str | None = None,
    user=Depends(actor),
):
    blocked = denied(user)
    if blocked:
        return blocked
    period, error = _period(mode, anchor, start, end)
    if error is not None:
        return error
    return _cached_json("employees", period, _employees_payload)


@app.get("/api/employees/rows")
def employee_rows(
    mode: str = "Месяц",
    anchor: str | None = None,
    start: str | None = None,
    end: str | None = None,
    user=Depends(actor),
):
    """Проблемные ряды сборки: ряд, число пропусков, сумма. Список сотрудников это не читает."""
    blocked = denied(user)
    if blocked:
        return blocked
    period, error = _period(mode, anchor, start, end)
    if error is not None:
        return error
    return _cached_json("employee-rows", period, _problem_rows_payload)


@app.get("/api/employees/items")
def employee_items(
    mode: str = "Месяц",
    anchor: str | None = None,
    start: str | None = None,
    end: str | None = None,
    badge: str = "",
    lr: str = "5",
    user=Depends(actor),
):
    """Стикеры и флаг оприходования одного бейджика. Список сотрудников это не читает."""
    blocked = denied(user)
    if blocked:
        return blocked
    badge = str(badge or "").strip()
    if not re.fullmatch(r"\d{0,64}", badge):
        return fail(400, "Некорректный бейджик")
    if lr not in analytics.EMPLOYEE_LRS:
        return fail(400, "Некорректный LR")
    period, error = _period(mode, anchor, start, end)
    if error is not None:
        return error
    key = db.ready_key("employee-items", period) + (badge, lr)
    cached = db.get_ready(key)
    if cached is not None:
        return _ready_response(cached)
    generation = db.cache_generation()
    try:
        payload = _employee_items_payload(period, badge, lr)
        body = db.put_ready(key, payload, generation)
    except (db.DataError, db.ConfigError) as exc:
        return fail(503, str(exc))
    except Exception as exc:
        log.exception("employee-items")
        return fail(500, db.public_error(exc))
    return _ready_response(body)


@app.post("/api/admin/cache/clear")
def clear_cache(user=Depends(actor)):
    blocked = denied(user, admin=True)
    if blocked:
        return blocked
    db.clear_cache()
    return {"ok": True, "message": "Кэш сброшен!"}


@app.get("/api/admin/access/next")
def access_next(user=Depends(actor)):
    blocked = denied(user, admin=True)
    if blocked:
        return blocked
    try:
        return {"id": db.next_access_id()}
    except (db.DataError, db.ConfigError) as exc:
        return fail(503, str(exc))


@app.post("/api/admin/access")
def access_grant(body: GrantIn, user=Depends(actor)):
    blocked = denied(user, admin=True)
    if blocked:
        return blocked
    try:
        return db.grant_access(body.role, body.phone, body.email, body.domain)
    except ValueError as exc:
        return fail(400, str(exc))
    except (db.DataError, db.ConfigError) as exc:
        return fail(503, str(exc))


@app.post("/api/admin/access/password")
def access_password(body: ContactIn, user=Depends(actor)):
    blocked = denied(user, admin=True)
    if blocked:
        return blocked
    try:
        saved = db.set_access_contact(user["id"], body.phone, body.email, body.domain)
        if not saved:
            return fail(404, "Нет такого ID")
        saved["password"] = db.set_access_password(user["id"])
    except ValueError as exc:
        return fail(400, str(exc))
    except (db.DataError, db.ConfigError) as exc:
        return fail(503, str(exc))
    return saved


@app.post("/api/admin/access/contact")
def access_contact(body: ContactIn, user=Depends(actor)):
    blocked = denied(user, admin=True)
    if blocked:
        return blocked
    try:
        saved = db.set_access_contact(user["id"], body.phone, body.email, body.domain)
    except ValueError as exc:
        return fail(400, str(exc))
    except (db.DataError, db.ConfigError) as exc:
        return fail(503, str(exc))
    if not saved:
        return fail(404, "Нет такого ID")
    return saved


@app.get("/api/admin/summary")
def admin_summary(user=Depends(actor)):
    blocked = denied(user, admin=True)
    if blocked:
        return blocked
    try:
        return db.get_summary()
    except (db.DataError, db.ConfigError) as exc:
        return fail(503, str(exc))


@app.post("/api/admin/preview")
async def preview(files: list[UploadFile] = File(...), user=Depends(actor)):
    blocked = denied(user, admin=True)
    if blocked:
        return blocked
    blobs = []
    for upload in files:
        blobs.append((upload.filename or "файл", await upload.read()))
    return _preview_from_blobs(blobs)


@app.post("/api/admin/preview/chunk")
async def preview_chunk(
    batch: str = Form(...),
    upload_id: str = Form(...),
    name: str = Form(...),
    index: int = Form(...),
    total: int = Form(...),
    chunk: UploadFile = File(...),
    user=Depends(actor),
):
    """Один кусок xlsx. Прокси перед сайтом на целом файле больше ~1 МБ отдаёт HTML."""
    blocked = denied(user, admin=True)
    if blocked:
        return blocked
    sweep_pending()
    if not _UPLOAD_ID.fullmatch(batch) or not _UPLOAD_ID.fullmatch(upload_id):
        return fail(400, "Некорректная загрузка")
    name = (name or "файл").strip()[:180]
    if not name.lower().endswith(".xlsx"):
        return fail(400, f"Нужен файл .xlsx: {name}")
    if total < 1 or total > 400 or index < 0 or index >= total:
        return fail(400, "Некорректный кусок файла")
    data = await chunk.read()
    if len(data) > UPLOAD_CHUNK:
        return fail(400, "Слишком большой кусок файла")
    with uploads_lock:
        item = uploads.get(batch)
        if item is None:
            item = {"ts": time.time(), "files": {}, "bytes": 0}
            uploads[batch] = item
        item["ts"] = time.time()
        slot = item["files"].get(upload_id)
        if slot is None:
            slot = {"name": name, "total": total, "parts": {}}
            item["files"][upload_id] = slot
        if slot["total"] != total or slot["name"] != name:
            return fail(400, "Куски одного файла не сходятся")
        prev = slot["parts"].get(index)
        next_bytes = item["bytes"] - (len(prev) if prev is not None else 0) + len(data)
        if next_bytes > UPLOAD_MAX_BYTES:
            return fail(400, "Файл слишком большой")
        slot["parts"][index] = data
        item["bytes"] = next_bytes
    return {"ok": True, "index": index, "total": total}


@app.post("/api/admin/preview/finish")
def preview_finish(body: BatchIn, user=Depends(actor)):
    blocked = denied(user, admin=True)
    if blocked:
        return blocked
    if not _UPLOAD_ID.fullmatch(body.batch) or not body.ids or len(body.ids) > 20:
        return fail(400, "Некорректная загрузка")
    with uploads_lock:
        item = uploads.get(body.batch)
    if item is None:
        return fail(404, "Загрузка устарела. Выберите файлы ещё раз.")
    blobs = []
    for upload_id in body.ids:
        if not _UPLOAD_ID.fullmatch(upload_id):
            return fail(400, "Некорректная загрузка")
        slot = item["files"].get(upload_id)
        if slot is None or len(slot["parts"]) != slot["total"]:
            return fail(400, "Не все куски файла дошли")
        if any(i not in slot["parts"] for i in range(slot["total"])):
            return fail(400, "Не все куски файла дошли")
        blobs.append((slot["name"], b"".join(slot["parts"][i] for i in range(slot["total"]))))
    result = _preview_from_blobs(blobs)
    if isinstance(result, dict):
        with uploads_lock:
            uploads.pop(body.batch, None)
    return result


@app.post("/api/admin/commit")
def commit(body: TokenIn, user=Depends(actor)):
    blocked = denied(user, admin=True)
    if blocked:
        return blocked
    with pending_lock:
        item = pending.pop(body.token, None)
    if item is None:
        return fail(404, "Загрузка устарела. Выберите файлы ещё раз.")
    frame = item["df"]
    try:
        d_min = pd.to_datetime(frame["loss_date"]).min().date()
        d_max = pd.to_datetime(frame["loss_date"]).max().date()
        db.replace_period(frame, d_min, d_max)
        summary = db.get_summary()
    except (db.DataError, db.ConfigError) as exc:
        with pending_lock:
            pending[body.token] = item
        return fail(400, str(exc))
    except Exception as exc:
        log.exception("commit")
        with pending_lock:
            pending[body.token] = item
        return fail(400, db.public_error(exc))
    return {"ok": True, "message": "Данные сохранены!", "summary": summary}


@app.post("/api/admin/truncate")
def truncate(body: ConfirmIn, user=Depends(actor)):
    blocked = denied(user, admin=True)
    if blocked:
        return blocked
    if not body.confirm:
        return fail(400, "Подтвердите удаление")
    try:
        db.truncate_losses()
        summary = db.get_summary()
    except (db.DataError, db.ConfigError) as exc:
        return fail(503, str(exc))
    except Exception as exc:
        log.exception("truncate")
        return fail(500, db.public_error(exc))
    return {"ok": True, "message": "База очищена!", "summary": summary}


def _static_asset_stamp() -> str:
    return str(
        int(
            max(
                (STATIC / "app.js").stat().st_mtime,
                (STATIC / "styles.css").stat().st_mtime,
            )
        )
    )


def _index_html() -> str:
    body = (STATIC / "index.html").read_text(encoding="utf-8")
    stamp = _static_asset_stamp()
    return re.sub(r"(\.(?:css|js)\?v=)[^\"']+", rf"\g<1>{stamp}", body)


@app.get("/")
def index():
    return Response(
        content=_index_html(),
        media_type="text/html; charset=utf-8",
        headers={"Cache-Control": "no-store"},
    )


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    return Response(status_code=204)


class CachedStatic(StaticFiles):
    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        if response.status_code != 200:
            return response
        query = scope.get("query_string") or b""
        if b"v=" in query:
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        elif path.endswith(".woff2"):
            response.headers["Cache-Control"] = "public, max-age=2592000"
        return response


app.mount("/static", CachedStatic(directory=STATIC), name="static")
