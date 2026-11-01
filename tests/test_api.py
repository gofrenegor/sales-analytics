import os
import re
from datetime import date
from io import BytesIO

import pandas as pd
import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'losses.db'}")
    monkeypatch.delenv("APP_TODAY", raising=False)
    import db
    import server

    db.reset_engine()
    server.pending.clear()
    with TestClient(server.app) as test_client:
        logged = test_client.post("/api/auth/login", json={"id": 1})
        assert logged.status_code == 200
        assert logged.json()["role"] == "admin"
        yield test_client
    db.reset_engine()
    server.pending.clear()


def xlsx(rows):
    frame = pd.DataFrame(rows)
    buffer = BytesIO()
    frame.to_excel(buffer, index=False)
    return buffer.getvalue()


MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def loss_row(day, barcode, amount, status="ANV", block="Рязань Тюшевское 1"):
    return {
        "ШК": barcode,
        "Наименование блока": block,
        "ID блока": 6801,
        "ID блока перед списанием": 6801,
        "ID офиса": 301760,
        "Сумма списания": amount,
        "Кол-во лог объектов для списания": 2,
        "Дата первого списания": day,
        "Лостризон первого списания": 1,
        "Статус перед списанием": status,
    }


def test_empty_then_upload_replace_and_wipe(client):
    boot = client.get("/api/bootstrap")
    assert boot.status_code == 200
    body = boot.json()
    assert body["ok"] is True
    assert body["summary"]["total_rows"] == 0
    today = date.fromisoformat(body["today"])
    yesterday = today.fromordinal(today.toordinal() - 1)

    empty = client.get("/api/report", params={"mode": "День", "anchor": today.isoformat()})
    assert empty.json()["empty"] is True

    blob = xlsx([
        loss_row(today, "a", 10),
        loss_row(today, "a", 77),
        loss_row(yesterday, "b", 20),
    ])
    preview = client.post("/api/admin/preview", files=[("files", ("data.xlsx", blob, MIME))])
    assert preview.status_code == 200
    meta = preview.json()
    assert meta["rows"] == 2
    assert meta["duplicates"] == 1
    assert client.get("/api/report", params={"mode": "День", "anchor": today.isoformat()}).json()["empty"] is True

    committed = client.post("/api/admin/commit", json={"token": meta["token"]})
    assert committed.status_code == 200
    assert committed.json()["summary"]["total_rows"] == 2

    day = client.get("/api/report", params={"mode": "День", "anchor": today.isoformat()}).json()
    assert day["empty"] is False
    assert day["dashboard"]["in_sum"] == 77
    earlier = client.get("/api/report", params={"mode": "День", "anchor": yesterday.isoformat()}).json()
    assert earlier["dashboard"]["in_sum"] == 20

    replacement = xlsx([loss_row(today, "c", 99)])
    preview2 = client.post("/api/admin/preview", files=[("files", ("next.xlsx", replacement, MIME))])
    assert client.post("/api/admin/commit", json={"token": preview2.json()["token"]}).status_code == 200
    assert client.get("/api/report", params={"mode": "День", "anchor": today.isoformat()}).json()["dashboard"]["in_sum"] == 99
    assert client.get("/api/report", params={"mode": "День", "anchor": yesterday.isoformat()}).json()["dashboard"]["in_sum"] == 20

    assert client.post("/api/admin/cache/clear").json()["message"] == "Кэш сброшен!"
    assert client.post("/api/admin/truncate", json={"confirm": False}).status_code == 400
    wiped = client.post("/api/admin/truncate", json={"confirm": True})
    assert wiped.status_code == 200
    assert wiped.json()["summary"]["total_rows"] == 0
    assert client.get("/api/report", params={"mode": "День", "anchor": today.isoformat()}).json()["empty"] is True


def test_secrets_file_order(tmp_path, monkeypatch):
    import db

    monkeypatch.setattr(db, "ROOT", tmp_path)
    legacy = tmp_path / ".streamlit"
    legacy.mkdir()
    (legacy / "secrets.toml").write_text(
        '[database]\nconnection_string = "postgresql://legacy"\n',
        encoding="utf-8",
    )
    assert db.read_secrets_url() == "postgresql://legacy"
    (tmp_path / "secrets.toml").write_text(
        '[database]\nconnection_string = "postgresql://root"\n',
        encoding="utf-8",
    )
    assert db.read_secrets_url() == "postgresql://root"


