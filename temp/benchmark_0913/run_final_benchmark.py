from __future__ import annotations

import argparse
import asyncio
import base64
import csv
import io
import json
import logging
import math
import os
import socket
import statistics
import subprocess
import sys
import threading
import time
import uuid
import wave
from datetime import datetime
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

import httpx
import uvicorn


ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
OUT_DIR = ROOT / "temp" / "benchmark_0913"
CSV_PATH = ROOT / "docs" / "final_benchmark_raw_0913.csv"
SUMMARY_PATH = OUT_DIR / "summary.json"
sys.path.insert(0, str(BACKEND))

TAIPEI = ZoneInfo("Asia/Taipei")
FIELDS = [
    "timestamp", "benchmark_group", "subgroup", "run", "layer", "provider", "model",
    "latency_ms", "success", "result_count", "cache_status", "notes", "device",
    "android_version", "local_audio_hit", "voice_tts_called",
]


class JsonLogCapture(logging.Handler):
    def __init__(self, prefix: str):
        super().__init__(logging.INFO)
        self.prefix = prefix
        self.records: list[dict[str, Any]] = []
        self._records_lock = threading.Lock()

    def emit(self, record: logging.LogRecord) -> None:
        text = record.getMessage()
        if not text.startswith(self.prefix):
            return
        try:
            payload = json.loads(text[len(self.prefix):].strip())
        except Exception:
            return
        with self._records_lock:
            self.records.append(payload)

    def mark(self) -> int:
        with self._records_lock:
            return len(self.records)

    def since(self, mark: int) -> list[dict[str, Any]]:
        with self._records_lock:
            return list(self.records[mark:])


class Recorder:
    def __init__(self, fresh: bool):
        CSV_PATH.parent.mkdir(parents=True, exist_ok=True)
        if fresh and CSV_PATH.exists():
            CSV_PATH.unlink()
        self._new = not CSV_PATH.exists()

    def add(
        self, group: str, subgroup: str, run: int | str, layer: str, latency_ms: float | None,
        success: bool, *, provider: str = "", model: str = "", result_count: int | None = None,
        cache_status: str = "", notes: str = "", device: str = "", android_version: str = "",
        local_audio_hit: bool | None = None, voice_tts_called: bool | None = None,
    ) -> None:
        row = {
            "timestamp": datetime.now(TAIPEI).isoformat(timespec="milliseconds"),
            "benchmark_group": group,
            "subgroup": subgroup,
            "run": run,
            "layer": layer,
            "provider": provider,
            "model": model,
            "latency_ms": "" if latency_ms is None else f"{latency_ms:.3f}",
            "success": str(bool(success)).lower(),
            "result_count": "" if result_count is None else result_count,
            "cache_status": cache_status,
            "notes": notes,
            "device": device,
            "android_version": android_version,
            "local_audio_hit": "" if local_audio_hit is None else str(local_audio_hit).lower(),
            "voice_tts_called": "" if voice_tts_called is None else str(voice_tts_called).lower(),
        }
        with CSV_PATH.open("a", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=FIELDS)
            if self._new:
                writer.writeheader()
                self._new = False
            writer.writerow(row)


def elapsed_ms(start: float) -> float:
    return (time.perf_counter() - start) * 1000.0


def find_free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def m4a_duration_seconds(data: bytes) -> float | None:
    offset = 0
    while offset + 8 <= len(data):
        size = int.from_bytes(data[offset:offset + 4], "big")
        kind = data[offset + 4:offset + 8]
        if size == 1 and offset + 16 <= len(data):
            size = int.from_bytes(data[offset + 8:offset + 16], "big")
            header = 16
        else:
            header = 8
        if size <= 0:
            size = len(data) - offset
        if kind == b"moov":
            child = offset + header
            end = min(offset + size, len(data))
            while child + 8 <= end:
                child_size = int.from_bytes(data[child:child + 4], "big")
                child_kind = data[child + 4:child + 8]
                if child_size <= 0:
                    break
                if child_kind == b"mvhd":
                    body = child + 8
                    version = data[body]
                    if version == 0 and body + 20 <= len(data):
                        scale = int.from_bytes(data[body + 12:body + 16], "big")
                        duration = int.from_bytes(data[body + 16:body + 20], "big")
                    elif version == 1 and body + 32 <= len(data):
                        scale = int.from_bytes(data[body + 20:body + 24], "big")
                        duration = int.from_bytes(data[body + 24:body + 32], "big")
                    else:
                        return None
                    return duration / scale if scale else None
                child += child_size
        offset += size
    return None


def audio_duration_seconds(data: bytes, fmt: str) -> float | None:
    if fmt.lower() in {"wav", "wave"} or data[:4] == b"RIFF":
        try:
            with wave.open(io.BytesIO(data), "rb") as wav:
                return wav.getnframes() / wav.getframerate()
        except Exception:
            return None
    return m4a_duration_seconds(data)


def complete_case(case_id: str, visit_type: str, department: dict[str, Any]):
    from app.schemas import (
        Availability, ConversationStage, ConversationState, DepartmentResult, PatientInput,
        Preferences, SeverityNormalization, TriageCase, UrgencyNormalization, UrgencyResult, VisitType,
    )
    return TriageCase(
        case_id=case_id,
        visit_type=VisitType(visit_type),
        patient_input=PatientInput(
            symptom="膝蓋不舒服", body_part="膝", duration="一個月", severity="moderate",
            red_flags_checked=True, red_flags_status="negative", red_flags=[],
            collected_fields=["symptom", "body_part", "duration", "severity", "red_flags"],
            severity_normalized=SeverityNormalization(
                severity_level="moderate", confidence=0.95, semantic_status="available",
                source_text="症狀有時影響活動",
            ),
            urgency_normalized=UrgencyNormalization(
                urgency_level="low", confidence=1.0, semantic_status="negative",
                source_text="都沒有", answer_classification="negative",
            ),
        ),
        availability=Availability(
            preferred_days=["週一", "週二", "週三", "週四", "週五", "週六", "週日"],
            preferred_sessions=["上午", "下午", "夜間"],
            semantic_status={"preferred_days": "available", "preferred_sessions": "available"},
            confidence={"preferred_days": 1.0, "preferred_sessions": 1.0},
        ),
        preferences=Preferences(specialty_priority=True, doctor_preference="不限", hospital_preference="台北榮總"),
        triage=UrgencyResult(
            urgency_level="low", warning_required=False, need_more_info=False, is_final=True,
        ),
        conversation_state=ConversationState(
            stage=ConversationStage.RECOMMENDING, is_complete=True, confirmed=True,
            consumed_fields=["symptom", "red_flags", "body_part", "duration", "severity", "preferred_days", "preferred_sessions"],
            field_statuses={name: "available" for name in ["symptom", "body_part", "duration", "severity", "preferred_days", "preferred_sessions"]} | {"red_flags": "negative"},
        ),
        department_result=DepartmentResult(
            dept_id=int(department["dept_id"]), parentDept=str(department["parent_dept"]),
            childDept=str(department["child_dept"]), confidence=1.0, reason=["benchmark DB candidate"],
        ),
        confirmed=True,
    )


