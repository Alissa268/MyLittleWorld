# Phase 2 審查報告（Backend only）

> 審查日期：2026-10-27
> 依據：`docs/修改計畫.md` §9 Phase 2，並參照 §2 架構原則、§11 永久禁止事項、§0／§14 最小修改原則與 Architecture Cleanup Gate
> 範圍：只審查 `backend/`。Android（DTO、ViewModel、Chat Screen、S23 實機）不在本次範圍。
> 方式：只讀程式碼。未修改任何 production 檔案，未執行 pytest，未 commit。

## 0. 證據等級與執行環境

- **[讀碼]**：已讀過該函式本體，並以 grep 追查 `backend/app` 內的 caller，確認它位於 production 控制流上。
- **[測試名稱]**：只確認測試檔中有這個案例，未執行，不能當作通過的證據。
- 執行設定：`backend/.env:25` 為 `BATCH_TRIAGE_ENABLED=false`，`backend/app/config.py:29` 的預設值也是 `False`。

## 1. /chat 主控制流（[讀碼]）

`backend/app/routes/chat.py` 的 `chat()`：

1. `batch_mode`（L91-99）需要 `batch_enabled`，目前設定為 False，因此 `conversational_mode = True`（L193）。
2. `apply_user_message`（`rule_engine.py:29-41`）只把訊息寫進 history，不解析內容。
3. `refine_case_with_ai`（L281）：AI Turn Interpreter 解讀本輪內容。
4. `apply_ttas_evaluation`（L299）。
5. 若 `ttas_requires_immediate_action`（L333）成立，走 urgent 終止流程。否則依序執行：
   - `capture_prior_answer_reference`／`capture_pending_answer`（L361-368）
   - `reason_about_departments`（L376）
   - `request_clarification(classify_answer_fields=False)`（L386-395）
   - `advance_conversation`（L396）
6. stage 與 confirmed 由 Backend 在 L474-516 決定。

## 2. 逐條審查表

