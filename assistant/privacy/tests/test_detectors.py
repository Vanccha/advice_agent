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
    spans = find_national_ids("Müşteri TC no: 35111111110 kayıtlı.")
    assert [v for _, _, v in spans] == ["35111111110"]


def test_find_phones_plus90_and_local_format():
    spans = find_phones("Telefon: +905321234567 ve 0535 123 45 67")
    values = [v for _, _, v in spans]
    assert values[0] == "905321234567"
    assert values[1] == "05351234567"


def test_find_emails():
    spans = find_emails("iletisim: ayse.kaya@ornek-eposta.test lütfen")
    assert [v for _, _, v in spans] == ["ayse.kaya@ornek-eposta.test"]


def test_find_ibans():
    spans = find_ibans("IBAN TR330006100519786457841326 ile ödeme")
    assert [v for _, _, v in spans] == ["TR330006100519786457841326"]


def test_find_card_numbers_only_luhn_valid():
    text = "Kart: 4111111111111111, sipariş no: 1234567890123456"
    spans = find_card_numbers(text)
    values = [v for _, _, v in spans]
    assert "4111111111111111" in values
    assert "1234567890123456" not in values


def test_find_full_names_only_from_injected_list():
    text = "Merhaba Ahmet Yilmaz, kaydınızı görüyorum."
    assert find_full_names(text, ["Ahmet Yilmaz"]) != []
    assert find_full_names(text, []) == []
    assert find_full_names("Rastgele Isim burada.", ["Ahmet Yilmaz"]) == []
