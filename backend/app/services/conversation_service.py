"""Natural-language clarification planning with Backend-owned completion gates."""

from __future__ import annotations

import json
import logging
import math
import re
from dataclasses import dataclass

from app.schemas import DialogueActInterpretation, PendingAnswerInterpretation, SemanticExtraction, TriageCase
from app.services.ai_service import complete_runtime_json
from app.services.confidence_scoring import ACCEPT_THRESHOLD
from app.services.rule_engine import QUESTION_TEXTS, RED_FLAG_QUESTION_KEY, mark_questions_asked

logger = logging.getLogger(__name__)
HARD_TURN_CAP = 8
SAFETY_ANSWER_CAP = 3
SAFETY_UNRESOLVED_REPLY = (
    "目前無法可靠確認急迫症狀，不能繼續一般掛號導引。請由醫療人員協助評估；"
    "若情況緊急，請立即尋求緊急醫療協助。"
)
SAFETY_RETRY_REPLY = "目前尚無法可靠理解這次安全回答。請說明上一個安全問題中您能確認的情況；不知道也可以直接說明。"
SAFETY_PENDING_INTENT = "safety_screen"
# Retained only for import compatibility and tests asserting they never reach patients.
FALLBACK_QUESTIONS = (
    "可以再描述一下目前最困擾你的不舒服，以及它什麼情況下會變嚴重嗎？",
    "剛才提到的不舒服，還有什麼變化或細節是你覺得重要的？",
    "可以補充目前症狀發生時的感受，或回答上一個追問嗎？",
)
NEUTRAL_PENDING_RETRIES = (
    "可以再補充和剛才問題相關的症狀細節嗎？",
    "可以換個方式描述剛才問題所詢問的具體情況嗎？",
    "請再說明一下和剛才問題相關的症狀變化或感受。",
)
UNRESOLVED_REPLY = "目前仍無法可靠釐清症狀，系統不會替你猜測科別；請改用手動選科或洽醫院掛號服務。"
TECHNICAL_CLARIFICATION_REPLY = "目前暫時無法可靠產生下一個問診問題，請稍後再試。"
_INTENT_PATTERN = re.compile(r"[a-z][a-z0-9_]{2,63}\Z")
_UNSAFE_QUESTION = re.compile(r"確診|診斷為|你患有|你得了|科|醫師|醫生|掛號|哪天有空|看診日期|看診時段|什麼時段方便")
_DURATION_QUESTION = re.compile(r"多久|幾天|幾週|幾個月|什麼時候開始")
_MULTI_QUESTION = re.compile(r"[?？].*[?？]|[;；]", re.DOTALL)
_MULTI_DIMENSION_QUESTION = re.compile(
    r"(?:另外|並且|還有|以及).{0,12}(?:是否|有沒有|多久|何時|哪裡|什麼|如何|多少|幾次|幾天)|"
    r"(?:如何|多少|多久|幾次|幾天)[^？?]{0,40}[，、](?:是否|有沒有|何時|哪裡)"
)
_INTERNAL_STATE_TEXT = re.compile(
    r"已記錄本輪新資訊|上一個澄清(?:面向|問題)|目前仍需更多可靠|"
    r"科別仍不確定|科別.*(?:不確定|未收斂)|workflow|工作流程|"
    r"本輪症狀抽取|尚無\s*grounded|目前仍缺少可區分症狀|安全篩檢所列|確認是否有目前安全"
)
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
    information_need: str | None = None
    rejection_reason: str | None = None
    repair_attempted: bool = False
    reply_source: str = "AI_PLANNER"


@dataclass(frozen=True)
class PendingAnswerClassification:
    answered_intent: str
    answer_source_text: str
    answer_status: str
    answer_confidence: float


def _pending_question(case: TriageCase) -> str | None:
    if case.conversation_state.pending_clarification_intent and case.conversation_state.pending_clarification_question:
        return case.conversation_state.pending_clarification_question
    return next(
        (item.content for item in reversed(case.history_records) if item.role == "assistant"),
        None,
    )


