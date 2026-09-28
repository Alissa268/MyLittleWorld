"""Natural-language clarification planning with Backend-owned completion gates."""

from __future__ import annotations

import json
import logging
import math
import re
from dataclasses import dataclass

from app.schemas import SemanticExtraction, TriageCase
from app.services.ai_service import complete_runtime_json
from app.services.confidence_scoring import ACCEPT_THRESHOLD
from app.services.rule_engine import QUESTION_TEXTS, RED_FLAG_QUESTION_KEY, mark_questions_asked

logger = logging.getLogger(__name__)
HARD_TURN_CAP = 8
FALLBACK_QUESTIONS = (
    "可以再描述一下目前最困擾你的不舒服，以及它什麼情況下會變嚴重嗎？",
    "剛才提到的不舒服，還有什麼變化或細節是你覺得重要的？",
    "可以補充目前症狀發生時的感受，或回答上一個追問嗎？",
)
UNRESOLVED_REPLY = "目前仍無法可靠釐清症狀，系統不會替你猜測科別；請改用手動選科或洽醫院掛號服務。"
_INTENT_PATTERN = re.compile(r"[a-z][a-z0-9_]{2,63}\Z")
_UNSAFE_QUESTION = re.compile(r"確診|診斷為|你患有|你得了|科別|醫師|醫生|掛號|哪天有空|看診日期|看診時段|什麼時段方便")
_DURATION_QUESTION = re.compile(r"多久|幾天|幾週|幾個月|什麼時候開始")
_MEDICAL_FIELDS = {"symptom", "body_part", "duration", "severity", "onset", "accompanying_symptoms"}
_DETAIL_FIELDS = _MEDICAL_FIELDS - {"symptom"}


def safety_screen_resolved(case: TriageCase) -> bool:
    return case.patient_input.red_flags_checked or bool(case.patient_input.red_flags)


@dataclass(frozen=True)
class ClarificationSuggestion:
    status: str
    question: str | None
    intent: str | None
    reason: str
    answered_intent: str | None = None
    answer_source_text: str | None = None
    answer_status: str | None = None
    answer_confidence: float | None = None


async def request_clarification(case: TriageCase, user_sources: list[str]) -> ClarificationSuggestion | None:
    """Treat the provider's JSON as a proposal, never a workflow transition."""
    state = case.conversation_state
    payload = {
        "conversation_history": [item.model_dump() for item in case.history_records[-24:]],
        "current_user_text": user_sources,
        "grounded_patient_evidence": case.patient_input.model_dump(),
        "semantic_extractions": [item.model_dump() for item in case.semantic_extractions[-20:]],
        "uncertainty_reasons": state.uncertainty_reasons,
        "next_information_needed": state.next_information_needed,
        "asked_clarification_intents": state.asked_clarification_intents,
        "pending_clarification_intent": state.pending_clarification_intent,
        "clarification_evidence": state.clarification_evidence,
    }
    prompt = (
        "你是醫療問診的自然澄清問題規劃器，只提出目前最能降低不確定性的一個追問。"
        "不要按固定欄位順序詢問，也不要要求補滿看診日期或時段。"
        "已經有可靠回答的資訊不要重問；若本輪回答了 pending intent，"
        "若本輪回答了 pending intent，用 answered_intent、逐字來自 current_user_text 的 "
        "answer_source_text、answer_status (answered、partial 或 unclear) 與 0 到 1 的 "
        "answer_confidence 表示；即使回答無法映射到既有症狀欄位，也要標示。"
        "partial 或 unclear 時可針對相同 intent 提出一個更精確且不重複的追問。"
        "如果症狀資訊足夠，可建議 status=sufficient，但最終完成與否由 Backend 決定。"
        "不得診斷、建議科別或醫師、修改患者事實或控制 workflow。"
        "只輸出 JSON，欄位為 status (clarification_needed 或 sufficient), question, intent, reason, "
        "answered_intent, answer_source_text, answer_status, answer_confidence。"
        "intent 用簡短英文 snake_case；沒有 pending 回答時，四個 answer 欄位用 null。\n"
        f"資料：{json.dumps(payload, ensure_ascii=False)}"
    )
    try:
        raw = await complete_runtime_json(prompt, purpose="conversation_clarification")
        data = json.loads(str(raw).strip())
        if not isinstance(data, dict):
            return None
        status = data.get("status")
        reason = data.get("reason")
        if status not in {"clarification_needed", "sufficient"} or not isinstance(reason, str):
            return None
        reason = reason.strip()[:240]
        if not reason:
            return None
        question = data.get("question")
        intent = data.get("intent")
        if status == "clarification_needed":
            if not isinstance(question, str) or not 4 <= len(question.strip()) <= 180:
                return None
            question = question.strip()
            if _UNSAFE_QUESTION.search(question) or not isinstance(intent, str) or not _INTENT_PATTERN.fullmatch(intent):
                return None
        else:
            question = None
            intent = None
        answered_intent = data.get("answered_intent")
        answer_source = data.get("answer_source_text")
        answer_status = data.get("answer_status")
        answer_confidence = data.get("answer_confidence")
        if not _grounded_clarification_answer(
            answered_intent, answer_source, answer_status, answer_confidence,
            state.pending_clarification_intent, user_sources,
        ):
            answered_intent = None
            answer_source = None
            answer_status = None
            answer_confidence = None
        return ClarificationSuggestion(
            status, question, intent, reason, answered_intent,
            answer_source.strip() if answer_source else None,
            answer_status, answer_confidence,
        )
    except Exception as exc:
        logger.warning("clarification provider failed case_id=%s error=%s", case.case_id, exc)
        return None


