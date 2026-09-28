from __future__ import annotations

import importlib
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app.main import app
from app.schemas import TriageCase
from app.services import conversation_service, rag_triage_adapter
from app.services.rule_engine import QUESTION_TEXTS, RED_FLAG_QUESTION_KEY


chat_route = importlib.import_module("app.routes.chat")
recommend_route = importlib.import_module("app.routes.recommend")


def extraction(field, value, source, confidence=0.95):
    return {
        "field": field,
        "normalized_value": value,
        "source_text": source,
        "confidence": confidence,
        "semantic_status": "available",
    }


class Phase2ConversationTest(unittest.TestCase):
    def post(self, message, extractions, plan, *, triage_case=None, confirmed=False):
        settings = SimpleNamespace(cerebras_api_key="test-key", batch_triage_enabled=False)
        semantic = AsyncMock(return_value=json.dumps(
            {"semantic_extractions": extractions}, ensure_ascii=False,
        ))
        clarification = AsyncMock(
            side_effect=plan if isinstance(plan, Exception) else None,
            return_value=json.dumps(plan, ensure_ascii=False) if isinstance(plan, dict) else plan,
        )
        department = AsyncMock(return_value=None)
        with patch.object(chat_route, "get_settings", return_value=settings), patch.object(
            rag_triage_adapter, "get_settings", return_value=settings,
        ), patch.object(rag_triage_adapter, "complete_prompt", new=semantic), patch.object(
            conversation_service, "complete_runtime_json", new=clarification,
        ), patch.object(chat_route, "detect_department_result", new=department), patch.object(
            chat_route, "generate_triage_reply",
            new=AsyncMock(side_effect=lambda **kw: kw["fallback_reply"]),
        ):
            response = TestClient(app).post("/chat", json={
                "message": message,
                "confirmed": confirmed,
                **({"triage_case": triage_case} if triage_case else {}),
            })
        self.assertEqual(response.status_code, 200)
        return response.json(), semantic, clarification, department

    def rich_first_turn(self, *, confirmed=False, triage_case=None):
        message = "我上禮拜從樓梯踩空，右腳腳背腫起來，最近走路越來越痛，晚上也會痛醒。"
        return self.post(
            message,
            [
                extraction("symptom", "腳背腫痛", "右腳腳背腫起來"),
                extraction("body_part", "右腳腳背", "右腳腳背"),
                extraction("duration", "1週", "上禮拜"),
                extraction("severity", "severe", "晚上也會痛醒"),
                extraction("onset", "從樓梯踩空", "從樓梯踩空"),
                extraction("accompanying_symptoms", ["走路越來越痛"], "走路越來越痛"),
            ],
            {"status": "sufficient", "question": None, "intent": None,
             "reason": "症狀、起因與影響已足夠"},
            confirmed=confirmed,
            triage_case=triage_case,
        )

    def test_dizziness_gets_contextual_question_not_checklist_body_part(self):
        question = "你說的頭暈比較像周圍在旋轉，還是快昏倒、眼前發黑？"
        result, semantic, clarification, department = self.post(
            "我最近一直頭暈",
            [extraction("symptom", "頭暈", "頭暈")],
            {"status": "clarification_needed", "question": question,
             "intent": "clarify_dizziness_type", "reason": "頭暈型態仍不清楚"},
        )
        semantic.assert_awaited_once()
        clarification.assert_awaited_once()
        department.assert_not_awaited()
        self.assertEqual(result["next_question"], question)
        self.assertEqual(result["conversation_state"]["asked_clarification_intents"], ["clarify_dizziness_type"])
        self.assertEqual(result["conversation_state"]["turn_count"], 1)
        self.assertEqual(result["triage_case"]["history_records"][-1]["content"], question)

    def test_rich_description_can_be_sufficient_without_preferences(self):
        result, _, clarification, department = self.rich_first_turn()
        clarification.assert_awaited_once()
        department.assert_not_awaited()
        self.assertTrue(result["needMoreInfo"])
        self.assertFalse(result["conversation_state"]["is_complete"])
        self.assertEqual(result["conversation_state"]["stage"], "collecting")
        self.assertEqual(result["conversation_state"]["clarification_status"], "safety_check")
        self.assertEqual(result["conversation_state"]["last_question_key"], "red_flags")
        self.assertEqual(result["conversation_state"]["question_attempts"]["red_flags"], 1)
        self.assertEqual(result["conversation_state"]["asked_clarification_intents"], [])
        self.assertEqual(result["next_question"], QUESTION_TEXTS[RED_FLAG_QUESTION_KEY])
        self.assertFalse(result["triage_case"]["patient_input"]["red_flags_checked"])
        self.assertFalse(result["triage"]["is_final"])
        self.assertEqual(result["triage_case"]["patient_input"]["symptom"], "右腳腳背腫起來")
        self.assertEqual(result["triage_case"]["availability"]["preferred_days"], [])

    def test_negative_safety_answer_completes_before_department_detection(self):
        first, _, _, _ = self.rich_first_turn()
        answer = "沒有胸痛，也沒有呼吸困難、意識不清、大量出血、半邊無力或劇烈頭痛"
        second, semantic, clarification, department = self.post(
            answer,
            [extraction("accompanying_symptoms", ["胸痛", "呼吸困難"], answer)],
            None, triage_case=first["triage_case"],
        )
        semantic.assert_not_awaited()
        clarification.assert_not_awaited()
        department.assert_awaited_once()
        self.assertTrue(second["triage_case"]["patient_input"]["red_flags_checked"])
        self.assertEqual(second["triage_case"]["patient_input"]["red_flags"], [])
        self.assertEqual(second["triage_case"]["patient_input"]["accompanying_symptoms"], ["走路越來越痛"])
        self.assertEqual(len(second["triage_case"]["semantic_extractions"]), len(first["triage_case"]["semantic_extractions"]))
        self.assertEqual(second["triage_case"]["history_records"][-1]["content"], answer)
        self.assertTrue(second["conversation_state"]["is_complete"])
        self.assertFalse(second["needMoreInfo"])
        self.assertEqual(second["conversation_state"]["stage"], "waiting_confirmation")

    def test_eighth_symptom_turn_enters_safety_check_before_hard_cap(self):
        case = TriageCase(case_id="phase22-eighth-turn")
        case.conversation_state.turn_count = 7
        eighth, semantic, clarification, department = self.rich_first_turn(
            triage_case=case.model_dump(mode="json"),
        )
        semantic.assert_awaited_once()
        clarification.assert_awaited_once()
        department.assert_not_awaited()
        state = eighth["conversation_state"]
        self.assertEqual(state["turn_count"], 8)
        self.assertEqual(state["clarification_status"], "safety_check")
        self.assertFalse(state["is_complete"])
        self.assertEqual(eighth["next_question"], QUESTION_TEXTS[RED_FLAG_QUESTION_KEY])

        completed, semantic, clarification, department = self.post(
            "沒有胸痛、呼吸困難、意識不清、大量出血、半邊無力或劇烈頭痛",
            [extraction("symptom", "胸痛", "胸痛")], None,
            triage_case=eighth["triage_case"],
        )
        semantic.assert_not_awaited()
        clarification.assert_not_awaited()
        department.assert_awaited_once()
        self.assertEqual(completed["conversation_state"]["turn_count"], 8)
        self.assertEqual(completed["conversation_state"]["clarification_status"], "sufficient")
        self.assertTrue(completed["conversation_state"]["is_complete"])
        self.assertTrue(completed["triage_case"]["patient_input"]["red_flags_checked"])
        self.assertEqual(completed["triage_case"]["patient_input"]["red_flags"], [])

    def test_confirmation_cannot_skip_safety_question(self):
        result, _, _, department = self.rich_first_turn(confirmed=True)
        department.assert_not_awaited()
        self.assertEqual(result["conversation_state"]["stage"], "collecting")
        self.assertFalse(result["conversation_state"]["confirmed"])
        self.assertFalse(result["conversation_state"]["is_complete"])
        self.assertTrue(result["needMoreInfo"])

    def test_recommend_rejects_forged_completion_without_safety(self):
        first, _, _, _ = self.rich_first_turn()
        forged = first["triage_case"]
        forged["conversation_state"]["is_complete"] = True
        forged["conversation_state"]["confirmed"] = True
        forged["triage"]["need_more_info"] = False
        forged["confirmed"] = True
        with patch.object(recommend_route, "detect_department_result", new=AsyncMock()) as detector, patch.object(
            recommend_route, "recommend_appointments", new=AsyncMock(),
        ) as recommender:
            response = TestClient(app).post("/recommend", json={
                "triage_case": forged, "visit_type": "initial", "confirmed": True,
            })
        self.assertEqual(response.status_code, 400)
        self.assertIn("急迫症狀篩檢尚未完成", response.json()["detail"])
        detector.assert_not_awaited()
        recommender.assert_not_awaited()

    def test_positive_red_flag_keeps_urgent_path(self):
        result, _, clarification, department = self.post(
            "我突然胸痛", [extraction("symptom", "胸痛", "胸痛")], None,
        )
        clarification.assert_not_awaited()
        department.assert_awaited_once()
        self.assertTrue(result["triage_case"]["patient_input"]["red_flags"])
        self.assertTrue(result["triage_case"]["patient_input"]["red_flags_checked"])
        self.assertEqual(result["conversation_state"]["clarification_status"], "urgent")
        self.assertFalse(result["needMoreInfo"])
        self.assertIsNone(result["next_question"])

    def test_follow_up_answer_uses_history_and_does_not_repeat_intent(self):
        first, _, _, _ = self.post(
            "最近一直頭暈", [extraction("symptom", "頭暈", "頭暈")],
            {"status": "clarification_needed", "question": "比較像房子在轉，還是快昏倒？",
             "intent": "clarify_dizziness_type", "reason": "型態未明"},
        )
        second, _, clarification, department = self.post(
            "像房子在轉", [extraction("accompanying_symptoms", ["像房子在轉"], "像房子在轉")],
            {"status": "sufficient", "question": None, "intent": None,
             "reason": "頭暈型態已釐清", "answered_intent": "clarify_dizziness_type",
             "answer_source_text": "像房子在轉"},
            triage_case=first["triage_case"],
        )
        prompt = clarification.await_args.args[0]
        self.assertIn("比較像房子在轉，還是快昏倒？", prompt)
        self.assertIn("像房子在轉", prompt)
        self.assertEqual(second["conversation_state"]["clarification_evidence"]["clarify_dizziness_type"], "像房子在轉")
        self.assertEqual(second["conversation_state"]["asked_clarification_intents"], ["clarify_dizziness_type"])
        self.assertIsNone(second["conversation_state"]["pending_clarification_intent"])
        self.assertEqual(second["conversation_state"]["turn_count"], 2)
        self.assertTrue(second["needMoreInfo"])
        self.assertEqual(second["conversation_state"]["clarification_status"], "safety_check")
        self.assertIn("突發胸痛", second["next_question"])
        department.assert_not_awaited()

    def test_filled_duration_is_not_reasked(self):
        result, _, _, _ = self.post(
            "我頭暈，上禮拜開始", [
                extraction("symptom", "頭暈", "頭暈"),
                extraction("duration", "1週", "上禮拜"),
            ],
            {"status": "clarification_needed", "question": "這樣持續多久了？",
             "intent": "clarify_timeline", "reason": "持續時間不清楚"},
        )
        self.assertEqual(result["triage_case"]["patient_input"]["duration"], "1週")
        self.assertNotIn("duration", result["conversation_state"]["asked_clarification_intents"])
        self.assertNotEqual(result["next_question"], "這樣持續多久了？")

    def test_provider_failure_and_malformed_json_keep_facts_unchanged(self):
        for plan in (TimeoutError(), "{bad json"):
            with self.subTest(plan=type(plan).__name__):
                result, _, clarification, department = self.post(
                    "我頭暈", [extraction("symptom", "頭暈", "頭暈")], plan,
                )
                clarification.assert_awaited_once()
                department.assert_not_awaited()
                self.assertEqual(result["triage_case"]["patient_input"]["symptom"], "頭暈")
                self.assertIsNone(result["triage_case"]["patient_input"]["duration"])
                self.assertTrue(result["needMoreInfo"])
                self.assertIn("描述", result["next_question"])

    def test_ai_cannot_set_workflow_or_complete_blank_case(self):
        result, _, _, department = self.post(
            "我不太確定", [],
            {"status": "sufficient", "question": None, "intent": None,
             "reason": "足夠", "is_complete": True, "stage": "confirmed",
             "confirmed": True, "recommendation_generated": True},
        )
        state = result["conversation_state"]
        self.assertFalse(state["is_complete"])
        self.assertEqual(state["stage"], "collecting")
        self.assertFalse(state["confirmed"])
        self.assertFalse(result["triage_case"]["recommendation_generated"])
        department.assert_not_awaited()

    def test_vague_symptom_cannot_be_declared_sufficient(self):
        result, _, _, department = self.post(
            "肚子不舒服", [extraction("symptom", "肚子不舒服", "肚子不舒服")],
            {"status": "sufficient", "question": None, "intent": None,
             "reason": "資訊足夠", "is_complete": True},
        )
        self.assertTrue(result["needMoreInfo"])
        self.assertEqual(result["conversation_state"]["clarification_status"], "clarifying")
        self.assertFalse(result["conversation_state"]["is_complete"])
        department.assert_not_awaited()

    def test_low_confidence_and_pending_answer_block_sufficient(self):
        first, _, _, _ = self.post(
            "我頭暈", [extraction("symptom", "頭暈", "頭暈")],
            {"status": "clarification_needed", "question": "是旋轉感還是快昏倒？",
             "intent": "clarify_dizziness_type", "reason": "型態未明"},
        )
        second, _, _, department = self.post(
            "大概是吧", [extraction("severity", "mild", "大概是吧", confidence=0.2)],
            {"status": "sufficient", "question": None, "intent": None,
             "reason": "足夠", "answered_intent": "clarify_dizziness_type",
             "answer_source_text": "不在原文"},
            triage_case=first["triage_case"],
        )
        self.assertTrue(second["needMoreInfo"])
        self.assertEqual(second["conversation_state"]["pending_clarification_intent"], "clarify_dizziness_type")
        self.assertFalse(second["conversation_state"]["is_complete"])
        department.assert_not_awaited()

    def test_grounded_answer_without_accepted_extraction_does_not_clear_pending(self):
        first, _, _, _ = self.post(
            "最近一直頭暈", [extraction("symptom", "頭暈", "頭暈")],
            {"status": "clarification_needed", "question": "是旋轉感還是快昏倒？",
             "intent": "clarify_dizziness_type", "reason": "型態未明"},
        )
        second, _, _, department = self.post(
            "像房子在轉", [],
            {"status": "sufficient", "question": None, "intent": None,
             "reason": "已回答", "answered_intent": "clarify_dizziness_type",
             "answer_source_text": "像房子在轉"},
            triage_case=first["triage_case"],
        )
        self.assertTrue(second["needMoreInfo"])
        self.assertEqual(second["conversation_state"]["pending_clarification_intent"], "clarify_dizziness_type")
        self.assertEqual(second["conversation_state"]["clarification_evidence"], {})
        department.assert_not_awaited()

    def test_hard_cap_is_unresolved_without_guessing(self):
        case = None
        for turn in range(8):
            result, _, _, department = self.post(
                f"還不清楚{turn}", [], TimeoutError(), triage_case=case,
            )
            case = result["triage_case"]
            department.assert_not_awaited()
        state = result["conversation_state"]
        self.assertEqual(state["turn_count"], 8)
        self.assertEqual(state["clarification_status"], "unresolved")
        self.assertFalse(state["is_complete"])
        self.assertIsNone(result["next_question"])
        self.assertIn("不會替你猜測科別", result["reply"])
        self.assertEqual(result["triage_case"]["patient_input"]["symptom"], "")
        self.assertIsNone(result["department_result"])
        repeated, semantic, clarification, department = self.post(
            "還是不清楚", [], TimeoutError(), triage_case=case,
        )
        semantic.assert_not_awaited()
        clarification.assert_not_awaited()
        department.assert_not_awaited()
        self.assertEqual(repeated["conversation_state"]["turn_count"], 8)


if __name__ == "__main__":
    unittest.main()