async def classify_pending_answer(
    case: TriageCase,
    current_user_text: list[str],
) -> PendingAnswerClassification | None:
    """Classify only whether the current turn answered the pending question."""
    pending_intent = case.conversation_state.pending_clarification_intent
    pending_question = _pending_question(case)
    if not pending_intent or not pending_question or not current_user_text:
        return None
    payload = {
        "pending_clarification_intent": pending_intent,
        "pending_question": pending_question,
        "current_user_text": current_user_text,
    }
    prompt = (
        "你只負責判斷 current_user_text 是否回答 pending_question 與 pending_clarification_intent。"
        "不要規劃下一題、不要做科別推理、不要診斷，也不要輸出或修改任何 workflow state。"
        "明確否定仍然是 answered，不需要出現正向症狀。例如問題『有沒有腰部或腹部疼痛？』，"
        "回答『沒有腰痛，也沒有腹痛』應輸出 answered_intent=pain_presence、"
        "answer_status=answered，並以該段逐字回答作 answer_source_text。"
        "程度問題『血尿的量多不多？』回答『只有一點點血絲』也應是 answered。"
        "只判斷本輪 current_user_text，不得引用 prior history。answer_source_text 必須是本輪回答中"
        "直接支持判定的最短連續逐字片段，不可改寫。"
        "answer_status 只能是 answered、partial、unclear 或 null；answer_confidence 必須是 0 到 1 的數字。"
        "若本輪沒有回答 pending question，四個欄位都輸出 null。"
        "只輸出固定 JSON：answered_intent, answer_status, answer_source_text, answer_confidence。\n"
        f"資料：{json.dumps(payload, ensure_ascii=False)}"
    )
    try:
        raw = await complete_runtime_json(prompt, purpose="pending_answer_classification")
        data = json.loads(str(raw).strip())
        expected_keys = {
            "answered_intent", "answer_status", "answer_source_text", "answer_confidence",
        }
        if not isinstance(data, dict) or set(data) != expected_keys:
            return None
        intent = data.get("answered_intent")
        source = data.get("answer_source_text")
        status = data.get("answer_status")
        confidence = data.get("answer_confidence")
        if not _grounded_clarification_answer(
            intent, source, status, confidence, pending_intent, current_user_text,
        ):
            return None
        return PendingAnswerClassification(
            answered_intent=intent,
            answer_source_text=source.strip(),
            answer_status=status,
            answer_confidence=float(confidence),
        )
    except Exception as exc:
        logger.warning(
            "pending answer classifier failed case_id=%s error_type=%s",
            case.case_id,
            type(exc).__name__,
        )
        return None


