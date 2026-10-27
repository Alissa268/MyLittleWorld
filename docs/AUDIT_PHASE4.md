# Phase 4 審查報告（Backend only）

> 審查日期：2026-10-27
> 依據：`docs/修改計畫.md` §9 Phase 4（L946-1024），並參照 §2 架構原則（L112-190）、§11 永久禁止事項（L1464-1485）、§0／§14 最小修改原則與 Architecture Cleanup Gate（L26-28、L1619-1640）
> 範圍：`backend/app` 中候選科別收斂與驗證：`services/department_reasoning_service.py`（`validate_candidate_proposal`、`reason_about_departments`）、AI 輸出驗證、候選狀態跨輪保存，以及 `services/appointment_service.py` 的科別判斷殘留。Android 不在範圍內。
> 不重審：KB 載入、source provenance、`retrieve_official_evidence` 的 lexical 檢索風險（見 `docs/AUDIT_PHASE3.md` §3.2、§3.6），需要時只引用其結論。
> 方式：只讀。未修改 production 檔案、測試檔或資料檔，未執行 pytest，未 commit，未連 310。

## 0. 證據等級與執行環境
- **[讀碼]**：已讀函式本體，並 grep `backend/app` 確認 caller 在 production 控制流上。
- **[測試名稱]**：只確認測試檔有此案例，未執行，**不能當作通過證據**。
- **無法確認**：讀不到或需要實測才能判定，不推測。
- 目前設定值（[讀碼]，`backend/.env` 只擷取非機密鍵；`backend/app/config.py`）：
  - `AI_TIMEOUT_SECONDS=60`（`.env`；預設 8.0，`config.py:34`）
  - `CEREBRAS_MODEL=gpt-oss-120b`；`AI_PROVIDER` 未在 `.env` 設定 → 預設 `cerebras`（`config.py:26`）。runtime JSON 呼叫寫死 `RUNTIME_AI_PROVIDER="cerebras"`、`RUNTIME_AI_MODEL="gpt-oss-120b"`（`services/ai_service.py:9-10`）
  - `BATCH_TRIAGE_ENABLED=false`（因此 `routes/chat.py:450-472` 的 legacy batch 選科區塊在目前設定下不會進入）
  - `DEPLOY_MODE=local`、`AI_DOCTOR_SCORING_ENABLED=true`
  - `ACCEPT_THRESHOLD=0.55`（`services/confidence_scoring.py:4`）；`HARD_TURN_CAP=8`（無進展輪數）、`ABSOLUTE_TURN_CAP=24`、`SAFETY_ANSWER_CAP=3`（`services/conversation_service.py:18-22`）
- voice 路徑：`routes/voice.py` 轉呼叫 `routes.chat.chat`（Phase 3 報告 §0 已確認），因此本報告所述的 `/chat` 控制流同樣適用於 voice。

## 1. 主控制流摘要（[讀碼]）
`POST /chat`（`routes/chat.py:73-611`），自然對話模式（`conversational_mode = not batch_mode`，L193）：
1. `_resolve_case`（L701-721）：先用 server 端已保存案件；只有沒有已保存案件時，才採用 client 快照，並經過 `sanitize_untrusted_snapshot` 清理（`case_store.py:54-80`）。
2. `apply_user_message` 只把原文記入 history，不做任何語意解析（`rule_engine.py:29-41`）。
3. `refine_case_with_ai`（Turn Interpreter，`rag_triage_adapter.py:63-230`）：輸出 semantic_extractions（含 `assertion`）、ttas_evidence、`department_preference` 等，並由 Backend 驗證；provider 失敗時回 `None`（L85-91），repair 失敗時回 `interpretation_complete=False`（L153-155、L175-177）。
4. `apply_ttas_evaluation`（L299）→ 若 TTAS 判定需立即處置，進入 urgent terminal 狀態，不進入科別判斷（L333-347）。
5. `department_reasoning_allowed`＝本輪有輸入、Turn Interpreter 完整，而且（不是安全篩檢回合，或本輪剛完成安全篩檢）（L369-373）→ `reason_about_departments`（L376）。
6. `reason_about_departments`（`department_reasoning_service.py:326-398`）：
   - L329-334：每次都先清空 status／candidates／uncertainty／intent／`department_result`（**每輪從頭重算，不累加**）。
   - L336-339：讀不到 SQL Department → `unresolved`。
   - L340-344：使用者指定科別 → `resolve_requested_department`（不經 KB 與 AI）→ `resolved`。
   - L345-352：載入 KB → lexical 檢索 → 沒有結果才走語意檢索（Phase 3 範圍）→ 沒有證據就 `unresolved`。
   - L353-370：收斂 AI（`purpose="department_convergence"`）。
   - L371-373：`validate_candidate_proposal`（L245-323）。
   - L382-393：只有在「AI 回 `resolved`、驗證後恰好 1 個候選、confidence ≥ 0.55」時才判 `resolved`，否則判 `ambiguous`／`unresolved`。
   - L394-398：任何例外 → `unresolved`，清空候選。
