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


def test_bad_upload_and_mode(client):
    bad = client.post("/api/admin/preview", files=[("files", ("a.xlsx", b"not-a-zip", MIME))])
    assert bad.status_code == 400
    missing = xlsx([{"ШК": "1"}])
    assert client.post("/api/admin/preview", files=[("files", ("a.xlsx", missing, MIME))]).status_code == 400
    assert client.get("/api/report", params={"mode": "Год", "anchor": "2026-10-01"}).status_code == 400
    assert client.post("/api/admin/commit", json={"token": "missing"}).status_code == 404
