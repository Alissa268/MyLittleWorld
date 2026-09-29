# Phase 4 Candidate Department Convergence Validation

## Scope and architecture

Phase 4 adds `backend/app/services/department_reasoning_service.py`. In the AI-enabled natural `/chat` path, each new non-safety user turn now follows:

`grounded patient evidence -> Phase 3 official KB lookup -> exact resolution against fetch_active_departments() -> restricted AI comparison -> Backend candidate validation -> unresolved / ambiguous / resolved`.

The service recomputes candidates from current patient evidence every turn. A previous AI candidate is not carried forward as a patient fact. Structured `CandidateDepartmentEvidence` and `CandidateDepartment` live in `app/schemas.py`; `ConversationState` stores `department_status`, up to three `candidate_departments`, an uncertainty reason, the next information needed, and a proposed clarification intent. Only Backend writes these fields and `DepartmentResult`.

Production retrieval uses `load_department_knowledge()`, `lookup_concept()`, and `resolve_department_names()` from the existing Phase 3 service. It reads the unchanged `backend/knowledge/sources.json` and `backend/knowledge/vghtpe_department_guidance.json`. DB truth comes from the current `fetch_active_departments()` call. The audit file `docs/PHASE3_310_DEPARTMENT_INVENTORY.json` is never read by production reasoning. Phase 3 evidence was not edited.

## AI proposal and Backend validation

The AI receives only retrieved official KB evidence with exactly resolved live DB IDs, plus conversation context and grounded patient evidence. It proposes `status`, at most three useful `candidates` (each with `dept_id`, `confidence`, and `supporting_evidence`), `uncertainty_reason`, and `next_question_intent`. Every support must contain verbatim `patient_source_text`, registered `knowledge_source_id`, and an exact retrieved `knowledge_concept`. AI-provided department names, workflow fields, diagnoses, or DB mappings are not accepted.

Backend rejects a candidate when its ID is not a positive integer, is absent or duplicated in the active DB list, or is not in the exact official-KB-to-live-DB resolution for that turn. It rejects unknown or mismatched source IDs, concepts not in the retrieved record, patient quotations not in actual user history, empty support, and confidence outside finite numeric `[0, 1]` (including booleans). Duplicate IDs are discarded; validated candidates are sorted and capped at three. Two or more valid candidates stay `ambiguous` even if AI says `resolved`; one valid candidate still stays `ambiguous` unless AI proposes `resolved` and confidence reaches `ACCEPT_THRESHOLD`. No valid candidate is `unresolved`. Only the successful single-candidate gate creates `DepartmentResult`, populated from the live DB resolution rather than AI names.

An explicit user-requested department remains a separate preference path and is checked against the live DB. Its reason identifies it as the user's registration preference, not a KB-derived medical conclusion.

## Clarification and safety

`request_clarification()` now sees candidate state, uncertainty, and a required intent. When candidates are ambiguous, the planner is asked for one question that distinguishes them without telling the patient department names. A mismatched required intent is rejected and handled with the existing safe fallback. Free-form grounded answers to pending clarification are saved before candidate recomputation, enabling cross-turn convergence without adding fixed medical fields.

Existing positive red-flag urgency and the deterministic `safety_check` turn take precedence. Safety turns do not run semantic extraction, candidate reasoning, or an AI clarification call. Symptom sufficiency alone no longer permits confirmation: a natural case needs both resolved safety and a validated resolved department. At the eight-turn cap, ambiguous/unresolved candidates remain unresolved; the first candidate is never selected automatically. A confirmation-only request retains the previously validated state rather than rerunning a detector.

Ordinary natural free-text never invokes `detect_department_result()`, `_rule_based_department()`, or the project-smart single-department adapter, including when the AI runtime is entirely unavailable or its provider fails. `/recommend` and `recommend_appointments()` no longer rerun old detection when `department_result` is absent; they reject the case. The legacy detector remains only for structured/batch compatibility, not for ordinary free-text symptom inference.

## Final no-runtime-AI gate

`/chat` distinguishes `req.message` or user text in `req.messages` from machine-readable `req.answers`. `ConversationState.free_text_mode` persists that distinction across later confirmation-only turns; an actual structured batch request may enter the existing compatibility path. With no runtime AI, ordinary free-text continues deterministic symptom/safety collection but keeps `department_status=unresolved`, `department_result=null`, and `stage=collecting` unless the user explicitly requested a department that `resolve_requested_department()` uniquely verifies against the live DB. That exception is recorded as a user preference, never as an official-KB medical conclusion. If the checklist has no more question but department evidence is unresolved, Backend returns an explicit safe no-automatic-department reply instead of entering confirmation. Positive red-flag urgency and a pending safety question retain precedence.

