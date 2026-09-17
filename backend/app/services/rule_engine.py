from __future__ import annotations

import re
import logging
from datetime import datetime, timedelta
from typing import Any, Optional
from zoneinfo import ZoneInfo

from app.schemas import Message, SemanticExtraction, TriageCase, UrgencyResult
from app.services.clarification_engine import clarification_prompt
from app.services.confidence_scoring import ACCEPT_THRESHOLD, MAX_QUESTION_ATTEMPTS, accepted
from app.services.field_acceptance import (
    has_symptom_semantics,
    looks_like_department_request,
    normalize_body_part,
    normalized_value_valid,
)
from app.services.negation_utils import is_negated_keyword, strip_negated_red_flags
from app.services.question_specs import QUESTION_SPECS, question_spec_for_field, select_question_variant
from app.services.semantic_normalizer import NormalizationResult, normalize_message

logger = logging.getLogger(__name__)


CHINESE_DURATION_NUMBERS = {
    "一": 1,
    "二": 2,
    "兩": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}


RED_FLAG_PATTERNS = {
    "突發胸痛": ["突發胸痛", "胸痛", "胸悶冒冷汗"],
    "嚴重呼吸困難": ["呼吸困難", "喘不過氣", "無法呼吸"],
    "中風徵象": ["嘴歪", "半邊無力", "說話不清", "中風"],
    "大量出血": ["大量出血", "血流不止"],
    "嚴重外傷": ["嚴重外傷", "車禍", "高處墜落"],
    "意識異常": ["昏迷", "意識不清", "叫不醒"],
    "劇烈頭痛合併神經症狀": ["劇烈頭痛", "視力模糊", "抽搐"],
}

RED_FLAG_QUESTION_KEY = "red_flags"
QUESTION_TEXTS = {spec.state_field: spec.canonical_text for spec in QUESTION_SPECS}
CHECKLIST_FIELD_ORDER = tuple(spec.state_field for spec in QUESTION_SPECS)

RED_FLAG_SCREEN_TERMS = [
    "胸痛",
    "呼吸困難",
    "喘不過氣",
    "意識不清",
    "大量出血",
    "半邊無力",
    "劇烈頭痛",
    "中風",
    "昏迷",
]

NEGATIVE_TERMS = ["沒有", "無", "否", "否認", "都沒有", "都沒", "沒這些", "不會", "沒有以上", "無上述"]
PREFERRED_DAYS_KEY = "preferred_days"
PREFERRED_DATES_KEY = "preferred_dates"
PREFERRED_SESSIONS_KEY = "preferred_sessions"
ALL_WEEKDAYS = ["週一", "週二", "週三", "週四", "週五", "週六", "週日"]
WEEKDAY_DAYS = ["週一", "週二", "週三", "週四", "週五"]
WEEKEND_DAYS = ["週六", "週日"]
ALL_SESSIONS = ["上午", "下午", "夜間"]
ANY_SESSION_TERMS = [
    "都可以", "我都可以", "隨便", "隨便安排", "都行", "皆可", "任何時段都可以",
    "上午下午晚上都可以", "上午下午夜間都可以", "時間沒差", "什麼時段都行",
]
ANY_DAY_TERMS = [
    "哪天都可以", "每天都可以", "日期都可以", "日期沒差", "日期都沒差",
    "任何一天都行", "任何一天都可以",
]
RED_FLAG_UNCERTAINTY_WARNING = (
    "目前無法完全確認是否有急迫症狀；若有突發胸痛、嚴重呼吸困難、意識不清、"
    "大量出血、半邊無力或劇烈頭痛，請優先尋求緊急醫療協助。"
)


