# Phase 2 - 自然多輪問診驗證

日期：2026-09-29  
工作目錄：`New_Android_Backend`  
使用者指定基準 commit：`6d5b65b0c70a541e137d4537333ddca19bd2beba`

## 修改檔案

| 檔案 | 目的 |
| --- | --- |
| `backend/app/services/conversation_service.py` | 獨立 AI clarification call；輸入完整對話、grounded evidence、semantic extractions、不確定性、已問 intent；驗證 AI 問題與回答來源，並由 Backend 控制完成、pending、hard cap 和 unresolved。 |
| `backend/app/routes/chat.py` | 自然文字且 AI 可用時，改走 extraction → urgency safety → conversation clarification；保存助理追問至 history。保留結構化 batch 與無 AI key 的 legacy 路徑。 |
| `backend/app/schemas.py` | 對 `ConversationState` 只增可選且有預設值的 `turn_count`、`clarification_status`、`uncertainty_reasons`、`next_information_needed`、`asked_clarification_intents`、`pending_clarification_intent`、`clarification_evidence`。 |
| `backend/app/services/rule_engine.py` | `evaluate_urgency(mark_next_question=None)` 僅計算既有急迫度，不執行 checklist 選題；預設行為與 batch/legacy 路徑不變。 |
| `backend/app/services/rag_triage_adapter.py` | 更新 extraction prompt 的責任說明：此 call 不產生追問，另有 clarification call；Backend 掌控 state。 |
| `backend/app/services/ai_reply_generator.py` | 更新既有「不二次改寫已選問題」的註解，涵蓋自然追問。 |
| `backend/tests/test_phase2_conversation.py` | 新增 10 個 API 驗收測試。 |
| `backend/tests/test_semantic_refinement_gate.py` | 將有 AI 的測試預期更新為 extraction 與 clarification 各一次 provider call。 |
| `docs/BASELINE_NEW_BACKEND.md`、`docs/PHASE1_AI_FIRST_VALIDATION.md` | 依本階段要求刪除已驗收施工紀錄。 |
| `docs/PHASE2_CONVERSATIONAL_TRIAGE_VALIDATION.md` | 本施工紀錄。 |

`README.md`、`CONTRIBUTING.md`、根目錄 `修改計畫.md` 保留。Android、SQL、DB schema 未修改。

## 新資料流與狀態

自然文字 `/chat` 且 AI key 可用時：使用者原文 → Phase 1 AI-first semantic extraction 與 `source_text` grounding → Backend 保存患者原文及 AI normalized concept → 既有 red-flag/urgency safety 計算（不選 checklist 下一題）→ 專責 AI clarification call 根據 history、已知 evidence、未釐清項目和已問 intent 提議一個問題或 `sufficient` → Backend 驗證並決定狀態。助理追問記入 `history_records`，供下一輪理解「像房子在轉」等上下文回答。

Backend 只接受非空、長度合理、有新 intent、未重複且不包含診斷斷言／科別／醫師／掛號建議的問題；已保存 duration 時拒絕再次詢問持續時間。AI 回答 `sufficient` 仍須有 grounded symptom、至少一項已驗證的症狀細節或已驗證澄清回答、無 unresolved low-confidence medical extraction、無 pending clarification。`answered_intent` 只有在本輪 `answer_source_text` 出現在使用者原文，且對應已接受的 extraction 時才可清除 pending。AI 回應中的 `stage`、`is_complete`、`confirmed` 等 workflow 欄位不解析。日期／時段偏好不屬於症狀完成門檻。

Provider timeout、quota、error、malformed JSON 或不合格問題時，使用不填造病情的泛化追問。第 8 個自然文字 turn 仍不足時進入 `clarification_status=unresolved`，不補未知欄位、不硬選科；之後不再自動呼叫 AI，除非使用者啟動 revision。現有 red-flag 陽性即時路徑保持原樣；症狀資料足夠但 `red_flags_checked=false` 時，`triage.is_final` 仍為 false，不冒稱安全篩檢完成。

`CHECKLIST_FIELD_ORDER`、`missing_checklist_fields()`、`_next_question_key()`、固定 question variants 仍保留給無 AI key、結構化 batch 與其他既有 legacy 呼叫；它們不再決定 AI 可用時自然文字 `/chat` 的追問或完成狀態。`TriageResult` 舊欄位未刪；`ConversationState` 僅加預設欄位。Android 的 `JSONObject` parser 只讀既有欄位，能忽略新欄位，因此 Android source/DTO 未改。

## 測試

`test_phase2_conversation.py` 覆蓋：頭暈改問語境相關問題；一次描述症狀、部位、期間、程度、起因及功能影響可直接足以往下走而不等掛號偏好；上下輪澄清回答與 history；已回答 duration 不重問；provider timeout／malformed JSON 保守退路；AI workflow 注入無效；空白或籠統症狀不能由 AI 宣布完成；低信心、未 grounded 或未通過 extraction 的回答不能解除 pending；8 輪上限轉 unresolved 且不重試 AI、不選科。原 Phase 1 的 grounding、red-flag、provider failure 與其他 Backend regression tests 全數保留。

在 `New_Android_Backend/backend` 執行：

```powershell
$env:CEREBRAS_API_KEY=''
$env:PYTHONPATH=(Get-Location).Path
$env:Path=(Resolve-Path '.\.venv\Scripts').Path + ';' + $env:Path
pytest -q
```

結果：`406 passed, 8 warnings, 277 subtests passed in 7.91s`。警告是既有 FastAPI/Starlette deprecation 與 pytest cache 寫入權限；無測試失敗。AI provider 使用 mock，未呼叫真實 Cerebras。

因 `ConversationState` 回應增加向後相容欄位，仍在 `New_Android_Backend/android` 執行 Android 驗證：

```powershell
$env:JAVA_HOME='C:\Program Files\Android\Android Studio\jbr'
$env:Path="$env:JAVA_HOME\bin;$env:Path"
$env:ANDROID_HOME='C:\Users\10650\AppData\Local\Android\Sdk'
$env:ANDROID_SDK_ROOT=$env:ANDROID_HOME
.\gradlew.bat testDebugUnitTest --no-daemon
.\gradlew.bat assembleDebug --no-daemon
```

兩者皆 `BUILD SUCCESSFUL`。Unit test XML：12 suites、121 tests、0 failures、0 errors、0 skipped；`android/app/build/outputs/apk/debug/app-debug.apk` 存在。初次未設定 SDK 的 Gradle 呼叫因找不到 SDK 失敗；sandbox 內重試亦遇 wrapper 網路／離線 plugin cache 限制，使用已安裝的 JDK 21、SDK 並允許工作區外快取存取後完成。未修改 `local.properties`。

## 留待後續 Phase

- Phase 3/4：沒有建立北榮官方科別 KB、正式候選科別收斂或 Top-K ranking；沿用既有 `detect_department_result()`，`unresolved` 僅提供手動選科／院方協助的保守出口。
- Phase 5：沒有 TTAS 改造；既有 red-flag/urgency safety 路徑保留。
- Phase 6：沒有修改醫師專長 scoring。
- Phase 7/8：沒有修改 Android UI、DTO、引導或其他後續流程。
- 沒有新增 DB/SQL 資料或自創醫療科別 mapping。AI 提議的醫療澄清問題仍需後續人工／臨床審核；本階段測試驗證流程與安全門檻，不宣稱臨床正確性。

目前工作副本沒有可用 `.git` metadata，故無法在此環境獨立核對指定基準 commit 或列出 Git diff；沒有 commit 或 push。
