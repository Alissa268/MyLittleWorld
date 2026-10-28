"""Official-KB-grounded department candidates with live-DB and source validation."""

from __future__ import annotations

import json
import logging
import math
import re

from app.config import get_settings
from app.db import fetch_active_departments
from app.schemas import (
    CandidateDepartment, CandidateDepartmentEvidence, ClueDecisionRecord, DepartmentResult, SemanticOfficialLink,
    TriageCase,
)
from app.services import department_clues as clues
from app.services.ai_service import complete_runtime_json
from app.services.confidence_scoring import ACCEPT_THRESHOLD
from app.services.conversation_service import safety_screen_resolved
from app.services.department_knowledge import (
    canonical_department_id, load_department_knowledge, lookup_concept, normalize_concept, resolve_department_names,
)
from app.services.department_preference_service import resolve_requested_department
from app.services.field_acceptance import ai_normalized_value_valid

logger = logging.getLogger(__name__)
_INTENT = re.compile(r"[a-z][a-z0-9_]{2,63}\Z")
_ASSERTED_MEDICAL_FIELDS = {"symptom", "accompanying_symptoms"}
_CANDIDATE_CONTEXT_FIELDS = {
    "symptom", "accompanying_symptoms", "body_part", "duration", "severity", "onset",
}
_EVIDENCE_ASSERTIONS = {"present", "absent", "uncertain"}
_SEMANTIC_STATUSES = {"available", "unavailable", "unknown", "partial", "ambiguous"}


def _user_texts(case: TriageCase) -> list[str]:
    return [message.content for message in case.history_records if message.role == "user"]


def validated_semantic_evidence_history(case: TriageCase) -> list[dict]:
    """Return validated evidence in append order without interpreting raw text."""
    history = _user_texts(case)
    evidence: list[dict] = []
    for item in case.semantic_extractions:
        source = item.source_text.strip()
        if (
            item.field not in _CANDIDATE_CONTEXT_FIELDS
            or item.semantic_status not in _SEMANTIC_STATUSES
            or type(item.confidence) not in {int, float}
            or not math.isfinite(item.confidence)
            or item.confidence < ACCEPT_THRESHOLD
            or not source
            or not any(source in text for text in history)
            or not ai_normalized_value_valid(
                item.field,
                item.normalized_value,
                item.semantic_status,
                source,
            )
        ):
            continue
        if item.field in _ASSERTED_MEDICAL_FIELDS:
            if item.assertion not in _EVIDENCE_ASSERTIONS:
                continue
            concepts = (
                [item.normalized_value]
                if isinstance(item.normalized_value, str)
                else item.normalized_value
            )
            for concept in concepts if isinstance(concepts, list) else []:
                if not isinstance(concept, str) or not concept.strip():
                    continue
                evidence.append({
                    "field": item.field,
                    "normalized_value": concept.strip(),
                    "source_text": source,
                    "assertion": item.assertion,
                    "semantic_status": item.semantic_status,
                    "confidence": float(item.confidence),
                })
        elif item.semantic_status in {"available", "partial"}:
            evidence.append({
                "field": item.field,
                "normalized_value": item.normalized_value,
                "source_text": source,
                "assertion": None,
                "semantic_status": item.semantic_status,
                "confidence": float(item.confidence),
            })
    return evidence


def effective_semantic_evidence(case: TriageCase) -> list[dict]:
    """Collapse validated evidence history into the latest current fact per key."""
    current: dict[tuple[str, ...], tuple[int, dict]] = {}
    for position, evidence in enumerate(validated_semantic_evidence_history(case)):
        field = evidence["field"]
        if field in _ASSERTED_MEDICAL_FIELDS:
            key = ("concept", field, normalize_concept(str(evidence["normalized_value"])))
        else:
            key = ("field", field)
        current[key] = (position, evidence)
    return [evidence for _, evidence in sorted(current.values(), key=lambda item: item[0])]


def accepted_semantic_evidence(case: TriageCase) -> list[dict]:
    """Compatibility name for current effective structured semantic evidence."""
    return effective_semantic_evidence(case)


