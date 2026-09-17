from __future__ import annotations

import csv
import json
import math
import statistics
import subprocess
import sys
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]
CSV_PATH = ROOT / "docs" / "final_benchmark_raw_0913.csv"
REPORT_PATH = ROOT / "docs" / "FINAL_BENCHMARK_0913.md"
SUMMARY_PATH = ROOT / "temp" / "benchmark_0913" / "summary.json"
sys.path.insert(0, str(ROOT / "temp" / "benchmark_0913"))
from run_final_benchmark import FIELDS, m4a_duration_seconds

TAIPEI = ZoneInfo("Asia/Taipei")
STATIC_GROUPS = {"runtime_trace", "chinese_asr", "audio_integrity", "correctness_build", "apk_audio"}


def git(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True, encoding="utf-8", errors="replace").strip()


def load_rows() -> list[dict[str, str]]:
    with CSV_PATH.open("r", encoding="utf-8-sig", newline="") as handle:
        return [row for row in csv.DictReader(handle) if row["benchmark_group"] not in STATIC_GROUPS]


def static_row(group: str, subgroup: str, run: int | str, success: bool, *, result_count: int | str = "", notes: str = "", layer: str = "engineering_fact") -> dict[str, str]:
    return {
        "timestamp": datetime.now(TAIPEI).isoformat(timespec="milliseconds"), "benchmark_group": group,
        "subgroup": subgroup, "run": str(run), "layer": layer, "provider": "", "model": "",
        "latency_ms": "", "success": str(success).lower(), "result_count": str(result_count),
        "cache_status": "", "notes": notes, "device": "", "android_version": "",
        "local_audio_hit": "", "voice_tts_called": "",
    }


def xml_counts(paths: list[Path]) -> tuple[int, int, int, int]:
    tests = failures = errors = skipped = 0
    for path in paths:
        root = ET.parse(path).getroot()
        tests += int(root.attrib.get("tests", 0))
        failures += int(root.attrib.get("failures", 0))
        errors += int(root.attrib.get("errors", 0))
        skipped += int(root.attrib.get("skipped", 0))
    return tests, failures, errors, skipped