7. `request_clarification`（`conversation_service.py:151-290`）：把 `department_status`／`candidate_departments` 當成 planner 的輸入；AI 回 `sufficient` 但科別仍為 ambiguous 時會被拒絕（L308-309）。
8. `advance_conversation`（`conversation_service.py:560-713`）：要完成對話，必須安全篩檢已解決**且** `department_status=="resolved"`（L633）；未 resolved 時回 `DEPARTMENT_EVIDENCE_REPLY`（L651-662）；達上限時 `_unresolved`（L646-648、L783-796），不會自動選科。
9. stage gate（`routes/chat.py:490-516`）：need_more_info、安全篩檢未完成，或科別未 resolved，一律回到 COLLECTING；只有 `req.confirmed` 才會進入 RECOMMENDING。
10. `/recommend`（`routes/recommend.py:91-116`）與 `recommend_appointments`（`appointment_service.py:79-94`）：都要求已完成、已確認、科別已驗證；沒有科別就 raise，不猜科。

## 2. 逐條審查表

| # | 要求 | 判定 | 證據（檔案:行號） | 說明 |
|---|---|---|---|---|
| 1 | 每輪更新 Top-K 候選＋支持證據＋不足證據＋目前最重要的歧義 | 部分符合 | `department_reasoning_service.py:317-323,329-334,374-381`；`schemas.py:288-299,330-334` | [讀碼] 每輪重算（L329-334）；Top-K≤3（L317-318）；每個候選都帶 `supporting_evidence`（L305-309）；「最重要歧義」以 `next_question_intent`（L321-322,376-378）＋自由文字 `uncertainty_reason`（L319-320）表示。**「不足證據」沒有結構化欄位**：`CandidateDepartment` 只有 dept_id／名稱／confidence／supporting_evidence（`schemas.py:294-299`）；`department_next_information_needed` 永遠設為 None（L381，註解 L379-380 說明契約目前未提供）。 |
| 2 | AI 輸入只限 conversation summary、accepted semantic evidence、官方 KB retrieval、SQL active departments | 部分符合 | `department_reasoning_service.py:353-357,175,223` | [讀碼] 收斂 AI 的 payload＝`accepted_semantic_evidence`、`retrieved_official_evidence`、`previously_asked_intents`。沒有送原始 history，也沒有送 clarification_evidence（[測試名稱] `test_phase4_department_convergence.py:193`）。與規格的差異：①沒有 conversation summary（結構化 evidence 本身含有逐字 `source_text`）；②SQL active departments 不會整份送出，只以 retrieved evidence 內「已對應 live DB」的 dept_id／parent／child 出現（L133-143）；③多了 `previously_asked_intents`（workflow metadata，不在四項清單內）。 |
| 3 | AI 輸出只能含既有 dept_id、confidence、supporting evidence、source ids、uncertainty、next_question_intent | 符合 | `department_reasoning_service.py:251-253,266-322` | [讀碼] Backend 只讀取 `status`、`candidates[].dept_id/confidence/supporting_evidence[].patient_source_text/knowledge_source_id/knowledge_concept`、`uncertainty_reason`、`next_question_intent`，其他 key 一律忽略。`status` 只是提案，最終狀態由 L382-393 決定。parent／child 取自 retrieved 的 live 資料（L313-314），不採用 AI 給的值。intent 必須符合 snake_case regex（L21,322）；uncertainty 截斷為 240 字（L320）。[測試名稱] `test_phase4_department_convergence.py:159`（`stage`／`is_complete` 被忽略）。 |
| 4 | dept_id 必須存在 active Department | 符合（程式面） | `department_reasoning_service.py:259-263,273-274`；`db.py:70-97` | [讀碼] dept_id 必須是 int > 0，且在 `fetch_active_departments()` 結果中恰好出現 1 次。注意：`fetch_active_departments` 的 SQL 沒有任何 active 條件（`db.py:75-83`，Department JOIN DepartmentCategory 全取）；Doctor 有 `is_active=1` 條件（`db.py:162` 等），Department 沒有。Department 表是否有停用欄位，**無法確認**（見 §5）。 |
| 5 | source_id 必須存在於 KB | 符合 | `department_reasoning_service.py:254,293,298` | [讀碼] 必須在 `sources` registry 中，而且 (dept_id, source_text, source_id, concept) 必須完全等於某筆已取回的證據。[測試名稱] `test_phase4_department_convergence.py:120`。 |
| 6 | patient source_text 必須存在於對話 | 符合 | `department_reasoning_service.py:290-292,372`；上游 `:46-47` | [讀碼] 必須是非空、已去頭尾空白的字串，且為某則 user 訊息的子字串；另需命中 retrieved key，而 retrieved 本身只來自已驗證 evidence（L47）。[測試名稱] `test_phase4_department_convergence.py:125`。 |
| 7 | 不可生成新科別 | 符合 | `department_reasoning_service.py:273-274,298-303,313-314` | [讀碼] 同 #4、#5；同一候選所有 support 的 live parent／child 必須一致（L299-301），任何一筆 support 不合格，整個候選作廢（L296-303）。[測試名稱] `:114`。 |
| 8 | unresolved 時不得強制 resolve | 符合 | `department_reasoning_service.py:382-393,394-398`；`conversation_service.py:308-309,633,646-662,783-796`；`routes/chat.py:490-501` | [讀碼] 見 §1 第 6～9 步；所有失敗分支都不會選科。觀察：AI 回 `resolved` 但列了多個候選、其中只有 1 個通過驗證時，L384 會判 `resolved`（列入待辦 R5）。 |
| 9 | appointment_service 舊弱規則不再作為主要判斷 | 符合 | `appointment_service.py:79-94`；grep 結果見 §3.A／§3.H | [讀碼] 檔內已無「膝／關節→骨科、胸痛／心悸→心臟內科」等規則（grep `骨科\|心臟\|膝\|關節\|胸痛\|心悸` 於本檔無結果）。沒有科別時 raise「尚無已驗證的正式科別結果；不執行舊版科別猜測」（L88-90）。`resolve_department_result`（L41-72）只做 DB 唯一列比對。`detect_department_result` 已不存在（grep 無結果；[測試名稱] `test_phase4_department_convergence.py:1021` 斷言此屬性不存在）。 |
| 10 | 驗收：模糊症狀不會過早 resolve | 部分符合 | Backend 閘門：`department_reasoning_service.py:384`；`conversation_service.py:598-619` | [讀碼] Backend 只擋「AI 沒說 resolved」「多候選」「confidence<0.55」三種情況；**沒有**檢查證據是否足以區分候選（例如「頭暈」在 KB 對應 7 科，見 AUDIT_PHASE3 §3.6）。若 AI 對模糊症狀回單一候選且 confidence≥0.55，就會 resolved；之後仍須經過安全篩檢與使用者確認。實際 AI 行為無法確認。[測試名稱] `test_phase2_conversation.py:322`、`test_phase4_department_convergence.py:159,184`。 |
| 11 | 驗收：新資訊加入後候選能收斂 | 部分符合 | `department_reasoning_service.py:87-97,329-334` | [讀碼] 每輪以最新的有效 evidence 從頭重算，具備收斂機制。實際能否收斂取決於 AI，無法確認。[測試名稱] `test_phase4_department_convergence.py:327`（mock provider）。 |
| 12 | 驗收：否定句不會誤判 | 部分符合 | 見 §3.D | [讀碼] 否定由 AI 的 `assertion` 判定，Backend 只把 present 的證據送進檢索。已知缺口沿用 AUDIT_PHASE3 §3.6：present 證據的 `source_text` 若夾帶否定子句，lexical 子字串檢索仍可能取到被否定的 concept。 |
| 13 | 驗收：同義句／口語句表現合理 | 無法確認 | `department_reasoning_service.py:147-242` | [讀碼] 同義句靠 Turn Interpreter 的 `normalized_value` 與語意檢索 AI 處理，Backend 不使用同義詞字典。「表現合理」需要真 Cerebras 實測。[測試名稱] `test_department_evidence_negation.py:136,273`、`test_semantic_department_retrieval.py:51`（皆為 mock）。 |
| 14 | 驗收：API 失敗不會亂選第一個科別 | 符合 | 見 §3.E | [讀碼] 所有 AI／DB／KB 失敗分支最終都是 `unresolved` 且候選為空；唯一用到 `candidates[0]` 的地方，前提是 `len(candidates)==1`（L384-385）。[測試名稱] `test_phase4_department_convergence.py:352`、`test_semantic_department_retrieval.py:88`。 |

