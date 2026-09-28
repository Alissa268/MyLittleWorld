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

Backend 只接受非空、長度合理、有新 intent、未重複且不包含診斷斷言／科別／醫師／掛號建議的問題；已保存 duration 時拒絕再次詢問持續時間。AI 回答 `sufficient` 仍須有 grounded symptom、至少一項已驗證的症狀細節或已驗證澄清回答、無 unresolved low-confidence medical extraction、無 pending clarification。Phase 2.3 起，`answered_intent` 清除 pending 改依本輪逐字 grounded 的 `answer_source_text`、`answer_status=answered` 與達標信心驗證，不再要求能映射到已接受的 structured extraction；詳見下節。AI 回應中的 `stage`、`is_complete`、`confirmed` 等 workflow 欄位不解析。日期／時段偏好不屬於症狀完成門檻。

Provider timeout、quota、error、malformed JSON 或不合格問題時，使用不填造病情的泛化追問。第 8 個自然文字 turn 仍不足時進入 `clarification_status=unresolved`，不補未知欄位、不硬選科；之後不再自動呼叫 AI，除非使用者啟動 revision。現有 red-flag 陽性即時路徑保持原樣。Phase 2.1 起，症狀資訊足夠但 safety screen 未完成時仍須先問既有受控安全問題，不得直接完成或選科。

`CHECKLIST_FIELD_ORDER`、`missing_checklist_fields()`、`_next_question_key()`、固定 question variants 仍保留給無 AI key、結構化 batch 與其他既有 legacy 呼叫；它們不再決定 AI 可用時自然文字 `/chat` 的追問或完成狀態。`TriageResult` 舊欄位未刪；`ConversationState` 僅加預設欄位。Android 的 `JSONObject` parser 只讀既有欄位，能忽略新欄位，因此 Android source/DTO 未改。

## 測試

`test_phase2_conversation.py` 覆蓋：頭暈改問語境相關問題；一次描述症狀、部位、期間、程度、起因及功能影響可通過症狀澄清而不等掛號偏好；上下輪澄清回答與 history；已回答 duration 不重問；provider timeout／malformed JSON 保守退路；AI workflow 注入無效；空白或籠統症狀不能由 AI 宣布完成；低信心、未 grounded 或未通過 extraction 的回答不能解除 pending；8 輪上限轉 unresolved 且不重試 AI、不選科。Phase 2.1 補強 safety completion gate，見下節。原 Phase 1 的 grounding、red-flag、provider failure 與其他 Backend regression tests 全數保留。

在 `New_Android_Backend/backend` 執行：

```powershell
$env:CEREBRAS_API_KEY=''
$env:PYTHONPATH=(Get-Location).Path
$env:Path=(Resolve-Path '.\.venv\Scripts').Path + ';' + $env:Path
pytest -q
```

原 Phase 2 驗收結果：`406 passed, 8 warnings, 277 subtests passed in 7.91s`。Phase 2.1 完整重跑結果見下節。警告是既有 FastAPI/Starlette deprecation 與 pytest cache 寫入權限。AI provider 使用 mock，未呼叫真實 Cerebras。

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

## Phase 2.1 - 獨立 safety completion gate

使用者指定本次基準 commit：`c11cf3a9acdd8003612dc0818becee369989ee35`。本次只修 Phase 2 的 safety blocker，未開始 Phase 3 或 TTAS Phase 5。

