# 1. 目前系統重點更新摘要

> 盤點基準：2026-09-12 最新工作樹（`HEAD eb47dd1`）。正式 runtime 為 `android/` 與 `backend/`；本文件只採用目前程式碼、本次本機測試／build，以及已確認的語音 runtime smoke 事實。

- **台語 ASR／TTS runtime 已恢復**：台語 ASR 已完成 Gateway 與 Backend smoke，單次 Gateway processing time 為 **1632 ms**；台語 TTS smoke 成功，Backend 可接收並回傳可播放的合法 **M4A**。1632 ms 是單次 Gateway 樣本，不是平均值或完整 App 端到端時間。
- **`appointmentType` 傳遞已修正**：`ConfirmNeedScreen.kt` 會依 `VisitPlan` 明確傳入 Accessibility；初診映射 `INITIAL`，複診、快速查詢與舊回診 route 均映射 `RETURN_VISIT`，不再一律落到初診導引。
- **快速查詢流程已獨立**：使用者可直接依科別、日期、時段查詢正式班表；Backend 固定篩選具複診依據的資料，且不建立問診 case、不呼叫 AI 推薦流程。選定班表後會在產生導引 script 前重新向 DB 核對。
- **AI 問診更重視可控性**：規則引擎先處理必要欄位、紅旗與確認狀態；僅在語意模糊且已設定 provider 時使用 AI 補強，AI 輸出仍須通過 schema、來源文字與合法科別／欄位驗證。
- **Accessibility 掛號導引已串接就診類型**：可依科別、診別、醫師、日期、時段及初／複診類型，在院方 App 內顯示逐步紅框與提示；不代填個資，也不宣稱已完成第三方院方 App 的正式全自動掛號驗證。
- **取消掛號導引已強化**：具備獨立 session、目標 App 離開逾時中止、可取消項目辨識、捲動搜尋，以及必須點到「確認取消掛號」才進入完成狀態等保護。
- **最新測試與 build**：本次完整 Backend suite 為 **280 passed，另有 134 subtests passed**；Backend 語音相關口徑為 **23 passed**；Android unit tests 為 **117 passed**，0 failure／error／skipped；強制重跑 `assembleDebug` 為 **BUILD SUCCESSFUL**。題目所附的 `198 passed` 與 `112 passed` 是較早 checkpoint，已由本次較完整結果更新。

---

# 2. 前端主要檔案與用途（精簡）

```text
ANDROID APP（Front-End）
├─ MainActivity.kt                         # App 入口、Compose 啟動與舊 TTS 暫存清理。
├─ screen/HomeScreen.kt                    # 首頁入口，導向問診、快速查詢、歷史、說明及取消掛號。
├─ screen/VisitTypeSelectionScreen.kt      # 選擇初診、複診或快速查詢流程。
├─ screen/ChatScreen.kt                    # AI 問診、文字／語音輸入、Batch 問題與確認介面。
├─ screen/QuickSearchScreen.kt             # 科別、日期、時段的快速班表查詢與選擇。
├─ screen/DoctorSelectionScreen.kt         # 顯示專長優先／時間優先推薦與班表篩選。
├─ screen/ConfirmNeedScreen.kt             # 確認掛號資訊、傳遞 appointmentType 並開啟院方 App。
├─ screen/HistoryScreen.kt                 # 顯示本機問診與推薦歷史紀錄。
├─ screen/GettingStartedScreen.kt          # 使用說明及本地國／台語導覽內容。
├─ viewmodel/ChatViewModel.kt              # 管理問診 case、Batch、確認、ASR 草稿與 TTS 狀態。
├─ viewmodel/QuickSearchViewModel.kt       # 管理快速查詢表單、正式班表結果與選定結果。
├─ viewmodel/DoctorViewModel.kt            # 呼叫推薦、管理排序／日期篩選及選定班表。
├─ viewmodel/ConfirmViewModel.kt           # 管理掛號確認與導引前的語音清理。
├─ viewmodel/HistoryViewModel.kt           # 管理歷史清單與讀取狀態。
├─ repository/MedicalRepository.kt         # 串接 API，保存當前 case／推薦／script 與本機歷史。
├─ repository/TtsSession.kt                # TTS session、預取、pending 共用、快取與生命週期管理。
├─ network/MedicalApiClient.kt             # 以 HTTP 呼叫 Backend 的問診、推薦、查詢、script 與語音 API。
├─ network/MedicalDtos.kt                  # 前後端 request／response DTO 與 JSON 轉換。
├─ util/AudioRecorder.kt                   # 錄製台語 ASR 使用的 16 kHz mono PCM WAV。
├─ util/AudioPlayer.kt                     # 解碼並播放 WAV／MP3／M4A 等 TTS 音訊。
├─ util/SystemTextSpeaker.kt               # Backend TTS 失敗時使用 Android 系統 TTS 降級。
├─ service/MyAccessibilityService.kt       # 解析院方 App 畫面並執行掛號／取消掛號提示流程。
├─ service/OverlayManager.kt               # 顯示紅框、說明與操作提示浮層。
├─ service/CancellationGuidance.kt         # 定義取消掛號步驟與完成判斷。
├─ service/CancellationAppointmentMatcher.kt # 辨識真正可操作的取消按鈕及多筆預約情境。
├─ service/CancellationScrollSearchState.kt  # 控制取消掛號清單的有限次捲動搜尋。
├─ service/GuidanceSessionState.kt         # 隔離每次 Accessibility 導引的步驟與狀態。
├─ navigation/NavGraph.kt                  # 定義 Compose routes 與主要畫面導航。
└─ model/VisitPlan.kt                      # 定義 initial／followup／quick_search／return_visit 類型。
```

