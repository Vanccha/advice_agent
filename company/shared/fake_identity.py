"""Fake-but-realistic Turkish customer data.

Every value here is deliberately impossible to belong to a real person:
national ids fail the official checksum, e-mails use the reserved `.test` TLD and
addresses carry fictional street names.
"""
from __future__ import annotations

import random
import unicodedata

FIRST_NAMES_MALE = [
    "Ahmet", "Mehmet", "Mustafa", "Emre", "Burak", "Hakan", "Serkan", "Yusuf", "Kerem",
    "Oğuz", "Barış", "Cem", "Tolga", "Volkan", "Onur", "Deniz", "Selim", "Kaan", "Umut",
    "Berk", "Eren", "Furkan", "Tarık", "Levent", "Sinan", "Murat", "Okan", "Yiğit",
]
FIRST_NAMES_FEMALE = [
    "Ayşe", "Fatma", "Zeynep", "Elif", "Merve", "Büşra", "Esra", "Gamze", "Seda", "Pınar",
    "Dilek", "Hande", "Ceren", "Melis", "Sibel", "Tuğba", "Nalan", "Özge", "Şeyma", "Yasemin",
    "İrem", "Buse", "Damla", "Nihan", "Cansu", "Gizem", "Aslı", "Ebru",
]
LAST_NAMES = [
    "Yılmaz", "Kaya", "Demir", "Çelik", "Şahin", "Yıldız", "Yıldırım", "Öztürk", "Aydın",
    "Özdemir", "Arslan", "Doğan", "Kılıç", "Aslan", "Çetin", "Kara", "Koç", "Kurt",
    "Özkan", "Şimşek", "Polat", "Korkmaz", "Erdoğan", "Güneş", "Bulut", "Aksoy",
    "Tekin", "Sarı", "Avcı", "Keskin", "Toprak", "Bozkurt", "Ünal", "Akın", "Soylu",
]
STREET_NAMES = [
    "Yalıçapkını", "Gümüşdere", "Karanfilli", "Zeytinlik", "Mavikuş", "Erguvan", "Sıracevizler",
    "Papatya", "Kiraz Çiçeği", "Akasya", "Çamlıyurt", "İnciburnu", "Lale Tarlası", "Mercanköy",
]
PHONE_PREFIXES = ["530", "532", "535", "541", "544", "551", "555"]


def national_id_checksum_valid(value: str) -> bool:
    """Official Turkish national id checksum. Used only to prove our data is NOT valid."""
    if len(value) != 11 or not value.isdigit() or value[0] == "0":
        return False
    digits = [int(c) for c in value]
    odd_sum = sum(digits[0:9:2])
    even_sum = sum(digits[1:8:2])
    tenth = (odd_sum * 7 - even_sum) % 10
    eleventh = sum(digits[:10]) % 10
    return digits[9] == tenth and digits[10] == eleventh


def make_invalid_national_id(rng: random.Random) -> str:
    """11-digit, realistic shape, guaranteed to FAIL the official checksum."""
    body = [rng.randint(1, 9)] + [rng.randint(0, 9) for _ in range(8)]
    odd_sum = sum(body[0:9:2])
    even_sum = sum(body[1:8:2])
    tenth = (odd_sum * 7 - even_sum) % 10
    eleventh = (sum(body) + tenth) % 10
    # Break the final check digit on purpose.
    eleventh = (eleventh + 1) % 10
    value = "".join(str(d) for d in [*body, tenth, eleventh])
    assert not national_id_checksum_valid(value)
    return value


def make_phone(rng: random.Random) -> str:
    return "+90" + rng.choice(PHONE_PREFIXES) + "".join(str(rng.randint(0, 9)) for _ in range(7))


def slugify(value: str) -> str:
    normalised = (
        value.replace("ı", "i").replace("İ", "I").replace("ğ", "g").replace("Ğ", "G")
        .replace("ü", "u").replace("Ü", "U").replace("ş", "s").replace("Ş", "S")
        .replace("ö", "o").replace("Ö", "O").replace("ç", "c").replace("Ç", "C")
    )
    normalised = unicodedata.normalize("NFKD", normalised).encode("ascii", "ignore").decode()
    return "".join(ch if ch.isalnum() else "-" for ch in normalised.lower()).strip("-")


def make_email(full_name: str, rng: random.Random) -> str:
    first, _, last = full_name.partition(" ")
    return f"{slugify(first)}.{slugify(last)}{rng.randint(1, 99)}@ornek-eposta.test"


def make_full_name(rng: random.Random) -> str:
    pool = FIRST_NAMES_MALE if rng.random() < 0.5 else FIRST_NAMES_FEMALE
    return f"{rng.choice(pool)} {rng.choice(LAST_NAMES)}"


def make_address(rng: random.Random) -> str:
    return f"{rng.choice(STREET_NAMES)} Sokak No:{rng.randint(1, 120)} D:{rng.randint(1, 20)}"


def make_card_token(rng: random.Random) -> str:
    """No PAN is ever stored; services only see opaque tokens."""
    return f"tok_test_{rng.randint(100000, 999999)}"
