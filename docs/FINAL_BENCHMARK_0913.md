# FINAL BENCHMARK — 2026-09-13

> All measurements in this report were newly collected from the locked working tree below. No prior benchmark value was imported.

## A. 測試版本與環境

| Item | Value |
|---|---|
| Benchmark window (Asia/Taipei) | 2026-09-13T03:29:52.957918+08:00 → 2026-09-13T04:04:05.430+08:00 |
| Git branch | `codex0909` |
| Git HEAD | `d6c14eb8fe0ec9cc9801e270ac249c33f2d29235` |
| HEAD commit time | `2026-09-13T03:03:27+08:00` |
| Git status at benchmark start | clean (captured before benchmark artifacts were created) |
| Git status after benchmark | ?? android/app/src/androidTest/java/com/example/medicalaiguidance/FinalAndroidE2E0913InstrumentedTest.kt<br>?? android/app/src/androidTest/java/com/example/medicalaiguidance/FinalBenchmark0913InstrumentedTest.kt<br>?? docs/FINAL_BENCHMARK_0913.md<br>?? docs/final_benchmark_raw_0913.csv<br>?? temp/benchmark_0913/ |
| Python | 3.12.14 (isolated `temp/benchmark_0913/.venv`) |
| Android variant | `debug` |
| APK | `C:\Users\TKU\Downloads\test02-feature-frontend-backend\android\app\build\outputs\apk\debug\app-debug.apk` |
| APK size | 30,243,276 bytes / 28.842 MiB |
| Device | `emulator-5554`, `sdk_gphone16k_x86_64` / Medium_Phone AVD |
| Android | Android 17, SDK 37 |
| App Backend URL | `http://100.64.116.35:8080` |
| Benchmark Backend endpoints | loopback ephemeral port for host HTTP; `10.0.2.2:18080` for device tests |
| Active AI | `cerebras` / `gpt-oss-120b` |
| Voice Gateway | `http://localhost:8000/…`, service router (`taiwanese_asr`, `chinese_tts`, `taiwanese_tts`) |
| Working tree basis | HEAD plus benchmark-only instrumentation/docs created during this run; production source was not changed |

### Methodology

- Every performance row is preserved in `docs/final_benchmark_raw_0913.csv`; summary values are recomputed from that file.
- `p95` uses nearest rank: sort ascending and select rank `ceil(0.95 × n)` (1-based).
- Success rate uses all attempts. Latency statistics use successful runs only; `Latency n` makes that denominator explicit.
- Samples with `n < 30` are demonstration samples, not SLA evidence.
- Backend HTTP, AI provider, Voice Gateway/model, SQL adapter, local MediaPlayer, and Android elapsed time are never combined into one metric.
- SQL adapter time includes connection, query, row fetch, and mapping; it is not SQL Server engine execution time.
- AI benchmark sessions/cases were unique. Batch and department direct calls share one long-lived event loop, matching runtime client lifetime.
- Production code, DB schema/data, prompts, Voice Gateway, and Android main runtime were not modified.
- Invalid early harness batches (cross-event-loop async client reuse, obsolete return-visit success predicate, and blank Gateway instrumentation) were removed before this raw dataset was finalized.

### Current runtime trace

```text
A. Initial/follow-up triage
Android → POST /chat → deterministic extraction → optional Cerebras semantic refinement
        → rule engine → missing fields → optional Cerebras department selection from 133 DB candidates
        → exact tuple validation → confirmation

B. Initial/follow-up recommendation
POST /recommend → DepartmentResult.dept_id exact route → SQL Schedule
→ visit type / availability / cutoff / active-doctor filters
→ deterministic specialty scoring → specialty_first / time_first (max 5 each)

C. Return visit
POST /followup/recommend → exact original dept_id + original doctor identity
→ SQL Schedule → availability filter → return-visit recommendations

D. Quick Search
GET /schedules/search(dept_id,date,period) → SQL → deterministic selection
→ POST /generate_script → DB revalidation for quick-search item

E. Fixed voice
ChatViewModel → FixedTriageAudioResolver exact trim match → R.raw → MediaPlayer
(/voice/tts is not called)

F. Dynamic voice
resolver miss → TtsSession → POST /voice/tts → Voice Gateway → base64 audio → MediaPlayer

G. Chinese ASR
Android RecognizerIntent(ACTION_RECOGNIZE_SPEECH, Locale.TAIWAN); no Backend call

H. Taiwanese ASR
Android AudioRecorder (16 kHz mono WAV) → POST /voice/asr
→ Voice Gateway taiwanese_asr → transcript
```

### Raw-data audit and rerun decision

Raw CSV audit: 1,045 performance rows before engineering-fact rows are appended; all timestamps belong to this run date, required keys are present, and `(benchmark_group, subgroup, run, layer)` has zero duplicates.

