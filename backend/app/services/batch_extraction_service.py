from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.config import get_settings
from app.schemas import BatchAnswer, Message, SemanticExtraction, TriageCase
from app.services.ai_service import complete_runtime_json as _complete_runtime_json, runtime_ai_available
from app.services.confidence_scoring import MAX_QUESTION_ATTEMPTS
from app.services.department_preference_service import capture_department_preference
from app.services.field_acceptance import (
    has_symptom_semantics,
    normalized_value_valid as strict_normalized_value_valid,
    plausible_semantic_target,
    requires_semantic_refinement,
)
from app.services.rule_engine import (
    CHECKLIST_FIELD_ORDER,
    apply_semantic_extractions,
    apply_user_message,
    missing_checklist_fields,
)

logger = logging.getLogger(__name__)

_ALLOWED_FIELDS = frozenset(CHECKLIST_FIELD_ORDER)
_ALLOWED_STATUSES = {"available", "unavailable", "unknown", "partial", "ambiguous"}
_META_REPLY_TERMS = (
    "我剛剛已經回答了",
    "我不是說了嗎",
    "剛剛有講",
    "前面講過了",
    "我剛才回答過",
    "不是才說過",
)


async def complete_prompt(prompt: str) -> str:
    """Compatibility seam for tests; production always uses Cerebras JSON mode."""
    return await _complete_runtime_json(prompt, purpose="semantic_extraction")


class _AiExtractionItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field: str
    normalized_value: Any = None
    semantic_status: str = "unknown"
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    source_text: str = ""
    needs_clarification: bool = False
    follow_up_reason: str | None = None


class _AiExtractionPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    extractions: list[_AiExtractionItem]


@dataclass
class BatchExtractionOutcome:
    deterministic_fields: list[str] = field(default_factory=list)
    accepted_fields: list[str] = field(default_factory=list)
    ai_fields: list[str] = field(default_factory=list)
    unresolved_fields: list[str] = field(default_factory=list)
    ai_attempted: bool = False
    fallback_reason: str | None = None