**統計：符合 8（#3,4,5,6,7,8,9,14）／部分符合 5（#1,2,10,11,12）／不符合 0／無法確認 1（#13）。**

## 3. 特別檢查項目

### A. production 中對「患者自由文字」做 keyword／regex／phrase-list 判斷的路徑（[讀碼]）
grep `re\.(compile|search|match|fullmatch|findall|sub|split)\(` 於 `backend/app`，全部列出：

| 位置 | 作用對象 | 是否判斷患者語意 |
|---|---|---|
| `conversation_service.py:32` `_INTENT_PATTERN` | AI 輸出的 intent 字串 | 否（格式檢查） |
| `conversation_service.py:33` `_UNSAFE_QUESTION`（含「科」「醫師」「掛號」等） | AI 產生的追問（L324）與 information_need（L756） | 否（AI 輸出品質 guard） |
| `conversation_service.py:34` `_DURATION_QUESTION` | AI 產生的追問（L740） | 否 |
| `conversation_service.py:35-39` `_MULTI_QUESTION`、`_MULTI_DIMENSION_QUESTION` | AI 追問（L326） | 否 |
| `conversation_service.py:40-44` `_INTERNAL_STATE_TEXT` | AI 追問／information_need（L324,757） | 否 |
| `conversation_service.py:48-52` `_GENERIC_*` | AI 輸出（L331,335,758） | 否（L47 註解明示 output-only） |
| `conversation_service.py:337` | AI 追問 | 否 |
| `department_knowledge.py:43` | KB concept／evidence 正規化（NFKC＋去空白） | 否；但它也套用在 present evidence 的 `source_text` 上做子字串檢索（`department_reasoning_service.py:120-127`），屬 Phase 3 §3.6 已記錄的官方 KB concept 檢索 |
| `department_knowledge.py:113,195` | source_id 格式、dept_id 數字格式 | 否 |
| `department_reasoning_service.py:21` | AI 輸出的 intent | 否 |
| `rule_engine.py:393` | AI 結構化日期格式 | 否 |

