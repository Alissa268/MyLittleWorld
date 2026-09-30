# Phase 4 Candidate Department Convergence Validation

## Scope and architecture

Phase 4 adds `backend/app/services/department_reasoning_service.py`. In the AI-enabled natural `/chat` path, each new non-safety user turn now follows:

`grounded patient evidence -> Phase 3 official KB lookup -> exact resolution against fetch_active_departments() -> restricted AI comparison -> Backend candidate validation -> unresolved / ambiguous / resolved`.

The service recomputes candidates from current patient evidence every turn. A previous AI candidate is not carried forward as a patient fact. Structured `CandidateDepartmentEvidence` and `CandidateDepartment` live in `app/schemas.py`; `ConversationState` stores `department_status`, up to three `candidate_departments`, an uncertainty reason, the next information needed, and a proposed clarification intent. Only Backend writes these fields and `DepartmentResult`.

Production retrieval uses `load_department_knowledge()`, `lookup_concept()`, and `resolve_department_names()` from the existing Phase 3 service. It reads the unchanged `backend/knowledge/sources.json` and `backend/knowledge/vghtpe_department_guidance.json`. DB truth comes from the current `fetch_active_departments()` call. The audit file `docs/PHASE3_310_DEPARTMENT_INVENTORY.json` is never read by production reasoning. Phase 3 evidence was not edited.

## AI proposal and Backend validation

The AI receives current validated structured semantic evidence, retrieved official KB evidence with exactly resolved live DB IDs, and previously asked intents. It proposes `status`, at most three useful `candidates` (each with `dept_id`, `confidence`, and `supporting_evidence`), `uncertainty_reason`, and `next_question_intent`. Every support must contain verbatim `patient_source_text`, registered `knowledge_source_id`, and an exact retrieved `knowledge_concept`. AI-provided department names, workflow fields, diagnoses, or DB mappings are not accepted.

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

## Phase 4.2.1: trust-boundary closure

### Complete isolated snapshot sanitization

An isolated client `triage_case` is now compatibility input only. Backend keeps nonempty user-authored history and may retain raw symptom/preferences, but discards all client assistant history and replaces the entire `ConversationState` with a fresh default instance. It also clears semantic extractions, triage, department result, confirmation/recommendation/script flags, selected recommendation, red-flag conclusions, severity and urgency normalization, and collected-field bookkeeping. Existing server-stored cases still win over every supplied snapshot. Tests that need an established workflow now seed the server store instead of treating a client snapshot as authoritative.

### Database-unavailable boundary

FastAPI has an application-level `DatabaseUnavailableError` handler. Any typed Department, Doctor, or Schedule outage that is not handled more specifically by a route returns HTTP 503 with the fixed message `正式資料目前無法查詢，請稍後重試。`; exception text, SQL, hosts, and credentials are not exposed. Regressions cover `/chat` explicit department preference, structured department detection, `/recommend` explicit preference resolution, and the existing normal recommendation failure path.

### Canonical return-visit and schedule identity

`fetch_reference_doctors()` keeps the public name-based reference API for compatibility and adds an internal optional `department_id` constraint. Return-visit validation always supplies the canonical live Department ID, so two Department rows with the same child name cannot share doctor identity accidentally. The SQL predicate becomes `dep.dept_id = ?`, and doctor identity remains `doctor_id` plus exact name.

Every stored recommendation used for navigation must contain `schedule_id`, `doctor_id`, `dept_id`, `date`, and `session`. Missing identity is rejected before script construction. Complete stored recommendations and Quick Search selections always run the shared live schedule revalidation exactly once; the navigation script receives only the DB-authoritative row.

### ChatPerf privacy

`record_ai_call()` no longer stores raw presentation reply text. INFO-facing summary and debug structures retain only `raw_reply_present` and `raw_reply_chars`; provider-call metadata cannot serialize the medical reply itself. A sentinel regression serializes both structures and proves the sentinel is absent.

### Phase 4.2.1 validation

Focused trust-boundary, DB adapter, and follow-up suites: **53 passed, 7 warnings**. The complete Backend suite was run with the real-provider key blank for the unit-test process:

