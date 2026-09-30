from __future__ import annotations

import pytest

from app.schemas import Message, SemanticExtraction, TriageCase
from app.services.department_reasoning_service import (
    retrieve_official_evidence,
    validate_candidate_proposal,
)
from app.services.negation_utils import contains_non_negated_keyword


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


def _case_with_grounded_text(text: str) -> TriageCase:
    case = TriageCase(case_id="department-negation")
    case.history_records = [Message(role="user", content=text)]
    case.patient_input.symptom = text
    return case


def test_exact_live_hematuria_denial_cannot_create_general_medicine_evidence():
    current_text = (
        "只有一點點血絲，不是整泡尿都紅，而且尿尿時有灼熱感。"
        "我沒有胸痛、呼吸困難、意識不清、大量出血、半邊無力或劇烈頭痛"
    )
    case = TriageCase(case_id="live-hematuria-negation")
    case.history_records = [
        Message(role="user", content="我最近有血尿，已經兩天了"),
        Message(role="user", content=current_text),
    ]
    case.patient_input.symptom = "血尿"
    case.conversation_state.clarification_evidence["severity"] = current_text
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
                "patient_source_text": current_text,
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


@pytest.mark.parametrize(
    ("text", "expected_concepts"),
    [
        ("我沒有症狀甲", set()),
        ("之前沒有症狀甲，但是現在有症狀甲", {"症狀甲"}),
        ("症狀甲沒有了，但目前症狀乙很明顯", {"症狀乙"}),
        ("我有症狀甲", {"症狀甲"}),
    ],
)
def test_synthetic_department_retrieval_requires_non_negated_occurrence(
    text,
    expected_concepts,
):
    records = [
        _record("測試甲科", "症狀甲", "official_a"),
        _record("測試乙科", "症狀乙", "official_b"),
    ]
    resolutions = [
        _resolution("測試甲科", 101),
        _resolution("測試乙科", 102),
    ]

    retrieved = retrieve_official_evidence(
        _case_with_grounded_text(text),
        records,
        resolutions,
    )

    assert {item["knowledge_concept"] for item in retrieved} == expected_concepts


def test_non_negated_helper_checks_every_occurrence():
    text = "之前沒有症狀甲，但是現在有症狀甲"

    assert contains_non_negated_keyword("我沒有症狀甲", "症狀甲") is False
    assert contains_non_negated_keyword(text, "症狀甲") is True


def test_ai_normalized_value_cannot_erase_grounded_source_negation():
    case = TriageCase(case_id="normalized-negation-bypass")
    case.history_records = [Message(role="user", content="我沒有胸痛")]
    case.semantic_extractions = [SemanticExtraction(
        field="accompanying_symptoms",
        normalized_value=["胸痛"],
        semantic_status="available",
        confidence=0.96,
        source_text="我沒有胸痛",
        extractor="ai",
    )]

    retrieved = retrieve_official_evidence(
        case,
        [_record("一般內科", "胸痛", "general_official")],
        [_resolution("一般內科", 1232)],
    )

    assert retrieved == []


def test_negated_synonym_normalization_cannot_create_positive_department_evidence():
    case = TriageCase(case_id="normalized-negated-synonym")
    case.history_records = [Message(role="user", content="我沒有胸口痛")]
    case.semantic_extractions = [SemanticExtraction(
        field="symptom",
        normalized_value="胸痛",
        semantic_status="available",
        confidence=0.96,
        source_text="我沒有胸口痛",
        extractor="ai",
    )]

    retrieved = retrieve_official_evidence(
        case,
        [_record("一般內科", "胸痛", "general_official")],
        [_resolution("一般內科", 1232)],
    )

    assert retrieved == []


def test_positive_synonym_normalization_remains_available():
    case = TriageCase(case_id="normalized-positive-synonym")
    case.history_records = [Message(role="user", content="我有胸口痛")]
    case.semantic_extractions = [SemanticExtraction(
        field="symptom",
        normalized_value="胸痛",
        semantic_status="available",
        confidence=0.96,
        source_text="我有胸口痛",
        extractor="ai",
    )]

    retrieved = retrieve_official_evidence(
        case,
        [_record("一般內科", "胸痛", "general_official")],
        [_resolution("一般內科", 1232)],
    )

    assert len(retrieved) == 1
    assert retrieved[0]["knowledge_concept"] == "胸痛"
    assert retrieved[0]["patient_source_text"] == "我有胸口痛"


@pytest.mark.parametrize(
    "source",
    [
        "我沒有胸口痛，但是現在有頭暈",
        "我沒有頭暈，但是現在有胸口痛",
    ],
)
def test_mixed_polarity_source_cannot_support_normalized_only_concept(source):
    case = TriageCase(case_id="normalized-mixed-polarity")
    case.history_records = [Message(role="user", content=source)]
    case.semantic_extractions = [SemanticExtraction(
        field="symptom",
        normalized_value="胸痛",
        semantic_status="available",
        confidence=0.96,
        source_text=source,
        extractor="ai",
    )]

    retrieved = retrieve_official_evidence(
        case,
        [_record("一般內科", "胸痛", "general_official")],
        [_resolution("一般內科", 1232)],
    )

    assert retrieved == []


def test_minimal_positive_synonym_span_supports_normalized_concept():
    case = TriageCase(case_id="normalized-minimal-positive-span")
    source = "現在有胸口痛"
    case.history_records = [Message(role="user", content=source)]
    case.semantic_extractions = [SemanticExtraction(
        field="symptom",
        normalized_value="胸痛",
        semantic_status="available",
        confidence=0.96,
        source_text=source,
        extractor="ai",
    )]

    retrieved = retrieve_official_evidence(
        case,
        [_record("一般內科", "胸痛", "general_official")],
        [_resolution("一般內科", 1232)],
    )

    assert len(retrieved) == 1
    assert retrieved[0]["knowledge_concept"] == "胸痛"
    assert retrieved[0]["patient_source_text"] == source


def test_literal_concept_uses_all_occurrences_and_keeps_current_positive_one():
    source = "之前沒有胸痛，但是現在有胸痛"

    retrieved = retrieve_official_evidence(
        _case_with_grounded_text(source),
        [_record("一般內科", "胸痛", "general_official")],
        [_resolution("一般內科", 1232)],
    )

    assert len(retrieved) == 1
    assert retrieved[0]["knowledge_concept"] == "胸痛"
    assert retrieved[0]["patient_source_text"] == source


def test_positive_normalized_interpretation_without_literal_canonical_term_survives():
    case = TriageCase(case_id="positive-semantic-normalization")
    case.history_records = [Message(role="user", content="症狀俗稱甲")]
    case.semantic_extractions = [SemanticExtraction(
        field="symptom",
        normalized_value="正式症狀甲",
        semantic_status="available",
        confidence=0.96,
        source_text="症狀俗稱甲",
        extractor="ai",
    )]

    retrieved = retrieve_official_evidence(
        case,
        [_record("測試甲科", "正式症狀甲", "official_a")],
        [_resolution("測試甲科", 101)],
    )

    assert len(retrieved) == 1
    assert retrieved[0]["knowledge_concept"] == "正式症狀甲"
    assert retrieved[0]["patient_source_text"] == "症狀俗稱甲"
