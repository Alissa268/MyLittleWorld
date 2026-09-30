from __future__ import annotations

from app.schemas import Message, SemanticExtraction, TriageCase
from app.services.department_reasoning_service import (
    accepted_semantic_evidence,
    retrieve_official_evidence,
    validate_candidate_proposal,
)
from app.services.rule_engine import apply_semantic_extractions


def _record(department: str, concept: str, source_id: str) -> dict:
    return {
        "department_name": department,
        "concept": concept,
        "source_id": source_id,
        "evidence_text": f"{department}：{concept}",
        "source_priority": 1,
    }


def _resolution(department: str, dept_id: int) -> dict:
    return {
        "knowledge_department_name": department,
        "status": "resolved",
        "db_dept_id": dept_id,
        "db_parent_dept": "測試系",
        "db_child_dept": department,
    }


def _case_with_extraction(
    text: str,
    normalized_value: str | list[str],
    assertion: str,
    *,
    field: str = "symptom",
    status: str = "available",
) -> TriageCase:
    case = TriageCase(case_id=f"assertion-{assertion}")
    case.history_records = [Message(role="user", content=text)]
    case.semantic_extractions = [SemanticExtraction(
        field=field,
        normalized_value=normalized_value,
        semantic_status=status,
        assertion=assertion,
        confidence=0.96,
        source_text=text,
        extractor="ai",
    )]
    return case


def _retrieve(case: TriageCase, concept: str = "胸痛") -> list[dict]:
    return retrieve_official_evidence(
        case,
        [_record("一般內科", concept, "general_official")],
        [_resolution("一般內科", 1232)],
    )


def test_present_assertion_creates_positive_department_evidence():
    case = _case_with_extraction("我有胸痛", "胸痛", "present")

    retrieved = _retrieve(case)

    assert len(retrieved) == 1
    assert retrieved[0]["knowledge_concept"] == "胸痛"
    assert retrieved[0]["patient_source_text"] == "我有胸痛"


def test_absent_assertion_is_saved_but_not_positive_patient_or_department_state():
    case = TriageCase(case_id="assertion-absent-apply")
    case.history_records = [Message(role="user", content="我沒有胸痛")]
    extraction = SemanticExtraction(
        field="symptom",
        normalized_value="胸痛",
        semantic_status="available",
        assertion="absent",
        confidence=0.97,
        source_text="沒有胸痛",
        extractor="ai",
    )

    apply_semantic_extractions(case, [extraction])

    assert case.semantic_extractions == [extraction]
    assert case.patient_input.symptom == ""
    assert case.patient_input.accompanying_symptoms == []
    assert _retrieve(case) == []


def test_uncertain_assertion_is_saved_but_not_positive_department_evidence():
    case = TriageCase(case_id="assertion-uncertain-apply")
    case.history_records = [Message(role="user", content="我不知道這算不算胸痛")]
    extraction = SemanticExtraction(
        field="accompanying_symptoms",
        normalized_value=["胸痛"],
        semantic_status="ambiguous",
        assertion="uncertain",
        confidence=0.75,
        source_text="不知道這算不算胸痛",
        extractor="ai",
    )

    apply_semantic_extractions(case, [extraction])

    assert case.semantic_extractions == [extraction]
    assert case.patient_input.accompanying_symptoms == []
    assert _retrieve(case) == []


def test_positive_synonym_normalization_retrieves_official_concept_without_dictionary():
    case = _case_with_extraction(
        "尿尿很痛",
        ["小便疼痛"],
        "present",
        field="accompanying_symptoms",
    )

    retrieved = _retrieve(case, "小便疼痛")

    assert len(retrieved) == 1
    assert retrieved[0]["knowledge_concept"] == "小便疼痛"
    assert retrieved[0]["patient_source_text"] == "尿尿很痛"


def test_absent_synonym_normalization_cannot_create_positive_department_evidence():
    case = _case_with_extraction(
        "尿尿不會痛",
        ["小便疼痛"],
        "absent",
        field="accompanying_symptoms",
    )

    assert _retrieve(case, "小便疼痛") == []