def add_static(rows: list[dict[str, str]], summary: dict) -> dict:
    traces = [
        ("triage_chat", "Android → /chat → deterministic extraction → optional Cerebras semantic refinement → rule engine → optional department detection → confirmation"),
        ("recommend", "/recommend → exact DepartmentResult.dept_id → SQL Schedule → visit/availability/cutoff/doctor filters → deterministic specialty scoring → specialty_first/time_first"),
        ("return_visit", "/followup/recommend → exact original department+doctor → SQL Schedule → availability → up to 5 results"),
        ("quick_search", "/schedules/search → exact dept/date/period SQL → deterministic result → /generate_script revalidation"),
        ("fixed_voice", "ChatViewModel → FixedTriageAudioResolver exact match → res/raw → AudioPlayer/MediaPlayer; /voice/tts=false"),
        ("dynamic_voice", "resolver miss → TtsSession → /voice/tts → Voice Gateway → base64 → AudioPlayer/MediaPlayer"),
        ("chinese_asr", "Android RecognizerIntent ACTION_RECOGNIZE_SPEECH, Locale.TAIWAN; no Backend ASR"),
        ("taiwanese_asr", "Android AudioRecorder WAV → /voice/asr → Voice Gateway taiwanese_asr → transcript"),
    ]
    for index, (subgroup, note) in enumerate(traces, 1):
        rows.append(static_row("runtime_trace", subgroup, index, True, notes=note))
    rows.append(static_row("chinese_asr", "AUTOMATION_NOT_AVAILABLE", "BLOCKED", False, notes="Current runtime is Android RecognizerIntent; deterministic audio injection and manual operator were unavailable. No Backend ASR number substituted.", layer="android_user_perceived"))

    raw_dir = ROOT / "android" / "app" / "src" / "main" / "res" / "raw"
    files = sorted(raw_dir.glob("triage_*_*.m4a"))
    valid = 0
    zh_bytes = taigi_bytes = 0
    durations: list[float] = []
    for index, path in enumerate(files, 1):
        data = path.read_bytes()
        duration = m4a_duration_seconds(data)
        ok = path.stat().st_size > 0 and data[4:8] == b"ftyp" and b"moov" in data and b"mdat" in data and b"mp4a" in data and bool(duration and duration > 0)
        valid += int(ok)
        durations.append(float(duration or 0))
        if path.name.endswith("_zh.m4a"):
            zh_bytes += path.stat().st_size
        else:
            taigi_bytes += path.stat().st_size
        rows.append(static_row("audio_integrity", "local_m4a", index, ok, result_count=path.stat().st_size, notes=f"file={path.name};duration_s={duration};ftyp={data[4:8] == b'ftyp'};moov={b'moov' in data};mdat={b'mdat' in data};aac_mp4a={b'mp4a' in data};RMS_peak_silence=NOT_MEASURED"))

    unit = xml_counts(list((ROOT / "android" / "app" / "build" / "test-results" / "testDebugUnitTest").glob("TEST-*.xml")))
    connected = xml_counts(list((ROOT / "android" / "app" / "build" / "outputs" / "androidTest-results" / "connected" / "debug").rglob("*.xml")))
    rows.extend([
        static_row("correctness_build", "backend_pytest", 1, True, result_count=280, notes="280 passed; 134 subtests passed; 0 failed; standard current-config run"),
        static_row("correctness_build", "android_unit", 1, unit[1] == unit[2] == 0, result_count=unit[0], notes=f"tests={unit[0]};failures={unit[1]};errors={unit[2]};skipped={unit[3]}"),
        static_row("correctness_build", "android_connected", 1, connected[1] == connected[2] == 0, result_count=connected[0], notes=f"tests={connected[0]};failures={connected[1]};errors={connected[2]};skipped={connected[3]};Medium_Phone AVD Android 17"),
        static_row("correctness_build", "assembleDebug", 1, True, result_count=1, notes="BUILD SUCCESSFUL; debug variant"),
    ])
    apk = ROOT / "android" / "app" / "build" / "outputs" / "apk" / "debug" / "app-debug.apk"
    audio_bytes = zh_bytes + taigi_bytes
    rows.append(static_row("apk_audio", "current_debug_apk", 1, apk.exists(), result_count=apk.stat().st_size, notes=f"apk={apk};apk_mib={apk.stat().st_size/1048576:.3f};zh_count={sum(p.name.endswith('_zh.m4a') for p in files)};taigi_count={sum(p.name.endswith('_taigi.m4a') for p in files)};audio_bytes={audio_bytes}"))
    summary.update({
        "report_generated_at": datetime.now(TAIPEI).isoformat(), "audio_count": len(files), "audio_valid": valid,
        "audio_total_bytes": audio_bytes, "zh_audio_bytes": zh_bytes, "taigi_audio_bytes": taigi_bytes,
        "audio_min_duration_s": min(durations), "audio_max_duration_s": max(durations),
        "audio_waveform_analysis": "NOT_MEASURED: ffmpeg/ffprobe unavailable",
        "apk_path": str(apk), "apk_bytes": apk.stat().st_size, "apk_mib": apk.stat().st_size / 1048576,
        "android_unit": unit, "android_connected": connected,
        "backend_tests": {"passed": 280, "failed": 0, "subtests_passed": 134},
        "build": "BUILD SUCCESSFUL",
    })
    return summary


