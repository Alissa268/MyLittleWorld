# Phase 7 Natural Chat and Bounded Safety Clarification

> **2026-10-27 更新**
>
> legacy batch 路徑、`BATCH_TRIAGE_ENABLED`、`BATCH_EXTRACTION_PROVIDER` 已在 commit
> `8656314` 移除。`legacy_batch`（request）與 `question_batch`（response）只保留為
> 相容欄位；`POST /chat` 只要帶 `answers` 一律回 HTTP 400。以下為當時的歷史紀錄，
> 其中提到上述設定與相容路徑的段落已不再適用。

## Scope and Authority

This repair follows `修改計畫.md` Phase 1/2/5/7 and the Phase 7 live-blocker task.
It does not change official TTAS rules, Department KB/canonical mappings, SQL
identities, recommendation ranking, or Accessibility matching/overlay algorithms.
Phase 8 is not started. Local tests do not establish 310/phone live acceptance.

## Primary API Contract

- `POST /chat {"visit_type":"initial"}` (or `followup`) creates a case without
  requiring batch answers. Its initial prompt invites a symptom description.
- Subsequent natural messages use `case_id`, `message`, and the existing visit type.
  New Android start/send paths do not route text through `question_batch` keys.
- `question_batch` remains an additive-compatible response field, empty on the
  natural path. Explicit `legacy_batch=true` / keyed `answers` use the existing
  compatibility path only when `BATCH_TRIAGE_ENABLED=true`.
- Natural turns, including safety replies, use the SAME Turn Interpreter for
  semantic extractions, pending answer, TTAS evidence and time preferences.
  Backend still validates schema, grounding, assertion, confidence and identities.
- Accepted evidence is applied before Department recomputation. The existing
  clarification planner generates one useful next question; it cannot clear
  pending state or complete safety. No standalone pending-answer classifier or
  additional reply-polishing model call is added. Existing conditional repair
  of an incomplete clinical interpretation remains bounded.
- No-input confirmation/status requests preserve a completed workflow rather
  than restarting symptom collection. Existing urgent terminal warnings remain
  Backend-only, and `/recommend` continues to reject urgent/unsafe cases.

## Safety Loop Root Cause and Repair

Previously, an unaccepted safety answer resulted in the canonical full checklist
being assigned again. Safety turns did not increment the ordinary turn counter,
and there was no independent limit on these unsuccessful attempts. Android's
batch-driven entry/send path also bypassed the natural chat flow. A configured
key alone was not proof that the SDK/client could initialize. These are code-level
causes; the exact provider response in the 310 incident has not been captured here.

- First safety entry retains the existing Backend canonical question and priority.
- Every substantive safety reply attempts Turn Interpretation when the client is
  initialized. Client readiness is not a promise of successful provider/network
  access; provider failures still fail closed.
- A negative screen requires matching `safety_screen` intent, current-turn literal
  grounding, finite in-range confidence meeting the existing threshold, status
  `answered`, and assertion `absent`. The prompt defines this as denial of the
  WHOLE canonical screen, not just one focused follow-up. Partial denial,
  uncertainty, off-topic complaints and invalid output do not complete it.
- Positive pending-answer assertion alone does not assign urgency: validated TTAS
  evidence and the existing deterministic official evaluator remain authoritative.
- For an incomplete answer, the planner receives the accepted evidence, canonical
  safety context and validation outcome and proposes one focused safety question.
  Repeating the canonical checklist or an already asked question is rejected.
- A nonmedical retry message handles missing/invalid planner output. Three
  unsuccessful safety answers terminate in `clarification_status=unresolved`,
  no next question, `is_complete=false`, and no ordinary appointment confirmation.
  Unknown is never set to negative. All safety answers count toward conversation
  diagnostics as well as this independent attempt cap.
- Complete grounded denial reconnects the existing Department reasoning/planning
  pipeline. No new symptom, negation, urgency score or medical threshold is added.

