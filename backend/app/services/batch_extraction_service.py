from __future__ import annotations

from dataclasses import dataclass, field

from app.config import get_settings
from app.schemas import BatchAnswer, SemanticExtraction, TriageCase
from app.services.ai_service import runtime_ai_available
from app.services.conversation_service import _enter_safety_check, safety_screen_resolved
from app.services.rag_triage_adapter import RagTriageSuggestion, refine_case_with_ai
from app.services.rule_engine import apply_semantic_extractions, apply_user_message, missing_checklist_fields


@dataclass
class BatchExtractionOutcome:
    deterministic_fields: list[str] = field(default_factory=list)
    accepted_fields: list[str] = field(default_factory=list)
    ai_fields: list[str] = field(default_factory=list)
    unresolved_fields: list[str] = field(default_factory=list)
    ai_attempted: bool = False
    fallback_reason: str | None = None
    turn_interpretation: RagTriageSuggestion | None = None
    free_text_received: bool = False


async def extract_batch_answers(
    case: TriageCase, answers: list[BatchAnswer], *, semantic_first: bool = False,
) -> BatchExtractionOutcome:
    """Legacy keyed transport shares the same Interpreter for all free text."""
    outcome = BatchExtractionOutcome()
    free_sources: list[str] = []
    answered_fields: list[str] = []
    for answer in answers:
        text = answer.answer.strip()
        if not text:
            continue
        key = str(answer.key)
        answered_fields.append(key)
        apply_user_message(case, text)
        choice = _structured_choice(key, text)
        if choice is None:
            free_sources.append(text)
            continue
        apply_semantic_extractions(case, [choice], allow_red_flag_completion=False)
        outcome.deterministic_fields.append(key)
        outcome.accepted_fields.append(key)

    if free_sources:
        outcome.free_text_received = True
        if case.conversation_state.last_question_key == "red_flags" and not safety_screen_resolved(case):
            _enter_safety_check(case)
        if runtime_ai_available(get_settings()):
            outcome.ai_attempted = True
            suggestion = await refine_case_with_ai(case, user_sources=free_sources)
            outcome.turn_interpretation = suggestion
            if suggestion is not None and suggestion.interpretation_complete:
                outcome.ai_fields = sorted({item.field for item in suggestion.semantic_extractions or []})
                if suggestion.department_preference is not None:
                    outcome.accepted_fields.append("requested_department")
            else:
                outcome.fallback_reason = "turn_interpretation_unavailable_or_invalid"
        else:
            outcome.fallback_reason = "runtime_unavailable"
    missing = set(missing_checklist_fields(case, apply_attempt_fallback=False))
    for name in outcome.ai_fields:
        if name not in missing and name not in outcome.accepted_fields:
            outcome.accepted_fields.append(name)
    outcome.unresolved_fields = [name for name in answered_fields if name in missing]
    return outcome


def _structured_choice(key: str, value: str) -> SemanticExtraction | None:
    """Exact canonical API choices only; a sentence is never a controlled enum."""
    options = {
        "preferred_days": {"週一", "週二", "週三", "週四", "週五", "週六", "週日"},
        "preferred_sessions": {"上午", "下午", "夜間"},
        "severity": {"mild", "moderate", "severe"},
    }
    if value not in options.get(key, set()):
        return None
    return SemanticExtraction(
        field=key, normalized_value=[value] if key.startswith("preferred_") else value,
        semantic_status="available", confidence=1.0, source_text=value,
        extractor="structured_choice",
    )