def adjacent_present_spans(user_texts: list[str], evidence: list[dict]) -> list[tuple[str, tuple[str, ...]]]:
    """Join validated spans that touch in one message, using only AI-grounded source_text.

    Parts are present symptom spans and body_part spans; absent/uncertain spans never take
    part. A span found more than once in a message has no certain position there, so it is
    skipped for that message. Each result is ``(verbatim fragment, part sources)`` and
    contains at least one present symptom span.
    """
    symptom_sources = _present_sources(evidence)
    part_sources = symptom_sources | {
        item["source_text"] for item in evidence
        if item["field"] == "body_part" and item["semantic_status"] in {"available", "partial"}
    }
    merged: dict[str, tuple[str, ...]] = {}
    for text in user_texts:
        starts: dict[int, list[str]] = {}
        for source in part_sources:
            if text.count(source) == 1:
                starts.setdefault(text.index(source), []).append(source)

        def extend(end: int, chain: tuple[str, ...]) -> None:
            for source in sorted(starts.get(end, [])):
                longer = chain + (source,)
                if any(part in symptom_sources for part in longer):
                    merged.setdefault(text[end - sum(map(len, chain)):end + len(source)], longer)
                extend(end + len(source), longer)

        for start, sources in sorted(starts.items()):
            for source in sorted(sources):
                extend(start + len(source), (source,))
    return list(merged.items())


def _official_row(resolution: dict, source: str, record: dict) -> dict:
    return {
        "dept_id": resolution["db_dept_id"],
        "parentDept": resolution["db_parent_dept"],
        "childDept": resolution["db_child_dept"],
        "patient_source_text": source,
        "knowledge_source_id": record["source_id"],
        "knowledge_concept": record["concept"],
        "knowledge_evidence_text": record["evidence_text"],
        "source_priority": record["source_priority"],
    }


def _mark_span_parts(rows: list[dict], evidence: list[dict], case: TriageCase) -> list[dict]:
    """Tag rows anchored on a joined fragment with its parts so scoring counts it once."""
    plain = _present_sources(evidence)
    spans = {fragment: parts for fragment, parts in adjacent_present_spans(_user_texts(case), evidence)
             if fragment not in plain}
    for row in rows:
        if row["patient_source_text"] in spans:
            row["patient_source_parts"] = list(spans[row["patient_source_text"]])
    return rows


def retrieve_official_evidence(case: TriageCase, records: list[dict], resolutions: list[dict]) -> list[dict]:
    """Match official concepts only against validated present semantic evidence."""
    resolved = {
        entry["canonical_dept_id"]: entry
        for entry in resolutions if entry["status"] == "resolved"
    }
    concepts = {record["concept"] for record in records}
    found: dict[tuple[int, str, str, str], dict] = {}
    evidence_items = accepted_semantic_evidence(case)
    surfaces_by_source: dict[str, set[str]] = {}
    for evidence in evidence_items:
        if (
            evidence["assertion"] != "present"
            or evidence["semantic_status"] not in {"available", "partial"}
        ):
            continue
        source = evidence["source_text"]
        retrieval_surfaces = {
            normalize_concept(str(value))
            for value in (evidence["normalized_value"], source)
            if isinstance(value, str) and value.strip()
        }
        surfaces_by_source.setdefault(source, set()).update(retrieval_surfaces)
        for concept in concepts:
            normalized_concept = normalize_concept(concept)
            if not any(normalized_concept in surface for surface in retrieval_surfaces):
                continue
            for record in lookup_concept(concept, records):
                resolution = resolved.get(record["canonical_dept_id"])
                if resolution is None:
                    continue
                key = (resolution["db_dept_id"], source, record["source_id"], record["concept"])
                found[key] = _official_row(resolution, source, record)
    # A concept spanning touching present spans (e.g. two halves of one compound phrase)
    # is matched against the verbatim joined fragment. It must need every part: a concept
    # that a shorter chain or a single part already covers is not re-anchored, so one
    # patient phrase never counts twice.
    contradicted = _contradicted_concepts(evidence_items)
    for fragment, parts in adjacent_present_spans(_user_texts(case), evidence_items):
        surface = normalize_concept(fragment)
        shorter = {normalize_concept(fragment[len(parts[0]):]), normalize_concept(fragment[:-len(parts[-1])])}
        shorter |= {value for part in parts for value in surfaces_by_source.get(part, set())}
        for concept in concepts:
            normalized_concept = normalize_concept(concept)
            if (normalized_concept not in surface or normalized_concept in contradicted
                    or any(normalized_concept in value for value in shorter)):
                continue
            for record in lookup_concept(concept, records):
                resolution = resolved.get(record["canonical_dept_id"])
                if resolution is None:
                    continue
                key = (resolution["db_dept_id"], fragment, record["source_id"], record["concept"])
                found[key] = _official_row(resolution, fragment, record)
    return _mark_span_parts(list(found.values()), evidence_items, case)


