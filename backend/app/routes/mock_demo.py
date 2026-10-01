from uuid import uuid4

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict

from app.db import fetch_active_departments
from app.schemas import (
    Availability, ConversationStage, ConversationState, DepartmentResult,
    PatientInput, Preferences, TriageCase, TriageResult, UrgencyResult, VisitType,
)
from app.services.case_store import save_case

router = APIRouter(prefix="/mock-demo", tags=["mock-demo"])


class MockDemoPrepareRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


@router.post("/prepare", response_model=TriageResult)
def prepare_mock_demo(req: MockDemoPrepareRequest) -> TriageResult:
    """Create the fixed recording case using canonical DB department identity only."""
    departments = fetch_active_departments()
    matches = [row for row in departments if row.get("child_dept") == "一般骨科"]
    if len(matches) != 1 or matches[0].get("dept_id") is None:
        raise HTTPException(
            status_code=503,
            detail="正式 DB 無法取得唯一的一般骨科科別資料，無法準備 MockDemo 案件。",
        )
    department = matches[0]
    case = TriageCase(
        case_id=f"case_mockdemo_{uuid4().hex[:8]}",
        visit_type=VisitType.INITIAL,
        patient_input=PatientInput(
            symptom="右膝疼痛，上下樓梯及走久時較明顯，偶爾腫脹",
            body_part="右膝",
            duration="約兩週",
            severity="目前仍可正常行走，但走久或上下樓梯時疼痛較明顯",
            accompanying_symptoms=["偶爾腫脹"],
            red_flags=[],
            red_flags_checked=True,
        ),
        availability=Availability(
            preferred_dates=[], preferred_days=[],
            preferred_sessions=["上午"], can_take_leave=True,
        ),
        preferences=Preferences(
            specialty_priority=True, doctor_preference="不限", hospital_preference="台北榮總",
        ),
        department_result=DepartmentResult(
            dept_id=department["dept_id"],
            parentDept=department["parent_dept"],
            childDept=department["child_dept"],
            confidence=1.0,
            reason=["右膝疼痛且上下樓梯及走久時較明顯，建議先由一般骨科評估"],
        ),
        conversation_state=ConversationState(
            stage=ConversationStage.RECOMMENDING, is_complete=True,
            awaiting_confirmation=False, confirmed=True,
        ),
        triage=UrgencyResult(
            need_more_info=False, warning_required=False, warning_message=None, is_final=True,
        ),
        confirmed=True,
    )
    save_case(case)
    return TriageResult(
        case_id=case.case_id, triage_case=case,
        conversation_state=case.conversation_state, triage=case.triage,
        department_result=case.department_result,
        reply="已完成掛號需求整理。", needMoreInfo=False,
    )