| 修改檔案 | 目的 |
| --- | --- |
| `backend/app/services/conversation_service.py` | 共用 `safety_screen_resolved()`；症狀 sufficient 但 safety 未完成時以現有 `red_flags` 受控問題進入 `safety_check`，設定 `last_question_key` 與既有問答狀態，不新增 AI clarification intent；明確 negative 後才真正 complete。 |
| `backend/app/routes/chat.py` | `safety_check` 回答仍由原有 deterministic parser 處理，這一輪不需新的 AI clarification 提議；department detection 與 stage transition 都加 safety gate。positive red flag urgent path 保留。 |
| `backend/app/routes/recommend.py` | 在任何確認與推薦動作前拒絕 safety 未解析 case，包括 client 自帶 `is_complete=true` 的資料。 |
| `backend/tests/test_phase2_conversation.py` | 更新 rich-description 預期，新增 negative 後完成、`confirmed=true` 不可跳過、偽造 `/recommend` 完成狀態被拒、positive red flag 不被攔截。 |
| `backend/tests/test_db_adapter_mapping.py` | 舊「已完成 case」fixture 補上已完成 safety screen，保留 DB slot 映射回歸測試原意。 |
| `docs/PHASE2_CONVERSATIONAL_TRIAGE_VALIDATION.md` | 更新施工紀錄與測試結果。 |

資料流：自然症狀澄清足夠 → Backend 檢查 `red_flags_checked` 或已命中 `red_flags` → 若兩者皆否，`is_complete=false`、`need_more_info=true`、`stage=collecting`，使用既有 `QUESTION_TEXTS[RED_FLAG_QUESTION_KEY]` 並透過 `mark_questions_asked()` 設定 `last_question_key=red_flags`；此問題不進 `asked_clarification_intents`。下一輪否認由 `_apply_free_text_safety()` 與既有 red-flag parser 判定；negative 解析完成後，若症狀證據仍足夠，才令 `is_complete=true`、`need_more_info=false` 並進行既有科別偵測。positive red flag 仍沿 urgent 路徑，無須 negative 確認。`/recommend` 獨立要求相同 safety-resolved 條件。沒有恢復固定七題，也沒有新增醫療 keyword 規則或修改 API schema。

測試 A–E 均在 API 層覆蓋；另保留 Phase 1/2 與 Backend 全部回歸。在 `New_Android_Backend/backend` 使用上節相同環境設定執行 `pytest -q`，最終結果：`410 passed, 8 warnings, 277 subtests passed in 9.48s`。未修改 Android source、DTO 或 API schema，因此未重跑 Android build；Phase 2 的 Android unit/build 結果仍如上。此副本仍無可用 `.git` metadata，無法獨立驗證指定 commit；沒有 commit 或 push。

## Phase 2.2 - safety 回答隔離與第八輪收尾

使用者指定本次基準 commit：`e027ac162c674f640f1b8cc8424c6abf738f2014`。只修 Phase 2 的兩個 edge cases，未開始 Phase 3。

| 修改檔案 | 目的 |
| --- | --- |
| `backend/app/routes/chat.py` | 在處理本輪輸入前鎖定 `safety_check` 狀態；該輪保留 user history，但只用既有 deterministic safety parser，不呼叫一般 semantic extraction 或 AI clarification，也不擷取無關科別偏好。一般自然症狀輪仍 AI-first。 |
| `backend/app/services/conversation_service.py` | 將 sufficient 分支移到 hard cap 判斷之前。第 8 輪已足夠但 safety 未完成時先進 `safety_check`；safety 回答不增加症狀澄清 `turn_count`。只有症狀仍不 sufficient 才由 hard cap 進 `unresolved`。 |
| `backend/tests/test_phase2_conversation.py` | Test A 用會污染伴隨症狀的 mock extraction 驗證 safety negative 回答不呼叫兩個 AI provider、不添病情且保存 history；Test B 驗證第 8 輪 sufficient 先問既有安全問題，下一輪 negative 後才完成。 |
| `docs/PHASE2_CONVERSATIONAL_TRIAGE_VALIDATION.md` | 記錄 Phase 2.2 修正與測試結果。 |

在 `New_Android_Backend/backend` 使用上節相同環境設定執行 `pytest -q`，最終結果：`411 passed, 8 warnings, 277 subtests passed in 6.32s`。Phase 1 AI-first、原文 evidence／normalized concept 分離、自然多輪澄清、pending intent、低信心與 provider failure、真正未釐清時的 hard cap、Phase 2.1 safety gate、`/recommend` 防線及 urgent positive red flag 回歸均通過。未修改 API schema 或 Android source，未重跑 Android；仍無可用 `.git` metadata 可獨立核對指定 commit，沒有 commit 或 push。