```powershell
$env:CEREBRAS_API_KEY=''
.\.venv\Scripts\python.exe -m pytest -q --tb=short -p no:cacheprovider
```

Final result: **500 passed, 7 warnings, 308 subtests passed**. The Phase 4.1 hematuria `有血絲` regression remains passing. Zero-schedule API semantics were not changed. Android, SQL schema/data, official Department KB, TTAS/red-flag and urgency rules, and doctor scoring were not modified. Phase 5 was not started.

## Phase 4.2.2: negation-safe evidence and clarification focus state

The live hematuria conversation exposed two independent bugs. First, a surviving clarification intent could fall back through `next_information_needed`, which had been populated with a Backend diagnostic reason. That caused workflow wording such as `已記錄本輪新資訊，上一個澄清面向仍需確認` to be quoted to the patient. Second, the apparent general-medicine candidate was not legitimate ambiguity: lexical KB retrieval matched `胸痛` inside the grounded denial `我沒有胸痛`, incorrectly treating a negated concept as positive official Department evidence.

Clarification state now has strict responsibilities:

- `uncertainty_reasons` stores Backend diagnostic and workflow reasons.
- `next_information_needed` stores only concrete patient-answerable needs that pass the focus validator.
- `pending_clarification_intent` stores the current canonical machine intent.
- `department_next_question_intent` stores the candidate-disambiguation machine intent.
- `department_next_information_needed` is left empty unless a separately validated patient-answerable need exists; the current department proposal's uncertainty reason is no longer copied into it.

A confidently grounded pending answer clears both the pending intent and its old information need. A valid, safe, nonduplicate same-intent planner question is accepted even when the provider does not emit a canonical structured field. Partial or unclear answers keep the pending intent and may use a targeted same-intent follow-up. Invalid, wrong-intent, unsafe, or duplicate proposals fall back without quoting uncertainty reasons. If no concrete patient-facing focus is available, Backend uses a neutral retry such as `可以再補充和剛才問題相關的症狀細節嗎？`; it does not derive medical wording from a snake_case intent.

`backend/tests/test_phase422_clarification_focus.py` covers clarification focus separation with required `flank_pain`, valid same-intent planning, invalid and duplicate proposals, partial/unclear persistence, grounded clearing, ambiguity preservation for genuinely validated candidates, forbidden workflow-reason invariants, and the unchanged eight-turn cap. Phase 4.1's grounded `有血絲` severity progression remains passing.

The same live test also exposed a separate Department evidence bug: `我沒有胸痛` could be treated as positive `胸痛` evidence. An early Phase 4.2.2 repair added Department-specific raw-text negation and clause parsing. Phase 4 Final Alignment supersedes that repair because it made Backend rules responsible for natural-language polarity and synonym scope, which conflicts with the AI-first architecture. The Department-specific `CLAUSE_BOUNDARIES`, occurrence parser, normalized-source polarity helper, and their retrieval dependencies have been removed. Existing deterministic red-flag helpers remain unchanged for the pre-Phase-5 safety path.

## Phase 4 Final Alignment: semantic assertion and structured evidence

`SemanticExtraction` now has an additive, backward-compatible `assertion` contract: `present`, `absent`, or `uncertain`. This is the Phase 4 final alignment extension to the Phase 1 semantic extraction contract; it does not rewrite the Phase 1 history. `semantic_status` continues to describe extraction availability/ambiguity; `assertion` independently describes whether the normalized medical concept is asserted, denied, or uncertain. AI output for nonempty `symptom` or `accompanying_symptoms` concepts must include a valid assertion. Mixed-polarity language must be split into separate extractions, and each `source_text` must be the shortest contiguous verbatim patient span directly supporting that extraction.

Backend validates the field allow-list, normalized-value shape, semantic status, finite numeric confidence, assertion enum, and exact source grounding. It does not infer assertion from words such as `沒有` or `但是`, does not map synonyms, and does not decide which clause supports a normalized concept. A structurally valid `absent` or `uncertain` extraction remains in `case.semantic_extractions` for clarification, candidate context, and audit, but it cannot populate positive `patient_input.symptom` or `patient_input.accompanying_symptoms` state. Missing or invalid assertion on an AI medical concept fails closed.