def test_mixed_statement_uses_ai_assertions_without_backend_polarity_parsing():
    text = "我沒有胸口痛，但是現在會頭暈"
    case = TriageCase(case_id="assertion-mixed")
    case.history_records = [Message(role="user", content=text)]
    case.semantic_extractions = [
        SemanticExtraction(
            field="accompanying_symptoms",
            normalized_value=["胸痛"],
            semantic_status="available",
            assertion="absent",
            confidence=0.97,
            source_text="沒有胸口痛",
            extractor="ai",
        ),
        SemanticExtraction(
            field="symptom",
            normalized_value="頭暈",
            semantic_status="available",
            assertion="present",
            confidence=0.98,
            source_text="現在會頭暈",
            extractor="ai",
        ),
    ]
    records = [
        _record("一般內科", "胸痛", "chest_official"),
        _record("測試甲科", "頭暈", "dizziness_official"),
    ]
    resolutions = [
        _resolution("一般內科", 1232),
        _resolution("測試甲科", 101),
    ]

    retrieved = retrieve_official_evidence(case, records, resolutions)

    assert {(item["dept_id"], item["knowledge_concept"]) for item in retrieved} == {
        (101, "頭暈"),
    }


def test_raw_history_never_becomes_department_fact_without_structured_evidence():
    case = TriageCase(case_id="raw-history-is-context-only")
    case.history_records = [Message(role="user", content="我沒有胸痛")]
    case.patient_input.symptom = "我沒有胸痛"

    assert accepted_semantic_evidence(case) == []
    assert _retrieve(case) == []


def test_clarification_evidence_is_not_lexically_scanned_for_department_truth():
    text = "只有一點點血絲，而且我沒有胸痛"
    case = TriageCase(case_id="clarification-is-workflow-only")
    case.history_records = [Message(role="user", content=text)]
    case.conversation_state.clarification_evidence["severity"] = text

    assert accepted_semantic_evidence(case) == []
    assert _retrieve(case) == []


def test_positive_normalized_interpretation_without_literal_canonical_term_survives():
    case = _case_with_extraction("症狀俗稱甲", "正式症狀甲", "present")

    retrieved = _retrieve(case, "正式症狀甲")

    assert len(retrieved) == 1
    assert retrieved[0]["knowledge_concept"] == "正式症狀甲"
    assert retrieved[0]["patient_source_text"] == "症狀俗稱甲"


def test_live_hematuria_denial_uses_structured_assertions_only():
    current_text = (
        "只有一點點血絲，不是整泡尿都紅，而且尿尿時有灼熱感。"
        "我沒有胸痛、呼吸困難、意識不清、大量出血、半邊無力或劇烈頭痛"
    )
    case = TriageCase(case_id="live-hematuria-assertion")
    case.history_records = [
        Message(role="user", content="我最近有血尿，已經兩天了"),
        Message(role="user", content=current_text),
    ]
    case.conversation_state.clarification_evidence["severity"] = current_text
    case.semantic_extractions = [
        SemanticExtraction(
            field="symptom", normalized_value="血尿", semantic_status="available",
            assertion="present", confidence=0.98, source_text="血尿", extractor="ai",
        ),
        SemanticExtraction(
            field="accompanying_symptoms", normalized_value=["胸痛"], semantic_status="available",
            assertion="absent", confidence=0.98, source_text="沒有胸痛", extractor="ai",
        ),
    ]
    records = [
        _record("腎臟科", "血尿", "kidney_official"),
        _record("一般內科", "胸痛", "general_official"),
    ]
    resolutions = [
        _resolution("腎臟科", 1242),
        _resolution("一般內科", 1232),
    ]

    retrieved = retrieve_official_evidence(case, records, resolutions)

    assert {(item["dept_id"], item["knowledge_concept"]) for item in retrieved} == {
        (1242, "血尿"),
    }
    proposal = {
        "status": "resolved",
        "candidates": [{
            "dept_id": 1232,
            "confidence": 0.92,
            "supporting_evidence": [{
                "patient_source_text": "沒有胸痛",
                "knowledge_source_id": "general_official",
                "knowledge_concept": "胸痛",
            }],
        }],
    }
    _, candidates, _, _ = validate_candidate_proposal(
        proposal,
        retrieved,
        [{"source_id": "kidney_official"}, {"source_id": "general_official"}],
        [
            {"dept_id": 1242, "parent_dept": "測試系", "child_dept": "腎臟科"},
            {"dept_id": 1232, "parent_dept": "測試系", "child_dept": "一般內科"},
        ],
        [message.content for message in case.history_records],
    )
    assert candidates == []