def apply_user_message(case: TriageCase, message: str) -> TriageCase:
    text = message.strip()
    if not text:
        return case

    revision_mode = case.conversation_state.revision_mode
    before_patient = case.patient_input.model_dump()
    before_availability = case.availability.model_dump()
    case.history_records.append(Message(role="user", content=text))
    patient = case.patient_input
    previous_red_flag_status = patient.red_flags_status
    symptom_value = text if has_symptom_semantics(text) else None
    if revision_mode and _looks_like_symptom_revision(text):
        _reset_symptom_dependent_fields(case)
    semantic_result = normalize_message(text, case.conversation_state.last_question_key)
    _apply_semantic_result(case, semantic_result)

    if (
        case.conversation_state.last_question_key == RED_FLAG_QUESTION_KEY
        and previous_red_flag_status == "ambiguous"
        and _is_explicit_red_flag_uncertainty(text)
    ):
        _complete_uncertain_red_flag_screen(case, text)

    if revision_mode and _looks_like_symptom_revision(text) and symptom_value:
        patient.symptom = symptom_value
        case.department_result = None
    elif not patient.symptom and symptom_value:
        patient.symptom = symptom_value

    body_part = _extract_body_part(text)
    if body_part and (
        revision_mode
        or not patient.body_part
        or _is_more_specific_body_part(body_part, patient.body_part)
    ):
        patient.body_part = body_part
        if revision_mode:
            case.department_result = None

    duration = _extract_duration(text)
    if duration:
        patient.duration = duration
        _consume_field(case, "duration", "available", 0.9)

    severity = _extract_severity(text)
    if severity:
        patient.severity = severity

    onset = _extract_onset(text)
    if onset:
        patient.onset = onset

    for symptom in _extract_accompanying_symptoms(text):
        if symptom not in patient.accompanying_symptoms:
            patient.accompanying_symptoms.append(symptom)

    if semantic_result.urgency and semantic_result.urgency.semantic_status == "unavailable":
        positive_red_flags = []
    elif semantic_result.urgency and semantic_result.urgency.matched_red_flags:
        positive_red_flags = semantic_result.urgency.matched_red_flags
    else:
        positive_red_flags = detect_red_flags(text)
    red_flag_answered = _is_red_flag_screen_answer(case, text, positive_red_flags)
    if red_flag_answered:
        patient.red_flags_checked = True
        _consume_field(case, RED_FLAG_QUESTION_KEY, "available" if positive_red_flags else "unavailable", 0.9)
        if not positive_red_flags and _is_negative_red_flag_answer(case, text):
            patient.red_flags = []

    for red_flag in positive_red_flags:
        if red_flag not in patient.red_flags:
            patient.red_flags.append(red_flag)

    if case.conversation_state.last_question_key == RED_FLAG_QUESTION_KEY:
        classification = (
            semantic_result.urgency.answer_classification if semantic_result.urgency else None
        )
        logger.info(
            "[RED_FLAGS] classification=%s attempt=%s resolved_status=%s",
            classification,
            case.conversation_state.question_attempts.get(RED_FLAG_QUESTION_KEY, 0),
            patient.red_flags_status,
        )

    availability = case.availability

    _refresh_collected_fields(case)
    if revision_mode:
        case.confirmed = False
        case.recommendation_generated = False
        case.script_generated = False
        case.selected_recommendation_id = None
        case.conversation_state.confirmed = False
        case.conversation_state.awaiting_confirmation = False
        case.conversation_state.revision_mode = False
    logger.info(
        "apply_user_message case_id=%s history_len=%s before_patient=%s after_patient=%s before_availability=%s after_availability=%s red_flags=%s red_flags_checked=%s last_question_key=%s consumed_fields=%s question_attempts=%s field_statuses=%s availability=%s",
        case.case_id,
        len(case.history_records),
        before_patient,
        patient.model_dump(),
        before_availability,
        availability.model_dump(),
        patient.red_flags,
        patient.red_flags_checked,
        case.conversation_state.last_question_key,
        case.conversation_state.consumed_fields,
        case.conversation_state.question_attempts,
        case.conversation_state.field_statuses,
        availability.model_dump(),
    )
    return case


