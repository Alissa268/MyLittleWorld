"""Architecture boundaries, exercised offline without patient-text parsers."""
import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from app.schemas import BatchAnswer, Message, TriageCase
from app.services import batch_extraction_service as batch, conversation_service as cs
from app.services import department_preference_service as preference, rag_triage_adapter as adapter
from app.services.rule_engine import apply_user_message
from app.services.case_store import save_case
from test_phase7_natural_safety import runtime, evidence, pending, respond


ACTIVE = [{"dept_id": 1333, "parent_dept": "耳鼻喉部", "child_dept": "耳科"}]
QUESTION = "我想確認這次頭痛是否突然開始？"


@pytest.mark.parametrize("success", [True, False])
def test_safety_explanation_has_ai_question_or_technical_failure(runtime, success):
    client, interpreter, planner, department = runtime
    case = TriageCase(case_id=f"cleanup-explanation-{success}")
    case.patient_input.symptom = "頭痛"
    case.history_records = [Message(role="user", content="頭痛")]
    cs._enter_safety_check(case)
    save_case(case)
    text = "我有點搞不懂，你現在主要是想確認什麼狀況？"
    interpreter.return_value = json.dumps(dict(semantic_extractions=[], pending_answer=None,
        ttas_evidence=[], time_preferences=[], prior_answer_reference=None, department_preference=None,
        dialogue_act=dict(act="request_explanation", source_text=text, confidence=0.99)))
    planner.return_value = json.dumps(dict(status="clarification_needed", intent="safety_screen",
        question=QUESTION if success else "", reason="安全資訊尚未確認", information_needs=["頭痛發作方式"]))
    result = client.post("/chat", json={"case_id": case.case_id, "message": text}).json()
    state = result["conversation_state"]
    assert state["safety_answer_attempts"] == 0
    assert state["clarification_status"] == "safety_check"
    assert not result["triage_case"]["patient_input"]["red_flags_checked"]
    assert result["reply"] == (QUESTION if success else cs.TECHNICAL_CLARIFICATION_REPLY)
    assert state["reply_source"] == ("AI_PLANNER" if success else "TECHNICAL_FALLBACK")
    assert planner.await_count == (1 if success else 2)
    department.assert_not_awaited()


def test_unclear_medical_safety_answer_still_uses_three_attempt_cap(runtime):
    client, interpreter, planner, department = runtime
    case = TriageCase(case_id="cleanup-medical-cap")
    case.patient_input.symptom = "頭痛"
    cs._enter_safety_check(case)
    save_case(case)
    planner.return_value = "{}"
    for attempt in range(1, 4):
        respond(interpreter, answer=pending("不知道", "unclear", "uncertain"))
        result = client.post("/chat", json={"case_id": case.case_id, "message": "不知道"}).json()
        assert result["conversation_state"]["safety_answer_attempts"] == attempt
        assert not result["triage_case"]["patient_input"]["red_flags_checked"]
        if attempt < 3:
            assert result["reply"] == cs.SAFETY_RETRY_REPLY
    assert result["conversation_state"]["clarification_status"] == "unresolved"
    department.assert_not_awaited()


def test_safety_explanation_provider_failure_repairs_once_then_technical(runtime):
    client, interpreter, planner, department = runtime
    case = TriageCase(case_id="cleanup-explanation-timeout")
    cs._enter_safety_check(case)
    save_case(case)
    text = "請解釋這個問題"
    interpreter.return_value = json.dumps(dict(semantic_extractions=[], ttas_evidence=[], pending_answer=None,
        time_preferences=[], prior_answer_reference=None, department_preference=None,
        dialogue_act=dict(act="request_explanation", source_text=text, confidence=0.99)))
    planner.side_effect = TimeoutError("offline timeout")
    result = client.post("/chat", json={"case_id": case.case_id, "message": text}).json()
    assert planner.await_count == 2
    assert result["reply"] == cs.TECHNICAL_CLARIFICATION_REPLY
    assert result["conversation_state"]["reply_source"] == "TECHNICAL_FALLBACK"
    assert result["conversation_state"]["question_repair_attempted"] is True
    assert result["conversation_state"]["safety_answer_attempts"] == 0
    department.assert_not_awaited()


@pytest.mark.parametrize("text", ["麻煩幫我安排耳科", "我想改指定耳科看看"])
@pytest.mark.parametrize("variant", ["valid", "null", "fake", "mismatch", "inactive", "duplicate", "ungrounded", "low", "extra", "string_id"])
def test_department_preference_requires_grounded_ai_and_current_exact_active_identity(text, variant):
    case = TriageCase(case_id="cleanup-preference")
    case.history_records.append(Message(role="user", content=text))
    value = dict(dept_id=1333, department_name="耳科", source_text=text, confidence=0.99)
    rows = ACTIVE
    if variant == "null": value = None
    elif variant == "fake": value["dept_id"] = 9999
    elif variant == "mismatch": value["department_name"] = "耳鼻喉科"
    elif variant == "inactive": rows = []
    elif variant == "duplicate": rows = ACTIVE * 2
    elif variant == "ungrounded": value["source_text"] = "不存在的原文"
    elif variant == "low": value["confidence"] = 0.1
    elif variant == "extra": value["confirmed"] = True
    elif variant == "string_id": value["dept_id"] = "1333"
    payload = dict(semantic_extractions=[], ttas_evidence=[], pending_answer=None, time_preferences=[],
        dialogue_act=dict(act="unknown" if value is None else "new_information", source_text=text, confidence=0.99),
        prior_answer_reference=None, department_preference=value)
    with patch.object(adapter, "_ai_available", return_value=True), patch.object(
        adapter, "complete_prompt", AsyncMock(return_value=json.dumps(payload)),
    ) as ai, patch.object(preference, "fetch_active_departments", return_value=rows):
        result = asyncio.run(adapter.refine_case_with_ai(case, user_sources=[text]))
    assert result is not None
    ai.assert_awaited_once()
    assert case.patient_input.requested_department_id == (1333 if variant == "valid" else None)
    assert case.patient_input.requested_department_name == ("耳科" if variant == "valid" else None)
    assert not case.patient_input.red_flags_checked
    assert case.department_result is None
    assert "department_preference" in ai.await_args.args[0]