| # | 要求 | 判定 | 證據（檔案:行號） | 問題說明 |
|---|---|---|---|---|
| 1 | 目標：取消「缺哪個 checklist field 就問固定問題」 | 部分符合 | 主路徑：`routes/chat.py:193,300,326,417`；殘留：`routes/chat.py:296-309,416-435,450-472`、`services/rule_engine.py:74-113`、`services/batch_question_service.py:14-45`、`services/batch_extraction_service.py:25-68` | [讀碼] 主路徑走 AI 規劃器，不會呼叫 `next_question_for`。但固定 checklist 整條路徑仍在，只靠 `BATCH_TRIAGE_ENABLED` 旗標擋住；旗標打開就會回到固定題流程。以目前設定而言是 dead code。 |
| 2 | 目標：依「為何無法區分候選」提出最有價值的澄清問題 | 符合 | `services/conversation_service.py:151-290`（payload 含 `candidate_departments`、`department_status`、`required_next_question_intent`，L162-187）；`services/department_reasoning_service.py:376-378` | [讀碼] 科別收斂產生的 `next_question_intent` 交給規劃器作為 `required_intent`，`_question_rejection` 在 L322 強制 intent 必須一致。AI 實際問句的品質要靠實測確認。 |
| 3 | 新增狀態 `candidate_departments` | 符合 | `schemas.py:331`；寫入 `services/department_reasoning_service.py:330,374`；清除 `routes/chat.py:764` | [讀碼] 只寫入經 `validate_candidate_proposal`（L245-323）驗證的候選：dept_id 必須唯一對應 active SQL Department，支持證據必須來自已取回的官方 KB 證據。 |
| 4 | 新增狀態 `department_status` | 符合 | `schemas.py:330`（Literal 三值）；`services/department_reasoning_service.py:329,343,383-396` | [讀碼] 由 Backend 依驗證後的候選數量與信心值決定。 |
| 5 | 新增狀態 `uncertainty_reason` | 符合 | `schemas.py:324`（`uncertainty_reasons`）、`schemas.py:332`（`department_uncertainty_reason`）；寫入 `services/conversation_service.py:698-707`、`services/department_reasoning_service.py:375` | [讀碼] 名稱改成複數，並另外拆出科別專用欄位，語意與計畫相同。 |
| 6 | 新增狀態 `next_information_needed` | 符合 | `schemas.py:325`；寫入 `services/conversation_service.py:692`；過濾 `services/conversation_service.py:749-761` | [讀碼] 只接受 AI `information_needs` 的第一筆，而且要先通過輸出端 guard。 |
| 7 | 新增狀態 `turn_count` | 符合 | `schemas.py:316`；遞增 `services/conversation_service.py:580`、`routes/chat.py:338`；重設 `routes/chat.py:754` | [讀碼] |
| 8 | 下一題由 AI 生成 | 符合 | `services/conversation_service.py:241-243`；`advance_conversation` L669-693 | [讀碼] 規劃器失敗時改用固定的 `TECHNICAL_CLARIFICATION_REPLY`（L695），屬於 §14 允許的技術錯誤文案。`services/ai_reply_generator.py` 的 `generate_triage_reply`（L130-156）只原樣回傳，不會另外生成第二個問題。 |
| 9 | 驗證：問題不為空、長度合理 | 符合 | `services/conversation_service.py:312-313`（4～180 字） | [讀碼] |
| 10 | 驗證：不包含診斷斷言 | 符合 | `services/conversation_service.py:33,324` | [讀碼] 這個 regex 套在 AI 產生的問句上，不是套在患者文字上，屬於允許的輸出端 guard。 |
| 11 | 驗證：不包含不存在的科別／醫師 | 符合（以更嚴格方式） | `services/conversation_service.py:33`（`科\|醫師\|醫生\|掛號`） | [讀碼] 問句完全禁止出現任何科別或醫師字樣，而不是比對 SQL 名單，結果比要求更嚴格。 |
| 12 | 驗證：intent 符合目前 unresolved information | 部分符合 | `services/conversation_service.py:162-167,316-323` | [讀碼] 只有 `required_intent` 存在時（有 pending intent，或科別收斂提供了 intent）才強制一致。`required_intent=None` 時，只要格式正確、沒問過的 intent 都會被接受，不會比對 `next_information_needed` 或 `uncertainty_reasons`。 |
| 13 | 停止條件：`resolved` | 符合 | `services/department_reasoning_service.py:384-391`；完成閘門 `services/conversation_service.py:627-644`；stage 判定 `routes/chat.py:490-516` | [讀碼] 必須同時滿足：AI 回 resolved、只有 1 個候選、信心值達門檻、安全篩檢完成、沒有 pending intent，才會進入 `WAITING_CONFIRMATION`。 |
| 14 | 停止條件：`ambiguous` | 符合 | `services/department_reasoning_service.py:392-393`；`services/conversation_service.py:308-309`（ambiguous 時拒收 AI 的 sufficient） | [讀碼] |
| 15 | 停止條件：`unresolved`（多輪後仍不足） | 符合 | `services/conversation_service.py:646-649,783-796`；回覆 `routes/chat.py:560-561`；停用 AI `routes/chat.py:185-188` | [讀碼] 進入 unresolved 後不再呼叫 AI，也不會自動選科；回覆引導使用者改用手動選科或洽掛號服務。 |
| 16 | 停止條件：urgent path 交給 TTAS 模組 | 符合 | `routes/chat.py:299,333-347,475-483,555-557` | [讀碼] |
| 17 | hard cap（計畫範例為 8 輪） | 部分符合 | `services/conversation_service.py:18-21,646-648` | [讀碼] 8 只用在「連續 8 輪沒有進展」（`HARD_TURN_CAP`）；每輪都有進展時，總上限是 `ABSOLUTE_TURN_CAP=24`，是範例值的 3 倍。這次變更記錄在 `docs/CHAT_PROGRESS_LIMIT_FIX_20261021.md:9`，但計畫書 §9 未同步修訂。另外 `_begin_revision`（`routes/chat.py:754-755`）會把兩個計數器歸零。 |
| 18 | soft target 3～5 輪 | 不符合 | 在 `backend/app` grep `soft` 沒有結果 | [讀碼] 沒有任何 soft target 機制。 |
| 19 | 超過上限不硬猜，走安全 fallback | 符合 | `services/conversation_service.py:783-796`；`routes/chat.py:560-561` | [讀碼] |
| 20 | 內部欄位是記事本，不是問卷 | 符合（主路徑） | `services/conversation_service.py:205-207,224-227`（prompt 明令不按固定欄位順序）；`services/conversation_service.py:598-619` | [讀碼] 只要有 symptom 加任一 detail 就能進入安全篩檢，不要求補齊 7 欄。 |
| 21 | 多輪 state 由 Backend 控制，AI 不得直接修改 | 符合 | `services/conversation_service.py:297-298`（拒收 workflow key）；`services/rag_triage_adapter.py:190-218`（只套用驗證後的 evidence）；`services/case_store.py:54-80`（client 快照會清除 state）；`services/department_preference_service.py:37-58` | [讀碼] AI 回的 `status=resolved` 必須先通過 Backend 驗證才生效；stage 與 confirmed 只在 `routes/chat.py:474-516` 設定。 |
| 22 | AI 問題可驗證、可追蹤 | 符合 | `schemas.py:320-329`（`reply_source`、`question_rejection_reason`、`asked_clarification_intents`、`pending_clarification_intent`）；`routes/chat.py:577-592`（log） | [讀碼] |
| 23 | 驗收 Case 1：頭暈後追問、收斂 | 控制流符合／AI 行為無法確認 | `services/conversation_service.py:598-625` | [讀碼] 只有 symptom 時不會進入安全篩檢，會先追問。AI 實際問句與收斂結果需要真 Cerebras 實測。[測試名稱] `tests/test_phase2_conversation.py:120,258`；「房子會轉」只出現在 `tests/test_phase7_natural_safety.py:59`。 |
| 24 | 驗收 Case 2：「肚子不舒服」不可直接定科 | 符合 | `routes/chat.py:490-501`；`services/conversation_service.py:619-625` | [讀碼] 安全篩檢未完成時強制 COLLECTING，結構上保證第一輪不可能進入 WAITING_CONFIRMATION。[測試名稱] `tests/test_phase2_conversation.py:324` |
| 25 | 驗收 Case 3：「我想看皮膚科」辨識偏好，並遵守 dept_id 驗證 | 符合 | `services/rag_triage_adapter.py:108,216-218`；`services/department_preference_service.py:37-58,61-91`；`services/department_reasoning_service.py:340-344` | [讀碼] 偏好必須能在原文找到依據、信心值達門檻、在 SQL active Department 中只對應 1 筆且名稱一致。偏好不會跳過安全篩檢。[測試名稱] `tests/test_phase7_natural_safety.py:62` |

