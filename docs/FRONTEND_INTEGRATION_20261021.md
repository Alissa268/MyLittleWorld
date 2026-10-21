# HelloProject 前端整合

來源：[melody650123/HelloProject](https://github.com/melody650123/HelloProject/tree/e035e4920ef9dcda92cda7ce370293304c7ef07b)，預設分支 `mockdemo`，固定比對 commit `e035e4920ef9dcda92cda7ce370293304c7ef07b`。

## 已整合

- 醫師頁：未選日期時依後端原排序每位醫師顯示一個診次；選日期後顯示當日全部診次。原始推薦資料與 recommendation ID 保留。
- 醫師卡片：移除日期下方重複說明，詳情仍可查看；增加朗讀／停止按鈕及語音錯誤提示。
- 朗讀內容由目前 API 的醫師、科別、日期與時段產生，透過既有 `/voice/tts` 播放。切換語言、離開畫面、選醫師或開啟詳情時停止播放。
- 聊天頁與醫師頁共用國語／台語偏好，使用 SharedPreferences 保存。
- 確認頁：日期、星期、診別各段以 U+2060 避免文字從段內斷行；依後續要求移除額外的 sessionTime 時間範圍顯示（例如 13:30-17:00）。

## 保留與排除

- `backend/`、network、MedicalRepository、所有 ViewModel、navigation 及現有問診判斷均未更動，已以整合前 SHA-256 逐檔核對。
- 保留初診／複診／快速查詢、危險徵兆、確認 gate、班表重新驗證、歷史續接、重試與錯誤處理。
- 不移入 MockDemoScript、固定聊天劇本、固定醫師資料、mock API、mockdemo 預錄音檔、demo 狀態或來源測試中的 demo fixture。
- 不移入來源的前端症狀推論、分數轉星級與匹配說明改寫；沿用目前專案正式專長與班表事實的呈現。既有 Preview 範例保留，未增加來源的排班快照。
- 原本國語與台語的 ASR 路由保持現狀。
- 本工作區沒有 `.git`；整合直接寫入本機檔案，未建立 commit 或推送。三個修改檔的原版備份與差異保存在 `.integration/original/`、`.integration/frontend-integration.patch`。

## 驗證

以 Android Studio 的 JBR 執行：

```powershell
$env:JAVA_HOME = 'C:\Program Files\Android\Android Studio\jbr'
.\android\gradlew.bat -p android testDebugUnitTest assembleDebug --no-daemon --offline
```

既有單元測試、醫師去重／日期篩選／動態朗讀／播放停止與錯誤處理的新增回歸測試、確認時間換行測試及 APK 建置皆通過。

APK：`android/app/build/outputs/apk/debug/app-debug.apk`。此輪未安裝 APK 或進行完整實機 UI 流程；真實語音播放仍取決於既有 Voice Gateway 可用性。
