from fastapi import APIRouter, HTTPException

from app.schemas import ConversationStage, RecommendRequest, RecommendationResult, VisitType
from app.services.appointment_service import (
    DepartmentResolutionError,
    detect_department_result,
    normalize_visit_type,
    recommend_appointments,
)
from app.services.ai_reply_generator import build_no_schedule_message
from app.services.case_store import create_case, get_case, save_case, save_recommendation_result
from app.services.rule_engine import apply_user_message, evaluate_urgency

router = APIRouter(prefix="/recommend", tags=["recommend"])


@router.post("", response_model=RecommendationResult)
async def recommend(req: RecommendRequest) -> RecommendationResult:
    supplied_case = req.triage_case
    lookup_id = req.case_id or (supplied_case.case_id if supplied_case else None)
    stored_case = get_case(lookup_id) if lookup_id else None
    if supplied_case is not None and stored_case is not None:
        if (
            supplied_case.visit_type is not None
            and stored_case.visit_type is not None
            and supplied_case.visit_type != stored_case.visit_type
        ):
            raise HTTPException(status_code=409, detail="triage_case.visit_type 與已保存案件不一致。")
        if supplied_case.visit_type is None:
            supplied_case.visit_type = stored_case.visit_type
    case = supplied_case or stored_case
    if case is None and req.userQuery:
        case = create_case(req.case_id)
        apply_user_message(case, req.userQuery)
        case.triage = evaluate_urgency(case)
        case.conversation_state.is_complete = not case.triage.need_more_info

    if case is None:
        raise HTTPException(status_code=400, detail="請提供 triage_case、case_id 或 userQuery。")

    request_visit_type = normalize_visit_type(req.visit_type) if req.visit_type is not None else None
    if case.visit_type is not None and request_visit_type is not None and case.visit_type != request_visit_type:
        raise HTTPException(
            status_code=409,
            detail="visit_type 與問診案件建立時的選擇不一致。",
        )
    effective_visit_type = case.visit_type or request_visit_type
    if effective_visit_type is None:
        raise HTTPException(
            status_code=422,
            detail="缺少合法 visit_type，請重新選擇 initial、followup、quick_search 或 return_visit。",
        )
    if effective_visit_type == VisitType.QUICK_SEARCH:
        raise HTTPException(
            status_code=400,
            detail="quick_search 不使用推薦流程，請改呼叫 /schedules/search。",
        )
    if case.visit_type is None:
        case.visit_type = effective_visit_type

    if case.conversation_state.is_complete is not True or case.triage.need_more_info is True:
        raise HTTPException(status_code=400, detail="triage_case 尚未完成，請先完成 /chat 多輪問答。")

    if req.confirmed:
        case.confirmed = True
        case.conversation_state.confirmed = True
        case.conversation_state.awaiting_confirmation = False

    if not (case.confirmed or case.conversation_state.confirmed):
        raise HTTPException(status_code=400, detail="triage_case 尚未確認，請先以 /chat 傳入 confirmed=true。")

    case.confirmed = True
    case.conversation_state.stage = ConversationStage.RECOMMENDING
    case.conversation_state.confirmed = True
    case.conversation_state.awaiting_confirmation = False

    try:
        if case.department_result is None:
            case.department_result = await detect_department_result(case)
        result = await recommend_appointments(case, visit_type=effective_visit_type)
    except DepartmentResolutionError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    case.recommendation_generated = True
    save_case(case)
    save_recommendation_result(result)

    if not result.recommendations.specialty_first or not result.recommendations.time_first:
        raise HTTPException(status_code=503, detail=build_no_schedule_message(case))
    return result
