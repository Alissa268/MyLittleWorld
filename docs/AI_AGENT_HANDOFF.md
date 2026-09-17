# GPT／Codex 專案交接

> ## 【唯一正式主專案】
>
> `C:\Users\TKU\Downloads\test02-feature-frontend-backend`
>
> ## 【正式 Backend】
>
> `C:\Users\TKU\Downloads\test02-feature-frontend-backend\backend`
>
> ## 【正式 Android】
>
> `C:\Users\TKU\Downloads\test02-feature-frontend-backend\android`
>
## 不可違反的邊界

- 不要從舊 clone 或 Downloads 內其他同名資料夾執行 Backend。
- 不要把外部 prototype source 直接 merge 成正式 state machine。
- 不要自行修改 SQL schema 或資料；read-only audit 僅允許 `SELECT`。
- 不要 commit `.env`、`local.properties`、API key、DB password、token、患者資料或 log。
- 不要 hardcode secret，也不要輸出 secret 的片段、長度或遮罩後可識別資訊。
- 不要建立 fake doctors 或在無班表時回退到不符條件的 rows。
- 不要用 AI output 控制 stage、completion、confirmation 或 red flag safety。
- 不要 push，除非使用者在當次任務明確授權，且只 push 指定 branch。

## 必知架構

### Visit type

Canonical values 包含三種目前流程與一個相容值：

- `initial → SQL 初診`
- `followup → SQL 複診`
- `quick_search → SQL 複診`
- `return_visit → SQL 複診`（compatibility）

正式第三入口是 QuickSearch：Android 透過 `/reference/departments`、`/schedules/search` 與 `/generate_script` 完成科別、班表與導引流程，不進入症狀問診。`Route.RETURN_VISIT` 保留為導向 `QuickSearchScreen` 的 compatibility alias，Backend 也保留 `/followup/recommend` 與 `return_visit` contract。所有 Schedule query/recommendation 必須 strict filter。

### Batch triage

`rule_engine.py`／checklist 是流程與安全 source of truth。`batch_question_service.py` 只分組缺漏問題；`batch_extraction_service.py` 先 deterministic parse，模糊非 red-flag 欄位才可能呼叫一次 provider。舊 single-message path 由 `BATCH_TRIAGE_ENABLED=false` 保留。

### Red flag

`red_flags_checked` 必須由 deterministic user-answer path 設定。AI、空 start、重複 request 或 Android UI 不得代填。推薦前還必須通過 completion 與 confirmation gate。

### project-smart

正式 Backend 內已存在移植後、受測試保護的 concept adapters；repository 不再包含獨立的 project-smart reference 資料夾。若要再參考外部 prototype，先比較 schema、state ownership、visit type 與 safety，不能整檔複製。

## 修改前後必跑

修改前：

```powershell
git status
git branch --show-current
git log --oneline -10
```

修改 Backend／contract／Android integration 後：

```powershell
cd backend
pytest -q

cd ..\android
.\gradlew.bat testDebugUnitTest --no-daemon
.\gradlew.bat assembleDebug --no-daemon
```

涉及 external service 時，再依 [TESTING](TESTING.md) 執行 runtime smoke，並分開標示 unit、build、runtime 與 provider 實測狀態。

## Git workflow

1. 保留使用者工作樹，不 reset／checkout 丟棄未知變更。
2. 在指定 branch 工作；若 branch 已存在，先檢查來源，不 force delete。
3. 只 stage 本次安全且應納管的 source/docs/tests。
4. staged diff 做 secret、build、APK、cache、log 審查。
5. commit message 清楚描述目的。
6. 只有使用者明確要求時才 push，且 push 前所有要求的 tests 必須通過。

## AI 接手任務前 checklist

1. 執行 `git status`、確認 branch 與 recent history。
2. 讀 [README](../README.md)。
3. 讀 [PROJECT_STATUS](PROJECT_STATUS.md)。
4. 讀 [ARCHITECTURE](ARCHITECTURE.md)。
5. 依任務讀 [BACKEND](BACKEND.md)、[ANDROID](ANDROID.md) 或 [AI_TRIAGE](AI_TRIAGE.md) 與相關 source/test。
6. 明確區分已驗證、推論與未驗證，不從舊報告猜測。
7. 做最小範圍修改，不重構無關 production code。
8. 執行對應 unit tests、build 與必要 runtime smoke。
9. 以遮罩方式做 secret scan，審查 `git diff`／staged files。
10. 回報結果；除非使用者明確要求，否則不 push。

## 目前三個優先缺口

1. Cerebras 真實 API smoke 尚未完成。
2. 中文 TTS downstream 尚需部署／再驗證。
3. Accessibility 對真實院方 App 的完整端到端驗證尚未完成。
