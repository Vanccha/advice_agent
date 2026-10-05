import datetime as dt

from core_common.tr import format_count_tr, format_date_tr, format_money_try


def test_format_money_try_thousands():
    assert format_money_try(1234.5) == "1.234,50 TL"


def test_format_money_try_small_amount():
    assert format_money_try(50) == "50,00 TL"


def test_format_money_try_negative():
    assert format_money_try(-12.3) == "-12,30 TL"


def test_format_date_tr():
    assert format_date_tr(dt.date(2026, 10, 5)) == "5 Ekim 2026"


def test_format_count_tr_no_pluralization():
    assert format_count_tr(3, "cihaz") == "3 cihaz"
    assert format_count_tr(1, "cihaz") == "1 cihaz"
