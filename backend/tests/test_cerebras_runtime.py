from __future__ import annotations

import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.schemas import BatchAnswer, TriageCase, VisitType
from app.services import ai_service, batch_extraction_service
from app.services.batch_extraction_service import extract_batch_answers


def _settings(*, key: str = "cerebras-test-key") -> SimpleNamespace:
    return SimpleNamespace(
        ai_provider="gemini",  # A stale environment value must not switch runtime.
        batch_extraction_provider="gemini",
        cerebras_api_key=key,
        cerebras_model="gpt-oss-120b",
        google_api_key="",
        llm_model="gemini-must-not-run",
        ai_timeout_seconds=1.0,
    )


class CerebrasRuntimeContractTest(unittest.IsolatedAsyncioTestCase):
    def test_cerebras_key_is_sufficient_without_google_key(self):
        self.assertTrue(ai_service.runtime_ai_available(_settings()))
        self.assertFalse(ai_service.runtime_ai_available(_settings(key="")))

    async def test_default_completion_cannot_be_switched_to_gemini_by_stale_config(self):
        cerebras = AsyncMock(return_value='{"ok":true}')
        gemini_client = SimpleNamespace(
            aio=SimpleNamespace(
                models=SimpleNamespace(
                    generate_content=AsyncMock(
                        side_effect=AssertionError("Gemini runtime call is forbidden")
                    )
                )
            )
        )
        with patch.object(ai_service, "get_settings", return_value=_settings()), patch.object(
            ai_service, "initialize_ai", return_value=None
        ), patch.object(ai_service, "_complete_cerebras", new=cerebras), patch.object(
            ai_service, "_google_client", gemini_client
        ):
            result = await ai_service.complete_prompt("runtime")

        self.assertEqual(result, '{"ok":true}')
        cerebras.assert_awaited_once()
        gemini_client.aio.models.generate_content.assert_not_awaited()

    async def test_runtime_json_uses_cerebras_json_mode_and_fixed_model_contract(self):
        provider = AsyncMock(return_value='{"extractions":[]}')
        with patch.object(ai_service, "get_settings", return_value=_settings()), patch.object(
            ai_service, "complete_prompt", new=provider
        ):
            result = await ai_service.complete_runtime_json(
                "extract",
                purpose="semantic_extraction",
            )

        self.assertEqual(result, '{"extractions":[]}')
        provider.assert_awaited_once_with(
            "extract",
            provider="cerebras",
            json_mode=True,
        )
        self.assertEqual(ai_service.RUNTIME_AI_MODEL, "gpt-oss-120b")

    async def test_keyed_free_text_uses_shared_cerebras_interpreter(self):
        from app.services import rag_triage_adapter
        case = TriageCase(case_id="semantic", visit_type=VisitType.INITIAL)
        provider = AsyncMock(return_value=json.dumps({
            "semantic_extractions": [{"field": "duration", "normalized_value": "1週",
                "source_text": "上禮拜", "semantic_status": "available", "confidence": 0.99}],
            "pending_answer": None, "ttas_evidence": [], "time_preferences": [],
            "prior_answer_reference": None, "department_preference": None,
            "dialogue_act": {"act": "new_information", "source_text": "上禮拜", "confidence": 0.99},
        }))
        with patch.object(batch_extraction_service, "get_settings", return_value=_settings()), patch.object(
            rag_triage_adapter, "_ai_available", return_value=True,
        ), patch.object(rag_triage_adapter, "complete_prompt", provider):
            result = await extract_batch_answers(case, [BatchAnswer(key="duration", answer="上禮拜開始")])
        provider.assert_awaited_once()
        self.assertEqual(case.patient_input.duration, "1週")
        self.assertTrue(result.ai_attempted)

    async def test_provider_timeout_is_fail_closed_without_semantic_fallback(self):
        from app.services import rag_triage_adapter
        case = TriageCase(case_id="timeout")
        with patch.object(batch_extraction_service, "get_settings", return_value=_settings()), patch.object(
            rag_triage_adapter, "_ai_available", return_value=True,
        ), patch.object(rag_triage_adapter, "complete_prompt", AsyncMock(side_effect=TimeoutError())):
            result = await extract_batch_answers(case, [BatchAnswer(key="body_part", answer="右腳")])
        self.assertIsNone(case.patient_input.body_part)
        self.assertEqual(result.accepted_fields, [])
        self.assertEqual(result.fallback_reason, "turn_interpretation_unavailable_or_invalid")