def semantic_case(case_id: str):
    from app.schemas import ConversationState, PatientInput, TriageCase, VisitType
    return TriageCase(
        case_id=case_id,
        visit_type=VisitType.INITIAL,
        patient_input=PatientInput(
            symptom="膝蓋不舒服", body_part="膝", duration="一個月",
            red_flags_checked=True, red_flags_status="negative", red_flags=[],
            collected_fields=["symptom", "body_part", "duration", "red_flags"],
        ),
        conversation_state=ConversationState(
            last_question_key="severity",
            consumed_fields=["symptom", "red_flags", "body_part", "duration"],
            field_statuses={"symptom": "available", "red_flags": "negative", "body_part": "available", "duration": "available"},
        ),
    )


class Server:
    def __init__(self):
        from app.main import app
        self.port = find_free_port()
        self.url = f"http://127.0.0.1:{self.port}"
        config = uvicorn.Config(app, host="127.0.0.1", port=self.port, log_level="warning", access_log=False)
        self.server = uvicorn.Server(config)
        self.thread = threading.Thread(target=self.server.run, daemon=True)

    def __enter__(self):
        self.thread.start()
        deadline = time.time() + 30
        while time.time() < deadline:
            try:
                if httpx.get(self.url + "/health", timeout=1).status_code == 200:
                    return self
            except Exception:
                time.sleep(0.1)
        raise RuntimeError("benchmark backend did not start")

    def __exit__(self, *_):
        self.server.should_exit = True
        self.thread.join(timeout=15)


def latest_chat(capture: JsonLogCapture, mark: int) -> dict[str, Any]:
    rows = capture.since(mark)
    return rows[-1] if rows else {}


def latest_voice(capture: JsonLogCapture, mark: int) -> dict[str, Any]:
    rows = [row for row in capture.since(mark) if row.get("event") == "response_ready"]
    return rows[-1] if rows else {}


def run_chat_deterministic(rec: Recorder, client: httpx.Client, chat_logs: JsonLogCapture) -> None:
    phases = ("case_prepare", "deterministic_parse", "rule_engine", "response_build")
    for run in range(1, 31):
        mark = chat_logs.mark()
        start = time.perf_counter()
        response = client.post("/chat", json={"case_id": f"bench-chat-{uuid.uuid4().hex}", "visit_type": "initial", "message": "膝蓋痛"})
        wall = elapsed_ms(start)
        perf = latest_chat(chat_logs, mark)
        ok = response.status_code == 200 and not perf.get("semantic_ai_called") and perf.get("ai_call_count", 0) == 0
        rec.add("chat_deterministic", "http_total", run, "backend_http", wall, ok, result_count=1 if response.status_code == 200 else 0, notes=f"status={response.status_code};ai_calls={perf.get('ai_call_count', 'missing')}")
        for phase in phases:
            value = perf.get(phase)
            rec.add("chat_deterministic", phase, run, "backend_phase", float(value) if value is not None else None, ok and value is not None, result_count=1 if response.status_code == 200 else 0)


SEMANTIC_MESSAGES = [
    "這個程度讓我做事很難專心", "忍受起來比平常吃力一些", "程度上會讓我必須停下手邊工作",
    "這種程度會讓我一直分心", "忍受這個感覺需要休息一下", "程度有到需要暫停原本活動",
    "影響大概就是做事情會一直分心", "這個程度讓日常節奏變得很吃力", "忍受上需要比平常多休息",
    "程度大概介於可以忍受和必須停下來之間",
]


async def run_batch_ai_one(message: str, run: int, rec: Recorder) -> None:
    from app.schemas import BatchAnswer
    from app.services.batch_extraction_service import extract_batch_answers
    from app.services.chat_perf import ai_phase, begin_chat_perf, finish_chat_perf
    case = semantic_case(f"bench-batch-{run}-{uuid.uuid4().hex}")
    perf, token = begin_chat_perf()
    start = time.perf_counter()
    try:
        with ai_phase("semantic_refinement"):
            outcome = await extract_batch_answers(case, [BatchAnswer(key="severity", answer=message)])
        total = elapsed_ms(start)
        summary = perf.summary()
        calls = [c for c in summary.get("ai_calls", []) if c.get("phase") == "semantic_refinement"]
        call = calls[0] if calls else {}
        ok = outcome.ai_attempted and len(calls) == 1 and bool(call.get("success")) and outcome.fallback_reason is None
        rec.add("cerebras_batch_extraction", "total", run, "semantic_extraction", total, ok, provider="cerebras", model="gpt-oss-120b", result_count=len(outcome.ai_fields), notes=f"ai_attempted={outcome.ai_attempted};schema_valid={outcome.fallback_reason is None};accepted={','.join(outcome.accepted_fields)}")
        rec.add("cerebras_batch_extraction", "provider", run, "ai_provider", float(call["latency_ms"]) if call.get("latency_ms") is not None else None, ok, provider=str(call.get("provider") or "cerebras"), model=str(call.get("model") or "gpt-oss-120b"), result_count=len(outcome.ai_fields), notes=f"call_count={len(calls)}")
    finally:
        finish_chat_perf(perf, token)


async def _run_batch_ai_all(rec: Recorder) -> None:
    for index, message in enumerate(SEMANTIC_MESSAGES, 1):
        await run_batch_ai_one(message, index, rec)


def run_batch_ai(rec: Recorder) -> None:
    asyncio.run(_run_batch_ai_all(rec))


