import logging

from fastapi import APIRouter, HTTPException

from app.config import get_settings
from app.schemas import ChatRequest, ConversationStage, QuestionItem, TriageCase, TriageResult, VisitType
from app.services.ai_reply_generator import build_department_confirmation_reply, generate_triage_reply
from app.services.appointment_service import DepartmentResolutionError, detect_department_result
from app.services.batch_extraction_service import extract_batch_answers
from app.services.batch_question_service import build_question_batch
from app.services.case_store import create_case, get_case, save_case
from app.services.chat_perf import (
    ai_phase,
    begin_chat_perf,
    finish_chat_perf,
    record_chat_response_trace,
    record_chat_route_trace,
)
from app.services.department_preference_service import capture_department_preference
from app.services.rag_triage_adapter import merge_ai_next_question, refine_case_with_ai
from app.services.rule_engine import apply_user_message, evaluate_urgency
from app.services.semantic_refinement_gate import (
    capture_deterministic_parse_snapshot,
    decide_semantic_refinement,
)

router = APIRouter(prefix="/chat", tags=["chat"])
logger = logging.getLogger(__name__)

DEPARTMENT_CLARIFICATION_KEY = "department_clarification"
DEPARTMENT_CLARIFICATION_TEXT = (
    "目前提供的資訊還不足以確定最合適的科別，我再確認一點："
    "除了目前描述的不舒服之外，還有其他明顯症狀嗎？"
)
DEPARTMENT_UNRESOLVED_REPLY = (
    "目前仍無法安全地自動判定唯一科別，系統沒有替你套用預設科別。"
    "請改用手動選科，或洽醫院掛號服務協助。"
)