def test_bad_upload_and_mode(client):
    bad = client.post("/api/admin/preview", files=[("files", ("a.xlsx", b"not-a-zip", MIME))])
    assert bad.status_code == 400
    missing = xlsx([{"ШК": "1"}])
    assert client.post("/api/admin/preview", files=[("files", ("a.xlsx", missing, MIME))]).status_code == 400
    assert client.get("/api/report", params={"mode": "Год", "anchor": "2026-10-01"}).status_code == 400
    assert client.post("/api/admin/commit", json={"token": "missing"}).status_code == 404


def test_access_grant_and_view_role(client):
    assert client.post("/api/auth/logout").status_code == 200
    assert client.get("/api/auth/me").json()["ok"] is False
    assert client.get("/api/report", params={"mode": "Месяц"}).status_code == 401
    assert client.post("/api/auth/login", json={"id": 99}).status_code == 401
    assert client.post("/api/auth/login", json={"id": 1}).status_code == 200
    own = client.post("/api/admin/access/contact", json={
        "phone": "8 900 000-00-01", "email": "boss", "domain": "mail.ru",
    })
    assert own.status_code == 200
    assert own.json()["phone"] == "79000000001"
    assert own.json()["email"] == "boss@mail.ru"
    client.post("/api/auth/logout")
    assert client.post("/api/auth/login", json={"id": 1}).status_code == 401
    assert client.post("/api/auth/login", json={"id": 1, "contact": "70000000000"}).status_code == 401
    assert client.post("/api/auth/login", json={"id": 1, "contact": "+7 900 000-00-01"}).status_code == 200
    client.post("/api/auth/logout")
    assert client.post("/api/auth/login", json={
        "id": 1, "contact": "boss", "domain": "gmail.com",
    }).status_code == 401
    assert client.post("/api/auth/login", json={
        "id": 1, "contact": "boss", "domain": "mail.ru",
    }).status_code == 200
    assert client.get("/api/admin/access/next").json()["id"] == 2
    assert client.post("/api/admin/access", json={
        "role": "view", "phone": "89001112233",
    }).status_code == 400
    assert client.post("/api/admin/access", json={
        "role": "root", "phone": "79001112233", "email": "ivan", "domain": "gmail.com",
    }).status_code == 400
    assert client.post("/api/admin/access", json={
        "role": "view", "phone": "89001112233", "email": "ivan", "domain": "yandex.ru",
    }).status_code == 400
    granted = client.post("/api/admin/access", json={
        "role": "view", "phone": "89001112233", "email": "ivan", "domain": "gmail.com",
    })
    assert granted.status_code == 200
    assert granted.json() == {
        "id": 2, "role": "view", "phone": "79001112233", "email": "ivan@gmail.com",
    }
    assert client.post("/api/admin/access", json={
        "role": "view", "phone": "79002223344", "email": "ivan@gmail.com",
    }).status_code == 400
    assert client.get("/api/admin/access/next").json()["id"] == 3
    admin_again = client.post("/api/admin/access", json={
        "role": "admin", "phone": "79002223344", "email": "petr", "domain": "mail.ru",
    })
    assert admin_again.json()["id"] == 3
    assert admin_again.json()["email"] == "petr@mail.ru"
    client.post("/api/auth/logout")
    assert client.post("/api/auth/login", json={"id": 2}).status_code == 401
    assert client.post("/api/auth/login", json={"id": 2, "contact": "79001112233"}).status_code == 200
    client.post("/api/auth/logout")
    assert client.post("/api/auth/login", json={
        "id": 2, "contact": "ivan", "domain": "gmail.com",
    }).status_code == 200
    assert client.get("/api/bootstrap").status_code == 200
    assert client.post("/api/admin/truncate", json={"confirm": True}).status_code == 403
    assert client.post("/api/admin/access", json={"role": "view"}).status_code == 403
    assert client.get("/api/admin/access/next").status_code == 403
    assert client.post("/api/admin/cache/clear").status_code == 403


