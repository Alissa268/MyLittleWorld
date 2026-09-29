from __future__ import annotations

import asyncio
import json
import logging
from datetime import date
from unittest.mock import AsyncMock, Mock, patch

import pytest
from fastapi.testclient import TestClient

from app import db
from app.main import app
from app.routes.chat import _resolve_case
from app.schemas import (
    ChatRequest,
    ConversationStage,
    DepartmentResult,
    RecommendationColumns,
    RecommendationItem,
    RecommendationResult,
    TriageCase,
    VisitType,
)
from app.services import case_store, conversation_service, followup_service
from app.services.appointment_service import DepartmentResolutionError, recommend_appointments
from app.services.case_store import get_case, save_case, save_recommendations
from app.services.rule_engine import apply_user_message, evaluate_urgency


@pytest.fixture(autouse=True)
def isolated_store():
    case_store._CASES.clear()
    case_store._RECOMMENDATIONS_BY_CASE.clear()
    case_store._LAST_TOUCHED.clear()
    yield
    case_store._CASES.clear()
    case_store._RECOMMENDATIONS_BY_CASE.clear()
    case_store._LAST_TOUCHED.clear()


def _complete_case(case_id: str = "case_hardened") -> TriageCase:
    case = TriageCase(case_id=case_id, visit_type=VisitType.INITIAL)
    case.patient_input.symptom = "grounded symptom"
    case.patient_input.red_flags_checked = True
    case.triage.need_more_info = False
    case.triage.is_final = True
    case.confirmed = True
    case.conversation_state.is_complete = True
    case.conversation_state.confirmed = True
    case.conversation_state.stage = ConversationStage.WAITING_CONFIRMATION
    case.conversation_state.department_status = "resolved"
    case.department_result = DepartmentResult(
        dept_id=1232, parentDept="內科系", childDept="一般內科", confidence=0.9,
    )
    return case


def _item(recommendation_id: str = "rec_hardened") -> RecommendationItem:
    return RecommendationItem(
        recommendation_id=recommendation_id,
        parentDept="內科系", childDept="一般內科", doctor="正式醫師",
        doctor_id="88", schedule_id="900", dept_id=1232,
        date="2026-10-10", session="上午", slot="A1", score=90,
    )


def _result(case_id: str, *, with_rows: bool = True) -> RecommendationResult:
    items = [_item()] if with_rows else []
    return RecommendationResult(
        case_id=case_id,
        recommendations=RecommendationColumns(specialty_first=items, time_first=items),
        total_count=len(items) * 2,
    )


def test_existing_server_case_wins_and_mismatched_snapshot_id_is_rejected():
    stored = TriageCase(case_id="case_real")
    save_case(stored)
    forged = _complete_case("case_real")
    forged.department_result = DepartmentResult(dept_id=999999, childDept="不存在科")

    resolved = _resolve_case(ChatRequest(case_id="case_real", triage_case=forged))
    assert resolved is stored
    assert resolved.conversation_state.is_complete is False
    assert resolved.patient_input.red_flags_checked is False
    assert resolved.department_result is None

    response = TestClient(app).post("/chat", json={
        "case_id": "case_real",
        "triage_case": forged.model_copy(update={"case_id": "case_other"}).model_dump(mode="json"),
    })
    assert response.status_code == 422


def test_forged_recommend_snapshot_cannot_bypass_completion_confirmation_or_safety():
    stored = TriageCase(case_id="case_incomplete", visit_type=VisitType.INITIAL)
    save_case(stored)
    forged = _complete_case("case_incomplete")
    forged.department_result = DepartmentResult(dept_id=999999, childDept="不存在科")

    response = TestClient(app).post("/recommend", json={
        "case_id": stored.case_id,
        "triage_case": forged.model_dump(mode="json"),
        "confirmed": True,
    })
    assert response.status_code == 400
    authoritative = get_case(stored.case_id)
    assert authoritative.conversation_state.is_complete is False
    assert authoritative.confirmed is False
    assert authoritative.patient_input.red_flags_checked is False
    assert authoritative.department_result is None


def test_mismatched_recommend_snapshot_id_is_rejected():
    save_case(TriageCase(case_id="case_a", visit_type=VisitType.INITIAL))
    response = TestClient(app).post("/recommend", json={
        "case_id": "case_a", "triage_case": {"case_id": "case_b"},
    })
    assert response.status_code == 422


def test_department_tuple_is_revalidated_before_schedule_query(monkeypatch):
    case = _complete_case()
    called = Mock(return_value=[])
    monkeypatch.setattr(
        "app.services.appointment_service.fetch_active_departments",
        lambda: [{"dept_id": 1232, "parent_dept": "內科系", "child_dept": "一般內科"}],
    )
    monkeypatch.setattr("app.services.appointment_service.fetch_available_slots", called)

    case.department_result.childDept = "錯誤名稱"
    with pytest.raises(DepartmentResolutionError):
        asyncio.run(recommend_appointments(case))
    called.assert_not_called()

    case.department_result = DepartmentResult(
        dept_id=999999, parentDept="內科系", childDept="一般內科", confidence=0.9,
    )
    with pytest.raises(DepartmentResolutionError):
        asyncio.run(recommend_appointments(case))
    called.assert_not_called()