---

# 3. 後端主要檔案與用途（精簡）

```text
BACKEND（Back-End）
├─ main.py                                 # 本機啟動入口，載入 app.main:app 並以 8080 啟動 Uvicorn。
├─ app/main.py                             # 建立 FastAPI、註冊 middleware／routes、health 與啟停事件。
├─ app/config.py                           # DB、AI、Batch 與 Voice Gateway 環境設定。
├─ app/schemas.py                          # 問診、推薦、快速查詢、script 與語音資料契約。
├─ app/routes/chat.py                      # AI 問診、Batch、確認／修改及科別釐清主路由。
├─ app/routes/recommend.py                 # 已確認問診的推薦入口與 visit type 保護。
├─ app/routes/schedules.py                 # 快速查詢 `/schedules/search` 路由。
├─ app/routes/reference.py                 # 正式科別與醫師參考資料路由。
├─ app/routes/followup.py                  # 舊回診推薦相容路由。
├─ app/routes/generate_script.py           # 驗證選定班表並產生掛號導引 script。
├─ app/routes/voice.py                     # `/voice/asr`、`/voice/tts`、cleanup、health 與相容 voice chat。
├─ app/services/rule_engine.py             # 決定必要問題、紅旗、完整度、急迫性與問診狀態。
├─ app/services/batch_question_service.py  # 組合分批問題。
├─ app/services/batch_extraction_service.py # 先規則抽取，模糊欄位最多一次 AI 語意抽取。
├─ app/services/appointment_service.py     # 科別判斷、班表篩選及專長／時間雙排序推薦。
├─ app/services/schedule_filter.py         # 正規化日期／診次並過濾不可掛號、過期或不符偏好班表。
├─ app/services/quick_search_service.py    # 執行無 AI 的複診班表查詢、去重與選定班表重驗證。
├─ app/services/script_service.py          # 將已驗證 recommendation 轉為描述性導引步驟。
├─ app/db.py                               # 以 pyodbc 讀取科別、醫師與 Schedule，並提供 mock fallback。
├─ app/services/ai_service.py              # Cerebras／Gemini client 抽象、timeout 與 provider 呼叫。
├─ app/services/rag_triage_adapter.py      # 語意補強，但不得覆寫規則安全邊界。
├─ app/services/project_smart_department_adapter.py # 受合法科別清單限制的科別判斷 adapter。
├─ app/services/ai_reply_generator.py      # 產生受限的問診顯示文字，失敗時回 deterministic 文字。
├─ app/services/voice_client.py            # Voice Gateway `/api/process` 的 ASR／TTS client 與格式正規化。
├─ app/services/voice_gateway_executor.py  # 分離 ASR／TTS workers，讓本機待處理 ASR 優先。
├─ app/services/tts_cache.py               # TTS session cache、pending 共用、TTL 與 cleanup。
├─ app/services/voice_perf.py              # 記錄 ASR／TTS route、等待與 Gateway 分段時間。
├─ app/services/case_store.py              # 保存單一 Backend process 內的 case 與推薦狀態。
└─ tests/                                  # 問診、規則、推薦、DB、AI 契約、快速查詢及語音回歸測試。
```

