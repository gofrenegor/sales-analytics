from datetime import date

import pandas as pd
import pytest

from analytics import (
    ALL_BLOCKS,
    DEFECT_GROUP,
    DEFECT_STATUSES,
    FOREIGN_GROUP,
    build_loss_donut_svg,
    build_report,
    compute_period,
    daily_series,
    fmt_rub,
    get_delta_text,
    prepare_losses,
    prepare_upload,
)


def row(**overrides):
    base = {
        "loss_date": pd.Timestamp("2026-10-10"),
        "barcode": "1",
        "block_name": "Рязань Тюшевское 1",
        "status_block_id": "6801",
        "loss_sum": 100,
        "loss_qty": 1,
        "reason": "1",
        "status_before_date": pd.Timestamp("2026-10-10"),
        "status_before": "ANV",
        "block_id": "6801",
        "office_id": "301760",
    }
    base.update(overrides)
    return base


@pytest.mark.parametrize(
    "overrides,flow,group",
    [
        ({}, "Входящий поток", "Рязань Тюшевское 1"),
        ({"status_before": "smc", "status_block_id": "6800.0"}, "Исходящий поток", "Опер площадь"),
        ({"status_before": "SDL"}, "Входящий поток", DEFECT_GROUP),
        (
            {"status_before": "CTS", "reason": "5", "status_block_id": "6800"},
            "Исходящий поток",
            "Рязань Тюшевское 1",
        ),
        ({"block_id": "100"}, "Входящий поток", FOREIGN_GROUP),
        ({"office_id": "999"}, "Входящий поток", FOREIGN_GROUP),
        ({"office_id": "301760.0"}, "Входящий поток", "Рязань Тюшевское 1"),
        ({"block_name": "Склад Х", "block_id": "6810"}, "Входящий поток", "Блоки вне маппинга"),
        (
            {"block_name": "Склад Х", "block_id": None, "office_id": None},
            "Входящий поток",
            FOREIGN_GROUP,
        ),
        ({"status_before": "ZZZ"}, "Другое", "Рязань Тюшевское 1"),
        (
            {"reason": "5.0", "status_before": "SDL", "status_block_id": "6800"},
            "Исходящий поток",
            "Рязань Тюшевское 1",
        ),
    ],
)
def test_classify(overrides, flow, group):
    frame = prepare_losses(pd.DataFrame([row(**overrides)]))
    assert frame.loc[0, "flow"] == flow
    assert frame.loc[0, "block_group"] == group


def test_operka_stage():
    frame = prepare_losses(pd.DataFrame([row(status_before="SMC", status_block_id="6800")]))
    assert frame.loc[0, "process_stage"] == "Сортировка"
    assert frame.loc[0, "block_group"] == "Опер площадь"


def test_periods():
    today = date(2026, 10, 28)
    month = compute_period("Месяц", date(2026, 10, 15), today)
    assert (month["start"], month["end"], month["prev_start"], month["prev_end"]) == (
        "2026-10-01", "2026-10-28", "2026-09-01", "2026-09-28",
    )
    past = compute_period("Месяц", date(2026, 9, 15), today)
    assert (past["start"], past["end"], past["prev_start"], past["prev_end"]) == (
        "2026-09-01", "2026-09-30", "2026-08-01", "2026-08-30",
    )
    week = compute_period("Неделя", date(2026, 10, 15), today)
    assert (week["start"], week["end"], week["prev_start"], week["prev_end"]) == (
        "2026-10-12", "2026-10-18", "2026-10-05", "2026-10-11",
    )
    current = compute_period("Неделя", today, today)
    assert (current["start"], current["end"], current["prev_start"], current["prev_end"]) == (
        "2026-10-26", "2026-10-28", "2026-10-19", "2026-10-21",
    )
    day = compute_period("День", today, today)
    assert (day["start"], day["prev_start"], day["days"]) == ("2026-10-28", "2026-10-27", 1)
    future = compute_period("Месяц", date(2026, 12, 1), today)
    assert future["start"] == future["end"] == "2026-12-01"
    assert future["days"] == 1


def test_delta_and_donut():
    assert get_delta_text(80, 2, 100, 4) == "-20 ₽ (-20.0%) / -2 шт"
    assert get_delta_text(120, 1, 100, 1) == "+20 ₽ (+20.0%) / 0 шт"
    svg = build_loss_donut_svg(1500, 3500, fmt_rub(5000))
    assert 'data-flow="in"' in svg
    assert 'data-flow="out"' in svg
    assert "5 000 ₽" in svg
    assert "30.0%" in svg
    assert "70.0%" in svg
    assert 'data-in-pct="30.000000"' in svg
    assert 'data-out-pct="70.000000"' in svg
    assert 'data-gap="5.0"' in svg
    assert 'fill="var(--green)"' in svg
    assert 'fill="var(--blue)"' in svg
    assert 'class="loss-arc-cover"' in svg
    assert svg.find("</svg>") < svg.find('class="loss-caption"')
    assert 'class="loss-caption"' in svg
    assert ">Полная сумма</span>" in svg
    assert ">списаний</span>" in svg
    solo = build_loss_donut_svg(5000, 0, fmt_rub(5000))
    assert 'data-gap="0.0"' in solo
    assert 'data-in-pct="100.000000"' in solo
    assert 'data-out-pct="0.000000"' in solo
    assert 'd="M 63.00 200.00 A 137.00 137.00 0 1 1 337.00 200.00' in solo


