from __future__ import annotations

import logging
from uuid import uuid4

from app.schemas import RecommendationItem, RecommendationResult, TriageCase

logger = logging.getLogger(__name__)


# Temporary in-memory workflow store.
#
# Responsibilities:
# - /chat creates and updates TriageCase by case_id.
# - /recommend stores the latest RecommendationResult items by case_id.
# - /generate_script resolves case_id + recommendation_id back to the selected item.
#
# Deployment note:
# This module is intentionally lightweight for the current prototype. Data is lost
# on process restart and is not shared across workers. Replace it with SQLite,
# Redis, or the primary DB before production or multi-worker deployment.
_CASES: dict[str, TriageCase] = {}
_RECOMMENDATIONS_BY_CASE: dict[str, dict[str, RecommendationItem]] = {}


def new_case_id() -> str:
    return f"case_{uuid4().hex[:8]}"


def create_case(case_id: str | None = None) -> TriageCase:
    case = TriageCase(case_id=case_id or new_case_id())
    _CASES[case.case_id] = case
    logger.info("case_store create_case case_id=%s", case.case_id)
    return case


def get_case(case_id: str) -> TriageCase | None:
    case = _CASES.get(case_id)
    logger.debug("case_store get_case case_id=%s found=%s", case_id, case is not None)
    return case


def save_case(case: TriageCase) -> TriageCase:
    _CASES[case.case_id] = case
    logger.debug(
        "case_store save_case case_id=%s history_len=%s stage=%s",
        case.case_id,
        len(case.history_records),
        case.conversation_state.stage,
    )
    return case


def get_recommendations_for_case(case_id: str) -> dict[str, RecommendationItem]:
    return _RECOMMENDATIONS_BY_CASE.get(case_id, {})


def save_recommendation_result(result: RecommendationResult) -> RecommendationResult:
    items = [
        *result.recommendations.specialty_first,
        *result.recommendations.time_first,
    ]
    save_recommendations(result.case_id, items)
    return result


def save_recommendations(
    case_id: str,
    items: list[RecommendationItem],
) -> list[RecommendationItem]:
    _RECOMMENDATIONS_BY_CASE[case_id] = {
        item.recommendation_id: item for item in items
    }
    return items


def get_recommendation(case_id: str, recommendation_id: str) -> RecommendationItem | None:
    return get_recommendations_for_case(case_id).get(recommendation_id)


def find_recommendation(recommendation_id: str) -> RecommendationItem | None:
    for recommendations in _RECOMMENDATIONS_BY_CASE.values():
        if recommendation_id in recommendations:
            return recommendations[recommendation_id]
    return None
