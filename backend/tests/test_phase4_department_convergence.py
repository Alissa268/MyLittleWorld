from __future__ import annotations

import importlib
import inspect
import json
import math
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app.main import app
from app.schemas import DepartmentResult, Message, SemanticExtraction, TriageCase, VisitType
from app.services import department_reasoning_service as reasoning
from app.services import department_preference_service
from app.services.appointment_service import DepartmentResolutionError
from app.services.case_store import save_case
from app.services.conversation_service import ClarificationSuggestion, advance_conversation, request_clarification
from app.services.department_knowledge import resolve_department_names
from app.services.rule_engine import QUESTION_TEXTS, RED_FLAG_QUESTION_KEY

chat_route = importlib.import_module("app.routes.chat")
recommend_route = importlib.import_module("app.routes.recommend")


def fixture():
    sources = [{"source_id": "official_one"}, {"source_id": "official_two"}]
    records = [
        {"department_name": "測試甲科", "concept": "症狀甲", "source_id": "official_one", "evidence_text": "測試甲科：症狀甲", "source_priority": 1},
        {"department_name": "測試乙科", "concept": "症狀甲", "source_id": "official_two", "evidence_text": "測試乙科：症狀甲", "source_priority": 2},
        {"department_name": "測試乙科", "concept": "線索乙", "source_id": "official_two", "evidence_text": "測試乙科：線索乙", "source_priority": 2},
        {"department_name": "近似測試科", "concept": "症狀甲", "source_id": "official_one", "evidence_text": "近似測試科：症狀甲", "source_priority": 1},
    ]
    active = [
        {"dept_id": "101", "parent_dept": "測試系", "child_dept": "測試甲科"},
        {"dept_id": 102, "parent_dept": "測試系", "child_dept": "測試乙科"},
        {"dept_id": 103, "parent_dept": "測試系", "child_dept": "近似測試科別"},
    ]
    return sources, records, active


def case_with_symptom() -> TriageCase:
    case = TriageCase(case_id="phase4-synthetic")
    case.history_records = [Message(role="user", content="我有症狀甲")]
    case.patient_input.symptom = "症狀甲"
    return case


def support(dept_id: int, concept: str = "症狀甲", source: str | None = None, text: str = "症狀甲") -> dict:
    return {
        "patient_source_text": text,
        "knowledge_source_id": source or ("official_one" if dept_id == 101 else "official_two"),
        "knowledge_concept": concept,
    }


def candidate(dept_id: int, confidence: object = 0.9, evidence: list[dict] | None = None) -> dict:
    return {"dept_id": dept_id, "confidence": confidence, "supporting_evidence": evidence or [support(dept_id)]}


