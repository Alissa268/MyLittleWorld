"""Conversation ownership regressions; all provider responses are offline proposals."""
import asyncio
import json
import logging
from unittest.mock import AsyncMock, patch

import pytest

from app.schemas import Message, PendingAnswerInterpretation, TriageCase
from app.services import conversation_service as cs, rag_triage_adapter as adapter
from app.services.case_store import save_case
from app.services.rule_engine import QUESTION_TEXTS, RED_FLAG_QUESTION_KEY
from test_phase7_natural_safety import runtime, respond, evidence, pending, route


FOCUSED = "我想確認頭暈的感受，是周圍在旋轉，還是快昏倒、眼前發黑？"
INTENT = "dizziness_quality"
TECHNICAL = "目前暫時無法可靠產生下一個問診問題，請稍後再試。"


def plan(question=FOCUSED, intent=INTENT):
    return dict(status="clarification_needed", question=question, intent=intent,
                reason="需要釐清頭暈感受", information_needs=["頭暈的感受"])


def clinical_case():
    case = TriageCase(case_id="ownership-test")
    case.patient_input.symptom = "頭暈"
    case.patient_input.red_flags_checked = True
    case.history_records = [Message(role="user", content="我頭暈"),
                            Message(role="assistant", content="你頭暈時有什麼感受？")]
    state = case.conversation_state
    state.department_status = "ambiguous"
    state.department_next_question_intent = INTENT
    state.pending_clarification_intent = INTENT
    state.asked_clarification_intents = [INTENT]
    state.next_information_needed = ["頭暈的感受"]
    return case


def act(text, kind):
    return dict(act=kind, source_text=text, confidence=0.99)


def test_complete_negative_exits_safety_before_department_and_planner(runtime):
    client, interpreter, planner, department = runtime
    first_text = "我今天頭很暈而且心情不好一直想哭。"
    respond(interpreter, [evidence("symptom", "頭暈", "頭很暈"),
        evidence("duration", "1天", "今天", None),
        evidence("accompanying_symptoms", ["一直想哭"], "一直想哭")])
    first = client.post("/chat", json=dict(message=first_text)).json()
    assert first["conversation_state"]["clarification_status"] == "safety_check"
    assert first["conversation_state"]["next_information_needed"] == []

    async def recompute(case):
        assert case.conversation_state.clarification_status == "collecting"
        assert case.conversation_state.pending_clarification_intent is None
        assert case.conversation_state.next_information_needed == []
        case.conversation_state.department_status = "ambiguous"
        case.conversation_state.department_next_question_intent = INTENT

    department.reset_mock()
    department.side_effect = recompute
    respond(interpreter, answer=pending("都沒有"))
    planner.return_value = json.dumps(plan(), ensure_ascii=False)
    second = client.post("/chat", json=dict(case_id=first["case_id"], message="都沒有。")).json()
    department.assert_awaited_once()
    state = second["conversation_state"]
    assert second["triage_case"]["patient_input"]["red_flags_status"] == "negative"
    assert state["clarification_status"] == "clarifying"
    assert state["pending_clarification_intent"] == INTENT
    assert second["reply"] == FOCUSED
    assert "安全篩檢" not in "".join(state["next_information_needed"])
    assert '"safety_planning": false' in planner.await_args.args[0]


def test_safety_scope_clears_stale_pending_and_resets_only_fresh_attempts():
    case = clinical_case()
    case.patient_input.red_flags_checked = False
    case.conversation_state.safety_answer_attempts = 2
    case.conversation_state.safety_answer_validation = "old_cycle"
    cs._enter_safety_check(case)
    state = case.conversation_state
    assert state.pending_clarification_intent is None
    assert state.next_information_needed == []
    assert state.safety_answer_attempts == 0
    assert state.safety_answer_validation is None
    answer = PendingAnswerInterpretation(**pending("沒有胸痛，但其他我不知道。", "partial", "uncertain"))
    assert not cs.capture_safety_screen_answer(case, answer, [answer.answer_source_text])
    cs.continue_safety_check(case, None)
    assert state.safety_answer_attempts == 1
    assert not case.patient_input.red_flags_checked
    assert state.pending_clarification_intent is None
    answer = PendingAnswerInterpretation(**pending("都沒有"))
    assert cs.capture_safety_screen_answer(case, answer, ["都沒有"])
    assert state.clarification_status == "collecting"
    assert state.last_question_key is None
    assert state.department_next_question_intent is None