其他字面值比對（grep 中文字串 `in` 比對）：
- `appointment_service.py:558-562` `_session_rank`：套用在 SQL 班表 session 欄位，不是患者文字。
- `db.py:337-349` session aliases：SQL 班表欄位。
- `batch_extraction_service.py:71-84` `_structured_choice`：只做 API enum 完全相等比對（L78），句子不會被當成 enum；目前 `BATCH_TRIAGE_ENABLED=false`。
- `rule_engine.py:29-41` `apply_user_message`：只記錄原文，不做解析。

**結論：** 未發現對患者自由文字以 keyword／regex／phrase-list 判斷科別或症狀語意的 production 路徑。唯一的「詞→科」比對是官方 KB concept 對 AI 已接受 evidence（含 `source_text`）的子字串檢索，Phase 3 已記錄（不重審）。

### B. rule-based 科別判斷是否搶在 AI 前面決策（[讀碼]）
- `reason_about_departments` 中唯一在 AI 之前就決定科別的分支，是使用者明確指定科別（L340-344）。偏好本身來自 AI `department_preference`，並經 Backend 驗證（`rag_triage_adapter.py:108,157,216-218`；`department_preference_service.py:37-58`），不是 rule 判斷。
- AI 不可用時（`routes/chat.py:285-294`）只接受已存的指定科別，否則 `unresolved`、候選清空，不做規則猜科。[測試名稱] `test_phase4_department_convergence.py:394,416,428`。
- `routes/chat.py:450-472`（legacy batch）同樣呼叫 `reason_about_departments`，沒有另一套 rule。
- `appointment_service.py` 沒有 rule 選科（#9）。
- **結論：** 沒有 rule-based 科別判斷搶先決策。

### C. candidate state 跨輪保存、清除、重設（[讀碼]）
- **保存位置：** `ConversationState.department_status / candidate_departments / department_uncertainty_reason / department_next_information_needed / department_next_question_intent`（`schemas.py:330-334`）＋ `TriageCase.department_result`（`schemas.py:365`）；存於 in-memory `_CASES`（`case_store.py:31`，TTL 2 小時 L34，不跨 worker、重啟即遺失，L27-30 自述）。
- **重設時機：**
  1. 每次執行 `reason_about_departments` 都先清空（`department_reasoning_service.py:329-334`）→ 候選不累加，只依最新有效 evidence 重算。
  2. 修訂流程 `_begin_revision`（`routes/chat.py:743-771`）：清空科別狀態、`department_result`、`field_statuses["department"]` 與 department clarification 次數。**不清空** `semantic_extractions` 與 `patient_input.requested_department_*`（設計上延續問診紀錄；指定科別在修訂後仍然有效）。
  3. AI 更正舊欄位（correction）時：`invalidate_corrected_department_context`（`conversation_service.py:422-435`，由 `rag_triage_adapter.py:206-207` 呼叫）。
  4. 安全篩檢完成或進入安全篩檢時，只清 intent／information_needed（`conversation_service.py:519-520,726-727`），不清候選。
