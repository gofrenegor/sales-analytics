"""Аналитика списаний. Запуск: python -m uvicorn server:app --host 0.0.0.0 --port 8501"""

import logging
import os
import re
import secrets
import threading
import time
from contextlib import asynccontextmanager
from datetime import date
from io import BytesIO
from pathlib import Path

import pandas as pd
from fastapi import Cookie, Depends, FastAPI, File, Form, Query, Request, UploadFile
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

import analytics
import db
from request_metrics import measure, timings

log = logging.getLogger("losses")
ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "static"
PENDING_TTL = 1800
PENDING_MAX_BYTES = 128 * 1024 * 1024
UPLOADS_MAX_BYTES = 80 * 1024 * 1024

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
    kind: str = "superset"


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


class UserPasswordIn(BaseModel):
    user_id: int
    password: str = ""


class PasswordIn(BaseModel):
    password: str = ""


def fail(status, message):
    return JsonBody(status_code=status, content={"error": message})


def _ready_response(body, cache_status):
    """Тело без gzip. Прокси перед сайтом на уже сжатом ответе отдаёт HTML-страницу ошибки."""
    return Response(content=body, media_type="application/json", headers={"X-Cache": cache_status})


def actor(access_session: str | None = Cookie(default=None)):
    try:
        return db.get_session_user(access_session)
    except (db.DataError, db.ConfigError):
        return None


def denied(user, admin=False):
    if not user:
        return fail(401, "вы не зарегистрированы")
    if admin and user.get("role") != "admin":
        return fail(403, "Недостаточно прав")
    return None


def _auth_cookie(response, token, request):
    secure = os.environ.get("AUTH_COOKIE_SECURE", "").lower()
    response.set_cookie(
        "access_session", token,
        httponly=True,
        secure=secure in {"1", "true"} or request.url.scheme == "https",
        samesite="lax", max_age=db.SESSION_TTL, path="/",
    )
    response.delete_cookie("access_id", path="/")
    response.headers["Cache-Control"] = "no-store"
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


def _put_pending(frame, kind, owner):
    size = int(frame.memory_usage(index=True, deep=True).sum())
    token = secrets.token_urlsafe(18)
    with pending_lock:
        if len(pending) >= 8 or sum(item.get("size", 0) for item in pending.values()) + size > PENDING_MAX_BYTES:
            return None
        pending[token] = {"df": frame, "ts": time.time(), "kind": kind,
                          "owner": owner, "size": size}
    return token


def _upload_kind(value):
    kind = str(value or "superset").strip().lower()
    if kind not in {"superset", "twin"}:
        return None
    return kind


def _read_workbooks(blobs, reader):
    frames = []
    for name, payload in blobs:
        name = name or "файл"
        if not str(name).lower().endswith(".xlsx"):
            return fail(400, f"Нужен файл .xlsx: {name}")
        try:
            frames.append(reader(BytesIO(payload)))
        except Exception as exc:
            return fail(400, f"Не удалось прочитать {name}: {exc}")
    return frames


def _date_span(frame):
    dates = pd.to_datetime(frame["loss_date"], errors="coerce")
    if getattr(dates, "ndim", 1) != 1:
        raise ValueError("В файле две колонки «Дата первого списания»")
    dates = dates.dropna()
    if dates.empty:
        raise ValueError("В файле нет дат списания")
    return dates.min().date().isoformat(), dates.max().date().isoformat()


def _preview_from_blobs(blobs, kind="superset", owner=None):
    """Список (имя, байты xlsx) -> JSON превью. Ошибки разбора — 400, не страница прокси."""
    sweep_pending()
    kind = _upload_kind(kind)
    if kind is None:
        return fail(400, "Некорректный тип выгрузки")
    reader = analytics.read_twin_frame if kind == "twin" else analytics.process_excel
    frames = _read_workbooks(blobs, reader)
    if not isinstance(frames, list):
        return frames
    if kind == "twin":
        return _preview_twin(blobs, frames, owner)
    return _preview_superset(blobs, frames, owner)


