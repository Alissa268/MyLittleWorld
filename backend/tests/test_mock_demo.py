from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app.main import app
from app.schemas import DepartmentResult, PatientInput, TriageCase, VisitType
from app.services.case_store import _CASES, _RECOMMENDATIONS_BY_CASE, get_case
from app.services.schedule_filter import TAIPEI_ZONE
from app.services.specialty_scoring import score_doctor_deterministically, score_doctor_specialties


class MockDemoPrepareTest(TestCase):
    def setUp(self):
        self.client = TestClient(app)
        self.departments = [
            {"dept_id": 91837, "parent_dept": "DB 骨科部", "child_dept": "一般骨科"},
            {"dept_id": 55, "parent_dept": "內科部", "child_dept": "一般內科"},
        ]
        self.fetch = patch("app.routes.mock_demo.fetch_active_departments", return_value=self.departments)
        self.fetch.start()
        self.addCleanup(self.fetch.stop)
        self.case_ids = []

    def tearDown(self):
        for case_id in self.case_ids:
            _CASES.pop(case_id, None)
            _RECOMMENDATIONS_BY_CASE.pop(case_id, None)

    def prepare(self):
        response = self.client.post("/mock-demo/prepare", json={})
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        self.case_ids.append(result["case_id"])
        return result, get_case(result["case_id"])

    def test_creates_fixed_confirmed_initial_case_from_db(self):
        forbidden = AsyncMock(side_effect=AssertionError("MockDemo must never call LLM"))
        with patch("app.services.ai_service.complete_runtime_json", forbidden), patch(
            "app.services.specialty_scoring.complete_prompt", forbidden
        ):
            result, case = self.prepare()
        forbidden.assert_not_called()
        self.assertRegex(case.case_id, r"^case_mockdemo_[0-9a-f]{8}$")
        self.assertEqual(case.visit_type, VisitType.INITIAL)
        self.assertTrue(case.confirmed)
        self.assertTrue(case.conversation_state.is_complete)
        self.assertTrue(case.conversation_state.confirmed)
        self.assertFalse(case.conversation_state.awaiting_confirmation)
        self.assertEqual(case.conversation_state.stage, "recommending")
        self.assertEqual(case.department_result.childDept, "一般骨科")
        self.assertEqual(case.department_result.dept_id, 91837)
        self.assertEqual(case.department_result.parentDept, "DB 骨科部")
        self.assertEqual(case.department_result.confidence, 1.0)
        self.assertEqual(case.patient_input.symptom, "右膝疼痛，上下樓梯及走久時較明顯，偶爾腫脹")
        self.assertEqual(case.patient_input.body_part, "右膝")
        self.assertEqual(case.patient_input.duration, "約兩週")
        self.assertEqual(case.patient_input.severity, "目前仍可正常行走，但走久或上下樓梯時疼痛較明顯")
        self.assertEqual(case.patient_input.accompanying_symptoms, ["偶爾腫脹"])
        self.assertEqual(case.patient_input.red_flags, [])
        self.assertTrue(case.patient_input.red_flags_checked)
        self.assertEqual(case.availability.preferred_sessions, ["上午"])
        self.assertTrue(case.availability.can_take_leave)
        self.assertEqual(case.availability.preferred_dates, [])
        self.assertEqual(case.availability.preferred_days, [])
        self.assertTrue(case.preferences.specialty_priority)
        self.assertEqual(case.preferences.doctor_preference, "不限")
        self.assertEqual(case.preferences.hospital_preference, "台北榮總")
        self.assertFalse(case.triage.need_more_info)
        self.assertFalse(case.triage.warning_required)
        self.assertIsNone(case.triage.warning_message)
        self.assertTrue(case.triage.is_final)
        self.assertFalse(result["needMoreInfo"])
        self.assertEqual(result["reply"], "已完成掛號需求整理。")
        self.assertEqual(result["department_result"], result["triage_case"]["department_result"])
        self.assertEqual(case.history_records, [])

    def test_department_id_follows_changed_db_record(self):
        self.departments[0]["dept_id"] = 24680
        result, _ = self.prepare()
        self.assertEqual(result["department_result"]["dept_id"], 24680)

    def test_missing_ambiguous_or_idless_department_returns_error_without_saving(self):
        for rows in ([], [self.departments[1]], [self.departments[0]] * 2,
                     [{"dept_id": None, "child_dept": "一般骨科"}]):
            with self.subTest(rows=rows), patch("app.routes.mock_demo.fetch_active_departments", return_value=rows):
                before = set(_CASES)
                response = self.client.post("/mock-demo/prepare", json={})
                self.assertEqual(response.status_code, 503)
                self.assertIn("正式 DB", response.json()["detail"])
                self.assertEqual(set(_CASES), before)

    def test_chat_content_is_rejected(self):
        response = self.client.post("/mock-demo/prepare", json={"message": "任意聊天"})
        self.assertEqual(response.status_code, 422)

    def test_each_prepare_creates_a_new_case(self):
        first, _ = self.prepare()
        second, _ = self.prepare()
        self.assertNotEqual(first["case_id"], second["case_id"])

    def test_recommend_and_script_keep_db_schedule_identity_and_never_call_ai(self):
        forbidden = AsyncMock(side_effect=AssertionError("MockDemo must never call LLM"))
        future = (datetime.now(TAIPEI_ZONE) + timedelta(days=3)).date()
        row = {
            "dept_id": 91837, "parent_dept": "DB 骨科部", "child_dept": "一般骨科",
            "doctor": "SQL測試醫師", "doctor_id": "db-doctor-42", "schedule_id": "db-slot-97",
            "date": future, "session": "上午", "room": "DB診間", "slot": "DB診間",
            "specialty_tags": "膝關節、運動傷害", "status": "open", "visit_type": "初診",
            "source": "db", "is_placeholder": False,
        }
        with patch("app.services.specialty_scoring.get_settings", return_value=SimpleNamespace(
            ai_doctor_scoring_enabled=True, cerebras_api_key="test-key"
        )), patch("app.services.specialty_scoring.complete_prompt", forbidden), patch(
            "app.services.appointment_service.detect_department_result", forbidden
        ), patch("app.services.appointment_service.fetch_available_slots", return_value=[row]) as slots:
            result, _ = self.prepare()
            response = self.client.post("/recommend", json={"case_id": result["case_id"], "visit_type": "initial"})
            self.assertEqual(response.status_code, 200, response.text)
            slots.assert_called_once_with("一般骨科", search_days=21, max_slots=None,
                                          schedule_visit_type="初診", department_id=91837)
            recommendation = response.json()["recommendations"]["specialty_first"][0]
            for key in ("doctor", "doctor_id", "schedule_id", "room", "dept_id"):
                self.assertEqual(recommendation[key], row[key])
            self.assertEqual(recommendation["date"], future.isoformat())
            script = self.client.post("/generate_script", json={
                "case_id": result["case_id"], "recommendation_id": recommendation["recommendation_id"],
            })
            self.assertEqual(script.status_code, 200, script.text)
            self.assertTrue(script.json()["isSuccess"])
            self.assertTrue(script.json()["steps"])
        forbidden.assert_not_called()

    def test_no_morning_slots_relaxes_to_available_afternoon(self):
        result, _ = self.prepare()
        future = (datetime.now(TAIPEI_ZONE) + timedelta(days=3)).date()
        row = {"dept_id": 91837, "parent_dept": "DB 骨科部", "child_dept": "一般骨科",
               "doctor": "SQL醫師", "doctor_id": "42", "schedule_id": "97", "date": future,
               "session": "下午", "room": "201", "specialty_tags": "膝關節", "status": "open",
               "visit_type": "初診", "is_placeholder": False}
        with patch("app.services.appointment_service.fetch_available_slots", return_value=[row]):
            response = self.client.post("/recommend", json={"case_id": result["case_id"], "visit_type": "initial"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["recommendations"]["specialty_first"][0]["session"], "下午")


class MockDemoScoringTest(IsolatedAsyncioTestCase):
    async def test_enabled_ai_is_never_called_for_mock_demo(self):
        case = TriageCase(case_id="case_mockdemo_ab12cd34", patient_input=PatientInput(symptom="右膝疼痛"))
        department = DepartmentResult(childDept="一般骨科")
        row = {"doctor_id": "sql-doctor", "doctor": "SQL醫師", "child_dept": "一般骨科", "specialty_tags": "膝關節"}
        forbidden = AsyncMock(side_effect=AssertionError("LLM was called"))
        with patch("app.services.specialty_scoring.get_settings", return_value=SimpleNamespace(
            ai_doctor_scoring_enabled=True, cerebras_api_key="test-key"
        )), patch("app.services.specialty_scoring._ai_available", return_value=True) as availability, patch(
            "app.services.specialty_scoring.complete_prompt", forbidden
        ):
            scores = await score_doctor_specialties(case, department, [row])
        forbidden.assert_not_called()
        availability.assert_not_called()
        self.assertEqual(scores["sql-doctor"], score_doctor_deterministically(case, department, row))