- **風險 1（狀態殘留）：** 若本輪 Turn Interpreter 失敗（`ai_suggestion is None` 或 `interpretation_complete=False`），`department_reasoning_allowed` 為 False（`routes/chat.py:356-359,369-373`），**不會重算**，上一輪的 `resolved`／`department_result` 會保留。此時 `advance_conversation` 走技術性 fallback（`conversation_service.py:694-697,710`，need_more_info=True），stage 被 gate 回 COLLECTING（`routes/chat.py:490-501`），所以不會直接進入確認。但下一輪若同樣失敗，舊結果仍會留著，要等到下一次成功解讀才重算。未被實測證實造成錯誤。
- **風險 2（client 快照）：** `sanitize_untrusted_snapshot`（`case_store.py:54-80`）會清空 `department_result`、`semantic_extractions`、`conversation_state`，但**沒有清 `patient_input.requested_department_id／requested_department_name`**。只有在 server 端沒有該 case（`routes/chat.py:713-720`）時才會採用快照；之後只要某一輪 AI 解讀成功，`reason_about_departments` 就會透過 `resolve_requested_department`（只比對 SQL 唯一列，不檢查原文 grounding，`department_preference_service.py:61-91`）把 client 帶入的科別判為 resolved。仍需經過安全篩檢 gate（`routes/chat.py:492`）與使用者確認（L502）。`/recommend` 採用快照時，conversation_state 會被重設 → `is_complete=False` → 400（`routes/recommend.py:37,91-92`），因此 recommend 端無法直接利用。[測試名稱] `test_phase42_backend_hardening.py:133` 的快照清理測試沒有涵蓋 requested_department_*。
- **風險 3（遺失）：** in-memory store 在重啟或多 worker 時會遺失（設計上已知，`case_store.py:27-30`），不屬於 Phase 4 程式錯誤。
- [測試名稱] `test_phase4_department_convergence.py:327`（跨輪收斂）、`:352`（失敗清空舊候選）、`:1025`（resolved → 安全篩檢 → 確認後保留結果）。

### D. 否定句處理
- **判定者：** AI（Turn Interpreter 的 `assertion` ∈ present／absent／uncertain）。Backend 刻意不解析否定詞（A 節 grep 無否定詞表）。
- **Backend 驗證（[讀碼]）：** assertion 必須屬於 `_EVIDENCE_ASSERTIONS`（`department_reasoning_service.py:26,56-58`）；同一 concept 以最新的一筆為準（L87-97，可用新的 absent 推翻舊的 present）；lexical 檢索只用 `assertion=="present"`（L114-118）；語意檢索的 `patient_source_text` 只能取自 present 證據（L152-156,191）；收斂 AI 的 support 必須命中 retrieved（L298），而 retrieved 只來自 present 證據。
- **缺口（沿用 AUDIT_PHASE3 §3.6，不重審）：** `source_text` 的寬度由 AI 決定，Backend 只檢查它是否為原文子字串（L47）。若 present 的 `source_text` 夾帶否定子句（例如「頭痛但沒有發燒」），lexical 會取到「發燒」，且不會經過 entailment 審核（L347-349）。收斂 AI 能看到 absent evidence（payload L354），但 Backend 不驗證「候選 support 的 concept 沒有被 absent evidence 否定」。
- **現有測試（[測試名稱]）：** `test_department_evidence_negation.py:95,116,166,176,203`（absent／uncertain 不會產生證據）、`:214`（混合句由 AI 分段）、`:283,361`（血尿否認）、`:343,384,435`（新舊 assertion 互相取代）、`:401`（低信心不可取代）、`:418`（list 逐 concept 取代）；`test_semantic_department_retrieval.py:76`。**未見**「present 證據的 source_text 含否定子句」以及「support concept 與 absent evidence 衝突」的測試。

### E. AI 失敗、逾時、格式不合法時的行為（[讀碼]）

