# New_Android_Backend Phase 0 Baseline

Date: 2026-09-28  
Scope: Phase 0 only. No production feature code was intentionally changed.

## Project Structure

Working project:

- `New_Android_Backend`

Reference projects, not modified in this phase:

- `Android_Backend`
- `feat-dept-doctor-recommendation`

Top-level structure in `New_Android_Backend`:

- `backend`
- `android`
- `scripts`
- `temp`
- `.env.example`
- `.gitignore`
- `CONTRIBUTING.md`
- `README.md`

Backend routes present:

- `chat.py`
- `followup.py`
- `generate_script.py`
- `recommend.py`
- `reference.py`
- `schedules.py`
- `voice.py`

Backend services present:

- `ai_reply_generator.py`
- `ai_service.py`
- `appointment_service.py`
- `batch_extraction_service.py`
- `batch_question_service.py`
- `case_store.py`
- `chat_perf.py`
- `clarification_engine.py`
- `confidence_scoring.py`
- `department_preference_service.py`
- `field_acceptance.py`
- `fixed_sentences.py`
- `followup_service.py`
- `negation_utils.py`
- `project_smart_department_adapter.py`
- `question_specs.py`
- `quick_search_service.py`
- `rag_triage_adapter.py`
- `rule_engine.py`
- `schedule_filter.py`
- `script_service.py`
- `semantic_normalizer.py`
- `semantic_refinement_gate.py`
- `specialty_scoring.py`
- `tts_cache.py`
- `voice_client.py`
- `voice_gateway_executor.py`
- `voice_perf.py`

Backend tests present:

- `test_ai_reply_generator.py`
- `test_appointment_ranking.py`
- `test_backend_flow.py`
- `test_batch_question_flow.py`
- `test_cerebras_runtime.py`
- `test_db_adapter_mapping.py`
- `test_followup_service.py`
- `test_project_smart_department_adapter.py`
- `test_question_specs.py`
- `test_quick_search.py`
- `test_recommendation_data_access.py`
- `test_reference_routes.py`
- `test_schedule_filter.py`
- `test_selective_wu_merge.py`
- `test_semantic_ai_validation.py`
- `test_semantic_refinement_gate.py`
- `test_specialty_scoring.py`
- `test_strict_keyed_acceptance.py`
- `test_tts_cache.py`
- `test_voice_perf.py`

Android unit tests present:

- `AppointmentTypeMappingUnitTest.kt`
- `BatchTriageIntegrationUnitTest.kt`
- `CancellationAppointmentMatcherUnitTest.kt`
- `CancellationFlowPolicyUnitTest.kt`
- `ChatDecisionVisibilityUnitTest.kt`
- `ExampleUnitTest.kt`
- `FixedTriageAudioResolverTest.kt`
- `GuidanceSessionStateUnitTest.kt`
- `HistoryRepositoryUnitTest.kt`
- `QuickSearchIntegrationUnitTest.kt`
- `RecommendationIntegrationUnitTest.kt`
- `VoiceFlowUnitTest.kt`

## Commands Run

Backend environment discovery:

```powershell
pytest -q
python -m pytest -q
powershell -ExecutionPolicy Bypass -File .\scripts\test-backend.ps1
powershell -ExecutionPolicy Bypass -File .\scripts\test-backend.ps1
```

Android:

```powershell
.\gradlew.bat testDebugUnitTest --no-daemon
$env:ANDROID_HOME='C:\Users\10650\AppData\Local\Android\Sdk'; $env:ANDROID_SDK_ROOT='C:\Users\10650\AppData\Local\Android\Sdk'; .\gradlew.bat testDebugUnitTest --no-daemon
$env:ANDROID_HOME='C:\Users\10650\AppData\Local\Android\Sdk'; $env:ANDROID_SDK_ROOT='C:\Users\10650\AppData\Local\Android\Sdk'; .\gradlew.bat assembleDebug --no-daemon
```

The plain Android commands initially failed because `ANDROID_HOME` and `ANDROID_SDK_ROOT` were not set in the shell. The SDK exists at:

```text
C:\Users\10650\AppData\Local\Android\Sdk
```

The Gradle wrapper and pip dependency installation required network access. Initial sandboxed attempts failed with network permission errors; reruns with approved network access succeeded.

## Backend Test Result

Initial direct attempts:

- `pytest -q`: failed because `pytest` was not on PATH.
- `python -m pytest -q`: failed because the global `C:\Python314\python.exe` did not have `pytest` installed.
- First `powershell -ExecutionPolicy Bypass -File .\scripts\test-backend.ps1`: created `backend\.venv`, then failed while installing dependencies because sandboxed network access could not reach PyPI.

