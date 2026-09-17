# 台語 ASR／TTS contention 診斷與修改報告

日期：2026-09-04。分支：`codex0904`。在上一輪尚未 commit 的修改上繼續工作；沒有 reset、切 branch、commit、push、修改 .env、金鑰或正式 DB。上一輪紀錄保留在 `VOICE_FLOW_REPORT_2026-09-04.md`，排程與最新測試結果以本報告為準。

## 第一階段檢查 A–G

| 問題 | 程式碼可確認的結果 |
|---|---|
| A. 台語 ASR 與 TTS 是否共用 Gateway？ | 是，共用 `VOICE_GATEWAY_URL` 與 `POST /api/process`；以 `taiwanese_asr`、`taiwanese_tts`、`chinese_tts` 區分。每次建立 client，並非共用一個有鎖的 requests.Session。 |
| B. Backend 原本是否有 lock／semaphore／queue 讓 ASR 等 TTS？ | 沒有語音共同的 semaphore 或跨文字 cache lock；但是 asyncio 的預設 executor 是共用有限 thread pool，繁忙時會排隊。reference、followup 等其他 to_thread 也會使用預設 pool。 |
| C. to_thread 是否可以並行？ | 可並行執行 blocking HTTP，但取決於 executor 有空位；不能保證 Gateway 內部並行。本機 Python 3.12 實測 CPU count=16、預設 executor workers=20。 |
| D. 外部 Gateway 是否單 worker／單 GPU queue？ | 無法確認。專案只有 client，没有 Gateway server、GPU 排程或部署設定。同一 endpoint 不代表內部必然同一模型 worker。 |
| E. Android 原本一次預生成幾個？ | 每次最新 AI message 或語言改變，準備最新顯示的一題；每次 effect 一個。 |
| F. 是否會同時預生成整批問題？ | 不遍歷 questionBatch。但使用者快速送出切題／切語言，舊生成持續、新生成啟動；不同 key 沒有併發上限，未完成 N 個 key 就可能有 N 個工作。相同 key 原本已共用 pending。 |
| G. 開始錄音時是否繼續預生成？ | 原本是。startVoiceRecording 會 stopVoicePlayback，但取消播放 waiter 不會取消獨立的 TtsSession 工作，也沒有暫停新背景工作。 |

原台語路徑：`AudioRecorder → MedicalRepository.transcribeVoice → MedicalApiClient.voiceAsr → /voice/asr → asyncio.to_thread → VoiceGatewayClient.transcribe → /api/process`。

國語維持 `RecognizerIntent`，不走這個 Backend ASR 路徑。

## A. 根因判斷

**實機根因尚未確定。** 已確認兩個可能放大 contention 的程式碼條件：Backend ASR／TTS 共用預設 executor；Android 不同題目的背景生成沒有上限且錄音時不暫停。

Mock 重現：刻意限制舊式共享 executor 為 2 個 worker，讓 2 個 blocking TTS 佔滿，ASR 會等待空位。這證明本地排隊風險存在，**不等於實機已飽和**。本機實際預設容量是 20；僅提供的幾行完成順序沒有到達時間、active 數量或 worker 等待資料，不能拿來斷定這次延遲的來源。

外部 Gateway 共用 queue／GPU contention 仍是待驗證假設，需 L 節的 timing 和 Gateway 端 log 證據。

## B. 是否確認共用資源

已確認原 Backend 使用同一套 Gateway URL／HTTP endpoint 與預設 executor。未確認外部 Gateway 是否共用模型 worker、CPU/GPU、GPU memory、單 queue 或 concurrency limit。

修改後 ASR 與 TTS 各有專用 executor，仍使用同一外部 Gateway；沒有修改外部 URL、金鑰或部署設定。

## C. ASR 是否真的在等 TTS

- 舊式 executor 在 mock 飽和情境會等待，測試已證明。
- **實機那次 ASR 是否在等 TTS 尚未證明**，沒有真實 request_received／gateway_start 的測量。
- 新測試確認：2 個 TTS 同時未完成，ASR 仍可進入專用 worker；已排入的 ASR 不需要等 TTS executor slot。
- 如果 Gateway 已收到一個不能搶占的 TTS，Backend 無法保證後來 ASR 在遠端不排隊；本次沒有假裝可以取消外部計算。

## D. 原本背景 TTS 數量

每次顯示一題觸發一個，而非一次預生成整批。但沒有總併發上限：快速歷經 Q1、Q2、Q3、切語言時，可累積多個不同 key 的未完成請求。實際併發數不能由 access log 的 200 完成順序回推。

