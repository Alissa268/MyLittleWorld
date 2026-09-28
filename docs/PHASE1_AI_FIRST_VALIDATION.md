# Phase 1 - AI-first Semantic Extraction 驗證

日期：2026-09-28  
工作目錄：`New_Android_Backend`  
使用者指定基準 commit：`76076d6327b20d033d4b7cfaf5fc5f63a6fedf30`

## 範圍與修改檔案

| 檔案 | 目的 |
| --- | --- |
| `backend/app/routes/chat.py` | `/chat` 在 AI 可用時讓本輪 `message/messages` 先走 semantic extraction；keyed batch answer 顯式開啟 AI-first 模式。將自由文字科別偏好擷取移到本輪 AI 抽取之後。Backend 繼續決定確認、完成、題目與推薦狀態。 |
| `backend/app/routes/recommend.py` | 無既有 case 時的 `userQuery` 也是自由文字入口；AI 可用時先用相同抽取與驗證流程，再讓 Backend 判斷問診是否完整。推薦演算法未改。 |
| `backend/app/services/rule_engine.py` | 新增自由文字的 safety-only 入口，只執行既有 red-flag 檢查，不預先填入症狀、部位、期間、程度、時段；接受的 AI evidence 仍經原有 field validation 與 confidence gate，並可記錄 onset/伴隨症狀。低信心 AI 結果不因固定題重試上限而被當成可靠完成值。修正症狀 revision 時舊依賴欄位的清理。 |
| `backend/app/services/rag_triage_adapter.py` | 只以本輪使用者原文驗證 `source_text`；檢查 field 白名單、status、normalized value 與有限且在 0-1 的 confidence。provider/JSON/無效 triage 回應安全忽略。 |
| `backend/app/services/field_acceptance.py` | 對 AI onset 與伴隨症狀加入長度、型別及 normalized value 必須逐字受 `source_text` 支持的驗證；避免切換 AI 路徑後遺失原本會收集的欄位。 |
| `backend/app/services/batch_extraction_service.py` | `/chat` 的 AI-first batch 模式將自然語言 keyed answer 送 AI，明確單值 UI 選擇仍可走既有 deterministic 路徑。已填欄位收到新自由文字仍會呼叫 AI，但維持既有避免非 revision 覆寫的保護。直接呼叫 service 時保留既有預設模式。 |
| `backend/tests/test_phase1_ai_first.py` | 新增 API 層 Phase 1 驗收測試。 |
| `backend/tests/test_batch_question_flow.py` | 舊 batch/regression 情境明確使用無 AI key 設定，以驗證原有 deterministic compatibility。 |
| `backend/tests/test_semantic_refinement_gate.py` | AI 呼叫情境明確在 `/chat` 設定可用 AI key；無 key 情境仍驗證原有 fallback。 |
| `docs/PHASE1_AI_FIRST_VALIDATION.md` | 本驗證紀錄。 |

`backend/app/services/ai_service.py` 的 Cerebras JSON mode 與 timeout 既有實作沿用。`backend/app/schemas.py` 與 Android DTO 未改；`schemas.py` 對 `Android_Backend` 的 SHA-256 相同。

## 資料流

Phase 0：自由文字先進 `apply_user_message()` 的 deterministic 語意解析；`semantic_refinement_gate` 在規則認為欄位可靠時跳過 AI；只有不確定時才呼叫 `refine_case_with_ai()`。

Phase 1：`/chat` 收到自由文字且 AI key 可用時，先保留本輪原文並執行既有獨立 red-flag safety screen，接著每輪呼叫 AI semantic extraction。只有 field 在白名單、`source_text` 為本輪使用者原文連續子字串、normalized value 通過 field validation、confidence 達既有 `ACCEPT_THRESHOLD=0.55` 的 extraction 才能套用。無效、低信心、provider timeout/quota/error 或 malformed JSON 不填入新語意值；Backend 仍依原有固定題、urgency、確認與推薦流程決定 state。AI-normalized 症狀文字也不能在後續 urgency evaluation 中間接建立 red flag；red flag 由原文 safety screen 負責。

`/chat` 的 keyed batch answer 若答案是自然語言，AI 可用時也先送 semantic extraction；如 `preferred_sessions=上午` 的明確 UI 值可 deterministic-only。red-flag screen 始終由 Backend deterministic 邏輯處理，AI 的 `red_flags`、`stage`、`is_complete`、`confirmed`、`awaiting_confirmation`、`recommendation_generated`、`script_generated`、`red_flags_checked` 等輸出不生效。AI 的 `triage`/`reply` 也不控制 Backend 題目或狀態。

沒有 AI key 時，沿用既有 deterministic 相容路徑；AI key 存在但 provider 失敗時保留安全檢查與已接受資料，這一輪不以廣泛的規則解析補造新值。未進行真實 Cerebras 網路呼叫，AI 路徑使用 mock provider 驗證。

## 新增測試

`test_phase1_ai_first.py`：清楚可規則解析的自由文字仍呼叫 AI；合法 grounded duration、severity、body_part、onset 與伴隨症狀可套用，憑空補入的伴隨症狀被拒；來源不在本輪原文、非白名單欄位及非法 normalized value 被拒；低信心不填值或因題目重試上限完成；quota、timeout、malformed JSON、無效 `triage` 不 crash 或補值；AI 無法直接或藉 normalized symptom 間接改 red-flag/workflow state；症狀 revision 清除舊依賴值；keyed 自由文字呼叫 AI、明確 UI choice 仍可 deterministic、AI 失敗不填值、已有值不被暗中覆寫；`/recommend userQuery` 也使用 AI-first。