@pytest.mark.parametrize("text", ["頭暈想看一般內科", "右腳痛三天很嚴重", "沒有胸痛，我說錯了", "下週二下午可以"])
def test_history_recording_does_not_interpret_any_patient_text(text):
    case = TriageCase(case_id="cleanup-raw")
    before = case.patient_input.model_dump()
    apply_user_message(case, text, semantic_first=False)
    assert case.patient_input.model_dump() == before
    assert case.semantic_extractions == []
    assert case.availability.preferred_days == []
    assert case.history_records[-1].content == text


@pytest.mark.parametrize("key,text", [("symptom", "我頭很暈"), ("body_part", "右腳腳背"),
    ("duration", "已經三天"), ("severity", "痛到睡不著")])
def test_batch_free_text_unavailable_fails_closed(key, text):
    case = TriageCase(case_id="cleanup-batch")
    with patch.object(batch, "runtime_ai_available", return_value=False):
        result = asyncio.run(batch.extract_batch_answers(case, [BatchAnswer(key=key, answer=text)]))
    assert not getattr(case.patient_input, key)
    assert not result.accepted_fields
    assert key in result.unresolved_fields


def test_no_retired_patient_semantic_modules_or_production_callers():
    app = Path(__file__).parents[1] / "app"
    for name in ("project_smart_department_adapter", "semantic_normalizer", "semantic_refinement_gate", "negation_utils"):
        assert not (app / "services" / f"{name}.py").exists()
    retired = ("capture_department_preference", "detect_department_result", "_rule_based_department",
        "strip_negated_red_flags", "has_symptom_semantics", "plausible_semantic_target",
        "FALLBACK_QUESTIONS", "NEUTRAL_PENDING_RETRIES")
    for source in app.rglob("*.py"):
        content = source.read_text(encoding="utf-8-sig")
        for symbol in retired:
            assert symbol not in content, (source, symbol)


def test_department_insufficiency_never_selects_from_keywords():
    from app.services import department_reasoning_service as reasoning
    case = TriageCase(case_id="cleanup-no-keywords")
    apply_user_message(case, "膝蓋痛，頭暈，想看耳科")
    with patch.object(reasoning, "fetch_active_departments", return_value=ACTIVE), patch.object(
        reasoning, "complete_runtime_json", AsyncMock(side_effect=RuntimeError("offline")),
    ):
        asyncio.run(reasoning.reason_about_departments(case))
    assert case.department_result is None
    assert case.conversation_state.department_status in {"unresolved", "ambiguous"}


def test_preference_repair_contract_includes_new_nullable_field():
    prompt = adapter._build_turn_interpretation_repair_prompt("original", "invalid")
    assert "department_preference" in prompt


def test_all_full_interpreter_examples_include_nullable_department_preference():
    with patch.object(preference, "fetch_active_departments", return_value=ACTIVE):
        prompt = adapter._build_symptom_collection_prompt(TriageCase(case_id="prompt"), ["請幫我安排耳科"])
    assert prompt.count('"department_preference": null') == prompt.count('"prior_answer_reference": null')
    assert '"dept_id": 1333' in prompt


def test_changed_active_identity_is_rechecked_after_interpretation():
    case = TriageCase(case_id="stale-department")
    text = "請幫我安排耳科"
    data = dict(semantic_extractions=[], pending_answer=None, ttas_evidence=[], time_preferences=[],
        dialogue_act=dict(act="new_information", source_text=text, confidence=0.99), prior_answer_reference=None,
        department_preference=dict(dept_id=1333, department_name="耳科", source_text=text, confidence=0.99))
    with patch.object(preference, "fetch_active_departments", side_effect=[ACTIVE, []]) as db, patch.object(
        adapter, "_ai_available", return_value=True,
    ), patch.object(adapter, "complete_prompt", AsyncMock(return_value=json.dumps(data))):
        asyncio.run(adapter.refine_case_with_ai(case, user_sources=[text]))
    assert db.call_count == 2
    assert case.patient_input.requested_department_id is None


def test_inactive_row_and_database_failure_cannot_accept_preference():
    value = dict(dept_id=1333, department_name="耳科", source_text="耳科", confidence=0.99)
    for rows in [[dict(ACTIVE[0], is_active=False)], RuntimeError("offline DB unavailable")]:
        case = TriageCase(case_id="inactive")
        with patch.object(preference, "fetch_active_departments", side_effect=rows if isinstance(rows, Exception) else None,
                          return_value=rows if isinstance(rows, list) else None):
            assert preference.accept_department_preference(case, value, ["耳科"]) is None
        assert case.patient_input.requested_department_id is None