async def retrieve_semantic_official_evidence(
    case: TriageCase, records: list[dict], resolutions: list[dict],
) -> list[dict]:
    """Bridge wording to the existing official catalog, never invent routing data."""
    evidence = accepted_semantic_evidence(case)
    combined = [fragment for fragment, _ in adjacent_present_spans(_user_texts(case), evidence)]
    present_sources = _present_sources(evidence) | set(combined)
    # Only sent when present, so prompts for single-span evidence stay unchanged.
    combined_payload = dict(combined_present_source_texts=combined) if combined else {}
    contradicted = _contradicted_concepts(evidence)
    resolved = {item["canonical_dept_id"]: item for item in resolutions if item["status"] == "resolved"}
    catalog = sorted({record["concept"] for record in records if record["canonical_dept_id"] in resolved})
    if not present_sources or not catalog:
        return []
    prompt = (
        "你只做官方知識詞彙的語意檢索，不決定科別、診斷或流程。"
        "依 accepted_semantic_evidence 中的症狀、部位、伴隨情況與否定證據，"
        "找出 official_concepts 中語意相符的症狀概念。症狀與部位可能分開儲存，應一起理解。"
        "只對應患者已明確表達的症狀，不可從症狀推測疾病或把未提及的症狀補上；"
        "可使用部位的解剖學上位概念，但不得增加疼痛性質、腫脹機轉或病因。"
        "例如疼痛不等於酸痛，腫脹不等於已確認水腫；不能僅因詞彙相近就加入這些新性質。"
        "優先保留同一部位與同一症狀的概念，而非轉向其他器官或疾病。"
        "不得將 absent、uncertain 或只有部位的資訊當成陽性症狀。"
        "無可靠對應就回空 matches。最多 5 筆，不能自創 concept。"
        "patient_source_text 必須逐字取自 present 症狀 evidence 的 source_text，"
        + ("或取自 combined_present_source_texts（多段在原文中完全相接的 present evidence 合成的整段原文），"
           "原話本來就連在一起時應以整段當錨點；" if combined else "") +
        "knowledge_concept 必須逐字取自 official_concepts，confidence 為 0 到 1。"
        "只回 JSON：{\"matches\":[{\"patient_source_text\":\"...\","
        "\"knowledge_concept\":\"...\",\"confidence\":0.9}]}\n"
        + json.dumps(dict(accepted_semantic_evidence=evidence, official_concepts=catalog, **combined_payload),
                     ensure_ascii=False)
    )
    try:
        proposal = json.loads((await complete_runtime_json(prompt, purpose="department_semantic_retrieval")).strip())
    except Exception as exc:
        logger.warning("semantic retrieval unavailable case_id=%s error_type=%s", case.case_id, type(exc).__name__)
        return []
    if not isinstance(proposal, dict) or set(proposal) != {"matches"} or not isinstance(proposal["matches"], list):
        return []
    if len(proposal["matches"]) > 5:
        return []
    found = {}
    for match in proposal["matches"]:
        if not isinstance(match, dict) or set(match) != {"patient_source_text", "knowledge_concept", "confidence"}:
            continue
        source, concept, confidence = (match.get(key) for key in ("patient_source_text", "knowledge_concept", "confidence"))
        if (not isinstance(source, str) or source not in present_sources
                or not isinstance(concept, str) or concept not in catalog
                or type(confidence) not in {int, float} or not math.isfinite(confidence)
                or not ACCEPT_THRESHOLD <= confidence <= 1
                or (source in combined and normalize_concept(concept) in contradicted)):
            continue
        for record in lookup_concept(concept, records):
            row = resolved.get(record["canonical_dept_id"])
            if row is None:
                continue
            key = (row["db_dept_id"], source, record["source_id"], record["concept"])
            found[key] = dict(
                dept_id=row["db_dept_id"], parentDept=row["db_parent_dept"], childDept=row["db_child_dept"],
                patient_source_text=source, knowledge_source_id=record["source_id"],
                knowledge_concept=record["concept"], knowledge_evidence_text=record["evidence_text"],
                source_priority=record["source_priority"],
            )
    if not found:
        return []
    # A second entailment pass can only remove proposed links, never introduce
    # another symptom, source, concept or department.
    links = [dict(patient_source_text=source, knowledge_concept=concept)
             for source, concept in sorted({(item["patient_source_text"], item["knowledge_concept"])
                                            for item in found.values()})]
    audit_prompt = (
        "審核語意檢索是否忠於患者已接受的 evidence。只保留與原文相同含義的 proposed_matches。"
        "可以合併症狀與解剖部位，但不得加入原文沒有的性質、病因或診斷；"
        "patient_source_text 是症狀的原文錨點，不要求這個片段單獨包含部位；"
        + ("錨點若列在 combined_present_source_texts，是多段相接 present evidence 合成的整段原文，"
           "應以整段原話判斷，而非只看其中一段；" if combined else "") +
        "必須一起參考 evidence 的 body_part，不能因為錨點只有『疼痛』而忽略已提供的疼痛部位。"
        "尤其『腫脹』不能自行當成已確認的『水腫』，『疼痛』不能自行加上『酸』或『麻』。"
        "不得因為概念可能與症狀有醫學關聯就保留，必須是這段原話實際表達的症狀。"
        "請逐筆判斷並解釋，不確定則 supported=false。不可新增或改寫連結。只回 JSON {\"judgments\":["
        "{\"patient_source_text\":\"...\",\"knowledge_concept\":\"...\",\"supported\":true,\"reason\":\"依據\"}]}。\n"
        + json.dumps(dict(accepted_semantic_evidence=evidence, proposed_matches=links, **combined_payload),
                     ensure_ascii=False)
    )
    try:
        audit = json.loads((await complete_runtime_json(audit_prompt, purpose="department_retrieval_entailment")).strip())
        if not isinstance(audit, dict) or set(audit) != {"judgments"} or not isinstance(audit["judgments"], list):
            return []
        original = {(item["patient_source_text"], item["knowledge_concept"]) for item in links}
        retained = set()
        for item in audit["judgments"]:
            if not isinstance(item, dict):
                continue
            source, concept = item.get("patient_source_text"), item.get("knowledge_concept")
            if (isinstance(source, str) and isinstance(concept, str) and (source, concept) in original
                    and item.get("supported") is True and isinstance(item.get("reason"), str)
                    and item["reason"].strip()):
                retained.add((source, concept))
        return _mark_span_parts(
            [item for item in found.values() if (item["patient_source_text"], item["knowledge_concept"]) in retained],
            evidence, case,
        )
    except Exception as exc:
        logger.warning("retrieval entailment unavailable case_id=%s error_type=%s", case.case_id, type(exc).__name__)
        return []