def test_structure_and_quality():
    today = date(2026, 10, 28)
    period = compute_period("День", date(2026, 10, 10), today)
    current = prepare_losses(pd.DataFrame([
        row(barcode="a", loss_sum=150, loss_qty=2),
        row(barcode="b", block_name="Склад Х", block_id="6810", loss_sum=7, loss_qty=1),
        row(barcode="c", status_before="ZZZ", loss_sum=5, loss_qty=1, reason="8"),
        row(barcode="d", status_before="ANV", status_block_id="6800", loss_sum=9, loss_qty=1),
        row(barcode="e", status_before="SMC", status_block_id="6800", reason="3", loss_sum=50, loss_qty=4),
    ]))
    previous = prepare_losses(pd.DataFrame([
        row(barcode="p", loss_sum=100, loss_qty=1, loss_date=pd.Timestamp("2026-10-09")),
    ]))
    report = build_report(current, previous, period)
    assert report["empty"] is False
    assert report["has_prev"] is True
    out_names = [card["name"] for card in report["flows"]["out"]["cards"]]
    assert "Опер площадь" in out_names
    assert DEFECT_GROUP not in out_names
    inbound_names = [card["name"] for card in report["flows"]["in"]["cards"]]
    assert inbound_names[0] == "Рязань Тюшевское 1"
    assert "Блоки вне маппинга" in inbound_names
    inbound = {card["name"]: card for card in report["flows"]["in"]["cards"]}
    grown = inbound["Рязань Тюшевское 1"]
    assert grown["color"] == "red"
    assert grown["chip"]["text"].startswith("▲")
    defect = inbound[DEFECT_GROUP]
    assert [card["name"] for card in defect["group_view"]["sections"][0]["cards"]] == DEFECT_STATUSES
    quality = report["quality"]
    assert quality["all_clear"] is False
    assert quality["operka_lrs"][0]["reason"] == "3"
    assert quality["unmapped"][0]["name"] == "Склад Х"
    assert quality["other_rows"][0]["status"] == "ZZZ"
    assert quality["block_6800"]["count"] == 1
    assert report["dashboard"]["delta_text"].startswith("+")
    platform = grown["group_view"]["sections"][0]["cards"]
    home = next(card for card in platform if card["name"] == "Рязань Тюшевское 1")
    assert home["detail"]["rows"]
    assert home["detail"]["delta_text"]


def test_other_only_keeps_empty_structure():
    period = compute_period("День", date(2026, 10, 10), date(2026, 10, 28))
    current = prepare_losses(pd.DataFrame([row(status_before="ZZZ", loss_sum=5)]))
    report = build_report(current, prepare_losses(pd.DataFrame()), period)
    assert report["empty"] is False
    assert [card["name"] for card in report["flows"]["in"]["cards"]] == ALL_BLOCKS + [DEFECT_GROUP, FOREIGN_GROUP]
    assert [card["name"] for card in report["flows"]["out"]["cards"]] == ALL_BLOCKS + ["Опер площадь", FOREIGN_GROUP]
    assert report["dashboard"]["in_sum"] == 0
    assert report["dashboard"]["other"]["sum"] == 5
    assert report["dashboard"]["other"]["show"] is True


def test_daily_tops():
    day = pd.Timestamp("2026-10-12")
    frame = pd.DataFrame([
        {"loss_date": day, "block_group": "A", "reason": "1", "loss_sum": 40, "loss_qty": 1},
        {"loss_date": day, "block_group": "A", "reason": "2", "loss_sum": 20, "loss_qty": 1},
        {"loss_date": day, "block_group": "B", "reason": "9", "loss_sum": 50, "loss_qty": 1},
    ])
    out = daily_series(frame, date(2026, 10, 12), date(2026, 10, 14), "Текущий период")
    assert len(out) == 3
    assert out[0]["top_block"].startswith("A ·")
    assert "LR 9" in out[0]["top_lr"]
    assert "LR 1" in out[0]["top_block_lr"]
    assert out[1]["sum"] == 0
    assert out[1]["top_lr"] == "—"


def test_prepare_upload_dedup():
    frame = pd.DataFrame([
        {"loss_date": pd.Timestamp("2026-10-01"), "barcode": "1", "loss_sum": 10},
        {"loss_date": pd.Timestamp("2026-10-02"), "barcode": "1", "loss_sum": 77},
        {"loss_date": pd.NaT, "barcode": "2", "loss_sum": 5},
    ])
    out, skipped, dups = prepare_upload([frame])
    assert skipped == 1
    assert dups == 1
    assert len(out) == 1
    assert out.iloc[0]["loss_sum"] == 77