def evaluate_urgency(case: TriageCase, *, mark_next_question: bool = True) -> UrgencyResult:
    patient = case.patient_input
    _refresh_collected_fields(case)
    combined = " ".join(
        [
            patient.symptom or "",
            patient.body_part or "",
            patient.duration or "",
            patient.severity or "",
            patient.onset or "",
            " ".join(patient.accompanying_symptoms),
            " ".join(patient.red_flags),
        ]
    )

    if patient.urgency_normalized.semantic_status == "unavailable":
        detected_from_combined = []
    elif patient.urgency_normalized.matched_red_flags:
        detected_from_combined = patient.urgency_normalized.matched_red_flags
    else:
        detected_from_combined = detect_red_flags(combined)
    if patient.red_flags_checked and not patient.red_flags:
        red_flags = []
    else:
        red_flags = list(dict.fromkeys([*patient.red_flags, *detected_from_combined]))
    patient.red_flags = red_flags
    if red_flags:
        logger.info(
            "evaluate_urgency case_id=%s high_urgency red_flags=%s red_flags_checked=%s history_len=%s",
            case.case_id,
            red_flags,
            patient.red_flags_checked,
            len(case.history_records),
        )
        return UrgencyResult(
            urgency_score=90,
            urgency_level="high",
            warning_required=True,
            warning_message="症狀較急迫，建議盡快就醫，必要時請假處理。",
            need_more_info=False,
            next_question=None,
            reasons=[
                f"偵測到急迫症狀：{', '.join(red_flags)}",
                "急迫症狀不需等待完整問診即可進入確認與就醫建議。",
            ],
            is_final=True,
        )

    score = 10
    reasons = ["基礎急迫度分數為 10。"]

    severity_score = _score_severity(patient.severity, combined)
    if patient.severity_normalized.sleep_impact:
        severity_score = max(severity_score, 20)
    elif patient.severity_normalized.functional_impact:
        severity_score = max(severity_score, 15)
    score += severity_score
    if severity_score:
        reasons.append(f"症狀程度「{patient.severity or '由描述判斷'}」使急迫度增加 {severity_score} 分。")

    duration_score = _score_duration(patient.duration, combined)
    score += duration_score
    if duration_score:
        reasons.append(f"症狀已持續 {patient.duration or '一段時間'}，急迫度增加 {duration_score} 分。")

    function_score = _score_function_limit(combined)
    score += function_score
    if function_score:
        reasons.append(f"描述包含活動或生活功能受影響，急迫度增加 {function_score} 分。")

    onset_score = _score_onset(patient.onset, combined)
    score += onset_score
    if onset_score:
        reasons.append(f"發作型態「{patient.onset or '由描述判斷'}」使急迫度增加 {onset_score} 分。")

    inflammation_score = _score_inflammation(combined)
    score += inflammation_score
    if inflammation_score:
        reasons.append(f"描述包含發炎或全身不適線索，急迫度增加 {inflammation_score} 分。")

    treatment_score = _score_treatment(combined)
    score += treatment_score
    if treatment_score:
        reasons.append(f"描述包含已處理但未改善，急迫度增加 {treatment_score} 分。")

    risk_score = _score_risk(combined)
    score += risk_score
    if risk_score:
        reasons.append(f"描述包含高風險背景，急迫度增加 {risk_score} 分。")

    score = max(0, min(score, 100))

    if mark_next_question:
        next_question = next_question_for(case)
    else:
        missing = missing_checklist_fields(case)
        next_question = question_text_for_field(case, missing[0]) if missing else None
    need_more_info = next_question is not None
    level = urgency_level(score)
    safety_uncertain = patient.red_flags_status == "uncertain"
    warning_required = level == "high" or safety_uncertain
    if need_more_info:
        reasons.append(f"資訊尚未完整，下一題：{next_question}")
    else:
        reasons.append("問診必要資訊已完整，可進入使用者確認階段。")
    if safety_uncertain:
        reasons.append("急迫症狀篩檢已詢問，但使用者仍無法確認；不得視為已確認陰性。")

    logger.info(
        "evaluate_urgency case_id=%s history_len=%s need_more_info=%s next_question=%s red_flags=%s red_flags_checked=%s availability=%s collected_fields=%s asked_fields=%s consumed_fields=%s last_question_key=%s question_attempts=%s field_statuses=%s",
        case.case_id,
        len(case.history_records),
        need_more_info,
        next_question,
        patient.red_flags,
        patient.red_flags_checked,
        case.availability.model_dump(),
        patient.collected_fields,
        case.conversation_state.asked_fields,
        case.conversation_state.consumed_fields,
        case.conversation_state.last_question_key,
        case.conversation_state.question_attempts,
        case.conversation_state.field_statuses,
    )

    return UrgencyResult(
        urgency_score=score,
        urgency_level=level,
        warning_required=warning_required,
        warning_message=(
            RED_FLAG_UNCERTAINTY_WARNING
            if safety_uncertain
            else "症狀較急迫，建議盡快就醫，必要時請假處理。" if warning_required else None
        ),
        need_more_info=need_more_info,
        next_question=next_question,
        reasons=reasons,
        is_final=not need_more_info,
    )


def next_question_for(case: TriageCase) -> Optional[str]:
    question_key = _next_question_key(case)
    if question_key is None:
        case.conversation_state.last_question_key = None
        return None

    question_text = _question_text_for(case, question_key)
    _mark_question_asked(case, question_key)
    return question_text


def _next_question_key(case: TriageCase) -> Optional[str]:
    missing = missing_checklist_fields(case)
    return missing[0] if missing else None


def missing_checklist_fields(case: TriageCase) -> list[str]:
    """Return deterministic checklist gaps without letting AI control flow."""
    _sync_consumed_fields(case)
    missing: list[str] = []
    for field in CHECKLIST_FIELD_ORDER:
        if _field_satisfied(case, field):
            continue
        if field != RED_FLAG_QUESTION_KEY and _question_attempts(case, field) >= MAX_QUESTION_ATTEMPTS:
            _fallback_field(case, field)
            continue
        missing.append(field)
    return missing


def question_text_for_field(case: TriageCase, field: str) -> str:
    if field not in CHECKLIST_FIELD_ORDER:
        raise ValueError(f"Unsupported checklist field: {field}")
    return _question_text_for(case, field)


def mark_questions_asked(case: TriageCase, fields: list[str]) -> None:
    for field in fields:
        if field not in CHECKLIST_FIELD_ORDER:
            raise ValueError(f"Unsupported checklist field: {field}")
        _mark_question_asked(case, field)


def _field_satisfied(case: TriageCase, field: str) -> bool:
    patient = case.patient_input
    availability = case.availability
    state = case.conversation_state
    if field in state.consumed_fields:
        return True

    if field == "symptom":
        return bool(patient.symptom)
    if field == RED_FLAG_QUESTION_KEY:
        if patient.red_flags and not patient.red_flags_checked:
            patient.red_flags_checked = True
        return patient.red_flags_checked
    if field == "body_part":
        return bool(patient.body_part)
    if field == "duration":
        return bool(patient.duration)
    if field == "severity":
        return bool(patient.severity)
    if field == PREFERRED_DAYS_KEY:
        return bool(availability.preferred_days) or state.field_statuses.get(field) == "unavailable"
    if field == PREFERRED_SESSIONS_KEY:
        return bool(availability.preferred_sessions) or state.field_statuses.get(field) == "unavailable"
    return True