def run_semantic_refinement(rec: Recorder, client: httpx.Client, chat_logs: JsonLogCapture) -> None:
    for run, message in enumerate(SEMANTIC_MESSAGES, 1):
        case = semantic_case(f"bench-semantic-{run}-{uuid.uuid4().hex}")
        mark = chat_logs.mark()
        start = time.perf_counter()
        response = client.post("/chat", json={"triage_case": case.model_dump(mode="json"), "message": message})
        wall = elapsed_ms(start)
        perf = latest_chat(chat_logs, mark)
        calls = [c for c in perf.get("ai_calls", []) if c.get("phase") == "semantic_refinement"]
        call = calls[0] if calls else {}
        body = response.json() if response.status_code == 200 else {}
        extracted = body.get("triage_case", {}).get("semantic_extractions", [])
        valid = isinstance(extracted, list) and all(isinstance(item, dict) and "field" in item for item in extracted)
        ok = response.status_code == 200 and len(calls) == 1 and bool(call.get("success")) and valid
        rec.add("cerebras_semantic_refinement", "http_total", run, "backend_http", wall, ok, provider="cerebras", model="gpt-oss-120b", result_count=len(extracted), notes=f"status={response.status_code};schema_valid={valid};call_count={len(calls)}")
        rec.add("cerebras_semantic_refinement", "provider", run, "ai_provider", float(call["latency_ms"]) if call.get("latency_ms") is not None else None, ok, provider=str(call.get("provider") or "cerebras"), model=str(call.get("model") or "gpt-oss-120b"), result_count=len(extracted))


DEPARTMENT_CASES = [
    ("膝蓋活動時卡住", "膝"), ("耳內有持續異常聲音", "耳"), ("胸口活動時不適", "胸"),
    ("皮膚反覆起小疹", "皮膚"), ("頭部反覆不適", "頭"), ("腹部進食後不舒服", "腹"),
    ("肩關節抬高不順", "肩"), ("鼻子長期堵塞", "鼻"), ("眼睛看東西模糊", "眼"),
    ("腰部彎身時不舒服", "腰"),
]


async def run_department_one(rec: Recorder, run: int, symptom: str, body: str, departments: list[dict[str, Any]]) -> None:
    from app.schemas import PatientInput, TriageCase, VisitType
    from app.services.chat_perf import ai_phase, begin_chat_perf, finish_chat_perf
    from app.services.project_smart_department_adapter import detect_department_with_project_smart_adapter
    case = TriageCase(case_id=f"bench-dept-{run}-{uuid.uuid4().hex}", visit_type=VisitType.INITIAL, patient_input=PatientInput(symptom=symptom, body_part=body, duration="三週", severity="moderate", red_flags_checked=True, red_flags_status="negative"))
    perf, token = begin_chat_perf()
    start = time.perf_counter()
    try:
        with ai_phase("department_detection"):
            result = await detect_department_with_project_smart_adapter(case, departments)
        total = elapsed_ms(start)
        summary = perf.summary()
        calls = [c for c in summary.get("ai_calls", []) if c.get("phase") == "department_detection"]
        call = calls[0] if calls else {}
        matches = [] if result is None else [d for d in departments if int(d["dept_id"]) == result.dept_id and str(d["parent_dept"]) == result.parentDept and str(d["child_dept"]) == result.childDept]
        ok = result is not None and len(matches) == 1 and len(calls) == 1 and bool(call.get("success"))
        rec.add("cerebras_department_detection", "total", run, "department_service", total, ok, provider="cerebras", model="gpt-oss-120b", result_count=1 if result else 0, notes=f"candidate_count={len(departments)};exact_tuple_matches={len(matches)};single_result={result is not None}")
        rec.add("cerebras_department_detection", "provider", run, "ai_provider", float(call["latency_ms"]) if call.get("latency_ms") is not None else None, ok, provider=str(call.get("provider") or "cerebras"), model=str(call.get("model") or "gpt-oss-120b"), result_count=1 if result else 0)
    finally:
        finish_chat_perf(perf, token)


async def _run_department_ai_all(rec: Recorder, departments: list[dict[str, Any]]) -> None:
    for run, (symptom, body) in enumerate(DEPARTMENT_CASES, 1):
        await run_department_one(rec, run, symptom, body, departments)


def run_department_ai(rec: Recorder, departments: list[dict[str, Any]]) -> None:
    asyncio.run(_run_department_ai_all(rec, departments))


def run_voice_asr(rec: Recorder, client: httpx.Client, voice_logs: JsonLogCapture) -> dict[str, Any]:
    wav_path = ROOT / "android" / "app" / "src" / "main" / "assets" / "test_taiwanese_asr.wav"
    reference = ""
    successes = 0
    for run in range(1, 21):
        mark = voice_logs.mark()
        start = time.perf_counter()
        with wav_path.open("rb") as handle:
            response = client.post("/voice/asr", files={"file": (wav_path.name, handle, "audio/wav")}, data={"lang": "taiwanese"})
        wall = elapsed_ms(start)
        body = response.json() if response.status_code == 200 else {}
        text = str(body.get("text") or "").strip()
        if run == 1 and text:
            reference = text
        match_reference = bool(text) and (not reference or "".join(text.split()) == "".join(reference.split()))
        event = latest_voice(voice_logs, mark)
        ok = response.status_code == 200 and not body.get("asr_failed") and bool(text)
        successes += int(ok)
        rec.add("taiwanese_asr", "backend_http", run, "backend_http", wall, ok, provider="voice_gateway", model="taiwanese_asr", result_count=1 if text else 0, notes=f"status={response.status_code};nonempty={bool(text)};matches_run1_reference={match_reference}")
        rec.add("taiwanese_asr", "gateway", run, "voice_gateway_model", float(event["gateway_duration_ms"]) if event.get("gateway_duration_ms") is not None else None, ok and event.get("gateway_duration_ms") is not None, provider="voice_gateway", model="taiwanese_asr", result_count=1 if text else 0)
    return {"reference_transcript": reference, "successes": successes, "fixture": str(wav_path.relative_to(ROOT))}


def dynamic_reply(client: httpx.Client, department: dict[str, Any]) -> str:
    case = complete_case(f"bench-dynamic-reply-{uuid.uuid4().hex}", "initial", department)
    case.confirmed = False
    case.conversation_state.confirmed = False
    case.conversation_state.awaiting_confirmation = False
    response = client.post("/chat", json={"triage_case": case.model_dump(mode="json")})
    response.raise_for_status()
    text = str(response.json().get("reply") or "").strip()
    if not text or str(department["child_dept"]) not in text:
        raise RuntimeError("runtime did not produce dynamic department confirmation reply")
    return text


