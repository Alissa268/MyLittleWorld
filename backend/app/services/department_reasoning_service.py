"""Official-KB-grounded department candidates with live-DB and source validation."""

from __future__ import annotations

import json
import logging
import math
import re

from app.config import get_settings
from app.db import fetch_active_departments
from app.schemas import (
    CandidateDepartment, CandidateDepartmentEvidence, DepartmentResult, SemanticOfficialLink, TriageCase,
)
from app.services import department_clues as clues
from app.services.ai_service import complete_runtime_json
from app.services.confidence_scoring import ACCEPT_THRESHOLD
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


def retrieve_official_evidence(case: TriageCase, records: list[dict], resolutions: list[dict]) -> list[dict]:
    """Match official concepts only against validated present semantic evidence."""
    resolved = {
        entry["canonical_dept_id"]: entry
        for entry in resolutions if entry["status"] == "resolved"
    }
    concepts = {record["concept"] for record in records}
    found: dict[tuple[int, str, str, str], dict] = {}
    for evidence in accepted_semantic_evidence(case):
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
        for concept in concepts:
            normalized_concept = normalize_concept(concept)
            if not any(normalized_concept in surface for surface in retrieval_surfaces):
                continue
            for record in lookup_concept(concept, records):
                resolution = resolved.get(record["canonical_dept_id"])
                if resolution is None:
                    continue
                key = (resolution["db_dept_id"], source, record["source_id"], record["concept"])
                found[key] = {
                    "dept_id": resolution["db_dept_id"],
                    "parentDept": resolution["db_parent_dept"],
                    "childDept": resolution["db_child_dept"],
                    "patient_source_text": source,
                    "knowledge_source_id": record["source_id"],
                    "knowledge_concept": record["concept"],
                    "knowledge_evidence_text": record["evidence_text"],
                    "source_priority": record["source_priority"],
                }
    return list(found.values())


async def retrieve_semantic_official_evidence(
    case: TriageCase, records: list[dict], resolutions: list[dict],
) -> list[dict]:
    """Bridge wording to the existing official catalog, never invent routing data."""
    evidence = accepted_semantic_evidence(case)
    present_sources = _present_sources(evidence)
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
        "knowledge_concept 必須逐字取自 official_concepts，confidence 為 0 到 1。"
        "只回 JSON：{\"matches\":[{\"patient_source_text\":\"...\","
        "\"knowledge_concept\":\"...\",\"confidence\":0.9}]}\n"
        + json.dumps(dict(accepted_semantic_evidence=evidence, official_concepts=catalog), ensure_ascii=False)
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
                or not ACCEPT_THRESHOLD <= confidence <= 1):
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
        "必須一起參考 evidence 的 body_part，不能因為錨點只有『疼痛』而忽略已提供的疼痛部位。"
        "尤其『腫脹』不能自行當成已確認的『水腫』，『疼痛』不能自行加上『酸』或『麻』。"
        "不得因為概念可能與症狀有醫學關聯就保留，必須是這段原話實際表達的症狀。"
        "請逐筆判斷並解釋，不確定則 supported=false。不可新增或改寫連結。只回 JSON {\"judgments\":["
        "{\"patient_source_text\":\"...\",\"knowledge_concept\":\"...\",\"supported\":true,\"reason\":\"依據\"}]}。\n"
        + json.dumps(dict(accepted_semantic_evidence=evidence, proposed_matches=links), ensure_ascii=False)
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
        return [item for item in found.values() if (item["patient_source_text"], item["knowledge_concept"]) in retained]
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
    present_sources = _present_sources(evidence)
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
    return list(found.values())


def _contradicted_concepts(evidence: list[dict]) -> set[str]:
    return {
        normalize_concept(str(item["normalized_value"])) for item in evidence
        if item["field"] in _ASSERTED_MEDICAL_FIELDS and item["assertion"] in {"absent", "uncertain"}
    }


def store_clue_offer(case: TriageCase, records: list[dict], retrieved: list[dict]) -> None:
    """Persist the Backend-ranked askable clues of all retrieved candidates for the planner."""
    state = case.conversation_state
    settings = get_settings()
    candidates = clues.candidate_dept_ids(retrieved)
    mentioned = clues.mentioned_concepts(retrieved, accepted_semantic_evidence(case))
    askable = clues.clue_concepts(records, candidates, mentioned, state.clue_concepts_asked)
    state.department_clue_candidates = sorted(candidates)
    state.department_clue_offer = clues.clue_offer(
        clues.rank_clue_concepts(askable, settings.clue_planner_concept_limit)
    )


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
        if not retrieved:
            retrieved = await retrieve_semantic_official_evidence(case, records, resolutions)
            remember_semantic_official_links(case, retrieved)
        retrieved = _merge_official_evidence(retrieved, retained)
        retrieved = _merge_official_evidence(retrieved, clues.clue_answer_evidence(
            state.clue_concepts_asked, records, resolutions,
            _contradicted_concepts(accepted_semantic_evidence(case)),
        ))
        if not retrieved:
            state.department_uncertainty_reason = "目前沒有可精確對應正式科別的官方症狀證據"
            return
        store_clue_offer(case, records, retrieved)
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
    except Exception as exc:
        logger.warning("department convergence unavailable case_id=%s error=%s", case.case_id, type(exc).__name__)
        state.department_status = "unresolved"
        state.candidate_departments = []
        state.department_clue_offer = []
        state.department_clue_candidates = []
        state.department_uncertainty_reason = "科別證據比對暫時無法完成"