取消掛號沒有 Backend route 或 service；現行功能完全位於 Android 的 `MyAccessibilityService.kt`、`CancellationGuidance.kt`、`CancellationAppointmentMatcher.kt`、`CancellationScrollSearchState.kt` 與 `GuidanceSessionState.kt`，避免把前端導引誤畫成後端功能。

---

# 4. 圖一可直接使用的「後端技術架構」版本

```text
BACKEND／FastAPI
├─ main.py                              # Uvicorn 啟動入口
├─ app/main.py                          # App 組裝、middleware、routes、health
├─ app/config.py                        # DB／AI／Batch／Voice 環境設定
├─ app/schemas.py                       # API 與核心資料模型
├─ app/routes/
│  ├─ chat.py                           # 問診、Batch、確認與科別釐清
│  ├─ recommend.py                      # 問診後掛號推薦
│  ├─ schedules.py                      # 快速查詢
│  ├─ reference.py                      # 科別／醫師主資料
│  ├─ followup.py                       # 舊回診推薦相容入口
│  ├─ generate_script.py                # 班表重驗證與導引 script
│  └─ voice.py                          # ASR／TTS／cleanup／voice health
├─ app/services/
│  ├─ rule_engine.py                    # deterministic 問診與安全規則
│  ├─ question_specs.py                 # 標準問題與五種問句版本
│  ├─ batch_question_service.py         # Batch 問題組合
│  ├─ batch_extraction_service.py       # 規則優先、AI 補強的欄位抽取
│  ├─ appointment_service.py            # 科別判斷與推薦排序
│  ├─ schedule_filter.py                # 班表可用性／偏好篩選
│  ├─ quick_search_service.py           # 無 AI 的複診快速查詢
│  ├─ script_service.py                 # 產生描述性導引步驟
│  ├─ ai_service.py                     # 外部 LLM provider client
│  ├─ rag_triage_adapter.py             # 問診語意補強
│  ├─ project_smart_department_adapter.py # 合法科別範圍內的判斷
│  ├─ ai_reply_generator.py             # 安全限制下的問診文字生成
│  ├─ case_store.py                     # process-local case／推薦狀態
│  ├─ voice_client.py                   # Voice Gateway client
│  ├─ voice_gateway_executor.py         # ASR／TTS worker 隔離與優先序
│  ├─ tts_cache.py                      # TTS session cache／TTL／cleanup
│  └─ voice_perf.py                     # 語音分段效能紀錄
├─ app/db.py                            # SQL Server 查詢與資料映射
├─ data/                                # mock／fallback 參考資料
└─ tests/                               # Backend 自動化回歸測試
```

---

# 5. 前後端主要流程（簡短版）

- **AI 問診流程**：`VisitTypeSelectionScreen` → `ChatScreen／ChatViewModel` → `MedicalRepository` → `POST /chat` → deterministic 欄位抽取／規則與紅旗 gate → 必要時單次 AI 語意補強 → 科別判斷 → 使用者確認。
- **推薦流程**：已確認 case → `POST /recommend` → 合法科別判斷 → DB 讀取正式班表 → strict visit type／日期／時段／狀態篩選 → 專長優先與時間優先排序 → `DoctorSelectionScreen`。
- **快速查詢流程**：`QuickSearchScreen` → 科別／日期／時段 → `GET /schedules/search` → `quick_search_service` → DB 複診班表篩選／去重 → 選定班表 → `POST /generate_script` 再驗證 → 確認頁。
- **台語 ASR／TTS 流程**：`AudioRecorder` 產生 WAV → `/voice/asr` → ASR 專用 worker → Voice Gateway `taiwanese_asr` → 辨識文字只填入可編輯草稿；文字播放 → `TtsSession` → `/voice/tts` → cache／TTS worker → Voice Gateway `taiwanese_tts` → Base64＋音訊格式 → `AudioPlayer`，失敗時可降級系統 TTS。
- **Accessibility 掛號導引流程**：確認選定班表 → `VisitPlan` 映射 `AppointmentType` → `MyAccessibilityService.updateTarget()` → 開啟院方 App → 依科別／診別／日期／時段／醫師顯示紅框與提示 → 個資頁停止代操作、交由使用者完成。