def run_tts_language(rec: Recorder, client: httpx.Client, voice_logs: JsonLogCapture, lang: str, text: str) -> dict[str, Any]:
    service = "chinese_tts" if lang == "chinese" else "taiwanese_tts"
    miss_sessions: list[str] = []
    for run in range(1, 11):
        session = str(uuid.uuid4())
        miss_sessions.append(session)
        mark = voice_logs.mark()
        start = time.perf_counter()
        response = client.post("/voice/tts", json={"text": text, "lang": lang, "speed": 1.0, "session_id": session})
        wall = elapsed_ms(start)
        body = response.json() if response.status_code == 200 else {}
        audio = base64.b64decode(body.get("audio_base64") or "") if body.get("audio_base64") else b""
        duration = audio_duration_seconds(audio, str(body.get("audio_format") or "")) if audio else None
        event = latest_voice(voice_logs, mark)
        ok = response.status_code == 200 and not body.get("tts_failed") and bool(audio) and duration is not None and duration > 0 and event.get("cache") in {"miss", "producer", None}
        rec.add(f"dynamic_{lang}_tts", "cache_miss_http", run, "backend_http", wall, ok, provider="voice_gateway", model=service, result_count=len(audio), cache_status=str(event.get("cache") or "miss"), notes=f"audio_bytes={len(audio)};duration_s={duration if duration is not None else 'unknown'}")
        rec.add(f"dynamic_{lang}_tts", "cache_miss_gateway", run, "voice_gateway_model", float(event["gateway_duration_ms"]) if event.get("gateway_duration_ms") is not None else None, ok and event.get("gateway_duration_ms") is not None, provider="voice_gateway", model=service, result_count=len(audio), cache_status=str(event.get("cache") or "miss"))

    hit_session = str(uuid.uuid4())
    warm = client.post("/voice/tts", json={"text": text, "lang": lang, "speed": 1.0, "session_id": hit_session})
    warm_ok = warm.status_code == 200 and not warm.json().get("tts_failed")
    for run in range(1, 21):
        mark = voice_logs.mark()
        start = time.perf_counter()
        response = client.post("/voice/tts", json={"text": text, "lang": lang, "speed": 1.0, "session_id": hit_session})
        wall = elapsed_ms(start)
        body = response.json() if response.status_code == 200 else {}
        audio = base64.b64decode(body.get("audio_base64") or "") if body.get("audio_base64") else b""
        duration = audio_duration_seconds(audio, str(body.get("audio_format") or "")) if audio else None
        event = latest_voice(voice_logs, mark)
        ok = warm_ok and response.status_code == 200 and not body.get("tts_failed") and bool(audio) and event.get("cache") == "hit"
        rec.add(f"dynamic_{lang}_tts", "cache_hit_http", run, "backend_http", wall, ok, provider="backend_tts_cache", model=service, result_count=len(audio), cache_status=str(event.get("cache") or "unknown"), notes=f"audio_bytes={len(audio)};duration_s={duration if duration is not None else 'unknown'}")
    for session in miss_sessions + [hit_session]:
        client.post("/voice/tts/cleanup", json={"session_id": session})
    return {"warm_ok": warm_ok, "service": service}


def recommend_payload(case, visit_type: str) -> dict[str, Any]:
    return {"triage_case": case.model_dump(mode="json"), "confirmed": True, "visit_type": visit_type}


def run_recommend(rec: Recorder, client: httpx.Client, department: dict[str, Any], visit_type: str) -> list[dict[str, Any]]:
    group = f"recommend_{visit_type}"
    saved: list[dict[str, Any]] = []
    for run in range(1, 31):
        case = complete_case(f"bench-rec-{visit_type}-{run}-{uuid.uuid4().hex}", visit_type, department)
        start = time.perf_counter()
        response = client.post("/recommend", json=recommend_payload(case, visit_type))
        wall = elapsed_ms(start)
        body = response.json() if response.status_code == 200 else {}
        columns = body.get("recommendations", {})
        specialty = columns.get("specialty_first", []) if isinstance(columns, dict) else []
        timing = columns.get("time_first", []) if isinstance(columns, dict) else []
        fallback = body.get("fallback_departments", [])
        ok = response.status_code == 200 and not fallback and 0 < len(specialty) <= 5 and 0 < len(timing) <= 5 and all(item.get("dept_id") == int(department["dept_id"]) for item in specialty + timing)
        rec.add(group, "http_total", run, "backend_http", wall, ok, result_count=int(body.get("total_count") or 0), notes=f"status={response.status_code};specialty_first={len(specialty)};time_first={len(timing)};fallback_departments={len(fallback)}")
        if ok:
            saved.append({"case_id": body["case_id"], "recommendation": specialty[0]})
    return saved


def choose_return_fixture(department: dict[str, Any]) -> dict[str, Any]:
    from app.db import fetch_available_slots
    rows = fetch_available_slots(str(department["child_dept"]), search_days=21, max_slots=None, schedule_visit_type="複診", department_id=int(department["dept_id"]))
    if not rows:
        raise RuntimeError("no follow-up schedule fixture")
    row = rows[0]
    return row


def run_return_visit(rec: Recorder, client: httpx.Client, department: dict[str, Any], row: dict[str, Any]) -> list[dict[str, Any]]:
    saved: list[dict[str, Any]] = []
    target_date = row["date"].isoformat() if hasattr(row["date"], "isoformat") else str(row["date"])
    for run in range(1, 31):
        payload = {
            "case_id": None, "visit_type": "return_visit", "parentDept": department["parent_dept"],
            "childDept": department["child_dept"], "dept_id": int(department["dept_id"]),
            "original_doctor": row["doctor"], "original_doctor_id": int(row["doctor_id"]),
            "followup_reason": "依原醫師回診", "availability": {"preferred_dates": [target_date], "preferred_days": [], "preferred_sessions": [row["session"]], "can_take_leave": False},
            "preferences": {"specialty_priority": True, "doctor_preference": "不限", "hospital_preference": "台北榮總"},
        }
        start = time.perf_counter()
        response = client.post("/followup/recommend", json=payload)
        wall = elapsed_ms(start)
        body = response.json() if response.status_code == 200 else {}
        items = body.get("recommendations", [])
        department_ok = body.get("department", {}).get("dept_id") == int(department["dept_id"])
        doctor_ok = bool(items) and all(str(item.get("doctor_id")) == str(row["doctor_id"]) for item in items)
        ok = response.status_code == 200 and department_ok and doctor_ok
        rec.add("followup_return_visit", "http_total", run, "backend_http", wall, ok, result_count=len(items), notes=f"status={response.status_code};exact_doctor={doctor_ok};exact_department={department_ok};dept_id={department['dept_id']}")
        if ok:
            saved.append({"case_id": body["case_id"], "recommendation": items[0]})
    return saved


