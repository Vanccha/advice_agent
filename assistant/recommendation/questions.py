"""Fixed Turkish question bank for ADVISORY mode (contracts §4.3: 3-5 questions).

Import as: ``from recommendation.questions import QUESTIONS, next_question, profile_is_complete``.
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from core_common.types import AdvisoryProfile


class Question(BaseModel):
    model_config = ConfigDict(frozen=True)

    field: str
    question_tr: str
    expected_type: str
    options_tr: list[str] | None = None


# Order matters: this is also the order the assistant asks them in.
QUESTIONS: list[Question] = [
    Question(
        field="usage",
        question_tr=(
            "İnterneti ağırlıklı olarak ne için kullanıyorsunuz? Birden fazla seçebilirsiniz."
        ),
        expected_type="multi_choice",
        options_tr=["Öğrenci", "Aile", "Ev Ofisi", "Oyun", "Dizi/Film İzleme", "Temel Kullanım"],
    ),
    Question(
        field="household_size",
        question_tr="Evde kaç kişi yaşıyorsunuz?",
        expected_type="int",
    ),
    Question(
        field="device_count",
        question_tr="İnternete bağlanan yaklaşık kaç cihazınız var (telefon, bilgisayar, TV vb.)?",
        expected_type="int",
    ),
    Question(
        field="budget_try",
        question_tr=(
            "Aylık ödemek istediğiniz yaklaşık bütçe nedir (TL)? Belirtmek istemiyorsanız "
            "geçebilirsiniz."
        ),
        expected_type="float",
    ),
    Question(
        field="commitment_preference",
        question_tr="Taahhüt tercihiniz nedir?",
        expected_type="enum",
        options_tr=["Taahhütsüz", "12 Ay", "24 Ay", "Farketmez"],
    ),
]

def _is_missing(profile: AdvisoryProfile, field: str) -> bool:
    value = getattr(profile, field)
    if field == "usage":
        return not value
    return value is None


def next_question(profile: AdvisoryProfile) -> Question | None:
    """The next unanswered question, in fixed order. ``None`` once all 5 are answered."""
    for question in QUESTIONS:
        if _is_missing(profile, question.field):
            return question
    return None


def profile_is_complete(profile: AdvisoryProfile) -> bool:
    """Complete once usage + (device_count or household_size) are known; budget and
    commitment preference stay optional (contracts §4.4 / brief)."""
    if not profile.usage:
        return False
    return profile.device_count is not None or profile.household_size is not None