## Phase 2.3 - Free-form Clarification Evidence

使用者指定基準 commit：`3218026d4e78fdb96b2a01153ccc4cb8301581d4`。真實 Cerebras smoke test 顯示：腳傷的承重能力回答可能不對應任何既有 `SemanticExtraction` 欄位；舊條件要求本輪必須有已接受的 extraction，導致 pending intent 無法解除，連續回泛化提示。本次只修自然澄清的回答證據與同 intent 追問，未開始 Phase 3。

| 修改檔案 | 目的 |
| --- | --- |
| `backend/app/services/conversation_service.py` | planner JSON 加入 `answer_status`、`answer_confidence`；驗證 intent、逐字 source、有限 0–1 信心值。`answered` 且信心達既有 `ACCEPT_THRESHOLD` 時保存 `clarification_evidence[intent]` 並清除 pending，不依賴 PatientInput 新欄位或 accepted semantic extraction。`partial`／`unclear` 保留 pending，允許合法、非重複、同 intent 的精確追問。 |
| `backend/tests/test_phase2_conversation.py` | 加入無 structured extraction 仍可清除 pending、虛構 source、partial／unclear、低信心／非有限值、錯 intent、重複與不安全問題的 API 測試；保留「像房子在轉」同時具 semantic extraction 的既有路徑。 |
| `docs/PHASE2_CONVERSATIONAL_TRIAGE_VALIDATION.md` | 更新資料流、smoke 問題與驗證結果。 |

新資料流：本輪 user 原文 → 原有 AI-first structured extraction（若有）→ AI clarification planner 提議 answer metadata 與下一步 → Backend 僅在 `answered_intent == pending_clarification_intent`、`answer_source_text` 非空且逐字存在本輪 user 原文、`answer_status=answered`、`answer_confidence` 為有限 0–1 且 `>= ACCEPT_THRESHOLD` 時保存 free-form evidence 並清除 pending。`partial`／`unclear` 不保存完成證據、不清除 pending；若同 intent 問題通過既有長度、診斷／科別／醫師／掛號字樣、已問時長及 exact-duplicate 檢查，則可續問。AI 仍不能控制 workflow、red flag 或直接選科；safety gate、hard cap 與後續科別邏輯未變。未新增醫療 field、keyword 或 synonym dictionary。

驗證：在 `New_Android_Backend/backend` 執行 `pytest -q`（測試程序內暫時清空 `CEREBRAS_API_KEY`，不改 `.env`），結果 `416 passed, 8 warnings, 285 subtests passed in 13.74s`。警告仍是既有 FastAPI/Starlette deprecation 與 pytest cache 權限。Android DTO／UI／network contract 未修改，故未重跑 Android。

另外以本機 `.env` 真實 Cerebras `gpt-oss-120b`、in-memory `/chat` 再驗證腳傷多輪（僅在測試程序內替換科別偵測，避免 DB 呼叫）。一次第二輪回傳合法 JSON、`partial`、grounded source、信心 `0.92`，Backend 保留 pending 並使用更具體的同 intent 追問，而非泛化提示。另一次三輪驗證中，第二輪 provider 未標示 answer metadata，因此仍回泛化提示；第三輪明確說明能站立承重後，provider 回 `answered`、grounded source、信心 `0.96`，Backend 保存 `clarification_evidence[functional_impact]`、清除 pending，進入受控 `safety_check`。這顯示 Backend 已能接受 free-form evidence，但 provider 對簡短回答是否標示為 answered 仍有變異；未為此加醫療硬編碼。測試未顯示、記錄或回報 API key。

Phase 3/4 的官方 KB 與科別收斂、Phase 5 TTAS、Phase 6 doctor scoring、Android、DB schema 與 SQL data 均未修改。此副本仍無法透過 Git metadata 獨立核對指定 commit；沒有 commit 或 push。