@router.post("", response_model=TriageResult)
async def chat(req: ChatRequest) -> TriageResult:
    perf, perf_token = begin_chat_perf()
    with perf.measure("case_prepare"):
        case = _resolve_case(req)
        _apply_visit_type(case, req.visit_type)
        settings = get_settings()
        batch_enabled = bool(settings.batch_triage_enabled)
        batch_mode = (
            batch_enabled
            and case.visit_type in {VisitType.INITIAL, VisitType.FOLLOWUP}
            and (bool(req.answers) or not _request_has_text_input(req))
        )
    record_chat_route_trace(case_id=case.case_id, triage_branch="rule_engine")
    logger.info(
        "chat request case_id=%s confirmed=%s has_message=%s has_triage_case=%s history_len=%s stage=%s",
        case.case_id,
        req.confirmed,
        bool(req.message),
        req.triage_case is not None,
        len(case.history_records),
        case.conversation_state.stage,
    )

    if req.answers and not batch_enabled:
        finish_chat_perf(perf, perf_token)
        raise HTTPException(status_code=400, detail="batch triage 尚未啟用。")

    if case.visit_type == VisitType.RETURN_VISIT:
        with perf.measure("case_store"):
            save_case(case)
        reply = "回診不進行完整症狀分診，請使用 /followup/recommend 提供原科別、原醫師與可看診時段。"
        response = TriageResult(
            case_id=case.case_id,
            triage_case=case,
            conversation_state=case.conversation_state,
            triage=case.triage,
            department_result=case.department_result,
            next_question=None,
            reply=reply,
            needMoreInfo=True,
            triage_reasons=case.triage.reasons,
            question_batch=[],
        )
        record_chat_response_trace(deterministic_next_question=None, final_reply=reply)
        finish_chat_perf(perf, perf_token)
        return response

    if case.visit_type == VisitType.QUICK_SEARCH:
        with perf.measure("case_store"):
            save_case(case)
        finish_chat_perf(perf, perf_token)
        raise HTTPException(
            status_code=400,
            detail="quick_search 不進行問診，請改呼叫 /schedules/search。",
        )

    if req.revision_requested:
        with perf.measure("deterministic_parse"):
            _begin_revision(case)
        with perf.measure("case_store"):
            save_case(case)
        reply = "好的，我會延續這次問診紀錄重新整理。請補充想修改的症狀、科別、嚴重程度、看診日期或時段。"
        logger.info(
            "chat revision requested case_id=%s history_len=%s",
            case.case_id,
            len(case.history_records),
        )
        with perf.measure("response_build"):
            response = TriageResult(
                case_id=case.case_id,
                triage_case=case,
                conversation_state=case.conversation_state,
                triage=case.triage,
                department_result=case.department_result,
                next_question=reply,
                reply=reply,
                needMoreInfo=True,
                triage_reasons=case.triage.reasons,
            )
        record_chat_response_trace(
            deterministic_next_question=reply,
            final_reply=reply,
        )
        finish_chat_perf(perf, perf_token)
        return response

    with perf.measure("deterministic_parse"):
        ai_suggestion = None
        department_preference_name = None
        has_user_input = False
        before_patient = case.patient_input.model_dump()
        before_availability = case.availability.model_dump()
        parse_snapshot = capture_deterministic_parse_snapshot(case)
        user_text_parts: list[str] = []
        messages = req.messages or []
        should_auto_revision = _should_treat_user_input_as_revision(case, req)
        if should_auto_revision:
            _begin_revision(case)
            logger.info(
                "chat auto revision case_id=%s stage=%s history_len=%s",
                case.case_id,
                case.conversation_state.stage,
                len(case.history_records),
            )
        if req.answers:
            batch_outcome = await extract_batch_answers(case, req.answers)
            if "requested_department" in batch_outcome.accepted_fields:
                department_preference_name = case.patient_input.requested_department_name
            user_text_parts.extend(answer.answer for answer in req.answers if answer.answer.strip())
            has_user_input = bool(user_text_parts)
            logger.info(
                "chat batch extraction case_id=%s deterministic_fields=%s ai_fields=%s unresolved_fields=%s ai_attempted=%s fallback_reason=%s",
                case.case_id,
                batch_outcome.deterministic_fields,
                batch_outcome.ai_fields,
                batch_outcome.unresolved_fields,
                batch_outcome.ai_attempted,
                batch_outcome.fallback_reason,
            )
        elif req.message:
            apply_user_message(case, req.message)
            preference = capture_department_preference(case, req.message)
            if preference and preference.resolved:
                department_preference_name = preference.name
            user_text_parts.append(req.message)
            has_user_input = True
        elif messages:
            for message in messages:
                if message.role == "user":
                    apply_user_message(case, message.content)
                    preference = capture_department_preference(case, message.content)
                    if preference and preference.resolved:
                        department_preference_name = preference.name
                    user_text_parts.append(message.content)
                    has_user_input = True
        if has_user_input:
            logger.info(
                "chat patient updated case_id=%s before_patient=%s after_patient=%s before_availability=%s after_availability=%s last_question_key=%s history_len=%s",
                case.case_id,
                before_patient,
                case.patient_input.model_dump(),
                before_availability,
                case.availability.model_dump(),
                case.conversation_state.last_question_key,
                len(case.history_records),
            )

        _sync_confirmation_flags(case)
    if has_user_input and not req.answers:
        semantic_decision = decide_semantic_refinement(
            before=parse_snapshot,
            after=case,
            user_text="\n".join(user_text_parts),
            request_confirmed=req.confirmed,
        )
        logger.info(
            "chat semantic refinement decision case_id=%s should_call=%s reason=%s target_field=%s reliable_fields=%s",
            case.case_id,
            semantic_decision.should_call,
            semantic_decision.reason,
            semantic_decision.target_field,
            semantic_decision.reliable_fields,
        )
        if semantic_decision.reason == "off_topic_for_requested_field" and semantic_decision.target_field:
            target = semantic_decision.target_field
            case.conversation_state.field_statuses[target] = "unknown"
            case.conversation_state.field_confidence[target] = 0.0
            case.conversation_state.clarification_reasons[target] = (
                "answer did not pass strict field validation"
            )
        if semantic_decision.should_call:
            with perf.measure("semantic_refinement"), ai_phase("semantic_refinement"):
                ai_suggestion = await refine_case_with_ai(case)

    with perf.measure("rule_engine"):
        case.triage = evaluate_urgency(case, mark_next_question=not batch_mode)
        ai_attempted_override = merge_ai_next_question(case, ai_suggestion)
        case.conversation_state.is_complete = not case.triage.need_more_info
        question_batch = []
        if batch_mode and case.triage.need_more_info:
            question_batch = build_question_batch(
                case,
                increment_existing=bool(req.answers),
            )
            if (
                department_preference_name
                and question_batch
                and question_batch[0].key == "symptom"
            ):
                question_batch[0] = question_batch[0].model_copy(
                    update={
                        "question": _department_preference_acknowledgement(
                            department_preference_name,
                            question_batch[0].question,
                        )
                    }
                )
            case.triage.next_question = question_batch[0].question if question_batch else None
    logger.info(
        "chat triage evaluated case_id=%s need_more_info=%s next_question=%s red_flags=%s red_flags_checked=%s availability=%s last_question_key=%s consumed_fields=%s question_attempts=%s field_statuses=%s field_confidence=%s ai_attempted_override=%s",
        case.case_id,
        case.triage.need_more_info,
        case.triage.next_question,
        case.patient_input.red_flags,
        case.patient_input.red_flags_checked,
        case.availability.model_dump(),
        case.conversation_state.last_question_key,
        case.conversation_state.consumed_fields,
        case.conversation_state.question_attempts,
        case.conversation_state.field_statuses,
        case.conversation_state.field_confidence,
        ai_attempted_override,
    )

    with perf.measure("department_detection"), ai_phase("department_detection"):
        department_status = case.conversation_state.field_statuses.get("department")
        if (
            not case.triage.need_more_info
            and case.department_result is None
            and department_status != "unresolved_final"
        ):
            try:
                case.department_result = await detect_department_result(case)
            except DepartmentResolutionError as exc:
                question_batch = _handle_department_resolution_failure(
                    case,
                    str(exc),
                    batch_mode=batch_mode,
                )

    with perf.measure("rule_engine"):
        if case.conversation_state.field_statuses.get("department") == "unresolved_final":
            case.confirmed = False
            case.conversation_state.stage = ConversationStage.COLLECTING
            case.conversation_state.awaiting_confirmation = False
            case.conversation_state.confirmed = False
            case.conversation_state.is_complete = False
        elif case.triage.need_more_info:
            case.confirmed = False
            case.conversation_state.stage = ConversationStage.COLLECTING
            case.conversation_state.awaiting_confirmation = False
            case.conversation_state.confirmed = False
        elif req.confirmed:
            case.confirmed = True
            case.conversation_state.stage = ConversationStage.RECOMMENDING
            case.conversation_state.awaiting_confirmation = False
            case.conversation_state.confirmed = True
        elif case.confirmed or case.conversation_state.confirmed:
            case.confirmed = True
            case.conversation_state.stage = ConversationStage.RECOMMENDING
            case.conversation_state.awaiting_confirmation = False
            case.conversation_state.confirmed = True
        else:
            case.confirmed = False
            case.conversation_state.stage = ConversationStage.WAITING_CONFIRMATION
            case.conversation_state.awaiting_confirmation = True
            case.conversation_state.confirmed = False

    with perf.measure("case_store"):
        save_case(case)
    logger.info(
        "chat response case_id=%s stage=%s is_complete=%s awaiting_confirmation=%s confirmed=%s history_len=%s next_question=%s last_question_key=%s consumed_fields=%s availability=%s question_attempts=%s field_statuses=%s red_flags_checked=%s ai_attempted_override=%s",
        case.case_id,
        case.conversation_state.stage,
        case.conversation_state.is_complete,
        case.conversation_state.awaiting_confirmation,
        case.conversation_state.confirmed,
        len(case.history_records),
        case.triage.next_question,
        case.conversation_state.last_question_key,
        case.conversation_state.consumed_fields,
        case.availability.model_dump(),
        case.conversation_state.question_attempts,
        case.conversation_state.field_statuses,
        case.patient_input.red_flags_checked,
        ai_attempted_override,
    )

    reply = "請一次回答以下問題。" if question_batch else case.triage.next_question
    if (
        department_preference_name
        and question_batch
        and question_batch[0].key == "symptom"
    ):
        reply = question_batch[0].question
    if (
        department_preference_name
        and not question_batch
        and case.conversation_state.last_question_key == "symptom"
        and case.triage.next_question
    ):
        reply = _department_preference_acknowledgement(
            department_preference_name,
            case.triage.next_question,
        )
    if case.conversation_state.field_statuses.get("department") == "unresolved_final":
        reply = DEPARTMENT_UNRESOLVED_REPLY
    elif reply is None and case.conversation_state.awaiting_confirmation and case.department_result:
        reply = build_department_confirmation_reply(case)
    elif reply is None and case.conversation_state.confirmed:
        reply = "已確認分診結果，可呼叫 /recommend 取得推薦掛號方案。"

    if not batch_mode:
        with perf.measure("ai_reply_total"), ai_phase("ai_reply"):
            reply = await generate_triage_reply(
                case=case,
                next_question=case.triage.next_question,
                fallback_reply=reply or "",
            )

    with perf.measure("response_build"):
        response = TriageResult(
            case_id=case.case_id,
            triage_case=case,
            conversation_state=case.conversation_state,
            triage=case.triage,
            department_result=case.department_result,
            next_question=case.triage.next_question,
            reply=reply,
            needMoreInfo=case.triage.need_more_info,
            triage_reasons=case.triage.reasons,
            question_batch=question_batch,
        )
    record_chat_response_trace(
        deterministic_next_question=case.triage.next_question,
        final_reply=reply,
    )
    finish_chat_perf(perf, perf_token)
    return response