現在每個 Android TtsSession 最多執行 **1 個背景 prefetch**，最多保留最新問題的未送出 prefetch。手動播放可優先送出，已在執行的 HTTP 不被强制取消，因此「總 TTS」仍可能包含背景＋使用者明確播放。切換新 session 時，上一 session 已發出的外部工作也可能短暫重疊。

## E. 新增 timing logs

新增 `VoicePerf: {JSON}`，logger 為 `uvicorn.error.voice_perf`，沿用 Uvicorn console handler 的 INFO 輸出。

完整追蹤 `/voice/asr`、`/voice/tts`。ASGI middleware 在 body／multipart 解析前記錄到達，Gateway worker 內才記錄真正開始呼叫 client，回應 headers 準備送出時記錄 response_ready。

| 欄位／事件 | 意義 |
|---|---|
| `request_id` | Backend 自產隨機 ID，每次 HTTP 一個，不信任使用者輸入為 log ID |
| `session_id` | TTS 使用合法 UUID；ASR 無 session 欄位，沿用 request_id，未改 ASR schema |
| `request_received` | 請求進入 ASGI app 的時間；不含 TCP／反向代理之前的等待 |
| `gateway_submitted` | 已呼叫本地 executor 排程入口 |
| `gateway_start` | 實際 worker 開始呼叫 blocking Gateway client |
| `gateway_end` | blocking client 真正結束（包含錯誤） |
| `response_ready` | ASGI response start，並含 `http_status` |
| `wait_before_gateway_ms` | gateway_start − request_received，包含 upload/body parse、驗證與本地排程等待 |
| `executor_and_admission_wait_ms` | gateway_start − gateway_submitted，縮小到 executor／ASR 優先入口等待 |
| `gateway_duration_ms` | gateway_end − gateway_start；包含 HTTP／網路、Gateway 排隊與推論，無法單獨區分它們 |
| `total_duration_ms` | response_ready − request_received；尚未結束的事件則為當下累計 |
| `cache` | `cache_hit`、`cache_miss`、`pending_join`；舊無 session client 為 `uncached` |
| `cache_key` | session＋文字／語言／語速的 SHA-256 16 字元摘要；不印原文或未加 session 的短句 hash |
| `producer_request_id` | pending_join 指向最初生成該音訊的 request，避免把「等待共享結果」誤當成自己再次呼叫 Gateway |
| `active_asr`／`active_tts` | 此 Backend process 正在 blocking client 呼叫內的數量；gateway_start 含自己，gateway_end 已扣自己 |
| `pid`／`task`／`thread` | process、async task、實際 worker 名稱，例如 voice-asr／voice-tts |
| `outcome` | 成功、空辨識、停用、輸入錯誤、Gateway error、取消等狀態，不含原始錯誤內容 |
| `asr_priority_wait` | TTS worker 發現本地已有尚未完成的 ASR，讓出 Gateway 入口 |
| `request_interrupted` | 請求被取消／非預期例外，中止的 request 不一定有 response_ready |

時間戳是 UTC Unix 秒，耗時以 monotonic clock 計算，不受系統校時跳動影響。request_received 事件尚未解析 body，TTS 的 session_id 暫用 request_id，後续 cache／response 事件才有合法 session UUID；依 request_id 串接即可。

Cache hit／pending join 沒有發起自己的一次 Gateway 呼叫，所以 gateway 時間欄位為 null；pending 的實際生成時間請查 producer_request_id。已取消 HTTP 等待但 blocking 呼叫仍在執行時，active counter 會保留直到真正結束。

此外移除原 ASR 成功 log 的完整轉錄，以及 Gateway 錯誤 log 的 response body、原始 error／data keys；錯誤改記固定類型／HTTP status。CLI 語音測試入口也不再印辨識全文。沒有 log API key、Base64 audio、音檔內容、病患回答或上游原始錯誤 body。

## F. 實際修改策略

**Backend**

1. 使用 Python 標準 `ThreadPoolExecutor`，ASR 2 個專用 worker、TTS 2 個專用 worker；不再占用共用 asyncio default pool。
2. ASR 排入專用 executor 後，TTS 尚未進入 Gateway 的工作會等待這些 ASR 完成。ASR 不等待 TTS pool／TTS semaphore。
3. Condition 只維護尚未完成的 ASR 數量，不在 lock 裡進行 HTTP 或 logging；TTS 的 condition.wait 釋放 lock。
4. ASR 完成才解除其 reservation；HTTP caller 取消不會把尚在執行的 Gateway 工作當成結束。
5. 尚未执行的排程可取消，已發出的 blocking HTTP 會繼續；cache cleanup 仍可阻止結果回填。
6. 原 `/voice/chat` 相容路徑也走同一 executor，避免繞過本地資源隔離；完整 HTTP timing middleware 聚焦 `/voice/asr`、`/voice/tts`。