## 3. 特別檢查項目

### 3.1 對患者自由文字做 keyword／regex 語意判斷

Phase 2 範圍內未發現。

- [讀碼] `apply_user_message` 不解析內容。`services/conversation_service.py` 的所有 regex（L32-52、L337）都套在 AI 輸出的問句或 `information_needs` 上，不是套在患者文字上。
- [讀碼] `services/batch_extraction_service.py:71-84` 的 `_structured_choice` 是對 UI canonical 選項做精確等值比對，屬於計畫允許的「結構化 UI 選項 rule-only」，不是關鍵字判斷。
- 範圍外觀察（不判定）：`services/department_reasoning_service.py:119-127` 會把官方 KB concept 拿去對患者 `source_text` 做子字串比對。這屬於 Phase 3/4 的 lexical retrieval，不在本次範圍。

### 3.2 AI 直接修改 state

未發現，見表 #21。

### 3.3 固定欄位問卷殘留（[讀碼]，以目前設定都不可達）

- `routes/chat.py:296-309`：`next_question_for`、`missing_checklist_fields`，只在 batch_mode 下執行。
- `routes/chat.py:416-435`：`build_question_batch`，只在 batch_mode 下執行。
- `routes/chat.py:450-472`，以及 `_handle_department_resolution_failure`（L635-686，使用固定的 `DEPARTMENT_CLARIFICATION_TEXT`），只在 batch_mode 下執行。
- `routes/chat.py:411-415`：非 batch 模式下 `semantic_first` 一定是 True，所以這個條件不可能成立。
- `routes/chat.py:538-553`：`last_question_key=="symptom"` 的偏好前綴。對話模式中 `advance_conversation` 會把這個 key 清掉（`conversation_service.py:708`），所以只有 batch 模式到得了。
- `services/rule_engine.py:74-171` 的 checklist 函式群，以及 `services/batch_question_service.py`、`services/batch_extraction_service.py` 兩個檔案的全部內容。
- `ConversationState` 的 `asked_fields`、`consumed_fields`、`question_attempts`、`field_statuses`、`last_question_key`（`schemas.py:309-315`）屬於 checklist 時期的欄位，仍在 `/chat` 回應中；刪除就是修改 API schema。
- **不可刪除**：`services/question_specs.py` 與 `services/clarification_engine.py` 的 `CLARIFICATION_PROMPTS` 仍被 `services/fixed_sentences.py:3-4` 引用，再由 `routes/voice.py:26` 用於 TTS。初始提示（`routes/chat.py:406`）與安全篩檢文案（`services/conversation_service.py:732`）也仍在使用，屬於 §14 允許的固定系統文案。