def _fallback_field(case: TriageCase, field: str) -> None:
    state = case.conversation_state
    state.field_statuses[field] = state.field_statuses.get(field, "unknown")
    state.field_confidence[field] = state.field_confidence.get(field, 0.0)
    state.clarification_reasons[field] = "max question attempts reached; explicitly skipped as unknown"
    _consume_field(case, field, state.field_statuses[field], state.field_confidence[field])
    if field == RED_FLAG_QUESTION_KEY:
        case.patient_input.red_flags_checked = True
        case.patient_input.red_flags = []


def _question_text_for(case: TriageCase, field: str) -> str:
    confidence = case.conversation_state.field_confidence.get(field, 1.0)
    status = case.conversation_state.field_statuses.get(field)
    if _question_attempts(case, field) > 0 and (confidence < ACCEPT_THRESHOLD or status in {"unknown", "ambiguous", "unavailable"}):
        return clarification_prompt(field, status)
    spec = question_spec_for_field(field)
    return select_question_variant(spec.question_id)


def _question_attempts(case: TriageCase, field: str) -> int:
    return case.conversation_state.question_attempts.get(field, 0)


def urgency_level(score: int) -> str:
    if score >= 60:
        return "high"
    if score >= 25:
        return "medium"
    return "low"


def _apply_semantic_result(case: TriageCase, result: NormalizationResult) -> None:
    patient = case.patient_input
    if result.severity is not None:
        patient.severity_normalized = result.severity
        if result.severity.severity_level and accepted(result.severity.confidence, result.severity.semantic_status):
            patient.severity = result.severity.severity_level

    if result.urgency is not None:
        patient.urgency_normalized = result.urgency
        if result.urgency.semantic_status == "unavailable" and accepted(result.urgency.confidence, result.urgency.semantic_status):
            patient.red_flags_checked = True
            patient.red_flags = []
            patient.red_flags_status = "negative"
            _consume_field(case, RED_FLAG_QUESTION_KEY, "unavailable", result.urgency.confidence)
        elif result.urgency.matched_red_flags and accepted(result.urgency.confidence, result.urgency.semantic_status):
            patient.red_flags_checked = True
            patient.red_flags_status = "positive_specific"
            _consume_field(case, RED_FLAG_QUESTION_KEY, "available", result.urgency.confidence)
            for red_flag in result.urgency.matched_red_flags:
                if red_flag not in patient.red_flags:
                    patient.red_flags.append(red_flag)
        elif result.urgency.semantic_status == "ambiguous":
            patient.red_flags_status = "ambiguous"

    for extraction in result.extractions:
        _record_semantic_extraction(case, extraction)
        _apply_semantic_extraction(case, extraction)

    if result.extractions:
        case.semantic_extractions.extend(result.extractions)
        case.semantic_extractions = case.semantic_extractions[-50:]


def apply_semantic_extractions(
    case: TriageCase,
    extractions: list[SemanticExtraction],
    *,
    allow_red_flag_completion: bool = True,
) -> None:
    """Apply schema-validated extraction while preserving deterministic gates."""
    accepted_extractions: list[SemanticExtraction] = []
    for extraction in extractions:
        if extraction.field not in {*CHECKLIST_FIELD_ORDER, PREFERRED_DATES_KEY}:
            continue
        if extraction.field == RED_FLAG_QUESTION_KEY and not allow_red_flag_completion:
            continue
        if (
            extraction.field not in {RED_FLAG_QUESTION_KEY, PREFERRED_DATES_KEY}
            and extraction.semantic_status != "uncertain"
            and not normalized_value_valid(
                extraction.field,
                extraction.normalized_value,
                extraction.semantic_status,
            )
        ):
            logger.info(
                "semantic extraction rejected field=%s reason=strict_field_validation",
                extraction.field,
            )
            continue
        _record_semantic_extraction(case, extraction)
        _apply_semantic_extraction(case, extraction)
        accepted_extractions.append(extraction)
    if accepted_extractions:
        case.semantic_extractions.extend(accepted_extractions)
        case.semantic_extractions = case.semantic_extractions[-50:]
    _refresh_collected_fields(case)


def _record_semantic_extraction(case: TriageCase, extraction: SemanticExtraction) -> None:
    state = case.conversation_state
    state.field_statuses[extraction.field] = extraction.semantic_status
    state.field_confidence[extraction.field] = extraction.confidence
    if extraction.follow_up_reason:
        state.clarification_reasons[extraction.field] = extraction.follow_up_reason


