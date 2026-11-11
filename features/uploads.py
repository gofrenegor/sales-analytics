"""Куски xlsx и кадр предпросмотра на диске, общем для воркеров сервиса.

Память процесса остаётся быстрым путём. Диск нужен, когда кусок принял один
воркер, а finish или commit попали на другой. Каталог — частный /tmp сервиса
либо UPLOAD_DIR.
"""

import contextvars
import json
import os
import time
from pathlib import Path

_preview_slot = contextvars.ContextVar("preview_slot", default=None)


def upload_root():
    configured = os.environ.get("UPLOAD_DIR", "").strip()
    path = Path(configured) if configured else Path(os.environ.get("TMPDIR", "/tmp")) / "sales-analytics-uploads"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _pending_path(token, suffix):
    return upload_root() / "pending" / f"{token}{suffix}"


def save_pending(token, frame, kind, owner, size, ts):
    folder = upload_root() / "pending"
    folder.mkdir(parents=True, exist_ok=True)
    data_path = _pending_path(token, ".pkl")
    temporary = data_path.with_suffix(".pkl.tmp")
    frame.to_pickle(temporary)
    os.replace(temporary, data_path)
    _pending_path(token, ".json").write_text(json.dumps({
        "kind": kind,
        "owner": owner,
        "size": int(size),
        "ts": float(ts),
    }, ensure_ascii=False), encoding="utf-8")


def load_pending(token):
    import pandas as pd
    meta_path = _pending_path(token, ".json")
    data_path = _pending_path(token, ".pkl")
    if not meta_path.is_file() or not data_path.is_file():
        return None
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    return {
        "df": pd.read_pickle(data_path),
        "kind": meta.get("kind") or "superset",
        "owner": meta.get("owner"),
        "size": int(meta.get("size") or 0),
        "ts": float(meta.get("ts") or 0),
    }


def delete_pending(token):
    for suffix in (".json", ".pkl", ".pkl.tmp"):
        try:
            _pending_path(token, suffix).unlink()
        except FileNotFoundError:
            pass


def pending_usage():
    folder = upload_root() / "pending"
    if not folder.is_dir():
        return 0, 0
    count = 0
    total = 0
    for path in folder.glob("*.pkl"):
        count += 1
        try:
            total += path.stat().st_size
        except OSError:
            continue
    return count, total


def claim(token):
    folder = upload_root() / "claims"
    folder.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(folder / token, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        return False
    os.close(fd)
    return True


def release_claim(token):
    try:
        (upload_root() / "claims" / token).unlink()
    except FileNotFoundError:
        pass


def _batch_dir(batch):
    return upload_root() / "batches" / batch


def batch_count():
    folder = upload_root() / "batches"
    if not folder.is_dir():
        return 0
    return sum(1 for path in folder.iterdir() if path.is_dir())


def save_part(batch, upload_id, name, index, total, data, owner, ts, batch_bytes):
    folder = _batch_dir(batch)
    parts = folder / upload_id
    parts.mkdir(parents=True, exist_ok=True)
    target = parts / f"{int(index)}.part"
    temporary = parts / f"{int(index)}.part.tmp"
    temporary.write_bytes(data)
    os.replace(temporary, target)
    meta_path = folder / "meta.json"
    meta = {}
    if meta_path.is_file():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            meta = {}
    files = meta.get("files") or {}
    slot = files.get(upload_id) or {"name": name, "total": int(total), "indexes": []}
    indexes = {int(value) for value in slot.get("indexes") or []}
    indexes.add(int(index))
    slot.update(name=name, total=int(total), indexes=sorted(indexes))
    files[upload_id] = slot
    meta.update(owner=owner, ts=float(ts), bytes=int(batch_bytes), files=files)
    meta_path.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")


def load_batch_meta(batch):
    folder = _batch_dir(batch)
    meta_path = folder / "meta.json"
    if not meta_path.is_file():
        return None
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    files = {}
    for upload_id, slot in (meta.get("files") or {}).items():
        files[upload_id] = {
            "name": slot.get("name") or "файл",
            "total": int(slot.get("total") or 0),
            "parts": {},
        }
    return {
        "ts": float(meta.get("ts") or 0),
        "owner": meta.get("owner"),
        "bytes": int(meta.get("bytes") or 0),
        "files": files,
    }


def load_batch(batch):
    folder = _batch_dir(batch)
    meta_path = folder / "meta.json"
    if not meta_path.is_file():
        return None
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    files = {}
    for upload_id, slot in (meta.get("files") or {}).items():
        parts = {}
        for index in slot.get("indexes") or []:
            path = folder / upload_id / f"{int(index)}.part"
            if path.is_file():
                parts[int(index)] = path.read_bytes()
        files[upload_id] = {
            "name": slot.get("name") or "файл",
            "total": int(slot.get("total") or 0),
            "parts": parts,
        }
    return {
        "ts": float(meta.get("ts") or 0),
        "owner": meta.get("owner"),
        "bytes": int(meta.get("bytes") or 0),
        "files": files,
    }


def delete_batch(batch):
    folder = _batch_dir(batch)
    if not folder.exists():
        return
    for path in sorted(folder.rglob("*"), reverse=True):
        if path.is_file():
            path.unlink(missing_ok=True)
        elif path.is_dir():
            try:
                path.rmdir()
            except OSError:
                pass
    try:
        folder.rmdir()
    except OSError:
        pass


def stored_bytes():
    root = upload_root()
    total = 0
    if not root.is_dir():
        return 0
    for path in root.rglob("*"):
        if path.is_file() and path.suffix in {".part", ".pkl"}:
            try:
                total += path.stat().st_size
            except OSError:
                continue
    return total


def sweep(ttl):
    now = time.time()
    root = upload_root()
    pending = root / "pending"
    if pending.is_dir():
        for meta_path in pending.glob("*.json"):
            try:
                ts = float(json.loads(meta_path.read_text(encoding="utf-8")).get("ts") or 0)
            except (OSError, json.JSONDecodeError, TypeError, ValueError):
                ts = 0
            if now - ts > ttl:
                delete_pending(meta_path.stem)
                release_claim(meta_path.stem)
    batches = root / "batches"
    if batches.is_dir():
        for folder in list(batches.iterdir()):
            ts = 0
            meta_path = folder / "meta.json"
            if meta_path.is_file():
                try:
                    ts = float(json.loads(meta_path.read_text(encoding="utf-8")).get("ts") or 0)
                except (OSError, json.JSONDecodeError, TypeError, ValueError):
                    ts = 0
            else:
                try:
                    ts = folder.stat().st_mtime
                except OSError:
                    ts = 0
            if now - ts > ttl:
                delete_batch(folder.name)
    for folder_name in ("claims", "locks"):
        folder = root / folder_name
        if not folder.is_dir():
            continue
        for path in folder.iterdir():
            try:
                if now - path.stat().st_mtime > ttl:
                    path.unlink()
            except OSError:
                continue


def try_preview_lock(limit):
    """Слоты разбора общие для процессов: файл создаётся через O_EXCL."""
    if _preview_slot.get() is not None:
        return True
    folder = upload_root() / "locks"
    try:
        folder.mkdir(parents=True, exist_ok=True)
    except OSError:
        return True
    for index in range(max(1, int(limit))):
        path = folder / f"preview-{index}.slot"
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            continue
        except OSError:
            return True
        os.close(fd)
        _preview_slot.set(path)
        return True
    return False


def release_preview_lock():
    path = _preview_slot.get()
    if path is None:
        return
    _preview_slot.set(None)
    try:
        Path(path).unlink()
    except FileNotFoundError:
        pass