_MAX_SEMANTIC_LINKS = 50


def _present_sources(evidence: list[dict]) -> set[str]:
    return {
        item["source_text"] for item in evidence
        if item["field"] in _ASSERTED_MEDICAL_FIELDS and item["assertion"] == "present"
        and item["semantic_status"] in {"available", "partial"}
    }


def retained_semantic_official_evidence(
    case: TriageCase, records: list[dict], resolutions: list[dict],
) -> list[dict]:
    """Rebuild earlier audited links that current structured evidence still supports.

    A link survives only while its source is current present evidence and no current
    absent/uncertain evidence names the same official concept; stale links are pruned.
    """
    state = case.conversation_state
    evidence = accepted_semantic_evidence(case)
    present_sources = _present_sources(evidence) | {
        fragment for fragment, _ in adjacent_present_spans(_user_texts(case), evidence)
    }
    contradicted = _contradicted_concepts(evidence)
    resolved = {item["canonical_dept_id"]: item for item in resolutions if item["status"] == "resolved"}
    kept, found = [], {}
    for link in state.semantic_official_links:
        if (link.patient_source_text not in present_sources
                or normalize_concept(link.knowledge_concept) in contradicted):
            continue
        rows = [(record, resolved[record["canonical_dept_id"]])
                for record in lookup_concept(link.knowledge_concept, records)
                if record["canonical_dept_id"] in resolved]
        if not rows:
            continue
        kept.append(link)
        for record, row in rows:
            key = (row["db_dept_id"], link.patient_source_text, record["source_id"], record["concept"])
            found[key] = dict(
                dept_id=row["db_dept_id"], parentDept=row["db_parent_dept"], childDept=row["db_child_dept"],
                patient_source_text=link.patient_source_text, knowledge_source_id=record["source_id"],
                knowledge_concept=record["concept"], knowledge_evidence_text=record["evidence_text"],
                source_priority=record["source_priority"],
            )
    state.semantic_official_links = kept
    return _mark_span_parts(list(found.values()), evidence, case)