| Benchmark group | Subgroup | Expected n | Actual valid n | Success | Failure | Needs rerun |
|---|---|---:|---:|---:|---:|---|
| Deterministic /chat | `http_total` | 30 | 30 | 30 | 0 | NO |
| Cerebras batch extraction | `provider` | 10 | 10 | 10 | 0 | NO |
| Cerebras semantic refinement | `provider` | 10 | 10 | 10 | 0 | NO |
| Cerebras department detection | `provider` | 10 | 10 | 8 | 2 | NO |
| Taiwanese ASR | `backend_http` | 20 | 20 | 20 | 0 | NO |
| Taiwanese ASR | `gateway` | 20 | 20 | 20 | 0 | NO |
| Local prerecorded audio | `zh_short` | 20 | 20 | 20 | 0 | NO |
| Local prerecorded audio | `zh_long_red_flag` | 20 | 20 | 20 | 0 | NO |
| Local prerecorded audio | `taigi_short` | 20 | 20 | 20 | 0 | NO |
| Local prerecorded audio | `taigi_long_red_flag` | 20 | 20 | 20 | 0 | NO |
| Initial /recommend | `http_total` | 30 | 30 | 30 | 0 | NO |
| Follow-up /recommend | `http_total` | 30 | 30 | 30 | 0 | NO |
| Return /followup/recommend | `http_total` | 30 | 30 | 30 | 0 | NO |
| Quick Search | `department_date` | 20 | 20 | 20 | 0 | NO |
| Quick Search | `department_period` | 20 | 20 | 20 | 0 | NO |
| Quick Search | `complete_conditions` | 20 | 20 | 20 | 0 | NO |
| /generate_script | `initial_recommendation` | 30 | 30 | 30 | 0 | NO |
| /generate_script | `followup_recommendation` | 30 | 30 | 30 | 0 | NO |
| /generate_script | `quick_search` | 30 | 30 | 30 | 0 | NO |
| SQL adapter | `initial_schedule` | 20 | 20 | 20 | 0 | NO |
| SQL adapter | `return_visit_schedule` | 20 | 20 | 20 | 0 | NO |
| SQL adapter | `quick_search` | 20 | 20 | 20 | 0 | NO |
| Android E2E | `initial` | 5 | 5 | 5 | 0 | NO |
| Android E2E | `followup` | 5 | 5 | 5 | 0 | NO |
| Android E2E | `quick_search` | 5 | 5 | 5 | 0 | NO |
| Android E2E | `return_visit` | 5 | 5 | 5 | 0 | NO |
| Android Voice | `taiwanese_asr_user_perceived` | 5 | 5 | 5 | 0 | NO |
| Android Voice | `dynamic_chinese_tts_user_perceived` | 5 | 5 | 5 | 0 | NO |
| Android Voice | `dynamic_taiwanese_tts_user_perceived` | 5 | 5 | 5 | 0 | NO |
| Dynamic Chinese TTS | `cache_miss_http` | 10 | 10 | 10 | 0 | NO |
| Dynamic Chinese TTS | `cache_hit_http` | 20 | 20 | 20 | 0 | NO |
| Dynamic Chinese TTS | `cache_miss_gateway` | 10 | 10 | 10 | 0 | NO |
| Dynamic Taigi TTS | `cache_miss_http` | 10 | 10 | 10 | 0 | NO |
| Dynamic Taigi TTS | `cache_hit_http` | 20 | 20 | 20 | 0 | NO |
| Dynamic Taigi TTS | `cache_miss_gateway` | 10 | 10 | 10 | 0 | NO |

Every planned series reached its expected attempt count. The two department connection failures are valid production-adapter outcomes, so that group is complete and was not rerun.

### Coverage status

| Area | Status | Notes |
|---|---|---|
| Deterministic `/chat` n=30 | COMPLETE | HTTP plus case preparation, parse, rule, response build |
| Cerebras batch n=10 | COMPLETE | real adapter, unique cases |
| Cerebras semantic n=10 | COMPLETE | formal `/chat` path, all inputs bypassed deterministic acceptance |
| Cerebras department n=10 | COMPLETE_WITH_FAILURES | exact tuple checked; 2 real APIConnectionError failures retained |
| Chinese ASR | BLOCKED | RecognizerIntent cannot receive deterministic fixture in this environment; no Backend surrogate used |
| Taiwanese ASR | COMPLETE | Backend n=20, Gateway/model n=20, device upload-to-transcript n=5 |
| Fixed Chinese/Taigi audio | COMPLETE | 4 groups × n=20; local exact hit; `/voice/tts=false` |
| Dynamic Chinese/Taigi TTS | COMPLETE | each HTTP miss n=10, hit n=20, Gateway n=10, Android cold click n=5 |
| Initial/follow-up/return recommendation | COMPLETE | real SQL, 30 each |
| Quick Search | COMPLETE | 3 conditions × n=20; API requires dept/date/period together |
| Generate script | COMPLETE | 3 sources × n=30 using actual recommendations |
| SQL adapters | COMPLETE | 3 query paths × n=20 |
| Android E2E | COMPLETE_WITH_SCOPE_NOTE | 4 paths × n=5, device repository-to-DTO; Compose render excluded |
| Waveform RMS/peak/silence | NOT_MEASURED | ffmpeg/ffprobe unavailable; container/codec/duration and playback smoke passed |

## B. Android user-perceived

