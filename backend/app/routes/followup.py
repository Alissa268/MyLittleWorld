from fastapi import APIRouter, HTTPException

from app.schemas import DepartmentResult, FollowupRecommendRequest, FollowupRecommendResponse, VisitType
from app.services.case_store import create_case, get_case, save_case, save_recommendations
from app.services.followup_service import recommend_followup

router = APIRouter(prefix="/followup", tags=["followup"])


@router.post("/recommend", response_model=FollowupRecommendResponse)
async def followup_recommend(req: FollowupRecommendRequest) -> FollowupRecommendResponse:
    if req.case_id:
        case = get_case(req.case_id)
        if case is None:
            raise HTTPException(status_code=404, detail="找不到指定 case_id，請重新建立回診查詢。")
        if case.visit_type not in {None, VisitType.RETURN_VISIT}:
            raise HTTPException(status_code=409, detail="此 case_id 不是回診流程，不能查詢回診班表。")
    else:
        case = create_case()

    child = (req.childDept or "").strip()
    parent = (req.parentDept or "").strip()
    if not child and case.department_result:
        child = case.department_result.childDept
        parent = parent or case.department_result.parentDept
    dept_id = req.dept_id or (case.department_result.dept_id if case.department_result else None)

    request = req.model_copy(
        update={
            "case_id": case.case_id,
            "childDept": child,
            "parentDept": parent or None,
            "dept_id": dept_id,
        }
    )

    if not request.childDept:
        raise HTTPException(status_code=422, detail="請提供 childDept 或有效 case_id。")

    case.visit_type = VisitType.RETURN_VISIT
    case.department_result = DepartmentResult(
        dept_id=request.dept_id,
        parentDept=request.parentDept or "",
        childDept=request.childDept,
        confidence=1.0,
        reason=["回診科別由使用者指定"],
    )
    result = await recommend_followup(request)
    case.department_result = result.department
    case.recommendation_generated = True
    save_case(case)
    save_recommendations(result.case_id, result.recommendations)
    return result