async def extract_batch_answers(
    case: TriageCase,
    answers: list[BatchAnswer],
) -> BatchExtractionOutcome:
    """Consume a keyed batch, calling at most one AI provider for ambiguity."""
    outcome = BatchExtractionOutcome()
    answered_text: dict[str, str] = {}
    ambiguous_fallbacks: dict[str, SemanticExtraction] = {}
    settings = get_settings()
    provider = "cerebras"

    for answer in answers:
        text = answer.answer.strip()
        if not text:
            continue
        key = str(answer.key)
        if key in _ALLOWED_FIELDS and key != "red_flags" and _is_meta_reply(text):
            previous_answer = _previous_keyed_answer(case, key)
            case.history_records.append(Message(role="user", content=f"[{key}] {text}"))
            if previous_answer:
                previous_extractions = _deterministic_extractions_for_answer(
                    case,
                    key,
                    previous_answer,
                )
                apply_semantic_extractions(case, previous_extractions)
            if _field_has_accepted_value(case, key):
                if key not in outcome.deterministic_fields:
                    outcome.deterministic_fields.append(key)
                if key not in outcome.accepted_fields:
                    outcome.accepted_fields.append(key)
            else:
                attempts = case.conversation_state.question_attempts.get(key, 0)
                case.conversation_state.question_attempts[key] = min(
                    attempts,
                    MAX_QUESTION_ATTEMPTS - 1,
                )
                if key in case.conversation_state.consumed_fields:
                    case.conversation_state.consumed_fields.remove(key)
                case.conversation_state.field_statuses[key] = "unknown"
                case.conversation_state.field_confidence[key] = 0.0
                case.conversation_state.clarification_reasons[key] = (
                    "meta reply did not count as a new clinical answer"
                )
            continue
        answered_text[key] = text
        before_missing = set(missing_checklist_fields(case, apply_attempt_fallback=False))
        case.history_records.append(Message(role="user", content=f"[{key}] {text}"))
        if key == "department_clarification":
            case.patient_input.department_context = text
        preference = capture_department_preference(case, text)
        if preference and preference.resolved and "requested_department" not in outcome.accepted_fields:
            outcome.accepted_fields.append("requested_department")
        if key == "duration" and _looks_ambiguous(text):
            fallback_case = TriageCase(case_id=case.case_id)
            fallback = _deterministic_extraction_for_answer(
                fallback_case,
                key,
                text,
                allow_ambiguous=True,
            )
            if fallback is not None:
                ambiguous_fallbacks[key] = fallback
        extractions = _deterministic_extractions_for_answer(case, key, text)
        if extractions:
            apply_semantic_extractions(case, extractions)
        if (
            key in _ALLOWED_FIELDS
            and key not in case.conversation_state.consumed_fields
            and not any(item.field == key for item in extractions)
        ):
            case.conversation_state.field_statuses[key] = "unknown"
            case.conversation_state.field_confidence[key] = 0.0
            case.conversation_state.clarification_reasons[key] = "answer did not pass strict field validation"
        if key == "red_flags":
            logger.info(
                "[RED_FLAGS] classification=%s attempt=%s resolved_status=%s",
                case.patient_input.urgency_normalized.answer_classification,
                case.conversation_state.question_attempts.get("red_flags", 0),
                case.patient_input.red_flags_status,
            )
        after_missing = set(missing_checklist_fields(case, apply_attempt_fallback=False))
        resolved_now = [field_name for field_name in before_missing if field_name not in after_missing]
        for field_name in resolved_now:
            if field_name not in outcome.deterministic_fields:
                outcome.deterministic_fields.append(field_name)
            if field_name not in outcome.accepted_fields:
                outcome.accepted_fields.append(field_name)

    unresolved = [
        field_name
        for field_name in missing_checklist_fields(case, apply_attempt_fallback=False)
        if field_name in answered_text
    ]
    # Red-flag completion is deterministic-only. AI may never mark the screen
    # checked merely by returning a JSON field.
    # A non-empty keyed answer for an unresolved field must reach the semantic
    # provider once when it is available. Failing to understand the answer
    # deterministically is exactly when semantic refinement is needed.
    semantic_sources = {
        field_name: answered_text[field_name]
        for field_name in unresolved
        if field_name != "red_flags"
    }
    if semantic_sources:
        all_missing = missing_checklist_fields(case, apply_attempt_fallback=False)
        for source_text in tuple(semantic_sources.values()):
            for field_name in all_missing:
                if (
                    field_name != "red_flags"
                    and field_name not in semantic_sources
                    and plausible_semantic_target(field_name, source_text)
                ):
                    semantic_sources[field_name] = source_text
    ai_targets = list(semantic_sources)
    if not ai_targets:
        outcome.unresolved_fields = [
            field_name
            for field_name in missing_checklist_fields(case)
            if field_name in answered_text
        ]
        _log_extraction_outcome(answered_text, outcome, provider, settings)
        return outcome

    if not runtime_ai_available(settings):
        outcome.fallback_reason = f"provider_key_missing:{provider}"
        _apply_ambiguous_fallbacks(case, ambiguous_fallbacks, outcome)
        outcome.unresolved_fields = [
            field_name
            for field_name in missing_checklist_fields(case)
            if field_name in answered_text
        ]
        logger.info("batch extraction skipped reason=%s", outcome.fallback_reason)
        _log_extraction_outcome(answered_text, outcome, provider, settings)
        return outcome

    outcome.ai_attempted = True
    prompt = _build_batch_prompt(case, semantic_sources, ai_targets)
    try:
        raw = await complete_prompt(prompt)
        extractions = _parse_ai_extractions(raw, answered_text, ai_targets)
        apply_semantic_extractions(
            case,
            extractions,
            allow_red_flag_completion=False,
        )
        outcome.ai_fields = [item.field for item in extractions]
        remaining = set(missing_checklist_fields(case, apply_attempt_fallback=False))
        for item in extractions:
            if item.field not in remaining and item.field not in outcome.accepted_fields:
                outcome.accepted_fields.append(item.field)
    except Exception as exc:
        status = getattr(exc, "status_code", None)
        suffix = f":{status}" if status is not None else ""
        outcome.fallback_reason = f"{type(exc).__name__}{suffix}"
        _apply_ambiguous_fallbacks(case, ambiguous_fallbacks, outcome)
        logger.warning(
            "batch extraction provider fallback provider=%s reason=%s",
            provider,
            outcome.fallback_reason,
        )

    outcome.unresolved_fields = [
        field_name
        for field_name in missing_checklist_fields(case)
        if field_name in answered_text
    ]
    _log_extraction_outcome(answered_text, outcome, provider, settings)
    return outcome