| Benchmark | Subgroup | Layer | n | Success | Failure | Latency n | Mean ms | Median ms | p95 ms | Min ms | Max ms | Success rate | Avg result count |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Android E2E | `followup` | `android_user_perceived` | 5 | 5 | 0 | 5 | 797.224 | 712.666 | 1006.493 | 689.066 | 1006.493 | 100.0% | 10.00 |
| Android E2E | `initial` | `android_user_perceived` | 5 | 5 | 0 | 5 | 1280.235 | 769.048 | 2296.254 | 706.960 | 2296.254 | 100.0% | 10.00 |
| Android E2E | `quick_search` | `android_user_perceived` | 5 | 5 | 0 | 5 | 21.094 | 17.331 | 37.252 | 16.687 | 37.252 | 100.0% | 1.00 |
| Android E2E | `return_visit` | `android_user_perceived` | 5 | 5 | 0 | 5 | 13.592 | 13.706 | 15.396 | 12.147 | 15.396 | 100.0% | 1.00 |
| Android Voice | `dynamic_chinese_tts_user_perceived` | `android_user_perceived` | 5 | 5 | 0 | 5 | 12230.208 | 11873.631 | 13106.639 | 11779.099 | 13106.639 | 100.0% | 1.00 |
| Android Voice | `dynamic_taiwanese_tts_user_perceived` | `android_user_perceived` | 5 | 5 | 0 | 5 | 4142.235 | 4167.086 | 4248.335 | 4020.944 | 4248.335 | 100.0% | 1.00 |
| Android Voice | `taiwanese_asr_user_perceived` | `android_user_perceived` | 5 | 5 | 0 | 5 | 2619.519 | 2497.018 | 3162.197 | 2440.473 | 3162.197 | 100.0% | 1.00 |
| Local prerecorded audio | `taigi_long_red_flag` | `android_user_perceived` | 20 | 20 | 0 | 20 | 23.605 | 23.478 | 27.375 | 20.483 | 29.144 | 100.0% | 1.00 |
| Local prerecorded audio | `taigi_short` | `android_user_perceived` | 20 | 20 | 0 | 20 | 22.956 | 22.730 | 26.497 | 19.512 | 26.546 | 100.0% | 1.00 |
| Local prerecorded audio | `zh_long_red_flag` | `android_user_perceived` | 20 | 20 | 0 | 20 | 21.982 | 21.751 | 24.415 | 19.121 | 25.887 | 100.0% | 1.00 |
| Local prerecorded audio | `zh_short` | `android_user_perceived` | 20 | 20 | 0 | 20 | 25.635 | 23.446 | 28.311 | 16.629 | 72.302 | 100.0% | 1.00 |

Local audio timing is resolver lookup plus synchronous `MediaPlayer.create/start` return. Dynamic TTS device timing is a cold `TtsSession.get` through the Gateway plus base64 decode, `MediaPlayer.prepare`, and `start`. Taiwanese ASR device timing starts when the prepared WAV upload begins and ends when the transcript DTO is available; real microphone stop overhead and Compose rendering are excluded.

Chinese ASR is `AUTOMATION_NOT_AVAILABLE`. No latency is reported because the formal runtime is an Android `RecognizerIntent` and this emulator harness cannot inject deterministic speech into it.

The Android E2E measurements cover device → `MedicalRepository` → HTTP → DTO only. UI/Compose render is not included.

## C. Local prerecorded audio

| Benchmark | Subgroup | Layer | n | Success | Failure | Latency n | Mean ms | Median ms | p95 ms | Min ms | Max ms | Success rate | Avg result count |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Local prerecorded audio | `taigi_long_red_flag` | `android_user_perceived` | 20 | 20 | 0 | 20 | 23.605 | 23.478 | 27.375 | 20.483 | 29.144 | 100.0% | 1.00 |
| Local prerecorded audio | `taigi_short` | `android_user_perceived` | 20 | 20 | 0 | 20 | 22.956 | 22.730 | 26.497 | 19.512 | 26.546 | 100.0% | 1.00 |
| Local prerecorded audio | `zh_long_red_flag` | `android_user_perceived` | 20 | 20 | 0 | 20 | 21.982 | 21.751 | 24.415 | 19.121 | 25.887 | 100.0% | 1.00 |
| Local prerecorded audio | `zh_short` | `android_user_perceived` | 20 | 20 | 0 | 20 | 25.635 | 23.446 | 28.311 | 16.629 | 72.302 | 100.0% | 1.00 |

Every local-audio row was an exact resolver hit with `/voice/tts` not called. Timing is resolver lookup plus synchronous `MediaPlayer.create/start` return.

## D. Backend HTTP

| Benchmark | Subgroup | Layer | n | Success | Failure | Latency n | Mean ms | Median ms | p95 ms | Min ms | Max ms | Success rate | Avg result count |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Cerebras semantic refinement | `http_total` | `backend_http` | 10 | 10 | 0 | 10 | 837.513 | 789.436 | 1364.546 | 521.276 | 1364.546 | 100.0% | 1.10 |
| Deterministic /chat | `http_total` | `backend_http` | 30 | 30 | 0 | 30 | 3.163 | 2.973 | 3.993 | 2.765 | 7.032 | 100.0% | 1.00 |
| Dynamic Chinese TTS | `cache_hit_http` | `backend_http` | 20 | 20 | 0 | 20 | 6.501 | 6.362 | 6.748 | 6.135 | 8.786 | 100.0% | 58601.00 |
| Dynamic Chinese TTS | `cache_miss_http` | `backend_http` | 10 | 10 | 0 | 10 | 12219.007 | 11869.561 | 13847.589 | 11107.940 | 13847.589 | 100.0% | 58017.30 |
| Dynamic Taigi TTS | `cache_hit_http` | `backend_http` | 20 | 20 | 0 | 20 | 6.383 | 6.321 | 6.894 | 5.905 | 7.441 | 100.0% | 59379.00 |
| Dynamic Taigi TTS | `cache_miss_http` | `backend_http` | 10 | 10 | 0 | 10 | 3919.163 | 3910.079 | 4478.426 | 3690.380 | 4478.426 | 100.0% | 58039.80 |
| Return /followup/recommend | `http_total` | `backend_http` | 30 | 30 | 0 | 30 | 7.883 | 6.824 | 8.162 | 5.990 | 35.438 | 100.0% | 1.00 |
| /generate_script | `followup_recommendation` | `backend_http` | 30 | 30 | 0 | 30 | 0.964 | 0.942 | 1.151 | 0.848 | 1.178 | 100.0% | 10.00 |
| /generate_script | `initial_recommendation` | `backend_http` | 30 | 30 | 0 | 30 | 1.022 | 0.958 | 1.634 | 0.871 | 1.812 | 100.0% | 10.00 |
| /generate_script | `quick_search` | `backend_http` | 30 | 30 | 0 | 30 | 6.920 | 6.755 | 8.079 | 6.263 | 9.862 | 100.0% | 10.00 |
| Quick Search | `complete_conditions` | `backend_http` | 20 | 20 | 0 | 20 | 13.049 | 13.078 | 13.874 | 12.308 | 14.012 | 100.0% | 2.00 |
| Quick Search | `department_date` | `backend_http` | 20 | 20 | 0 | 20 | 14.313 | 13.121 | 23.194 | 12.445 | 23.527 | 100.0% | 1.00 |
| Quick Search | `department_period` | `backend_http` | 20 | 20 | 0 | 20 | 13.533 | 12.999 | 14.371 | 12.442 | 22.486 | 100.0% | 1.00 |
| Follow-up /recommend | `http_total` | `backend_http` | 30 | 30 | 0 | 30 | 11.196 | 10.785 | 13.463 | 10.031 | 14.470 | 100.0% | 10.00 |
| Initial /recommend | `http_total` | `backend_http` | 30 | 30 | 0 | 30 | 11.216 | 10.628 | 13.714 | 9.871 | 23.800 | 100.0% | 10.00 |
| Taiwanese ASR | `backend_http` | `backend_http` | 20 | 20 | 0 | 20 | 2417.760 | 2407.372 | 2464.743 | 2365.509 | 2692.345 | 100.0% | 1.00 |

