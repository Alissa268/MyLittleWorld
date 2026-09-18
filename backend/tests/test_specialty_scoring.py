from __future__ import annotations

import asyncio
import json
import unittest
from unittest.mock import AsyncMock

from app.schemas import DepartmentResult, TriageCase
from app.services import specialty_scoring
from app.services.rule_engine import apply_user_message
from app.services.specialty_scoring import (
    clamp_score,
    score_doctor_deterministically,
    score_doctor_specialties,
)


class SpecialtyScoringTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.original_settings = specialty_scoring.get_settings
        self.original_complete = specialty_scoring.complete_prompt

    def tearDown(self):
        specialty_scoring.get_settings = self.original_settings
        specialty_scoring.complete_prompt = self.original_complete

    def test_keyword_scoring(self):
        score = score_doctor_deterministically(
            _case("膝蓋走路疼痛"),
            _department(),
            _row(specialty_tags="膝關節、運動傷害、骨科"),
        )

        self.assertGreaterEqual(score.score, 0.7)
        self.assertIn("膝", score.reason)

    async def test_mocked_ai_success(self):
        class FakeSettings:
            cerebras_api_key = "test-key"
            ai_doctor_scoring_enabled = True

        async def fake_complete(_: str) -> str:
            return '{"scores": [{"doctor_id": "doc-1", "score": 0.91, "reason": "AI matched knee specialty"}]}'

        specialty_scoring.get_settings = lambda: FakeSettings()
        specialty_scoring.complete_prompt = fake_complete

        scores = await score_doctor_specialties(_case("膝蓋走路疼痛"), _department(), [_row()])

        self.assertEqual(scores["doc-1"].source, "ai")
        self.assertEqual(scores["doc-1"].score, 0.91)

    async def test_five_doctors_use_one_batch_ai_call_and_receive_distinct_results(self):
        class FakeSettings:
            cerebras_api_key = "test-key"
            ai_doctor_scoring_enabled = True

        rows = [
            _row(
                doctor_id=f"doc-{index}",
                doctor=f"醫師{index}",
                specialty_tags=f"專長{index}",
            )
            for index in range(1, 6)
        ]
        response = {
            "scores": [
                {
                    "doctor_id": f"doc-{index}",
                    "score": 0.5 + index * 0.05,
                    "reason": f"症狀方向與醫師{index}的專長{index}相關程度不同，依實際專長提供評估。",
                }
                for index in range(1, 6)
            ]
        }
        provider = AsyncMock(return_value=json.dumps(response, ensure_ascii=False))
        specialty_scoring.get_settings = lambda: FakeSettings()
        specialty_scoring.complete_prompt = provider

        scores = await score_doctor_specialties(_case("飯後胃痛、反胃"), _department(), rows)

        provider.assert_awaited_once()
        self.assertEqual(set(scores), {f"doc-{index}" for index in range(1, 6)})
        prompt = provider.await_args.args[0]
        self.assertIn("同科醫師仍須依實際專長拉開差異", prompt)
        self.assertIn("不可自行診斷疾病", prompt)
        self.assertIn("絕對不可超過 64 個中文字", prompt)
        self.assertIn("最適合", prompt)
        for index in range(1, 6):
            result = scores[f"doc-{index}"]
            self.assertEqual(result.source, "ai")
            self.assertIn(f"醫師{index}", result.reason)

    async def test_duplicate_schedule_rows_appear_once_in_ai_prompt(self):
        class FakeSettings:
            cerebras_api_key = "test-key"
            ai_doctor_scoring_enabled = True

        rows = [
            _row(date="2026-09-18"),
            _row(date="2026-09-22"),
            _row(date="2026-09-25"),
        ]
        provider = AsyncMock(
            return_value=json.dumps(
                {
                    "scores": [
                        {
                            "doctor_id": "doc-1",
                            "score": 0.88,
                            "reason": "目前症狀與膝關節不適相關，此醫師具膝關節與運動傷害專長。",
                        }
                    ]
                },
                ensure_ascii=False,
            )
        )
        specialty_scoring.get_settings = lambda: FakeSettings()
        specialty_scoring.complete_prompt = provider

        scores = await score_doctor_specialties(_case("膝蓋走路疼痛"), _department(), rows)

        provider.assert_awaited_once()
        prompt = provider.await_args.args[0]
        self.assertEqual(prompt.count('"doctor_id": "doc-1"'), 1)
        self.assertEqual(list(scores), ["doc-1"])

    async def test_ai_reason_is_hard_limited_to_64_characters(self):
        class FakeSettings:
            cerebras_api_key = "test-key"
            ai_doctor_scoring_enabled = True

        provider = AsyncMock(
            return_value=json.dumps(
                {
                    "scores": [
                        {
                            "doctor_id": "doc-1",
                            "score": 0.9,
                            "reason": "長" * 150,
                        }
                    ]
                },
                ensure_ascii=False,
            )
        )
        specialty_scoring.get_settings = lambda: FakeSettings()
        specialty_scoring.complete_prompt = provider

        scores = await score_doctor_specialties(_case("膝蓋走路疼痛"), _department(), [_row()])

        self.assertEqual(len(scores["doc-1"].reason), 64)
        self.assertEqual(scores["doc-1"].source, "ai")

    async def test_missing_ai_doctor_result_keeps_deterministic_fallback(self):
        class FakeSettings:
            cerebras_api_key = "test-key"
            ai_doctor_scoring_enabled = True

        rows = [
            _row(doctor_id="doc-1", doctor="醫師一"),
            _row(doctor_id="doc-2", doctor="醫師二"),
        ]
        provider = AsyncMock(
            return_value=json.dumps(
                {
                    "scores": [
                        {
                            "doctor_id": "doc-1",
                            "score": 0.9,
                            "reason": "目前膝部不適與此醫師的膝關節及運動傷害專長直接相關。",
                        }
                    ]
                },
                ensure_ascii=False,
            )
        )
        specialty_scoring.get_settings = lambda: FakeSettings()
        specialty_scoring.complete_prompt = provider

        scores = await score_doctor_specialties(_case("膝蓋走路疼痛"), _department(), rows)

        provider.assert_awaited_once()
        self.assertEqual(scores["doc-1"].source, "ai")
        self.assertEqual(scores["doc-2"].source, "deterministic")

    async def test_ai_exception_fallback(self):
        class FakeSettings:
            cerebras_api_key = "test-key"
            ai_doctor_scoring_enabled = True

        async def fail_complete(_: str) -> str:
            raise RuntimeError("ai failed")

        specialty_scoring.get_settings = lambda: FakeSettings()
        specialty_scoring.complete_prompt = fail_complete

        scores = await score_doctor_specialties(_case("膝蓋走路疼痛"), _department(), [_row()])

        self.assertEqual(scores["doc-1"].source, "deterministic")
        self.assertGreater(scores["doc-1"].score, 0)

    async def test_ai_timeout_fallback(self):
        class FakeSettings:
            cerebras_api_key = "test-key"
            ai_doctor_scoring_enabled = True

        async def timeout_complete(_: str) -> str:
            raise asyncio.TimeoutError("timeout")

        specialty_scoring.get_settings = lambda: FakeSettings()
        specialty_scoring.complete_prompt = timeout_complete

        scores = await score_doctor_specialties(_case("膝蓋走路疼痛"), _department(), [_row()])

        self.assertEqual(scores["doc-1"].source, "deterministic")

    async def test_invalid_doctor_rejection(self):
        class FakeSettings:
            cerebras_api_key = "test-key"
            ai_doctor_scoring_enabled = True

        async def fake_complete(_: str) -> str:
            return '{"scores": [{"doctor_id": "doc-x", "score": 0.99, "reason": "invented doctor"}]}'

        specialty_scoring.get_settings = lambda: FakeSettings()
        specialty_scoring.complete_prompt = fake_complete

        scores = await score_doctor_specialties(_case("膝蓋走路疼痛"), _department(), [_row()])

        self.assertEqual(scores["doc-1"].source, "deterministic")

    async def test_ai_scoring_is_disabled_by_default(self):
        class FakeSettings:
            cerebras_api_key = "test-key"
            ai_doctor_scoring_enabled = False

        async def forbidden_complete(_: str) -> str:
            raise AssertionError("doctor scoring must be deterministic when feature is disabled")

        specialty_scoring.get_settings = lambda: FakeSettings()
        specialty_scoring.complete_prompt = forbidden_complete

        scores = await score_doctor_specialties(_case("膝蓋走路疼痛"), _department(), [_row()])

        self.assertEqual(scores["doc-1"].source, "deterministic")

    def test_score_clamp(self):
        self.assertEqual(clamp_score(3), 1.0)
        self.assertEqual(clamp_score(-1), 0.0)
        self.assertEqual(clamp_score("bad"), 0.5)

    def test_empty_specialty_data(self):
        score = score_doctor_deterministically(_case("膝蓋痛"), _department(), _row(specialty_tags=""))

        self.assertEqual(score.score, 0.5)
        self.assertIn("專長資料不足", score.reason)

    def test_negated_chest_symptoms_do_not_match_specialty(self):
        score = score_doctor_deterministically(
            _case("頭暈一天，沒有胸痛、呼吸困難"),
            _department(),
            _row(specialty_tags="心臟、胸腔、肺"),
        )

        self.assertEqual(score.score, 0.5)
        self.assertNotIn("胸痛/呼吸困難", score.reason)

    def test_positive_chest_symptoms_match_specialty(self):
        score = score_doctor_deterministically(
            _case("突然胸痛、呼吸困難"),
            _department(),
            _row(specialty_tags="心臟、胸腔、肺"),
        )

        self.assertGreater(score.score, 0.5)
        self.assertIn("胸痛", score.reason)

    def test_wrong_department_is_rejected(self):
        score = score_doctor_deterministically(
            _case("膝蓋痛"),
            _department(),
            _row(child_dept="皮膚科", specialty_tags="膝關節"),
        )

        self.assertEqual(score.score, 0.0)
        self.assertIn("科別不符", score.reason)


def _case(message: str) -> TriageCase:
    case = TriageCase(case_id="case_specialty")
    apply_user_message(case, message)
    return case


def _department() -> DepartmentResult:
    return DepartmentResult(parentDept="外科系", childDept="一般骨科", confidence=0.8)


def _row(**override):
    row = {
        "doctor_id": "doc-1",
        "doctor": "測試醫師",
        "child_dept": "一般骨科",
        "specialty_tags": "膝關節、運動傷害、骨科",
        "date": "2026-09-18",
    }
    row.update(override)
    return row


if __name__ == "__main__":
    unittest.main()