def _contradicted_concepts(evidence: list[dict]) -> set[str]:
    return {
        normalize_concept(str(item["normalized_value"])) for item in evidence
        if item["field"] in _ASSERTED_MEDICAL_FIELDS and item["assertion"] in {"absent", "uncertain"}
    }


def _clue_priority(state, retrieved: list[dict], candidates: set[int], records: list[dict] | None) -> dict:
    """Target priority from scores, then the validated convergence candidates' order."""
    return clues.department_priority(
        clues.department_scores(retrieved, candidates, state.clue_concepts_asked, records),
        [item.dept_id for item in state.candidate_departments],
        {get_settings().family_medicine_dept_id},
    )


def store_clue_offer(case: TriageCase, records: list[dict], retrieved: list[dict]) -> list[clues.ClueConcept]:
    """Persist the Backend-ranked askable clues of all retrieved candidates; return every askable clue.

    Family medicine stays a candidate but is never a clue target; ranking reads the
    validated convergence candidates, so call this after they are stored.
    """
    state = case.conversation_state
    settings = get_settings()
    candidates = clues.candidate_dept_ids(retrieved)
    mentioned = clues.mentioned_concepts(retrieved, accepted_semantic_evidence(case))
    askable = clues.clue_concepts(records, candidates, mentioned, state.clue_concepts_asked,
                                  {settings.family_medicine_dept_id})
    state.department_clue_candidates = sorted(candidates)
    state.department_clue_offer = clues.clue_offer(clues.rank_clue_concepts(
        askable, settings.clue_planner_concept_limit, _clue_priority(state, retrieved, candidates, records),
    ))
    return askable


def _live_rows(active: list[dict], dept_id: int) -> list[dict]:
    return [row for row in active if canonical_department_id(row.get("dept_id")) == dept_id]


def _live_department(active: list[dict], dept_id: int | None, confidence: float, reason: str) -> DepartmentResult | None:
    """Adopt a decided department only when exactly one live Department row has its id."""
    rows = _live_rows(active, dept_id) if dept_id is not None else []
    if len(rows) != 1:
        logger.warning("clue decision department not adopted dept_id=%s live_rows=%s", dept_id, len(rows))
        return None
    row = rows[0]
    return DepartmentResult(
        dept_id=canonical_department_id(row["dept_id"]), parentDept=row["parent_dept"], childDept=row["child_dept"],
        confidence=confidence, reason=[reason],
    )


