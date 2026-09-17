from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

from app.config import get_settings
from app.schemas import DepartmentResult, SemanticExtraction, TriageCase, UrgencyResult
from app.services.ai_service import complete_runtime_json as _complete_runtime_json, runtime_ai_available
from app.services.field_acceptance import normalized_value_valid as strict_normalized_value_valid
from app.services.negation_utils import strip_negated_red_flags

logger = logging.getLogger(__name__)


async def complete_prompt(prompt: str) -> str:
    """Compatibility seam for the legacy adapter; runtime provider is Cerebras."""
    return await _complete_runtime_json(prompt, purpose="semantic_extraction")


@dataclass
class RagTriageSuggestion:
    triage: UrgencyResult | None = None
    reply: str | None = None
    semantic_extractions: list[SemanticExtraction] | None = None


async def refine_case_with_ai(case: TriageCase) -> RagTriageSuggestion | None:
    """Use the rag_demo-style prompt to refine collected symptom fields.

    This is an adapter, not a route dependency. It preserves the current backend
    schema and lets the deterministic rule engine remain the fallback source of
    truth when AI is disabled or returns an invalid payload.
    """
    if not _ai_available():
        logger.info("rag_triage_adapter refine skipped: AI key is not configured")
        return None

    prompt = _build_symptom_collection_prompt(case)
    try:
        raw = await complete_prompt(prompt)
        logger.debug("rag_triage_adapter response received case_id=%s chars=%s", case.case_id, len(raw))
        data = _parse_json_object(raw)
    except Exception as exc:
        logger.warning(
            "rag_triage_adapter refine failed case_id=%s error=%s",
            case.case_id,
            exc,
        )
        return None

    user_sources = [message.content for message in case.history_records if message.role == "user"]
    semantic_extractions = _semantic_extractions_from_ai(
        data.get("semantic_extractions"),
        user_sources=user_sources,
    )
    if semantic_extractions:
        # Import locally to keep the adapter independent at module load time.
        # The rule engine performs field allow-listing, confidence checks and
        # merge protection. Generative AI may never complete red-flag safety.
        from app.services.rule_engine import apply_semantic_extractions

        apply_semantic_extractions(
            case,
            semantic_extractions,
            allow_red_flag_completion=False,
        )

    triage = None
    triage_data = data.get("triage")
    if isinstance(triage_data, dict):
        triage = _urgency_result_from_ai(triage_data)

    reply = data.get("reply")
    return RagTriageSuggestion(
        triage=triage,
        reply=str(reply).strip() if isinstance(reply, str) and reply.strip() else None,
        semantic_extractions=semantic_extractions,
    )


async def detect_department_with_ai(
    case: TriageCase,
    departments: list[dict[str, Any]],
) -> DepartmentResult | None:
    """Pick a department from the DB-backed department list.

    This ports the useful rag_demo idea into backend service form: the AI sees
    the active DB department names and must return one exact child department.
    Invalid or invented departments are rejected so the caller can fall back to
    deterministic rules.
    """
    if not departments or not _ai_available():
        logger.info(
            "rag_triage_adapter department skipped case_id=%s has_departments=%s ai_available=%s",
            case.case_id,
            bool(departments),
            _ai_available(),
        )
        return None

    candidates = [item for item in departments if item.get("dept_id") is not None and item.get("child_dept")]
    if not candidates:
        return None

    prompt = _build_department_prompt(case, candidates)
    try:
        raw = await complete_prompt(prompt)
        logger.debug(
            "rag_triage_adapter department response received case_id=%s chars=%s",
            case.case_id,
            len(raw),
        )
        data = _parse_json_object(raw)
    except Exception as exc:
        logger.warning(
            "rag_triage_adapter department parse failed case_id=%s error=%s",
            case.case_id,
            exc,
        )
        return None

    dept_id = _optional_int(data.get("dept_id"))
    matches = [
        item for item in candidates
        if _optional_int(item.get("dept_id")) == dept_id
    ]
    if len(matches) != 1:
        logger.warning(
            "rag_triage_adapter rejected department case_id=%s dept_id=%s reason=dept_id_not_in_candidates",
            case.case_id,
            dept_id,
        )
        return None

    selected = matches[0]
    reasons = [str(item) for item in data.get("reason", []) if str(item).strip()]
    if not reasons:
        reasons = ["AI 依據問診內容從資料庫科別清單中選出此科別"]
    reasons.append("來源：rag_demo adapter，已限制於後端 DB 科別清單")

    return DepartmentResult(
        dept_id=int(selected["dept_id"]),
        parentDept=str(selected["parent_dept"]),
        childDept=str(selected["child_dept"]),
        confidence=float(data.get("confidence", 0.0) or 0.0),
        reason=reasons,
    )


