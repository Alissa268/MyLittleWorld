from __future__ import annotations

import json
import unittest
from unittest.mock import AsyncMock, patch

from app.schemas import BatchAnswer, SemanticExtraction, TriageCase, VisitType
from app.services import batch_extraction_service
from app.services.batch_extraction_service import extract_batch_answers
from app.services.field_acceptance import has_symptom_semantics
from app.services.rule_engine import apply_semantic_extractions, missing_checklist_fields


class SemanticAiValidationTest(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def _provider(extraction: dict) -> AsyncMock:
        return AsyncMock(
            return_value=json.dumps({"extractions": [extraction]}, ensure_ascii=False)
        )

    async def _extract(self, field: str, answer: str, extraction: dict):
        case = TriageCase(case_id=f"semantic-ai-{field}", visit_type=VisitType.INITIAL)
        provider = self._provider(extraction)
        with patch.object(
            batch_extraction_service,
            "runtime_ai_available",
            return_value=True,
        ), patch.object(
            batch_extraction_service,
            "complete_prompt",
            new=provider,
        ):
            outcome = await extract_batch_answers(
                case,
                [BatchAnswer(key=field, answer=answer)],
            )
        return case, outcome, provider

    async def test_ai_body_part_can_bypass_deterministic_dictionary_for_clavicle(self):
        answer = "鎖骨附近怪怪的"
        case, outcome, provider = await self._extract(
            "body_part",
            answer,
            {
                "field": "body_part",
                "normalized_value": "鎖骨",
                "semantic_status": "available",
                "confidence": 0.9,
                "source_text": "鎖骨附近",
            },
        )

        provider.assert_awaited_once()
        self.assertTrue(outcome.ai_attempted)
        self.assertEqual(case.patient_input.body_part, "鎖骨")
        self.assertNotIn("body_part", missing_checklist_fields(case))

    async def test_ai_body_part_can_replace_broad_rule_match_with_grounded_wrist(self):
        answer = "手腕那邊一直怪怪的"
        case, outcome, provider = await self._extract(
            "body_part",
            answer,
            {
                "field": "body_part",
                "normalized_value": "手腕",
                "semantic_status": "available",
                "confidence": 0.88,
                "source_text": "手腕那邊",
            },
        )

        provider.assert_awaited_once()
        self.assertTrue(outcome.ai_attempted)
        self.assertEqual(case.patient_input.body_part, "手腕")

    async def test_ai_symptom_is_not_limited_to_symptom_terms(self):
        answer = "一直有燒灼的感覺"
        self.assertFalse(has_symptom_semantics(answer))
        case, _, provider = await self._extract(
            "symptom",
            answer,
            {
                "field": "symptom",
                "normalized_value": "灼熱感",
                "semantic_status": "available",
                "confidence": 0.87,
                "source_text": "燒灼的感覺",
            },
        )

        provider.assert_awaited_once()
        self.assertEqual(case.patient_input.symptom, "灼熱感")

    async def test_indirect_weak_severity_answers_use_ai_and_land_as_mild(self):
        for answer in ("不影響吧", "應該還好吧"):
            with self.subTest(answer=answer):
                case, _, provider = await self._extract(
                    "severity",
                    answer,
                    {
                        "field": "severity",
                        "normalized_value": "mild",
                        "semantic_status": "available",
                        "confidence": 0.82,
                        "source_text": answer,
                        "needs_clarification": False,
                    },
                )

                provider.assert_awaited_once()
                self.assertEqual(case.patient_input.severity, "mild")
                self.assertNotIn("severity", missing_checklist_fields(case))

    async def test_weak_duration_with_clear_core_keeps_the_fast_path(self):
        case = TriageCase(case_id="semantic-ai-duration", visit_type=VisitType.INITIAL)
        provider = AsyncMock(side_effect=AssertionError("clear duration must not call Cerebras"))
        with patch.object(
            batch_extraction_service,
            "runtime_ai_available",
            return_value=True,
        ), patch.object(
            batch_extraction_service,
            "complete_prompt",
            new=provider,
        ):
            outcome = await extract_batch_answers(
                case,
                [BatchAnswer(key="duration", answer="應該半年左右吧")],
            )

        provider.assert_not_awaited()
        self.assertFalse(outcome.ai_attempted)
        self.assertEqual(case.patient_input.duration, "6個月")

    async def test_unknown_ai_result_does_not_fill_body_part(self):
        answer = "我真的不知道是哪裡"
        case, _, provider = await self._extract(
            "body_part",
            answer,
            {
                "field": "body_part",
                "normalized_value": None,
                "semantic_status": "unknown",
                "confidence": 0.2,
                "source_text": answer,
                "needs_clarification": True,
            },
        )

        provider.assert_awaited_once()
        self.assertIsNone(case.patient_input.body_part)
        self.assertIn("body_part", missing_checklist_fields(case, apply_attempt_fallback=False))

    async def test_ai_source_text_must_be_contiguous_in_the_same_keyed_answer(self):
        answer = "我不知道是哪裡"
        with self.assertLogs(batch_extraction_service.logger, level="INFO") as logs:
            case, outcome, _ = await self._extract(
                "body_part",
                answer,
                {
                    "field": "body_part",
                    "normalized_value": "頭",
                    "semantic_status": "available",
                    "confidence": 0.9,
                    "source_text": "頭",
                },
            )

        self.assertIsNone(case.patient_input.body_part)
        self.assertEqual(outcome.ai_fields, [])
        self.assertIn("reject_reason=source_not_grounded", "\n".join(logs.output))

    async def test_grounded_source_cannot_support_unrelated_body_part(self):
        answer = "鎖骨附近怪怪的"
        with self.assertLogs(batch_extraction_service.logger, level="INFO") as logs:
            case, outcome, _ = await self._extract(
                "body_part",
                answer,
                {
                    "field": "body_part",
                    "normalized_value": "膝蓋",
                    "semantic_status": "available",
                    "confidence": 0.9,
                    "source_text": "鎖骨附近",
                },
            )

        self.assertIsNone(case.patient_input.body_part)
        self.assertEqual(outcome.ai_fields, [])
        self.assertIn(
            "reject_reason=normalized_value_not_supported_by_source",
            "\n".join(logs.output),
        )

    async def test_semantic_ai_logs_request_parsed_value_and_final_decision(self):
        answer = "鎖骨附近怪怪的"
        with self.assertLogs(level="INFO") as logs:
            case, _, _ = await self._extract(
                "body_part",
                answer,
                {
                    "field": "body_part",
                    "normalized_value": "鎖骨",
                    "semantic_status": "available",
                    "confidence": 0.9,
                    "source_text": "鎖骨附近",
                },
            )

        output = "\n".join(logs.output)
        self.assertEqual(case.patient_input.body_part, "鎖骨")
        self.assertIn("[SEMANTIC_AI_REQUEST]", output)
        self.assertIn("[SEMANTIC_AI_PARSED]", output)
        self.assertIn("[SEMANTIC_AI_DECISION]", output)
        self.assertIn("accepted=true", output)

    def test_ai_cannot_overwrite_an_existing_confirmed_body_part(self):
        case = TriageCase(case_id="semantic-ai-no-overwrite", visit_type=VisitType.INITIAL)
        case.patient_input.body_part = "右下腹"
        extraction = SemanticExtraction(
            field="body_part",
            normalized_value="右邊",
            semantic_status="available",
            confidence=0.8,
            source_text="右邊",
            extractor="ai_batch",
        )

        with self.assertLogs(level="INFO") as logs:
            apply_semantic_extractions(case, [extraction])

        self.assertEqual(case.patient_input.body_part, "右下腹")
        self.assertIn("reject_reason=duplicate_existing_value", "\n".join(logs.output))

    def test_prompt_contains_current_question_and_weak_uncertainty_contract(self):
        case = TriageCase(case_id="semantic-ai-prompt", visit_type=VisitType.INITIAL)
        prompt = batch_extraction_service._build_batch_prompt(
            case,
            {"severity": "不影響吧"},
            ["severity"],
        )

        self.assertIn("current_question_field", prompt)
        self.assertIn("請問症狀程度", prompt)
        self.assertIn("不影響吧", prompt)
        self.assertIn("弱語氣不等於無法回答", prompt)
        self.assertIn("ambiguous 只用於互相衝突", prompt)


if __name__ == "__main__":
    unittest.main()