async def request_clarification(
    case: TriageCase,
    user_sources: list[str],
    *,
    classify_answer_fields: bool = True,
    focused_answer_status: str | None = None,
    dialogue_act: DialogueActInterpretation | None = None,
) -> ClarificationSuggestion | None:
    """Treat the provider's JSON as a proposal, never a workflow transition."""
    state = case.conversation_state
    safety_planning = state.clarification_status == "safety_check" and not safety_screen_resolved(case)
    required_intent = (
        SAFETY_PENDING_INTENT if safety_planning else
        state.pending_clarification_intent or state.department_next_question_intent
    )
    if required_intent in state.clarification_evidence:
        required_intent = None
    payload = {
        "conversation_history": [item.model_dump() for item in case.history_records[-24:]],
        "current_user_text": user_sources,
        "grounded_patient_evidence": case.patient_input.model_dump(),
        "semantic_extractions": [item.model_dump() for item in case.semantic_extractions[-20:]],
        "uncertainty_reasons": state.uncertainty_reasons,
        "next_information_needed": _patient_information_needs(state.next_information_needed),
        "asked_clarification_intents": state.asked_clarification_intents,
        "pending_clarification_intent": state.pending_clarification_intent,
        "pending_question": _pending_question(case) if state.pending_clarification_intent or safety_planning else None,
        "focused_pending_answer_status": focused_answer_status,
        "clarification_evidence": state.clarification_evidence,
        "department_status": state.department_status,
        "candidate_departments": [item.model_dump() for item in state.candidate_departments],
        "department_uncertainty_reason": state.department_uncertainty_reason,
        "department_next_information_needed": _patient_information_need(state.department_next_information_needed),
        "required_next_question_intent": required_intent,
        "safety_planning": safety_planning,
        "safety_screen_question": QUESTION_TEXTS[RED_FLAG_QUESTION_KEY] if safety_planning else None,
        "safety_answer_validation": state.safety_answer_validation,
        "dialogue_act": (dialogue_act or DialogueActInterpretation()).model_dump(),
    }
    answer_contract = (
        "請先比對 pending_question 與本輪回答，再規劃下一題；不要把回答中的其他新症狀或安全篩檢否認混作 pending 回答。"
        "若本輪以具體描述回答了 pending intent，用 answered_intent、逐字來自 current_user_text 的 "
        "answer_source_text、answer_status (answered、partial 或 unclear) 與 0 到 1 的 "
        "answer_confidence 表示；例如程度問題回答『只有一點』『不是很多』『明顯變多』『影響活動』，"
        "即使沒有 canonical severity extraction，也可以是 answered。"
        "回答欄位與整體 status 獨立，status=clarification_needed 時也須標記已回答的 pending intent。"
        if classify_answer_fields else
        "本輪 pending answer 已由 Turn Interpreter 與 semantic evidence 一併處理；你只規劃下一個問題，"
        "answered_intent、answer_source_text、answer_status、answer_confidence 必須全部輸出 null。"
    )
    prompt = (
        ("目前仍在安全澄清，不能改問科別問題。只針對目前原文與已接受證據中仍未確認的一個安全面向自然追問，"
         "不要重貼整份安全清單，不要把不知道、離題或部分否認當成整份陰性；不得新增醫療判級規則。"
         "intent 必須為 safety_screen；即使你認為已足夠，Backend 仍決定安全狀態。" if safety_planning else "") +
        "你是醫療問診的自然澄清問題規劃器，只提出目前最能降低不確定性的一個追問。"
        "不要按固定欄位順序詢問，也不要要求補滿看診日期或時段。"
        "已經有可靠回答的資訊不要重問。每個 intent 只對應一個資訊面向、一個聚焦問句、一次詢問要求；"
        "不得用連接詞在同一問句追加第二個臨床面向，即使整句只有一個問號也不可以；"
        "例如詢問程度時，不要在同一句再問疼痛或其他伴隨不適。"
        f"{answer_contract}"
        "partial 或 unclear 時維持同一 intent，提出一個更精確、不重複、只問同一面向的追問。"
        "dialogue_act=request_explanation 時，具體解釋目前要確認的單一資訊並提出可回答的聚焦問題；"
        "claims_already_answered 時，先參照 accepted clarification_evidence，不得重問已回答 intent，"
        "也不得僅因使用者表示說過就新增證據。若仍缺資訊，明確指出那一個資訊。"
        "不要使用『剛才問題』『再補充細節』『換個方式』等沒有具體資訊的罐頭。"
        "department_status=ambiguous 時，優先問最能區分候選的一個自然問題；"
        "若有 required_next_question_intent，它只是 Backend/AI 間的 opaque correlation key：輸出的 intent 必須"
        "原樣 echo 並與它完全一致，但 question 仍然只問一個聚焦臨床面向。不得解析 key 名稱，也不得因 key"
        "看似包含多個英文概念，就要求 question 同時涵蓋所有概念。"
        "uncertainty_reasons 與 department_uncertainty_reason 是 Backend 診斷資訊，絕不可引用、改寫或當成患者追問焦點。"
        "next_information_needed 只在內容是患者可直接回答的具體資訊需求時使用；若為空，應依 required intent、"
        "既有對話和 grounded evidence 自然產生問題，不得把 snake_case intent 逐字翻譯成診斷。"
        "不得向患者顯示候選科別名稱。"
        "如果症狀資訊足夠，可建議 status=sufficient，但最終完成與否由 Backend 決定。"
        "不得診斷、建議科別或醫師、修改患者事實或控制 workflow。"
        "只輸出 JSON，欄位為 status (clarification_needed 或 sufficient), question, intent, reason, "
        "answered_intent, answer_source_text, answer_status, answer_confidence, information_needs。"
        "information_needs 是僅含一個患者能回答的具體資訊需求的字串陣列；不能寫安全狀態或工作流程。"
        "intent 用簡短英文 snake_case；沒有 pending 回答時，四個 answer 欄位用 null。\n"
        f"資料：{json.dumps(payload, ensure_ascii=False)}"
    )
    compatibility_answer = (None, None, None, None)
    rejection = None
    initial_rejection = None
    for attempt in range(2):
        proposal = None
        try:
            raw = await complete_runtime_json(
                prompt, purpose="conversation_clarification" if attempt == 0 else "conversation_clarification_repair",
            )
        except Exception as exc:
            rejection = "provider_timeout" if isinstance(exc, TimeoutError) else "provider_failure"
            logger.warning("clarification failed case_id=%s reason=%s error_type=%s", case.case_id, rejection, type(exc).__name__)
            break
        try:
            proposal = json.loads(str(raw).strip())
        except (TypeError, ValueError):
            rejection = "invalid_json"
        else:
            if classify_answer_fields and isinstance(proposal, dict):
                fields = tuple(proposal.get(key) for key in (
                    "answered_intent", "answer_source_text", "answer_status", "answer_confidence",
                ))
                if _grounded_clarification_answer(*fields, state.pending_clarification_intent, user_sources):
                    compatibility_answer = fields
            rejection = _question_rejection(case, proposal, required_intent, safety_planning)
        logger.info(
            "clarification proposal case_id=%s accepted=%s rejection_reason=%s repair_attempted=%s",
            case.case_id, rejection is None, rejection, bool(attempt),
        )
        if rejection is None:
            needs = (proposal.get("information_needs") or []) if proposal["status"] == "clarification_needed" else []
            return ClarificationSuggestion(
                proposal["status"],
                proposal.get("question", "").strip() if proposal["status"] == "clarification_needed" else None,
                proposal.get("intent") if proposal["status"] == "clarification_needed" else None,
                proposal["reason"].strip()[:240], *compatibility_answer,
                information_need=needs[0].strip() if needs else None,
                rejection_reason=initial_rejection,
                repair_attempted=bool(attempt), reply_source="AI_REPAIR" if attempt else "AI_PLANNER",
            )
        if attempt == 0:
            initial_rejection = rejection
            repair_context = dict(payload, original_proposal=proposal if proposal is not None else str(raw),
                                  rejection_reason=rejection)
            prompt = (
                prompt + "\n這是唯一一次修復：依下列 validator rejection_reason 修正 proposal，"
                "只產生目前 required intent 的一個具體問句，不得新增患者事實或控制 state。\n"
                + json.dumps(repair_context, ensure_ascii=False)
            )
    return ClarificationSuggestion(
        "clarification_needed", None, None, "clarification_generation_failed", *compatibility_answer,
        rejection_reason=rejection, repair_attempted=bool(attempt), reply_source="TECHNICAL_FALLBACK",
    )