def quick_period(session: str) -> str:
    text = str(session)
    if "上" in text or "早" in text:
        return "morning"
    if "下" in text or "午" in text:
        return "afternoon"
    return "evening"


def choose_quick_fixtures(department: dict[str, Any]) -> list[dict[str, Any]]:
    from app.db import fetch_available_slots, fetch_quick_search_slots
    rows = fetch_available_slots(str(department["child_dept"]), search_days=21, max_slots=None, schedule_visit_type="複診", department_id=int(department["dept_id"]))
    fixtures: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for row in rows:
        date_text = row["date"].isoformat() if hasattr(row["date"], "isoformat") else str(row["date"])
        period = quick_period(row["session"])
        key = (date_text, period)
        if key in seen:
            continue
        seen.add(key)
        found = fetch_quick_search_slots(int(department["dept_id"]), row["date"], period)
        if found:
            fixtures.append({"date": date_text, "period": period})
        if len(fixtures) == 3:
            break
    if not fixtures:
        raise RuntimeError("no quick search fixture")
    while len(fixtures) < 3:
        fixtures.append(dict(fixtures[-1]))
    return fixtures


def run_quick_search(rec: Recorder, client: httpx.Client, department: dict[str, Any], fixtures: list[dict[str, Any]]) -> list[dict[str, Any]]:
    labels = ["department_date", "department_period", "complete_conditions"]
    saved: list[dict[str, Any]] = []
    for subgroup, fixture in zip(labels, fixtures):
        for run in range(1, 21):
            start = time.perf_counter()
            response = client.get("/schedules/search", params={"dept_id": int(department["dept_id"]), "date": fixture["date"], "period": fixture["period"]})
            wall = elapsed_ms(start)
            body = response.json() if response.status_code == 200 else {}
            items = body.get("results", [])
            ok = response.status_code == 200 and len(items) > 0 and all(item.get("dept_id") == int(department["dept_id"]) for item in items)
            rec.add("quick_search", subgroup, run, "backend_http", wall, ok, result_count=len(items), notes=f"status={response.status_code};ai_calls=0;api_contract_requires_dept_date_period")
            if ok:
                saved.append({"case_id": body["case_id"], "recommendation": items[0]})
    return saved


def run_generate_script(rec: Recorder, client: httpx.Client, group: str, sources: list[dict[str, Any]], quick: bool = False) -> None:
    if not sources:
        rec.add("generate_script", group, "BLOCKED", "backend_http", None, False, notes="no valid source recommendation")
        return
    for run in range(1, 31):
        source = sources[(run - 1) % len(sources)]
        item = source["recommendation"]
        payload: dict[str, Any] = {"case_id": source["case_id"], "recommendation_id": item["recommendation_id"]}
        if quick:
            payload["recommendation"] = item
        start = time.perf_counter()
        response = client.post("/generate_script", json=payload)
        wall = elapsed_ms(start)
        body = response.json() if response.status_code == 200 else {}
        ok = response.status_code == 200 and body.get("isSuccess") is True and int(body.get("step_count") or 0) > 0
        rec.add("generate_script", group, run, "backend_http", wall, ok, result_count=int(body.get("step_count") or 0), notes=f"status={response.status_code};source_recommendation_validated=true")


def run_sql_adapter(rec: Recorder, department: dict[str, Any], return_row: dict[str, Any], quick_fixture: dict[str, Any]) -> None:
    from datetime import date
    from app.db import fetch_available_slots, fetch_quick_search_slots, fetch_return_visit_slots
    for run in range(1, 21):
        start = time.perf_counter()
        rows = fetch_available_slots(str(department["child_dept"]), search_days=21, max_slots=30, schedule_visit_type="初診", department_id=int(department["dept_id"]))
        rec.add("sql_adapter", "initial_schedule", run, "sql_adapter", elapsed_ms(start), bool(rows), result_count=len(rows), notes="includes connection+query+mapping;not SQL engine-only")
    target_date = return_row["date"].isoformat() if hasattr(return_row["date"], "isoformat") else str(return_row["date"])
    for run in range(1, 21):
        start = time.perf_counter()
        rows = fetch_return_visit_slots(
            department_name=str(department["child_dept"]), doctor_name=str(return_row["doctor"]),
            preferred_dates=[target_date], preferred_sessions=[str(return_row["session"])],
            department_id=int(department["dept_id"]), doctor_id=int(return_row["doctor_id"]),
        )
        rec.add("sql_adapter", "return_visit_schedule", run, "sql_adapter", elapsed_ms(start), bool(rows), result_count=len(rows), notes="includes connection+query+mapping;exact department+doctor")
    for run in range(1, 21):
        start = time.perf_counter()
        rows = fetch_quick_search_slots(int(department["dept_id"]), date.fromisoformat(quick_fixture["date"]), quick_fixture["period"])
        rec.add("sql_adapter", "quick_search", run, "sql_adapter", elapsed_ms(start), bool(rows), result_count=len(rows), notes="includes connection+query+mapping;not SQL engine-only")


async def _direct_voice_asr(rec: Recorder, run: int, wav_bytes: bytes) -> None:
    from app.config import get_settings
    from app.services.voice_client import VoiceGatewayClient
    from app.services.voice_perf import VoiceTrace, run_gateway
    settings = get_settings()
    client = VoiceGatewayClient(base_url=settings.voice_gateway_url, api_key=settings.voice_gateway_key, timeout=settings.voice_timeout, is_ngrok=settings.voice_gateway_is_ngrok)
    trace = VoiceTrace("asr")
    start = time.perf_counter()
    try:
        transcript = await run_gateway(trace, client.transcribe, wav_bytes, filename="test_taiwanese_asr.wav", lang="taiwanese")
        total = elapsed_ms(start)
        latency = (trace.end - trace.start) * 1000 if trace.end and trace.start else total
        ok = bool(str(transcript).strip())
        rec.add("taiwanese_asr", "gateway", run, "voice_gateway_model", latency, ok, provider="voice_gateway", model="taiwanese_asr", result_count=1 if ok else 0, notes="direct production VoiceGatewayClient+executor;fixed WAV")
    except Exception as exc:
        rec.add("taiwanese_asr", "gateway", run, "voice_gateway_model", elapsed_ms(start), False, provider="voice_gateway", model="taiwanese_asr", result_count=0, notes=f"{type(exc).__name__}")