def merge_ai_next_question(case: TriageCase, suggestion: RagTriageSuggestion | None) -> bool:
    if suggestion is None or suggestion.triage is None:
        return False

    ai_question = suggestion.triage.next_question or suggestion.reply
    if not ai_question:
        return False

    attempted_override = bool(case.triage.next_question)
    if case.triage.next_question:
        logger.info(
            "rag_triage_adapter kept deterministic next_question case_id=%s deterministic=%s ai_question=%s ai_attempted_override=%s last_question_key=%s consumed_fields=%s question_attempts=%s field_statuses=%s availability=%s red_flags_checked=%s next_question=%s",
            case.case_id,
            case.triage.next_question,
            ai_question,
            attempted_override,
            case.conversation_state.last_question_key,
            case.conversation_state.consumed_fields,
            case.conversation_state.question_attempts,
            case.conversation_state.field_statuses,
            case.availability.model_dump(),
            case.patient_input.red_flags_checked,
            case.triage.next_question,
        )
    else:
        logger.info(
            "rag_triage_adapter ignored AI next_question case_id=%s deterministic=%s ai_question=%s ai_attempted_override=%s last_question_key=%s consumed_fields=%s question_attempts=%s field_statuses=%s availability=%s red_flags_checked=%s next_question=%s",
            case.case_id,
            case.triage.next_question,
            ai_question,
            attempted_override,
            case.conversation_state.last_question_key,
            case.conversation_state.consumed_fields,
            case.conversation_state.question_attempts,
            case.conversation_state.field_statuses,
            case.availability.model_dump(),
            case.patient_input.red_flags_checked,
            case.triage.next_question,
        )

    case.triage.reasons.append("AI 追問建議已記錄，但 next_question 由後端 deterministic state machine 決定。")
    return attempted_override


def _ai_available() -> bool:
    return runtime_ai_available(get_settings())


def _build_department_prompt(case: TriageCase, departments: list[dict[str, Any]]) -> str:
    dept_list = json.dumps(
        [
            {
                "dept_id": item.get("dept_id"),
                "parent_dept": item.get("parent_dept"),
                "child_dept": item.get("child_dept"),
            }
            for item in departments
        ],
        ensure_ascii=False,
        indent=2,
    )
    symptom_text = _case_text(case)
    candidate_names = {str(item.get("child_dept") or "").strip() for item in departments}
    orthopedic_guidance = ""
    if {"一般骨科", "骨科"}.issubset(candidate_names):
        orthopedic_guidance = """

骨科候選規則：一般肌肉骨骼、膝蓋、關節、走路或運動傷害症狀，若沒有兒童或其他細分依據，
優先選正式候選中的「一般骨科」；只有資料明確支持其他候選時才選其他骨科相關 dept_id。"""
    return f"""你是台灣醫院掛號分診助理。你只能從下列資料庫科別清單中選一個科別，不可以自行創造科別。

科別清單：
{dept_list}
{orthopedic_guidance}

病患資料：
{symptom_text}

請只輸出 JSON，不要輸出其他文字：
{{
  "dept_id": 1234,
  "confidence": 0.0,
  "reason": ["理由1", "理由2"]
}}"""


def _parse_json_object(raw: str) -> dict[str, Any]:
    text = str(raw).strip().replace("```json", "").replace("```", "").strip()
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("AI response is not a JSON object")
    return data


