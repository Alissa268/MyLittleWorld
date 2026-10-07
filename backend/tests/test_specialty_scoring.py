from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, patch

import pytest

from app.schemas import DepartmentResult, Message, SemanticExtraction, TriageCase
from app.services import specialty_scoring
from app.services.specialty_scoring import score_doctor_deterministically, score_doctor_specialties


class AiSettings:
    cerebras_api_key = "test-key"
    ai_doctor_scoring_enabled = True
    doctor_scoring_batch_size = 10
    doctor_scoring_max_candidates = 40
    doctor_scoring_max_batches = 4


def _case() -> TriageCase:
    case = TriageCase(case_id="phase6-specialty")
    case.semantic_extractions = [SemanticExtraction(
        field="symptom",
        normalized_value="膝部疼痛",
        semantic_status="available",
        assertion="present",
        confidence=0.98,
        source_text="膝蓋痛",
        extractor="ai",
    )]
    case.history_records = [Message(role="user", content="膝蓋痛")]
    return case


def _department() -> DepartmentResult:
    return DepartmentResult(dept_id=1298, parentDept="外科系", childDept="一般骨科")


def _row(doctor_id: str = "doc-1", *, tags: str = "膝關節") -> dict:
    return {
        "doctor_id": doctor_id,
        "doctor": f"醫師 {doctor_id}",
        "child_dept": "一般骨科",
        "specialty_tags": tags,
        "schedule_id": f"schedule-{doctor_id}",
    }


def _response(rows: list[dict], *, offset: float = 0.0) -> str:
    return json.dumps({
        "scores": [
            {"doctor_id": row["doctor_id"], "score": 0.6 + offset + index * 0.01}
            for index, row in enumerate(rows)
        ]
    })


def test_deterministic_fallback_is_neutral_and_has_no_keyword_inference():
    score = score_doctor_deterministically(_case(), _department(), _row(tags="膝關節、運動傷害"))
    assert score.score == 0.5
    assert score.source == "neutral"


def test_five_unique_doctors_use_one_batch_and_reason_is_not_required():
    rows = [_row(f"doc-{index}") for index in range(5)]
    provider = AsyncMock(return_value=_response(rows))
    with patch.object(specialty_scoring, "get_settings", return_value=AiSettings()), patch.object(
        specialty_scoring, "complete_prompt", new=provider
    ):
        scores = asyncio.run(score_doctor_specialties(_case(), _department(), rows))

    provider.assert_awaited_once()
    assert set(scores) == {row["doctor_id"] for row in rows}
    assert all(item.source == "ai" for item in scores.values())
    assert '"reason"' not in provider.await_args.args[0]


def test_twenty_schedule_rows_for_one_doctor_are_scored_once():
    rows = [{**_row("doc-1"), "schedule_id": f"s-{index}"} for index in range(20)]
    provider = AsyncMock(return_value=_response([rows[0]]))
    with patch.object(specialty_scoring, "get_settings", return_value=AiSettings()), patch.object(
        specialty_scoring, "complete_prompt", new=provider
    ):
        scores = asyncio.run(score_doctor_specialties(_case(), _department(), rows))

    provider.assert_awaited_once()
    assert list(scores) == ["doc-1"]
    assert provider.await_args.args[0].count('"doctor_id": "doc-1"') == 1


def test_large_set_uses_small_batches_not_one_call_per_doctor():
    rows = [_row(f"doc-{index}") for index in range(23)]
    provider = AsyncMock(side_effect=[
        _response(rows[0:10]), _response(rows[10:20]), _response(rows[20:23]),
    ])
    with patch.object(specialty_scoring, "get_settings", return_value=AiSettings()), patch.object(
        specialty_scoring, "complete_prompt", new=provider
    ):
        scores = asyncio.run(score_doctor_specialties(_case(), _department(), rows))

    assert provider.await_count == 3
    assert len(scores) == 23
    assert all(item.source == "ai" for item in scores.values())


@pytest.mark.parametrize("bad_payload", [
    {"scores": [{"doctor_id": "missing", "score": 0.8}]},
    {"scores": [{"doctor_id": "doc-0", "score": 0.8}, {"doctor_id": "doc-0", "score": 0.7}]},
    {"scores": [{"doctor_id": "doc-0", "score": 0.8}]},
    {"scores": [{"doctor_id": "doc-0", "score": 1.1}, {"doctor_id": "doc-1", "score": 0.7}]},
    {"scores": [{"doctor_id": "doc-0", "score": -0.1}, {"doctor_id": "doc-1", "score": 0.7}]},
    {"scores": [{"doctor_id": "doc-0", "score": float("nan")}, {"doctor_id": "doc-1", "score": 0.7}]},
])
def test_any_invalid_batch_result_makes_whole_set_neutral(bad_payload):
    rows = [_row("doc-0"), _row("doc-1")]
    provider = AsyncMock(return_value=json.dumps(bad_payload))
    with patch.object(specialty_scoring, "get_settings", return_value=AiSettings()), patch.object(
        specialty_scoring, "complete_prompt", new=provider
    ):
        scores = asyncio.run(score_doctor_specialties(_case(), _department(), rows))

    assert all(item.score == 0.5 and item.source == "neutral" for item in scores.values())


def test_timeout_in_later_batch_neutralizes_earlier_successes():
    rows = [_row(f"doc-{index}") for index in range(12)]
    provider = AsyncMock(side_effect=[_response(rows[:10]), TimeoutError("timeout")])
    with patch.object(specialty_scoring, "get_settings", return_value=AiSettings()), patch.object(
        specialty_scoring, "complete_prompt", new=provider
    ):
        scores = asyncio.run(score_doctor_specialties(_case(), _department(), rows))

    assert provider.await_count == 2
    assert all(item.score == 0.5 and item.source == "neutral" for item in scores.values())


def test_over_capacity_neutralizes_everyone_without_calling_ai():
    rows = [_row(f"doc-{index}") for index in range(41)]
    provider = AsyncMock()
    with patch.object(specialty_scoring, "get_settings", return_value=AiSettings()), patch.object(
        specialty_scoring, "complete_prompt", new=provider
    ):
        scores = asyncio.run(score_doctor_specialties(_case(), _department(), rows))

    provider.assert_not_awaited()
    assert len(scores) == 41
    assert all(item.score == 0.5 for item in scores.values())


def test_missing_tags_are_neutral_and_not_sent_to_ai():
    rows = [_row("doc-tagged"), _row("doc-empty", tags="")]
    provider = AsyncMock(return_value=_response([rows[0]]))
    with patch.object(specialty_scoring, "get_settings", return_value=AiSettings()), patch.object(
        specialty_scoring, "complete_prompt", new=provider
    ):
        scores = asyncio.run(score_doctor_specialties(_case(), _department(), rows))

    assert scores["doc-tagged"].source == "ai"
    assert scores["doc-empty"].score == 0.5
    assert "doc-empty" not in provider.await_args.args[0]


def test_missing_doctor_id_never_enters_formal_scoring():
    provider = AsyncMock()
    row = _row("doc-1")
    row["doctor_id"] = None
    row["doctor"] = "不可當 identity 的名字"
    with patch.object(specialty_scoring, "get_settings", return_value=AiSettings()), patch.object(
        specialty_scoring, "complete_prompt", new=provider
    ):
        scores = asyncio.run(score_doctor_specialties(_case(), _department(), [row]))

    provider.assert_not_awaited()
    assert scores == {}