def apply_clue_decision(
    case: TriageCase, askable: list[clues.ClueConcept], retrieved: list[dict], active: list[dict],
    records: list[dict] | None = None,
) -> None:
    """Run decide() once safety is resolved; every adopted decision still waits for user confirmation.

    Not run while a clue question awaits its answer or the patient asked for an explanation,
    unless the convergence AI already resolved one candidate. Clue answers are judged by the
    current official holders of each concept (``records``) and the current candidates.
    """
    state = case.conversation_state
    if not safety_screen_resolved(case):
        return
    ai_resolved = (case.department_result.dept_id
                   if state.department_status == "resolved" and case.department_result else None)
    if ai_resolved is None and (clues.pending_clue_index(state) is not None
                                or state.last_dialogue_act == "request_explanation"):
        return
    settings = get_settings()
    candidates = set(state.department_clue_candidates)
    decision = clues.decide(
        ai_resolved_dept_id=ai_resolved, candidates=candidates,
        scores=clues.department_scores(retrieved, candidates, state.clue_concepts_asked, records),
        askable=askable, questions_asked=state.clue_questions_asked, tiebreak_used=state.clue_tiebreak_used,
        user_declined=False,
        clue_confirmed_dept_ids=clues.clue_confirmed_departments(state.clue_concepts_asked, candidates, records),
        question_limit=settings.clue_question_limit, non_target_dept_ids={settings.family_medicine_dept_id},
        ai_order=[item.dept_id for item in state.candidate_departments],
    )
    kind, dept_id, result = decision.kind, decision.dept_id, None
    if kind == "decide" and dept_id == ai_resolved:
        result = case.department_result
    elif kind == "decide":
        confidence = next((item.confidence for item in state.candidate_departments if item.dept_id == dept_id), 0.0)
        result = _live_department(active, dept_id, confidence, "依線索題回答與官方科別證據決定")
    elif kind == "family_medicine":
        result = _live_department(active, settings.family_medicine_dept_id, 0.0, "目前症狀無法明確區分科別")
        if result is None:
            kind = "family_medicine_unavailable"
        dept_id = result.dept_id if result else None
    state.clue_decision = ClueDecisionRecord(
        kind=kind, reason=decision.reason, dept_id=dept_id, tied_dept_ids=list(decision.tied_dept_ids),
    )
    logger.info("clue decision case_id=%s kind=%s reason=%s dept_id=%s", case.case_id, kind, decision.reason, dept_id)
    if result is not None:
        case.department_result = result
        state.department_status = "resolved"
        state.department_next_question_intent = None
        state.pending_clarification_intent = None
        state.pending_clarification_question = None
        state.pending_clue_question_index = None
        state.clue_reask_count = 0
        state.next_information_needed = []


def _clue_answers(case: TriageCase) -> list[dict]:
    return [
        dict(concept=record.concept, answer=record.answer)
        for record in case.conversation_state.clue_concepts_asked if record.answer != "unanswered"
    ]


def remember_semantic_official_links(case: TriageCase, retrieved: list[dict]) -> None:
    """Persist audited (source, concept) pairs so later lexical hits cannot drop them."""
    state = case.conversation_state
    known = {(link.patient_source_text, link.knowledge_concept) for link in state.semantic_official_links}
    for item in retrieved:
        pair = (item["patient_source_text"], item["knowledge_concept"])
        if pair not in known:
            known.add(pair)
            state.semantic_official_links.append(SemanticOfficialLink(
                patient_source_text=pair[0], knowledge_concept=pair[1],
            ))
    state.semantic_official_links = state.semantic_official_links[-_MAX_SEMANTIC_LINKS:]


def _new_concept_rows(literal: list[dict], semantic: list[dict]) -> list[dict]:
    """Semantic rows whose official concept the literal match has not already found for that department."""
    found = {(item["dept_id"], normalize_concept(item["knowledge_concept"])) for item in literal}
    return [item for item in semantic if (item["dept_id"], normalize_concept(item["knowledge_concept"])) not in found]


def _merge_official_evidence(primary: list[dict], extra: list[dict]) -> list[dict]:
    def key(item: dict) -> tuple:
        return (item["dept_id"], item["patient_source_text"], item["knowledge_source_id"], item["knowledge_concept"])
    seen = {key(item) for item in primary}
    return primary + [item for item in extra if key(item) not in seen]