Debug fields `safety_answer_attempts` and `safety_answer_validation` contain only
attempt counts and machine reasons. Logs distinguish no key, SDK/initialization
failure, provider timeout, invalid JSON, rejected pending schema/grounding, and
deterministic fallback. They do not log patient answer spans or credentials.
`ai_reply_ai_called=false` is expected: natural question generation is logged under
`conversation_clarification`, not the deterministic status reply wrapper.

## Android Presentation and Preserved Contracts

- Backend `reply` / `next_question` and workflow state drive chat; consecutive
  duplicate assistant bubbles are suppressed. Chat content is inset below the
  existing status bar/header controls.
- Doctor cards/details show SQL specialty tags and actual appointment date/session,
  not locally inferred symptom-to-specialty relationships, score stars, or
  unsupported time-match claims. Legacy free-form reason strings are not promoted
  into clinical claims when structured provenance is unavailable.
- Two concrete-Schedule recommendation lists and formal IDs remain unchanged.
- History resume, permanent completed-history read-only status, final external
  launch completion, revalidation, empty `steps=[]`, Quick Search and Accessibility
  behavior remain covered by the Android regression suites.

## Runtime Configuration

The actual file is `backend/.env`, loaded by `app/config.py`.
Only local `BATCH_TRIAGE_ENABLED` was changed from true to false; existing key,
SQL credentials, model, timeout, voice settings and doctor-scoring setting were
retained. No 310 file has been accessed or modified.

| Setting | Actual role |
| --- | --- |
| `AI_PROVIDER` / `CEREBRAS_MODEL` | Runtime JSON calls use the existing Cerebras `gpt-oss-120b` path. These do not turn SDK initialization/network failures into success. |
| `CEREBRAS_API_KEY` | Required secret; only presence/readiness is checked or reported. |
| `AI_TIMEOUT_SECONDS` | Existing per-provider call timeout, retained locally. |
| `AI_REPLY_GENERATION_ENABLED` | Deprecated compatibility setting; neither value disables the natural clarification planner or enables a separate model call. |
| `AI_DOCTOR_SCORING_ENABLED` | Existing Phase 6 specialty scorer switch, unchanged. |

### 310 Restart Instructions

Deploy the reviewed source/APK first. In the existing Backend terminal, stop only
that Backend with Ctrl+C. Preserve its existing `.env`, secrets, SQL settings,
Tailscale settings, host and port. The following overrides only nonsecret process
settings for this terminal; it does not overwrite `.env`:

```powershell
cd C:\Users\10650\Downloads\Total\New_Android_Backend
$env:AI_PROVIDER='cerebras'
$env:CEREBRAS_MODEL='gpt-oss-120b'
$port = [int](Read-Host 'Enter the existing Backend port (unchanged)')
.\scripts\run-backend.ps1 -Port $port
```

If 310 uses a different folder or a nondefault bind host, use its existing path
and the script's `-HostName` argument. Do not paste test commands clearing the API
key into the production terminal. Process overrides must be repeated on a new
terminal, or the same nonsecret settings can be retained in 310's backed-up `.env`.

## Validation and Remaining Work

- Focused tests exercise the reproduced headache/partial-denial/off-topic loop,
  complete denial, ungrounded/low-confidence/wrong-intent answers, unavailable AI,
  timeout/invalid JSON, time-only turns, multi-turn symptom revision, Phase 4/5
  gates and history resume. Providers/SQL are mocked; the test suite blocks real
  Cerebras SDK and real SQL connection attempts.
- Stale tests expecting unavailable AI to infer free-text fields now assert that
  no such facts are invented. Explicit keyed legacy tests are labeled/configured
  as compatibility tests. An expired followup fixture uses a future date without
  weakening production rules.
- The relative-days order failure was independently reproduced with a frozen
  Friday reference. A small existing normalizer fix preserves Sunday then Monday;
  no Phase 6 time model or ranking algorithm was redesigned.
