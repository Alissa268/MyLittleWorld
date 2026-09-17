# 專案狀態

- 更新日期：2026-09-14
- 正式 Backend：`backend/`
- 正式 Android：`android/`
- 示範環境：手機經 Tailscale 連至 310 電腦上由 PowerShell 啟動的 FastAPI

## 目前正式架構

Android Compose App 透過 HTTP 呼叫 FastAPI。Backend 的 `rule_engine`／checklist 掌握問診流程與 safety state；AI provider 只做允許的語意補強；推薦服務將 canonical `VisitType` 映射到 SQL Server `Schedule.visit_type`，再依日期、時段與醫師偏好排序。正式第三入口是 QuickSearch，直接使用科別與班表查詢 API，不進入症狀問診。

就診類型的現行 mapping：

| Canonical type | SQL 值 | 流程 |
|---|---|---|
| `initial` | 初診 | Batch／單題問診後推薦 |
| `followup` | 複診 | Batch／單題問診後推薦 |
| `quick_search` | 複診 | `/reference/departments` → `/schedules/search` → `/generate_script` |
| `return_visit` | 複診 | 保留給舊 navigation／API contract 的 compatibility value |

SQL 現況只有「初診／複診」，沒有獨立「回診」班表值。使用者目前可選初診、複診與快速查詢；Android 的 `Route.RETURN_VISIT` 保留為導向 `QuickSearchScreen` 的 compatibility alias，Backend 仍保留 `/followup/recommend` contract。

## 已完成

- `VisitType` schema、Android `VisitPlan` 與 409 conflict 保護。
- Initial／Followup Batch triage 與舊 single-message feature flag fallback。
- deterministic checklist、red flag gate、修改與確認狀態。
- deterministic-first batch extraction；模糊欄位最多呼叫一次 Cerebras。
- 科別判斷、SQL Schedule adapter、strict visit type filter、可行時段篩選與推薦排序。
- QuickSearch 科別清單、日期／時段查詢、`/schedules/search` 與相容用 `/followup/recommend`。
- Android DoctorSelection、NoSlots、History、Voice 與 recommendation/script DTO。
- `/generate_script`、`ConfirmNeedScreen` 與 Accessibility 視覺導引流程。
- project-smart 關鍵字概念已移植到正式 adapter，不依賴外部 reference source 資料夾。

## 已完成 Runtime 驗證

- Initial：完整 Batch、確認與 recommendation。
- Followup：完整 Batch、確認與 recommendation。
- QuickSearch：正式科別清單、班表查詢、選擇結果與 `/generate_script`。
- strict no-slots：一般推薦不回退到不符時間的班表；回診回傳空結果。
- 同 case 改變 visit type：HTTP 409。
- `BATCH_TRIAGE_ENABLED=false` 的舊單題問診。
- red flag warning、確認／修改、歷史與主要 UI navigation。
- Voice ASR 與 `/voice/chat` 的安全降級路徑。

上述是既有 Runtime checkpoint；本次文件整理重新執行的是 unit tests 與 build，不宣稱再次完成所有外部服務實機流程。

## 已完成 Unit Test／Build 驗證

2026-09-14 已重新驗證：Backend `280 passed`（另有 `134 subtests passed`）；`scripts/test-backend.ps1` 會執行既有 unittest suites、完整 pytest、`/health` 與 OpenAPI contract 檢查。Android `testDebugUnitTest BUILD SUCCESSFUL`，且 `assembleDebug BUILD SUCCESSFUL`。詳細指令與判讀見 [TESTING](TESTING.md)。

## 外部服務狀態

- SQL Server：既有 runtime 已用真實 Schedule 驗證；本次整理不修改 DB。
- Gemini：adapter 原始碼僅供 legacy 使用，正式 Runtime call path 為 0。
- Cerebras：正式 Runtime 使用 `gpt-oss-120b`；provider failure 依用途回 deterministic 或 unresolved flow。
- Voice Gateway：ASR 曾成功；中文 TTS downstream 當時 unavailable，Backend 會回 `tts_failed` 並讓 Android 降級。

## 尚未完成

- 真實 smoke 已確認 Cerebras HTTP 可達；模型輸出仍須通過既有 schema／exact tuple validator。
- 中文 TTS downstream 的穩定部署與再次端到端驗證。
- 持久化／可多 worker 的 case store。
- 每個 Batch 欄位的獨立語音輸入。
- Accessibility 對真實院方 App 的完整自動掛號驗證與維護策略。

## 下一步

1. 由授權人員安全設定 Cerebras Key，執行不記錄 prompt／secret 的 smoke test。
2. 啟動並驗證中文 TTS downstream，再跑 Android 語音端到端流程。
3. 完成 Accessibility 對真實院方 App 的端到端驗證與維護策略。

完整限制與優先級見 [KNOWN_ISSUES](KNOWN_ISSUES.md)。