**Android**

1. 每個 session 最多 1 個背景 prefetch；未送出部分只保留最新題目／語言。
2. 麥克風啟動時同步暫停背景入口；台語錄音與 ASR loading 期間持續暫停。
3. 已 ready 的音訊保留；已 dispatch 的工作繼續完成並 cache，不假裝可取消遠端請求。
4. ASR finally（成功／失敗）恢復 prefetch，仍只把成功文字填入草稿。取消／離開畫面不會為了恢復而多送背景工作。
5. 使用者明確播放時可將尚未送出的 prefetch 提升為前景，共用相同結果；不重複 HTTP。
6. 當前播放在麥克風啟動時的 `stopVoicePlayback()` **維持原行為**；沒有另外改音訊回授策略。
7. 不變更 ChatScreen 顯示新問題即 prepareSpeech 的觸發，不關閉預生成、不改回按播放才生成、不刪除 cache。

優先保障範圍：本地 ASR > 本地尚未送入 Gateway 的 TTS；Android 明確播放 > Android 尚未送出的背景 prefetch。外部 Gateway 已接收工作不能在這裡重排或搶占。

## G. 為什麼選這個策略

程式碼已確認共享 executor 與背景生成無上限，mock 可重現本地排隊風險，所以用小型標準庫 executor 隔離及 Android admission 控制先消除已知風險。沒有修改 Gateway 協定、沒有新增 API endpoint／ASR session 欄位，也沒有引入 Redis、Celery 或 RabbitMQ。

TTS 合成仍有背景預生成、session cache、pending 共用與 TTL。外部 contention 不可從 client 原始碼證明，因此沒有宣稱已根治下游排程。

## H. 本輪修改檔案

相對專案根目錄；Android 縮寫前綴為 `android/app/src/main/java/com/example/medicalaiguidance/`。

| 檔案 | 本輪變動 |
|---|---|
| `backend/app/services/voice_perf.py`（新增） | ASGI timing、request 關聯、安全摘要、Gateway 實際執行數量與分階段 log |
| `backend/app/services/voice_gateway_executor.py`（新增） | ASR／TTS 分開的有限 executor，ASR 優先 admission，取消時的真實執行狀態 |
| `backend/app/routes/voice.py` | 連接 trace／executor，移除 ASR 全文與原始錯誤 log；schema 不變 |
| `backend/app/services/tts_cache.py` | cache_hit／cache_miss／pending_join 回報及 producer request ID；保留原 cache／cleanup |
| `backend/app/services/voice_client.py` | 刪除含原始 response body、錯誤內容或轉錄文字的 log／CLI 輸出 |
| `backend/app/main.py` | 註冊 VoicePerf ASGI middleware；既有 TTL sweep 保留 |
| Android `repository/TtsSession.kt` | 背景併發 1、最新題目待辦、錄音 pause／resume、明確播放提升優先、共用結果 |
| Android `viewmodel/ChatViewModel.kt` | 麥克風／ASR 生命週期操作 prefetch gate，透過 session factory 支援無外部呼叫測試 |
| `backend/tests/test_voice_perf.py`（新增） | 5 項排隊重現、ASR 與 TTS 並行／優先、counter、敏感 log、cache 關聯測試 |
| `android/app/src/test/java/com/example/medicalaiguidance/VoiceFlowUnitTest.kt` | 增加 4 項 prefetch／錄音／前景播放回歸，保留原 9 項 |
| `docs/VOICE_PERF_REPORT_2026-09-04.md`（新增） | 本報告 |

其他 git status 項目來自上一輪；本輪没有 reset 或重新覆蓋它們。`ChatScreen.kt`、`MedicalRepository.kt`、錄音／播放工具與掛號跳轉本輪没有新增功能改動。

## I. Backend 測試結果

```powershell
# 在 backend 工作目錄；只在本次 process 停用 .env，檔案不變
& .venv/Scripts/python.exe -c "from app.config import Settings; Settings.model_config['env_file']=None; import pytest; raise SystemExit(pytest.main(['tests','-q']))"
```