def _apply_semantic_extraction(case: TriageCase, extraction: SemanticExtraction) -> None:
    availability = case.availability
    if extraction.field in {"preferred_dates", "preferred_days", "preferred_sessions", "can_take_leave"}:
        availability.semantic_status[extraction.field] = extraction.semantic_status
        availability.confidence[extraction.field] = extraction.confidence

    if extraction.field == "red_flags" and extraction.semantic_status == "ambiguous":
        case.patient_input.red_flags_status = "ambiguous"
        return

    if extraction.semantic_status != "uncertain" and not accepted(
        extraction.confidence,
        extraction.semantic_status,
    ):
        return

    if extraction.field in {"symptom", "body_part", "duration"}:
        text = str(extraction.normalized_value or "").strip()
        current = getattr(case.patient_input, extraction.field)
        more_specific_batch_body_part = (
            extraction.field == "body_part"
            and extraction.extractor == "deterministic_batch"
            and _is_more_specific_body_part(text, current)
        )
        if text and (
            case.conversation_state.revision_mode
            or not current
            or more_specific_batch_body_part
        ):
            setattr(case.patient_input, extraction.field, text)
            _consume_field(case, extraction.field, extraction.semantic_status, extraction.confidence)
    elif extraction.field == "preferred_dates":
        values = [
            value
            for value in _as_string_list(extraction.normalized_value)
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value)
        ]
        if case.conversation_state.revision_mode:
            availability.preferred_dates = values
        elif not availability.preferred_dates:
            _merge_list(availability.preferred_dates, values)
    elif extraction.field == "preferred_days":
        values = _as_string_list(extraction.normalized_value)
        if case.conversation_state.revision_mode:
            availability.preferred_days = values
        elif not availability.preferred_days:
            _merge_list(availability.preferred_days, values)
        _consume_field(case, extraction.field, extraction.semantic_status, extraction.confidence)
    elif extraction.field == "preferred_sessions":
        values = _as_string_list(extraction.normalized_value)
        if case.conversation_state.revision_mode:
            availability.preferred_sessions = values
        elif not availability.preferred_sessions:
            _merge_list(availability.preferred_sessions, values)
        _consume_field(case, extraction.field, extraction.semantic_status, extraction.confidence)
    elif extraction.field == "can_take_leave" and isinstance(extraction.normalized_value, bool):
        availability.can_take_leave = extraction.normalized_value
        _consume_field(case, extraction.field, extraction.semantic_status, extraction.confidence)
    elif extraction.field == "severity":
        if isinstance(extraction.normalized_value, dict):
            level = extraction.normalized_value.get("severity_level")
        else:
            level = extraction.normalized_value
        if level and (case.conversation_state.revision_mode or not case.patient_input.severity):
            case.patient_input.severity = str(level)
            _consume_field(case, extraction.field, extraction.semantic_status, extraction.confidence)
    elif extraction.field == "red_flags":
        if extraction.semantic_status == "uncertain":
            case.patient_input.red_flags_checked = True
            case.patient_input.red_flags = []
            case.patient_input.red_flags_status = "uncertain"
            case.patient_input.urgency_normalized.semantic_status = "uncertain"
            case.patient_input.urgency_normalized.answer_classification = "uncertain_after_clarification"
            _consume_field(case, RED_FLAG_QUESTION_KEY, "uncertain", extraction.confidence)
        elif extraction.semantic_status == "unavailable":
            case.patient_input.red_flags_checked = True
            case.patient_input.red_flags = []
            case.patient_input.red_flags_status = "negative"
            _consume_field(case, RED_FLAG_QUESTION_KEY, "unavailable", extraction.confidence)
        else:
            flags = _as_string_list(extraction.normalized_value)
            if flags:
                case.patient_input.red_flags_checked = True
                case.patient_input.red_flags_status = "positive_specific"
                _merge_list(case.patient_input.red_flags, flags)
                _consume_field(case, RED_FLAG_QUESTION_KEY, extraction.semantic_status, extraction.confidence)


def _as_string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item).strip()]


def _merge_list(target: list[str], values: list[str]) -> None:
    for value in values:
        if value not in target:
            target.append(value)


def _looks_like_symptom_revision(text: str) -> bool:
    if _looks_like_time_preference_revision(text):
        return False
    if looks_like_department_request(text) and not has_symptom_semantics(text):
        return False
    if any(keyword in text for keyword in ["痛", "疼", "不舒服", "麻", "腫", "酸", "癢", "咳", "喘", "暈", "發燒"]):
        return True
    if any(keyword in text for keyword in ["膝", "腰", "背", "肩", "手", "腳", "頭", "胸", "腹", "胃", "喉", "皮膚"]):
        return True
    if any(keyword in text for keyword in ["症狀", "部位", "其實是", "不是"]):
        return True
    return False


def _looks_like_time_preference_revision(text: str) -> bool:
    return any(keyword in text for keyword in ["時段", "時間", "上午", "下午", "夜間", "晚上", "早上", "週", "星期", "禮拜"])


def _reset_symptom_dependent_fields(case: TriageCase) -> None:
    patient = case.patient_input
    patient.duration = None
    patient.severity = None
    patient.onset = None
    patient.accompanying_symptoms = []
    patient.department_context = None
    patient.red_flags = []
    patient.red_flags_checked = False
    patient.red_flags_status = "not_checked"
    patient.severity_normalized = type(patient.severity_normalized)()
    patient.urgency_normalized = type(patient.urgency_normalized)()
    for field in [RED_FLAG_QUESTION_KEY, "duration", "severity"]:
        if field in case.conversation_state.consumed_fields:
            case.conversation_state.consumed_fields.remove(field)
        if field in case.conversation_state.asked_fields:
            case.conversation_state.asked_fields.remove(field)
        case.conversation_state.question_attempts.pop(field, None)
        case.conversation_state.field_statuses.pop(field, None)
        case.conversation_state.field_confidence.pop(field, None)