The deterministic `/chat` test used a fresh case for every run and made zero AI calls. The dynamic TTS text was obtained from the current `/chat` runtime: `目前建議科別為 一般骨科，請確認後取得推薦掛號方案。`

## E. Cerebras provider

| Benchmark | Subgroup | Layer | n | Success | Failure | Latency n | Mean ms | Median ms | p95 ms | Min ms | Max ms | Success rate | Avg result count |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Cerebras batch extraction | `provider` | `ai_provider` | 10 | 10 | 0 | 10 | 717.580 | 708.350 | 895.400 | 564.900 | 895.400 | 100.0% | 0.00 |
| Cerebras department detection | `provider` | `ai_provider` | 10 | 8 | 2 | 8 | 940.938 | 820.500 | 1732.900 | 710.400 | 1732.900 | 80.0% | 1.00 |
| Cerebras semantic refinement | `provider` | `ai_provider` | 10 | 10 | 0 | 10 | 826.900 | 778.750 | 1353.500 | 510.400 | 1353.500 | 100.0% | 1.10 |

- Provider/model for every measured AI call: Cerebras / `gpt-oss-120b`.
- Batch extraction: 10/10 provider and schema-valid outcomes.
- Semantic refinement: 10/10 through the formal `/chat` semantic branch.
- Department selection: 8/10; two `APIConnectionError` failures are retained. Latency statistics above use the eight successful calls, while the success rate denominator remains ten attempts. Every successful result was one unique exact `(dept_id,parent,child)` from the 133 DB candidates.
- Failed AI calls do not corrupt state: semantic keeps deterministic data; department falls back to deterministic/unresolved behavior.

## F. Voice Gateway / model

| Benchmark | Subgroup | Layer | n | Success | Failure | Latency n | Mean ms | Median ms | p95 ms | Min ms | Max ms | Success rate | Avg result count |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Dynamic Chinese TTS | `cache_miss_gateway` | `voice_gateway_model` | 10 | 10 | 0 | 10 | 12437.951 | 12290.312 | 14054.128 | 11806.808 | 14054.128 | 100.0% | 58411.40 |
| Dynamic Taigi TTS | `cache_miss_gateway` | `voice_gateway_model` | 10 | 10 | 0 | 10 | 3949.942 | 3893.285 | 4409.837 | 3547.891 | 4409.837 | 100.0% | 59414.40 |
| Taiwanese ASR | `gateway` | `voice_gateway_model` | 20 | 20 | 0 | 20 | 2399.634 | 2391.180 | 2442.486 | 2365.072 | 2528.498 | 100.0% | 1.00 |

The fixed ASR fixture produced a non-empty transcript in every run. The observed run-1 reference was `喔`; all Backend runs matched it after whitespace normalization. This is consistency against this run's reference, not a linguistically adjudicated accuracy corpus.

## G. SQL adapter

| Benchmark | Subgroup | Layer | n | Success | Failure | Latency n | Mean ms | Median ms | p95 ms | Min ms | Max ms | Success rate | Avg result count |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Follow-up recommendation stages | `sql_retrieval` | `sql_adapter` | 30 | 30 | 0 | 30 | 6.826 | 6.736 | 7.413 | 6.352 | 7.612 | 100.0% | 16.00 |
| Initial recommendation stages | `sql_retrieval` | `sql_adapter` | 30 | 30 | 0 | 30 | 7.139 | 6.798 | 8.597 | 6.118 | 15.791 | 100.0% | 17.00 |
| SQL adapter | `initial_schedule` | `sql_adapter` | 20 | 20 | 0 | 20 | 6.514 | 6.433 | 6.956 | 5.964 | 7.243 | 100.0% | 17.00 |
| SQL adapter | `quick_search` | `sql_adapter` | 20 | 20 | 0 | 20 | 6.698 | 6.733 | 7.071 | 6.226 | 7.138 | 100.0% | 2.00 |
| SQL adapter | `return_visit_schedule` | `sql_adapter` | 20 | 20 | 0 | 20 | 5.628 | 5.522 | 6.438 | 5.166 | 6.480 | 100.0% | 2.00 |

Benchmark department: `dept_id=1298`, parent `外科系`, child `一般骨科`. Initial/follow-up recommendations and return visits kept exact department routing; return visit additionally validated doctor `7149` exactly.

