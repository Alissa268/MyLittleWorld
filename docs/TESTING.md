# 測試指南

本文件記錄目前的標準指令、最新結果與 runtime smoke checklist。測試應從正式 `backend/`／`android/` 執行。

## 最新完整結果

- 驗證日期：2026-09-14
- Backend pytest：`280 passed`，另有 `134 subtests passed`；0 failed。
- Android `testDebugUnitTest`：`BUILD SUCCESSFUL`。
- Android `assembleDebug`：`BUILD SUCCESSFUL`。

Backend 建議從 repository root 執行：

## Backend

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\test-backend.ps1
```

此腳本會執行既有 unittest suites、`python -m pytest tests -q`、`/health` 與 OpenAPI contract 檢查。

主要涵蓋：

- deterministic triage、semantic normalization/refinement
- Batch question/extraction、red flag 與 single-message fallback
- 409 visit type conflict 與 confirmation gate
- SQL adapter、status／placeholder／strict visit type filter
- recommendation、specialty/time ranking 與 no-slots regression
- QuickSearch 的科別 reference、班表查詢與 generate-script 流程
- AI reply/provider fallback 與 project-smart concept adapter

## Android unit tests

```powershell
cd android
.\gradlew.bat testDebugUnitTest
```

主要涵蓋：

- canonical navigation／VisitPlan
- Batch DTO parsing、keyed answers、required validation、下一批替換
- confirmation 與 recommendation visit type
- no-slots／409 使用者訊息
- QuickSearch request、日期／時段驗證與 schedule response
- history persistence/read-only compatibility
- recommendation/script parsing 與 Accessibility mapping

## Android build

```powershell
cd android
.\gradlew.bat assembleDebug
```

成功條件是 `BUILD SUCCESSFUL`。`build/` 與 APK 均為 local-only ignored artifacts。

## Runtime smoke checklist

完整整合或修改相關模組後，依序確認：

- [ ] Backend `/health` 回 200。
- [ ] Initial：Batch start、一次 answers POST、確認、`/recommend`。
- [ ] Followup：Batch start、一次 answers POST、確認、`/recommend`。
- [ ] QuickSearch：`/reference/departments` 載入科別 → `/schedules/search` 查詢班表 → `/generate_script` → ConfirmNeed。
- [ ] No slots：嚴格日期／時段與 visit type，沒有假醫師。
- [ ] 409：同 case 不得切換 canonical visit type。
- [ ] Old single message：`BATCH_TRIAGE_ENABLED=false` 仍可逐題問診。
- [ ] Voice：國語／台語 ASR、`/voice/chat`、TTS failure fallback。
- [ ] Red flag：正向危險徵兆顯示 warning，空／重複 start 不得自動通過。
- [ ] Confirmation：修改與確認維持 case／visit type，不可未確認推薦。
- [ ] History：舊紀錄可讀、完成紀錄唯讀，不洩漏前一 case state。
- [ ] Rotation／keyboard／scroll：Batch 欄位與提交按鈕可達，輸入不遺失。

## 外部服務判讀

Unit tests 與 APK build 成功，不代表 SQL、Gemini、Cerebras、Voice Gateway 或院方 App 已部署。每個外部服務必須單獨記錄實際 endpoint health 與 smoke 結果；禁止因 mock test 成功就標示 runtime completed。
