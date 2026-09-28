from models import ChatRequest, ChatResponse, RecommendRequest
from recommend import recommend
from triage import (
    AVAILABILITY_QUESTIONS,
    SYMPTOM_QUESTIONS,
    collect_symptoms,
    parse_availability_answer,
    parse_basic_info,
)


def step(chat_request: ChatRequest) -> ChatResponse:
    triage_case = chat_request.triage_case
    stage = triage_case.conversation_state.stage if triage_case else "collecting_basic"

    if stage == "collecting_basic":
        triage_case = parse_basic_info(chat_request)
        triage_case.visit_type = chat_request.visit_type
        triage_case.conversation_state.stage = "collecting"
        return ChatResponse(
            case_id=triage_case.case_id,
            reply=SYMPTOM_QUESTIONS,
            needMoreInfo=True,
            triage_case=triage_case,
        )

    if stage == "collecting_availability":
        triage_case = parse_availability_answer(chat_request)
        triage_case.conversation_state.stage = "complete"
        triage_case.conversation_state.is_complete = True

        preference = "醫師專長優先" if triage_case.preferences.specialty_priority else "時間優先"
        result = recommend(RecommendRequest(triage_case=triage_case, preference=preference))

        return ChatResponse(
            case_id=triage_case.case_id,
            reply="",
            needMoreInfo=False,
            triage_case=triage_case,
            recommendation=result,
        )

    result = collect_symptoms(chat_request)
    triage_case = result.triage_case

    if triage_case.triage.urgency_level == "high":
        return ChatResponse(
            case_id=triage_case.case_id,
            reply=result.reply,
            needMoreInfo=False,
            triage_case=triage_case,
        )

    if result.needMoreInfo:
        return ChatResponse(
            case_id=triage_case.case_id,
            reply=result.reply,
            needMoreInfo=True,
            triage_case=triage_case,
        )

    triage_case.conversation_state.stage = "collecting_availability"
    return ChatResponse(
        case_id=triage_case.case_id,
        reply=f"{result.reply}\n\n{AVAILABILITY_QUESTIONS}",
        needMoreInfo=True,
        triage_case=triage_case,
    )