| 情況 | 位置 | 最終狀態 | 是否可能回預設科別 |
|---|---|---|---|
| SQL Department 讀取失敗（`DatabaseUnavailableError`） | `db.py:90-94` → `department_reasoning_service.py:394-398` | `unresolved`、候選空、「科別證據比對暫時無法完成」 | 否 |
| SQL Department 回空 | L337-339 | `unresolved`、「正式科別主資料目前無法取得」 | 否 |
| KB 載入或驗證失敗 | L345 → L394-398 | `unresolved` | 否 |
| 語意檢索 provider 失敗、JSON 不合法、schema 不符、>5 筆 | L177-185 → `[]` → L350-352 | `unresolved`、「目前沒有可精確對應…」 | 否 |
| entailment 審核失敗 | L225-242 → `[]` | 同上 | 否 |
| 收斂 AI 逾時（`asyncio.wait_for`，`ai_service.py:227` 一帶；timeout＝60 秒） | L370 例外 → L394-398 | `unresolved`、候選空 | 否 |
| 收斂 AI 回非 JSON | `json.loads` ValueError → L394-398 | `unresolved` | 否 |
| 回 JSON 但不是 dict | L249-250 → L382-383 | `unresolved` | 否 |
| `status` 值不合法 | L252-253 強制改為 unresolved；有合格候選時 → L392-393 `ambiguous`，否則 `unresolved` | 不會 resolved | 否 |
| 候選欄位不合法 | L272-279、L289-303 逐筆剔除 | 剩 0 個 → `unresolved` | 否 |
| Turn Interpreter 失敗 | `rag_triage_adapter.py:85-91,153-155,175-177` → `routes/chat.py:369-373` 不重算 | 沿用上一輪狀態（C 節風險 1）；本輪回技術性 fallback 追問 | 否（但可能保留上一輪結果） |
| runtime AI 完全不可用 | `routes/chat.py:285-294` | 只有已驗證的指定科別；否則 `unresolved` | 否 |
| `asyncio.CancelledError`（BaseException） | `ai_service.py:173-183` 重新拋出；L394 `except Exception` 不會捕捉 | 請求中止，L519 `save_case` 不會執行 | 否 |

- 「取第一個」的檢查：`department_reasoning_service.py:384-385` 只在 `len(candidates)==1` 時取 `[0]`；`department_preference_service.py:82-84`、`appointment_service.py:49-51,63-65` 都要求恰好 1 筆。grep `backend/app` 沒有任何 default／fallback dept_id（見 H 節）。`RecommendationResult.fallback_departments` 固定為 `[]`（`appointment_service.py:169`）。
- 下游回覆：unresolved／ambiguous → `DEPARTMENT_EVIDENCE_REPLY`（`conversation_service.py:31,660`）或 `UNRESOLVED_REPLY`（L29）；legacy batch → `DEPARTMENT_UNRESOLVED_REPLY`（`routes/chat.py:54-57,559`），文案明示「沒有替你套用預設科別」。

### F. 使用者指定科別（例如「我想看皮膚科」）（[讀碼]）
- **語意辨識：** 由 AI 的 `department_preference` 輸出；prompt 規定只能從 active SQL Department 中選唯一的 ID／name pair，不得由症狀推論（`rag_triage_adapter.py:585,612`）。
- **正式 dept_id 驗證：** `validate_department_preference`（`department_preference_service.py:37-58`）：schema strict（`schemas.py:107-113`，`extra="forbid"`）、confidence ≥ 0.55、`source_text` 必須是本輪原文的子字串、dept_id 在 SQL 中恰好 1 筆，且名稱完全一致。通過後才寫入 `patient_input`（`rag_triage_adapter.py:216-218`）。
- **再次驗證：** `reason_about_departments` L340 → `resolve_requested_department`（L61-91）再比對一次 live DB；`/recommend`（`routes/recommend.py:104-108`）與 `recommend_appointments`（`appointment_service.py:81-87`）也會再驗一次。
- **安全篩檢：** 不會被跳過。①有 `red_flags` 時 `resolve_requested_department` 回 None（L66-67），改走一般 KB 收斂；②stage gate 要求 `safety_screen_resolved`（`routes/chat.py:492`）；③完成對話仍須安全篩檢與 grounded 症狀（`conversation_service.py:619-633`）；④`/recommend` 要求安全篩檢已完成（`routes/recommend.py:93-94`）；⑤TTAS urgent 優先（`routes/chat.py:333-347`；`routes/recommend.py:59-69`）。[測試名稱] `test_phase4_department_convergence.py:366,1002`、`test_phase42_backend_hardening.py:221`、`test_architecture_cleanup.py:195`。
- **觀察：** 指定科別路徑不會產生 `candidate_departments`，`department_result.confidence` 固定為 0.9（`department_preference_service.py:89`），理由標示為「使用者明確指定」，屬於偏好而非 KB 推薦，符合 prompt 第 31 條。快照帶入的指定科別未經原文 grounding（C 節風險 2）。

