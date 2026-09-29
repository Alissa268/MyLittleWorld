"""Read-only, source-validated department guidance; not connected to routing."""

from __future__ import annotations

import json
import re
import unicodedata
from datetime import date
from pathlib import Path
from urllib.parse import urlparse

KNOWLEDGE_DIR = Path(__file__).resolve().parents[2] / "knowledge"
_SOURCE_POLICY = {
    ("vghtpe", "symptom_department_guidance"): (1, {"wd.vghtpe.gov.tw"}),
    ("vghtpe", "doctor_specialty_keywords"): (2, {"www.vghtpe.gov.tw"}),
    ("vghtc", "symptom_query"): (3, {"www.vghtc.gov.tw"}),
}
_RECORD_FIELDS = (
    "department_name", "concept", "source_id", "source_hospital", "source_type",
    "source_title", "source_url", "source_priority", "retrieved_at",
    "evidence_text", "department_resolution", "fallback_for_vghtpe",
)
_SOURCE_FIELDS = (
    "source_id", "source_hospital", "source_type", "source_title", "source_url",
    "source_priority", "retrieved_at",
)


def normalize_concept(value: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", value)).casefold()


def load_department_knowledge(directory: Path = KNOWLEDGE_DIR) -> tuple[list[dict], list[dict]]:
    sources = json.loads((directory / "sources.json").read_text(encoding="utf-8"))["sources"]
    records = json.loads((directory / "vghtpe_department_guidance.json").read_text(encoding="utf-8"))["records"]
    validate_department_knowledge(sources, records)
    return sources, records


def validate_department_knowledge(sources: list[dict], records: list[dict]) -> None:
    if not isinstance(sources, list) or not sources or not isinstance(records, list) or not records:
        raise ValueError("knowledge sources and records must be nonempty lists")

    source_by_id = {}
    source_urls = set()
    for source in sources:
        if not isinstance(source, dict) or not _has_fields(source, _SOURCE_FIELDS):
            raise ValueError("source metadata is incomplete")
        source_id = source["source_id"]
        if not isinstance(source_id, str) or not re.fullmatch(r"[a-z][a-z0-9_]+", source_id):
            raise ValueError("invalid source_id")
        if source_id in source_by_id:
            raise ValueError("duplicate source_id")
        _validate_source_policy(source)
        url_key = source["source_url"].strip().casefold()
        if url_key in source_urls:
            raise ValueError("duplicate source URL")
        source_urls.add(url_key)
        _validate_date(source["retrieved_at"])
        source_by_id[source_id] = source

    seen = set()
    for record in records:
        if not isinstance(record, dict) or not _has_fields(record, _RECORD_FIELDS):
            raise ValueError("medical record metadata is incomplete")
        if not isinstance(record["source_id"], str):
            raise ValueError("medical record source_id must be a string")
        source = source_by_id.get(record["source_id"])
        if source is None:
            raise ValueError("medical record source_id is not registered")
        if any(record[field] != source[field] for field in _SOURCE_FIELDS):
            raise ValueError("medical record provenance differs from source registry")
        department = record["department_name"]
        concept = record["concept"]
        evidence = record["evidence_text"]
        if not all(isinstance(value, str) and value.strip() for value in (department, concept, evidence)):
            raise ValueError("department, concept and evidence_text are required")
        if len(evidence) > 120 or normalize_concept(concept) not in normalize_concept(evidence):
            raise ValueError("evidence_text must be short and contain the concept")
        if record["department_resolution"] != "unresolved":
            raise ValueError("department mapping requires an independently verified SQL row")
        if type(record["fallback_for_vghtpe"]) is not bool:
            raise ValueError("fallback_for_vghtpe must be boolean")
        if record["fallback_for_vghtpe"] != (record["source_hospital"] == "vghtc"):
            raise ValueError("Taichung records must be marked as fallback")
        key = (record["source_id"], normalize_concept(department), normalize_concept(concept))
        if key in seen:
            raise ValueError("duplicate normalized source/department/concept")
        seen.add(key)


def lookup_concept(concept: str, records: list[dict]) -> list[dict]:
    """Keep each department's best evidence for an exact concept, not a chosen department."""
    key = normalize_concept(concept)
    if not key:
        return []
    matches = [record for record in records if normalize_concept(record["concept"]) == key]
    best_by_department = {}
    for record in sorted(matches, key=lambda item: (item["source_priority"], item["source_id"])):
        best_by_department.setdefault(normalize_concept(record["department_name"]), record)
    return sorted(best_by_department.values(), key=lambda record: record["department_name"])


def canonical_department_id(value: object) -> int | None:
    """Accept only positive integer IDs or ASCII decimal strings from the reference API."""
    if type(value) is int:
        return value if value > 0 else None
    if isinstance(value, str) and re.fullmatch(r"[0-9]+", value):
        try:
            parsed = int(value)
        except ValueError:
            return None
        return parsed if parsed > 0 else None
    return None


def resolve_department_names(records: list[dict], db_departments: list[dict]) -> list[dict]:
    """Build separate, exact-only live DB resolution metadata; never alter evidence records."""
    resolutions = []
    for name in sorted({record["department_name"] for record in records}):
        matches = [row for row in db_departments if _db_child_name(row) == name]
        entry = {"knowledge_department_name": name}
        if not matches:
            entry.update(status="unresolved", reason="no_exact_live_db_child_name")
        elif len(matches) != 1:
            entry.update(status="unresolved", reason="duplicate_exact_live_db_child_name")
        else:
            row = matches[0]
            dept_id = canonical_department_id(row.get("dept_id"))
            parent = row.get("parentDept", row.get("parent_dept"))
            if dept_id is None:
                entry.update(status="unresolved", reason="invalid_live_db_dept_id")
            elif not isinstance(parent, str) or not parent.strip():
                entry.update(status="unresolved", reason="missing_live_db_parent_name")
            else:
                entry.update(
                    status="resolved", db_dept_id=dept_id, db_parent_dept=parent,
                    db_child_dept=name, resolution_method="exact_live_db_name",
                )
        resolutions.append(entry)
    return resolutions


def exact_db_department_ids(records: list[dict], db_departments: list[dict]) -> dict[str, int | None]:
    """Compatibility helper returning only IDs from exact-only resolution metadata."""
    return {
        entry["knowledge_department_name"]: entry.get("db_dept_id")
        for entry in resolve_department_names(records, db_departments)
    }


def _db_child_name(row: dict) -> object:
    return row.get("childDept", row.get("child_dept"))


def _has_fields(item: dict, fields: tuple[str, ...]) -> bool:
    return all(field in item and item[field] is not None for field in fields)


def _validate_source_policy(source: dict) -> None:
    if not isinstance(source["source_hospital"], str) or not isinstance(source["source_type"], str):
        raise ValueError("unknown source hospital or source type")
    policy = _SOURCE_POLICY.get((source["source_hospital"], source["source_type"]))
    if policy is None:
        raise ValueError("unknown source hospital or source type")
    priority, hosts = policy
    url = source["source_url"]
    parsed = urlparse(url) if isinstance(url, str) else None
    if not parsed or parsed.scheme != "https" or parsed.hostname not in hosts or not parsed.path:
        raise ValueError("source URL must be an official HTTPS page")
    if type(source["source_priority"]) is not int or source["source_priority"] != priority:
        raise ValueError("source priority violates hospital policy")
    if not isinstance(source["source_title"], str) or not source["source_title"].strip():
        raise ValueError("source title is required")


def _validate_date(value: object) -> None:
    if not isinstance(value, str):
        raise ValueError("retrieved_at must be an ISO date")
    try:
        date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("retrieved_at must be an ISO date") from exc