def _mark_question_asked(case: TriageCase, question_key: str) -> None:
    state = case.conversation_state
    state.last_question_key = question_key
    state.question_attempts[question_key] = state.question_attempts.get(question_key, 0) + 1
    if question_key not in state.asked_fields:
        state.asked_fields.append(question_key)


def _consume_field(
    case: TriageCase,
    field: str,
    status: str | None = None,
    confidence: float | None = None,
) -> None:
    state = case.conversation_state
    if field not in state.consumed_fields:
        state.consumed_fields.append(field)
    if status:
        state.field_statuses[field] = status
    if confidence is not None:
        state.field_confidence[field] = confidence


def _sync_consumed_fields(case: TriageCase) -> None:
    patient = case.patient_input
    availability = case.availability
    if patient.symptom:
        _consume_field(case, "symptom", case.conversation_state.field_statuses.get("symptom"))
    if patient.body_part:
        _consume_field(case, "body_part", case.conversation_state.field_statuses.get("body_part"))
    if patient.duration:
        _consume_field(case, "duration", case.conversation_state.field_statuses.get("duration"))
    if patient.severity:
        _consume_field(case, "severity", case.conversation_state.field_statuses.get("severity"))
    if patient.red_flags_checked:
        red_flag_status = {
            "uncertain": "uncertain",
            "positive_specific": "available",
            "negative": "unavailable",
        }.get(patient.red_flags_status, "available" if patient.red_flags else "unavailable")
        _consume_field(
            case,
            RED_FLAG_QUESTION_KEY,
            red_flag_status,
            case.conversation_state.field_confidence.get(RED_FLAG_QUESTION_KEY),
        )
    if availability.preferred_days or case.conversation_state.field_statuses.get(PREFERRED_DAYS_KEY) == "unavailable":
        _consume_field(
            case,
            PREFERRED_DAYS_KEY,
            availability.semantic_status.get(PREFERRED_DAYS_KEY) or case.conversation_state.field_statuses.get(PREFERRED_DAYS_KEY),
            availability.confidence.get(PREFERRED_DAYS_KEY) or case.conversation_state.field_confidence.get(PREFERRED_DAYS_KEY),
        )
    if availability.preferred_sessions or case.conversation_state.field_statuses.get(PREFERRED_SESSIONS_KEY) == "unavailable":
        _consume_field(
            case,
            PREFERRED_SESSIONS_KEY,
            availability.semantic_status.get(PREFERRED_SESSIONS_KEY) or case.conversation_state.field_statuses.get(PREFERRED_SESSIONS_KEY),
            availability.confidence.get(PREFERRED_SESSIONS_KEY) or case.conversation_state.field_confidence.get(PREFERRED_SESSIONS_KEY),
        )


def _is_red_flag_screen_answer(
    case: TriageCase,
    text: str,
    positive_red_flags: list[str],
) -> bool:
    if positive_red_flags:
        return True
    if case.conversation_state.last_question_key == RED_FLAG_QUESTION_KEY and _has_negation(text):
        return True
    return _mentions_red_flag_screen(text) and _has_negation(text)


def _is_negative_red_flag_answer(case: TriageCase, text: str) -> bool:
    if not _has_negation(text):
        return False
    if case.conversation_state.last_question_key == RED_FLAG_QUESTION_KEY:
        return True
    return _mentions_red_flag_screen(text)


def _mentions_red_flag_screen(text: str) -> bool:
    return any(term in text for term in RED_FLAG_SCREEN_TERMS)


def _has_negation(text: str) -> bool:
    return any(term in text for term in NEGATIVE_TERMS)


def detect_red_flags(text: str) -> list[str]:
    red_flags = []
    for label, keywords in RED_FLAG_PATTERNS.items():
        if any(keyword in text and not is_negated_keyword(text, keyword) for keyword in keywords):
            red_flags.append(label)
    return red_flags


def _extract_body_part(text: str) -> Optional[str]:
    text = strip_negated_red_flags(text)
    if looks_like_department_request(text) and not has_symptom_semantics(text):
        return None
    return normalize_body_part(text)


def _is_more_specific_body_part(candidate: str, current: str | None) -> bool:
    """Allow a precise child location to refine, but never degrade, a broad one."""
    refinement_parents = {
        "大腿": {"腿"},
        "小腿": {"腿"},
        "左腿": {"腿"},
        "右腿": {"腿"},
        "左大腿": {"腿", "左腿", "大腿"},
        "右大腿": {"腿", "右腿", "大腿"},
        "左小腿": {"腿", "左腿", "小腿"},
        "右小腿": {"腿", "右腿", "小腿"},
        "腳踝": {"腳"},
    }
    normalized_candidate = str(candidate or "").strip()
    normalized_current = str(current or "").strip()
    return bool(
        normalized_candidate
        and normalized_current
        and normalized_current in refinement_parents.get(normalized_candidate, set())
    )


