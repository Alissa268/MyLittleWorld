"""Taipei VGH symptom pages for skin, GI, chest and orthopedic complaints (added 2026-11-03)."""
import json

from app.schemas import Message, SemanticExtraction, TriageCase
from app.services import department_reasoning_service as reasoning
from app.services.department_knowledge import KNOWLEDGE_DIR, load_department_knowledge, resolve_department_names

_, RECORDS = load_department_knowledge()
ACTIVE = [
    dict(dept_id=record["canonical_dept_id"], parent_dept=record["canonical_parent_dept"],
         child_dept=record["canonical_child_dept"])
    for record in {item["canonical_dept_id"]: item for item in RECORDS}.values()
]
RESOLUTIONS = resolve_department_names(RECORDS, ACTIVE)
NAMES = {record["canonical_dept_id"]: record["department_name"] for record in RECORDS}
SUPPLEMENTS = sorted(KNOWLEDGE_DIR.glob("supplemental_guidance_20261103_*.json"))


def literal_departments(text):
    case = TriageCase(case_id="common-literal")
    case.history_records = [Message(role="user", content=text)]
    case.semantic_extractions = [SemanticExtraction(
        field="symptom", normalized_value=text, semantic_status="available", assertion="present",
        confidence=0.95, source_text=text, extractor="ai",
    )]
    return {NAMES[row["dept_id"]] for row in reasoning.retrieve_official_evidence(case, RECORDS, RESOLUTIONS)}


def test_supplements_cite_symptom_pages_and_skip_family_medicine():
    assert [path.stem.rsplit("_", 1)[1] for path in SUPPLEMENTS] == [
        "chest", "dermatology", "gastroenterology", "orthopedics"]
    for path in SUPPLEMENTS:
        data = json.loads(path.read_text(encoding="utf-8"))
        assert "mappings" not in data
        for source in data["sources"]:
            assert source["source_url"].startswith("https://www.vghtpe.gov.tw/vghtpe/Fpage.action?fid=")
        for record in data["records"]:
            assert record["evidence_type"] == "direct_routing_evidence"
            assert record["department_name"] != "家庭醫學科(一般門診/戒菸)"


def test_common_complaints_reach_the_listed_specialties():
    assert literal_departments("皮膚癢") >= {"皮膚科"}
    assert literal_departments("身上起蕁麻疹") == {"皮膚科", "過敏免疫風濕"}
    assert literal_departments("最近一直腹脹") == {"胃腸肝膽科"}
    assert literal_departments("胃灼熱") == {"胃腸肝膽科"}
    assert literal_departments("喘不過氣") == {"胸腔內科", "心臟內科"}
    assert literal_departments("背痛") == {"骨科", "復健醫學", "神經外科"}
    assert literal_departments("關節酸痛") == {"骨科", "復健醫學", "過敏免疫風濕"}