---

# 6. 實作成果評估：目前可以寫的內容

## A. 可以直接寫進企畫書的已確認成果

- **Backend 完整回歸**：本次最新工作樹實跑 `280 passed`，另有 `134 subtests passed`，0 failed。較早的 `198 passed` 可保留為歷史 checkpoint，但不應取代本次最新數字。
- **Backend 語音測試**：`test_tts_cache.py`、`test_voice_perf.py` 加上 `VoiceCompatibilityTest`，本次實跑 **23 passed**。
- **Android unit tests**：本次實跑 **117 passed**，0 failure、0 error、0 skipped。較早的 `112 passed` 是歷史 checkpoint。
- **Android build**：以 `--rerun-tasks` 強制重跑後，`assembleDebug` 為 **BUILD SUCCESSFUL**，43 個 tasks 實際執行。
- **台語 ASR runtime smoke**：Gateway 與 Backend 呼叫成功；可寫「單次 Gateway processing time 為 **1632 ms**」，必須同時標示為單次 smoke sample，不能寫成平均 ASR 延遲。
- **台語 TTS runtime smoke**：Gateway 與 Backend 呼叫成功，回傳合法 M4A；現行 Backend 會正規化回傳格式，Android `AudioPlayer` 支援 M4A 播放。
- **複診導引修正**：`ConfirmNeedScreen` 已明確傳遞 `appointmentType`；`FOLLOW_UP`、`QUICK_SEARCH`、`RETURN_VISIT` 均正確映射至 `RETURN_VISIT`。
- **快速查詢映射**：`quick_search` 的正式 DB 查詢只接受具複診依據的 Schedule，對應院方導引的 `RETURN_VISIT`；它不進入 AI 問診或一般推薦流程。
- **取消掛號與 Accessibility 防誤導**：取消流程具有明確 session、有限捲動、精確取消按鈕比對與確認完成條件；可寫「功能與單元測試已完成」，但不能寫成院方 App 實機成功率。

## B. 目前不能寫成正式實測數字的項目

| 欲使用的數字 | 目前可寫內容 | 不能寫成正式數字的原因 |
|---|---|---|
| End-to-End 8～10 秒 | 主要功能管線已串接；部分 Backend／語音階段具計時能力 | 沒有統一從 App 開始到院方 App 完成導引的起訖點與多次樣本 |
| ASR 平均 0.5 秒 | ASR 已完成且可量測；目前只有一次 Gateway **1632 ms** smoke sample | 單次 Gateway processing 不等於平均值，也不含 Android 上傳與畫面更新 |
| LLM 4～5 秒 | AI provider call 已有 latency log，可在正式情境累積樣本 | 沒有固定 prompt、相同 provider／model 的多次有效樣本與統計 |
| SQL < 0.1 秒 | SQL 查詢、篩選與推薦功能已完成 | `db.py` 尚無完整 connection／execute／fetch 分段 timer，也沒有正式樣本集 |
| TTS 3.5 秒 | TTS smoke 已成功並回傳合法 M4A；Backend 有 cache hit／miss 與 Gateway 分段 log | 沒有多次真實 cache miss／hit 的平均、median、p95 與成功率 |
| Overlay < 0.3 秒 | Overlay 與 180 ms 穩定等待機制已完成 | 180 ms 是程式常數，不是 event-to-render 實測總延遲；尚無實機樣本 |

因此，上述項目目前只能寫成「功能已完成／部分階段已具備量測機制，後續可累積統計」。其中完整 E2E、SQL 與 Overlay 若要形成正式統計，仍需補共同計時點或以固定實機流程採樣。

---

