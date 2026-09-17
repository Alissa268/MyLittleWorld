# 貢獻指南

本文件供人類組員協作使用。開始修改前，先讀 [README](README.md) 與 [PROJECT_STATUS](docs/PROJECT_STATUS.md)。

## Git 工作方式

- 不直接在 `main`、`master` 或別人的進行中分支上開發。
- 修改前先同步 remote，確認 `git status`、目前 branch 與 base commit。
- 一人一個 branch；可使用 `feature/<topic>`、`fix/<topic>` 或課程約定的 `codexNNNN`。
- 不要用 force push 覆蓋別人的歷史，也不要為了清理而 `reset --hard`。
- 避免多人同時修改同一檔案；特別是 `HomeScreen.kt`、`NavGraph.kt`、`ChatScreen.kt`，很容易產生衝突。

## Commit 與 PR

- 一個 commit 對應一個清楚目的，訊息可採 `feat:`、`fix:`、`test:`、`docs:`、`chore:`。
- PR 說明應包含變更目的、受影響流程、測試結果、環境或 migration 注意事項。
- merge 前重新同步目標 branch，逐一解決衝突；不要用整檔覆蓋方式解衝突。

## 必跑檢查

依修改範圍至少執行：

```powershell
cd backend
pytest -q

cd ..\android
.\gradlew.bat testDebugUnitTest --no-daemon
.\gradlew.bat assembleDebug --no-daemon
```

若修改 API contract、navigation、visit type、voice 或 recommendation，還要依 [TESTING](docs/TESTING.md) 做對應 runtime smoke。

## Secrets 與資料

- 禁止 commit `.env`、`local.properties`、API key、DB password、token、患者真實資料或私人 log。
- 新增設定時只更新安全的 example，值保持空白或明確 placeholder。
- 禁止 `git add -f` 強加 ignored 檔案。
- PR 前檢查 staged diff，確認沒有 build、APK、cache、IDE 或暫存檔。

## 核心規則

- `backend/` 與 `android/` 才是正式 runtime。
- `project-smart-guidance-system-main/` 僅供參考，不能直接覆蓋正式 Backend。
- 不建立假醫師，不繞過 SQL `visit_type`、red flag 或 confirmation gate。
- DB schema／資料修改必須另行取得明確授權。
