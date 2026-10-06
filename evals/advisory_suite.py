"""The advisory suite (`evals/datasets/advisory_profiles.yaml`, contracts §4.4): for each
customer profile, fetch the *live* package catalogue through the core adapter, call the
deterministic `recommend_packages` directly, and check every expectation key plus the
file's shared `invariants`. As a secondary, best-effort check (`conversation_tr`), the same
profile is also driven through the real `Orchestrator`'s ADVISORY mode, to confirm the
verbalised Turkish reply never names a package outside the live catalogue — this does not
have to reproduce `expected_best` (the deterministic field-by-field parser in
`modes/advisory.py` does not pretend to be an NLU model), so it is scored as an additional,
separate check rather than folded into the primary pass/fail for `expected_best`.

Import as: ``from evals.advisory_suite import run_advisory_suite``.
"""
from __future__ import annotations

import re
from typing import Any

from core_common.types import AdvisoryProfile
from evals.checks import CaseResult
from modes.tool_data import as_list
from recommendation.engine import recommend_packages

_PLACEHOLDER_RE = re.compile(r"\{[a-zA-Z_]+\}")
_MAX_CONVERSATION_TURNS = 7  # policy.yaml max_questions_advisory (5) + opening + margin


def _reason_patterns(templates: dict[str, str]) -> list[re.Pattern[str]]:
    """One regex per `routing.yaml: recommendation.reason_codes_tr` template, with every
    `{placeholder}` turned into a wildcard — used to prove every `PackageOffer.reasons`
    string actually came from the configured table, never free text."""
    patterns = []
    for template in templates.values():
        parts = re.split(r"(\{[a-zA-Z_]+\})", template)
        pattern_str = "".join(".+?" if _PLACEHOLDER_RE.fullmatch(p) else re.escape(p) for p in parts)
        patterns.append(re.compile(f"^{pattern_str}$"))
    return patterns


def _matches_any(text: str, patterns: list[re.Pattern[str]]) -> bool:
    return any(p.match(text) for p in patterns)


def _check_conversation_honesty(
    harness: Any, profile_case: dict[str, Any], catalogue_names: set[str], case: CaseResult
) -> None:
    conversation = list(profile_case.get("conversation_tr") or [])
    if not conversation:
        return

    conv_id = harness.new_conversation_id(f"advisory-conv-{profile_case['id']}")
    try:
        turn = harness.send(conv_id, None, conversation[0])
        turns_sent = 1
        idx = 1
        while turn.mode != "CLOSING" and turns_sent < _MAX_CONVERSATION_TURNS:
            answer = conversation[idx] if idx < len(conversation) else "farketmez"
            turn = harness.send(conv_id, None, answer)
            idx += 1
            turns_sent += 1
    except Exception as exc:  # noqa: BLE001 - reported, never silently skipped
        case.add("conversation_reply_only_catalogue_packages", False, expected="orchestrator reachable", actual=str(exc))
        return

    if turn.mode != "CLOSING":
        case.message = (
            (case.message + "; ") if case.message else ""
        ) + "conversation-driven advisory check inconclusive: never reached CLOSING"
        return

    reply = turn.reply_tr or ""
    # `modes/advisory.py:_format_offer` renders each offer as "• <name> — <down>/<up> Mbps,
    # <price>/ay" — parse that exact bullet shape to recover which package names the reply
    # actually names, without re-deriving the profile ourselves.
    mentioned = [line[2:].split(" — ")[0].strip() for line in reply.split("\n") if line.startswith("• ")]
    unknown = [name for name in mentioned if name not in catalogue_names]
    case.add(
        "conversation_reply_only_catalogue_packages",
        not unknown,
        expected="every named package is in the live catalogue",
        actual=mentioned,
    )


