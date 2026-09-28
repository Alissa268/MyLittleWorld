import os
import traceback
import uuid
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse

import config
from database import get_departments_with_category
from web.flow import step
from followup import FOLLOWUP_QUESTIONS, collect_followup, recommend_followup
from models import ChatRequest, ChatResponse, FollowupChatRequest
from triage import BASIC_INFO_QUESTIONS

BASE_DIR = Path(__file__).parent
INDEX_HTML = BASE_DIR / "index.html"

app = FastAPI(title="智慧分診導引系統")

# 除錯
DEBUG = os.getenv("DEBUG", "0").strip().lower() in ("1", "true", "yes")
print(f"  除錯面板：{'開啟' if DEBUG else '關閉'}")
#


@app.get("/")
def index():
    return FileResponse(INDEX_HTML)


@app.get("/api/start")
def start():
    return {"reply": BASIC_INFO_QUESTIONS}


@app.post("/api/chat", response_model=ChatResponse)
def chat(request: ChatRequest) -> ChatResponse:
    config.reset_trace()
    response = step(request)
    # 除錯
    response.debug = config.get_trace() if DEBUG else None
    #
    return response


@app.get("/api/departments")
def departments():
    depts = get_departments_with_category()
    grouped: dict[str, list[str]] = {}
    for d in depts:
        grouped.setdefault(d["parentDept"], []).append(d["childDept"])
    return {"grouped": grouped, "count": len(depts)}


@app.get("/api/followup/questions")
def followup_questions():
    return {"reply": FOLLOWUP_QUESTIONS}


@app.post("/api/followup")
def followup(request: FollowupChatRequest):
    config.reset_trace()
    if not request.case_id:
        request.case_id = uuid.uuid4().hex[:8].upper()
    result = recommend_followup(collect_followup(request))
    payload = {"recommendation": result}
    # 除錯
    payload["debug"] = config.get_trace() if DEBUG else None
    #
    return payload


@app.exception_handler(Exception)
async def on_unhandled_error(request, exc: Exception):
    traceback.print_exc()
    return JSONResponse(
        status_code=500,
        content={
            "error": "系統忙碌或連線異常，請稍候再送出一次（您填的資料還在）。",
            "detail": f"{type(exc).__name__}: {exc}",
        },
    )
