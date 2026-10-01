from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.schemas import DepartmentResult, Message, SemanticExtraction, TriageCase
from app.services import conversation_service
from app.services.case_store import save_case
from app.services.conversation_service import (
    PendingAnswerClassification,
    advance_conversation,
    capture_pending_answer,
    classify_pending_answer,
    request_clarification,
)
from app.services.rag_triage_adapter import RagTriageSuggestion


PENDING_QUESTION = "請問您是否有腰部或腹部的疼痛感？"


def pending_pain_case(case_id: str = "phase4-focused-pending") -> TriageCase:
    case = TriageCase(case_id=case_id)
    case.history_records = [
        Message(role="user", content="我最近有血尿，已經兩天了"),
        Message(role="assistant", content=PENDING_QUESTION),
    ]
    case.patient_input.symptom = "血尿"
    case.patient_input.red_flags_checked = True
    case.semantic_extractions = [SemanticExtraction(
        field="duration", normalized_value="2天", semantic_status="available",
        confidence=0.96, source_text="兩天", extractor="ai",
    )]
    case.conversation_state.free_text_mode = True
    case.conversation_state.pending_clarification_intent = "pain_presence"
    case.conversation_state.asked_clarification_intents = ["pain_presence"]
    case.conversation_state.department_status = "resolved"
    case.department_result = DepartmentResult(
        dept_id=1242, parentDept="內科部", childDept="腎臟科", confidence=0.94,
    )
    return case


def classifier_json(
    *,
    intent: object = "pain_presence",
    status: object = "answered",
    source: object,
    confidence: object = 0.95,
) -> str:
    return json.dumps({
        "answered_intent": intent,
        "answer_status": status,
        "answer_source_text": source,
        "answer_confidence": confidence,
    }, ensure_ascii=False)


def run_classifier(case: TriageCase, text: str, raw: object):
    provider = AsyncMock(
        side_effect=raw if isinstance(raw, Exception) else None,
        return_value=raw if isinstance(raw, str) else None,
    )
    with patch(
        "app.services.conversation_service.complete_runtime_json",
        new=provider,
    ):
        result = asyncio.run(classify_pending_answer(case, [text]))
    return result, provider


def test_explicit_negative_answer_is_grounded_and_clears_pending_without_semantic_extraction():
    case = pending_pain_case("phase4-negative-answer")
    text = "沒有腰痛，也沒有腹痛"
    result, provider = run_classifier(case, text, classifier_json(source=text))

    assert isinstance(result, PendingAnswerClassification)
    assert capture_pending_answer(case, result, [text]) is True
    assert case.conversation_state.pending_clarification_intent is None
    assert case.conversation_state.clarification_evidence["pain_presence"] == text
    assert len(case.semantic_extractions) == 1
    assert case.semantic_extractions[0].field == "duration"
    prompt = provider.await_args.args[0]
    assert "明確否定仍然是 answered" in prompt
    assert "沒有腰痛，也沒有腹痛" in prompt


def test_positive_answer_clears_pending():
    case = pending_pain_case("phase4-positive-answer")
    text = "右腰會痛"
    result, _ = run_classifier(case, text, classifier_json(source=text))

    assert capture_pending_answer(case, result, [text]) is True
    assert case.conversation_state.pending_clarification_intent is None
    assert case.conversation_state.clarification_evidence["pain_presence"] == text


@pytest.mark.parametrize("status", ["partial", "unclear"])
def test_partial_or_unclear_answer_keeps_pending(status):
    case = pending_pain_case(f"phase4-{status}-answer")
    text = "好像有一點，但不確定" if status == "partial" else "不知道"
    result, _ = run_classifier(
        case,
        text,
        classifier_json(status=status, source=text),
    )

    assert result is not None
    assert capture_pending_answer(case, result, [text]) is False
    assert case.conversation_state.pending_clarification_intent == "pain_presence"
    assert case.conversation_state.clarification_evidence == {}


@pytest.mark.parametrize(
    ("raw", "text", "expect_classification"),
    [
        (classifier_json(intent="other_intent", source="右腰會痛"), "右腰會痛", False),
        (classifier_json(source="先前說過腰痛"), "現在沒有了", False),
        (classifier_json(source="右腰會痛", confidence=0.2), "右腰會痛", True),
    ],
)
def test_wrong_intent_ungrounded_source_and_low_confidence_cannot_clear(
    raw,
    text,
    expect_classification,
):
    case = pending_pain_case(f"phase4-invalid-{len(text)}")
    result, _ = run_classifier(case, text, raw)

    assert (result is not None) is expect_classification
    assert capture_pending_answer(case, result, [text]) is False
    assert case.conversation_state.pending_clarification_intent == "pain_presence"
    assert case.conversation_state.clarification_evidence == {}


