"""Pure helpers for knowledge-based clue questions (docs/修改計畫.md 第 9 節「線索題收斂」).

No AI calls, no SQL and no settings reads: callers pass candidates, official records,
validated evidence and limits. Patient text is never parsed here; concepts are compared
only through official KB strings and AI-structured normalized values.

Scoring counts distinct grounded pieces of patient text per candidate department:
distinct ``patient_source_text`` in retrieved official evidence plus distinct clue
``question_index`` answered present for that department. Commit 3 把 concept_answers 轉成
語意證據時，必須標記來源是線索題回答，計分時從 retrieved 的文字計數中排除，避免和題號計數重複加分。
Such items carry ``evidence_origin == CLUE_ANSWER_ORIGIN`` and are skipped by the text count.
A concept held by several candidates credits every department in its ``dept_ids``.

``ConversationState.department_clue_offer`` holds only the top ``clue_planner_concept_limit``
ranked clues for the planner. 平手加問（Commit 5）必須從全部可問線索找，不能只用
department_clue_offer：tiebreak_clues() must receive the full clue_concepts() result.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Literal

from app.schemas import ClueConceptRecord, ClueOffer, ConversationState
from app.services.department_knowledge import lookup_concept, normalize_concept

CLUE_ANSWER_ORIGIN = "clue_answer"
MAX_CLUE_CONCEPT_RECORDS = 30
CLUE_INTENT_PREFIX = "department_clue_"
# The official routing tables list symptoms and diseases together, so wording covers both.
CLUE_TEMPLATE = "請問您有沒有以下情況，或以前曾被告知有：{}？"
CLUE_REASK_TEMPLATE = "想再確認一次，請問您有沒有以下情況，或以前曾被告知有：{}？"
_SYMPTOM_FIELDS = {"symptom", "accompanying_symptoms"}
_CONCLUDED_ANSWERS = {"present", "absent"}

DecisionKind = Literal["ask_clue", "decide", "tiebreak", "family_medicine", "defer_to_safety_net"]


@dataclass(frozen=True)
class ClueConcept:
    concept: str
    dept_ids: frozenset[int]
    unique: bool
    has_direct_routing: bool
    best_source_priority: int
    uncertain_before: bool


@dataclass(frozen=True)
class ClueDecision:
    kind: DecisionKind
    dept_id: int | None = None
    tied_dept_ids: tuple[int, ...] = ()
    reason: str = ""


def candidate_dept_ids(retrieved: list[dict]) -> set[int]:
    """All departments officially retrieved this turn, not only the convergence AI's top 3."""
    return {item["dept_id"] for item in retrieved}


def mentioned_concepts(retrieved: list[dict], accepted_evidence: list[dict]) -> set[str]:
    """Normalized concepts the patient already covered, from official matches and AI values."""
    mentioned = {normalize_concept(item["knowledge_concept"]) for item in retrieved}
    for evidence in accepted_evidence:
        value = evidence.get("normalized_value")
        if evidence.get("field") in _SYMPTOM_FIELDS and isinstance(value, str) and value.strip():
            mentioned.add(normalize_concept(value))
    return mentioned


def clue_concepts(
    records: list[dict], candidates: set[int], mentioned: set[str], asked: list[ClueConceptRecord],
) -> list[ClueConcept]:
    """Distinguishing official concepts of the candidates that the patient has not covered.

    Records are matched by ``canonical_dept_id``, which equals the live ``dept_id`` once
    resolved. A concept held by every candidate cannot distinguish them and is dropped.
    Concepts answered present/absent are never asked again; uncertain/unanswered stay.
    Only concepts with at least one candidate ``direct_routing_evidence`` record (the
    hospital's symptom-to-department guidance) are askable; their ``dept_ids`` still hold
    every candidate with the concept. Specialty-only concepts are never asked but keep
    their part in retrieval and scoring.
    """
    concluded = {normalize_concept(item.concept) for item in asked if item.answer in _CONCLUDED_ANSWERS}
    inconclusive = {normalize_concept(item.concept) for item in asked if item.answer not in _CONCLUDED_ANSWERS}
    grouped: dict[str, dict] = {}
    for record in records:
        dept_id = record["canonical_dept_id"]
        key = normalize_concept(record["concept"])
        if dept_id not in candidates or key in mentioned or key in concluded:
            continue
        entry = grouped.setdefault(key, {"names": set(), "depts": set(), "direct": False, "priority": None})
        entry["names"].add(record["concept"])
        entry["depts"].add(dept_id)
        entry["direct"] = entry["direct"] or record["evidence_type"] == "direct_routing_evidence"
        priority = record["source_priority"]
        entry["priority"] = priority if entry["priority"] is None else min(entry["priority"], priority)
    clues = []
    for key, entry in grouped.items():
        if entry["depts"] == candidates or not entry["direct"]:
            continue
        clues.append(ClueConcept(
            concept=min(entry["names"]), dept_ids=frozenset(entry["depts"]),
            unique=len(entry["depts"]) == 1, has_direct_routing=entry["direct"],
            best_source_priority=entry["priority"], uncertain_before=key in inconclusive,
        ))
    return clues