### G. dead code（無 production caller）
方法：對 Phase 4 範圍檔案（`department_reasoning_service.py`、`department_preference_service.py`、`appointment_service.py`、`confidence_scoring.py`、`field_acceptance.py`、`routes/chat.py`、`case_store.py`）的每個 `def`，以 `grep -rnw <name> backend/app`（排除定義行）計數，再對計數為 0 的項目 grep `backend/tests`。另外對 `needs_clarification` 以 `needs_clarification(` 搜尋（因 `SemanticExtraction.needs_clarification` 是同名欄位，`-w` 會誤計）。

| 項目 | 位置 | `backend/app` grep 結果 | tests 引用 | 判定 |
|---|---|---|---|---|
| `accept_department_preference` | `services/department_preference_service.py:25-34` | 只有定義本身 | `tests/test_architecture_cleanup.py:195`、`tests/test_strict_keyed_acceptance.py:104` | dead；production 改用 `validate_department_preference`＋`rag_triage_adapter.py:216-218` 寫入。可刪，需同步改 2 個測試 |
| `needs_clarification`（函式） | `services/confidence_scoring.py:14-15` | 沒有任何 `needs_clarification(` 呼叫；`import` 只有 `ACCEPT_THRESHOLD`、`accepted`、`MAX_QUESTION_ATTEMPTS` | 無 | dead；可刪 |
| `CLARIFICATION_THRESHOLD` | `services/confidence_scoring.py:3` | 只被 `needs_clarification` 使用（L15） | 無 | 隨上項一起成為 dead |
| `clamp_confidence` | `services/confidence_scoring.py:8-11` | 只有定義本身 | 無 | dead；可刪 |
| `_weekday_label` | `services/appointment_service.py:579-580` | 只有定義本身（檔內其他地方使用 `schedule_weekday_label`，L24,200,540） | 無 | dead；可刪 |
| `resume_case` | `routes/chat.py:64-70` | grep 計數為 0，但它是 `@router.get` route | — | **不是 dead**（FastAPI 註冊） |
| `routes/chat.py:450-472`（legacy batch 選科區塊）、`_handle_department_resolution_failure`（L635-686） | — | 有 caller | `test_batch_question_flow.py` 等 | **不是 dead**；目前設定 `BATCH_TRIAGE_ENABLED=false` 下不會進入，屬設定控制路徑 |
| `accepted_semantic_evidence` | `department_reasoning_service.py:100-102` | `routes/chat.py:735`、同檔 L113,151,354 | — | 不是 dead（Phase 3 已列） |

- 未列入：`project_smart_department_adapter.py`、`detect_department_result`：grep `backend/app` 無結果，已不存在。
- Voice／TTS 共用程式（`routes/voice.py`、`fixed_sentences.py`、`tts_cache.py`、`question_specs.py`、`clarification_engine.py`）**一律不列為可刪**；上表各項都沒有被這些檔案引用。
- Phase 3 報告已列的 `exact_db_department_ids`、`specialty_scoring.py` 三個函式，本次不重列。

### H. 硬編碼 dept_id 與科別名稱（[讀碼]）
- grep `backend/app/**/*.py`：
  - `1233|1333|[0-9]{4}`（排除年份、1000、8000、perf_counter、1200）：**無結果**。四位數只有 `script_service.py:16` `delay_ms=1200`（非科別）。
  - `dept_id\s*(==|!=|in|=)\s*[0-9]`：**無結果**。
  - 科別名稱 `皮膚|骨科|心臟|內科|外科|耳鼻|家醫|家庭醫學|神經|眼科`：**無結果**。
  - 字串字面值 `"…科"`：**無結果**。所有含「科」的程式行只有 `conversation_service.py:33` `_UNSAFE_QUESTION`（AI 追問不可提到科別的 guard）與 `followup_service.py:142` 的「同科」文案。
- `backend/app` 下沒有 `.json` 資料檔；科別 ID 只出現在 `backend/knowledge/*.json`（官方 KB 資料，Phase 3 範圍）與測試檔（例如 `test_phase42_backend_hardening.py:157`、`:268` 的 1232、1234，屬測試 fixture）。
- **結論：** production 邏輯中沒有寫死 dept_id（包含 1233）或科別名稱。