### 3.4 Dead code（[讀碼]，grep 確認 `backend/app` 中沒有 production caller）

- `services/conversation_service.py:92-148` 的 `classify_pending_answer`：只有 tests 呼叫。
- `services/department_preference_service.py:25-34` 的 `accept_department_preference`：沒有任何 `.py` caller。
- `services/rule_engine.py:44-71` 的 `evaluate_urgency`：只有 tests 呼叫。
- `services/rag_triage_adapter.py:355-388` 的 `merge_ai_next_question`：唯一 caller 在 batch 分支（`routes/chat.py:326`）。而且 `refine_case_with_ai` 永遠回傳 `triage=None`（L221），所以這個函式一定回傳 False。
- `services/conversation_service.py` 中 `request_clarification(classify_answer_fields=True)` 的分支（L190-196、L256-261），以及 `advance_conversation` 的 grounded_answer 分支（L585-591）：production 只傳 `False`，`answered_intent` 一定是 None。
- `services/ai_reply_generator.py:7,12`：註明為「Legacy test seam」的 `get_settings`／`complete_prompt`。

## 4. 需要修復的項目

| # | 項目 | 預估 production 檔案數 | 對照最小修改原則 |
|---|---|---|---|
| F1 | 移除 batch／checklist 固定問卷路徑（§3.3） | 6 個：`routes/chat.py`、`services/batch_question_service.py`、`services/batch_extraction_service.py`、`services/rule_engine.py`、`services/rag_triage_adapter.py`、`config.py` | ⚠ 超過 3 個檔案。只有在認定它屬於 §14 的 legacy／compatibility path 時，才適用 Cleanup Gate 的檔數例外；但它仍有一個靠設定開關的 caller，不完全符合「已無 production caller」。⚠ 若同時刪除 `ChatRequest.answers`、`legacy_batch`、`question_batch` 或 `ConversationState` 的舊欄位，就是**修改 API schema**，違反 §8.8 與 Phase 6 的契約保守原則，必須先取得使用者同意。`question_specs.py` 與 `clarification_engine.py` 必須保留（voice 仍在使用）。 |
| F2 | 刪除 §3.4 列出的 dead code | 5 個：`services/conversation_service.py`、`services/department_preference_service.py`、`services/rule_engine.py`、`services/rag_triage_adapter.py`、`services/ai_reply_generator.py`（若與 F1 合併處理，部分檔案重疊） | ⚠ 超過 3 個檔案，但屬於 §14 明列的「無 production caller 的 dead path」，適用 Cleanup Gate 例外。必須同步清理相關測試，例如 `tests/test_phase4_pending_answer_classification.py`，以及 `tests/test_backend_flow.py`、`tests/test_phase42_backend_hardening.py`、`tests/test_phase5_ttas_evidence.py` 中的 `evaluate_urgency` 用例。不涉及 schema 變更，也不新增 service。 |
| F3 | hard cap 為 24 輪，與計畫範例 8 輪不一致；且沒有 soft target（表 #17、#18） | 1 個：`services/conversation_service.py` | 既不是已證實的 regression，也不是 legacy path，**不在 Cleanup Gate 允許範圍內**。這是 state machine 參數，需要使用者先決定：改回 8，或在計畫書追認 24。新增 soft target 等於新增狀態機行為，同樣須先取得同意。 |
| F4 | `required_intent=None` 時，intent 不會比對 unresolved information（表 #12） | 1 個：`services/conversation_service.py` | 沒有實測證據顯示它造成 bug；依最小修改原則先列為待辦，不應主動修改。 |

## 5. 未能確認的項目與限制

- 沒有執行任何 pytest 或 310 實測。標記 [測試名稱] 的項目，只代表測試檔中有這個案例。
- AI 實際產生的問句品質，以及 Case 1 的收斂結果，需要真 Cerebras 實測才能判定。
- Phase 2 GitHub Gate 的「Android 是否仍偷偷一次顯示 batch 7 題」與 S23 實機項目屬於 Android 範圍，本次不審查。