**180 passed、24 subtests passed、0 failed**。新增 5 項測試：

- 舊共用 executor 飽和會排隊（刻意 2-worker mock，非實機根因認定）。
- 2 個慢 TTS 正在執行時 ASR 仍開始；ASR 未完成時新 TTS 讓出 Gateway 入口；counter 回到 0。
- 取消等待不會隱藏仍在跑的 ASR，例外完成後 reservation／counter 正常。
- HTTP timing 時間順序、ASR 與 TTS 真實 mock 重疊、cache hit 無 Gateway、pending_join 只共享一次生成且關聯正確、無敏感內容。
- 上游錯誤 body／金鑰／原文不被新的 timing 與 Gateway error log 輸出。

原 16 項 TTS cache／TTL／cleanup 測試與全部既有問診、推薦、安全 gate、掛號回歸一起通過。未大量或少量呼叫真實外部 ASR／TTS；新增執行都是 mock。

## J. Android 測試結果

**65 tests、0 failures、0 errors、0 skipped**。新增 4 項：

- 背景只執行 1 個，Q1 未完成時 Q2 被新 Q3 取代，之後只生成 Q3。
- pause 保留 ready／正在執行的音訊、取消舊待辦、resume 只準備最新題目。
- 明確播放提升未送出 prefetch，不重複 HTTP。
- ViewModel 語音輸入 pause／完成後 resume，仍只填草稿且可修改，沒有自動送出。

保留原 ASR callback 重複保護、手動送出 guard、session／語言隔離、取消 waiter、cache／TTL、播放 request 序列化等測試。ViewModel 測試使用共同的語音 pause/resume 路徑，沒有宣稱單元測試實際操作 Android 麥克風或真實 Recognizer Activity。

## K. assembleDebug

```powershell
# 在 android 工作目錄
$env:JAVA_HOME='C:\Program Files\Android\Android Studio\jbr'
.\gradlew.bat testDebugUnitTest assembleDebug --no-daemon --offline
```

**BUILD SUCCESSFUL**。APK 位於 `android/app/build/outputs/apk/debug/app-debug.apk`。仍有既有 SDK XML／Compose icon 警告；Backend 沿用 on_event／TestClient deprecation 警告，皆非失敗。`git diff --check` 通過。

## L. 實機重測看哪些 log

啟動更新後 Backend 與 Android APK；在 Backend console 搜尋 `VoicePerf:`，以 request_id 分組。每筆 request_received、gateway_submitted、gateway_start、gateway_end、response_ready 都會即時輸出，不必等到 200 才知道 request 到達。

建議比較：一次單獨台語 ASR；一次新問題的台語 TTS miss／明確播放尚未完成時立刻錄音；再一次同題 ready cache 的播放後錄音。保持近似音訊長度與網路，不要用 cache hit 去跟 cache miss 混比。

| 觀察 | 能判斷的事／下一步 |
|---|---|
| ASR 很晚才有 request_received | 尚未進 Backend ASGI；檢查 Android 上傳、網路、proxy／冷啟動，不能直接歸因 Backend TTS lock |
| request_received 早，但 gateway_submitted 晚 | 主要在上傳／body parse／route 準備階段 |
| gateway_submitted 早，但 gateway_start 晚 | 本地 executor／admission 等待；ASR 專用 pool 仍可能被其他 ASR 佔滿，查看 active_asr、pid、thread |
| ASR gateway_start 及時，但 gateway_duration_ms 大增 | 慢在 blocking Gateway client 的邊界內；仍可能是網路、Gateway queue、模型本身，需下游 log 再拆分 |
| ASR gateway_start 時 active_tts > 0 | 本地 ASR 已與 TTS HTTP 重疊，沒有等那些本地 TTS 全部完成才啟動；不代表 GPU 真正並行 |
| TTS 出現 asr_priority_wait | 本地優先控制生效；這是 TTS 讓 ASR，不是 ASR 等 TTS |
| 同 key 多筆 200，但 cache 為 pending_join／cache_hit | 多個 HTTP 回應不等於多次生成；追 producer_request_id 是否只有一次 gateway_start |
| 本機 ready 播放完全沒有新 /voice/tts log | Android cache hit，正常 |
| Gateway 收到 ASR 後到模型開始有明顯等待 | 才是更直接的下游 queue 證據；需 Gateway 方的到達、queue、推論開始／結束 log |

不要只看 response_ready 時的 active_tts；那可能已經是 0。看 ASR gateway_start 那一筆的 snapshot，以及同一時間 TTS 的 start/end 區間。