## 4. 需要修復的項目

| # | 項目 | 預估 production 檔案數 | 對照最小修改原則與 Cleanup Gate |
|---|---|---|---|
| R1 | 刪除 dead code：`accept_department_preference`、`needs_clarification`＋`CLARIFICATION_THRESHOLD`、`clamp_confidence`、`_weekday_label`（§3.G） | 3：`department_preference_service.py`、`confidence_scoring.py`、`appointment_service.py`（＋測試 2 檔） | 屬 §14「無 production caller 的 dead path」，適用 Cleanup Gate 的 3 檔例外；不改 schema 或 state machine。刪除前需重跑 grep。 |
| R2 | `sanitize_untrusted_snapshot` 未清 `requested_department_id／name`（§3.C 風險 2） | 1：`case_store.py` | 未被實測證實。**列入待辦，不主動修。** 若要修，屬「移除 client 斷言的 server-owned 狀態」，應與使用者確認是否算 Cleanup Gate 範圍。 |
| R3 | 「不足證據」沒有結構化欄位；`department_next_information_needed` 恆為 None（#1） | ≥2（`schemas.py`、`department_reasoning_service.py`，可能加上 `conversation_service.py`） | 需**改 API schema／state**，不適用 Cleanup Gate 例外（不是刪除 legacy，而是新增欄位）。**列入待辦，不主動修**；施工前需使用者同意。 |
| R4 | 收斂 AI 輸入與規格不一致：沒有 conversation summary、多了 `previously_asked_intents`（#2） | 1：`department_reasoning_service.py`（或只修文件） | 尚未證實造成錯誤；可能只需更新計畫書描述。**列入待辦，不主動修**，由使用者決定是改文件還是改程式。 |
| R5 | AI 回 `resolved`＋多候選、只有 1 個通過驗證時會被判 resolved（#8） | 1：`department_reasoning_service.py` | 未被實測證實。**列入待辦，不主動修。** |
| R6 | Turn Interpreter 失敗時不重算，舊 resolved 結果保留（§3.C 風險 1） | 1：`routes/chat.py` | 已有 stage gate 緩解；未被實測證實。**列入待辦，不主動修。** 修改會動到 chat 控制流，需先取得使用者同意。 |
| R7 | present `source_text` 夾帶否定子句的 lexical 誤取；Backend 不驗證 support 與 absent evidence 是否衝突（§3.D） | —（Phase 3 F4 同源） | 沿用 AUDIT_PHASE3 F4：只陳述事實，不屬已證實 regression，不在 Cleanup Gate 範圍。**列入待辦，不主動修。** |

## 5. 未能確認的項目與限制
- 未執行 pytest；所有 [測試名稱] 只代表測試存在，不代表通過。
- **需 310 實測／真 Cerebras：**
  - #10 模糊症狀時 AI 是否會過早給出單一高信心候選。
  - #11 實際多輪下候選是否收斂。
  - #12 AI 給的 `assertion` 正確率，以及 `source_text` 是否夾帶否定子句（R7 的實際發生率）。
  - #13 同義句／口語句的實際表現。
  - 收斂 AI 在 60 秒 timeout 下的實際延遲與逾時頻率。
  - AI 是否會回「resolved＋多候選」的不一致提案（R5 是否真的會發生）。
- **需真 SQL：**
  - Department 表是否有停用欄位；若有，`fetch_active_departments`（`db.py:75-83`）沒有過濾，「active」實際等於「全部」（#4）。
  - live DB 中 dept_id 是否唯一（`active_ids.get(dept_id) != 1` 的實際效果）。
  - KB canonical_dept_id 與 live DB 的對應（Phase 3 §5 同項）。
- **需實測證實的狀態風險：**
  - §3.C 風險 1：連續 Turn Interpreter 失敗時，舊結果殘留是否造成使用者可見的錯誤。
  - §3.C 風險 2：Android 或其他 client 實際上是否會送出含 `requested_department_*` 的快照（Android 不在範圍內，無法確認）。
- 本報告不審查 Phase 3 KB 內容、Phase 5 TTAS 規則、Phase 6 推薦排序，也不審查 2026-10-27 新增、尚未施工的 Phase 2.5 保底流程（20 輪上限／家醫科）；目前程式的 `HARD_TURN_CAP=8`、`ABSOLUTE_TURN_CAP=24` 只作為環境紀錄。
