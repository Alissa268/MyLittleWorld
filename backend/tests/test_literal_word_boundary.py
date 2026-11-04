"""A shorter official concept starting inside a longer word is not a literal match (「舌頭痛」 is not 「頭痛」)."""
from app.schemas import Message, SemanticExtraction, TriageCase
from app.services import department_reasoning_service as service
from app.services.department_knowledge import load_department_knowledge
from app.services.rule_engine import apply_semantic_extractions

_, RECORDS = load_department_knowledge()
RESOLUTIONS = [dict(status="resolved", canonical_dept_id=r["canonical_dept_id"], db_dept_id=r["canonical_dept_id"],
                    db_parent_dept=r["canonical_parent_dept"], db_child_dept=r["canonical_child_dept"])
               for r in RECORDS]


def concepts(text):
    case = TriageCase(case_id="word-boundary")
    case.history_records.append(Message(role="user", content=text))
    apply_semantic_extractions(case, [SemanticExtraction(
        field="symptom", normalized_value=text, source_text=text, semantic_status="available",
        assertion="present", confidence=.99, extractor="ai")], allow_red_flag_completion=False)
    return {row["knowledge_concept"] for row in service.retrieve_official_evidence(case, RECORDS, RESOLUTIONS)}


def test_body_part_words_do_not_hide_a_headache():
    for text in ("舌頭痛", "骨頭痛", "手指頭痛", "腳趾頭痛", "乳頭痛", "鼻頭痛", "喉頭痛"):
        assert "頭痛" not in concepts(text), text


def test_enclosing_words_do_not_match_a_different_concept():
    assert "經痛" not in concepts("坐骨神經痛")
    assert "坐骨神經痛" in concepts("坐骨神經痛")
    assert "背痛" not in concepts("手背痛")
    assert "肌炎" not in concepts("心肌炎")
    assert "囊腫" not in concepts("陰囊腫大")
    assert "頸痛" not in concepts("子宮頸痛")


def test_concepts_outside_enclosing_words_still_match():
    assert "頭痛" in concepts("額頭痛")
    assert "背痛" in concepts("上背痛")
    assert "經痛" in concepts("神經痛之外也有經痛")
    assert "囊腫" in concepts("陰囊囊腫")


def test_contains_concept():
    assert service.contains_concept("舌頭痛又頭痛", "頭痛")
    assert not service.contains_concept("舌頭痛", "頭痛")
    assert service.contains_concept("神經痛", "神經痛")