`active_*` 是此 Backend process 的 HTTP client 工作數，**不是 GPU active count**；HTTP timeout 後遠端模型可能還沒停止。不同 pid 的數量不能直接當成同一個 process 的 counter 比較。若使用非 Uvicorn 啟動或調高 log level，需讓 `uvicorn.error.voice_perf` 的 INFO 能輸出。

## M. 尚未確認的外部限制

- Gateway／下游模型 worker 數、GPU queue、是否有服務共用 lock 或推論 concurrency limit。
- 實機延遲究竟位於上傳、Backend 等待、Gateway queue、網路或模型推論。
- 外部 API 沒有在本專案提供真正 cancellation／priority contract，因此不能承諾正在遠端執行的 TTS 完全不影響之後 ASR。
- 新 Backend 限制是每 process ASR 2、TTS 2；多 process／多 instance 不共享 admission 或 cache，沿用原型單一 worker 部署邊界。
- Android priority 作用於本機尚未送出的 prefetch；已送到 Backend／外部的 TTS 不會因使用者後來按播放而被重新插隊。
- 沒有進行這次症狀的實機重測，也沒有測得改善百分比；待使用者收集新 timing log 後再作根因定論。

## N. git diff --stat

以下是相對 HEAD 的累計輸出，**包含上一輪尚未 commit 的修改**，且 Git 不計入 untracked 新檔。新增檔案見 H 與 O；沒有為取得統計執行 git add。

```text
 android/app/build.gradle.kts                       |   1 +
 .../com/example/medicalaiguidance/MainActivity.kt  |   2 +
 .../medicalaiguidance/network/MedicalApiClient.kt  |  12 +-
 .../medicalaiguidance/network/MedicalDtos.kt       |   4 +-
 .../repository/MedicalRepository.kt                |  39 +-
 .../example/medicalaiguidance/screen/ChatScreen.kt |  40 +-
 .../medicalaiguidance/screen/ConfirmNeedScreen.kt  |  14 +-
 .../example/medicalaiguidance/util/AudioPlayer.kt  |  16 +-
 .../medicalaiguidance/util/SystemTextSpeaker.kt    |  16 +-
 .../medicalaiguidance/viewmodel/ChatViewModel.kt   | 519 ++++++---------------
 .../viewmodel/ConfirmViewModel.kt                  |  16 +
 backend/app/main.py                                |  23 +
 backend/app/routes/voice.py                        |  74 ++-
 backend/app/services/voice_client.py               |  32 +-
 14 files changed, 371 insertions(+), 437 deletions(-)
```

## O. git status

```text
## codex0904...origin/codex0904
 M android/app/build.gradle.kts
 M android/app/src/main/java/com/example/medicalaiguidance/MainActivity.kt
 M android/app/src/main/java/com/example/medicalaiguidance/network/MedicalApiClient.kt
 M android/app/src/main/java/com/example/medicalaiguidance/network/MedicalDtos.kt
 M android/app/src/main/java/com/example/medicalaiguidance/repository/MedicalRepository.kt
 M android/app/src/main/java/com/example/medicalaiguidance/screen/ChatScreen.kt
 M android/app/src/main/java/com/example/medicalaiguidance/screen/ConfirmNeedScreen.kt
 M android/app/src/main/java/com/example/medicalaiguidance/util/AudioPlayer.kt
 M android/app/src/main/java/com/example/medicalaiguidance/util/SystemTextSpeaker.kt
 M android/app/src/main/java/com/example/medicalaiguidance/viewmodel/ChatViewModel.kt
 M android/app/src/main/java/com/example/medicalaiguidance/viewmodel/ConfirmViewModel.kt
 M backend/app/main.py
 M backend/app/routes/voice.py
 M backend/app/services/voice_client.py
?? android/app/src/main/java/com/example/medicalaiguidance/repository/TtsSession.kt
?? android/app/src/test/java/com/example/medicalaiguidance/VoiceFlowUnitTest.kt
?? backend/app/services/tts_cache.py
?? backend/app/services/voice_gateway_executor.py
?? backend/app/services/voice_perf.py
?? backend/tests/test_tts_cache.py
?? backend/tests/test_voice_perf.py
?? docs/VOICE_FLOW_REPORT_2026-09-04.md
?? docs/VOICE_PERF_REPORT_2026-09-04.md
```

已停在目前工作樹，等待使用者確認。未 commit、未 push。