def test_duplicate_live_department_rows_are_ambiguous(monkeypatch):
    case = _complete_case("case_duplicate_department")
    row = {"dept_id": 1232, "parent_dept": "內科系", "child_dept": "一般內科"}
    schedule_fetch = Mock(return_value=[])
    monkeypatch.setattr("app.services.appointment_service.fetch_active_departments", lambda: [row, row.copy()])
    monkeypatch.setattr("app.services.appointment_service.fetch_available_slots", schedule_fetch)
    with pytest.raises(DepartmentResolutionError):
        asyncio.run(recommend_appointments(case))
    schedule_fetch.assert_not_called()

def test_recommend_commits_only_after_success(monkeypatch):
    case = _complete_case("case_tx")
    save_case(case)
    monkeypatch.setattr(
        "app.routes.recommend.recommend_appointments",
        AsyncMock(side_effect=db.DatabaseUnavailableError("down")),
    )
    failed = TestClient(app).post("/recommend", json={"case_id": case.case_id, "confirmed": True})
    assert failed.status_code == 503
    assert get_case(case.case_id).conversation_state.stage == ConversationStage.WAITING_CONFIRMATION
    assert get_case(case.case_id).recommendation_generated is False

    monkeypatch.setattr(
        "app.routes.recommend.recommend_appointments",
        AsyncMock(return_value=_result(case.case_id, with_rows=False)),
    )
    empty = TestClient(app).post("/recommend", json={"case_id": case.case_id, "confirmed": True})
    assert empty.status_code == 503
    assert get_case(case.case_id).recommendation_generated is False

    monkeypatch.setattr(
        "app.routes.recommend.recommend_appointments",
        AsyncMock(return_value=_result(case.case_id)),
    )
    success = TestClient(app).post("/recommend", json={"case_id": case.case_id, "confirmed": True})
    assert success.status_code == 200
    assert get_case(case.case_id).conversation_state.stage == ConversationStage.RECOMMENDING
    assert get_case(case.case_id).recommendation_generated is True


def test_followup_rejects_noncanonical_department_and_doctor(monkeypatch):
    monkeypatch.setattr(
        followup_service, "fetch_active_departments",
        lambda: [{"dept_id": 7, "parent_dept": "外科系", "child_dept": "一般骨科"}],
    )
    monkeypatch.setattr(
        followup_service, "fetch_reference_doctors",
        lambda _department: [{"doctor_id": "101", "name": "原醫師"}],
    )
    client = TestClient(app)

    fake_dept = client.post("/followup/recommend", json={
        "dept_id": 999, "childDept": "一般骨科", "original_doctor": "原醫師",
    })
    assert fake_dept.status_code == 422

    mismatch = client.post("/followup/recommend", json={
        "dept_id": 7, "childDept": "錯誤科", "original_doctor": "原醫師",
    })
    assert mismatch.status_code == 422

    fake_doctor = client.post("/followup/recommend", json={
        "dept_id": 7, "childDept": "一般骨科", "original_doctor": "原醫師",
        "original_doctor_id": 999,
    })
    assert fake_doctor.status_code == 422

    name_mismatch = client.post("/followup/recommend", json={
        "dept_id": 7, "childDept": "一般骨科", "original_doctor": "另一位醫師",
        "original_doctor_id": 101,
    })
    assert name_mismatch.status_code == 422


def test_followup_canonical_relationship_passes_and_db_failure_is_503(monkeypatch):
    monkeypatch.setattr(
        followup_service, "fetch_active_departments",
        lambda: [{"dept_id": 7, "parent_dept": "外科系", "child_dept": "一般骨科"}],
    )
    monkeypatch.setattr(
        followup_service, "fetch_reference_doctors",
        lambda _department: [{"doctor_id": "101", "name": "原醫師"}],
    )
    row = {
        "parent_dept": "外科系", "child_dept": "一般骨科", "doctor": "原醫師",
        "doctor_id": "101", "dept_id": "7", "schedule_id": "901",
        "date": "2026-10-10", "session": "上午", "room": "A1", "status": "open",
    }
    monkeypatch.setattr(followup_service, "fetch_return_visit_slots", lambda **_kwargs: [row])
    response = TestClient(app).post("/followup/recommend", json={
        "dept_id": 7, "childDept": "一般骨科", "parentDept": "外科系",
        "original_doctor": "原醫師", "original_doctor_id": 101,
    })
    assert response.status_code == 200
    assert response.json()["department"]["dept_id"] == 7
    assert response.json()["recommendations"][0]["doctor_id"] == "101"

    monkeypatch.setattr(
        followup_service, "fetch_return_visit_slots",
        lambda **_kwargs: (_ for _ in ()).throw(db.DatabaseUnavailableError("down")),
    )
    unavailable = TestClient(app).post("/followup/recommend", json={
        "dept_id": 7, "childDept": "一般骨科", "original_doctor": "原醫師",
    })
    assert unavailable.status_code == 503


