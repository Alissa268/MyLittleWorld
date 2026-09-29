from copy import deepcopy
import json
from pathlib import Path

import pytest

from app.services.department_knowledge import (
    canonical_department_id,
    exact_db_department_ids,
    load_department_knowledge,
    lookup_concept,
    resolve_department_names,
    validate_department_knowledge,
)


@pytest.fixture
def knowledge():
    return load_department_knowledge()


def test_official_kb_records_have_registered_sources_and_short_evidence(knowledge):
    sources, records = knowledge
    registered = {source["source_id"] for source in sources}
    assert len(registered) == len(sources)
    assert records
    assert all(record["source_id"] in registered for record in records)
    assert all(record["source_hospital"] == "vghtpe" for record in records)
    assert all(record["department_resolution"] == "unresolved" for record in records)
    assert all(record["source_url"].startswith("https://") for record in records)


def test_missing_or_unknown_source_is_rejected(knowledge):
    sources, records = deepcopy(knowledge)
    records[0]["source_id"] = "legacy_dept_keywords"
    with pytest.raises(ValueError, match="not registered"):
        validate_department_knowledge(sources, records)

    sources, records = deepcopy(knowledge)
    records[0].pop("source_id")
    with pytest.raises(ValueError, match="incomplete"):
        validate_department_knowledge(sources, records)

    sources, records = deepcopy(knowledge)
    records[0]["source_id"] = ["not-a-source-id"]
    with pytest.raises(ValueError, match="must be a string"):
        validate_department_knowledge(sources, records)


def test_unknown_hospital_and_missing_official_url_are_rejected(knowledge):
    sources, records = deepcopy(knowledge)
    sources[0]["source_hospital"] = "unknown"
    with pytest.raises(ValueError, match="unknown source hospital"):
        validate_department_knowledge(sources, records)

    sources, records = deepcopy(knowledge)
    sources[0]["source_hospital"] = ["unknown"]
    with pytest.raises(ValueError, match="unknown source hospital"):
        validate_department_knowledge(sources, records)

    sources, records = deepcopy(knowledge)
    sources[0]["source_url"] = ""
    records[0]["source_url"] = ""
    with pytest.raises(ValueError, match="official HTTPS"):
        validate_department_knowledge(sources, records)


def test_provenance_mismatch_is_rejected(knowledge):
    sources, records = deepcopy(knowledge)
    records[0]["source_priority"] = 3
    with pytest.raises(ValueError, match="provenance"):
        validate_department_knowledge(sources, records)


def test_duplicate_source_and_normalized_concept_are_rejected(knowledge):
    sources, records = deepcopy(knowledge)
    sources.append(deepcopy(sources[0]))
    with pytest.raises(ValueError, match="duplicate source_id"):
        validate_department_knowledge(sources, records)

    sources, records = deepcopy(knowledge)
    duplicate_source = deepcopy(sources[0])
    duplicate_source["source_id"] = "vghtpe_duplicate_url"
    sources.append(duplicate_source)
    with pytest.raises(ValueError, match="duplicate source URL"):
        validate_department_knowledge(sources, records)

    sources, records = deepcopy(knowledge)
    duplicate = deepcopy(records[0])
    duplicate["concept"] = "  發 燒  "
    duplicate["evidence_text"] = "一般內科：發燒"
    records.append(duplicate)
    with pytest.raises(ValueError, match="duplicate normalized"):
        validate_department_knowledge(sources, records)


def test_taichung_fallback_requires_marker_and_cannot_override_taipei(knowledge):
    sources, records = deepcopy(knowledge)
    taichung = next(source for source in sources if source["source_hospital"] == "vghtc")
    fallback = deepcopy(records[0])
    fallback.update({field: taichung[field] for field in (
        "source_id", "source_hospital", "source_type", "source_title", "source_url",
        "source_priority", "retrieved_at",
    )})
    fallback["fallback_for_vghtpe"] = False
    records.append(fallback)
    with pytest.raises(ValueError, match="fallback"):
        validate_department_knowledge(sources, records)

    fallback["fallback_for_vghtpe"] = True
    validate_department_knowledge(sources, records)
    assert all(item["source_hospital"] == "vghtpe" for item in lookup_concept("發燒", records))

    fallback["concept"] = "未收錄症狀"
    fallback["evidence_text"] = "一般內科：未收錄症狀"
    validate_department_knowledge(sources, records)
    assert lookup_concept("未收錄症狀", records) == [fallback]