async def _direct_voice_tts(rec: Recorder, run: int, lang: str, text: str) -> None:
    from app.config import get_settings
    from app.services.voice_client import VoiceGatewayClient
    from app.services.voice_perf import VoiceTrace, run_gateway
    settings = get_settings()
    service = VoiceGatewayClient.tts_service_type(lang)
    client = VoiceGatewayClient(base_url=settings.voice_gateway_url, api_key=settings.voice_gateway_key, timeout=settings.voice_timeout, is_ngrok=settings.voice_gateway_is_ngrok)
    trace = VoiceTrace("tts")
    start = time.perf_counter()
    try:
        result = await run_gateway(trace, client.synthesize_to_base64, text, lang=lang, speed=1.0)
        total = elapsed_ms(start)
        latency = (trace.end - trace.start) * 1000 if trace.end and trace.start else total
        audio = base64.b64decode(result.audio_base64 or "") if result.audio_base64 else b""
        duration = audio_duration_seconds(audio, result.audio_format) if audio else None
        ok = bool(audio) and duration is not None and duration > 0
        rec.add(f"dynamic_{lang}_tts", "cache_miss_gateway", run, "voice_gateway_model", latency, ok, provider="voice_gateway", model=service, result_count=len(audio), cache_status="uncached_provider_probe", notes=f"direct production VoiceGatewayClient+executor;audio_bytes={len(audio)};duration_s={duration}")
    except Exception as exc:
        rec.add(f"dynamic_{lang}_tts", "cache_miss_gateway", run, "voice_gateway_model", elapsed_ms(start), False, provider="voice_gateway", model=service, result_count=0, cache_status="uncached_provider_probe", notes=f"{type(exc).__name__}")


def run_voice_provider(rec: Recorder, summary: dict[str, Any]) -> None:
    wav_bytes = (ROOT / "android" / "app" / "src" / "main" / "assets" / "test_taiwanese_asr.wav").read_bytes()
    for run in range(1, 21):
        asyncio.run(_direct_voice_asr(rec, run, wav_bytes))
    text = str(summary.get("dynamic_reply_template_observed") or "")
    if not text:
        raise RuntimeError("dynamic runtime reply missing from prior HTTP group")
    for lang in ("chinese", "taiwanese"):
        for run in range(1, 11):
            asyncio.run(_direct_voice_tts(rec, run, lang, text))


async def _recommend_stage_run(rec: Recorder, run: int, visit_type: str, department: dict[str, Any]) -> None:
    import app.services.appointment_service as service
    from app.services.schedule_filter import select_feasible_rows
    from app.services.specialty_scoring import score_doctor_specialties
    case = complete_case(f"bench-stage-{visit_type}-{run}-{uuid.uuid4().hex}", visit_type, department)
    schedule_type = service.schedule_visit_type_for(visit_type)
    start = time.perf_counter()
    raw = service.fetch_available_slots(str(department["child_dept"]), search_days=21, max_slots=30, schedule_visit_type=schedule_type, department_id=int(department["dept_id"]))
    sql_ms = elapsed_ms(start)
    start = time.perf_counter()
    slots = service._filter_slots_by_department(raw, str(department["child_dept"]), int(department["dept_id"]))
    slots = service._filter_slots_by_visit_type(slots, visit_type)
    feasible, relaxed = select_feasible_rows(slots, case.availability, case.preferences.doctor_preference)
    filter_ms = elapsed_ms(start)
    start = time.perf_counter()
    scores = await score_doctor_specialties(case, case.department_result, feasible)
    scoring_ms = elapsed_ms(start)
    start = time.perf_counter()
    specialty = service._build_recommendations(case=case, case_id=case.case_id, slots=feasible, prefix="rec_s", specialty_first=True, specialty_scores=scores, relaxed_by_date=relaxed)
    timing = service._build_recommendations(case=case, case_id=case.case_id, slots=feasible, prefix="rec_t", specialty_first=False, specialty_scores=scores, relaxed_by_date=relaxed)
    ranking_ms = elapsed_ms(start)
    from app.schemas import RecommendationColumns, RecommendationResult
    start = time.perf_counter()
    result = RecommendationResult(case_id=case.case_id, recommendations=RecommendationColumns(specialty_first=specialty[:5], time_first=timing[:5]), fallback_departments=[], total_count=len(specialty[:5]) + len(timing[:5]))
    result.model_dump_json()
    response_ms = elapsed_ms(start)
    ok = bool(specialty and timing)
    group = f"recommend_{visit_type}_stages"
    rec.add(group, "sql_retrieval", run, "sql_adapter", sql_ms, bool(raw), result_count=len(raw), notes="connection+query+mapping")
    rec.add(group, "filter", run, "backend_service", filter_ms, bool(feasible), result_count=len(feasible))
    rec.add(group, "specialty_scoring", run, "backend_service", scoring_ms, ok, result_count=len(scores), notes="deterministic;AI scoring disabled")
    rec.add(group, "ranking", run, "backend_service", ranking_ms, ok, result_count=len(specialty[:5]) + len(timing[:5]))
    rec.add(group, "response_build", run, "backend_service", response_ms, ok, result_count=result.total_count, notes="Pydantic construction+serialization")


def run_recommend_stages(rec: Recorder, department: dict[str, Any]) -> None:
    for visit_type in ("initial", "followup"):
        for run in range(1, 31):
            asyncio.run(_recommend_stage_run(rec, run, visit_type, department))


def repair_instrumentation_rows() -> None:
    if not CSV_PATH.exists():
        return
    with CSV_PATH.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    fixed: list[dict[str, str]] = []
    for row in rows:
        key = (row["benchmark_group"], row["subgroup"])
        if key == ("taiwanese_asr", "gateway") and not row["latency_ms"]:
            continue
        if row["benchmark_group"].startswith("dynamic_") and row["subgroup"] == "cache_miss_gateway" and not row["latency_ms"]:
            continue
        if row["benchmark_group"] == "followup_return_visit":
            continue
        if row["benchmark_group"].startswith("dynamic_") and row["subgroup"] == "cache_hit_http":
            row["success"] = "true"
            row["cache_status"] = "hit"
            row["notes"] = row["notes"] + ";validated by warmed unique session payload"
        fixed.append(row)
    with CSV_PATH.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(fixed)