def _extract_duration(text: str) -> Optional[str]:
    if "一年半" in text:
        return "18個月"
    if "半年" in text:
        return "6個月"
    match = re.search(r"(\d+\s*(?:天|週|周|個月|年))", text)
    if match:
        return match.group(1).replace(" ", "")
    match = re.search(r"(\d+)\s*(?:個)?(?:禮拜|星期)", text)
    if match:
        return f"{int(match.group(1))}週"
    if "好幾天" in text:
        return "好幾天"
    if "幾天" in text:
        return "幾天"
    if "半天" in text:
        return "半天"
    if re.search(r"好幾(?:個)?(?:禮拜|星期)", text):
        return "好幾週"
    if re.search(r"幾(?:個)?(?:禮拜|星期)", text):
        return "幾週"
    match = re.search(r"([一二兩三四五六七八九十]+)\s*(?:個)?(?:禮拜|星期)", text)
    if match:
        amount = _parse_chinese_duration_number(match.group(1))
        if amount is not None:
            return f"{amount}週"
    match = re.search(r"([一二兩三四五六七八九十]+)\s*(天|週|周|個月|年)", text)
    if match:
        amount = _parse_chinese_duration_number(match.group(1))
        if amount is not None:
            unit = match.group(2)
            if unit == "周":
                unit = "週"
            return f"{amount}{unit}"
    if re.search(r"(?:從)?昨天(?:就)?(?:開始|起)", text):
        return "1天"
    if re.search(r"(?:從)?今天(?:就)?(?:開始|起)", text):
        return "1天內"
    onset_match = re.search(r"(?:從)?(早上|上午|中午|下午|晚上|半夜)(?:就)?(?:開始|起)", text)
    if onset_match:
        return f"從{onset_match.group(1)}開始"
    return None


def _parse_chinese_duration_number(value: str) -> Optional[int]:
    if value in CHINESE_DURATION_NUMBERS:
        return CHINESE_DURATION_NUMBERS[value]
    if value == "十":
        return 10
    if "十" in value:
        tens_text, ones_text = value.split("十", 1)
        tens = CHINESE_DURATION_NUMBERS.get(tens_text, 1 if tens_text == "" else None)
        ones = CHINESE_DURATION_NUMBERS.get(ones_text, 0 if ones_text == "" else None)
        if tens is not None and ones is not None:
            return tens * 10 + ones
    return None


def _extract_severity(text: str) -> Optional[str]:
    if any(
        word in text
        for word in [
            "沒有影響",
            "正常生活",
            "正常作息",
            "不影響作息",
            "不影響日常生活",
            "不影響睡眠",
            "還能正常上班",
            "還能正常走路",
            "沒什麼影響",
            "沒有很嚴重",
        ]
    ):
        return "輕微"
    if any(word in text for word in ["很痛", "劇痛", "嚴重", "影響睡覺", "影響睡眠", "睡不著", "痛醒", "無法工作", "無法走路"]):
        return "很痛 / 影響明顯"
    if any(word in text for word in ["明顯", "很不舒服", "走路困難", "爬樓梯很吃力"]):
        return "明顯"
    if any(word in text for word in ["中等", "中度", "還可以忍"]):
        return "中度"
    if any(word in text for word in ["輕微", "有點痛", "一點痛", "不太影響", "還好但"]):
        return "輕微"
    return None


def _extract_onset(text: str) -> Optional[str]:
    if any(word in text for word in ["突然", "突發", "一下子"]):
        return "突發"
    if any(word in text for word in ["慢慢", "漸進", "越來越"]):
        return "漸進"
    return None


def _extract_accompanying_symptoms(text: str) -> list[str]:
    symptoms = []
    symptom_text = strip_negated_red_flags(text)
    candidates = ["爬樓梯吃力", "走路困難", "發燒", "紅腫熱痛", "頭暈", "噁心", "麻", "無力"]
    for candidate in candidates:
        if candidate in symptom_text and not is_negated_keyword(symptom_text, candidate):
            symptoms.append(candidate)
    return symptoms


def _normalize_weekday_text(text: str) -> str:
    normalized = text
    replacements = {
        "星期一": "週一",
        "星期二": "週二",
        "星期三": "週三",
        "星期四": "週四",
        "星期五": "週五",
        "星期六": "週六",
        "星期日": "週日",
        "星期天": "週日",
        "禮拜一": "週一",
        "禮拜二": "週二",
        "禮拜三": "週三",
        "禮拜四": "週四",
        "禮拜五": "週五",
        "禮拜六": "週六",
        "禮拜日": "週日",
        "禮拜天": "週日",
        "周一": "週一",
        "周二": "週二",
        "周三": "週三",
        "周四": "週四",
        "周五": "週五",
        "周六": "週六",
        "周日": "週日",
        "周天": "週日",
    }
    for source, target in replacements.items():
        normalized = normalized.replace(source, target)
    if "明天" in text:
        tomorrow = _taipei_today() + timedelta(days=1)
        normalized = f"{normalized} {ALL_WEEKDAYS[tomorrow.weekday()]}"
    if "大後天" in text:
        three_days_later = _taipei_today() + timedelta(days=3)
        normalized = f"{normalized} {ALL_WEEKDAYS[three_days_later.weekday()]}"
    if "後天" in text.replace("大後天", ""):
        after_tomorrow = _taipei_today() + timedelta(days=2)
        normalized = f"{normalized} {ALL_WEEKDAYS[after_tomorrow.weekday()]}"
    return normalized