@pytest.mark.parametrize("kind,text", [
    ("request_explanation", "我聽不懂，你要問什麼細節？"),
    ("claims_already_answered", "我說過了"),
])
def test_grounded_dialogue_act_reaches_focused_planner_without_fabrication(runtime, kind, text):
    client, interpreter, planner, department = runtime
    case = clinical_case()
    save_case(case)
    interpreter.return_value = json.dumps(dict(semantic_extractions=[], ttas_evidence=[],
        pending_answer=None, dialogue_act=act(text, kind)), ensure_ascii=False)
    planner.return_value = json.dumps(plan(), ensure_ascii=False)
    result = client.post("/chat", json=dict(case_id=case.case_id, message=text)).json()
    assert result["reply"] == FOCUSED
    assert result["triage_case"]["semantic_extractions"] == []
    assert result["conversation_state"]["clarification_evidence"] == {}
    assert f'"act": "{kind}"' in planner.await_args.args[0]
    interpreter.assert_awaited_once()


def test_claims_already_answered_uses_accepted_answer_before_recomputation(runtime):
    client, interpreter, planner, department = runtime
    case = clinical_case()
    case.history_records.append(Message(role="user", content="房子在轉"))
    case.conversation_state.clarification_evidence[INTENT] = "房子在轉"
    save_case(case)

    async def recompute(current):
        assert current.conversation_state.pending_clarification_intent is None
        current.conversation_state.department_next_question_intent = "hearing_changes"

    department.side_effect = recompute
    interpreter.return_value = json.dumps(dict(semantic_extractions=[], ttas_evidence=[],
        pending_answer=None, dialogue_act=act("我說過了", "claims_already_answered")))
    planner.return_value = json.dumps(plan("頭暈時聽到的聲音有變化嗎？", "hearing_changes"))
    result = client.post("/chat", json=dict(case_id=case.case_id, message="我說過了")).json()
    department.assert_awaited_once()
    assert result["conversation_state"]["pending_clarification_intent"] == "hearing_changes"
    assert result["conversation_state"]["clarification_evidence"][INTENT] == "房子在轉"


def test_invalid_question_repaired_once_with_structured_reason():
    case = clinical_case()
    invalid = plan("你確診了，現在要掛哪個科別？")
    provider = AsyncMock(side_effect=[json.dumps(invalid), json.dumps(plan())])
    with patch.object(cs, "complete_runtime_json", provider):
        suggestion = asyncio.run(cs.request_clarification(case, ["不懂"], classify_answer_fields=False))
    assert provider.await_count == 2
    assert suggestion.question == FOCUSED
    assert suggestion.reply_source == "AI_REPAIR"
    repair = provider.await_args.args[0]
    for key in ("rejection_reason", "original_proposal", "required_next_question_intent",
                "grounded_patient_evidence", "pending_question", "dialogue_act"):
        assert key in repair


def test_invalid_repair_is_transparent_technical_failure_not_normal_retry():
    case = clinical_case()
    provider = AsyncMock(return_value=json.dumps(plan("你確診了，現在要掛哪個科別？")))
    with patch.object(cs, "complete_runtime_json", provider):
        suggestion = asyncio.run(cs.request_clarification(case, ["不懂"], classify_answer_fields=False))
    cs.advance_conversation(case, suggestion, user_sources=["不懂"], current_extractions=[])
    assert provider.await_count == 2
    assert case.triage.next_question == TECHNICAL
    assert not case.conversation_state.confirmed
    assert case.conversation_state.department_status == "ambiguous"
    assert case.semantic_extractions == []