def _question_rejection(case: TriageCase, proposal: object, required_intent: str | None,
                        safety_planning: bool) -> str | None:
    if not isinstance(proposal, dict):
        return "invalid_schema"
    if set(proposal) & {"stage", "confirmed", "red_flags_checked", "department_result", "is_complete", "doctor_id", "schedule_id"}:
        return "workflow_authority"
    if not isinstance(proposal.get("status"), str) or proposal["status"] not in {"clarification_needed", "sufficient"}:
        return "invalid_status"
    if not isinstance(proposal.get("reason"), str) or not proposal["reason"].strip():
        return "invalid_reason"
    if proposal["status"] == "sufficient":
        return "safety_still_pending" if safety_planning else None
    question, intent = proposal.get("question"), proposal.get("intent")
    if not isinstance(question, str) or not 4 <= len(question.strip()) <= 180:
        return "invalid_question_length"
    if not isinstance(intent, str) or not _INTENT_PATTERN.fullmatch(intent):
        return "invalid_intent"
    if intent in case.conversation_state.clarification_evidence:
        return "intent_already_answered"
    state = case.conversation_state
    if (intent in state.asked_clarification_intents
            and intent not in {state.pending_clarification_intent, state.department_next_question_intent}):
        return "intent_has_no_current_owner"
    if required_intent and intent != required_intent:
        return "required_intent_mismatch"
    if _UNSAFE_QUESTION.search(question) or _INTERNAL_STATE_TEXT.search(question):
        return "unsafe_or_internal_question"
    if _MULTI_QUESTION.search(question) or _MULTI_DIMENSION_QUESTION.search(question):
        return "multiple_information_needs"
    needs = proposal.get("information_needs")
    if needs is not None and (not isinstance(needs, list) or len(needs) != 1
                              or _patient_information_need(needs[0]) is None):
        return "invalid_information_need"
    if question.strip() in (*FALLBACK_QUESTIONS, *NEUTRAL_PENDING_RETRIES):
        return "generic_retry"
    if any(item.role == "assistant" and item.content == question.strip() for item in case.history_records):
        return "duplicate_question"
    if safety_planning and question.strip() == QUESTION_TEXTS[RED_FLAG_QUESTION_KEY]:
        return "repeated_safety_checklist"
    if not safety_planning and _asks_filled_duration(case, intent, question):
        return "already_collected_duration"
    return None