# 7. 圖二「實作成果的評估」可直接貼用文字草稿

## （一）系統端到端整體效能與管線化驗證

本系統已完成 Android 前端、FastAPI 後端、問診規則、AI 語意補強、正式班表查詢、推薦、台語語音及 Accessibility 導引等主要模組串接。最新工作樹之 Backend 完整回歸測試為 **280 項通過**（另有 **134 項 subtests 通過**），語音相關測試為 **23 項通過**；Android unit tests 為 **117 項通過**，且 `assembleDebug` 強制重跑結果為 **BUILD SUCCESSFUL**。既有 198 項 Backend 與 112 項 Android 測試紀錄可視為前一階段 checkpoint，本次結果則反映最新程式範圍。

在真實語音 smoke 驗證方面，台語 ASR 已成功通過 Gateway 與 Backend，取得一筆 **Gateway processing time 1632 ms** 的單次樣本；台語 TTS 亦已成功回傳合法 M4A 音訊。此結果足以證明語音 runtime 管線已恢復，但 1632 ms 僅代表單次 Gateway 處理樣本，不代表平均 ASR 延遲，也不能推論完整端到端為 8～10 秒。現行程式已能記錄 ASR／TTS 的 request、worker 等待、Gateway 執行與總處理時間，以及 LLM provider latency；後續可在固定裝置、網路、模型與 cache 條件下累積樣本並統計平均、median、p95 與成功率。完整 E2E、SQL 與 Overlay 尚須補共同起訖計時或固定實機量測，現階段不填入未經驗證的平均數字。

## （二）業務閉環與雙向痛點破解效益

使用者端已形成「症狀蒐集 → 安全規則與必要 AI 補強 → 科別與班表推薦 → 掛號確認 → 院方 App 視覺導引」的主要閉環；對已有明確需求的使用者，另提供不經 AI 問診的快速查詢路徑，可直接依科別、日期與時段篩選正式複診班表。選定結果在產生導引 script 前會再次向 DB 核對，降低使用過期或被竄改班表的風險。

本次亦完成複診導引的關鍵修正：`FOLLOW_UP`、`QUICK_SEARCH` 與 `RETURN_VISIT` 已正確映射至院方 App 的 `RETURN_VISIT` 區段，避免複診流程誤導至初診掛號。反向需求方面，首頁提供取消掛號導引，透過明確 session、可取消按鈕辨識、有限捲動及「確認取消掛號」完成條件，降低誤選、誤判與舊導引殘留。上述成果可寫為功能閉環與自動化測試已完成；第三方院方 App 的完整實機成功率、節省時間比例及平均處理秒數，仍應在固定版本與足夠樣本下另行量測。

---

# 8. 最後給組長的超精簡結論

- 目前資訊已足夠先完成企畫書中的系統架構、主要流程、功能完成度與自動化驗證成果。
- AI 已用於模糊語意抽取、科別判斷、專長評分與問診文字補強；核心紅旗、流程狀態與合法值驗證仍由 deterministic 規則掌握。
- 「其他不舒服症狀」目前可以接入：回答會保存為 `department_context`，供後續科別判斷的規則／AI adapter 使用；它不是自由診斷入口，AI 結果仍受合法科別與安全 gate 限制。
- 後續可補強 AI 的方向包括模糊症狀理解、科別排序理由與自然語句品質，但不建議讓 AI 直接決定紅旗通過、產生不存在的科別／醫師或代替正式班表。
- 今天可先交：最新檔案架構、五條主要流程、`appointmentType` 修正、快速查詢、取消導引、Backend／Android 測試與 build 結果，以及兩項台語語音 smoke 成果。
- 仍缺正式樣本的數據：完整 E2E、ASR 平均、LLM 平均、SQL latency、TTS 平均及 Overlay latency／成功率；不能把單次 smoke 或程式延遲常數寫成平均值。
- 下一階段建議在固定手機、網路、Backend／Gateway 版本及 cache 條件下分組採樣，至少同步記錄樣本數、平均、median、p95、成功率與 fallback 原因。
- Accessibility 與取消掛號目前可寫「功能與單元測試完成」；若要宣稱正式院方 App 的完成率或節省工時，仍需真實實機端到端驗證。