def test_employee_items_return_json(client):
    today = date.fromisoformat(client.get("/api/bootstrap").json()["today"])
    row = loss_row(today, "sticker-1", 15, status="WMI")
    row["Лостризон первого списания"] = 5
    row["ID сотрудника статуса перед списанием"] = 12345
    row["Последний стикер до списания"] = "*123*"
    row["Оприходование"] = 1
    preview = client.post(
        "/api/admin/preview",
        files=[("files", ("data.xlsx", xlsx([row]), MIME))],
    )
    assert preview.status_code == 200
    assert client.post("/api/admin/commit", json={"token": preview.json()["token"]}).status_code == 200
    params = {"mode": "День", "anchor": today.isoformat(), "badge": "12345", "lr": "5"}
    first = client.get("/api/employees/items", params=params)
    assert first.status_code == 200
    assert first.headers["content-type"].startswith("application/json")
    item = first.json()["items"][0]
    assert item["sticker"] == "*123*"
    assert item["found"] is True
    assert item["sum"] == 15
    cached = client.get(
        "/api/employees/items",
        params=params,
        headers={"Accept-Encoding": "gzip"},
    )
    assert cached.status_code == 200
    assert cached.json()["items"][0]["sticker"] == "*123*"


def test_chunked_preview_over_proxy_limit(client):
    """Целый xlsx больше 1 МБ прокси режет HTML-страницей. Сайт шлёт куски по 256 КБ."""
    today = date.fromisoformat(client.get("/api/bootstrap").json()["today"])
    rows = []
    for i in range(120):
        row = loss_row(today, f"wide-{i}", 10 + i)
        row["хвост"] = os.urandom(12000).hex()
        rows.append(row)
    blob = xlsx(rows)
    assert len(blob) > 1024 * 1024
    chunk = 256 * 1024
    batch = "a" * 16
    upload_id = "b" * 16
    total = (len(blob) + chunk - 1) // chunk
    for index in range(total):
        part = blob[index * chunk:(index + 1) * chunk]
        res = client.post(
            "/api/admin/preview/chunk",
            data={"batch": batch, "upload_id": upload_id, "name": "wide.xlsx", "index": str(index), "total": str(total)},
            files=[("chunk", ("part", part, "application/octet-stream"))],
        )
        assert res.status_code == 200, res.text
        assert res.headers["content-type"].startswith("application/json")
        assert not res.text.lstrip().startswith("<")
    huge = client.post(
        "/api/admin/preview/chunk",
        data={"batch": "c" * 16, "upload_id": "d" * 16, "name": "wide.xlsx", "index": "0", "total": "1"},
        files=[("chunk", ("part", b"x" * (chunk + 1), "application/octet-stream"))],
    )
    assert huge.status_code == 400
    assert huge.json()["error"] == "Слишком большой кусок файла"
    done = client.post("/api/admin/preview/finish", json={"batch": batch, "ids": [upload_id]})
    assert done.status_code == 200, done.text
    assert done.json()["rows"] == 120
    assert done.json()["token"]
    saved = client.post("/api/admin/commit", json={"token": done.json()["token"]})
    assert saved.status_code == 200
    assert saved.json()["summary"]["total_rows"] == 120
    report = client.get("/api/report", params={"mode": "День", "anchor": today.isoformat()}, headers={"Accept-Encoding": "gzip"})
    assert report.status_code == 200
    assert report.headers["content-type"].startswith("application/json")
    assert "content-encoding" not in {k.lower() for k in report.headers}
    assert not report.text.lstrip().startswith("<")
    assert report.json()["empty"] is False


def test_index_injects_asset_version(client):
    html = client.get("/").text
    assert "admin-toggle" in html
    assert "вы не зарегистрированы" in html
    assert "grant-toggle" in html
    assert "gate-domain" in html
    assert "grant-email" in html
    match = re.search(r"/static/app\.js\?v=(\d+)", html)
    assert match
    assert f'/static/styles.css?v={match.group(1)}' in html