def clear_answered_pending(case: TriageCase) -> bool:
    """Reconcile stale workflow ownership only with an already accepted answer."""
    state = case.conversation_state
    if state.pending_clarification_intent in state.clarification_evidence:
        state.pending_clarification_intent = None
        state.pending_clarification_question = None
        state.next_information_needed = []
        return True
    return False


def capture_pending_answer(
    case: TriageCase,
    suggestion: ClarificationSuggestion | PendingAnswerClassification | PendingAnswerInterpretation | None,
    user_sources: list[str],
) -> bool:
    """Persist only a grounded, confident answer before candidate recomputation."""
    if not suggestion or suggestion.answer_status != "answered" or suggestion.answer_confidence is None:
        return False
    state = case.conversation_state
    if not _grounded_clarification_answer(
        suggestion.answered_intent, suggestion.answer_source_text, suggestion.answer_status,
        suggestion.answer_confidence, state.pending_clarification_intent, user_sources,
    ) or suggestion.answer_confidence < ACCEPT_THRESHOLD:
        return False
    state.clarification_evidence[suggestion.answered_intent] = suggestion.answer_source_text or ""
    state.pending_clarification_intent = None
    state.pending_clarification_question = None
    state.next_information_needed = []
    return True


def capture_safety_screen_answer(
    case: TriageCase,
    suggestion: PendingAnswerInterpretation | None,
    user_sources: list[str],
) -> bool:
    """Complete a negative safety screen from validated AI interpretation only."""
    state = case.conversation_state
    if state.clarification_status != "safety_check" or not user_sources:
        return False
    state.safety_answer_attempts += 1
    state.safety_answer_validation = (
        "missing_or_invalid_interpretation" if suggestion is None else
        "low_confidence" if suggestion.answer_confidence < ACCEPT_THRESHOLD else
        f"{suggestion.answer_status}_{suggestion.answer_assertion}"
    )
    if (
        case.conversation_state.clarification_status != "safety_check"
        or suggestion is None
        or suggestion.answer_status != "answered"
        or suggestion.answer_assertion != "absent"
        or suggestion.answer_confidence < ACCEPT_THRESHOLD
        or not _grounded_clarification_answer(
            suggestion.answered_intent,
            suggestion.answer_source_text,
            suggestion.answer_status,
            suggestion.answer_confidence,
            SAFETY_PENDING_INTENT,
            user_sources,
        )
    ):
        logger.info("safety answer accepted=false reason=%s attempt=%s", state.safety_answer_validation, state.safety_answer_attempts)
        return False

    patient = case.patient_input
    state = case.conversation_state
    patient.red_flags = []
    patient.red_flags_checked = True
    patient.red_flags_status = "negative"
    if RED_FLAG_QUESTION_KEY not in patient.collected_fields:
        patient.collected_fields.append(RED_FLAG_QUESTION_KEY)
    if RED_FLAG_QUESTION_KEY not in state.consumed_fields:
        state.consumed_fields.append(RED_FLAG_QUESTION_KEY)
    state.field_statuses[RED_FLAG_QUESTION_KEY] = "unavailable"
    state.field_confidence[RED_FLAG_QUESTION_KEY] = suggestion.answer_confidence
    state.safety_answer_validation = "accepted_complete_negative"
    # Release safety ownership before department recomputation in this same turn.
    state.clarification_status = "collecting"
    state.pending_clarification_intent = None
    state.pending_clarification_question = None
    state.next_information_needed = []
    state.department_next_question_intent = None
    state.department_next_information_needed = None
    state.uncertainty_reasons = []
    state.last_question_key = None
    case.triage.next_question = None
    logger.info("safety answer accepted=true reason=complete_negative attempt=%s", state.safety_answer_attempts)
    return True