def test_answered_intent_different_wording_cannot_be_reasked():
    case = clinical_case()
    case.conversation_state.clarification_evidence[INTENT] = "房子在轉"
    case.conversation_state.pending_clarification_intent = None
    provider = AsyncMock(return_value=json.dumps(plan("暈的時候會覺得周圍旋轉嗎？")))
    with patch.object(cs, "complete_runtime_json", provider):
        suggestion = asyncio.run(cs.request_clarification(case, ["我說過了"], classify_answer_fields=False))
    cs.advance_conversation(case, suggestion, user_sources=["我說過了"], current_extractions=[])
    assert suggestion.question is None
    assert suggestion.rejection_reason == "intent_already_answered"
    assert case.conversation_state.pending_clarification_intent is None
    assert case.triage.next_question == TECHNICAL


@pytest.mark.parametrize("question", [
    "你說的頭暈，是否像周圍在旋轉？",
    "頭暈和周圍旋轉的感覺，是同時出現嗎？",
])
def test_single_focused_question_with_normal_chinese_connector_is_valid(question):
    provider = AsyncMock(return_value=json.dumps(plan(question)))
    with patch.object(cs, "complete_runtime_json", provider):
        suggestion = asyncio.run(cs.request_clarification(clinical_case(), ["不確定"], focused_answer_status="partial"))
    assert suggestion.question == question
    provider.assert_awaited_once()


@pytest.mark.parametrize("question", ["程度如何，另外是否有疼痛？", "程度如何？持續多久？"])
def test_multiple_independent_requests_still_rejected(question):
    provider = AsyncMock(return_value=json.dumps(plan(question)))
    with patch.object(cs, "complete_runtime_json", provider):
        suggestion = asyncio.run(cs.request_clarification(clinical_case(), ["不確定"]))
    assert suggestion.question is None


@pytest.mark.parametrize("proposal", [
    act("原文不存在", "request_explanation"), act("不懂", "workflow_done"),
    {**act("不懂", "answer"), "confidence": float("nan")},
    {**act("不懂", "answer"), "confidence": True},
    {**act("不懂", "answer"), "confidence": 0.1},
    {**act("不懂", "answer"), "stage": "done"},
])
def test_invalid_dialogue_act_becomes_unknown(proposal):
    provider = AsyncMock(return_value=json.dumps(dict(semantic_extractions=[], dialogue_act=proposal)))
    with patch.object(adapter, "_ai_available", return_value=True), patch.object(adapter, "complete_prompt", provider):
        result = asyncio.run(adapter.refine_case_with_ai(clinical_case(), user_sources=["不懂"]))
    assert result.dialogue_act.act == "unknown"
    assert not clinical_case().conversation_state.confirmed


def test_explanation_act_and_new_medical_evidence_coexist(runtime):
    client, interpreter, planner, _ = runtime
    case = clinical_case()
    save_case(case)
    text = "我聽不懂，不過我還有一點耳鳴"
    interpreter.return_value = json.dumps(dict(semantic_extractions=[evidence(
        "accompanying_symptoms", ["耳鳴"], "還有一點耳鳴")], ttas_evidence=[],
        pending_answer=None, dialogue_act=act("我聽不懂", "request_explanation")))
    planner.return_value = json.dumps(plan())
    result = client.post("/chat", json=dict(case_id=case.case_id, message=text)).json()
    assert result["triage_case"]["semantic_extractions"][0]["normalized_value"] == ["耳鳴"]
    assert '"act": "request_explanation"' in planner.await_args.args[0]


def test_turn_log_has_ownership_and_reply_origin_but_no_patient_text(runtime, caplog):
    client, interpreter, planner, _ = runtime
    case = clinical_case()
    save_case(case)
    text = "SENTINEL_PRIVATE_我聽不懂"
    interpreter.return_value = json.dumps(dict(semantic_extractions=[], dialogue_act=act(text, "request_explanation")))
    planner.return_value = json.dumps(plan())
    with caplog.at_level(logging.INFO):
        client.post("/chat", json=dict(case_id=case.case_id, message=text))
    assert text not in caplog.text
    for field in ("clarification_before", "clarification_after", "pending_before", "pending_after",
                  "safety_completed_this_turn", "dialogue_act", "final_reply_source=AI_PLANNER"):
        assert field in caplog.text