class Phase4ValidationTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.sources, self.records, self.active = fixture()
        self.case = case_with_symptom()
        self.retrieved = reasoning.retrieve_official_evidence(
            self.case, self.records, resolve_department_names(self.records, self.active),
        )

    def validate(self, candidates, status="resolved"):
        return reasoning.validate_candidate_proposal(
            {"status": status, "candidates": candidates}, self.retrieved,
            self.sources, self.active, ["我有症狀甲"],
        )

    def test_invalid_dept_id_and_unresolved_near_name_are_rejected(self):
        for dept_id in (999, 103, 0, -1, True, "101"):
            with self.subTest(dept_id=dept_id):
                self.assertEqual(self.validate([candidate(dept_id)])[1], [])
        self.assertNotIn(103, {item["dept_id"] for item in self.retrieved})

    def test_source_id_must_exist_and_belong_to_retrieved_record(self):
        for source_id in ("invented", "official_two"):
            with self.subTest(source_id=source_id):
                self.assertEqual(self.validate([candidate(101, evidence=[support(101, source=source_id)])])[1], [])

    def test_patient_source_and_concept_must_match_grounded_retrieval(self):
        self.assertEqual(self.validate([candidate(101, evidence=[support(101, text="未曾說過")])])[1], [])
        self.assertEqual(self.validate([candidate(101, evidence=[support(101, concept="其他概念")])])[1], [])
        self.assertEqual(self.validate([candidate(101, evidence=[support(101, text="我有症狀甲")])])[1], [])

    def test_invalid_confidence_is_rejected(self):
        for confidence in (math.nan, math.inf, -math.inf, True, -0.1, 1.1, "0.9"):
            with self.subTest(confidence=confidence):
                self.assertEqual(self.validate([candidate(101, confidence)])[1], [])

    def test_multiple_departments_and_priorities_survive_retrieval(self):
        self.assertEqual({item["dept_id"] for item in self.retrieved}, {101, 102})
        self.assertEqual({item["source_priority"] for item in self.retrieved}, {1, 2})
        self.assertEqual(len(self.validate([candidate(101), candidate(102)])[1]), 2)

    def test_duplicate_departments_are_deduplicated_and_top_k_is_three(self):
        self.assertEqual(len(self.validate([candidate(101), candidate(101)])[1]), 1)
        retrieved = list(self.retrieved)
        active = list(self.active)
        candidates = [candidate(101), candidate(102)]
        for dept_id in (104, 105):
            active.append({"dept_id": dept_id, "parent_dept": "測試系", "child_dept": f"測試{dept_id}科"})
            retrieved.append({
                "dept_id": dept_id, "parentDept": "測試系", "childDept": f"測試{dept_id}科",
                "patient_source_text": "症狀甲", "knowledge_source_id": "official_two",
                "knowledge_concept": "症狀甲", "source_priority": 2,
            })
            candidates.append(candidate(dept_id))
        _, validated, _, _ = reasoning.validate_candidate_proposal(
            {"status": "ambiguous", "candidates": candidates}, retrieved,
            self.sources, active, ["我有症狀甲"],
        )
        self.assertEqual(len(validated), 3)

    async def test_backend_owns_ambiguous_and_resolved_gate(self):
        proposals = [
            {"status": "resolved", "candidates": [candidate(101), candidate(102)], "uncertainty_reason": "需要區分", "next_question_intent": "differentiate_signal"},
            {"status": "resolved", "candidates": [candidate(101, 0.54)]},
            {"status": "resolved", "candidates": [candidate(101, 0.9)], "stage": "confirmed", "is_complete": True},
        ]
        provider = AsyncMock(side_effect=[json.dumps(item, ensure_ascii=False) for item in proposals])
        with patch.object(reasoning, "fetch_active_departments", return_value=self.active), patch.object(
            reasoning, "load_department_knowledge", return_value=(self.sources, self.records),
        ), patch.object(reasoning, "complete_runtime_json", new=provider):
            await reasoning.reason_about_departments(self.case)
            self.assertEqual(self.case.conversation_state.department_status, "ambiguous")
            self.assertEqual(len(self.case.conversation_state.candidate_departments), 2)
            self.assertIsNone(self.case.department_result)
            self.assertEqual(self.case.conversation_state.department_next_question_intent, "differentiate_signal")
            await reasoning.reason_about_departments(self.case)
            self.assertEqual(self.case.conversation_state.department_status, "ambiguous")
            self.assertIsNone(self.case.department_result)
            await reasoning.reason_about_departments(self.case)
        self.assertEqual(self.case.conversation_state.department_status, "resolved")
        self.assertEqual(self.case.department_result.dept_id, 101)
        self.assertEqual(self.case.department_result.parentDept, "測試系")
        self.assertFalse(self.case.conversation_state.confirmed)
        self.assertEqual(self.case.conversation_state.stage.value, "collecting")

    async def test_single_valid_candidate_with_ambiguous_proposal_stays_ambiguous(self):
        provider = AsyncMock(return_value=json.dumps({"status": "ambiguous", "candidates": [candidate(101)]}, ensure_ascii=False))
        with patch.object(reasoning, "fetch_active_departments", return_value=self.active), patch.object(
            reasoning, "load_department_knowledge", return_value=(self.sources, self.records),
        ), patch.object(reasoning, "complete_runtime_json", new=provider):
            await reasoning.reason_about_departments(self.case)
        self.assertEqual(self.case.conversation_state.department_status, "ambiguous")
        self.assertIsNone(self.case.department_result)

    async def test_new_grounded_answer_recomputes_candidates_and_converges(self):
        first = {"status": "ambiguous", "candidates": [candidate(101, 0.8), candidate(102, 0.77)], "next_question_intent": "differentiate_signal"}
        second = {"status": "resolved", "candidates": [candidate(102, 0.91, [support(102, "線索乙", text="線索乙")])]}
        provider = AsyncMock(side_effect=[json.dumps(first, ensure_ascii=False), json.dumps(second, ensure_ascii=False)])
        with patch.object(reasoning, "fetch_active_departments", return_value=self.active), patch.object(
            reasoning, "load_department_knowledge", return_value=(self.sources, self.records),
        ), patch.object(reasoning, "complete_runtime_json", new=provider):
            await reasoning.reason_about_departments(self.case)
            self.case.history_records.append(Message(role="user", content="還有線索乙"))
            self.case.conversation_state.clarification_evidence["differentiate_signal"] = "線索乙"
            await reasoning.reason_about_departments(self.case)
        self.assertEqual(self.case.conversation_state.department_status, "resolved")
        self.assertEqual([item.dept_id for item in self.case.conversation_state.candidate_departments], [102])
        self.assertEqual(self.case.department_result.dept_id, 102)
        self.assertEqual(provider.await_count, 2)

    async def test_provider_failure_clears_stale_candidate_without_legacy_fallback(self):
        self.case.department_result = None
        for raw in (TimeoutError(), "{bad json"):
            with self.subTest(raw=type(raw).__name__):
                provider = AsyncMock(side_effect=raw if isinstance(raw, Exception) else None,
                                     return_value=raw if isinstance(raw, str) else None)
                with patch.object(reasoning, "fetch_active_departments", return_value=self.active), patch.object(
                    reasoning, "load_department_knowledge", return_value=(self.sources, self.records),
                ), patch.object(reasoning, "complete_runtime_json", new=provider):
                    await reasoning.reason_about_departments(self.case)
                self.assertEqual(self.case.conversation_state.department_status, "unresolved")
                self.assertEqual(self.case.conversation_state.candidate_departments, [])
                self.assertIsNone(self.case.department_result)

    async def test_explicit_department_preference_is_validated_but_not_kb_claim(self):
        self.case.history_records.append(Message(role="user", content="我想看測試甲科"))
        self.case.patient_input.requested_department_name = "測試甲科"
        self.case.patient_input.requested_department_id = 101
        provider = AsyncMock()
        with patch.object(reasoning, "fetch_active_departments", return_value=self.active), patch.object(
            reasoning, "complete_runtime_json", new=provider):
            await reasoning.reason_about_departments(self.case)
        provider.assert_not_awaited()
        self.assertEqual(self.case.conversation_state.department_status, "resolved")
        self.assertIn("使用者明確指定", self.case.department_result.reason[0])

    def test_production_service_does_not_use_audit_inventory(self):
        self.assertNotIn("PHASE3_310_DEPARTMENT_INVENTORY", inspect.getsource(reasoning))


