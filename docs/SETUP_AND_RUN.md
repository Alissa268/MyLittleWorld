# 安裝與啟動

以下以 Windows PowerShell 與 Android Emulator 為主。所有 secret 只放本機 `backend/.env`；不要貼入 issue、log、Markdown 或 commit。

## A. Windows／310 Backend

### 建立 venv 與安裝 dependency

在專案根目錄：

```powershell
.\scripts\setup.ps1
```

等價手動方式：

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
```

### 啟動 FastAPI

為了與 Android 預設 URL 對齊，建議使用 8080：

```powershell
.\scripts\run-backend.ps1 -Port 8080 -HostName 127.0.0.1
```

或：

```powershell
cd backend
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8080
```

檢查 health：

```powershell
Invoke-RestMethod http://127.0.0.1:8080/health
```

`scripts/run-backend.ps1` 的程式預設 port 是 8000；若不傳 `-Port 8080`，就必須同步調整 Android `API_BASE_URL`，且避免與 Voice Gateway 撞 port。

## B. SQL Server

需要 ODBC Driver 17 for SQL Server（或以 `DB_DRIVER` 指定已安裝版本）。Backend 會讀取下列設定：

| 環境變數 | 用途 |
|---|---|
| `DB_DRIVER` | ODBC driver 名稱 |
| `DB_SERVER` | SQL Server host |
| `DB_NAME` | database 名稱 |
| `DB_USER` |登入帳號 |
| `DB_PASSWORD` |登入密碼 |
| `DB_TRUST_SERVER_CERTIFICATE` |本機／測試 TLS 選項 |

班表查詢依賴 `DepartmentCategory`、`Department`、`Doctor`、`Schedule`。一般開發與驗證不得自行 INSERT／UPDATE／DELETE 或修改 schema。

## C. Android Emulator

在 `android/local.properties` 設定本機專用 URL：

```properties
API_BASE_URL=http://10.0.2.2:8080
```

`10.0.2.2` 代表 Android Emulator 連回 Windows host。實機需改成電腦在同網路可達的 IP，並確認 firewall／binding；不要 commit `local.properties`。

Build：

```powershell
cd android
.\gradlew.bat assembleDebug --no-daemon
```

APK 會出現在 ignored 的 `app/build/outputs/apk/debug/`，不得加入 Git。

## D. Voice

Voice Gateway 是獨立外部服務，Backend 只做轉接。相關環境變數：

| 環境變數 | 用途 |
|---|---|
| `VOICE_ENABLED` | 是否開啟 voice routes 的 gateway 功能 |
| `VOICE_GATEWAY_URL` | Gateway base URL；程式預設 `http://localhost:8000` |
| `VOICE_GATEWAY_KEY` | Gateway authentication；只放 `.env` |
| `VOICE_GATEWAY_IS_NGROK` | 是否加入 ngrok bypass header |
| `VOICE_DEFAULT_LANG` | `taiwanese` 或 `chinese` |
| `VOICE_TIMEOUT` | Gateway timeout 秒數 |
| `VOICE_USE_FIXED_CACHE` | 是否使用固定語音 cache id |

檢查：

```powershell
Invoke-RestMethod http://127.0.0.1:8080/voice/health
```

Gateway health 正常不代表其中文 TTS downstream 一定已啟動；要分別 smoke test ASR 與 TTS。TTS failure 應安全降級，不應使 `/voice/chat` 整體 crash。

## E. AI provider 與 Batch

| 環境變數 | 用途 |
|---|---|
| `AI_PROVIDER` | 正式 Runtime 固定為 `cerebras` |
| `CEREBRAS_API_KEY` | Cerebras credential |
| `CEREBRAS_MODEL` | Cerebras model，固定 `gpt-oss-120b` |
| `BATCH_TRIAGE_ENABLED` | 開啟 Batch triage；false 保留 single-message path |
| `BATCH_EXTRACTION_PROVIDER` | 相容設定；正式 Runtime 仍固定使用 Cerebras |
| `AI_TIMEOUT_SECONDS` | provider timeout |
| `AI_REPLY_GENERATION_ENABLED` | legacy 相容設定；狀態與問句使用受控文案 |
| `AI_REPLY_TIMEOUT_SECONDS` | reply generation timeout |
| `EMBEDDING_MODEL` | full AI stack embedding model |
| `DEPLOY_MODE` | local／deployment mode |
| `DISABLE_LOCAL_EMBEDDING` | 關閉本機 embedding |

可從 root `.env.example` 複製名稱，自行在 `backend/.env` 填入授權值。`.env` 已由 `.gitignore` 排除，仍應在 commit 前再次確認。

沒有 provider Key 時，deterministic triage 仍應可運作；Cerebras 未完成真實 smoke 前，不要把 adapter 存在誤寫成服務已驗證。

測試指令與 runtime checklist 見 [TESTING](TESTING.md)。
