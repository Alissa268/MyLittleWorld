# Known Issues

本文件只記錄目前程式仍存在的限制；已修正的「不符日期仍退回其他 open rows」問題不再列為 bug。

## Critical

目前沒有已知會在 deterministic 核心流程中必然造成資料破壞的 Critical issue。若發現 visit type cross-match、red flag bypass、secret 外洩或假醫師進入正式推薦，應立即停止 push／部署並提升為 Critical。

## Important

### Cerebras 尚未真實 smoke test

Adapter、SDK 與 `gpt-oss-120b` 設定已存在，但正式環境沒有已驗證可用的 `CEREBRAS_API_KEY`。目前只能宣稱 mock/unit path 完成。

### 中文 TTS downstream 依賴外部部署

Voice Gateway 可達不代表 `chinese_tts` downstream 已啟動。既有 runtime 中 TTS 曾 unavailable；Backend／Android 有降級路徑，但仍需部署與重新驗證服務品質。

### Case store 只在記憶體

`case_store.py` 在 process restart 後遺失，且多 worker 不共享。正式部署前應改為具 TTL、transaction 與隱私策略的 persistence layer。

### Accessibility 尚未完成真實院方 App 全流程保證

`/generate_script`、DTO 與 adapter 已有測試，但院方 App UI 可能改版，resource/text selector 也可能失效。目前不能宣稱已能穩定完成真實掛號。

### Backend／Android 預設 port 不完全一致

Android default 是 host 8080；`scripts/run-backend.ps1` 預設 8000，而 Voice Gateway 程式預設也可能是 8000。啟動 Backend 時應明確傳 `-Port 8080`，之後可再統一 script default。

## Nice-to-have

### Batch 欄位缺少獨立 voice input

Chat 有整體 voice flow，但 Batch form 的每個欄位尚無各自 mic button；目前需文字填寫或走非 Batch 語音路徑。

### 尚無個人掛號歷史整合

目前快速查詢由使用者從正式科別清單選擇科別、日期與時段；尚未串接個人掛號歷史 API，因此不能自動帶入既有掛號或原看診醫師。

### Logging 與隱私治理

部分 debug/runtime log 可能包含使用者輸入或 response metadata。正式部署前應完成結構化遮罩、retention 與權限規範。

## 維護規則

- 新增 issue 時附上可重現條件、受影響 module 與驗證方式。
- 修正並有 regression test 後，從本文件移除或改寫成設計限制。
- 外部服務未驗證時使用「未驗證／unavailable」，不要寫「完成」。