def _preview_superset(blobs, frames, owner):
    try:
        frame, skipped, dups = analytics.prepare_upload(frames)
    except ValueError as exc:
        return fail(400, str(exc))
    except Exception as exc:
        log.exception("preview")
        return fail(400, f"Не удалось разобрать файл: {exc}")
    token = None
    start = end = None
    mx_rows = 0
    if len(frame):
        try:
            start, end = _date_span(frame)
        except ValueError as exc:
            return fail(400, str(exc))
        except Exception as exc:
            log.exception("preview dates")
            return fail(400, f"Не удалось разобрать даты: {exc}")
        if "mx" in frame.columns:
            mx_rows = int((frame["mx"].map(analytics.sticker_text) != "").sum())
        token = _put_pending(frame, "superset", owner)
        if token is None:
            return fail(503, "Нет места для предпросмотра. Завершите другие загрузки.")
    return {
        "kind": "superset",
        "files": len(blobs),
        "rows": int(len(frame)),
        "skipped_no_date": int(skipped),
        "duplicates": int(dups),
        "mx_rows": mx_rows,
        "start": start,
        "end": end,
        "token": token,
    }


def _preview_twin(blobs, frames, owner):
    try:
        frame, stats = analytics.prepare_twin(frames)
    except ValueError as exc:
        return fail(400, str(exc))
    except Exception as exc:
        log.exception("preview twin")
        return fail(400, f"Не удалось разобрать файл: {exc}")
    token = None
    start = end = None
    matched = unmatched = 0
    if len(frame):
        try:
            plan = db.plan_twin(frame)
        except ValueError as exc:
            return fail(400, str(exc))
        except (db.DataError, db.ConfigError) as exc:
            return fail(503, str(exc))
        except Exception as exc:
            log.exception("preview twin match")
            return fail(400, f"Не удалось сверить twin с базой: {exc}")
        matched = int(plan["matched"])
        unmatched = int(plan["unmatched"])
        start, end = plan["start"], plan["end"]
        if matched:
            token = _put_pending(frame, "twin", owner)
            if token is None:
                return fail(503, "Нет места для предпросмотра. Завершите другие загрузки.")
    return {
        "kind": "twin",
        "files": len(blobs),
        "rows": int(len(frame)),
        "skipped_no_date": int(stats["skipped_no_date"]),
        "skipped_no_barcode": int(stats["skipped_no_barcode"]),
        "skipped_no_sticker": int(stats.get("skipped_no_sticker") or 0),
        "skipped_no_mx": int(stats["skipped_no_mx"]),
        "skipped_not_lr": int(stats["skipped_not_lr"]),
        "duplicates": int(stats["duplicates"]),
        "matched": matched,
        "unmatched": unmatched,
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


@app.middleware("http")
async def request_timing(request: Request, call_next):
    started = time.perf_counter()
    values = {}
    token = timings.set(values)
    try:
        response = await call_next(request)
    finally:
        timings.reset(token)
    elapsed = (time.perf_counter() - started) * 1000
    response.headers["Server-Timing"] = ", ".join(
        [f"app;dur={elapsed:.1f}"] + [f"{name};dur={value:.1f}" for name, value in values.items()]
    )
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
        if elapsed >= 1000:
            log.info("slow request method=%s path=%s status=%s ms=%.1f",
                     request.method, request.url.path, response.status_code, elapsed)
    return response


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
def auth_login(body: LoginIn, request: Request):
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
    try:
        token = db.create_session(user["id"])
    except Exception:
        log.exception("create session")
        return fail(503, "Не удалось создать сессию")
    return _auth_cookie(response, token, request)


@app.post("/api/auth/password")
def auth_password(body: PasswordIn, request: Request, user=Depends(actor)):
    blocked = denied(user)
    if blocked:
        return blocked
    try:
        db.set_access_password_value(user["id"], body.password)
        token = db.create_session(user["id"])
    except ValueError as exc:
        return fail(400, str(exc))
    except (db.DataError, db.ConfigError) as exc:
        return fail(503, str(exc))
    return _auth_cookie(JsonBody({"ok": True}), token, request)


@app.post("/api/auth/logout")
def auth_logout(access_session: str | None = Cookie(default=None)):
    try:
        db.revoke_session(access_session)
    except Exception:
        log.exception("revoke session")
        return fail(503, "Не удалось завершить сессию")
    response = JsonBody({"ok": True})
    response.delete_cookie("access_session", path="/")
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
            "user": {**user, "ok": True},
            "today": today,
            "max_date": summary.get("max_date"),
            "summary": summary,
        }
    except (db.DataError, db.ConfigError) as exc:
        return {"ok": False, "user": {**user, "ok": True}, "error": str(exc),
                "today": today, "max_date": None, "summary": None}
    except Exception as exc:
        log.exception("bootstrap")
        return {
            "ok": False,
            "error": db.public_error(exc),
            "user": {**user, "ok": True},
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


def _cached_json(kind, period, build, extra=()):
    key = db.ready_key(kind, period) + extra
    with db.build_lock(("json", key)):
        return _build_cached_json(key, kind, period, build)


def _build_cached_json(key, kind, period, build):
    cached = db.get_ready(key)
    if cached is not None:
        return _ready_response(cached, "HIT")
    generation = db.cache_generation()
    try:
        payload = build(period)
        with measure("json"):
            body = db.put_ready(key, payload, generation)
    except (db.DataError, db.ConfigError) as exc:
        return fail(503, str(exc))
    except Exception as exc:
        log.exception(kind)
        return fail(500, db.public_error(exc))
    return _ready_response(body, "MISS")


def _report_payload(period):
    current = db.load_cached(period["_start"], period["_end"], "report")
    if period.get("compare") is False:
        previous = pd.DataFrame()
    else:
        previous = db.load_cached(period["_prev_start"], period["_prev_end"], "report")
    with measure("compute"):
        return analytics.build_report(current, previous, period)


def _employees_payload(period):
    return {"employees": db.load_employee_summary(period["_start"], period["_end"])}


def _employees_page_payload(period, lr, page, sort, query):
    all_rows = db.load_employee_summary(period["_start"], period["_end"])[lr]
    rows = [row for row in all_rows if query in row["badge"]] if query else list(all_rows)

    def sort_key(row):
        # Числовые бейджики сравниваются естественно, как в браузере.
        badge = row["badge"]
        tie = (int(badge or "0"), badge)
        if sort == "sum":
            return (-row["sum"], -row["losses"], tie)
        if sort == "found":
            return (row["found"] is None, -(row["found"] or 0), -row["losses"], -row["sum"], tie)
        if sort == "found_share":
            known = row["found"] is not None and row["losses"] > 0
            share = row["found"] / row["losses"] if known else 0
            return (not known, -share, -(row["found"] or 0), -row["losses"], -row["sum"], tie)
        return (-row["losses"], -row["sum"], tie)

    rows.sort(key=sort_key)
    total = len(rows)
    pages = 1 if total <= 25 else 2 if total <= 75 else 2 + (total - 75 + 99) // 100
    page = min(page, pages - 1)
    offset = 0 if page == 0 else 25 if page == 1 else 75 + (page - 2) * 100
    limit = 25 if page == 0 else 50 if page == 1 else 100
    return {
        "rows": rows[offset:offset + limit], "page": page, "pages": pages,
        "total": total, "operation_total": len(all_rows),
        "losses": sum(row["losses"] for row in rows),
        "sum": analytics._f(sum(row["sum"] for row in rows)),
        "badges_missing": bool(all_rows) and all(not row["badge"] for row in all_rows),
    }


def _problem_rows_payload(period):
    current = db.load_cached(period["_start"], period["_end"], "rows")
    existing = db.known_columns() or ()
    return {
        "ready": "mx" in existing,
        "blocks": analytics.build_problem_blocks(current),
    }


def _employee_items_payload(period, badge, lr):
    frame, columns_ready = db.load_employee_slice(period["_start"], period["_end"], badge, lr)
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
    return _cached_json("report-v7", period, _report_payload)


@app.get("/api/employees")
def employees(
    mode: str = "Месяц",
    anchor: str | None = None,
    start: str | None = None,
    end: str | None = None,
    lr: str | None = Query(default=None, pattern="^(5|27)$"),
    page: int = Query(default=0, ge=0, le=1000000),
    sort: str = Query(default="qty", pattern="^(qty|sum|found|found_share)$"),
    q: str = Query(default="", max_length=64, pattern=r"^[0-9]*$"),
    user=Depends(actor),
):
    blocked = denied(user)
    if blocked:
        return blocked
    period, error = _period(mode, anchor, start, end)
    if error is not None:
        return error
    if lr is None:
        return _cached_json("employees-v3", period, _employees_payload)
    return _cached_json(
        "employee-page-v1", period,
        lambda window: _employees_page_payload(window, lr, page, sort, q),
        extra=(lr, page, sort, q),
    )


@app.get("/api/employees/rows")
def employee_rows(
    mode: str = "Месяц",
    anchor: str | None = None,
    start: str | None = None,
    end: str | None = None,
    user=Depends(actor),
):
    """Проблемные ряды сборки: 4 блока, 7 этажей, внутри ряды. Список сотрудников это не читает."""
    blocked = denied(user)
    if blocked:
        return blocked
    period, error = _period(mode, anchor, start, end)
    if error is not None:
        return error
    return _cached_json("employee-rows-v2", period, _problem_rows_payload)


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
    return _cached_json(
        "employee-items-v3", period,
        lambda value: _employee_items_payload(value, badge, lr), (badge, lr),
    )


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
        return db.grant_access(body.role, body.phone, body.email, body.domain, issuer_id=user["id"])
    except ValueError as exc:
        return fail(400, str(exc))
    except (db.DataError, db.ConfigError) as exc:
        return fail(503, str(exc))


def owner_one(user):
    if not user or user.get("id") != 1 or user.get("role") != "admin":
        return fail(403, "Раздел доступен только владельцу")
    return None


@app.get("/api/admin/owner/users")
def owner_users(user=Depends(actor)):
    blocked = owner_one(user)
    if blocked:
        return blocked
    try:
        return {"users": db.list_access_users()}
    except (db.DataError, db.ConfigError) as exc:
        return fail(503, str(exc))


@app.post("/api/admin/owner/password")
def owner_password(body: UserPasswordIn, user=Depends(actor)):
    blocked = owner_one(user)
    if blocked:
        return blocked
    if body.user_id == 1:
        return fail(400, "Пароль владельца меняется в разделе смены пароля")
    try:
        db.set_access_password_value(body.user_id, body.password)
        return {"ok": True, "user_id": body.user_id}
    except ValueError as exc:
        return fail(400, str(exc))
    except (db.DataError, db.ConfigError) as exc:
        return fail(503, str(exc))


@app.post("/api/admin/access/password")
def access_password(body: ContactIn, request: Request, user=Depends(actor)):
    blocked = denied(user, admin=True)
    if blocked:
        return blocked
    try:
        saved = db.set_access_contact(user["id"], body.phone, body.email, body.domain)
        if not saved:
            return fail(404, "Нет такого ID")
        saved["password"] = db.set_access_password(user["id"])
        token = db.create_session(user["id"])
    except ValueError as exc:
        return fail(400, str(exc))
    except (db.DataError, db.ConfigError) as exc:
        return fail(503, str(exc))
    return _auth_cookie(JsonBody(saved), token, request)


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
async def preview(
    files: list[UploadFile] = File(...),
    kind: str = Form("superset"),
    user=Depends(actor),
):
    blocked = denied(user, admin=True)
    if blocked:
        return blocked
    if len(files) > 20:
        return fail(400, "Не больше 20 файлов за одну загрузку")
    blobs = []
    size = 0
    for upload in files:
        data = await upload.read(UPLOAD_MAX_BYTES - size + 1)
        size += len(data)
        if size > UPLOAD_MAX_BYTES:
            return fail(400, "Файлы слишком большие: предел 80 МБ")
        blobs.append((upload.filename or "файл", data))
    return await run_in_threadpool(_preview_from_blobs, blobs, kind, user["id"])


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
    data = await chunk.read(UPLOAD_CHUNK + 1)
    if len(data) > UPLOAD_CHUNK:
        return fail(400, "Слишком большой кусок файла")
    with uploads_lock:
        item = uploads.get(batch)
        if item is None:
            if len(uploads) >= 8:
                return fail(503, "Слишком много одновременных загрузок")
            item = {"ts": time.time(), "files": {}, "bytes": 0, "owner": user["id"]}
            uploads[batch] = item
        if item["owner"] != user["id"]:
            return fail(404, "Загрузка не найдена")
        item["ts"] = time.time()
        slot = item["files"].get(upload_id)
        if slot is None:
            if len(item["files"]) >= 20:
                return fail(400, "Не больше 20 файлов за одну загрузку")
            slot = {"name": name, "total": total, "parts": {}}
            item["files"][upload_id] = slot
        if slot["total"] != total or slot["name"] != name:
            return fail(400, "Куски одного файла не сходятся")
        prev = slot["parts"].get(index)
        next_bytes = item["bytes"] - (len(prev) if prev is not None else 0) + len(data)
        if next_bytes > UPLOAD_MAX_BYTES:
            return fail(400, "Файл слишком большой")
        global_bytes = sum(value["bytes"] for value in uploads.values())
        if global_bytes - item["bytes"] + next_bytes > UPLOADS_MAX_BYTES:
            return fail(503, "Нет места для загрузки. Завершите другие загрузки.")
        slot["parts"][index] = data
        item["bytes"] = next_bytes
    return {"ok": True, "index": index, "total": total}


@app.post("/api/admin/preview/finish")
def preview_finish(body: BatchIn, user=Depends(actor)):
    blocked = denied(user, admin=True)
    if blocked:
        return blocked
    sweep_pending()
    if not _UPLOAD_ID.fullmatch(body.batch) or not body.ids or len(body.ids) > 20:
        return fail(400, "Некорректная загрузка")
    with uploads_lock:
        item = uploads.get(body.batch)
    if item is None or item["owner"] != user["id"]:
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
    result = _preview_from_blobs(blobs, body.kind, user["id"])
    if isinstance(result, dict):
        with uploads_lock:
            uploads.pop(body.batch, None)
    return result


@app.post("/api/admin/commit")
def commit(body: TokenIn, user=Depends(actor)):
    blocked = denied(user, admin=True)
    if blocked:
        return blocked
    sweep_pending()
    with pending_lock:
        item = pending.get(body.token)
        if item is not None and item.get("owner") == user["id"]:
            pending.pop(body.token)
        else:
            item = None
    if item is None:
        return fail(404, "Загрузка устарела. Выберите файлы ещё раз.")
    frame = item["df"]
    kind = item.get("kind") or "superset"
    try:
        if kind == "twin":
            result = db.apply_twin(frame)
            message = f"МХ дописано: {int(result['updated'])}"
        else:
            db.merge_losses(frame)
            message = "Данные сохранены!"
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
    return {"ok": True, "message": message, "summary": summary}


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
                (STATIC / "info-copy.js").stat().st_mtime,
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