def _log_extraction_outcome(
    answered_text: dict[str, str],
    outcome: BatchExtractionOutcome,
    provider: str,
    settings: Any,
) -> None:
    model = getattr(settings, "cerebras_model", "gpt-oss-120b")
    logger.info(
        "[EXTRACTION] current_question=%s deterministic_fields=%s accepted_fields=%s "
        "semantic_fallback_called=%s semantic_provider=%s semantic_model=%s",
        ",".join(answered_text.keys()) or "none",
        outcome.deterministic_fields,
        outcome.accepted_fields,
        outcome.ai_attempted,
        provider if outcome.ai_attempted else "none",
        model if outcome.ai_attempted else "none",
    )


def _apply_ambiguous_fallbacks(
    case: TriageCase,
    fallbacks: dict[str, SemanticExtraction],
    outcome: BatchExtractionOutcome,
) -> None:
    """Use prior deterministic behavior only when semantic AI cannot respond."""
    if not fallbacks:
        return
    apply_semantic_extractions(case, list(fallbacks.values()))
    remaining = set(missing_checklist_fields(case, apply_attempt_fallback=False))
    for field_name in fallbacks:
        if field_name in remaining:
            continue
        if field_name not in outcome.deterministic_fields:
            outcome.deterministic_fields.append(field_name)
        if field_name not in outcome.accepted_fields:
            outcome.accepted_fields.append(field_name)


def _deterministic_extraction_for_answer(
    case: TriageCase,
    key: str,
    text: str,
    *,
    allow_ambiguous: bool = False,
) -> SemanticExtraction | None:
    if (
        key == "red_flags"
        and case.patient_input.red_flags_status == "ambiguous"
        and _is_explicit_red_flag_uncertainty(text)
    ):
        return SemanticExtraction(
            field=key,
            normalized_value=[],
            semantic_status="uncertain",
            confidence=1.0,
            source_text=text,
            needs_clarification=False,
            follow_up_reason="clarification 後使用者仍無法判斷急迫症狀",
            extractor="deterministic_batch",
        )
    temporary = TriageCase(case_id=case.case_id)
    temporary.conversation_state.last_question_key = key
    apply_user_message(temporary, text)
    ambiguous = _looks_ambiguous(text)
    accept_ambiguous = allow_ambiguous or not ambiguous
    status = temporary.conversation_state.field_statuses.get(key, "available")
    confidence = temporary.conversation_state.field_confidence.get(key, 0.9)
    value: Any = None

    if key == "symptom" and accept_ambiguous and has_symptom_semantics(text):
        value = text
    elif key == "body_part" and accept_ambiguous:
        value = temporary.patient_input.body_part
    elif key == "duration" and accept_ambiguous and temporary.patient_input.duration:
        value = temporary.patient_input.duration
    elif key == "severity" and accept_ambiguous:
        case.patient_input.severity_normalized = temporary.patient_input.severity_normalized
        value = (
            {"severity_level": temporary.patient_input.severity_normalized.severity_level}
            if temporary.patient_input.severity_normalized.severity_level
            else None
        )
        status = temporary.patient_input.severity_normalized.semantic_status
        confidence = temporary.patient_input.severity_normalized.confidence
    elif key == "preferred_days" and accept_ambiguous:
        value = temporary.availability.preferred_days
        status = temporary.availability.semantic_status.get(key, status)
        confidence = temporary.availability.confidence.get(key, confidence)
    elif key == "preferred_sessions" and accept_ambiguous:
        value = temporary.availability.preferred_sessions
        status = temporary.availability.semantic_status.get(key, status)
        confidence = temporary.availability.confidence.get(key, confidence)
    elif key == "red_flags":
        case.patient_input.urgency_normalized = temporary.patient_input.urgency_normalized
        if not temporary.patient_input.red_flags_checked:
            urgency = temporary.patient_input.urgency_normalized
            if urgency.semantic_status in {"ambiguous", "unknown", "partial"}:
                return SemanticExtraction(
                    field=key,
                    normalized_value=[],
                    semantic_status="ambiguous",
                    confidence=urgency.confidence,
                    source_text=text,
                    needs_clarification=True,
                    follow_up_reason=urgency.follow_up_reason or "急迫症狀回答不明確",
                    extractor="deterministic_batch",
                )
            return None
        value = temporary.patient_input.red_flags
        status = "available" if value else "unavailable"
        confidence = max(confidence, 0.9)

    if value is None or (isinstance(value, list) and not value and status != "unavailable"):
        return None
    return SemanticExtraction(
        field=key,
        normalized_value=value,
        semantic_status=status,
        confidence=confidence,
        source_text=text,
        needs_clarification=False,
        extractor="deterministic_batch",
    )