- Legacy checklist/normalization helpers still exist for explicit compatibility;
  their deletion is not claimed. The full MD cleanup and later Phase 7 UX work
  (one doctor card/multiple slots, richer preference display) are not complete.
- 310 must verify actual Cerebras extraction and focused questions, timeout/log
  behavior, real SQL candidates/schedules and revalidation. A phone must verify
  chat header layout, Mandarin/Taiwanese text submission, history across restart,
  Quick Search, final external launch and Accessibility red-frame guidance.
- This folder is not a standalone Git checkout: Git resolves to the unrelated
  user-home repository. No branch switch, reset, commit or push was performed;
  a review-baseline Git diff cannot be truthfully reported from this folder.

### Executed Local Results

| Check | Passed | Failed | Errors | Skipped |
| --- | ---: | ---: | ---: | ---: |
| Focused Backend (9 files) | 165 + 33 subtests | 0 | 0 | 0 |
| Full Backend | 664 + 322 subtests | 0 | 0 | 0 |
| Android `:app:testDebugUnitTest` (14 suites) | 165 | 0 | 0 | 0 |

Backend reports seven existing FastAPI/Starlette deprecation warnings.
`:app:assembleDebug` succeeded; output is `android/app/build/outputs/apk/debug/app-debug.apk`.
Backend XML reports are in ignored `.pytest_tmp_phase7_targeted_report` and
`.pytest_tmp_phase7_full_report` directories. Android XML reports are in
`android/app/build/test-results/testDebugUnitTest`. These are local/mock results,
not real-provider, real-SQL or device acceptance.

### Changed File Inventory

Backend production:

- `backend/app/routes/chat.py`: natural/legacy isolation, same interpreter for
  safety, focused planner continuation and truthful branch/fallback diagnostics.
- `backend/app/schemas.py`: explicit legacy request flag and bounded safety diagnostics.
- `backend/app/services/ai_service.py`: actual SDK/client readiness checks.
- `backend/app/services/rag_triage_adapter.py`: whole-screen pending context and
  non-sensitive validation/failure logging.
- `backend/app/services/conversation_service.py`: focused safety planning and attempt cap.
- `backend/app/services/semantic_normalizer.py`: relative-day chronological ordering only.

Android (paths under `android/app/src/main/java/com/example/medicalaiguidance/`):

- `network/MedicalDtos.kt`: explicit legacy request flag, no response contract removal.
- `repository/MedicalRepository.kt`: separate natural start and explicit compatibility start.
- `viewmodel/ChatViewModel.kt`: natural message routing and duplicate reply suppression.
- `screen/ChatScreen.kt`: space below the existing floating header.
- `screen/DoctorSelectionScreen.kt`: SQL facts instead of medical keyword/star inferences.

Backend tests:

- Added `backend/tests/conftest.py` and `backend/tests/test_phase7_natural_safety.py`.
- Updated `backend/tests/test_backend_flow.py`, `test_batch_question_flow.py`,
  `test_phase2_conversation.py`, `test_phase4_department_convergence.py`,
  `test_phase42_backend_hardening.py`, `test_selective_wu_merge.py`,
  `test_semantic_refinement_gate.py`, `test_strict_keyed_acceptance.py`.

Android tests (under `android/app/src/test/java/com/example/medicalaiguidance/`):

- `RecommendationIntegrationUnitTest.kt`: conservative factual display, no score stars.
- `Phase7IntegrationRepairUnitTest.kt`: natural start/send and duplicate-message checks.
- `Phase7HistoryLifecycleUnitTest.kt`: resumed question submits natural text, preserving history protections.

Configuration/documentation:

- `.env.example`: explain compatibility-only batch and deprecated reply switch.
- `backend/.env`: local-only batch flag change, not for source control.
- This validation document: contract, safety decisions, deployment and remaining limits.