def continue_safety_check(case: TriageCase, suggestion: ClarificationSuggestion | None) -> None:
    """Bound safety workflow retries without interpreting patient language."""
    state = case.conversation_state
    state.pending_clarification_intent = None
    state.pending_clarification_question = None
    state.next_information_needed = []
    if state.safety_answer_attempts >= SAFETY_ANSWER_CAP:
        _unresolved(case)
        state.uncertainty_reasons = ["safety_information_unresolved"]
        state.next_information_needed = []
        state.reply_source = "SAFETY_CONTROLLED_FALLBACK"
        return
    question = suggestion.question if suggestion and suggestion.intent == SAFETY_PENDING_INTENT else None
    if not question or any(m.role == "assistant" and m.content == question for m in case.history_records):
        question = SAFETY_RETRY_REPLY
        state.reply_source = "SAFETY_CONTROLLED_FALLBACK"
    else:
        state.reply_source = suggestion.reply_source
    state.clarification_status = "safety_check"
    state.last_question_key = RED_FLAG_QUESTION_KEY
    state.is_complete = False
    case.triage.need_more_info = True
    case.triage.next_question = question
    case.triage.is_final = False


def advance_conversation(
    case: TriageCase,
    suggestion: ClarificationSuggestion | None,
    *,
    user_sources: list[str],
    current_extractions: list[SemanticExtraction],
) -> None:
    """Apply only validated clarification proposals and Backend-owned state gates."""
    state = case.conversation_state
    state.reply_source = None
    state.question_rejection_reason = suggestion.rejection_reason if suggestion else "planner_unavailable"
    state.question_repair_attempted = suggestion.repair_attempted if suggestion else False
    state.next_information_needed = _patient_information_needs(state.next_information_needed)
    state.department_next_information_needed = _patient_information_need(
        state.department_next_information_needed
    )
    if state.clarification_status == "unresolved":
        _unresolved(case)
        return
    state.turn_count += len(user_sources)
    if state.clarification_status == "safety_check" and not safety_screen_resolved(case):
        continue_safety_check(case, suggestion)
        return

    grounded_answer = bool(suggestion) and _grounded_clarification_answer(
        suggestion.answered_intent, suggestion.answer_source_text,
        suggestion.answer_status, suggestion.answer_confidence,
        state.pending_clarification_intent, user_sources,
    )
    if grounded_answer and suggestion.answer_status == "answered" and suggestion.answer_confidence >= ACCEPT_THRESHOLD:
        capture_pending_answer(case, suggestion, user_sources)
    clear_answered_pending(case)

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
    safety_ready = grounded_symptom and grounded_detail and not low_confidence
    if safety_ready and not safety_screen_resolved(case):
        _enter_safety_check(case)
        return

    sufficient = (
        (state.clarification_status == "safety_check" or (suggestion is not None and suggestion.status == "sufficient"))
        and safety_ready
        and state.pending_clarification_intent is None
    )
    if sufficient:
        if safety_screen_resolved(case) and state.department_status == "resolved":
            state.clarification_status = "sufficient"
            state.uncertainty_reasons = []
            state.next_information_needed = []
            state.last_question_key = None
            state.is_complete = True
            case.triage.need_more_info = False
            case.triage.next_question = None
            case.triage.is_final = True
            case.triage.reasons.append("症狀資訊已通過 Backend 澄清完成條件；掛號偏好不是醫療完成門檻。")
            state.reply_source = "CONFIRMATION"
            return

    if state.turn_count >= HARD_TURN_CAP:
        _unresolved(case)
        return

    same_pending_intent = bool(
        suggestion is not None
        and state.pending_clarification_intent
        and suggestion.intent == state.pending_clarification_intent
    )
    valid_question = (
        suggestion is not None
        and suggestion.status == "clarification_needed"
        and bool(suggestion.question)
        and bool(suggestion.intent)
        and suggestion.intent not in state.clarification_evidence
        and (same_pending_intent or (
            state.pending_clarification_intent is None
            and (suggestion.intent not in state.asked_clarification_intents
                 or suggestion.intent == state.department_next_question_intent)
        ))
        and not any(item.role == "assistant" and item.content == suggestion.question for item in case.history_records)
        and not _asks_filled_duration(case, suggestion.intent, suggestion.question)
    )
    if valid_question:
        question = suggestion.question or ""
        if state.pending_clarification_intent is None:
            state.pending_clarification_intent = suggestion.intent
            if suggestion.intent not in state.asked_clarification_intents:
                state.asked_clarification_intents.append(suggestion.intent or "")
        state.pending_clarification_question = question
        reason = suggestion.reason
        # Information needs belong to this proposal, never to the previous owner.
        state.next_information_needed = [suggestion.information_need] if suggestion.information_need else []
        state.reply_source = suggestion.reply_source
    else:
        question = TECHNICAL_CLARIFICATION_REPLY
        reason = "clarification_generation_failed"
        state.reply_source = "TECHNICAL_FALLBACK"
    if low_confidence:
        reason = "本輪症狀抽取信心不足，仍需釐清"
    elif not grounded_symptom:
        reason = "尚無 grounded 的主要症狀描述"
    elif not grounded_detail:
        reason = "目前仍缺少可區分症狀的細節"
    elif state.department_status != "resolved" and state.department_uncertainty_reason:
        reason = state.department_uncertainty_reason
    state.clarification_status = "clarifying"
    state.uncertainty_reasons = [reason]
    state.last_question_key = None
    state.is_complete = False
    case.triage.need_more_info = True
    case.triage.next_question = question
    case.triage.is_final = False
    case.triage.reasons.append(f"自然對話澄清中：{reason}")