def rank_clue_concepts(clues: Iterable[ClueConcept], limit: int) -> list[ClueConcept]:
    """Unique first, then direct routing, source priority, inconclusive last, then text."""
    ranked = sorted(clues, key=lambda clue: (
        not clue.unique, not clue.has_direct_routing, clue.best_source_priority,
        clue.uncertain_before, clue.concept,
    ))
    return ranked[:max(limit, 0)]


def common_clue_departments(
    selected: list[str], offered: Iterable[ClueConcept | ClueOffer], per_question: int,
) -> set[int]:
    """Departments shared by 1..per_question distinct offered concepts; empty when invalid."""
    by_concept = {clue.concept: clue for clue in offered}
    if not 1 <= len(selected) <= per_question or len(set(selected)) != len(selected):
        return set()
    if any(concept not in by_concept for concept in selected):
        return set()
    common = set(by_concept[selected[0]].dept_ids)
    for concept in selected[1:]:
        common &= set(by_concept[concept].dept_ids)
    return common


def clue_offer(ranked: Iterable[ClueConcept]) -> list[ClueOffer]:
    """Planner-facing form of ranked clues: official concept and opaque department ids only."""
    return [ClueOffer(concept=clue.concept, dept_ids=sorted(clue.dept_ids)) for clue in ranked]


def clue_intent(question_index: int) -> str:
    """Opaque Backend/AI correlation key of one clue question."""
    return f"{CLUE_INTENT_PREFIX}{question_index}"


def pending_clue_index(state: ConversationState) -> int | None:
    """The pending clue question's index, only while it still owns the pending intent."""
    index = state.pending_clue_question_index
    if index is not None and state.pending_clarification_intent == clue_intent(index):
        return index
    return None


def pending_clue_concepts(state: ConversationState) -> list[str]:
    index = pending_clue_index(state)
    if index is None:
        return []
    return [record.concept for record in state.clue_concepts_asked if record.question_index == index]


def concepts_missing_from_question(question: str, concepts: Iterable[str]) -> list[str]:
    """Official concepts not quoted verbatim in the planner's question (AI output only)."""
    return [concept for concept in concepts if concept not in question]


def template_clue_selection(offer: list[ClueOffer], per_question: int) -> tuple[list[str], int | None]:
    """Up to per_question top-ranked offered concepts sharing the first clue's department."""
    if not offer or not offer[0].dept_ids:
        return [], None
    dept_id = min(offer[0].dept_ids)
    selected = [item.concept for item in offer if dept_id in item.dept_ids][:max(per_question, 1)]
    return selected, dept_id


def clue_template_question(concepts: list[str], *, reask: bool = False) -> str:
    """Backend-owned fallback wording; it only lists already validated official concepts."""
    return (CLUE_REASK_TEMPLATE if reask else CLUE_TEMPLATE).format("、".join(concepts))


def append_clue_records(
    asked: list[ClueConceptRecord], concepts: list[str], dept_id: int, question_index: int,
    limit: int = MAX_CLUE_CONCEPT_RECORDS, dept_ids: dict[str, list[int]] | None = None,
) -> list[ClueConceptRecord]:
    """Return a new bounded list with one question's concepts appended as unanswered."""
    added = [ClueConceptRecord(concept=concept, dept_id=dept_id, question_index=question_index,
                               dept_ids=list((dept_ids or {}).get(concept) or [dept_id]))
             for concept in concepts]
    return (list(asked) + added)[-limit:]