## H. Recommendation

### HTTP totals

| Benchmark | Subgroup | Layer | n | Success | Failure | Latency n | Mean ms | Median ms | p95 ms | Min ms | Max ms | Success rate | Avg result count |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Return /followup/recommend | `http_total` | `backend_http` | 30 | 30 | 0 | 30 | 7.883 | 6.824 | 8.162 | 5.990 | 35.438 | 100.0% | 1.00 |
| Follow-up /recommend | `http_total` | `backend_http` | 30 | 30 | 0 | 30 | 11.196 | 10.785 | 13.463 | 10.031 | 14.470 | 100.0% | 10.00 |
| Initial /recommend | `http_total` | `backend_http` | 30 | 30 | 0 | 30 | 11.216 | 10.628 | 13.714 | 9.871 | 23.800 | 100.0% | 10.00 |

### Internal stages

| Benchmark | Subgroup | Layer | n | Success | Failure | Latency n | Mean ms | Median ms | p95 ms | Min ms | Max ms | Success rate | Avg result count |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Follow-up recommendation stages | `filter` | `backend_service` | 30 | 30 | 0 | 30 | 0.074 | 0.069 | 0.087 | 0.060 | 0.198 | 100.0% | 16.00 |
| Follow-up recommendation stages | `ranking` | `backend_service` | 30 | 30 | 0 | 30 | 0.564 | 0.527 | 0.751 | 0.491 | 0.784 | 100.0% | 10.00 |
| Follow-up recommendation stages | `response_build` | `backend_service` | 30 | 30 | 0 | 30 | 0.076 | 0.073 | 0.097 | 0.067 | 0.122 | 100.0% | 10.00 |
| Follow-up recommendation stages | `specialty_scoring` | `backend_service` | 30 | 30 | 0 | 30 | 2.055 | 2.019 | 2.482 | 1.880 | 2.548 | 100.0% | 4.00 |
| Initial recommendation stages | `filter` | `backend_service` | 30 | 30 | 0 | 30 | 0.076 | 0.071 | 0.108 | 0.064 | 0.134 | 100.0% | 17.00 |
| Initial recommendation stages | `ranking` | `backend_service` | 30 | 30 | 0 | 30 | 0.578 | 0.536 | 0.832 | 0.501 | 0.950 | 100.0% | 10.00 |
| Initial recommendation stages | `response_build` | `backend_service` | 30 | 30 | 0 | 30 | 0.081 | 0.075 | 0.115 | 0.066 | 0.149 | 100.0% | 10.00 |
| Initial recommendation stages | `specialty_scoring` | `backend_service` | 30 | 30 | 0 | 30 | 2.102 | 2.035 | 2.581 | 1.871 | 2.725 | 100.0% | 4.00 |

`specialty_first` sort key is specialty score descending, time score descending, date, session, doctor. `time_first` is time score descending, date, session, specialty score descending, doctor. Current AI doctor scoring is disabled; the measured specialty scoring is deterministic and adds no LLM call.

## I. Quick Search / generate_script

| Benchmark | Subgroup | Layer | n | Success | Failure | Latency n | Mean ms | Median ms | p95 ms | Min ms | Max ms | Success rate | Avg result count |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| /generate_script | `followup_recommendation` | `backend_http` | 30 | 30 | 0 | 30 | 0.964 | 0.942 | 1.151 | 0.848 | 1.178 | 100.0% | 10.00 |
| /generate_script | `initial_recommendation` | `backend_http` | 30 | 30 | 0 | 30 | 1.022 | 0.958 | 1.634 | 0.871 | 1.812 | 100.0% | 10.00 |
| /generate_script | `quick_search` | `backend_http` | 30 | 30 | 0 | 30 | 6.920 | 6.755 | 8.079 | 6.263 | 9.862 | 100.0% | 10.00 |
| Quick Search | `complete_conditions` | `backend_http` | 20 | 20 | 0 | 20 | 13.049 | 13.078 | 13.874 | 12.308 | 14.012 | 100.0% | 2.00 |
| Quick Search | `department_date` | `backend_http` | 20 | 20 | 0 | 20 | 14.313 | 13.121 | 23.194 | 12.445 | 23.527 | 100.0% | 1.00 |
| Quick Search | `department_period` | `backend_http` | 20 | 20 | 0 | 20 | 13.533 | 12.999 | 14.371 | 12.442 | 22.486 | 100.0% | 1.00 |

Quick Search is deterministic and did not call AI. The API requires department, date, and period together; the three subgroup names describe the scenario focus. Script generation used recommendations produced by the corresponding current-runtime path.

## J. Correctness / build

| Check | Result |
|---|---|
| Backend full suite | 280 passed, 0 failed; 134 subtests passed |
| Android unit | 120 passed, 0 failed/error/skipped |
| Android connected | 8 passed, 0 failed/error/skipped |
| `assembleDebug` | BUILD SUCCESSFUL |

An exploratory DB-disabled test run was excluded because exact department validation requires canonical Department records. It is not part of the correctness result above.

## K. APK / audio assets

| Item | Current measurement |
|---|---:|
| Required Chinese files | 45 |
| Required Taigi files | 45 |
| Total local audio files | 90 |
| Container/codec/duration valid | 90/90 |
| Chinese audio bytes | 2,631,515 |
| Taigi audio bytes | 2,500,849 |
| Total audio bytes | 5,132,364 (4.895 MiB) |
| Duration range | 3.200–23.391 s |
| APK | 30,243,276 bytes (28.842 MiB) |
| RMS / peak / silence scan | `NOT_MEASURED` — ffmpeg/ffprobe unavailable |