def advance_conversation(
    case: TriageCase,
    suggestion: ClarificationSuggestion | None,
    *,
    user_sources: list[str],
    current_extractions: list[SemanticExtraction],
) -> None:
    """Apply only validated clarification proposals and Backend-owned state gates."""
    state = case.conversation_state
    if state.clarification_status == "unresolved":
        _unresolved(case)
        return
    if state.clarification_status != "safety_check":
        state.turn_count += len(user_sources)

    grounded_answer = bool(suggestion) and _grounded_clarification_answer(
        suggestion.answered_intent, suggestion.answer_source_text,
        suggestion.answer_status, suggestion.answer_confidence,
        state.pending_clarification_intent, user_sources,
    )
    if grounded_answer and suggestion.answer_status == "answered" and suggestion.answer_confidence >= ACCEPT_THRESHOLD:
        state.clarification_evidence[suggestion.answered_intent] = suggestion.answer_source_text or ""
        state.pending_clarification_intent = None

    grounded_symptom = bool(case.patient_input.symptom) and any(
        case.patient_input.symptom in item.content
        for item in case.history_records if item.role == "user"
    )
    grounded_detail = bool(state.clarification_evidence) or any(
        item.field in _DETAIL_FIELDS
        and item.semantic_status in {"available", "partial"}
        and item.confidence >= ACCEPT_THRESHOLD
        and item.source_text
        and any(item.source_text in message.content for message in case.history_records if message.role == "user")
        for item in case.semantic_extractions
    )
    low_confidence = any(
        item.field in _MEDICAL_FIELDS and item.confidence < ACCEPT_THRESHOLD
        for item in current_extractions
    ) or any(
        field in _MEDICAL_FIELDS
        and reason == "AI extraction confidence below threshold"
        and state.field_confidence.get(field, 1.0) < ACCEPT_THRESHOLD
        for field, reason in state.clarification_reasons.items()
    )
    sufficient = (
        (state.clarification_status == "safety_check" or (suggestion is not None and suggestion.status == "sufficient"))
        and grounded_symptom
        and grounded_detail
        and not low_confidence
        and state.pending_clarification_intent is None
    )
    if sufficient:
        if safety_screen_resolved(case):
            state.clarification_status = "sufficient"
            state.uncertainty_reasons = []
            state.next_information_needed = []
            state.last_question_key = None
            state.is_complete = True
            case.triage.need_more_info = False
            case.triage.next_question = None
            case.triage.is_final = True
            case.triage.reasons.append("症狀資訊已通過 Backend 澄清完成條件；掛號偏好不是醫療完成門檻。")
            return
        state.clarification_status = "safety_check"
        state.uncertainty_reasons = ["急迫症狀篩檢尚未完成"]
        state.next_information_needed = ["確認是否有目前安全篩檢所列的急迫症狀"]
        state.is_complete = False
        mark_questions_asked(case, [RED_FLAG_QUESTION_KEY])
        case.triage.need_more_info = True
        case.triage.next_question = QUESTION_TEXTS[RED_FLAG_QUESTION_KEY]
        case.triage.is_final = False
        case.triage.reasons.append("症狀描述已足夠，仍須完成既有急迫症狀篩檢。")
        return

    if state.turn_count >= HARD_TURN_CAP:
        _unresolved(case)
        return

    pending_follow_up = bool(
        grounded_answer
        and suggestion.answer_status in {"partial", "unclear"}
        and suggestion.intent == state.pending_clarification_intent
    )
    valid_question = (
        suggestion is not None
        and suggestion.status == "clarification_needed"
        and (pending_follow_up or (
            state.pending_clarification_intent is None
            and suggestion.intent not in state.asked_clarification_intents
        ))
        and not any(item.role == "assistant" and item.content == suggestion.question for item in case.history_records)
        and not _asks_filled_duration(case, suggestion.intent, suggestion.question)
    )
    if valid_question:
        question = suggestion.question or ""
        if not pending_follow_up:
            state.pending_clarification_intent = suggestion.intent
            state.asked_clarification_intents.append(suggestion.intent or "")
        reason = suggestion.reason
    elif state.pending_clarification_intent:
        question = "可以先補充上一個追問的答案，或描述目前症狀有什麼新的變化嗎？"
        reason = "上一個澄清問題尚未可靠回答"
    else:
        question = next(
            (text for text in FALLBACK_QUESTIONS if not any(
                item.role == "assistant" and item.content == text for item in case.history_records
            )),
            FALLBACK_QUESTIONS[-1],
        )
        reason = "目前仍需更多可靠的症狀描述"
    if low_confidence:
        reason = "本輪症狀抽取信心不足，仍需釐清"
    elif not grounded_symptom:
        reason = "尚無 grounded 的主要症狀描述"
    elif not grounded_detail:
        reason = "目前仍缺少可區分症狀的細節"
    state.clarification_status = "clarifying"
    state.uncertainty_reasons = [reason]
    state.next_information_needed = [reason]
    state.last_question_key = None
    state.is_complete = False
    case.triage.need_more_info = True
    case.triage.next_question = question
    case.triage.is_final = False
    case.triage.reasons.append(f"自然對話澄清中：{reason}")


