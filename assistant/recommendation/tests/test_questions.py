from core_common.types import AdvisoryProfile, UsageType
from recommendation.questions import QUESTIONS, next_question, profile_is_complete


def test_question_bank_has_at_most_five():
    assert len(QUESTIONS) <= 5
    assert len(QUESTIONS) >= 3


def test_next_question_starts_with_usage():
    profile = AdvisoryProfile()
    question = next_question(profile)
    assert question is not None
    assert question.field == "usage"


def test_next_question_skips_answered_fields():
    profile = AdvisoryProfile(usage=[UsageType.STUDENT], household_size=2)
    question = next_question(profile)
    assert question is not None
    assert question.field == "device_count"


def test_next_question_none_when_all_answered():
    profile = AdvisoryProfile(
        usage=[UsageType.STUDENT],
        household_size=2,
        device_count=3,
        budget_gbp=30,
        commitment_preference="any",
    )
    assert next_question(profile) is None


def test_profile_is_complete_requires_usage_household_and_budget():
    assert profile_is_complete(AdvisoryProfile()) is False
    assert profile_is_complete(AdvisoryProfile(usage=[UsageType.STUDENT])) is False
    # usage + a size, but no budget yet -> still incomplete.
    assert profile_is_complete(AdvisoryProfile(usage=[UsageType.STUDENT], device_count=2)) is False
    assert profile_is_complete(AdvisoryProfile(usage=[UsageType.STUDENT], household_size=2)) is False
    # usage + a size + budget -> complete, either size field satisfies the "size" dimension.
    assert profile_is_complete(
        AdvisoryProfile(usage=[UsageType.STUDENT], device_count=2, budget_gbp=30)
    ) is True
    assert profile_is_complete(
        AdvisoryProfile(usage=[UsageType.STUDENT], household_size=2, budget_gbp=30)
    ) is True


def test_profile_is_complete_ignores_optional_commitment():
    profile = AdvisoryProfile(usage=[UsageType.FAMILY], household_size=3, budget_gbp=40)
    assert profile_is_complete(profile) is True
    assert profile.commitment_preference is None