def validate_candidate_proposal(
    proposal: object, retrieved: list[dict], sources: list[dict], active_departments: list[dict],
    user_texts: list[str],
) -> tuple[str, list[CandidateDepartment], str | None, str | None]:
    if not isinstance(proposal, dict):
        return "unresolved", [], None, None
    status = proposal.get("status")
    if status not in {"resolved", "ambiguous", "unresolved"}:
        status = "unresolved"
    allowed_sources = {source["source_id"] for source in sources}
    allowed = {
        (item["dept_id"], item["patient_source_text"], item["knowledge_source_id"], item["knowledge_concept"]): item
        for item in retrieved
    }
    active_ids: dict[int, int] = {}
    for row in active_departments:
        number = canonical_department_id(row.get("dept_id"))
        if number is not None:
            active_ids[number] = active_ids.get(number, 0) + 1
    validated: list[CandidateDepartment] = []
    seen_ids: set[int] = set()
    raw_candidates = proposal.get("candidates")
    for raw in raw_candidates if isinstance(raw_candidates, list) else []:
        if not isinstance(raw, dict):
            continue
        dept_id, confidence = raw.get("dept_id"), raw.get("confidence")
        supports = raw.get("supporting_evidence")
        if (
            type(dept_id) is not int or dept_id <= 0 or dept_id in seen_ids
            or active_ids.get(dept_id) != 1
            or type(confidence) not in {int, float} or not math.isfinite(confidence)
            or not 0 <= confidence <= 1
            or not isinstance(supports, list) or not supports
        ):
            continue
        valid_supports: list[CandidateDepartmentEvidence] = []
        live_item = None
        for support in supports:
            if not isinstance(support, dict):
                valid_supports = []
                break
            source_text = support.get("patient_source_text")
            source_id = support.get("knowledge_source_id")
            concept = support.get("knowledge_concept")
            if (
                not isinstance(source_text, str) or not source_text.strip()
                or source_text != source_text.strip()
                or not any(source_text in text for text in user_texts)
                or not isinstance(source_id, str) or source_id not in allowed_sources
                or not isinstance(concept, str)
            ):
                valid_supports = []
                break
            item = allowed.get((dept_id, source_text, source_id, concept))
            if item is None or (live_item is not None and (
                live_item["parentDept"], live_item["childDept"]
            ) != (item["parentDept"], item["childDept"])):
                valid_supports = []
                break
            live_item = item
            valid_supports.append(CandidateDepartmentEvidence(
                patient_source_text=source_text,
                knowledge_source_id=source_id,
                knowledge_concept=concept,
            ))
        if not valid_supports or live_item is None:
            continue
        seen_ids.add(dept_id)
        validated.append(CandidateDepartment(
            dept_id=dept_id, parentDept=live_item["parentDept"], childDept=live_item["childDept"],
            confidence=float(confidence), supporting_evidence=valid_supports,
        ))
    validated.sort(key=lambda item: item.confidence, reverse=True)
    validated = validated[:3]
    reason = proposal.get("uncertainty_reason")
    reason = reason.strip()[:240] if isinstance(reason, str) and reason.strip() else None
    intent = proposal.get("next_question_intent")
    intent = intent if isinstance(intent, str) and _INTENT.fullmatch(intent) else None
    return status, validated, reason, intent


