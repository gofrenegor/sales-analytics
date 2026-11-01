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
    own = client.post("/api/admin/access/phone", json={"phone": "8 900 000-00-01"})
    assert own.status_code == 200
    assert own.json()["phone"] == "79000000001"
    client.post("/api/auth/logout")
    assert client.post("/api/auth/login", json={"id": 1, "phone": "70000000000"}).status_code == 401
    assert client.post("/api/auth/login", json={"id": 1, "phone": "+7 900 000-00-01"}).status_code == 200
    assert client.get("/api/admin/access/next").json()["id"] == 2
    assert client.post("/api/admin/access", json={"role": "root", "phone": "79001112233"}).status_code == 400
    assert client.post("/api/admin/access", json={"role": "view", "phone": "123"}).status_code == 400
    granted = client.post("/api/admin/access", json={"role": "view", "phone": "89001112233"})
    assert granted.status_code == 200
    assert granted.json() == {"id": 2, "role": "view", "phone": "79001112233"}
    assert client.post("/api/admin/access", json={"role": "view", "phone": "+7 900 111-22-33"}).status_code == 400
    assert client.get("/api/admin/access/next").json()["id"] == 3
    admin_again = client.post("/api/admin/access", json={"role": "admin", "phone": "79002223344"})
    assert admin_again.json()["id"] == 3
    client.post("/api/auth/logout")
    assert client.post("/api/auth/login", json={"id": 2}).status_code == 401
    assert client.post("/api/auth/login", json={"id": 2, "phone": "79001112233"}).status_code == 200
    assert client.get("/api/bootstrap").status_code == 200
    assert client.post("/api/admin/truncate", json={"confirm": True}).status_code == 403
    assert client.post("/api/admin/access", json={"role": "view"}).status_code == 403
    assert client.get("/api/admin/access/next").status_code == 403
    assert client.post("/api/admin/cache/clear").status_code == 403


def test_index_injects_asset_version(client):
    html = client.get("/").text
    assert "admin-toggle" in html
    assert "вы не зарегистрированы" in html
    assert "grant-toggle" in html
    match = re.search(r"/static/app\.js\?v=(\d+)", html)
    assert match
    assert f'/static/styles.css?v={match.group(1)}' in html