Final successful command:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\test-backend.ps1
```

Result:

- Backend unittest groups passed.
- Full pytest suite passed.
- `/health` and OpenAPI contract checks passed.

Final pytest summary:

```text
379 passed, 7 warnings, 269 subtests passed in 29.06s
Backend health and OpenAPI contract checks passed.
```

Warnings observed:

- `StarletteDeprecationWarning` from FastAPI TestClient importing Starlette TestClient.
- FastAPI `on_event` deprecation warnings in `app/main.py`.

## Android Unit Test Result

Initial command without SDK environment variables:

```powershell
.\gradlew.bat testDebugUnitTest --no-daemon
```

Result:

```text
SDK location not found. Define a valid SDK location with an ANDROID_HOME environment variable or by setting the sdk.dir path in android/local.properties.
```

Final successful command:

```powershell
$env:ANDROID_HOME='C:\Users\10650\AppData\Local\Android\Sdk'; $env:ANDROID_SDK_ROOT='C:\Users\10650\AppData\Local\Android\Sdk'; .\gradlew.bat testDebugUnitTest --no-daemon
```

Gradle result:

```text
BUILD SUCCESSFUL in 9m 40s
26 actionable tasks: 26 executed
```

Android test XML summary:

```text
121 tests, 0 failures, 0 errors, 0 skipped
```

Per-suite counts:

- `AppointmentTypeMappingUnitTest`: 7
- `BatchTriageIntegrationUnitTest`: 24
- `CancellationAppointmentMatcherUnitTest`: 10
- `CancellationFlowPolicyUnitTest`: 9
- `ChatDecisionVisibilityUnitTest`: 2
- `ExampleUnitTest`: 3
- `FixedTriageAudioResolverTest`: 3
- `GuidanceSessionStateUnitTest`: 16
- `HistoryRepositoryUnitTest`: 9
- `QuickSearchIntegrationUnitTest`: 9
- `RecommendationIntegrationUnitTest`: 16
- `VoiceFlowUnitTest`: 13

Kotlin warnings observed:

- Deprecated `Icons.Filled.VolumeUp`, `Icons.Filled.ArrowBack`, `Icons.Filled.Launch`, and `Icons.Outlined.HelpOutline` usages.
- Deprecated Java `scaledDensity` field usage in `OverlayManager.kt`.

## Android Build Result

Successful command:

```powershell
$env:ANDROID_HOME='C:\Users\10650\AppData\Local\Android\Sdk'; $env:ANDROID_SDK_ROOT='C:\Users\10650\AppData\Local\Android\Sdk'; .\gradlew.bat assembleDebug --no-daemon
```

Gradle result:

```text
BUILD SUCCESSFUL in 5m 4s
37 actionable tasks: 17 executed, 20 up-to-date
```

Output files observed:

- `android/app/build/outputs/apk/debug/app-debug.apk`
- `android/app/build/outputs/apk/debug/output-metadata.json`

Build warning observed:

```text
Unable to strip the following libraries, packaging them as they are: libandroidx.graphics.path.so.
```

## AI Key Not Configured Behavior

`.env.example` leaves these AI keys empty:

- `CEREBRAS_API_KEY=`
- `DB_PASSWORD=`

Current defaults from `backend/app/config.py`:

- `AI_PROVIDER=cerebras`
- `CEREBRAS_MODEL=gpt-oss-120b`
- `BATCH_TRIAGE_ENABLED=false`
- `AI_REPLY_GENERATION_ENABLED=false`
- `AI_DOCTOR_SCORING_ENABLED=false`

Observed code behavior:

- `ai_service.runtime_ai_available()` returns false when `CEREBRAS_API_KEY` is empty.
- Cerebras initialization is skipped when `CEREBRAS_API_KEY` is not configured.
- If a Cerebras completion is requested without an initialized client, the AI call raises `RuntimeError("Cerebras AI is not initialized")`.
- Doctor specialty AI scoring is disabled by default and falls back to deterministic scoring.
- Test output showed expected fallback paths such as provider failure handling, disabled AI paths, and deterministic replies.

No live AI key was required for the passing Backend or Android baseline tests.

## Generated Local Artifacts

Phase 0 generated local test/build artifacts:

- `backend/.venv`
- `android/.gradle`
- `android/app/build`
- Gradle user cache outside the project

These are test/build environment artifacts, not production source changes.

## Git / Diff Note

`git status --short` could not be used in this workspace during Phase 0. It failed with:

```text
fatal: cannot change to 'C:/Users/10650'
```

The `New_Android_Backend` folder did not show a visible `.git` directory from filesystem listing. File-level Phase 0 source changes were intentionally limited to this baseline document.

## Phase 0 Conclusion

Baseline status:

- Backend tests: passed after creating the project venv and installing test dependencies.
- Backend health/OpenAPI checks: passed.
- Android unit tests: passed when `ANDROID_HOME` and `ANDROID_SDK_ROOT` were set for the command.
- Android `assembleDebug`: passed when `ANDROID_HOME` and `ANDROID_SDK_ROOT` were set for the command.

No Phase 1 work was started. No production feature logic, Android UI, AI logic, DB schema, or reference project files were intentionally modified.