def _sync_confirmation_flags(case: TriageCase) -> None:
    if case.confirmed:
        case.conversation_state.confirmed = True
        case.conversation_state.awaiting_confirmation = False


def _department_preference_acknowledgement(name: str, symptom_question: str) -> str:
    return (
        f"收到，已記錄您希望看{name}。這是您指定的科別偏好；"
        f"為了協助確認是否適合，仍需要了解症狀。{symptom_question}"
    )


def _handle_department_resolution_failure(
    case: TriageCase,
    reason: str,
    *,
    batch_mode: bool,
) -> list[QuestionItem]:
    state = case.conversation_state
    attempts = state.question_attempts.get(DEPARTMENT_CLARIFICATION_KEY, 0)
    state.clarification_reasons["department"] = reason
    case.department_result = None
    if attempts == 0:
        state.question_attempts[DEPARTMENT_CLARIFICATION_KEY] = 1
        state.field_statuses["department"] = "unresolved"
        state.last_question_key = DEPARTMENT_CLARIFICATION_KEY
        case.triage.need_more_info = True
        case.triage.is_final = False
        case.triage.next_question = DEPARTMENT_CLARIFICATION_TEXT
        case.triage.reasons.append("科別無法唯一判定，進行一次受控補充詢問。")
        state.is_complete = False
        logger.warning(
            "[DEPARTMENT] detection_called=true candidate_count=unknown selected_dept_id=null "
            "selected_parent=null selected_child=null validation_result=clarification "
            "failure_reason=%s",
            reason,
        )
        if batch_mode:
            return [
                QuestionItem(
                    key=DEPARTMENT_CLARIFICATION_KEY,
                    question=DEPARTMENT_CLARIFICATION_TEXT,
                    question_id=DEPARTMENT_CLARIFICATION_KEY,
                    state_field="department_context",
                    required=True,
                    input_type="text",
                )
            ]
        return []

    state.field_statuses["department"] = "unresolved_final"
    state.last_question_key = None
    case.triage.need_more_info = False
    case.triage.is_final = False
    case.triage.next_question = None
    case.triage.reasons.append("一次補充後仍無法唯一判定科別；停止自動重試且未套用預設科別。")
    state.is_complete = False
    logger.warning(
        "[DEPARTMENT] detection_called=true candidate_count=unknown selected_dept_id=null "
        "selected_parent=null selected_child=null validation_result=unresolved_final "
        "failure_reason=%s",
        reason,
    )
    return []


