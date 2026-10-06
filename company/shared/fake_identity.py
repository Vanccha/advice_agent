"""Fake-but-realistic UK customer data.

Every value here is deliberately impossible to belong to a real person:
National Insurance numbers use prefixes HMRC never allocates, phone numbers come from
Ofcom's range reserved for drama (07700 900000-900999), e-mails use the reserved `.test`
TLD and addresses carry fictional street names.
"""
from __future__ import annotations

import random
import re
import unicodedata

FIRST_NAMES_MALE = [
    "James", "Oliver", "Harry", "George", "Jack", "Thomas", "William", "Charlie", "Daniel",
    "Samuel", "Joseph", "Henry", "Edward", "Alfie", "Leo", "Oscar", "Arthur", "Noah", "Ethan",
    "Lewis", "Callum", "Ryan", "Liam", "Owen", "Rhys", "Connor", "Adam", "Ben",
]
FIRST_NAMES_FEMALE = [
    "Olivia", "Amelia", "Isla", "Emily", "Sophie", "Grace", "Jessica", "Charlotte", "Lucy",
    "Hannah", "Ella", "Chloe", "Megan", "Holly", "Ruby", "Freya", "Poppy", "Evie", "Alice",
    "Isabel", "Katie", "Laura", "Rachel", "Zoe", "Niamh", "Eilidh", "Rosie", "Abigail",
]
LAST_NAMES = [
    "Smith", "Jones", "Taylor", "Brown", "Williams", "Wilson", "Johnson", "Davies", "Robinson",
    "Wright", "Thompson", "Evans", "Walker", "White", "Roberts", "Green", "Hall", "Wood",
    "Jackson", "Clarke", "Patel", "Khan", "Lewis", "Harris", "Martin", "Cooper", "Hughes",
    "Edwards", "Turner", "Hill", "Moore", "Ward", "Morris", "Campbell", "Murphy",
]
STREET_NAMES = [
    "Kingfisher Lane", "Silverbrook Road", "Carnation Close", "Olive Grove", "Bluebird Way",
    "Juniper Crescent", "Chestnut Hollow", "Daisy Mews", "Cherry Blossom Avenue",
    "Acacia Gardens", "Pinecrest Drive", "Pearl Point Road", "Tulip Field Lane", "Coral Walk",
]

# Ofcom reserves 07700 900000-900999 for TV/radio drama: never assigned to a real line.
DRAMA_MOBILE_PREFIX = "7700900"

# HMRC never allocates these National Insurance prefixes.
UNALLOCATED_NINO_PREFIXES = ("BG", "GB", "KN", "NK", "NT", "TN", "ZZ")
_NINO_INVALID_LETTERS = set("DFIQUV")
_NINO_SHAPE_RE = re.compile(r"^[A-Z]{2}\d{6}[A-D]$")


def national_id_is_valid(value: str) -> bool:
    """HMRC's National Insurance number allocation rules. Used only to prove our data is
    NOT valid."""
    if not _NINO_SHAPE_RE.match(value):
        return False
    prefix = value[:2]
    if prefix in UNALLOCATED_NINO_PREFIXES:
        return False
    if prefix[0] in _NINO_INVALID_LETTERS or prefix[1] in _NINO_INVALID_LETTERS | {"O"}:
        return False
    return True


def make_invalid_national_id(rng: random.Random) -> str:
    """9 characters, realistic shape, guaranteed to use a never-allocated prefix."""
    prefix = rng.choice(UNALLOCATED_NINO_PREFIXES)
    digits = "".join(str(rng.randint(0, 9)) for _ in range(6))
    value = f"{prefix}{digits}{rng.choice('ABCD')}"
    assert not national_id_is_valid(value)
    return value


def make_phone(rng: random.Random) -> str:
    return "+44" + DRAMA_MOBILE_PREFIX + "".join(str(rng.randint(0, 9)) for _ in range(3))


def slugify(value: str) -> str:
    normalised = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    return "".join(ch if ch.isalnum() else "-" for ch in normalised.lower()).strip("-")


def make_email(full_name: str, rng: random.Random) -> str:
    first, _, last = full_name.partition(" ")
    return f"{slugify(first)}.{slugify(last)}{rng.randint(1, 99)}@example-mail.test"


def make_full_name(rng: random.Random) -> str:
    pool = FIRST_NAMES_MALE if rng.random() < 0.5 else FIRST_NAMES_FEMALE
    return f"{rng.choice(pool)} {rng.choice(LAST_NAMES)}"


def make_address(rng: random.Random) -> str:
    return f"Flat {rng.randint(1, 20)}, {rng.randint(1, 120)} {rng.choice(STREET_NAMES)}"


def make_card_token(rng: random.Random) -> str:
    """No PAN is ever stored; services only see opaque tokens."""
    return f"tok_test_{rng.randint(100000, 999999)}"
