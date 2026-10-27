"""Validated dialogue act diagnostics and planner explanations; all AI responses are mocked."""
import json

import pytest

from app.schemas import DialogueActInterpretation
from app.services import conversation_service as cs
from app.services.case_store import save_case
from test_natural_chat_state_repair import act, clinical_case, plan
from test_phase7_natural_safety import runtime  # noqa: F401


def interpret(interpreter, dialogue_act):
    interpreter.return_value = json.dumps(dict(
        semantic_extractions=[], pending_answer=None, ttas_evidence=[], time_preferences=[],
        prior_answer_reference=None, department_preference=None, dialogue_act=dialogue_act,
    ), ensure_ascii=False)


def test_validated_dialogue_act_is_exposed_in_chat_response(runtime):
    client, interpreter, planner, _ = runtime
    case = clinical_case()
    save_case(case)
    interpret(interpreter, act("我聽不懂", "request_explanation"))
    planner.return_value = json.dumps(plan(), ensure_ascii=False)
    result = client.post("/chat", json=dict(case_id=case.case_id, message="我聽不懂")).json()
    state = result["conversation_state"]
    assert state["last_dialogue_act"] == "request_explanation"
    assert "我聽不懂" not in json.dumps(state, ensure_ascii=False)


@pytest.mark.parametrize("dialogue_act", [
    {**act("我聽不懂", "request_explanation"), "confidence": 0.1},
    act("本輪沒有這段原文", "request_explanation"),
])
def test_low_confidence_or_ungrounded_act_is_not_recorded(runtime, dialogue_act):
    client, interpreter, planner, _ = runtime
    case = clinical_case()
    save_case(case)
    interpret(interpreter, dialogue_act)
    planner.return_value = json.dumps(plan(), ensure_ascii=False)
    result = client.post("/chat", json=dict(case_id=case.case_id, message="我聽不懂")).json()
    assert "last_dialogue_act" in result["conversation_state"]
    assert result["conversation_state"]["last_dialogue_act"] is None


def test_previous_turn_act_is_cleared_when_this_turn_has_none(runtime):
    client, _, _, _ = runtime
    case = clinical_case()
    case.conversation_state.last_dialogue_act = "request_explanation"
    save_case(case)
    result = client.post("/chat", json=dict(case_id=case.case_id)).json()
    assert result["conversation_state"]["last_dialogue_act"] is None


@pytest.mark.parametrize("dialogue_act,sources", [
    (None, ["我聽不懂"]),
    (DialogueActInterpretation(act="request_explanation", source_text="我聽不懂", confidence=0.1), ["我聽不懂"]),
    (DialogueActInterpretation(act="request_explanation", source_text="原文不存在", confidence=0.99), ["我聽不懂"]),
    (DialogueActInterpretation(act="request_explanation", source_text=" ", confidence=0.99), ["我聽不懂"]),
])
def test_validated_dialogue_act_rejects_unreliable_acts(dialogue_act, sources):
    assert cs.validated_dialogue_act(dialogue_act, sources) is None


def test_validated_dialogue_act_accepts_grounded_confident_act():
    value = DialogueActInterpretation(act="request_explanation", source_text="聽不懂", confidence=0.99)
    assert cs.validated_dialogue_act(value, ["我聽不懂"]) == "request_explanation"