class Phase4ChatGateTest(unittest.IsolatedAsyncioTestCase):
    def no_ai_chat(self, message: str, *, triage_case: dict | None = None, confirmed: bool = False):
        settings = SimpleNamespace(cerebras_api_key="", batch_triage_enabled=False)
        old_detector = AsyncMock()
        with patch.object(chat_route, "get_settings", return_value=settings), patch.object(
            chat_route, "detect_department_result", new=old_detector,
        ), patch.object(chat_route, "generate_triage_reply", new=AsyncMock(side_effect=lambda **kw: kw["fallback_reply"])):
            response = TestClient(app).post("/chat", json={
                "message": message, "confirmed": confirmed,
                **({"triage_case": triage_case} if triage_case else {}),
            })
        self.assertEqual(response.status_code, 200)
        old_detector.assert_not_awaited()
        return response.json()

    def test_missing_ai_key_free_text_stays_unresolved(self):
        result = self.no_ai_chat("我最近一直頭暈", confirmed=True)
        self.assertTrue(result["conversation_state"]["free_text_mode"])
        self.assertEqual(result["conversation_state"]["department_status"], "unresolved")
        self.assertIsNone(result["department_result"])
        self.assertEqual(result["conversation_state"]["stage"], "collecting")
        self.assertFalse(result["conversation_state"]["awaiting_confirmation"])
        self.assertFalse(result["conversation_state"]["confirmed"])
        confirmed = self.no_ai_chat("", triage_case=result["triage_case"], confirmed=True)
        self.assertEqual(confirmed["conversation_state"]["stage"], "collecting")
        self.assertIsNone(confirmed["department_result"])

    def test_missing_ai_key_messages_user_text_is_also_free_text(self):
        settings = SimpleNamespace(cerebras_api_key="", batch_triage_enabled=False)
        old_detector = AsyncMock()
        with patch.object(chat_route, "get_settings", return_value=settings), patch.object(
            chat_route, "detect_department_result", new=old_detector,
        ), patch.object(chat_route, "generate_triage_reply", new=AsyncMock(side_effect=lambda **kw: kw["fallback_reply"])):
            response = TestClient(app).post("/chat", json={"messages": [{"role": "user", "content": "我膝蓋很痛"}], "confirmed": True})
        self.assertEqual(response.status_code, 200)
        old_detector.assert_not_awaited()
        self.assertTrue(response.json()["conversation_state"]["free_text_mode"])
        self.assertIsNone(response.json()["department_result"])
        self.assertEqual(response.json()["conversation_state"]["stage"], "collecting")

    def test_missing_ai_key_knee_pain_cannot_use_legacy_mapping_even_later(self):
        first = self.no_ai_chat("我膝蓋很痛")
        self.assertIsNone(first["department_result"])
        second = self.no_ai_chat(
            "膝蓋痛兩週，爬樓梯很吃力，沒有胸痛呼吸困難意識不清大量出血，週一上午可以看診",
            triage_case=first["triage_case"], confirmed=True,
        )
        self.assertEqual(second["conversation_state"]["department_status"], "unresolved")
        self.assertIsNone(second["department_result"])
        self.assertEqual(second["conversation_state"]["stage"], "collecting")
        self.assertFalse(second["conversation_state"]["confirmed"])
        self.assertIn("無法安全地自動判定", second["reply"])

    def test_missing_ai_key_explicit_preference_uses_exact_live_db(self):
        row = {"dept_id": 1234, "parent_dept": "內科系", "child_dept": "感染科"}
        with patch.object(department_preference_service, "fetch_active_departments", return_value=[row]) as live_db:
            result = self.no_ai_chat("我想看感染科")
        self.assertGreaterEqual(live_db.call_count, 1)
        self.assertEqual(result["department_result"]["dept_id"], 1234)
        self.assertEqual(result["department_result"]["childDept"], "感染科")
        self.assertEqual(result["conversation_state"]["department_status"], "resolved")
        self.assertIn("使用者明確指定", result["department_result"]["reason"][0])
        self.assertNotIn("官方 KB", result["department_result"]["reason"][0])

    def test_missing_ai_key_keeps_safety_question_ahead_of_department(self):
        result = self.no_ai_chat("我最近一直頭暈")
        self.assertFalse(result["triage_case"]["patient_input"]["red_flags_checked"])
        self.assertEqual(result["conversation_state"]["last_question_key"], RED_FLAG_QUESTION_KEY)
        self.assertIn("胸痛", result["next_question"])
        self.assertIsNone(result["department_result"])
        self.assertEqual(result["conversation_state"]["stage"], "collecting")

    def test_missing_ai_key_pending_safety_is_not_replaced_by_department(self):
        case = TriageCase(case_id="phase4-no-ai-pending-safety")
        case.history_records = [Message(role="user", content="我最近一直頭暈，上禮拜開始")]
        case.patient_input.symptom = "頭暈"
        case.semantic_extractions = [SemanticExtraction(
            field="duration", normalized_value="一週", source_text="上禮拜",
            confidence=0.9, semantic_status="available",
        )]
        case.conversation_state.clarification_status = "safety_check"
        case.conversation_state.last_question_key = RED_FLAG_QUESTION_KEY
        case.conversation_state.free_text_mode = True
        save_case(case)
        result = self.no_ai_chat("不太確定", triage_case=case.model_dump(mode="json"))
        self.assertEqual(result["conversation_state"]["clarification_status"], "safety_check")
        self.assertEqual(result["conversation_state"]["last_question_key"], RED_FLAG_QUESTION_KEY)
        self.assertIn("胸痛", result["next_question"])
        self.assertIsNone(result["department_result"])
        self.assertEqual(result["conversation_state"]["stage"], "collecting")

    def test_missing_ai_key_positive_red_flag_keeps_urgent_warning(self):
        result = self.no_ai_chat("我突然胸痛")
        self.assertTrue(result["triage_case"]["patient_input"]["red_flags"])
        self.assertTrue(result["triage"]["warning_required"])
        self.assertEqual(result["triage"]["urgency_level"], "high")
        self.assertIsNone(result["department_result"])

    async def test_safety_question_precedes_candidate_question(self):
        case = case_with_symptom()
        case.conversation_state.department_status = "ambiguous"
        case.conversation_state.department_next_question_intent = "differentiate_signal"
        case.semantic_extractions = [SemanticExtraction(
            field="body_part", normalized_value="症狀甲", semantic_status="available",
            confidence=0.9, source_text="症狀甲",
        )]
        advance_conversation(
            case, ClarificationSuggestion("sufficient", None, None, "症狀足夠"),
            user_sources=["我有症狀甲"], current_extractions=[],
        )
        self.assertEqual(case.conversation_state.clarification_status, "safety_check")
        self.assertEqual(case.triage.next_question, QUESTION_TEXTS[RED_FLAG_QUESTION_KEY])
        self.assertEqual(case.conversation_state.asked_clarification_intents, [])

    async def test_hard_cap_with_ambiguous_candidates_stays_unresolved(self):
        case = case_with_symptom()
        case.patient_input.red_flags_checked = True
        case.conversation_state.turn_count = 7
        case.conversation_state.department_status = "ambiguous"
        case.semantic_extractions = [SemanticExtraction(
            field="body_part", normalized_value="症狀甲", semantic_status="available",
            confidence=0.9, source_text="症狀甲",
        )]
        advance_conversation(
            case, ClarificationSuggestion("sufficient", None, None, "症狀足夠"),
            user_sources=["我有症狀甲"], current_extractions=[],
        )
        self.assertEqual(case.conversation_state.turn_count, 8)
        self.assertEqual(case.conversation_state.clarification_status, "unresolved")
        self.assertIsNone(case.department_result)

    async def test_required_intent_mismatch_is_rejected(self):
        case = case_with_symptom()
        case.conversation_state.department_status = "ambiguous"
        case.conversation_state.department_next_question_intent = "differentiate_signal"
        raw = {"status": "clarification_needed", "question": "可以補充症狀如何變化嗎？", "intent": "wrong_intent", "reason": "待釐清"}
        with patch("app.services.conversation_service.complete_runtime_json", new=AsyncMock(return_value=json.dumps(raw, ensure_ascii=False))):
            suggestion = await request_clarification(case, ["我有症狀甲"])
        self.assertIsNone(suggestion.question)
        self.assertIsNone(suggestion.intent)

    async def test_free_text_never_calls_legacy_single_department_detector(self):
        settings = SimpleNamespace(cerebras_api_key="test-key", batch_triage_enabled=False)
        async def semantic(case, **_):
            case.patient_input.symptom = "症狀甲"
            return None
        async def converge(case):
            case.conversation_state.department_status = "ambiguous"
        planner = ClarificationSuggestion("clarification_needed", "可以補充症狀如何變化嗎？", "differentiate_signal", "待釐清")
        old_detector = AsyncMock()
        with patch.object(chat_route, "get_settings", return_value=settings), patch.object(
            chat_route, "refine_case_with_ai", new=semantic,
        ), patch.object(chat_route, "reason_about_departments", new=converge), patch.object(
            chat_route, "request_clarification", new=AsyncMock(return_value=planner),
        ), patch.object(chat_route, "detect_department_result", new=old_detector), patch.object(
            chat_route, "generate_triage_reply", new=AsyncMock(side_effect=lambda **kw: kw["fallback_reply"]),
        ):
            response = TestClient(app).post("/chat", json={"message": "我有症狀甲"})
        self.assertEqual(response.status_code, 200)
        old_detector.assert_not_awaited()
        self.assertEqual(response.json()["conversation_state"]["department_status"], "ambiguous")
        self.assertEqual(response.json()["conversation_state"]["stage"], "collecting")

    async def test_recommend_rejects_missing_result_without_detector(self):
        case = case_with_symptom()
        case.visit_type = VisitType.INITIAL
        case.patient_input.red_flags_checked = True
        case.conversation_state.is_complete = True
        case.conversation_state.confirmed = True
        case.triage.need_more_info = False
        case.confirmed = True
        save_case(case)
        detector = AsyncMock()
        with patch.object(recommend_route, "detect_department_result", new=detector):
            response = TestClient(app).post("/recommend", json={"triage_case": case.model_dump(mode="json"), "visit_type": "initial"})
        self.assertEqual(response.status_code, 422)
        detector.assert_not_awaited()

    async def test_recommend_allows_only_live_validated_explicit_preference_exception(self):
        case = case_with_symptom()
        case.visit_type = VisitType.INITIAL
        case.patient_input.red_flags_checked = True
        case.patient_input.requested_department_id = 101
        case.patient_input.requested_department_name = "測試甲科"
        case.conversation_state.clarification_status = "sufficient"
        case.conversation_state.is_complete = True
        case.conversation_state.confirmed = True
        case.triage.need_more_info = False
        case.confirmed = True
        save_case(case)
        explicit = DepartmentResult(dept_id=101, parentDept="測試系", childDept="測試甲科", reason=["使用者明確指定"])
        recommender = AsyncMock(side_effect=DepartmentResolutionError("test stop"))
        with patch.object(recommend_route, "resolve_requested_department", return_value=explicit) as validator, patch.object(
            recommend_route, "recommend_appointments", new=recommender,
        ), patch.object(recommend_route, "detect_department_result", new=AsyncMock()) as detector:
            response = TestClient(app).post("/recommend", json={"triage_case": case.model_dump(mode="json"), "visit_type": "initial"})
        validator.assert_called_once()
        recommender.assert_awaited_once()
        detector.assert_not_awaited()
        self.assertEqual(response.status_code, 422)

    async def test_resolved_case_safety_then_confirmation_keeps_validated_result(self):
        settings = SimpleNamespace(cerebras_api_key="test-key", batch_triage_enabled=False)
        async def semantic(case, **_):
            case.patient_input.symptom = "頭暈"
            case.semantic_extractions.append(SemanticExtraction(
                field="duration", normalized_value="一週", source_text="上禮拜",
                confidence=0.9, semantic_status="available",
            ))
            return None
        async def converge(case):
            case.conversation_state.department_status = "resolved"
            case.department_result = DepartmentResult(
                dept_id=101, parentDept="測試系", childDept="測試甲科", confidence=0.9,
            )
        old_detector = AsyncMock()
        with patch.object(chat_route, "get_settings", return_value=settings), patch.object(
            chat_route, "refine_case_with_ai", new=semantic,
        ) as semantic_mock, patch.object(chat_route, "reason_about_departments", new=converge), patch.object(
            chat_route, "request_clarification", new=AsyncMock(return_value=ClarificationSuggestion("sufficient", None, None, "足夠")),
        ) as planner, patch.object(chat_route, "detect_department_result", new=old_detector), patch.object(
            chat_route, "generate_triage_reply", new=AsyncMock(side_effect=lambda **kw: kw["fallback_reply"]),
        ):
            client = TestClient(app)
            first = client.post("/chat", json={"message": "我頭暈，上禮拜開始"}).json()
            self.assertEqual(first["conversation_state"]["clarification_status"], "safety_check")
            second = client.post("/chat", json={
                "triage_case": first["triage_case"],
                "message": "沒有胸痛、呼吸困難、意識不清、大量出血、半邊無力或劇烈頭痛",
            }).json()
            self.assertEqual(second["conversation_state"]["stage"], "waiting_confirmation")
            self.assertEqual(second["department_result"]["dept_id"], 101)
            third = client.post("/chat", json={"triage_case": second["triage_case"], "confirmed": True}).json()
        self.assertEqual(third["conversation_state"]["stage"], "recommending")
        self.assertTrue(third["conversation_state"]["confirmed"])
        self.assertEqual(third["department_result"]["dept_id"], 101)
        self.assertEqual(planner.await_count, 1)
        old_detector.assert_not_awaited()
