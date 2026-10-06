from privacy.detectors import (
    find_card_numbers,
    find_emails,
    find_full_names,
    find_ibans,
    find_national_ids,
    find_phones,
    luhn_is_valid,
)


def test_luhn_valid_and_invalid():
    assert luhn_is_valid("4111111111111111") is True
    assert luhn_is_valid("1234567890123456") is False


def test_find_national_ids():
    spans = find_national_ids("Customer NI number: QQ123456C on file.")
    assert [v for _, _, v in spans] == ["QQ123456C"]


def test_find_national_ids_spaced_and_ignores_lowercase_words():
    spans = find_national_ids("NI: QQ 12 34 56 C, booked at 10 30 00 a.m.")
    assert [v for _, _, v in spans] == ["QQ 12 34 56 C"]


def test_find_phones_plus44_and_local_format():
    spans = find_phones("Phone: +447700900123 and 07700 900 456")
    values = [v for _, _, v in spans]
    assert values[0] == "447700900123"
    assert values[1] == "07700900456"


def test_find_emails():
    spans = find_emails("contact: amelia.jones@example-mail.test please")
    assert [v for _, _, v in spans] == ["amelia.jones@example-mail.test"]


def test_find_ibans():
    spans = find_ibans("Pay via IBAN GB29 NWBK 6016 1331 9268 19 today")
    assert [v for _, _, v in spans] == ["GB29NWBK60161331926819"]


def test_find_card_numbers_only_luhn_valid():
    text = "Card: 4111111111111111, order no: 1234567890123456"
    spans = find_card_numbers(text)
    values = [v for _, _, v in spans]
    assert "4111111111111111" in values
    assert "1234567890123456" not in values


def test_find_full_names_only_from_injected_list():
    text = "Hello James Smith, I can see your record."
    assert find_full_names(text, ["James Smith"]) != []
    assert find_full_names(text, []) == []
    assert find_full_names("Random Name here.", ["James Smith"]) == []