## Tests and result

`backend/tests/test_phase4_department_convergence.py` uses synthetic KB/DB records and mocked AI. It covers invalid/nonexact IDs, wrong or unknown provenance, ungrounded quotes, invalid confidence, deduplication and Top-K, equal concepts across different official-priority departments, ambiguous and low-confidence proposals, cross-turn ambiguous-to-resolved recomputation, provider timeout/malformed JSON, explicit preference, safety precedence, required intent validation, eight-turn cap, confirmation, no legacy detector in natural `/chat`, missing-result `/recommend`, and no production audit-snapshot dependency. The final gate adds no-key `message` and `messages` cases, a knee-pain legacy-mapping regression, persisted confirmation-only protection, exact live-DB preference resolution, pending and positive safety cases. Older end-to-end tests were updated to use an explicit validated department when they need to exercise confirmation/recommendation; symptom-only no-AI cases now assert safe collecting instead of legacy automatic routing. Structured batch regression remains covered by `test_batch_question_flow.py`.

Run from `backend` with the existing virtual environment and `CEREBRAS_API_KEY` temporarily empty for this test process (unit tests must not call the real provider):

```powershell
$env:CEREBRAS_API_KEY=''
$env:PYTHONPATH='.'
.\.venv\Scripts\pytest.exe -q
```

Full result after the final no-runtime-AI gate: **470 passed, 8 warnings, 302 subtests passed**. Warnings are existing FastAPI/TestClient deprecations plus a local `.pytest_cache` write warning. No Android build was needed because the API was extended additively and Android files were not changed.

## Remaining limits and phase boundary

The production official KB is intentionally small; only exact live-DB-resolved KB departments can become routable candidates. Unresolved Phase 3.1 names are not fuzzy-mapped. Lexical retrieval and accepted normalized interpretations are used, without new medical keyword rules or embeddings. A live SQL Server connection was not used in unit tests; live Department responses and AI proposals were mocked. This is engineering validation, not clinical validation. Phase 5 urgency/TTAS, Phase 6 doctor scoring, Android UI, SQL schema/data, accessibility, and script generation were not changed or started.

## Phase 4.1: free-form clarification state

The live Cerebras hematuria case exposed two linked issues: the planner could put severity and accompanying symptoms in one question, while a grounded answer such as "有血絲" did not reliably clear the pending `severity` intent. If the planner failed to recognize that answer, the Backend repeated a generic pending-answer prompt. This was a clarification-state problem; the validated nephrology result and negative safety screen were not the cause.

`backend/app/services/conversation_service.py` now sends the pending assistant question explicitly to the planner and instructs it to compare that question with the current reply before planning another. The prompt requires one intent, one focused information dimension, and one question; it specifically separates a degree question from pain or accompanying symptoms. The Backend rejects multiple question marks or semicolon-separated question proposals and still rejects diagnosis, department, and doctor wording. This structural check supplements the prompt; it does not add a symptom dictionary or claim to prove clinical atomicity of arbitrary prose.

The answer fields are validated independently of a proposed follow-up question. An invalid or compound next question therefore cannot discard a separately grounded answer. Clearing a pending intent still requires matching `answered_intent`, verbatim `answer_source_text` within the current user text, `answer_status=answered`, and finite non-boolean confidence at least `ACCEPT_THRESHOLD`; a canonical severity extraction is not required. `partial` and `unclear` keep the pending intent, while a safe, nonduplicate same-intent targeted follow-up is allowed. If the planner cannot supply one, Backend tries nonidentical focused retries using the existing information need; it acknowledges newly accepted grounded medical evidence without assuming that evidence answered the pending intent. Exhausted retries go to the existing unresolved path rather than repeating one prompt indefinitely.

`backend/tests/test_phase41_clarification.py` adds the exact two-turn hematuria conversation with mocked providers: the first turn has a resolved `dept_id=1242` and pending `severity`; the second includes "有血絲", burning on urination, and explicit red-flag denial. It verifies that `clarification_evidence["severity"]` records the grounded phrase, pending clears, the negative safety screen and accompanying symptom coexist, the department result remains 1242, and the old generic reply is absent. Additional cases cover partial targeted follow-up, wrong intent, ungrounded text, low/boolean confidence, duplicate or compound questions, independently accepted answer fields, and a nonduplicate focused retry after new accepted evidence. Existing Phase 2.3 and Phase 4 tests remain passing.

Full Backend run from `backend` with `CEREBRAS_API_KEY` empty for this unit-test process: `python -m pytest -q` -> **476 passed, 8 warnings, 308 subtests passed**. The warnings are FastAPI/TestClient deprecations and the local `.pytest_cache` write warning. No real provider, Android, DB, KB, TTAS, urgency, or doctor scoring changes were made. Phase 5 was not started.

