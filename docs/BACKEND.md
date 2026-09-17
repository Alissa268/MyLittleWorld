# Backend 指南

正式 Backend 位於 `backend/`，使用 FastAPI、Pydantic、SQL Server/pyodbc 與可選 AI provider。project-smart 的部分關鍵字概念已移植到正式 adapter，runtime 不依賴外部 reference 資料夾。

## 目錄與責任

- `backend/app/main.py`：建立 FastAPI、CORS、掛載 routers、startup AI initialization 與 `/health`。
- `backend/app/config.py`：以 `pydantic-settings` 讀取 `backend/.env`／環境變數。
- `backend/app/schemas.py`：`VisitType`、`TriageCase`、conversation、recommendation、followup、script、voice contracts。
- `backend/app/db.py`：SQL connection、Department/Schedule query 與正式資料 record mapping；DB 失敗或查無資料時不回傳 mock 班表。
- `backend/app/routes/`：HTTP boundary、validation 與 service orchestration。
- `backend/app/services/`：問診、AI、推薦、voice client 與 script 邏輯。
- `backend/app/services/case_store.py`：prototype in-memory case/recommendation store。
- `backend/tests/`：Backend unit／route／adapter regression tests。

## Routes

| Method | Route | 用途 |
|---|---|---|
| GET | `/health` | Backend process health |
| POST | `/chat` | 單題或 Batch triage、確認／修改、409 visit type 保護 |
| POST | `/recommend` | 完整且已確認 case 的一般推薦 |
| GET | `/schedules/search` | `quick_search` 正式科別／日期／時段班表查詢 |
| POST | `/followup/recommend` | 相容用 `return_visit` 回診推薦 |
| GET | `/reference/departments` | 正式 DB 科別主資料 |
| GET | `/reference/doctors` | 指定正式科別的醫師主資料 |
| POST | `/generate_script` | 將已儲存 recommendation 轉為掛號步驟 |
| POST | `/voice/asr` | 音檔轉文字 |
| POST | `/voice/chat` | ASR → 正式 chat handler → TTS |
| POST | `/voice/tts` | 文字轉語音 |
| GET | `/voice/health` | Voice Gateway 設定／連線狀態 |

科別與醫師 reference API 只讀正式 DB；查詢失敗時回錯誤，不使用 JSON/mock fallback。

## 主要 services

### `rule_engine.py`

問診流程與安全性的 source of truth。它解析訊息、維護 checklist／question attempts／field status、判斷缺漏欄位、red flags、urgency、completion 與下一題。AI 不得繞過它。

### `batch_question_service.py`

從 `missing_checklist_fields()` 取得目前缺漏項目，依 deterministic 順序組成最多六題的 `question_batch`，並記錄已問欄位。它不是第二套 state machine。

### `batch_extraction_service.py`

以 question key 消費 Batch answers。每欄先 deterministic parse；仍模糊的非 red-flag 欄位，才最多呼叫一次 Cerebras。輸出 schema 會再驗證，AI 不能設定 stage、confirmation 或 `red_flags_checked`。

### `ai_service.py`

正式 Runtime 統一使用 Cerebras `gpt-oss-120b` 與 JSON mode。沒有 Key、SDK、quota 或 timeout 時，上層 service 必須安全 fallback。Gemini client 僅保留作 legacy adapter，production route 不會呼叫；`ai_reply_generator` 使用受控文案，不呼叫 LLM。

### `appointment_service.py`

負責科別結果、canonical visit type mapping、SQL slot 取得、strict visit type filter、可行性篩選、專長／時間排序與 recommendation columns。現行 mapping：

- `initial → 初診`
- `followup → 複診`
- `return_visit → 複診`

### `followup_service.py`

只服務 `return_visit` contract，依手動科別、原醫師與 availability 查「複診」班表。原醫師可用時優先；日期／時段不符時回空結果，不可退回其他 open rows。

### 其他關鍵 modules

- `schedule_filter.py`：日期、星期、時段、停診、請假與可行 rows。
- `specialty_scoring.py`：deterministic-first 醫師專長分數與可選 AI scoring。
- `project_smart_department_adapter.py`：已移植的關鍵字概念，不依賴外部 reference folder。
- `rag_triage_adapter.py`：選擇性 AI 科別／語意補強。
- `semantic_normalizer.py`、`semantic_refinement_gate.py`：deterministic normalization 與是否值得呼叫 AI 的 gate。
- `voice_client.py`：外部 Voice Gateway HTTP client。
- `script_service.py`：從 recommendation 建立 navigation steps。

## State truth

「流程真相」是 `rule_engine.py`；「目前 process 內某個 case 的暫存真相」是 `case_store.py`。後者不是持久資料庫：Backend 重啟會遺失資料，多 worker 也不共享。

`/recommend` 必須使用已完成且已確認的 case；`/generate_script` 必須能在 case store 找到 recommendation。不要由 Android 或 AI 偽造這些 state。

## SQL 安全與推薦

`db.py` 使用參數化 SQL 查詢 `DepartmentCategory`、`Department`、`Doctor`、`Schedule`，並以 `AND s.visit_type = ?` 做精確篩選。正式開發不得自行修改 DB；若只做驗證，限定 `SELECT`。任何 log、測試 fixture 或文件都不得包含 DB credential 或患者資料。

設定與啟動見 [SETUP_AND_RUN](SETUP_AND_RUN.md)，測試見 [TESTING](TESTING.md)，AI 邊界見 [AI_TRIAGE](AI_TRIAGE.md)。