All 90 files were non-empty ISO-BMFF/M4A with `ftyp`, `moov`, `mdat`, AAC `mp4a`, and positive duration. Four connected playback groups (Chinese short/long-red-flag and Taigi short/long-red-flag) were 100% successful. This evidence does not justify claiming 90/90 are non-silent.

## L. Known limitations / BLOCKED

- Chinese ASR is `AUTOMATION_BLOCKED` and `NOT_MEASURED`: the current runtime is Android `RecognizerIntent`, and deterministic audio injection was unavailable. No Backend ASR latency was substituted.
- Android E2E is automated device repository-to-DTO latency; UI/Compose render and human input time are excluded.
- Taiwanese ASR device timing excludes real microphone stop overhead and Compose rendering.
- No ffmpeg, ffprobe, or mediainfo was available. RMS, peak, and all-file silence analysis are `NOT_MEASURED`.
- The four local playback scenarios are functional smoke samples, not waveform proof for all 90 assets.
- Any group with `n < 30` is a demonstration sample, not an SLA.
- Cerebras department selection had two live `APIConnectionError` failures out of ten attempts; they remain in the formal success rate.

## Complete statistical appendix

Every numeric series in the new raw CSV is listed below.

| Benchmark | Subgroup | Layer | n | Success | Failure | Latency n | Mean ms | Median ms | p95 ms | Min ms | Max ms | Success rate | Avg result count |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Android E2E | `followup` | `android_user_perceived` | 5 | 5 | 0 | 5 | 797.224 | 712.666 | 1006.493 | 689.066 | 1006.493 | 100.0% | 10.00 |
| Android E2E | `initial` | `android_user_perceived` | 5 | 5 | 0 | 5 | 1280.235 | 769.048 | 2296.254 | 706.960 | 2296.254 | 100.0% | 10.00 |
| Android E2E | `quick_search` | `android_user_perceived` | 5 | 5 | 0 | 5 | 21.094 | 17.331 | 37.252 | 16.687 | 37.252 | 100.0% | 1.00 |
| Android E2E | `return_visit` | `android_user_perceived` | 5 | 5 | 0 | 5 | 13.592 | 13.706 | 15.396 | 12.147 | 15.396 | 100.0% | 1.00 |
| Android Voice | `dynamic_chinese_tts_user_perceived` | `android_user_perceived` | 5 | 5 | 0 | 5 | 12230.208 | 11873.631 | 13106.639 | 11779.099 | 13106.639 | 100.0% | 1.00 |
| Android Voice | `dynamic_taiwanese_tts_user_perceived` | `android_user_perceived` | 5 | 5 | 0 | 5 | 4142.235 | 4167.086 | 4248.335 | 4020.944 | 4248.335 | 100.0% | 1.00 |
| Android Voice | `taiwanese_asr_user_perceived` | `android_user_perceived` | 5 | 5 | 0 | 5 | 2619.519 | 2497.018 | 3162.197 | 2440.473 | 3162.197 | 100.0% | 1.00 |
| Cerebras batch extraction | `provider` | `ai_provider` | 10 | 10 | 0 | 10 | 717.580 | 708.350 | 895.400 | 564.900 | 895.400 | 100.0% | 0.00 |
| Cerebras batch extraction | `total` | `semantic_extraction` | 10 | 10 | 0 | 10 | 779.151 | 730.885 | 1207.835 | 573.487 | 1207.835 | 100.0% | 0.00 |
| Cerebras department detection | `provider` | `ai_provider` | 10 | 8 | 2 | 8 | 940.938 | 820.500 | 1732.900 | 710.400 | 1732.900 | 80.0% | 1.00 |
| Cerebras department detection | `total` | `department_service` | 10 | 8 | 2 | 8 | 949.037 | 829.253 | 1740.978 | 718.447 | 1740.978 | 80.0% | 1.00 |
| Cerebras semantic refinement | `http_total` | `backend_http` | 10 | 10 | 0 | 10 | 837.513 | 789.436 | 1364.546 | 521.276 | 1364.546 | 100.0% | 1.10 |
| Cerebras semantic refinement | `provider` | `ai_provider` | 10 | 10 | 0 | 10 | 826.900 | 778.750 | 1353.500 | 510.400 | 1353.500 | 100.0% | 1.10 |
| Deterministic /chat | `case_prepare` | `backend_phase` | 30 | 30 | 0 | 30 | 1.827 | 1.800 | 2.100 | 1.600 | 2.600 | 100.0% | 1.00 |
| Deterministic /chat | `deterministic_parse` | `backend_phase` | 30 | 30 | 0 | 30 | 0.137 | 0.100 | 0.100 | 0.100 | 1.200 | 100.0% | 1.00 |
| Deterministic /chat | `http_total` | `backend_http` | 30 | 30 | 0 | 30 | 3.163 | 2.973 | 3.993 | 2.765 | 7.032 | 100.0% | 1.00 |
| Deterministic /chat | `response_build` | `backend_phase` | 30 | 30 | 0 | 30 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 100.0% | 1.00 |
| Deterministic /chat | `rule_engine` | `backend_phase` | 30 | 30 | 0 | 30 | 0.003 | 0.000 | 0.000 | 0.000 | 0.100 | 100.0% | 1.00 |
| Dynamic Chinese TTS | `cache_hit_http` | `backend_http` | 20 | 20 | 0 | 20 | 6.501 | 6.362 | 6.748 | 6.135 | 8.786 | 100.0% | 58601.00 |
| Dynamic Chinese TTS | `cache_miss_gateway` | `voice_gateway_model` | 10 | 10 | 0 | 10 | 12437.951 | 12290.312 | 14054.128 | 11806.808 | 14054.128 | 100.0% | 58411.40 |
| Dynamic Chinese TTS | `cache_miss_http` | `backend_http` | 10 | 10 | 0 | 10 | 12219.007 | 11869.561 | 13847.589 | 11107.940 | 13847.589 | 100.0% | 58017.30 |
| Dynamic Taigi TTS | `cache_hit_http` | `backend_http` | 20 | 20 | 0 | 20 | 6.383 | 6.321 | 6.894 | 5.905 | 7.441 | 100.0% | 59379.00 |
| Dynamic Taigi TTS | `cache_miss_gateway` | `voice_gateway_model` | 10 | 10 | 0 | 10 | 3949.942 | 3893.285 | 4409.837 | 3547.891 | 4409.837 | 100.0% | 59414.40 |
| Dynamic Taigi TTS | `cache_miss_http` | `backend_http` | 10 | 10 | 0 | 10 | 3919.163 | 3910.079 | 4478.426 | 3690.380 | 4478.426 | 100.0% | 58039.80 |
| Local prerecorded audio | `taigi_long_red_flag` | `android_user_perceived` | 20 | 20 | 0 | 20 | 23.605 | 23.478 | 27.375 | 20.483 | 29.144 | 100.0% | 1.00 |
| Local prerecorded audio | `taigi_short` | `android_user_perceived` | 20 | 20 | 0 | 20 | 22.956 | 22.730 | 26.497 | 19.512 | 26.546 | 100.0% | 1.00 |
| Local prerecorded audio | `zh_long_red_flag` | `android_user_perceived` | 20 | 20 | 0 | 20 | 21.982 | 21.751 | 24.415 | 19.121 | 25.887 | 100.0% | 1.00 |
| Local prerecorded audio | `zh_short` | `android_user_perceived` | 20 | 20 | 0 | 20 | 25.635 | 23.446 | 28.311 | 16.629 | 72.302 | 100.0% | 1.00 |
| Return /followup/recommend | `http_total` | `backend_http` | 30 | 30 | 0 | 30 | 7.883 | 6.824 | 8.162 | 5.990 | 35.438 | 100.0% | 1.00 |
| /generate_script | `followup_recommendation` | `backend_http` | 30 | 30 | 0 | 30 | 0.964 | 0.942 | 1.151 | 0.848 | 1.178 | 100.0% | 10.00 |
| /generate_script | `initial_recommendation` | `backend_http` | 30 | 30 | 0 | 30 | 1.022 | 0.958 | 1.634 | 0.871 | 1.812 | 100.0% | 10.00 |
| /generate_script | `quick_search` | `backend_http` | 30 | 30 | 0 | 30 | 6.920 | 6.755 | 8.079 | 6.263 | 9.862 | 100.0% | 10.00 |
| Quick Search | `complete_conditions` | `backend_http` | 20 | 20 | 0 | 20 | 13.049 | 13.078 | 13.874 | 12.308 | 14.012 | 100.0% | 2.00 |
| Quick Search | `department_date` | `backend_http` | 20 | 20 | 0 | 20 | 14.313 | 13.121 | 23.194 | 12.445 | 23.527 | 100.0% | 1.00 |
| Quick Search | `department_period` | `backend_http` | 20 | 20 | 0 | 20 | 13.533 | 12.999 | 14.371 | 12.442 | 22.486 | 100.0% | 1.00 |
| Follow-up /recommend | `http_total` | `backend_http` | 30 | 30 | 0 | 30 | 11.196 | 10.785 | 13.463 | 10.031 | 14.470 | 100.0% | 10.00 |
| Follow-up recommendation stages | `filter` | `backend_service` | 30 | 30 | 0 | 30 | 0.074 | 0.069 | 0.087 | 0.060 | 0.198 | 100.0% | 16.00 |
| Follow-up recommendation stages | `ranking` | `backend_service` | 30 | 30 | 0 | 30 | 0.564 | 0.527 | 0.751 | 0.491 | 0.784 | 100.0% | 10.00 |
| Follow-up recommendation stages | `response_build` | `backend_service` | 30 | 30 | 0 | 30 | 0.076 | 0.073 | 0.097 | 0.067 | 0.122 | 100.0% | 10.00 |
| Follow-up recommendation stages | `specialty_scoring` | `backend_service` | 30 | 30 | 0 | 30 | 2.055 | 2.019 | 2.482 | 1.880 | 2.548 | 100.0% | 4.00 |
| Follow-up recommendation stages | `sql_retrieval` | `sql_adapter` | 30 | 30 | 0 | 30 | 6.826 | 6.736 | 7.413 | 6.352 | 7.612 | 100.0% | 16.00 |
| Initial /recommend | `http_total` | `backend_http` | 30 | 30 | 0 | 30 | 11.216 | 10.628 | 13.714 | 9.871 | 23.800 | 100.0% | 10.00 |
| Initial recommendation stages | `filter` | `backend_service` | 30 | 30 | 0 | 30 | 0.076 | 0.071 | 0.108 | 0.064 | 0.134 | 100.0% | 17.00 |
| Initial recommendation stages | `ranking` | `backend_service` | 30 | 30 | 0 | 30 | 0.578 | 0.536 | 0.832 | 0.501 | 0.950 | 100.0% | 10.00 |
| Initial recommendation stages | `response_build` | `backend_service` | 30 | 30 | 0 | 30 | 0.081 | 0.075 | 0.115 | 0.066 | 0.149 | 100.0% | 10.00 |
| Initial recommendation stages | `specialty_scoring` | `backend_service` | 30 | 30 | 0 | 30 | 2.102 | 2.035 | 2.581 | 1.871 | 2.725 | 100.0% | 4.00 |
| Initial recommendation stages | `sql_retrieval` | `sql_adapter` | 30 | 30 | 0 | 30 | 7.139 | 6.798 | 8.597 | 6.118 | 15.791 | 100.0% | 17.00 |
| SQL adapter | `initial_schedule` | `sql_adapter` | 20 | 20 | 0 | 20 | 6.514 | 6.433 | 6.956 | 5.964 | 7.243 | 100.0% | 17.00 |
| SQL adapter | `quick_search` | `sql_adapter` | 20 | 20 | 0 | 20 | 6.698 | 6.733 | 7.071 | 6.226 | 7.138 | 100.0% | 2.00 |
| SQL adapter | `return_visit_schedule` | `sql_adapter` | 20 | 20 | 0 | 20 | 5.628 | 5.522 | 6.438 | 5.166 | 6.480 | 100.0% | 2.00 |
| Taiwanese ASR | `backend_http` | `backend_http` | 20 | 20 | 0 | 20 | 2417.760 | 2407.372 | 2464.743 | 2365.509 | 2692.345 | 100.0% | 1.00 |
| Taiwanese ASR | `gateway` | `voice_gateway_model` | 20 | 20 | 0 | 20 | 2399.634 | 2391.180 | 2442.486 | 2365.072 | 2528.498 | 100.0% | 1.00 |