def test_live_four_turn_sequence_never_rebinds_safety_need(runtime):
    client, interpreter, planner, department = runtime
    text = "我今天頭很暈而且心情不好一直想哭。"
    respond(interpreter, [evidence("symptom", "頭暈", "頭很暈"), evidence("duration", "1天", "今天", None)])
    first = client.post("/chat", json=dict(message=text)).json()
    case_id = first["case_id"]

    async def recompute(case):
        assert case.conversation_state.clarification_status != "safety_check"
        case.conversation_state.department_status = "ambiguous"
        case.conversation_state.department_next_question_intent = INTENT

    department.side_effect = recompute
    for text, interpretation, question in [
        ("都沒有。", dict(pending_answer=pending("都沒有"), dialogue_act=act("都沒有", "answer")), FOCUSED),
        ("我聽不懂，你要問什麼細節？", dict(pending_answer=None, dialogue_act=act("我聽不懂", "request_explanation")),
         "我想了解頭暈時周圍景物是否像在轉動？"),
        ("我說過了……", dict(pending_answer=None, dialogue_act=act("我說過了", "claims_already_answered")),
         "我還需要確認暈的感覺，是景物轉動還是眼前發黑？"),
    ]:
        interpreter.return_value = json.dumps(dict(semantic_extractions=[], ttas_evidence=[], **interpretation))
        planner.return_value = json.dumps(plan(question))
        result = client.post("/chat", json=dict(case_id=case_id, message=text)).json()
        assert result["reply"] == question
        assert result["conversation_state"]["pending_clarification_intent"] == INTENT
        assert result["conversation_state"]["next_information_needed"] == ["頭暈的感受"]
        assert result["triage_case"]["patient_input"]["red_flags_checked"]
        assert result["conversation_state"]["clarification_evidence"] == {}
        assert result["conversation_state"]["confirmed"] is False
    assert interpreter.await_count == 4


def test_partial_same_intent_uses_concrete_ai_question_without_clearing_answer(runtime):
    client, interpreter, planner, _ = runtime
    case = clinical_case()
    save_case(case)
    text = "好像有點轉但不確定"
    interpreter.return_value = json.dumps(dict(semantic_extractions=[], ttas_evidence=[],
        dialogue_act=act(text, "answer"), pending_answer=dict(answered_intent=INTENT,
            answer_status="partial", answer_source_text=text, answer_confidence=0.98)))
    planner.return_value = json.dumps(plan())
    result = client.post("/chat", json=dict(case_id=case.case_id, message=text)).json()
    assert result["reply"] == FOCUSED
    assert result["conversation_state"]["pending_clarification_intent"] == INTENT
    assert result["conversation_state"]["clarification_evidence"] == {}


def test_fresh_revision_safety_cycle_does_not_inherit_old_attempt_cap(runtime):
    client, interpreter, _, _ = runtime
    case = clinical_case()
    case.patient_input.red_flags_checked = False
    case.conversation_state.clarification_status = "unresolved"
    case.conversation_state.safety_answer_attempts = cs.SAFETY_ANSWER_CAP
    save_case(case)
    client.post("/chat", json=dict(case_id=case.case_id, revision_requested=True))
    respond(interpreter, [evidence("symptom", "頭暈", "頭暈"), evidence("duration", "3天", "3天", None)])
    fresh = client.post("/chat", json=dict(case_id=case.case_id, message="頭暈3天")).json()
    assert fresh["conversation_state"]["clarification_status"] == "safety_check"
    assert fresh["conversation_state"]["safety_answer_attempts"] == 0
    respond(interpreter, answer=pending("不確定", "unclear", "uncertain"))
    retry = client.post("/chat", json=dict(case_id=case.case_id, message="不確定")).json()
    assert retry["conversation_state"]["safety_answer_attempts"] == 1
    assert not retry["triage_case"]["patient_input"]["red_flags_checked"]