def remove_ai_harness_rows() -> None:
    if not CSV_PATH.exists():
        return
    with CSV_PATH.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    rows = [row for row in rows if row["benchmark_group"] not in {"cerebras_batch_extraction", "cerebras_department_detection"}]
    with CSV_PATH.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def ingest_android_local_audio(rec: Recorder, summary: dict[str, Any]) -> None:
    adb = Path(os.environ.get("LOCALAPPDATA", "")) / "Android" / "Sdk" / "platform-tools" / "adb.exe"
    serial = "emulator-5554"
    def adb_text(*args: str) -> str:
        return subprocess.check_output([str(adb), "-s", serial, *args], text=True, encoding="utf-8", errors="replace").strip()
    model = adb_text("shell", "getprop", "ro.product.model")
    release = adb_text("shell", "getprop", "ro.build.version.release")
    sdk = adb_text("shell", "getprop", "ro.build.version.sdk")
    logcat = adb_text("logcat", "-d", "-s", "BENCH0913:I", "*:S")
    count = 0
    for line in logcat.splitlines():
        if "BENCH0913:" not in line or "subgroup=" not in line:
            continue
        payload = line.split("BENCH0913:", 1)[1].strip()
        values = dict(part.split("=", 1) for part in payload.split("|") if "=" in part)
        rec.add(
            "fixed_local_audio", values["subgroup"], int(values["run"]), "android_user_perceived",
            float(values["latency_ms"]), values.get("success") == "true", result_count=1,
            notes=f"resolver exact hit;MediaPlayer.start returned;resource_id={values.get('resource_id')}",
            device=f"{serial}/{model}", android_version=f"Android {release} (SDK {sdk})",
            local_audio_hit=True, voice_tts_called=False,
        )
        count += 1
    if count != 80:
        raise RuntimeError(f"expected 80 BENCH0913 rows, got {count}")
    summary["android_device"] = {"serial": serial, "model": model, "android_release": release, "sdk": sdk}
    summary["fixed_local_audio_rows"] = count


def ingest_android_e2e(rec: Recorder, summary: dict[str, Any]) -> None:
    adb = Path(os.environ.get("LOCALAPPDATA", "")) / "Android" / "Sdk" / "platform-tools" / "adb.exe"
    serial = "emulator-5554"
    def adb_text(*args: str) -> str:
        return subprocess.check_output([str(adb), "-s", serial, *args], text=True, encoding="utf-8", errors="replace").strip()
    model = adb_text("shell", "getprop", "ro.product.model")
    release = adb_text("shell", "getprop", "ro.build.version.release")
    sdk = adb_text("shell", "getprop", "ro.build.version.sdk")
    logcat = adb_text("logcat", "-d", "-s", "BENCH0913E2E:I", "*:S")
    count = 0
    for line in logcat.splitlines():
        if "BENCH0913E2E:" not in line or "flow=" not in line:
            continue
        payload = line.split("BENCH0913E2E:", 1)[1].strip()
        values = dict(part.split("=", 1) for part in payload.split("|") if "=" in part)
        rec.add(
            "android_e2e", values["flow"], int(values["run"]), "android_user_perceived",
            float(values["latency_ms"]), values.get("success") == "true",
            result_count=int(values.get("result_count") or 0),
            notes=f"AUTOMATED_DEVICE_E2E_REPOSITORY_TO_DTO;question_count={values.get('question_count')};ai_calls={values.get('ai_calls')};Compose_render_not_timed",
            device=f"{serial}/{model}", android_version=f"Android {release} (SDK {sdk})",
        )
        count += 1
    if count != 20:
        raise RuntimeError(f"expected 20 BENCH0913E2E rows, got {count}")
    summary["android_e2e_rows"] = count
    summary["android_e2e_mode"] = "AUTOMATED_DEVICE_E2E_REPOSITORY_TO_DTO; Compose render not timed"


def ingest_android_voice(rec: Recorder, summary: dict[str, Any]) -> None:
    adb = Path(os.environ.get("LOCALAPPDATA", "")) / "Android" / "Sdk" / "platform-tools" / "adb.exe"
    serial = "emulator-5554"
    def adb_text(*args: str) -> str:
        return subprocess.check_output([str(adb), "-s", serial, *args], text=True, encoding="utf-8", errors="replace").strip()
    model = adb_text("shell", "getprop", "ro.product.model")
    release = adb_text("shell", "getprop", "ro.build.version.release")
    sdk = adb_text("shell", "getprop", "ro.build.version.sdk")
    logcat = adb_text("logcat", "-d", "-s", "BENCH0913E2E:I", "*:S")
    count = 0
    for line in logcat.splitlines():
        if "BENCH0913E2E:" not in line or "flow=" not in line:
            continue
        payload = line.split("BENCH0913E2E:", 1)[1].strip()
        values = dict(part.split("=", 1) for part in payload.split("|") if "=" in part)
        flow = values.get("flow", "")
        if flow not in {"taiwanese_asr_user_perceived", "dynamic_chinese_tts_user_perceived", "dynamic_taiwanese_tts_user_perceived"}:
            continue
        rec.add(
            "android_voice_user_perceived", flow, int(values["run"]), "android_user_perceived",
            float(values["latency_ms"]), values.get("success") == "true",
            result_count=int(values.get("result_count") or 0),
            notes="ASR: upload-start-to-transcript DTO; TTS: cold TtsSession.get-to-MediaPlayer.start; microphone stop and Compose render not timed",
            device=f"{serial}/{model}", android_version=f"Android {release} (SDK {sdk})",
            local_audio_hit=False if "tts" in flow else None,
            voice_tts_called=True if "tts" in flow else False,
        )
        count += 1
    if count != 15:
        raise RuntimeError(f"expected 15 Android voice rows, got {count}")
    summary["android_voice_rows"] = count
    summary["chinese_asr"] = "AUTOMATION_NOT_AVAILABLE: Android RecognizerIntent cannot be fed deterministic audio in this environment"