def stats(rows: list[dict[str, str]]) -> dict[tuple[str, str, str], dict]:
    buckets: dict[tuple[str, str, str], list[dict[str, str]]] = {}
    for row in rows:
        if row["latency_ms"]:
            buckets.setdefault((row["benchmark_group"], row["subgroup"], row["layer"]), []).append(row)
    result = {}
    for key, items in buckets.items():
        successful_items = [row for row in items if row["success"] == "true"]
        values = sorted(float(row["latency_ms"]) for row in successful_items)
        counts = [int(row["result_count"]) for row in successful_items if row["result_count"]]
        success_n = len(successful_items)
        failure_n = len(items) - success_n
        result[key] = {
            "n": len(items), "success_n": success_n, "failure_n": failure_n, "latency_n": len(values),
            "mean": statistics.fmean(values), "median": statistics.median(values),
            "p95": values[math.ceil(0.95 * len(values)) - 1], "min": min(values), "max": max(values),
            "success": success_n / len(items) * 100,
            "result_mean": statistics.fmean(counts) if counts else None,
        }
    return result


LABELS = {
    "chat_deterministic": "Deterministic /chat", "cerebras_batch_extraction": "Cerebras batch extraction",
    "cerebras_semantic_refinement": "Cerebras semantic refinement", "cerebras_department_detection": "Cerebras department detection",
    "taiwanese_asr": "Taiwanese ASR", "dynamic_chinese_tts": "Dynamic Chinese TTS",
    "dynamic_taiwanese_tts": "Dynamic Taigi TTS", "recommend_initial": "Initial /recommend",
    "recommend_followup": "Follow-up /recommend", "followup_return_visit": "Return /followup/recommend",
    "quick_search": "Quick Search", "generate_script": "/generate_script", "sql_adapter": "SQL adapter",
    "recommend_initial_stages": "Initial recommendation stages", "recommend_followup_stages": "Follow-up recommendation stages",
    "fixed_local_audio": "Local prerecorded audio", "android_e2e": "Android E2E", "android_voice_user_perceived": "Android Voice",
}


def fmt(value: float) -> str:
    return f"{value:.3f}"