@pytest.mark.parametrize("raw", ["{bad json", TimeoutError()])
def test_malformed_or_provider_failure_fails_closed(raw):
    case = pending_pain_case(f"phase4-provider-failure-{type(raw).__name__}")
    result, _ = run_classifier(case, "沒有腰痛，也沒有腹痛", raw)

    assert result is None
    assert capture_pending_answer(case, result, ["沒有腰痛，也沒有腹痛"]) is False
    assert case.conversation_state.pending_clarification_intent == "pain_presence"


def test_planner_answer_fields_cannot_override_focused_classifier_failure():
    case = pending_pain_case("phase4-planner-cannot-override")
    text = "沒有腰痛，也沒有腹痛"
    planner_payload = {
        "status": "sufficient",
        "question": None,
        "intent": None,
        "reason": "planner attempted to classify the pending answer",
        "answered_intent": "pain_presence",
        "answer_source_text": text,
        "answer_status": "answered",
        "answer_confidence": 0.99,
    }
    with patch(
        "app.services.conversation_service.complete_runtime_json",
        new=AsyncMock(return_value=json.dumps(planner_payload, ensure_ascii=False)),
    ):
        suggestion = asyncio.run(request_clarification(
            case,
            [text],
            classify_answer_fields=False,
        ))

    assert suggestion is not None
    assert suggestion.answered_intent is None
    assert suggestion.answer_status is None
    advance_conversation(case, suggestion, user_sources=[text], current_extractions=[])
    assert case.conversation_state.pending_clarification_intent == "pain_presence"
    assert case.conversation_state.clarification_evidence == {}


def test_chat_orders_semantic_classifier_department_and_planner_and_avoids_neutral_retry():
    case = pending_pain_case("phase4-live-pain-presence")
    save_case(case)
    events: list[str] = []
    semantic = AsyncMock(side_effect=lambda *_args, **_kwargs: (
        events.append("semantic") or RagTriageSuggestion(semantic_extractions=[])
    ))

    async def provider(_prompt: str, *, purpose: str) -> str:
        events.append(purpose)
        if purpose == "pending_answer_classification":
            return classifier_json(source="沒有腰痛，也沒有腹痛")
        assert purpose == "conversation_clarification"
        return json.dumps({
            "status": "sufficient",
            "question": None,
            "intent": None,
            "reason": "上一題已由 focused classifier 確認",
            "answered_intent": None,
            "answer_source_text": None,
            "answer_status": None,
            "answer_confidence": None,
        }, ensure_ascii=False)

    async def keep_department_resolved(current: TriageCase) -> None:
        events.append("department")
        current.conversation_state.department_status = "resolved"
        current.department_result = DepartmentResult(
            dept_id=1242, parentDept="內科部", childDept="腎臟科", confidence=0.94,
        )

    settings = SimpleNamespace(cerebras_api_key="test-key", batch_triage_enabled=False)
    with patch("app.routes.chat.get_settings", return_value=settings), patch(
        "app.routes.chat.refine_case_with_ai", new=semantic,
    ), patch(
        "app.services.conversation_service.complete_runtime_json", new=provider,
    ), patch(
        "app.routes.chat.reason_about_departments", new=keep_department_resolved,
    ), patch(
        "app.routes.chat.generate_triage_reply",
        new=AsyncMock(side_effect=lambda **kwargs: kwargs["fallback_reply"]),
    ):
        response = TestClient(app).post("/chat", json={
            "case_id": case.case_id,
            "message": "沒有腰痛，也沒有腹痛",
        })

    assert response.status_code == 200
    data = response.json()
    assert events == [
        "semantic",
        "pending_answer_classification",
        "department",
        "conversation_clarification",
    ]
    assert data["conversation_state"]["pending_clarification_intent"] is None
    assert data["conversation_state"]["clarification_evidence"]["pain_presence"] == "沒有腰痛，也沒有腹痛"
    assert len(data["triage_case"]["semantic_extractions"]) == 1
    assert data["next_question"] is None
    assert data["reply"] != "可以再補充和剛才問題相關的症狀細節嗎？"