def test_priority_one_precedes_doctor_keywords_for_same_concept(knowledge):
    sources, records = deepcopy(knowledge)
    doctor_source = next(source for source in sources if source["source_priority"] == 2)
    lower = deepcopy(records[0])
    lower.update({field: doctor_source[field] for field in (
        "source_id", "source_hospital", "source_type", "source_title", "source_url",
        "source_priority", "retrieved_at",
    )})
    records.append(lower)
    validate_department_knowledge(sources, records)
    assert all(item["source_priority"] == 1 for item in lookup_concept("發燒", records))


def test_different_departments_survive_different_official_priorities(knowledge):
    sources, records = deepcopy(knowledge)
    doctor_source = next(source for source in sources if source["source_priority"] == 2)
    second_department = deepcopy(records[0])
    second_department.update({field: doctor_source[field] for field in (
        "source_id", "source_hospital", "source_type", "source_title", "source_url",
        "source_priority", "retrieved_at",
    )})
    second_department["department_name"] = "耳鼻喉頭頸部"
    second_department["evidence_text"] = "耳鼻喉頭頸部：發燒"
    records.append(second_department)
    validate_department_knowledge(sources, records)
    found = lookup_concept("發燒", records)
    assert {item["department_name"] for item in found} == {"一般內科", "耳鼻喉頭頸部"}
    assert {item["source_priority"] for item in found} == {1, 2}


@pytest.mark.parametrize(("raw", "expected"), [
    (123, 123), ("123", 123), ("00123", 123),
    (True, None), (False, None), (None, None), ("", None),
    ("abc", None), ("1.5", None), (" 123", None),
    (1.5, None), (0, None), (-1, None), ("0", None), ("-1", None),
    ("1" * 5000, None),
])
def test_canonical_department_id_accepts_only_positive_decimal_ids(raw, expected):
    assert canonical_department_id(raw) == expected


def test_no_near_name_or_duplicate_sql_row_is_guessed(knowledge):
    _, records = knowledge
    ids = exact_db_department_ids(records, [
        {"dept_id": 5, "parent_dept": "內科", "child_dept": "一般內科"},
        {"dept_id": 7, "parent_dept": "內科", "child_dept": "心臟內科"},
        {"dept_id": 8, "parent_dept": "內科", "child_dept": "胃腸肝膽科"},
        {"dept_id": 9, "parent_dept": "內科", "child_dept": "胃腸肝膽科"},
    ])
    assert ids["一般內科"] == 5
    assert ids["心臟科"] is None
    assert ids["胃腸肝膽科"] is None


def test_live_310_inventory_resolves_only_exact_unique_kb_names(knowledge):
    _, records = knowledge
    snapshot = Path(__file__).resolve().parents[2] / "docs" / "PHASE3_310_DEPARTMENT_INVENTORY.json"
    rows = json.loads(snapshot.read_text(encoding="utf-8"))["departments"]
    assert len(rows) == 133
    resolutions = {entry["knowledge_department_name"]: entry for entry in resolve_department_names(records, rows)}
    assert {name for name, entry in resolutions.items() if entry["status"] == "resolved"} == {
        "一般內科", "感染科", "胃腸肝膽科", "腎臟科", "血液腫瘤科",
    }
    assert resolutions["一般內科"] == {
        "knowledge_department_name": "一般內科", "status": "resolved",
        "db_dept_id": 1232, "db_parent_dept": "內科", "db_child_dept": "一般內科",
        "resolution_method": "exact_live_db_name",
    }
    assert resolutions["心臟科"] == {
        "knowledge_department_name": "心臟科", "status": "unresolved",
        "reason": "no_exact_live_db_child_name",
    }
    assert sum(entry["status"] == "unresolved" for entry in resolutions.values()) == 5
    assert exact_db_department_ids(records, rows)["一般內科"] == 1232


def test_duplicate_api_style_name_remains_unresolved(knowledge):
    _, records = knowledge
    resolutions = resolve_department_names(records, [
        {"dept_id": "1232", "parentDept": "內科", "childDept": "一般內科"},
        {"dept_id": "1232", "parentDept": "內科", "childDept": "一般內科"},
    ])
    match = next(entry for entry in resolutions if entry["knowledge_department_name"] == "一般內科")
    assert match == {
        "knowledge_department_name": "一般內科", "status": "unresolved",
        "reason": "duplicate_exact_live_db_child_name",
    }


def test_unverified_legacy_term_cannot_enter_as_unofficial_source(knowledge):
    sources, records = deepcopy(knowledge)
    invented = deepcopy(records[0])
    invented["concept"] = "舊詞庫未查證詞"
    invented["evidence_text"] = "舊詞庫未查證詞"
    invented["source_id"] = "AILogic/dept_keywords.py"
    records.append(invented)
    with pytest.raises(ValueError):
        validate_department_knowledge(sources, records)