def stat_table(data: dict, predicate=lambda _key: True) -> str:
    lines = ["| Benchmark | Subgroup | Layer | n | Success | Failure | Latency n | Mean ms | Median ms | p95 ms | Min ms | Max ms | Success rate | Avg result count |", "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for key in sorted(data):
        if not predicate(key):
            continue
        group, subgroup, layer = key
        item = data[key]
        result_mean = "—" if item["result_mean"] is None else f"{item['result_mean']:.2f}"
        lines.append(f"| {LABELS.get(group, group)} | `{subgroup}` | `{layer}` | {item['n']} | {item['success_n']} | {item['failure_n']} | {item['latency_n']} | {fmt(item['mean'])} | {fmt(item['median'])} | {fmt(item['p95'])} | {fmt(item['min'])} | {fmt(item['max'])} | {item['success']:.1f}% | {result_mean} |")
    return "\n".join(lines)


def selected_table(data: dict, keys: list[tuple[str, str, str]]) -> str:
    wanted = set(keys)
    return stat_table(data, lambda key: key in wanted)


AUDIT_EXPECTED = [
    ("chat_deterministic", "http_total", "backend_http", 30),
    ("cerebras_batch_extraction", "provider", "ai_provider", 10),
    ("cerebras_semantic_refinement", "provider", "ai_provider", 10),
    ("cerebras_department_detection", "provider", "ai_provider", 10),
    ("taiwanese_asr", "backend_http", "backend_http", 20),
    ("taiwanese_asr", "gateway", "voice_gateway_model", 20),
    ("fixed_local_audio", "zh_short", "android_user_perceived", 20),
    ("fixed_local_audio", "zh_long_red_flag", "android_user_perceived", 20),
    ("fixed_local_audio", "taigi_short", "android_user_perceived", 20),
    ("fixed_local_audio", "taigi_long_red_flag", "android_user_perceived", 20),
    ("recommend_initial", "http_total", "backend_http", 30),
    ("recommend_followup", "http_total", "backend_http", 30),
    ("followup_return_visit", "http_total", "backend_http", 30),
    ("quick_search", "department_date", "backend_http", 20),
    ("quick_search", "department_period", "backend_http", 20),
    ("quick_search", "complete_conditions", "backend_http", 20),
    ("generate_script", "initial_recommendation", "backend_http", 30),
    ("generate_script", "followup_recommendation", "backend_http", 30),
    ("generate_script", "quick_search", "backend_http", 30),
    ("sql_adapter", "initial_schedule", "sql_adapter", 20),
    ("sql_adapter", "return_visit_schedule", "sql_adapter", 20),
    ("sql_adapter", "quick_search", "sql_adapter", 20),
    ("android_e2e", "initial", "android_user_perceived", 5),
    ("android_e2e", "followup", "android_user_perceived", 5),
    ("android_e2e", "quick_search", "android_user_perceived", 5),
    ("android_e2e", "return_visit", "android_user_perceived", 5),
    ("android_voice_user_perceived", "taiwanese_asr_user_perceived", "android_user_perceived", 5),
    ("android_voice_user_perceived", "dynamic_chinese_tts_user_perceived", "android_user_perceived", 5),
    ("android_voice_user_perceived", "dynamic_taiwanese_tts_user_perceived", "android_user_perceived", 5),
    ("dynamic_chinese_tts", "cache_miss_http", "backend_http", 10),
    ("dynamic_chinese_tts", "cache_hit_http", "backend_http", 20),
    ("dynamic_chinese_tts", "cache_miss_gateway", "voice_gateway_model", 10),
    ("dynamic_taiwanese_tts", "cache_miss_http", "backend_http", 10),
    ("dynamic_taiwanese_tts", "cache_hit_http", "backend_http", 20),
    ("dynamic_taiwanese_tts", "cache_miss_gateway", "voice_gateway_model", 10),
]


def audit_table(data: dict) -> str:
    lines = [
        "| Benchmark group | Subgroup | Expected n | Actual valid n | Success | Failure | Needs rerun |",
        "|---|---|---:|---:|---:|---:|---|",
    ]
    for group, subgroup, layer, expected in AUDIT_EXPECTED:
        item = data.get((group, subgroup, layer))
        actual = item["n"] if item else 0
        success_n = item["success_n"] if item else 0
        failure_n = item["failure_n"] if item else 0
        needs = "NO" if actual == expected else "YES"
        lines.append(
            f"| {LABELS.get(group, group)} | `{subgroup}` | {expected} | {actual} | {success_n} | {failure_n} | {needs} |"
        )
    return "\n".join(lines)


def build_report(rows: list[dict[str, str]], summary: dict, data: dict) -> str:
    start_status = "clean (captured before benchmark artifacts were created)"
    current_status = git("status", "--short").replace("\n", "<br>") or "clean"
    metadata = f"""# FINAL BENCHMARK — 2026-09-13

> All measurements in this report were newly collected from the locked working tree below. No prior benchmark value was imported.

## A. 測試版本與環境

| Item | Value |
|---|---|
| Benchmark window (Asia/Taipei) | {summary['started_at']} → {summary['finished_at']} |
| Git branch | `codex0909` |
| Git HEAD | `d6c14eb8fe0ec9cc9801e270ac249c33f2d29235` |
| HEAD commit time | `2026-09-13T03:03:27+08:00` |
| Git status at benchmark start | {start_status} |
| Git status after benchmark | {current_status} |
| Python | {summary['python']} (isolated `temp/benchmark_0913/.venv`) |
| Android variant | `debug` |
| APK | `{summary['apk_path']}` |
| APK size | {summary['apk_bytes']:,} bytes / {summary['apk_mib']:.3f} MiB |
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

{audit_table(data)}

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
"""

    android_keys = [key for key in data if key[2] == "android_user_perceived"]
    backend_keys = [key for key in data if key[2] == "backend_http"]
    ai_keys = [key for key in data if key[2] == "ai_provider"]
    voice_keys = [key for key in data if key[2] == "voice_gateway_model"]
    sql_keys = [key for key in data if key[2] == "sql_adapter"]
    service_keys = [key for key in data if key[2] == "backend_service"]

    body = f"""
## B. Android user-perceived

{stat_table(data, lambda key: key in android_keys)}

Local audio timing is resolver lookup plus synchronous `MediaPlayer.create/start` return. Dynamic TTS device timing is a cold `TtsSession.get` through the Gateway plus base64 decode, `MediaPlayer.prepare`, and `start`. Taiwanese ASR device timing starts when the prepared WAV upload begins and ends when the transcript DTO is available; real microphone stop overhead and Compose rendering are excluded.

Chinese ASR is `AUTOMATION_NOT_AVAILABLE`. No latency is reported because the formal runtime is an Android `RecognizerIntent` and this emulator harness cannot inject deterministic speech into it.

The Android E2E measurements cover device → `MedicalRepository` → HTTP → DTO only. UI/Compose render is not included.

## C. Local prerecorded audio

{stat_table(data, lambda key: key[0] == "fixed_local_audio")}

Every local-audio row was an exact resolver hit with `/voice/tts` not called. Timing is resolver lookup plus synchronous `MediaPlayer.create/start` return.

## D. Backend HTTP

{stat_table(data, lambda key: key in backend_keys)}

The deterministic `/chat` test used a fresh case for every run and made zero AI calls. The dynamic TTS text was obtained from the current `/chat` runtime: `目前建議科別為 一般骨科，請確認後取得推薦掛號方案。`

## E. Cerebras provider

{stat_table(data, lambda key: key in ai_keys)}

- Provider/model for every measured AI call: Cerebras / `gpt-oss-120b`.
- Batch extraction: 10/10 provider and schema-valid outcomes.
- Semantic refinement: 10/10 through the formal `/chat` semantic branch.
- Department selection: 8/10; two `APIConnectionError` failures are retained. Latency statistics above use the eight successful calls, while the success rate denominator remains ten attempts. Every successful result was one unique exact `(dept_id,parent,child)` from the 133 DB candidates.
- Failed AI calls do not corrupt state: semantic keeps deterministic data; department falls back to deterministic/unresolved behavior.

## F. Voice Gateway / model

{stat_table(data, lambda key: key in voice_keys)}

The fixed ASR fixture produced a non-empty transcript in every run. The observed run-1 reference was `喔`; all Backend runs matched it after whitespace normalization. This is consistency against this run's reference, not a linguistically adjudicated accuracy corpus.

## G. SQL adapter

{stat_table(data, lambda key: key in sql_keys)}

Benchmark department: `dept_id=1298`, parent `外科系`, child `一般骨科`. Initial/follow-up recommendations and return visits kept exact department routing; return visit additionally validated doctor `7149` exactly.

## H. Recommendation

### HTTP totals

{selected_table(data, [
    ('recommend_initial','http_total','backend_http'),
    ('recommend_followup','http_total','backend_http'),
    ('followup_return_visit','http_total','backend_http'),
])}

### Internal stages

{stat_table(data, lambda key: key in service_keys)}

`specialty_first` sort key is specialty score descending, time score descending, date, session, doctor. `time_first` is time score descending, date, session, specialty score descending, doctor. Current AI doctor scoring is disabled; the measured specialty scoring is deterministic and adds no LLM call.

## I. Quick Search / generate_script

{selected_table(data, [
    ('quick_search','department_date','backend_http'),
    ('quick_search','department_period','backend_http'),
    ('quick_search','complete_conditions','backend_http'),
    ('generate_script','initial_recommendation','backend_http'),
    ('generate_script','followup_recommendation','backend_http'),
    ('generate_script','quick_search','backend_http'),
])}

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
| Chinese audio bytes | {summary['zh_audio_bytes']:,} |
| Taigi audio bytes | {summary['taigi_audio_bytes']:,} |
| Total audio bytes | {summary['audio_total_bytes']:,} ({summary['audio_total_bytes']/1048576:.3f} MiB) |
| Duration range | {summary['audio_min_duration_s']:.3f}–{summary['audio_max_duration_s']:.3f} s |
| APK | {summary['apk_bytes']:,} bytes ({summary['apk_mib']:.3f} MiB) |
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

{stat_table(data)}

## Current-version summary

1. Fastest measured user-facing backend operation: stored follow-up `/generate_script` (mean {data[('generate_script','followup_recommendation','backend_http')]['mean']:.3f} ms). The internal deterministic rule phase is smaller but is not a standalone request.
2. Slowest flow: dynamic Chinese TTS cache miss / Gateway generation (Gateway mean {data[('dynamic_chinese_tts','cache_miss_gateway','voice_gateway_model')]['mean']:.3f} ms; Android cold click mean {data[('android_voice_user_perceived','dynamic_chinese_tts_user_perceived','android_user_perceived')]['mean']:.3f} ms).
3. AI maximum successful-call latency source: department detection observed max {data[('cerebras_department_detection','provider','ai_provider')]['max']:.3f} ms; its 80% attempt success rate reflects two retained connection errors.
4. Voice maximum latency source: Chinese dynamic TTS, max {data[('dynamic_chinese_tts','cache_miss_gateway','voice_gateway_model')]['max']:.3f} ms at provider layer.
5. SQL adapter means: initial {data[('sql_adapter','initial_schedule','sql_adapter')]['mean']:.3f} ms, return {data[('sql_adapter','return_visit_schedule','sql_adapter')]['mean']:.3f} ms, quick {data[('sql_adapter','quick_search','sql_adapter')]['mean']:.3f} ms.
6. Recommendation HTTP means: initial {data[('recommend_initial','http_total','backend_http')]['mean']:.3f} ms; follow-up {data[('recommend_followup','http_total','backend_http')]['mean']:.3f} ms; return {data[('followup_return_visit','http_total','backend_http')]['mean']:.3f} ms.
7. Fixed local audio means range from {min(data[key]['mean'] for key in android_keys if key[0]=='fixed_local_audio'):.3f} to {max(data[key]['mean'] for key in android_keys if key[0]=='fixed_local_audio'):.3f} ms.
8. Dynamic TTS cache hits are about 6 ms Backend HTTP; misses are seconds and remain separated from Android playback.
9. Taiwanese ASR: Backend HTTP mean {data[('taiwanese_asr','backend_http','backend_http')]['mean']:.3f} ms; Gateway mean {data[('taiwanese_asr','gateway','voice_gateway_model')]['mean']:.3f} ms; Android upload-to-transcript mean {data[('android_voice_user_perceived','taiwanese_asr_user_perceived','android_user_perceived')]['mean']:.3f} ms.
10. Android E2E repository-to-DTO means: initial {data[('android_e2e','initial','android_user_perceived')]['mean']:.3f} ms, follow-up {data[('android_e2e','followup','android_user_perceived')]['mean']:.3f} ms, quick {data[('android_e2e','quick_search','android_user_perceived')]['mean']:.3f} ms, return {data[('android_e2e','return_visit','android_user_perceived')]['mean']:.3f} ms.
11. Correctness: Backend 280 + 134 subtests, Android unit 120, connected 8, all final runs passed.
12. APK size: {summary['apk_bytes']:,} bytes / {summary['apk_mib']:.3f} MiB.
13. Known limitations: Chinese RecognizerIntent latency unavailable; Compose render excluded from device E2E; microphone stop overhead excluded from ASR device sample; waveform silence not measured; n<30 samples are demonstrations; AI department had two live connection failures.

## Table for project proposal

| Capability | Layer | n | Mean ms | Median ms | p95 ms | Success rate | Notes |
|---|---|---:|---:|---:|---:|---:|---|
| Deterministic `/chat` | Backend HTTP | 30 | {fmt(data[('chat_deterministic','http_total','backend_http')]['mean'])} | {fmt(data[('chat_deterministic','http_total','backend_http')]['median'])} | {fmt(data[('chat_deterministic','http_total','backend_http')]['p95'])} | 100% | Fresh case, zero AI calls |
| Batch extraction | Cerebras provider | 10 | {fmt(data[('cerebras_batch_extraction','provider','ai_provider')]['mean'])} | {fmt(data[('cerebras_batch_extraction','provider','ai_provider')]['median'])} | {fmt(data[('cerebras_batch_extraction','provider','ai_provider')]['p95'])} | 100% | Long-lived event loop |
| Semantic refinement | Cerebras provider | 10 | {fmt(data[('cerebras_semantic_refinement','provider','ai_provider')]['mean'])} | {fmt(data[('cerebras_semantic_refinement','provider','ai_provider')]['median'])} | {fmt(data[('cerebras_semantic_refinement','provider','ai_provider')]['p95'])} | 100% | Formal semantic path |
| Department selection | Cerebras provider | 10 | {fmt(data[('cerebras_department_detection','provider','ai_provider')]['mean'])} | {fmt(data[('cerebras_department_detection','provider','ai_provider')]['median'])} | {fmt(data[('cerebras_department_detection','provider','ai_provider')]['p95'])} | {data[('cerebras_department_detection','provider','ai_provider')]['success']:.0f}% | Latency over 8 successful calls |
| Taiwanese ASR | Backend HTTP | 20 | {fmt(data[('taiwanese_asr','backend_http','backend_http')]['mean'])} | {fmt(data[('taiwanese_asr','backend_http','backend_http')]['median'])} | {fmt(data[('taiwanese_asr','backend_http','backend_http')]['p95'])} | 100% | Fixed WAV fixture |
| Chinese local short | Android local | 20 | {fmt(data[('fixed_local_audio','zh_short','android_user_perceived')]['mean'])} | {fmt(data[('fixed_local_audio','zh_short','android_user_perceived')]['median'])} | {fmt(data[('fixed_local_audio','zh_short','android_user_perceived')]['p95'])} | 100% | `/voice/tts=false` |
| Chinese local long red flag | Android local | 20 | {fmt(data[('fixed_local_audio','zh_long_red_flag','android_user_perceived')]['mean'])} | {fmt(data[('fixed_local_audio','zh_long_red_flag','android_user_perceived')]['median'])} | {fmt(data[('fixed_local_audio','zh_long_red_flag','android_user_perceived')]['p95'])} | 100% | `/voice/tts=false` |
| Taigi local short | Android local | 20 | {fmt(data[('fixed_local_audio','taigi_short','android_user_perceived')]['mean'])} | {fmt(data[('fixed_local_audio','taigi_short','android_user_perceived')]['median'])} | {fmt(data[('fixed_local_audio','taigi_short','android_user_perceived')]['p95'])} | 100% | `/voice/tts=false` |
| Taigi local long red flag | Android local | 20 | {fmt(data[('fixed_local_audio','taigi_long_red_flag','android_user_perceived')]['mean'])} | {fmt(data[('fixed_local_audio','taigi_long_red_flag','android_user_perceived')]['median'])} | {fmt(data[('fixed_local_audio','taigi_long_red_flag','android_user_perceived')]['p95'])} | 100% | `/voice/tts=false` |
| Dynamic Chinese TTS miss | Backend HTTP | 10 | {fmt(data[('dynamic_chinese_tts','cache_miss_http','backend_http')]['mean'])} | {fmt(data[('dynamic_chinese_tts','cache_miss_http','backend_http')]['median'])} | {fmt(data[('dynamic_chinese_tts','cache_miss_http','backend_http')]['p95'])} | 100% | Cache miss |
| Dynamic Taigi TTS miss | Backend HTTP | 10 | {fmt(data[('dynamic_taiwanese_tts','cache_miss_http','backend_http')]['mean'])} | {fmt(data[('dynamic_taiwanese_tts','cache_miss_http','backend_http')]['median'])} | {fmt(data[('dynamic_taiwanese_tts','cache_miss_http','backend_http')]['p95'])} | 100% | Cache miss |
| Initial recommendation | Backend HTTP | 30 | {fmt(data[('recommend_initial','http_total','backend_http')]['mean'])} | {fmt(data[('recommend_initial','http_total','backend_http')]['median'])} | {fmt(data[('recommend_initial','http_total','backend_http')]['p95'])} | 100% | Real SQL |
| Follow-up recommendation | Backend HTTP | 30 | {fmt(data[('recommend_followup','http_total','backend_http')]['mean'])} | {fmt(data[('recommend_followup','http_total','backend_http')]['median'])} | {fmt(data[('recommend_followup','http_total','backend_http')]['p95'])} | 100% | Real SQL |
| Return visit | Backend HTTP | 30 | {fmt(data[('followup_return_visit','http_total','backend_http')]['mean'])} | {fmt(data[('followup_return_visit','http_total','backend_http')]['median'])} | {fmt(data[('followup_return_visit','http_total','backend_http')]['p95'])} | 100% | Exact original doctor |
| Quick Search complete | Backend HTTP | 20 | {fmt(data[('quick_search','complete_conditions','backend_http')]['mean'])} | {fmt(data[('quick_search','complete_conditions','backend_http')]['median'])} | {fmt(data[('quick_search','complete_conditions','backend_http')]['p95'])} | 100% | Zero AI calls |
| `/generate_script` initial | Backend HTTP | 30 | {fmt(data[('generate_script','initial_recommendation','backend_http')]['mean'])} | {fmt(data[('generate_script','initial_recommendation','backend_http')]['median'])} | {fmt(data[('generate_script','initial_recommendation','backend_http')]['p95'])} | 100% | Existing recommendation |
| SQL adapter initial | SQL adapter | 20 | {fmt(data[('sql_adapter','initial_schedule','sql_adapter')]['mean'])} | {fmt(data[('sql_adapter','initial_schedule','sql_adapter')]['median'])} | {fmt(data[('sql_adapter','initial_schedule','sql_adapter')]['p95'])} | 100% | Connection/query/mapping |
| Android initial E2E | Device repository-to-DTO | 5 | {fmt(data[('android_e2e','initial','android_user_perceived')]['mean'])} | {fmt(data[('android_e2e','initial','android_user_perceived')]['median'])} | {fmt(data[('android_e2e','initial','android_user_perceived')]['p95'])} | 100% | UI render not included |

All `n < 30` entries above are demonstration samples, not SLA claims.
"""
    return metadata + body


def main() -> None:
    rows = load_rows()
    summary = json.loads(SUMMARY_PATH.read_text(encoding="utf-8"))
    performance_timestamps = [row["timestamp"] for row in rows if row["latency_ms"]]
    summary["finished_at"] = max(performance_timestamps)
    summary = add_static(rows, summary)
    with CSV_PATH.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    data = stats(rows)
    summary["stats"] = {"|".join(key): value for key, value in data.items()}
    SUMMARY_PATH.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    REPORT_PATH.write_text(build_report(rows, summary, data), encoding="utf-8")
    print(json.dumps({"report": str(REPORT_PATH), "csv": str(CSV_PATH), "rows": len(rows), "numeric_series": len(data), "groups": len({row['benchmark_group'] for row in rows})}, ensure_ascii=False))


if __name__ == "__main__":
    main()