def clue_answer_evidence(
    asked: list[ClueConceptRecord], records: list[dict], resolutions: list[dict], contradicted: set[str],
) -> list[dict]:
    """Official evidence rebuilt from present clue answers, marked with CLUE_ANSWER_ORIGIN.

    A concept that current semantic evidence marks absent/uncertain is skipped.
    """
    resolved = {item["canonical_dept_id"]: item for item in resolutions if item["status"] == "resolved"}
    found: dict[tuple, dict] = {}
    for item in asked:
        source = (item.answer_source_text or "").strip()
        if item.answer != "present" or not source or normalize_concept(item.concept) in contradicted:
            continue
        for record in lookup_concept(item.concept, records):
            row = resolved.get(record["canonical_dept_id"])
            if row is None:
                continue
            key = (row["db_dept_id"], source, record["source_id"], record["concept"])
            found[key] = dict(
                dept_id=row["db_dept_id"], parentDept=row["db_parent_dept"], childDept=row["db_child_dept"],
                patient_source_text=source, knowledge_source_id=record["source_id"],
                knowledge_concept=record["concept"], knowledge_evidence_text=record["evidence_text"],
                source_priority=record["source_priority"], evidence_origin=CLUE_ANSWER_ORIGIN,
            )
    return list(found.values())


def department_scores(
    retrieved: list[dict], candidates: set[int], asked: list[ClueConceptRecord],
) -> dict[int, int]:
    """Distinct retrieved text pieces plus distinct present clue questions, per candidate.

    Synonyms matched from one text piece score once; absent answers never subtract.
    """
    texts: dict[int, set[str]] = {dept_id: set() for dept_id in candidates}
    questions: dict[int, set[int]] = {dept_id: set() for dept_id in candidates}
    for item in retrieved:
        if item["dept_id"] in texts and item.get("evidence_origin") != CLUE_ANSWER_ORIGIN:
            texts[item["dept_id"]].add(item["patient_source_text"])
    for record in asked:
        if record.answer != "present":
            continue
        for dept_id in record.dept_ids:
            if dept_id in questions:
                questions[dept_id].add(record.question_index)
    return {dept_id: len(texts[dept_id]) + len(questions[dept_id]) for dept_id in candidates}


def clue_confirmed_departments(asked: list[ClueConceptRecord], candidates: set[int]) -> set[int]:
    """Candidate departments of concepts the patient answered present, across all their dept_ids."""
    return {dept_id for record in asked if record.answer == "present" for dept_id in record.dept_ids} & candidates


def tiebreak_clues(clues: Iterable[ClueConcept], tied: set[int]) -> list[ClueConcept]:
    """Clues held by exactly one of the tied departments, in normal rank order."""
    eligible = [clue for clue in clues if len(clue.dept_ids & tied) == 1]
    return rank_clue_concepts(eligible, len(eligible))


def decide(
    *, ai_resolved_dept_id: int | None, candidates: set[int], scores: dict[int, int],
    askable: list[ClueConcept], questions_asked: int, tiebreak_used: bool,
    user_declined: bool, clue_confirmed_dept_ids: set[int], question_limit: int,
) -> ClueDecision:
    """Apply rules A/C and the decision triggers; every decision still needs user confirmation."""
    if not candidates:
        return ClueDecision("defer_to_safety_net", reason="no_candidates")
    if ai_resolved_dept_id is not None and ai_resolved_dept_id in candidates:
        return ClueDecision("decide", dept_id=ai_resolved_dept_id, reason="ai_resolved")
    if user_declined:
        reason = "user_declined"
    elif len(clue_confirmed_dept_ids & candidates) == 1:
        reason = "clue_confirmed_single_department"
    elif not askable:
        reason = "no_askable_clues"
    elif questions_asked >= question_limit:
        reason = "question_limit"
    else:
        return ClueDecision("ask_clue", reason="continue")
    best = max(scores.get(dept_id, 0) for dept_id in candidates)
    tied = sorted(dept_id for dept_id in candidates if scores.get(dept_id, 0) == best)
    if len(tied) == 1:
        return ClueDecision("decide", dept_id=tied[0], reason=reason)
    if not user_declined and not tiebreak_used and tiebreak_clues(askable, set(tied)):
        return ClueDecision("tiebreak", tied_dept_ids=tuple(tied), reason=reason)
    return ClueDecision("family_medicine", tied_dept_ids=tuple(tied), reason=reason)