## Current-version summary

1. Fastest measured user-facing backend operation: stored follow-up `/generate_script` (mean 0.964 ms). The internal deterministic rule phase is smaller but is not a standalone request.
2. Slowest flow: dynamic Chinese TTS cache miss / Gateway generation (Gateway mean 12437.951 ms; Android cold click mean 12230.208 ms).
3. AI maximum successful-call latency source: department detection observed max 1732.900 ms; its 80% attempt success rate reflects two retained connection errors.
4. Voice maximum latency source: Chinese dynamic TTS, max 14054.128 ms at provider layer.
5. SQL adapter means: initial 6.514 ms, return 5.628 ms, quick 6.698 ms.
6. Recommendation HTTP means: initial 11.216 ms; follow-up 11.196 ms; return 7.883 ms.
7. Fixed local audio means range from 21.982 to 25.635 ms.
8. Dynamic TTS cache hits are about 6 ms Backend HTTP; misses are seconds and remain separated from Android playback.
9. Taiwanese ASR: Backend HTTP mean 2417.760 ms; Gateway mean 2399.634 ms; Android upload-to-transcript mean 2619.519 ms.
10. Android E2E repository-to-DTO means: initial 1280.235 ms, follow-up 797.224 ms, quick 21.094 ms, return 13.592 ms.
11. Correctness: Backend 280 + 134 subtests, Android unit 120, connected 8, all final runs passed.
12. APK size: 30,243,276 bytes / 28.842 MiB.
13. Known limitations: Chinese RecognizerIntent latency unavailable; Compose render excluded from device E2E; microphone stop overhead excluded from ASR device sample; waveform silence not measured; n<30 samples are demonstrations; AI department had two live connection failures.

