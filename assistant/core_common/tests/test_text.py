import datetime as dt

from core_common.text import format_count, format_date, format_money


def test_format_money_thousands():
    assert format_money(1234.5) == "£1,234.50"


def test_format_money_small_amount():
    assert format_money(5) == "£5.00"


def test_format_money_negative():
    assert format_money(-12.3) == "-£12.30"


def test_format_date():
    assert format_date(dt.date(2026, 10, 5)) == "5 October 2026"


def test_format_count_pluralizes():
    assert format_count(3, "device") == "3 devices"
    assert format_count(1, "device") == "1 device"
    assert format_count(2, "person", "people") == "2 people"