def _enter_safety_check(case: TriageCase) -> None:
    state = case.conversation_state
    if state.clarification_status != "safety_check":
        state.safety_answer_attempts = 0
        state.safety_answer_validation = None
    state.clarification_status = "safety_check"
    state.uncertainty_reasons = ["急迫症狀篩檢尚未完成"]
    state.next_information_needed = []
    state.pending_clarification_intent = None
    state.pending_clarification_question = None
    state.department_next_question_intent = None
    state.department_next_information_needed = None
    state.reply_source = "SAFETY_CONTROLLED_FALLBACK"
    state.is_complete = False
    mark_questions_asked(case, [RED_FLAG_QUESTION_KEY])
    case.triage.need_more_info = True
    case.triage.next_question = QUESTION_TEXTS[RED_FLAG_QUESTION_KEY]
    case.triage.is_final = False
    case.triage.reasons.append("症狀描述已足夠，仍須完成既有急迫症狀篩檢。")


def _asks_filled_duration(case: TriageCase, intent: str | None, question: str | None) -> bool:
    return bool(
        case.patient_input.duration
        and ((intent and "duration" in intent) or (question and _DURATION_QUESTION.search(question)))
    )


def _patient_information_needs(values: list[str]) -> list[str]:
    needs = [_patient_information_need(value) for value in values]
    return [need for need in needs if need]


def _patient_information_need(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    focus = value.strip().strip("。？！? ")
    if (
        not focus
        or len(focus) > 60
        or _UNSAFE_QUESTION.search(focus)
        or _INTERNAL_STATE_TEXT.search(focus)
    ):
        return None
    return focus


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
        and isinstance(status, str)
        and status in {"answered", "partial", "unclear"}
        and isinstance(confidence, (int, float))
        and not isinstance(confidence, bool)
        and math.isfinite(confidence)
        and 0 <= confidence <= 1
    )


def _unresolved(case: TriageCase) -> None:
    state = case.conversation_state
    state.clarification_status = "unresolved"
    state.reply_source = "UNRESOLVED"
    state.uncertainty_reasons = ["多輪後仍無法可靠釐清症狀"]
    state.next_information_needed = []
    state.pending_clarification_intent = None
    state.pending_clarification_question = None
    state.is_complete = False
    state.last_question_key = None
    case.triage.need_more_info = True
    case.triage.next_question = None
    case.triage.is_final = False
    case.triage.reasons.append("已達自然澄清輪數上限；未自動補值或選科。")