def test_fresh_reasoning_may_propose_pre_safety_unanswered_intent_but_not_restore_automatically():
    case = clinical_case()
    case.patient_input.red_flags_checked = False
    cs._enter_safety_check(case)
    assert cs.capture_safety_screen_answer(case, PendingAnswerInterpretation(**pending("都沒有")), ["都沒有"])
    assert case.conversation_state.pending_clarification_intent is None
    assert case.conversation_state.department_next_question_intent is None
    case.conversation_state.department_next_question_intent = INTENT  # Fresh reasoning result.
    cs.advance_conversation(case, cs.ClarificationSuggestion("clarification_needed", FOCUSED, INTENT, "需確認感受"),
                            user_sources=["都沒有"], current_extractions=[])
    assert case.triage.next_question == FOCUSED
    assert case.conversation_state.pending_clarification_intent == INTENT
    assert case.conversation_state.asked_clarification_intents == [INTENT]


def test_technical_reply_does_not_replace_actual_pending_question_for_interpreter():
    case = clinical_case()
    cs.advance_conversation(case, cs.ClarificationSuggestion("clarification_needed", FOCUSED, INTENT, "需確認感受"),
                            user_sources=["不確定"], current_extractions=[])
    cs.advance_conversation(case, None, user_sources=["我不懂"], current_extractions=[])
    case.history_records.append(Message(role="assistant", content=TECHNICAL))
    prompt = adapter._build_symptom_collection_prompt(case, ["再試一次"])
    assert f'"pending_question": "{FOCUSED}"' in prompt


@pytest.mark.parametrize("invalid", ["{broken", [], dict(status=[], reason="invalid"),
    {**plan(), "information_needs": ["感受", "持續時間"]},
    {**plan(), "stage": "done"}, plan("可以再補充和剛才問題相關的症狀細節嗎？"),
    plan("是否直接改看耳科？")])
def test_malformed_and_untrusted_planner_never_gain_state_authority(invalid):
    raw = invalid if isinstance(invalid, str) else json.dumps(invalid)
    provider = AsyncMock(return_value=raw)
    case = clinical_case()
    with patch.object(cs, "complete_runtime_json", provider):
        suggestion = asyncio.run(cs.request_clarification(case, ["不懂"], classify_answer_fields=False))
    cs.advance_conversation(case, suggestion, user_sources=["不懂"], current_extractions=[])
    assert case.triage.next_question == TECHNICAL
    assert not case.conversation_state.confirmed
    assert provider.await_count == 2


@pytest.mark.parametrize("field,value", [("answer_status", []), ("answer_assertion", [])])
def test_pending_answer_malformed_enum_fails_closed(field, value):
    item = dict(answered_intent=INTENT, answer_status="answered", answer_source_text="不懂", answer_confidence=0.99)
    item[field] = value
    assert adapter._pending_answer_from_ai(item, pending_intent=INTENT, user_sources=["不懂"]) is None


def test_explicit_revision_releases_answered_intent_dedupe_only_through_backend():
    case = clinical_case()
    case.conversation_state.clarification_evidence[INTENT] = "房子在轉"
    route._begin_revision(case)
    case.conversation_state.department_status = "ambiguous"
    case.conversation_state.department_next_question_intent = INTENT
    provider = AsyncMock(return_value=json.dumps(plan()))
    with patch.object(cs, "complete_runtime_json", provider):
        suggestion = asyncio.run(cs.request_clarification(case, ["更新資訊"], classify_answer_fields=False))
    cs.advance_conversation(case, suggestion, user_sources=["更新資訊"], current_extractions=[])
    assert case.triage.next_question == FOCUSED
    assert case.conversation_state.pending_clarification_intent == INTENT