## 實際驗證

在 `New_Android_Backend/backend` 執行：

```powershell
$env:CEREBRAS_API_KEY=''
$env:PYTHONPATH=(Get-Location).Path
$env:Path=(Resolve-Path '.\.venv\Scripts').Path + ';' + $env:Path
pytest -q
```

最後執行 `pytest -q`：`395 passed, 8 warnings, 275 subtests passed in 7.44s`。另執行 `pytest -q -p no:cacheprovider`：`395 passed, 7 warnings, 275 subtests passed in 6.34s`。警告為 FastAPI/Starlette deprecation 與預設 cache plugin 寫入權限警告，無測試失敗。未設定 `PYTHONPATH` 的裸 `pytest -q` 在 test collection 時因 `ModuleNotFoundError: app` 失敗，設定後通過；未修改 production import contract 來掩蓋環境差異。

API contract：以 `TestClient(app).get('/openapi.json')` 檢查 `/chat` POST 仍回 `TriageResult`，`ChatRequest` 與 `TriageResult` 欄位集合未變。Android source/DTO 未改，因此未執行 Android unit tests、`assembleDebug` 或實機測試。

## Phase 1.1 - 症狀原文與 AI 解讀分離

使用者指定本次基準 HEAD：`678b38eddff91393d9c0832b455ea677454062ca`。本次僅修正 AI 症狀套用及相關測試，未開始 Phase 2。

| 修改檔案 | 目的 |
| --- | --- |
| `backend/app/services/rule_engine.py` | AI 的 `symptom` 寫入 `patient_input.symptom` 時使用已通過 grounding 的 `source_text`；`normalized_value` 不再直接改寫患者主訴。其他欄位與 deterministic 路徑未改。 |
| `backend/tests/test_phase1_ai_first.py` | 補強「我頭暈」卻被 AI 解讀為「胸痛」的回歸測試，並新增「砰砰跳很快」保留患者原文、同時保存 AI「心悸」概念的測試。 |
| `backend/tests/test_semantic_ai_validation.py` | 將既有 batch AI 症狀測試的預期值改為 grounded 原文，並確認 normalized 概念仍在 `SemanticExtraction`。 |
| `.gitignore` | 移除 Phase 1 額外重複規則，恢復與 Phase 0 基準資料夾 `Android_Backend/.gitignore` 完全相同的內容。 |
| `docs/PHASE1_AI_FIRST_VALIDATION.md` | 記錄 Phase 1.1 修正和驗證。 |

修正前：通過 `source_text` grounding 與欄位驗證的 AI symptom extraction，仍會將 `normalized_value` 直接存入 `patient_input.symptom`；「頭暈」可因此被偽寫為「胸痛」。修正後：自由文字仍走 AI-first extraction → 既有 grounding／白名單／schema／confidence validation → apply；對 AI symptom，患者主訴欄位保存 `source_text`，AI 概念保存在 `case.semantic_extractions[].normalized_value`。因此「心臟有時候突然砰砰跳很快」仍可攜帶「心悸」的語意解讀，但不會冒充患者逐字陳述。Backend 仍掌控 workflow 與 red-flag 狀態；未新增症狀字典、修改推薦或 Android contract。

新增／補強測試：`test_ai_normalized_symptom_cannot_indirectly_complete_red_flag_screen` 現在也斷言 `patient_input.symptom == "頭暈"` 且絕非「胸痛」；新增 `test_ai_symptom_concept_keeps_grounded_patient_wording`；既有 `test_ai_symptom_is_not_limited_to_symptom_terms` 驗證 batch 路徑保留原文和 AI 概念。既有 workflow/red-flag、provider failure、malformed JSON、low confidence 測試均隨完整 Backend suite 通過。

在 `New_Android_Backend/backend` 執行：

```powershell
$env:CEREBRAS_API_KEY=''
$env:PYTHONPATH=(Get-Location).Path
$env:Path=(Resolve-Path '.\.venv\Scripts').Path + ';' + $env:Path
pytest -q tests/test_phase1_ai_first.py tests/test_semantic_ai_validation.py
pytest -q
```

聚焦測試：`36 passed, 8 warnings, 29 subtests passed in 1.10s`。完整 Backend：`396 passed, 8 warnings, 275 subtests passed in 6.14s`。警告仍為 FastAPI/Starlette deprecation 與 pytest cache 寫入權限，無測試失敗。`.gitignore` 與 `Android_Backend/.gitignore` 的 SHA-256 均為 `F4FE472F8277A3BCCCAD83385F62D3EB6FA002AF37D1C2065C454B6C534DDA77`。目前工作副本沒有可用 `.git` metadata，故無法以 `git show` 獨立核對指定 commit；此處依計畫所列 Phase 0 基準資料夾逐位元組比對。未修改 Android 或 API contract，未執行 Android build/測試。

## 留待後續 Phase

- Phase 2：固定 7 題、重試與下一題策略仍在；未改為真正多輪自然釐清。
- Phase 3/4：未建立官方科別知識庫、候選科別收斂引擎；目前科別推薦邏輯保持原樣。
- Phase 5：現有 red-flag/急迫度規則未改為 TTAS-based。
- Phase 6：醫師專長 scoring 未改。
- Phase 7：Android UI 與 DTO 未改。
- AI normalized symptom 的臨床語意正確性無法只靠原文子字串檢查證明；Phase 1 的 gate 驗證來源、形狀與信心，不宣稱臨床正確性。
- 目前檔案副本未見 `.git`，`git status` 無法取得本地 diff/驗證所述基準 commit；已以逐檔檢查和 SHA-256 核對未改的 schema。沒有 commit 或 push。