def _deterministic_extractions_for_answer(
    case: TriageCase,
    key: str,
    text: str,
) -> list[SemanticExtraction]:
    """Extract the keyed field plus other explicit deterministic slots."""
    primary = _deterministic_extraction_for_answer(case, key, text)
    results = [primary] if primary is not None else []
    if _looks_ambiguous(text):
        return results
    temporary = TriageCase(case_id=case.case_id)
    temporary.conversation_state.last_question_key = key
    apply_user_message(temporary, text)

    def add(field_name: str, value: Any, status: str = "available", confidence: float = 0.9) -> None:
        if field_name == key or value is None or (value == [] and status != "unavailable"):
            return
        results.append(
            SemanticExtraction(
                field=field_name,
                normalized_value=value,
                semantic_status=status,
                confidence=confidence,
                source_text=text,
                needs_clarification=False,
                extractor="deterministic_multi_slot",
            )
        )

    add("body_part", temporary.patient_input.body_part)
    add("duration", temporary.patient_input.duration)
    if temporary.patient_input.severity_normalized.severity_level:
        add(
            "severity",
            {"severity_level": temporary.patient_input.severity_normalized.severity_level},
            temporary.patient_input.severity_normalized.semantic_status,
            temporary.patient_input.severity_normalized.confidence,
        )
    if temporary.availability.preferred_days:
        add(
            "preferred_days",
            temporary.availability.preferred_days,
            temporary.availability.semantic_status.get("preferred_days", "partial"),
            temporary.availability.confidence.get("preferred_days", 0.9),
        )
    if temporary.availability.preferred_dates:
        add(
            "preferred_dates",
            temporary.availability.preferred_dates,
            temporary.availability.semantic_status.get("preferred_dates", "partial"),
            temporary.availability.confidence.get("preferred_dates", 0.94),
        )
    if temporary.availability.preferred_sessions:
        add(
            "preferred_sessions",
            temporary.availability.preferred_sessions,
            temporary.availability.semantic_status.get("preferred_sessions", "partial"),
            temporary.availability.confidence.get("preferred_sessions", 0.86),
        )
    if temporary.patient_input.red_flags_checked:
        add(
            "red_flags",
            temporary.patient_input.red_flags,
            "available" if temporary.patient_input.red_flags else "unavailable",
            0.9,
        )
    return results


def _looks_ambiguous(text: str) -> bool:
    return requires_semantic_refinement(text)


def _is_explicit_red_flag_uncertainty(text: str) -> bool:
    return any(term in text for term in ("不知道", "不確定", "不清楚", "沒辦法判斷", "無法判斷", "說不準"))


def _is_meta_reply(text: str) -> bool:
    normalized = "".join(character for character in text if character not in " \t\r\n，。！？!?、")
    return any(term in normalized for term in _META_REPLY_TERMS)


def _previous_keyed_answer(case: TriageCase, key: str) -> str | None:
    prefix = f"[{key}] "
    for message in reversed(case.history_records):
        if message.role == "user" and message.content.startswith(prefix):
            candidate = message.content[len(prefix) :].strip()
            if candidate and not _is_meta_reply(candidate):
                return candidate
    return None


def _field_has_accepted_value(case: TriageCase, field_name: str) -> bool:
    patient = case.patient_input
    if field_name in {"symptom", "body_part", "duration", "severity"}:
        return bool(getattr(patient, field_name))
    if field_name == "preferred_days":
        return bool(case.availability.preferred_days)
    if field_name == "preferred_sessions":
        return bool(case.availability.preferred_sessions)
    return False


