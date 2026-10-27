"""Legacy keys are correlation metadata, not authority to parse free text."""
import asyncio
import json
from unittest.mock import AsyncMock, patch

import pytest

from app.schemas import BatchAnswer, TriageCase
from app.services import batch_extraction_service as batch, department_preference_service as preference
from app.services import rag_triage_adapter as adapter
from app.services.rule_engine import apply_user_message, mark_questions_asked, missing_checklist_fields
from test_semantic_ai_validation import extraction, payload


@pytest.mark.parametrize("field", ["symptom", "body_part", "duration", "severity"])
@pytest.mark.parametrize("text", ["我要看皮膚科", "星期二下午可以", "我說過了", "不是舊症狀，是新症狀", "三天，很痛，右腳", "不知道"])
def test_keyed_raw_text_never_becomes_a_medical_value_without_ai(field, text):
    case = TriageCase(case_id="strict-no-parser")
    with patch.object(batch, "runtime_ai_available", return_value=False):
        result = asyncio.run(batch.extract_batch_answers(case, [BatchAnswer(key=field, answer=text)]))
    assert not getattr(case.patient_input, field)
    assert field in result.unresolved_fields
    assert not result.accepted_fields
    assert not case.patient_input.red_flags_checked


@pytest.mark.parametrize("text", ["都沒有", "不知道", "沒有胸痛，但其他不知道", "突然胸痛", "我說過了"])
def test_raw_safety_phrases_cannot_create_a_positive_or_negative_state(text):
    case = TriageCase(case_id="strict-safety")
    with patch.object(batch, "runtime_ai_available", return_value=False):
        asyncio.run(batch.extract_batch_answers(case, [BatchAnswer(key="red_flags", answer=text)]))
    assert not case.patient_input.red_flags_checked
    assert not case.patient_input.red_flags
    assert case.patient_input.red_flags_status == "not_checked"


@pytest.mark.parametrize("key,text", [("preferred_days", "週三不行其他都可以"), ("preferred_sessions", "都可以"),
    ("preferred_sessions", "晚上起床會痛"), ("preferred_days", "明天上午"), ("severity", "還好吧但是有時很痛")])
def test_unstructured_choices_require_ai_even_when_a_legacy_key_is_present(key, text):
    case = TriageCase(case_id="strict-choices")
    provider = AsyncMock(return_value=payload([], text))
    provider.return_value = json.dumps({**provider.return_value,
        "dialogue_act": {"act": "unknown", "source_text": text, "confidence": 0.99}})
    with patch.object(batch, "runtime_ai_available", return_value=True), patch.object(adapter, "_ai_available", return_value=True), patch.object(
        adapter, "complete_prompt", provider,
    ):
        outcome = asyncio.run(batch.extract_batch_answers(case, [BatchAnswer(key=key, answer=text)]))
    provider.assert_awaited_once()
    assert outcome.accepted_fields == []
    assert not case.availability.preferred_days
    assert not case.availability.preferred_sessions


@pytest.mark.parametrize("field,value,text", [
    ("duration", "1週", "上禮拜開始"), ("duration", "6個月", "半年了吧"),
    ("severity", "mild", "不太影響上班"), ("body_part", "右腳腳背", "右腳腳背腫痛"),
    ("body_part", "左臀部", "左臀部酸酸的"), ("accompanying_symptoms", ["耳鳴"], "耳朵嗡嗡叫"),
])
def test_ai_fact_can_arrive_out_of_order_without_rule_compression(field, value, text):
    case = TriageCase(case_id="strict-ai-out-of-order")
    provider = AsyncMock(return_value=json.dumps(payload([extraction(field, value, text)], text)))
    with patch.object(batch, "runtime_ai_available", return_value=True), patch.object(adapter, "_ai_available", return_value=True), patch.object(
        adapter, "complete_prompt", provider,
    ):
        result = asyncio.run(batch.extract_batch_answers(case, [BatchAnswer(key="symptom", answer=text)]))
    provider.assert_awaited_once()
    assert getattr(case.patient_input, field) == value
    assert field in result.ai_fields
    assert case.availability.preferred_sessions == []


@pytest.mark.parametrize("status", ["unknown", "ambiguous"])
def test_unknown_semantic_state_never_fills_patient_value(status):
    case = TriageCase(case_id="strict-unknown")
    text = "不確定"
    item = extraction("body_part", None, text)
    item["semantic_status"] = status
    provider = AsyncMock(return_value=json.dumps({**payload([item], text),
        "dialogue_act": {"act": "unknown", "source_text": text, "confidence": 0.99}}))
    with patch.object(batch, "runtime_ai_available", return_value=True), patch.object(adapter, "_ai_available", return_value=True), patch.object(
        adapter, "complete_prompt", provider,
    ):
        asyncio.run(batch.extract_batch_answers(case, [BatchAnswer(key="body_part", answer=text)]))
    assert case.patient_input.body_part is None


def test_retry_skip_unknown_never_pollutes_patient_value():
    case = TriageCase(case_id="strict-workflow")
    case.conversation_state.field_statuses["symptom"] = "unknown"
    case.conversation_state.field_confidence["symptom"] = 0.0
    mark_questions_asked(case, ["symptom", "symptom"])
    missing_checklist_fields(case)
    assert not case.patient_input.symptom
    assert case.conversation_state.field_statuses["symptom"] == "unknown"


def test_duplicate_names_do_not_require_alias_guessing_when_ai_selects_exact_id():
    rows = [
        {"dept_id": 204, "parent_dept": "其他科", "child_dept": "皮膚科"},
        {"dept_id": 304, "parent_dept": "特別門診", "child_dept": "皮膚科"},
    ]
    with patch.object(preference, "fetch_active_departments", return_value=rows):
        accepted = preference.validate_department_preference({
            "dept_id": 304, "department_name": "皮膚科", "source_text": "我要看皮膚科", "confidence": 0.99,
        }, ["我要看皮膚科"])
    assert accepted is not None
    assert accepted.dept_id == 304
    assert accepted.department_name == "皮膚科"