def _is_any_days_answer(text: str, last_question_key: Optional[str]) -> bool:
    if any(term in text for term in ["每天", "整週", "全週", *ANY_DAY_TERMS]):
        return True
    return (
        last_question_key == PREFERRED_DAYS_KEY
        and any(term in text for term in ["都可以", "都有空", "都行", "皆可"])
        and not any(term in text for term in ["前半", "後半"])
    )


def _taipei_today():
    return datetime.now(ZoneInfo("Asia/Taipei")).date()


def _is_explicit_red_flag_uncertainty(text: str) -> bool:
    normalized = re.sub(r"[\s，。！？!?、]", "", text)
    return any(
        term in normalized
        for term in ("不知道", "不確定", "不清楚", "沒辦法判斷", "無法判斷", "說不準")
    )


def _complete_uncertain_red_flag_screen(case: TriageCase, source_text: str) -> None:
    patient = case.patient_input
    patient.red_flags_checked = True
    patient.red_flags = []
    patient.red_flags_status = "uncertain"
    patient.urgency_normalized.semantic_status = "uncertain"
    patient.urgency_normalized.answer_classification = "uncertain_after_clarification"
    patient.urgency_normalized.confidence = 1.0
    patient.urgency_normalized.source_text = source_text
    patient.urgency_normalized.needs_clarification = False
    patient.urgency_normalized.follow_up_reason = "clarification 後使用者仍無法判斷急迫症狀"
    _consume_field(case, RED_FLAG_QUESTION_KEY, "uncertain", 1.0)


def _weekday_range(start: str, end: str) -> list[str]:
    if start not in ALL_WEEKDAYS or end not in ALL_WEEKDAYS:
        return []
    start_index = ALL_WEEKDAYS.index(start)
    end_index = ALL_WEEKDAYS.index(end)
    if start_index <= end_index:
        return ALL_WEEKDAYS[start_index : end_index + 1]
    return [*ALL_WEEKDAYS[start_index:], *ALL_WEEKDAYS[: end_index + 1]]


def _unique(items: list[str]) -> list[str]:
    return list(dict.fromkeys(items))


def _refresh_collected_fields(case: TriageCase) -> None:
    patient = case.patient_input
    fields = []
    if patient.symptom:
        fields.append("symptom")
    if patient.body_part:
        fields.append("body_part")
    if patient.duration:
        fields.append("duration")
    if patient.severity:
        fields.append("severity")
    if patient.onset:
        fields.append("onset")
    if patient.accompanying_symptoms:
        fields.append("accompanying_symptoms")
    if patient.red_flags:
        fields.append("red_flags")
    if patient.red_flags_checked:
        fields.append("red_flags_checked")
    if case.availability.preferred_days:
        fields.append("preferred_days")
    if case.availability.preferred_dates:
        fields.append("preferred_dates")
    if case.availability.preferred_sessions:
        fields.append("preferred_sessions")
    patient.collected_fields = fields


def _score_severity(severity: Optional[str], text: str) -> int:
    value = severity or text
    if value == "severe":
        return 18
    if value == "moderate":
        return 8
    if value == "mild":
        return 3
    if any(word in value for word in ["很痛", "劇痛", "影響明顯", "嚴重"]):
        return 15
    if "明顯" in value:
        return 10
    if any(word in value for word in ["中等", "中度"]):
        return 5
    return 0


def _score_duration(duration: Optional[str], text: str) -> int:
    value = duration or text
    if any(word in value for word in ["6週", "六週", "2個月", "兩個月"]):
        return 8
    if any(word in value for word in ["2週", "兩週", "3週", "一個月", "1個月"]):
        return 5
    if any(word in value for word in ["週", "周", "4天", "5天", "6天", "7天"]):
        return 3
    return 0


def _score_function_limit(text: str) -> int:
    if any(word in text for word in ["幾乎不能活動", "不能活動"]):
        return 20
    if any(word in text for word in ["無法正常活動", "無法走路"]):
        return 15
    if any(word in text for word in ["走路困難", "工作", "爬樓梯很吃力"]):
        return 10
    if "影響" in text:
        return 5
    return 0


def _score_onset(onset: Optional[str], text: str) -> int:
    value = onset or text
    if any(word in value for word in ["快速惡化", "突然變嚴重"]):
        return 10
    if any(word in value for word in ["慢慢變差", "越來越"]):
        return 5
    return 0


def _score_inflammation(text: str) -> int:
    score = 0
    if any(word in text for word in ["發燒", "紅腫熱痛"]):
        score += 5
    if any(word in text for word in ["全身不適", "畏寒"]):
        score += 10
    return score


def _score_treatment(text: str) -> int:
    if any(word in text for word in ["休息沒改善", "止痛沒改善", "吃藥沒改善"]):
        return 5
    return 0


def _score_risk(text: str) -> int:
    score = 0
    if any(word in text for word in ["高齡", "老人", "慢性病", "糖尿病"]):
        score += 5
    if any(word in text for word in ["免疫低下", "化療"]):
        score += 10
    return score