## Table for project proposal

| Capability | Layer | n | Mean ms | Median ms | p95 ms | Success rate | Notes |
|---|---|---:|---:|---:|---:|---:|---|
| Deterministic `/chat` | Backend HTTP | 30 | 3.163 | 2.973 | 3.993 | 100% | Fresh case, zero AI calls |
| Batch extraction | Cerebras provider | 10 | 717.580 | 708.350 | 895.400 | 100% | Long-lived event loop |
| Semantic refinement | Cerebras provider | 10 | 826.900 | 778.750 | 1353.500 | 100% | Formal semantic path |
| Department selection | Cerebras provider | 10 | 940.938 | 820.500 | 1732.900 | 80% | Latency over 8 successful calls |
| Taiwanese ASR | Backend HTTP | 20 | 2417.760 | 2407.372 | 2464.743 | 100% | Fixed WAV fixture |
| Chinese local short | Android local | 20 | 25.635 | 23.446 | 28.311 | 100% | `/voice/tts=false` |
| Chinese local long red flag | Android local | 20 | 21.982 | 21.751 | 24.415 | 100% | `/voice/tts=false` |
| Taigi local short | Android local | 20 | 22.956 | 22.730 | 26.497 | 100% | `/voice/tts=false` |
| Taigi local long red flag | Android local | 20 | 23.605 | 23.478 | 27.375 | 100% | `/voice/tts=false` |
| Dynamic Chinese TTS miss | Backend HTTP | 10 | 12219.007 | 11869.561 | 13847.589 | 100% | Cache miss |
| Dynamic Taigi TTS miss | Backend HTTP | 10 | 3919.163 | 3910.079 | 4478.426 | 100% | Cache miss |
| Initial recommendation | Backend HTTP | 30 | 11.216 | 10.628 | 13.714 | 100% | Real SQL |
| Follow-up recommendation | Backend HTTP | 30 | 11.196 | 10.785 | 13.463 | 100% | Real SQL |
| Return visit | Backend HTTP | 30 | 7.883 | 6.824 | 8.162 | 100% | Exact original doctor |
| Quick Search complete | Backend HTTP | 20 | 13.049 | 13.078 | 13.874 | 100% | Zero AI calls |
| `/generate_script` initial | Backend HTTP | 30 | 1.022 | 0.958 | 1.634 | 100% | Existing recommendation |
| SQL adapter initial | SQL adapter | 20 | 6.514 | 6.433 | 6.956 | 100% | Connection/query/mapping |
| Android initial E2E | Device repository-to-DTO | 5 | 1280.235 | 769.048 | 2296.254 | 100% | UI render not included |

All `n < 30` entries above are demonstration samples, not SLA claims.