def test_generate_script_revalidates_stored_schedule(monkeypatch):
    case = _complete_case("case_script")
    save_case(case)
    save_recommendations(case.case_id, [_item()])
    authoritative = _item().model_copy(update={"doctor": "DB 正式醫師", "room": "DB-Room"})
    revalidate = Mock(return_value=authoritative)
    monkeypatch.setattr("app.routes.generate_script.revalidate_schedule", revalidate)

    response = TestClient(app).post("/generate_script", json={
        "case_id": case.case_id, "recommendation_id": "rec_hardened",
    })
    assert response.status_code == 200
    assert response.json()["recommendation"]["doctor"] == "DB 正式醫師"
    revalidate.assert_called_once()

    monkeypatch.setattr(
        "app.routes.generate_script.revalidate_schedule",
        Mock(side_effect=ValueError("stale")),
    )
    stale = TestClient(app).post("/generate_script", json={
        "case_id": case.case_id, "recommendation_id": "rec_hardened",
    })
    assert stale.status_code == 409


class _Cursor:
    def __init__(self, rows=(), *, execute_error=None, fetch_error=None):
        self.rows = rows
        self.execute_error = execute_error
        self.fetch_error = fetch_error

    def execute(self, *_args):
        if self.execute_error:
            raise self.execute_error

    def fetchall(self):
        if self.fetch_error:
            raise self.fetch_error
        return self.rows


class _Connection:
    def __init__(self, cursor):
        self._cursor = cursor
        self.closed = False

    def cursor(self):
        return self._cursor

    def close(self):
        self.closed = True


@pytest.mark.parametrize("cursor", [
    _Cursor(rows=[]),
    _Cursor(execute_error=RuntimeError("execute")),
    _Cursor(fetch_error=RuntimeError("fetch")),
])
def test_db_connection_closes_on_all_paths(monkeypatch, cursor):
    conn = _Connection(cursor)
    monkeypatch.setattr(db, "create_db_connection", lambda: conn)
    try:
        db.fetch_reference_departments()
    except db.DatabaseUnavailableError:
        pass
    assert conn.closed is True


def test_reference_doctors_deduplicate_by_doctor_id(monkeypatch):
    conn = _Connection(_Cursor(rows=[(1, "王醫師"), (2, "王醫師"), (1, "王醫師")]))
    monkeypatch.setattr(db, "create_db_connection", lambda: conn)
    assert db.fetch_reference_doctors("一般內科") == [
        {"doctor_id": "1", "name": "王醫師"},
        {"doctor_id": "2", "name": "王醫師"},
    ]


def test_info_logs_do_not_contain_patient_health_text(caplog):
    case = TriageCase(case_id="case_log")
    with caplog.at_level(logging.INFO):
        apply_user_message(case, "我有血尿而且尿尿灼熱")
        evaluate_urgency(case)
    output = caplog.text
    assert "血尿" not in output
    assert "灼熱" not in output


def test_case_store_ttl_expires_case_and_recommendations_and_touch_extends(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(case_store, "_clock", lambda: now[0])
    case = case_store.create_case("case_ttl")
    case_store.save_recommendations(case.case_id, [_item()])

    now[0] += case_store.CASE_TTL_SECONDS - 1
    assert case_store.get_case(case.case_id) is case
    now[0] += case_store.CASE_TTL_SECONDS - 1
    assert case_store.get_case(case.case_id) is case
    now[0] += case_store.CASE_TTL_SECONDS
    assert case_store.get_case(case.case_id) is None
    assert case_store.get_recommendations_for_case(case.case_id) == {}


def test_conjunction_style_multidimension_question_is_rejected(monkeypatch):
    case = TriageCase(case_id="case_atomic")
    case.patient_input.symptom = "血尿"
    proposal = {
        "status": "clarification_needed",
        "question": "血尿程度如何，另外是否有疼痛？",
        "intent": "severity",
        "reason": "需要聚焦程度",
        "answered_intent": None,
        "answer_source_text": None,
        "answer_status": None,
        "answer_confidence": None,
    }
    monkeypatch.setattr(
        conversation_service,
        "complete_runtime_json",
        AsyncMock(return_value=json.dumps(proposal, ensure_ascii=False)),
    )
    suggestion = asyncio.run(conversation_service.request_clarification(case, ["血尿"]))
    assert suggestion.question is None
    assert suggestion.intent is None