Department reasoning now uses `accepted_semantic_evidence(case)` as its clinical evidence boundary. It returns current grounded, confidence-qualified, shape-valid structured evidence: asserted `symptom` and `accompanying_symptoms` concepts plus the relevant non-concept `body_part`, `duration`, `severity`, and `onset` fields. Raw conversation history remains available only for source grounding and workflow context. `patient_input` strings and `conversation_state.clarification_evidence` are no longer lexically scanned for KB concepts; clarification evidence proves that an intent received a grounded answer, not that every medical word in the answer is positive.

Official KB retrieval considers only structured evidence with `assertion=present` and an available/partial semantic status. AI normalization therefore remains useful (`尿尿很痛 → 小便疼痛`, `症狀俗稱甲 → 正式症狀甲`) without a Python synonym dictionary. `absent` and `uncertain` evidence are included in the candidate reasoning context but cannot create routable official evidence. Candidate proposal validation is unchanged: every support must exactly match retrieved official evidence, a registered source, a grounded patient source span, and a unique live DB Department ID.

The candidate AI payload is now limited to `accepted_semantic_evidence`, `retrieved_official_evidence`, and `previously_asked_intents`. It no longer receives raw conversation history, the full mutable patient snapshot, or raw clarification evidence as clinical truth. Provider failure still resolves to `unresolved`; no legacy single-department rule fallback, first-candidate selection, fuzzy Department mapping, or unverified source is enabled.

Regression coverage includes present/absent/uncertain assertion handling, missing/invalid assertion, malformed confidence and normalized-value shapes, source grounding, mixed statements split by mocked AI assertions, synonym normalization, raw-history isolation, clarification-evidence isolation, present-only KB retrieval, candidate payload minimization, candidate source/dept validation, convergence, provider failure, and the legacy-detector reachability guard. Phase 4.1 hematuria clarification, Phase 4.2/4.2.1 hardening, and the Phase 4.2.2 clarification focus suite remain passing.

Final Backend command, with the real-provider key blank for deterministic unit tests:

```powershell
$env:CEREBRAS_API_KEY=''
.\.venv\Scripts\python.exe -m pytest -q --tb=short -p no:cacheprovider
```

Result: **526 passed, 7 warnings, 322 subtests passed**. This is code-level Phase 4 architecture validation only; real Cerebras, real SQL, and Tailscale live acceptance remain separate after review. Android, SQL schema/data, official KB evidence, department mappings, TTAS/red-flag and urgency rules, and doctor scoring were unchanged. Phase 5 was not started.

## Phase 4 Final Alignment: evidence-state closure

`case.semantic_extractions` remains an append-only evidence history; it is not treated as the current clinical truth snapshot. Department reasoning now builds that snapshot in two steps. `validated_semantic_evidence_history()` keeps only grounded, schema-valid, confidence-qualified evidence in chronological append order. `effective_semantic_evidence()` then applies supersession and is the source used by candidate reasoning and official KB retrieval.

For `symptom` and `accompanying_symptoms`, the supersession key is `(field, normalized concept)`. A list-valued accompanying-symptom extraction is expanded concept by concept, so a later `胸痛 / absent` replaces only the earlier `胸痛 / present` while an unrelated `頭暈 / present` remains current. A newer valid `absent` or `uncertain` assertion can replace an older `present`, and a newer valid `present` can replace an older denial. Malformed, ungrounded, or below-threshold evidence never enters the validated history and therefore cannot erase an older valid fact. This state update uses structured assertion and append order only; no Chinese negation, contrast, synonym, or revision-language parser was added.

For non-concept context fields, the latest accepted value per field is current. The candidate AI's `accepted_semantic_evidence` now includes validated current evidence for `symptom`, `accompanying_symptoms`, `body_part`, `duration`, `severity`, and `onset`. This restores the structured location, timing, severity, and onset context that was lost when raw conversation and the mutable patient snapshot were removed from the payload. The candidate payload remains limited to `accepted_semantic_evidence`, `retrieved_official_evidence`, and `previously_asked_intents`; raw history, `patient_input`, and `clarification_evidence` are not clinical truth inputs.