def _apply_visit_type(case: TriageCase, requested: VisitType | None) -> None:
    if requested is None:
        return
    if case.visit_type is not None and case.visit_type != requested:
        raise HTTPException(
            status_code=409,
            detail="visit_type 已在問診開始時確定，不可於同一 case 中變更。",
        )
    if case.visit_type is None:
        case.visit_type = requested


def _resolve_case(req: ChatRequest) -> TriageCase:
    supplied = req.triage_case
    lookup_id = req.case_id or (supplied.case_id if supplied else None)
    stored = get_case(lookup_id) if lookup_id else None
    if supplied is not None and stored is not None:
        if supplied.visit_type is not None and stored.visit_type is not None and supplied.visit_type != stored.visit_type:
            raise HTTPException(
                status_code=409,
                detail="triage_case.visit_type 與已保存案件不一致。",
            )
        if supplied.visit_type is None:
            supplied.visit_type = stored.visit_type
    return supplied or stored or create_case(lookup_id)


def _request_has_text_input(req: ChatRequest) -> bool:
    if req.message and req.message.strip():
        return True
    return any(message.role == "user" and message.content.strip() for message in req.messages)


def _begin_revision(case: TriageCase) -> None:
    case.confirmed = False
    case.recommendation_generated = False
    case.script_generated = False
    case.selected_recommendation_id = None
    case.conversation_state.stage = ConversationStage.COLLECTING
    case.conversation_state.is_complete = False
    case.conversation_state.awaiting_confirmation = False
    case.conversation_state.confirmed = False
    case.conversation_state.revision_mode = True
    case.conversation_state.last_question_key = "revision"
    case.conversation_state.field_statuses.pop("department", None)
    case.conversation_state.clarification_reasons.pop("department", None)
    case.conversation_state.question_attempts.pop(DEPARTMENT_CLARIFICATION_KEY, None)


def _should_treat_user_input_as_revision(case: TriageCase, req: ChatRequest) -> bool:
    if req.confirmed or req.revision_requested:
        return False
    has_user_text = bool(req.message and req.message.strip()) or any(
        message.role == "user" and message.content.strip()
        for message in req.messages
    )
    if not has_user_text:
        return False
    return (
        case.confirmed
        or case.conversation_state.confirmed
        or case.conversation_state.awaiting_confirmation
        or case.conversation_state.stage in {
            ConversationStage.WAITING_CONFIRMATION,
            ConversationStage.RECOMMENDING,
            ConversationStage.SCRIPT_READY,
        }
    )