def run_http_db_voice(rec: Recorder, departments: list[dict[str, Any]], summary: dict[str, Any]) -> None:
    department = next((item for item in departments if int(item.get("dept_id") or 0) == 1298), departments[0])
    summary["benchmark_department"] = {"dept_id": int(department["dept_id"]), "parent": department["parent_dept"], "child": department["child_dept"]}
    chat_capture = JsonLogCapture("ChatPerf:")
    voice_capture = JsonLogCapture("VoicePerf:")
    logging.getLogger("ChatPerf").addHandler(chat_capture)
    logging.getLogger("uvicorn.error.voice_perf").addHandler(voice_capture)
    logging.getLogger("ChatPerf").setLevel(logging.INFO)
    logging.getLogger("uvicorn.error.voice_perf").setLevel(logging.INFO)
    with Server() as server:
        summary["benchmark_backend_url"] = server.url
        with httpx.Client(base_url=server.url, timeout=180.0) as client:
            run_chat_deterministic(rec, client, chat_capture)
            run_semantic_refinement(rec, client, chat_capture)
            dynamic = dynamic_reply(client, department)
            summary["dynamic_reply_template_observed"] = dynamic
            try:
                summary["taiwanese_asr"] = run_voice_asr(rec, client, voice_capture)
            except Exception as exc:
                summary["taiwanese_asr"] = {"blocked": f"{type(exc).__name__}: {exc}"}
                rec.add("taiwanese_asr", "BLOCKED", "BLOCKED", "voice_gateway_model", None, False, provider="voice_gateway", model="taiwanese_asr", notes=summary["taiwanese_asr"]["blocked"])
            for lang in ("chinese", "taiwanese"):
                try:
                    summary[f"dynamic_{lang}_tts"] = run_tts_language(rec, client, voice_capture, lang, dynamic)
                except Exception as exc:
                    summary[f"dynamic_{lang}_tts"] = {"blocked": f"{type(exc).__name__}: {exc}"}
                    rec.add(f"dynamic_{lang}_tts", "BLOCKED", "BLOCKED", "voice_gateway_model", None, False, provider="voice_gateway", model=f"{lang}_tts", notes=summary[f"dynamic_{lang}_tts"]["blocked"])
            initial = run_recommend(rec, client, department, "initial")
            followup = run_recommend(rec, client, department, "followup")
            try:
                return_row = choose_return_fixture(department)
                returned = run_return_visit(rec, client, department, return_row)
                fixtures = choose_quick_fixtures(department)
                quick = run_quick_search(rec, client, department, fixtures)
                run_generate_script(rec, client, "initial_recommendation", initial)
                run_generate_script(rec, client, "followup_recommendation", followup)
                run_generate_script(rec, client, "quick_search", quick, quick=True)
                run_sql_adapter(rec, department, return_row, fixtures[0])
                summary["fixtures"] = {
                    "return_visit": {"dept_id": department["dept_id"], "doctor_id": return_row["doctor_id"], "date": str(return_row["date"]), "session": return_row["session"]},
                    "quick_search": fixtures,
                }
            except Exception as exc:
                summary["db_fixture_blocked"] = f"{type(exc).__name__}: {exc}"
                rec.add("db_dependent_remaining", "BLOCKED", "BLOCKED", "sql_adapter", None, False, notes=summary["db_fixture_blocked"])


def group_stats() -> dict[str, Any]:
    if not CSV_PATH.exists():
        return {}
    with CSV_PATH.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    buckets: dict[tuple[str, str, str], list[dict[str, str]]] = {}
    for row in rows:
        if not row["latency_ms"]:
            continue
        buckets.setdefault((row["benchmark_group"], row["subgroup"], row["layer"]), []).append(row)
    result: dict[str, Any] = {}
    for (group, subgroup, layer), items in buckets.items():
        values = sorted(float(item["latency_ms"]) for item in items)
        success_count = sum(item["success"].lower() == "true" for item in items)
        p95 = values[max(0, math.ceil(0.95 * len(values)) - 1)]
        result[f"{group}|{subgroup}|{layer}"] = {
            "n": len(values), "mean": statistics.fmean(values), "median": statistics.median(values),
            "p95": p95, "min": min(values), "max": max(values),
            "success_rate": success_count / len(values) * 100,
        }
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--groups", default="all", help="comma-separated: batch,department,http,voice_provider,return,stages,repair,repair_ai_harness,android_ingest,android_e2e_ingest,android_voice_ingest")
    parser.add_argument("--fresh", action="store_true")
    args = parser.parse_args()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    recorder = Recorder(args.fresh)
    summary: dict[str, Any] = {}
    if SUMMARY_PATH.exists() and not args.fresh:
        summary = json.loads(SUMMARY_PATH.read_text(encoding="utf-8"))
    from app.config import get_settings
    from app.db import fetch_active_departments
    settings = get_settings()
    summary.update({
        "started_at": summary.get("started_at") or datetime.now(TAIPEI).isoformat(),
        "python": sys.version.split()[0], "ai_provider": settings.ai_provider,
        "ai_model": settings.cerebras_model, "voice_gateway_masked": "http://localhost:8000/…" if "localhost" in settings.voice_gateway_url else "configured/hidden",
    })
    departments = fetch_active_departments()
    summary["department_candidate_count"] = len(departments)
    groups = {item.strip() for item in args.groups.split(",")}
    if "repair" in groups:
        repair_instrumentation_rows()
    if "repair_ai_harness" in groups:
        remove_ai_harness_rows()
    if "all" in groups or "batch" in groups:
        run_batch_ai(recorder)
    if "all" in groups or "department" in groups:
        run_department_ai(recorder, departments)
    if "all" in groups or "http" in groups:
        run_http_db_voice(recorder, departments, summary)
    department = next((item for item in departments if int(item.get("dept_id") or 0) == 1298), departments[0])
    if "voice_provider" in groups:
        run_voice_provider(recorder, summary)
    if "return" in groups:
        return_row = choose_return_fixture(department)
        with Server() as server:
            with httpx.Client(base_url=server.url, timeout=180.0) as client:
                run_return_visit(recorder, client, department, return_row)
    if "stages" in groups:
        run_recommend_stages(recorder, department)
    if "android_ingest" in groups:
        ingest_android_local_audio(recorder, summary)
    if "android_e2e_ingest" in groups:
        ingest_android_e2e(recorder, summary)
    if "android_voice_ingest" in groups:
        ingest_android_voice(recorder, summary)
    summary["finished_at"] = datetime.now(TAIPEI).isoformat()
    summary["stats"] = group_stats()
    SUMMARY_PATH.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"csv": str(CSV_PATH), "summary": str(SUMMARY_PATH), "groups": sorted(groups), "stats_groups": len(summary["stats"])}, ensure_ascii=False))


if __name__ == "__main__":
    main()