Official KB retrieval remains narrower than candidate context. It only looks up current `symptom` or `accompanying_symptoms` concepts whose assertion is `present` and whose semantic status is `available` or `partial`. `body_part`, `duration`, `severity`, and `onset` may inform candidate comparison but do not become symptom-to-department facts. Clarification evidence remains workflow grounding only and is never raw-scanned for KB concepts.

The batch semantic extraction contract now applies the same assertion gate to both `symptom` and `accompanying_symptoms`: any nonempty medical concept requires `present`, `absent`, or `uncertain`. Missing or invalid assertion fails closed. The prompt states this requirement explicitly and continues to require the shortest directly supporting contiguous verbatim source span.

New regressions cover present-to-absent, absent-to-present, present-to-uncertain, low-confidence non-supersession, independent list-concept supersession, latest non-concept context, all six candidate-context fields, present-concept-only retrieval, and missing/invalid batch accompanying-symptom assertions. Focused evidence/candidate/assertion tests passed **64 tests with 44 subtests**. Phase 4.1, Phase 4.2 hardening, Phase 4.2.2 clarification focus, and batch-flow focused regressions passed **95 tests with 69 subtests**.

The complete Backend suite was run with `CEREBRAS_API_KEY` blank and no real provider calls:

```powershell
$env:CEREBRAS_API_KEY=''
.\.venv\Scripts\python.exe -m pytest -q --tb=short -p no:cacheprovider
```

Final result: **533 passed, 7 warnings, 324 subtests passed**. The warnings are existing FastAPI/Starlette deprecations.

This is Phase 4 code-level evidence-state closure only. It does not claim real Cerebras, real SQL, or Tailscale live acceptance. Android, SQL schema/data, official KB records and mappings, TTAS/red-flag policy, urgency scoring, doctor scoring, accessibility, ASR/TTS, and the Schedule contract were not changed. Phase 5 was not started.

## Phase 4 live fix: grounded source and normalized concept retrieval

A real Cerebras and live SQL/Tailscale test exposed a retrieval-surface mismatch. For `我最近有血尿，已經兩天了`, the provider returned a valid present symptom with grounded `source_text=血尿` but normalized it as `hematuria`. The official KB concept remains `血尿`, so retrieval that considered only `normalized_value` incorrectly produced no official evidence and left the department unresolved.

Official KB retrieval now builds two normalized lexical surfaces from each current effective, confidence-qualified, grounded `present` symptom concept: the AI `normalized_value` and the original grounded `source_text`. A KB concept may match either surface using the existing lexical behavior. This does not translate `hematuria`, add a bilingual synonym dictionary, infer medical meaning from arbitrary history, or introduce fuzzy matching. It only broadens lookup over the two values already contained in validated structured evidence after assertion, semantic-status, confidence, grounding, and supersession gates have passed.

The original `source_text` remains the provenance written to `patient_source_text`, including when the normalized surface produced the KB match. If both surfaces match the same Department/source/concept record, the existing `(dept_id, patient_source_text, source_id, concept)` key emits one record. `absent` and `uncertain` evidence are rejected before either surface is considered. Retrieval still starts from effective evidence, so a later valid denial supersedes an older present fact and raw conversation history cannot restore it.

Regressions cover the live `血尿 -> hematuria` variation resolving to the official `腎臟科 / dept_id 1242` candidate, normalized-only `尿尿很痛 -> 小便疼痛`, absent and uncertain literal source blocking, duplicate-surface deduplication, and present-to-absent supersession. Focused Department evidence and Phase 4 convergence tests passed **49 tests with 17 subtests**. The complete Backend suite, run with `CEREBRAS_API_KEY` blank, passed **539 tests with 324 subtests** and 7 existing FastAPI/Starlette deprecation warnings. This remains a narrow Phase 4 live fix; production KB coverage and records were not expanded, and Phase 5 was not started.
