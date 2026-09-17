# AI 問診與 Batch Triage 設計

## 最重要原則

**AI 不等於整個問診 state machine。**

正式流程與安全性的 source of truth 是 `backend/app/services/rule_engine.py` 的 deterministic checklist。AI 是受限制、可失敗、可關閉的輔助元件；即使 provider 沒有 Key、quota 不足、timeout 或回傳格式錯誤，核心流程也必須維持可預測。

## 正式 state ownership

只有 Backend deterministic logic 可以決定：

- `conversation_state.stage`
- `is_complete`
- `awaiting_confirmation`
- `confirmed`
- `red_flags_checked`
- recommendation gate
- 下一步是否允許 `/recommend`

AI output 不得直接控制以上欄位。Android 只能顯示 Backend state，不得自行宣告完成。

## Checklist 與 Batch

問診 checklist 依序涵蓋：

1. `symptom`
2. `red_flags`
3. `body_part`
4. `duration`
5. `severity`
6. `preferred_days`
7. `preferred_sessions`

`batch_question_service` 是 deterministic builder：它讀取目前缺漏欄位、套用既有 wording／attempt 規則，最多組成六題。Android 回傳的是 `[{key, answer}]`，不依 UI index 綁定。

## Batch extraction

`batch_extraction_service` 的順序：

1. 每個 keyed answer 先透過現有 normalizer／rule engine deterministic parse。
2. 已可靠解析的欄位立即套用。
3. 只有仍模糊的欄位可交給 `BATCH_EXTRACTION_PROVIDER`。
4. 一批答案最多進行一次 AI extraction call。
5. AI JSON 經 Pydantic schema、allowed field/status/value 驗證後才套用。
6. `red_flags` completion 永遠是 deterministic-only。

AI extraction 失敗時保留 unresolved fields，Backend 會再問或依 attempt 規則處理，不可假裝完成。

## Red flag safety gate

危險徵兆是 deterministic safety gate。使用者必須實際回答 red flag 問題；空 batch、重複 start 或 AI JSON 都不能自動設定 `red_flags_checked`。偵測到高風險內容時，Backend 產生 warning 與 urgency reasons，Android 顯示 urgent warning card。

## Runtime provider

正式 abstraction 位於 `ai_service.py`。Runtime 固定使用 Cerebras
`gpt-oss-120b`；Gemini adapter 僅保留為 legacy code，不是 production route 的
primary 或 fallback，也不需要 `GOOGLE_API_KEY`。

呼叫點各自有 gate：

- Batch ambiguous extraction
- semantic refinement
- department refinement
- specialty scoring
- doctor shortlist batch scoring

Checklist、red-flag clarification、department unresolved 與狀態提示皆使用受控文案，
不呼叫 LLM。

provider abstraction 不代表每個呼叫點都一定使用 AI。deterministic parsing、keyword/rule fallback 與 schema validation 必須保留。

## Cerebras 驗證狀態

Cerebras adapter、async client、timeout 與 model 設定已存在，也有 mock/unit coverage；**真實 Cerebras API runtime 尚未正式驗證**，因正式環境尚未取得／設定可用 `CEREBRAS_API_KEY`。禁止把此項寫成已成功，也禁止從 reference source 複製或硬編碼 Key。

## 為何從單題改為 Batch

單題模式每回答一欄都可能產生一次網路／AI round trip，容易增加延遲、API calls 與 quota 壓力。Batch 讓使用者一次看到相關問題、一次送出 keyed answers，deterministic parser 可先處理多數欄位，僅在必要時呼叫一次 AI；同時保留 feature flag 關閉時的舊 single-message fallback。

## 修改守則

- 新欄位先加入 schema、rule engine、question builder 與 deterministic parser，再考慮 AI。
- prompt 不可要求 AI 修改 safety/state 欄位。
- provider 回應必須結構驗證；不信任自由文字 JSON。
- 測試至少涵蓋無 Key、provider error、格式錯誤、red flag、409、confirmation 與 fallback。
- 不把患者原文、secret 或完整 provider response 寫入公開文件。

相關程式導覽見 [BACKEND](BACKEND.md)，完整架構見 [ARCHITECTURE](ARCHITECTURE.md)。
