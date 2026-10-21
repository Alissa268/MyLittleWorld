"""Progress extends ordinary clarification without weakening completion/safety gates."""
import json

import pytest

from app.schemas import DepartmentResult, Message, TriageCase
from app.services import conversation_service as cs
from app.services.case_store import save_case
from test_phase7_natural_safety import runtime, respond, evidence, route


def clinical_case(turns=7, stalls=0):
    case = TriageCase(case_id="progress-limit-test")
    case.patient_input.symptom = "腹痛"
    case.patient_input.red_flags_checked = True
    case.patient_input.red_flags_status = "negative"
    case.history_records = [Message(role="user", content="腹痛")]
    state = case.conversation_state
    state.turn_count = turns
    state.consecutive_no_progress_turns = stalls
    state.clarification_status = "clarifying"
    state.department_status = "ambiguous"
    state.pending_clarification_intent = "nausea_presence"
    state.pending_clarification_question = "腹痛時是否有噁心？"
    state.asked_clarification_intents = ["nausea_presence"]
    return case


def next_plan():
    return dict(status="clarification_needed", question="疼痛主要在腹部哪個位置？",
                intent="pain_location", reason="需要確認位置", information_needs=["腹痛的位置"])


@pytest.mark.parametrize("turns", [7, 8, 12])
@pytest.mark.parametrize("text", ["no", "沒有"])
def test_accepted_pending_answer_continues_past_eighth_turn(runtime, turns, text):
    client, interpreter, planner, _ = runtime
    case = clinical_case(turns, stalls=6)
    save_case(case)
    respond(interpreter, answer=dict(answered_intent="nausea_presence", answer_status="answered",
                                    answer_source_text=text, answer_confidence=0.99))
    planner.return_value = json.dumps(next_plan())
    response = client.post("/chat", json=dict(case_id=case.case_id, message=text))
    assert response.status_code == 200
    result = response.json()
    state = result["conversation_state"]
    assert state["turn_count"] == turns + 1
    assert state["consecutive_no_progress_turns"] == 0
    assert state["clarification_evidence"]["nausea_presence"] == text
    assert state["clarification_status"] == "clarifying"
    assert result["next_question"] == next_plan()["question"]
    assert result["needMoreInfo"] is True
    assert state["confirmed"] is False
    assert state["is_complete"] is False


def test_new_accepted_medical_fact_counts_as_progress(runtime):
    client, interpreter, _, _ = runtime
    case = clinical_case(stalls=7)
    save_case(case)
    respond(interpreter, [evidence("onset", "gradual", "gradual", None)])
    result = client.post("/chat", json=dict(case_id=case.case_id, message="gradual")).json()
    assert result["conversation_state"]["consecutive_no_progress_turns"] == 0
    assert result["conversation_state"]["clarification_status"] != "unresolved"


def test_repeated_fact_does_not_keep_resetting_stall_counter(runtime):
    client, interpreter, _, _ = runtime
    case = clinical_case(turns=8, stalls=6)
    save_case(case)
    respond(interpreter, [evidence("onset", "gradual", "gradual", None)])
    first = client.post("/chat", json=dict(case_id=case.case_id, message="gradual")).json()
    assert first["conversation_state"]["consecutive_no_progress_turns"] == 0
    respond(interpreter, [evidence("onset", "gradual", "gradual", None)])
    second = client.post("/chat", json=dict(case_id=case.case_id, message="gradual")).json()
    assert second["conversation_state"]["consecutive_no_progress_turns"] == 1


def test_low_confidence_proposal_does_not_count_as_progress(runtime):
    client, interpreter, _, _ = runtime
    case = clinical_case(stalls=7)
    save_case(case)
    proposal = evidence("onset", "gradual", "gradual", None)
    proposal["confidence"] = 0.1
    respond(interpreter, [proposal])
    result = client.post("/chat", json=dict(case_id=case.case_id, message="gradual")).json()
    assert result["conversation_state"]["consecutive_no_progress_turns"] == 8
    assert result["conversation_state"]["clarification_status"] == "unresolved"


def test_eight_consecutive_stalled_replies_still_terminate():
    case = clinical_case(turns=0)
    for index in range(cs.HARD_TURN_CAP):
        cs.advance_conversation(case, None, user_sources=["不知道"], current_extractions=[])
        if index < cs.HARD_TURN_CAP - 1:
            assert case.conversation_state.clarification_status != "unresolved"
    assert case.conversation_state.clarification_status == "unresolved"
    assert case.triage.next_question is None


def test_absolute_cap_stops_even_with_valid_progress(runtime):
    client, interpreter, planner, _ = runtime
    case = clinical_case(turns=cs.ABSOLUTE_TURN_CAP - 1)
    save_case(case)
    respond(interpreter, answer=dict(answered_intent="nausea_presence", answer_status="answered",
                                    answer_source_text="no", answer_confidence=0.99))
    planner.return_value = json.dumps(next_plan())
    result = client.post("/chat", json=dict(case_id=case.case_id, message="no")).json()
    assert result["conversation_state"]["clarification_status"] == "unresolved"
    assert result["conversation_state"]["confirmed"] is False


def test_revision_resets_both_counters():
    case = clinical_case(turns=15, stalls=7)
    route._begin_revision(case)
    assert case.conversation_state.turn_count == 0
    assert case.conversation_state.consecutive_no_progress_turns == 0


def test_completed_case_at_absolute_cap_still_uses_existing_confirmation_gate(runtime):
    client, interpreter, planner, department = runtime
    case = clinical_case(turns=cs.ABSOLUTE_TURN_CAP - 1)
    save_case(case)
    respond(interpreter, answer=dict(answered_intent="nausea_presence", answer_status="answered",
                                    answer_source_text="沒有", answer_confidence=0.99))
    planner.return_value = json.dumps(dict(status="sufficient", question=None, intent=None, reason="資訊足夠"))

    async def resolved(current):
        current.conversation_state.department_status = "resolved"
        current.department_result = DepartmentResult(dept_id=1, parentDept="測試", childDept="測試", confidence=0.99)

    department.side_effect = resolved
    result = client.post("/chat", json=dict(case_id=case.case_id, message="沒有")).json()
    assert result["conversation_state"]["turn_count"] == cs.ABSOLUTE_TURN_CAP
    assert result["conversation_state"]["is_complete"] is True
    assert result["conversation_state"]["awaiting_confirmation"] is True
    assert result["conversation_state"]["confirmed"] is False


def test_empty_request_does_not_change_counters():
    case = clinical_case(turns=9, stalls=3)
    cs.advance_conversation(case, None, user_sources=[], current_extractions=[])
    assert case.conversation_state.turn_count == 9
    assert case.conversation_state.consecutive_no_progress_turns == 3


def test_old_case_without_new_counter_loads_with_zero_stalls():
    payload = clinical_case().model_dump()
    payload["conversation_state"].pop("consecutive_no_progress_turns")
    assert TriageCase.model_validate(payload).conversation_state.consecutive_no_progress_turns == 0


def test_progress_does_not_bypass_safety():
    case = clinical_case(turns=8)
    case.patient_input.red_flags_checked = False
    case.history_records.append(Message(role="user", content="no"))
    suggestion = cs.ClarificationSuggestion("clarification_needed", "腹痛在哪個位置？", "pain_location",
        "確認位置", "nausea_presence", "no", "answered", 0.99)
    cs.advance_conversation(case, suggestion, user_sources=["no"], current_extractions=[])
    assert case.conversation_state.clarification_status == "safety_check"
    assert not case.patient_input.red_flags_checked
    assert not case.conversation_state.is_complete