## Phase 4.2: Backend trust boundary and reliability hardening

### Server authority and transactional recommendation

For an existing `case_id`, `/chat` and `/recommend` now always use the server-stored case. A supplied `triage_case` with a different request `case_id` is rejected. When no stored case exists, compatibility snapshots have all server-owned workflow, safety, confirmation, candidate, and department conclusions reset before use; they cannot self-assert completion. `/recommend` performs work on a deep copy and commits stage, `recommendation_generated`, the case, and recommendation records only after canonical validation and a nonempty successful recommendation. DB failure, invalid department, and empty schedules leave the stored case unchanged.

### Canonical DB validation and outage semantics

Every normal recommendation re-fetches the live Department master and requires one exact `dept_id` + `parentDept` + `childDept` tuple before any Schedule query. Invalid IDs and name mismatches raise `DepartmentResolutionError`; no fuzzy or legacy fallback runs. `DatabaseUnavailableError` now distinguishes connection/query failure from a successful zero-row query. Routes translate unavailable Department, Doctor, and Schedule data to HTTP 503, while genuine zero rows remain normal empty-data behavior where the endpoint contract permits it.

Return visits validate one exact live Department and one canonical active, non-placeholder Doctor relationship before schedule lookup. A supplied doctor ID must match both doctor name and department; name-only input must have one exact relationship. The response claims master-data validation only after this succeeds. With no preferred date, production queries only the next 21 calendar days, including today; explicit dates remain exact and past rows are not accepted by the default window.

### Schedule selection and resource safety

Quick Search, normal recommendations, and follow-up recommendations with a real `schedule_id` use one generalized schedule revalidation path immediately before `build_navigation_script()`. It verifies schedule, doctor, department, date, normalized session, active/non-placeholder doctor, and current availability. Changed or unavailable data returns 409; DB failure returns 503. The script uses the reloaded DB-authoritative row.

All direct `create_db_connection()` call sites in `app/db.py` now close in `finally`, including connection/cursor execution/fetch failures. Reference doctor deduplication uses `doctor_id`, preserving distinct same-name doctors while collapsing repeated Schedule associations for one ID.

### Privacy, TTL, and clarification robustness

Routine INFO logs were reduced to operational metadata: case IDs, field names, counts, booleans, provider/model/latency, and exception types. Patient messages, symptom/body-part/red-flag values, availability values, semantic normalized values, grounded source text, AI questions/replies, keyword matches, and raw medical payloads are no longer logged. Chat performance traces retain only presence/length metadata for generated content.

The prototype case store now has an opportunistic two-hour monotonic TTL. Create/get/save operations prune expired cases; recommendations expire with their case; successful access touches the case and extends its lifetime. This remains a single-process, single-worker store with no background thread or persistence.

Phase 4.1 grounding remains unchanged: a pending intent clears only through an exact grounded answer with matching intent/status and valid confidence. The planner contract now explicitly requires one intent, one information dimension, and one interrogative request. Backend additionally rejects obvious conjunction-style multi-dimension questions while retaining duplicate/history/same-intent/safety checks. The hematuria `有血絲` regression remains passing.

### Phase 4.2 tests and remaining limits

`backend/tests/test_phase42_backend_hardening.py` covers forged stored-state overrides, mismatched IDs, canonical department checks before Schedule access, commit-after-success, return-visit Department/Doctor identity, DB unavailable versus zero rows, schedule revalidation, connection cleanup on success/execute/fetch failure, same-name doctors, log privacy, TTL/touch behavior, and conjunction-style clarification rejection. Existing tests were updated only where prior expectations intentionally treated an invalid department or DB outage as an empty result, or where fixtures now need explicit canonical DB truth.

Final command from `backend` with runtime AI disabled for unit tests:

```powershell
$env:CEREBRAS_API_KEY=''
$env:PYTHONPATH='.'
python -m pytest -q --tb=short -p no:cacheprovider
```

Result: **492 passed, 8 warnings, 308 subtests passed**. Warnings are existing FastAPI/Starlette deprecations plus the local `.pytest_cache` permission warning. Known limitations remain: the in-memory store is opportunistically pruned and not shared across workers; Doctor-to-Department identity still relies on the existing Schedule association because Doctor has no Department foreign key; no authentication redesign or persistent workflow store was introduced. Android, SQL schema/data, official KB evidence, TTAS/red-flag rules, urgency logic, and Phase 6 doctor scoring behavior were not changed. Phase 5 was not started.