def _build_batch_prompt(
    case: TriageCase,
    answered_text: dict[str, str],
    targets: list[str],
) -> str:
    target_answers = {key: answered_text[key] for key in targets}
    history = [item.model_dump() for item in case.history_records[-20:]]
    return f"""你是醫療問診的 structured extraction 元件，只能抽取指定欄位。
不得決定 stage、is_complete、confirmed、red_flags_checked、next_question 或推薦流程。

允許欄位：{json.dumps(targets, ensure_ascii=False)}
本批仍需協助解析的 keyed answers：
{json.dumps(target_answers, ensure_ascii=False, indent=2)}

目前 patient_input：
{json.dumps(case.patient_input.model_dump(), ensure_ascii=False, indent=2)}

目前 availability：
{json.dumps(case.availability.model_dump(), ensure_ascii=False, indent=2)}

最近 history：
{json.dumps(history, ensure_ascii=False, indent=2)}

只輸出 JSON：
{{
  "extractions": [
    {{
      "field": "body_part",
      "normalized_value": "右肩",
      "semantic_status": "available",
      "confidence": 0.9,
      "source_text": "右肩附近",
      "needs_clarification": false,
      "follow_up_reason": null
    }}
  ]
}}
semantic_status 只能是 available、unavailable、unknown、partial、ambiguous。
confidence 必須介於 0 與 1。不得輸出未列在允許欄位中的 field。
key 只表示目前系統正在問的欄位，不代表原句一定回答了該欄位；答非所問時不得硬填。
同一句若明確包含其他允許欄位，可以一併抽取，但不得猜測未提及資訊。
最外層 key 必須且只能是 extractions，不可改成 semantic_extractions 或其他名稱。
source_text 必須逐字複製自本批 keyed answers 的一段連續原文，不可改寫或省略。
只要原句對任一允許欄位有明確資訊，就必須輸出該 extraction；不要因其他欄位不確定而整體回空。
duration 必須正規化為「數字+天／週／個月／年」，例如 3天、2週、6個月、1年；半年轉為 6個月，一年半轉為 18個月。
severity 的 normalized_value 只能是字串 "mild"、"moderate" 或 "severe"；輕微／還好轉為 mild，普通／中等／中度轉為 moderate，嚴重／很嚴重／痛到無法睡覺轉為 severe。不得輸出「輕微」「中等」「嚴重程度低」等其他字串。
preferred_days 只能正規化為週一至週日；preferred_sessions 只能是上午、下午、夜間。"""


def _parse_ai_extractions(
    raw: str,
    answered_text: dict[str, str],
    targets: list[str],
) -> list[SemanticExtraction]:
    text = str(raw).strip().replace("```json", "").replace("```", "").strip()
    try:
        raw_payload = json.loads(text)
        payload = _AiExtractionPayload.model_validate(raw_payload)
    except (json.JSONDecodeError, ValidationError, TypeError) as exc:
        envelope_keys = sorted(raw_payload.keys()) if isinstance(locals().get("raw_payload"), dict) else []
        logger.warning(
            "[AI] purpose=semantic_validation schema_validation=failed envelope_keys=%s error_type=%s",
            envelope_keys,
            type(exc).__name__,
        )
        raise ValueError("invalid batch extraction JSON/schema") from exc

    target_set = set(targets)
    validated: list[SemanticExtraction] = []
    seen: set[str] = set()
    rejected: list[dict[str, str]] = []
    normalized_fields: list[str] = []
    grounded_fields: list[str] = []
    for item in payload.extractions:
        field_name = item.field.strip()
        reason = _semantic_rejection_reason(item, target_set, seen)
        if reason:
            rejected.append({"field": field_name or "empty", "reason": reason})
            continue
        normalized_fields.append(field_name)
        source_text = item.source_text.strip()
        if not _source_text_grounded(source_text, answered_text):
            rejected.append({"field": field_name, "reason": "source_not_grounded"})
            continue
        grounded_fields.append(field_name)
        seen.add(field_name)
        validated.append(
            SemanticExtraction(
                field=field_name,
                normalized_value=item.normalized_value,
                semantic_status=item.semantic_status,
                confidence=item.confidence,
                source_text=source_text,
                needs_clarification=item.needs_clarification,
                follow_up_reason=item.follow_up_reason,
                extractor="ai_batch",
            )
        )
    logger.info(
        "[AI] purpose=semantic_validation raw_fields=%s schema_validation=passed "
        "normalized_fields=%s grounded_fields=%s final_fields=%s rejected=%s",
        [item.field for item in payload.extractions],
        normalized_fields,
        grounded_fields,
        [item.field for item in validated],
        rejected,
    )
    return validated


def _semantic_rejection_reason(
    item: _AiExtractionItem,
    target_set: set[str],
    seen: set[str],
) -> str | None:
    field_name = item.field.strip()
    if field_name not in _ALLOWED_FIELDS:
        return "field_not_allowed"
    if field_name not in target_set:
        return "field_not_requested"
    if field_name == "red_flags":
        return "red_flags_deterministic_only"
    if field_name in seen:
        return "duplicate_field"
    if item.semantic_status not in _ALLOWED_STATUSES:
        return "invalid_status"
    if not _normalized_value_valid(field_name, item.normalized_value, item.semantic_status):
        return "invalid_normalized_value"
    return None


def _source_text_grounded(source_text: str, answered_text: dict[str, str]) -> bool:
    return bool(source_text) and any(source_text in answer for answer in answered_text.values())


def _normalized_value_valid(field_name: str, value: Any, status: str) -> bool:
    return strict_normalized_value_valid(field_name, value, status)
