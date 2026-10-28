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
from fastapi import FastAPI, File, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
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


class TokenIn(BaseModel):
    token: str


class ConfirmIn(BaseModel):
    confirm: bool = False


def fail(status, message):
    return JSONResponse(status_code=status, content={"error": message})


def sweep_pending():
    now = time.time()
    with pending_lock:
        dead = [key for key, item in pending.items() if now - item["ts"] > PENDING_TTL]
        for key in dead:
            pending.pop(key, None)


@asynccontextmanager
async def lifespan(_app):
    try:
        db.ensure_schema()
    except Exception:
        log.exception("Не удалось проверить схему базы при старте")
    yield


app = FastAPI(title="Аналитика списаний", lifespan=lifespan)


@app.get("/api/health")
def health():
    return {"ok": True}


@app.get("/api/bootstrap")
def bootstrap():
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


@app.get("/api/report")
def report(mode: str = "Месяц", anchor: str | None = None):
    if mode not in analytics.MODES:
        return fail(400, "Неизвестный разрез")
    try:
        today = analytics.business_today()
        if anchor:
            anchor_day = date.fromisoformat(anchor)
        else:
            summary = db.get_summary()
            anchor_day = date.fromisoformat(summary["max_date"]) if summary.get("max_date") else today
        period = analytics.compute_period(mode, anchor_day, today)
        current = db.load_cached(period["_start"], period["_end"])
        previous = db.load_cached(period["_prev_start"], period["_prev_end"])
        return analytics.build_report(current, previous, period)
    except (db.DataError, db.ConfigError) as exc:
        return fail(503, str(exc))
    except ValueError:
        return fail(400, "Некорректная дата")
    except Exception as exc:
        log.exception("report")
        return fail(500, db.public_error(exc))


@app.post("/api/admin/cache/clear")
def clear_cache():
    db.clear_cache()
    return {"ok": True, "message": "Кэш сброшен!"}


@app.get("/api/admin/summary")
def admin_summary():
    try:
        return db.get_summary()
    except (db.DataError, db.ConfigError) as exc:
        return fail(503, str(exc))


@app.post("/api/admin/preview")
async def preview(files: list[UploadFile] = File(...)):
    sweep_pending()
    frames = []
    for upload in files:
        name = upload.filename or "файл"
        if not name.lower().endswith(".xlsx"):
            return fail(400, f"Нужен файл .xlsx: {name}")
        try:
            payload = await upload.read()
            frames.append(analytics.process_excel(BytesIO(payload)))
        except Exception as exc:
            return fail(400, f"Не удалось прочитать {name}: {exc}")
    try:
        frame, skipped, dups = analytics.prepare_upload(frames)
    except ValueError as exc:
        return fail(400, str(exc))
    token = None
    if len(frame):
        token = secrets.token_urlsafe(18)
        with pending_lock:
            pending[token] = {"df": frame, "ts": time.time()}
    start = end = None
    if len(frame):
        start = pd.to_datetime(frame["loss_date"]).min().date().isoformat()
        end = pd.to_datetime(frame["loss_date"]).max().date().isoformat()
    return {
        "files": len(files),
        "rows": int(len(frame)),
        "skipped_no_date": int(skipped),
        "duplicates": int(dups),
        "start": start,
        "end": end,
        "token": token,
    }


@app.post("/api/admin/commit")
def commit(body: TokenIn):
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
        return fail(503, str(exc))
    except Exception as exc:
        log.exception("commit")
        with pending_lock:
            pending[body.token] = item
        return fail(500, db.public_error(exc))
    return {"ok": True, "message": "Данные сохранены!", "summary": summary}


@app.post("/api/admin/truncate")
def truncate(body: ConfirmIn):
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


app.mount("/static", StaticFiles(directory=STATIC), name="static")