async def reason_about_departments(case: TriageCase) -> None:
    """Recompute candidates from current evidence; AI output never owns workflow state."""
    state = case.conversation_state
    state.department_status = "unresolved"
    state.candidate_departments = []
    state.department_uncertainty_reason = None
    state.department_next_information_needed = None
    state.department_next_question_intent = None
    state.department_clue_offer = []
    state.department_clue_candidates = []
    state.clue_decision = None
    case.department_result = None
    try:
        active = fetch_active_departments()
        if not active:
            state.department_uncertainty_reason = "正式科別主資料目前無法取得"
            return
        preference = resolve_requested_department(case, active)
        if preference is not None:
            case.department_result = preference
            state.department_status = "resolved"
            return
        sources, records = load_department_knowledge()
        resolutions = resolve_department_names(records, active)
        retained = retained_semantic_official_evidence(case, records, resolutions)
        retrieved = retrieve_official_evidence(case, records, resolutions)
        # Semantic retrieval runs even with literal hits, so a literal match on one word
        # cannot hide a closer official concept; literal concepts are not counted twice.
        semantic = _new_concept_rows(
            retrieved, await retrieve_semantic_official_evidence(case, records, resolutions),
        )
        remember_semantic_official_links(case, semantic)
        retrieved = _merge_official_evidence(retrieved, semantic)
        retrieved = _merge_official_evidence(retrieved, retained)
        retrieved = _merge_official_evidence(retrieved, clues.clue_answer_evidence(
            state.clue_concepts_asked, records, resolutions,
            _contradicted_concepts(accepted_semantic_evidence(case)),
        ))
        if not retrieved:
            state.department_uncertainty_reason = "目前沒有可精確對應正式科別的官方症狀證據"
            apply_clue_decision(case, [], [], active)
            return
        payload = {
            "accepted_semantic_evidence": accepted_semantic_evidence(case),
            "retrieved_official_evidence": retrieved,
            "previously_asked_intents": state.asked_clarification_intents,
        }
        clue_answers = _clue_answers(case)
        if clue_answers:
            payload["clue_answers"] = clue_answers
        prompt = (
            "僅比較 retrieved_official_evidence 中的正式 DB 科別，不可新增科別、猜 dept_id、診斷或控制流程。"
            "accepted_semantic_evidence 是已驗證的 present/absent/uncertain 語意證據；"
            "retrieved_official_evidence 只會由 present 證據建立，候選 supporting_evidence 只能引用其中項目。"
            + ("clue_answers 是使用者對線索題（詢問是否有某些官方症狀概念）的回答紀錄，"
               "answer 為 present、absent 或 uncertain。" if clue_answers else "") +
            "輸出 JSON：status (resolved/ambiguous/unresolved), candidates (最多 3 個，每項 dept_id, "
            "confidence 0..1, supporting_evidence 陣列；每筆有逐字 patient_source_text, knowledge_source_id, "
            "knowledge_concept), uncertainty_reason, next_question_intent。"
            "如目前仍不確定，提出可區分候選的英文 snake_case next_question_intent。它是 Backend/AI 間的"
            " opaque correlation key，只代表下一個 patient-answerable clinical dimension，不是 workflow 指令；"
            "優先一個聚焦面向，避免產生含 _or_ 或 _and_ 的 compound key。不要直接向患者顯示科別。"
            f"\n資料：{json.dumps(payload, ensure_ascii=False)}"
        )
        proposal = json.loads((await complete_runtime_json(prompt, purpose="department_convergence")).strip())
        proposed_status, candidates, reason, intent = validate_candidate_proposal(
            proposal, retrieved, sources, active, _user_texts(case),
        )
        state.candidate_departments = candidates
        askable = store_clue_offer(case, records, retrieved)
        state.department_uncertainty_reason = reason
        state.department_next_question_intent = intent if (
            state.pending_clarification_intent is None and (proposed_status != "resolved" or len(candidates) != 1)
        ) else None
        # The proposal contract currently provides a diagnostic uncertainty reason and
        # a machine intent, not a separately validated patient-answerable information need.
        state.department_next_information_needed = None
        if not candidates:
            state.department_status = "unresolved"
        elif proposed_status == "resolved" and len(candidates) == 1 and candidates[0].confidence >= ACCEPT_THRESHOLD:
            winner = candidates[0]
            state.department_status = "resolved"
            case.department_result = DepartmentResult(
                dept_id=winner.dept_id, parentDept=winner.parentDept, childDept=winner.childDept,
                confidence=winner.confidence,
                reason=["依已驗證患者原文、官方科別證據與正式 Department 主資料收斂"],
            )
        else:
            state.department_status = "ambiguous"
        apply_clue_decision(case, askable, retrieved, active, records)
    except Exception as exc:
        logger.warning("department convergence unavailable case_id=%s error=%s", case.case_id, type(exc).__name__)
        state.department_status = "unresolved"
        state.candidate_departments = []
        state.department_clue_offer = []
        state.department_clue_candidates = []
        state.clue_decision = None
        state.department_uncertainty_reason = "科別證據比對暫時無法完成"