def _asks_filled_duration(case: TriageCase, intent: str | None, question: str | None) -> bool:
    return bool(
        case.patient_input.duration
        and ((intent and "duration" in intent) or (question and _DURATION_QUESTION.search(question)))
    )


def _grounded_clarification_answer(
    intent: object, source_text: object, status: object, confidence: object,
    pending_intent: str | None, user_sources: list[str],
) -> bool:
    return bool(
        pending_intent
        and intent == pending_intent
        and isinstance(source_text, str)
        and source_text.strip()
        and any(source_text.strip() in source for source in user_sources)
        and status in {"answered", "partial", "unclear"}
        and isinstance(confidence, (int, float))
        and not isinstance(confidence, bool)
        and math.isfinite(confidence)
        and 0 <= confidence <= 1
    )


def _unresolved(case: TriageCase) -> None:
    state = case.conversation_state
    state.clarification_status = "unresolved"
    state.uncertainty_reasons = ["多輪後仍無法可靠釐清症狀"]
    state.next_information_needed = []
    state.pending_clarification_intent = None
    state.is_complete = False
    state.last_question_key = None
    case.triage.need_more_info = True
    case.triage.next_question = None
    case.triage.is_final = False
    case.triage.reasons.append("已達自然澄清輪數上限；未自動補值或選科。")