def _check_profile(harness: Any, profile_case: dict[str, Any]) -> CaseResult:
    case_id = profile_case["id"]
    expect = profile_case["expect"]
    case = CaseResult(suite="advisory", case_id=case_id)

    try:
        packages = as_list(harness.call_tool("list_packages", {}))
    except Exception as exc:  # noqa: BLE001
        case.status = "ERROR"
        case.message = f"list_packages failed: {exc}"
        return case
    if not packages:
        case.status = "ERROR"
        case.message = "list_packages returned no packages"
        return case
    catalog_by_code = {p["code"]: p for p in packages}

    try:
        profile = AdvisoryProfile.model_validate(profile_case["profile"])
    except Exception as exc:  # noqa: BLE001
        case.status = "ERROR"
        case.message = f"invalid profile fixture: {exc}"
        return case

    try:
        offers1 = recommend_packages(profile, packages, harness.tenant_config.routing)
        offers2 = recommend_packages(profile, packages, harness.tenant_config.routing)
    except Exception as exc:  # noqa: BLE001
        case.status = "ERROR"
        case.message = f"recommend_packages raised: {exc}"
        return case

    offer_codes = [o.package_code for o in offers1]
    best = offers1[0] if offers1 else None

    if "expected_best" in expect:
        case.add(
            "expected_best",
            bool(best) and best.package_code == expect["expected_best"],
            expected=expect["expected_best"],
            actual=best.package_code if best else None,
        )

    if "must_not_recommend" in expect:
        banned = set(expect["must_not_recommend"])
        hit = sorted(banned & set(offer_codes))
        case.add("must_not_recommend", not hit, expected=f"none of {sorted(banned)}", actual=offer_codes)

    # Interpretation (see evals/README.md): `max_monthly_price_try`/`min_down_mbps`, like
    # `must_have_static_ip`/`must_have_tv`/`must_have_no_commitment`, describe the *best*
    # (top-ranked) recommendation — a top-3 list legitimately includes weaker/cheaper
    # comparison alternatives alongside it (that is what `must_not_recommend` is for: it
    # bans a package from appearing *anywhere* in the list, which these numeric floors/
    # ceilings deliberately do not).
    if "max_monthly_price_try" in expect:
        limit = expect["max_monthly_price_try"]
        ok = bool(best) and best.monthly_price_try <= limit
        case.add("max_monthly_price_try", ok, expected=f"<= {limit}", actual=best.monthly_price_try if best else None)

    if "min_down_mbps" in expect:
        floor = expect["min_down_mbps"]
        ok = bool(best) and best.down_mbps >= floor
        case.add("min_down_mbps", ok, expected=f">= {floor}", actual=best.down_mbps if best else None)

    if expect.get("must_have_static_ip"):
        flag = bool(best) and bool(catalog_by_code.get(best.package_code, {}).get("static_ip"))
        case.add("must_have_static_ip", flag, expected=True, actual=flag)

    if expect.get("must_have_tv"):
        flag = bool(best) and bool(catalog_by_code.get(best.package_code, {}).get("tv_included"))
        case.add("must_have_tv", flag, expected=True, actual=flag)

    if expect.get("must_have_no_commitment"):
        flag = bool(best) and best.commitment_months == 0
        case.add("must_have_no_commitment", flag, expected=0, actual=best.commitment_months if best else None)

    # Shared invariants (dataset's top-level `invariants:`), checked per profile.
    case.add("max_offers_returned", len(offers1) <= 3, expected="<= 3", actual=len(offers1))
    case.add("min_offers_returned", len(offers1) >= 1, expected=">= 1", actual=len(offers1))

    def _fingerprint(offers: list[Any]) -> list[tuple[str, float, tuple[str, ...], bool]]:
        return [(o.package_code, o.score, tuple(o.reasons), o.is_best) for o in offers]

    deterministic = _fingerprint(offers1) == _fingerprint(offers2)
    case.add("deterministic", deterministic, expected="identical offers on repeat call", actual=deterministic)

    patterns = _reason_patterns(harness.tenant_config.routing.recommendation.reason_codes_tr)
    bad_reasons = [r for o in offers1 for r in o.reasons if not _matches_any(r, patterns)]
    case.add("reasons_from_config_only", not bad_reasons, expected="every reason matches a routing.yaml template", actual=bad_reasons)

    outside = [c for c in offer_codes if c not in catalog_by_code]
    case.add("no_package_outside_catalogue", not outside, expected="every offer code is in the catalogue", actual=outside)

    _check_conversation_honesty(harness, profile_case, {p["name"] for p in packages}, case)

    return case.finalize()


def run_advisory_suite(harness: Any, advisory_doc: dict[str, Any], *, only_case: str | None = None) -> list[CaseResult]:
    results: list[CaseResult] = []
    for profile_case in advisory_doc.get("profiles", []):
        if only_case and profile_case["id"] != only_case:
            continue
        results.append(_check_profile(harness, profile_case))
    return results