def _semantic_extractions_from_ai(
    value: Any,
    *,
    user_sources: list[str] | None = None,
) -> list[SemanticExtraction]:
    if not isinstance(value, list):
        return []

    extractions: list[SemanticExtraction] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        field = str(item.get("field") or "").strip()
        status = str(item.get("semantic_status") or "unknown")
        source_text = str(item.get("source_text") or "").strip()
        if (
            field not in {"symptom", "body_part", "duration", "severity", "preferred_days", "preferred_sessions"}
            or status not in {"available", "unavailable", "unknown", "partial", "ambiguous"}
            or not _semantic_value_valid(field, item.get("normalized_value"), status)
            or not _source_text_grounded(source_text, user_sources or [])
        ):
            continue
        try:
            extractions.append(
                SemanticExtraction(
                    field=field,
                    normalized_value=item.get("normalized_value"),
                    semantic_status=status,
                    confidence=float(item.get("confidence", 0.0) or 0.0),
                    source_text=source_text,
                    needs_clarification=bool(item.get("needs_clarification", False)),
                    follow_up_reason=item.get("follow_up_reason"),
                    extractor="ai",
                )
            )
        except (TypeError, ValueError):
            logger.warning("rag_triage_adapter skipped invalid semantic extraction item=%s", item)
    return extractions


def _source_text_grounded(source_text: str, user_sources: list[str]) -> bool:
    return bool(source_text) and any(source_text in source for source in user_sources)


def _semantic_value_valid(field: str, value: Any, status: str) -> bool:
    return strict_normalized_value_valid(field, value, status)


def _urgency_result_from_ai(data: dict[str, Any]) -> UrgencyResult:
    reasons = data.get("reasons", [])
    if not isinstance(reasons, list):
        reasons = []
    return UrgencyResult(
        urgency_score=_optional_int(data.get("urgency_score")),
        urgency_level=str(data.get("urgency_level") or "") or None,
        warning_required=bool(data.get("warning_required", False)),
        warning_message=data.get("warning_message"),
        need_more_info=bool(data.get("need_more_info", True)),
        next_question=data.get("next_question"),
        reasons=[str(item) for item in reasons if str(item).strip()],
        is_final=bool(data.get("is_final", False)),
    )


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _case_text(case: TriageCase) -> str:
    patient = case.patient_input
    parts = [
        patient.symptom,
        patient.body_part or "",
        patient.duration or "",
        patient.severity or "",
        patient.onset or "",
        " ".join(patient.accompanying_symptoms),
        " ".join(patient.red_flags),
    ]
    return strip_negated_red_flags("；".join(part for part in parts if part))


def _build_symptom_collection_prompt(case: TriageCase) -> str:
    history = "\n".join(
        f"{'使用者' if message.role == 'user' else '助理'}: {message.content}"
        for message in case.history_records
    )
    current_input = case.patient_input.model_dump()

    return f"""你是醫療問診的語意抽取器。你只能做 extraction、normalization、confidence estimation。
不要決定 next_question、stage、waiting_confirmation 或流程轉移，這些一律由 backend deterministic state machine 控制。

目前對話紀錄：
{history}

目前已收集到的症狀資料：{json.dumps(current_input, ensure_ascii=False, indent=2)}

你的任務：
1. 將使用者自然語言整理成 semantic_extractions。
2. semantic_status 只能使用 available、unavailable、unknown、partial、ambiguous。
3. confidence 使用 0 到 1。
4. 若信心不足，設定 needs_clarification=true 與 follow_up_reason。
5. 只輸出使用者實際表達、且目前尚未有明確值的欄位，不得猜測。
6. 不得輸出 red_flags；急迫症狀由 deterministic safety parser 獨立處理。
7. 不要輸出 patient_input、triage、next_question、stage、科別或掛號資訊。
8. duration 必須正規化為「數字+天／週／個月／年」，例如 3天、2週、6個月、1年；半年轉為 6個月，一年半轉為 18個月。
9. source_text 必須逐字複製使用者原話中的連續文字，不可改寫。

請只輸出 JSON，不要輸出其他文字：
{{
  "semantic_extractions": [
    {{
      "field": "preferred_days",
      "normalized_value": ["週一", "週二"],
      "semantic_status": "partial",
      "confidence": 0.82,
      "source_text": "週一週二可以",
      "needs_clarification": false,
      "follow_up_reason": null
    }}
  ]
}}"""
