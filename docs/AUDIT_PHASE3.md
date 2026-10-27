# Phase 3 審查報告（Backend only）

> 審查日期：2026-10-27
> 依據：`docs/修改計畫.md` §9 Phase 3（含 2026-10-27 新增「保底知識條目」），並參照 §2、§11、§0／§14 最小修改原則與 Architecture Cleanup Gate
> 範圍：`backend/knowledge/` 與載入／使用它的 `backend/app` 程式。Android 不在範圍內。
> 方式：只讀。未修改 production 檔案，未執行 pytest，未 commit。

## 0. 證據等級與執行環境
- **[讀碼]**：已讀函式本體，並 grep `backend/app` 確認 caller 在 production 控制流上。
- **[資料腳本]**：以唯讀 Python（`json.load`，不寫回）逐筆解析 knowledge JSON 得到的統計。
- **[測試名稱]**：只確認測試檔有此案例，未執行。
- 載入點：唯一 production loader 為 `services/department_knowledge.py:46-56` `load_department_knowledge()`，唯一 production caller 為 `services/department_reasoning_service.py:345`（`reason_about_departments`，由 `/chat` 呼叫；`routes/voice.py:24` 轉呼叫 `chat`，故 voice 也走此路徑）。
- 實際載入檔案：`knowledge/sources.json`（18 sources）、`knowledge/vghtpe_department_guidance.json`（541 records）、`knowledge/supplemental_guidance_20261021.json`（+1 source、+1 record）、`knowledge/department_canonical_mapping.json`（35 mappings）。合計 19 sources／542 records。`AI_Source/phase3_kb_migration_package/` 不被 `backend/app` 載入（grep `AI_Source|production_seed` 於 app 無結果），只被測試用於 lineage 比對。

## 1. KB 載入與檢索主流程（[讀碼]）
`reason_about_departments`（`department_reasoning_service.py:326-398`）：
1. 清空候選與 uncertainty（L329-334）。
2. `fetch_active_departments()` 無資料 → `unresolved`＋「正式科別主資料目前無法取得」（L336-339）。
3. 使用者明確指定科別 → `resolve_requested_department`，只對 SQL active 唯一列（L340-344；`department_preference_service.py:61-91`）。
4. `load_department_knowledge()` → `validate_department_knowledge` 全量驗證，任一筆不合格即 raise（fail-closed，由 L394-398 捕捉成 `unresolved`）。
5. `resolve_department_names`：canonical_dept_id 對 live DB 唯一列（`department_knowledge.py:204-242`）。
6. lexical：`retrieve_official_evidence`（L105-144）；無結果才走 AI 語意檢索＋entailment 審核 `retrieve_semantic_official_evidence`（L147-242）。
7. 仍無證據 → `unresolved`＋「目前沒有可精確對應正式科別的官方症狀證據」（L350-352）。
8. AI 收斂提案 → `validate_candidate_proposal`（L245-323）→ 依數量與信心決定 resolved／ambiguous／unresolved（L382-393）。

## 2. 逐條審查表

| # | 要求 | 判定 | 證據（檔案:行號） | 說明 |
|---|---|---|---|---|
| 1 | 新增 `backend/knowledge/vghtpe_department_guidance.json` | 符合 | `knowledge/vghtpe_department_guidance.json`（541 筆） | [資料腳本] 18 個 source_id、34 個 canonical_dept_id。 |
| 2 | 新增 `backend/knowledge/sources.json` | 符合 | `knowledge/sources.json:7-188` | [讀碼] 18 筆，每筆有 source_hospital／type／priority／title／url／retrieved_at／note。另有 `supplemental_guidance_20261021.json:2-13` 追加 1 筆，由 loader L49-53 合併。 |
| 3 | 每筆有 `source_id` | 符合 | [資料腳本]；強制：`department_knowledge.py:127-133` | 541/541 有值；全部存在於 sources.json（未註冊 0 筆）。loader 對未註冊 source_id raise。 |
| 4 | 每筆有 `source_url` | 符合 | [資料腳本]；`department_knowledge.py:134,276-277` | 541/541 有值。record 的 provenance 欄位必須與 registry 完全一致；URL 必須 https 且 host 在官方白名單（`_SOURCE_POLICY` L13-21）。 |
| 5 | 每筆有更新／擷取日期 | 符合 | [資料腳本]；`department_knowledge.py:122,284-290` | 541/541 有 `retrieved_at`；source 層驗證 ISO date，record 層透過 L134 與 source 一致。 |
| 6 | 可追溯 | 符合 | `department_knowledge.py:141-142`；`tests/test_department_knowledge.py:47-98` | [讀碼] `evidence_text` ≤120 字且必須含 concept 原字。[測試名稱] lineage 測試比對 production 與 migration package seed（523 筆 routing_records 依 `supporting_source_ids` 展開後 = 541）。 |
| 7 | 不允許 AI 自行「補常識」 | 符合（資料面）／無法完全確認（內容真實性） | `department_knowledge.py:141-144`；`department_reasoning_service.py:170,191-192` | [讀碼] runtime 不會新增 KB 條目；語意檢索只能回 catalog 內 concept。條目是否逐字出自官方頁面需對照原網頁／PDF，本次未做（見 §5）。 |
| 8 | 舊 `dept_keywords.py` 不得整份視為真理；有來源轉入、無來源標 `legacy_unverified` | 符合 | `knowledge/department_canonical_mapping.json:5-16`；`AI_Source/.../PHASE3_KB_MIGRATION_REPORT.md:11-17` | [讀碼] 763 對 legacy 配對分類：direct 229、specialty 294、legacy_synonym 18、legacy_unverified 222。`dept_keywords.py` 本體不在 repo（Glob 無結果）。 |
| 9 | `legacy_unverified` 不得進 production 判斷 | 符合 | `department_knowledge.py:38,143-144`；`vghtpe_department_guidance.json:5-10` | 見 §3.2。 |
| 10 | Retrieval：lexical／structured → Top-K → LLM reasoning | 符合（結構）；有誤取風險 | `department_reasoning_service.py:105-144,147-242,353-370` | [讀碼] 未使用 vector DB。誤取風險見 §3.6。 |
| 11 | 驗收：每個推薦理由能對應 `source_id` | 符合（候選證據層） | `department_reasoning_service.py:282-309`；`schemas.py:288-294` | [讀碼] 每個 candidate 的 `supporting_evidence` 都帶 `knowledge_source_id`，且 (dept_id, patient_source_text, source_id, concept) 必須完全等於某筆已取回證據（L298）。注意：`DepartmentResult.reason`（L390）是固定文案，不含 source_id；source_id 保存在 `conversation_state.candidate_departments`。使用者自選科別（L340-344）不經 KB，理由是「使用者明確指定」，屬偏好而非推薦。 |
| 12 | 驗收：AI 不得推薦 KB／DB 都不存在的科別 | 符合（更嚴格：須同時在 KB 與 DB） | `department_reasoning_service.py:273-274,298`；`department_knowledge.py:224-240` | [讀碼] dept_id 必須在 active DB 恰好 1 筆，且必須出現在已取回的官方證據中（證據本身只來自 resolved canonical id）。 |
| 13 | 驗收：官方資料不足時標示 uncertainty | 大致符合 | `department_reasoning_service.py:338,351,382-383,392-393,398` | 見 §3.5。AI 回 ambiguous 但未給 `uncertainty_reason` 時，`department_uncertainty_reason` 為 None，只剩 `department_status="ambiguous"` 表示不確定。 |
| 14 | GitHub Gate：production 是否仍有大量手寫醫療 mapping | 未發現 | 見 §3.3 | |
| 15 | （新增）`fallback_routing_evidence` 保底條目 | **待施工需求（不判缺失）** | 見 §3.8 | |

## 3. 特別檢查項目

### 3.1 provenance 欄位逐筆檢查（[資料腳本]＋[讀碼]）
- `vghtpe_department_guidance.json` 541 筆：`source_id`、`source_url`、`retrieved_at` 缺漏皆為 0；source_id 不在 `sources.json` 的筆數為 0。
- 每筆欄位集合一致（18 個 key，含 `notes`）。evidence_type：direct 246、specialty 295。`fallback_for_vghtpe=True` 31 筆，全為 `vghtc_ent_department_2026`（L162-163 強制台中來源必須標 fallback）。
- supplemental 檔 1 筆（`vghtpe_orth_foot_pain_20261021`）欄位完整、source 已註冊。觀察：其 `source_url` 的查詢參數為 `q=踝部扭傷`，但 title／evidence 為「足部疼痛、變形」（`supplemental_guidance_20261021.json:9,34`），未連網核對，列入 §5。

### 3.2 legacy_unverified／legacy_synonym 是否進 production（[讀碼]）
- meta：`vghtpe_department_guidance.json:5-10` `enabled_evidence_types=[direct, specialty]`、`legacy_synonym_routing_enabled=false`、`legacy_unverified_routing_enabled=false`。
- **但 loader 不讀 meta**：`department_knowledge.py:48` 只取 `["records"]`；grep `\["meta"\]|semantic_synonyms|synonym` 於 `backend/app` 無結果。meta 旗標是說明性質，不是實際開關。
- 實際阻擋機制有兩層：(a) 資料面：production 檔只含兩種 evidence_type，legacy 的 18＋222 筆只存在於 `AI_Source/.../phase3_kb_production_seed.json` 的 `semantic_synonyms`／`legacy_unverified`，app 不載入；(b) 程式面：`department_knowledge.py:38,143-144` 對其他 evidence_type 直接 raise，整份 KB 載入失敗 → `reason_about_departments` 例外分支回 `unresolved`（fail-closed，而不是靜默過濾）。
- 結論：legacy 條目目前不會進 production 判斷。

### 3.3 手寫症狀→科別 mapping 殘留（[讀碼]）
- `dept_keywords.py`：repo 內不存在（Glob `**/*dept_keyword*` 無結果），只在 `department_canonical_mapping.json:6` 與 migration 報告中作為歷史來源被提到。
- `appointment_service.py`：已無「膝／關節→骨科、胸痛／心悸→心臟內科」類規則。L88-90 沒有已驗證科別時直接 raise「尚無已驗證的正式科別結果；不執行舊版科別猜測」；`resolve_department_result`（L41-72）只做 DB 唯一列比對。
- `backend/app` grep 科別字面值 dict（`"…科"\s*[:,\]]`）無結果；所有 `re.*` 呼叫（conversation_service.py:32-49,337；department_reasoning_service.py:21；department_knowledge.py:113,195；rule_engine.py:393）都不是對患者文字判科別。
- 症狀字詞只出現在固定安全篩檢文案（`question_specs.py:45-49`、`clarification_engine.py:6`）與 AI prompt 範例（`rag_triage_adapter.py:633-652`、`conversation_service.py:110`），不是 mapping。
- `specialty_scoring.py:128-135` `score_doctor_deterministically` 已改為中性值，不做關鍵字推論。
- 唯一仍存在的「詞→科」比對是官方 KB concept 的子字串檢索（§3.6），資料來源為官方 KB。

### 3.4 AI 推薦理由與 source_id（[讀碼]）
見表 #11、#12。補充：語意檢索 AI 回的 `knowledge_concept` 必須在 catalog（L192）、`patient_source_text` 必須在 present 證據來源集合（L191）；entailment 審核只能刪不能加（L209-239）；收斂 AI 的 source_id 必須在 registry（L293）且整組 key 必須命中已取回證據（L298），任何一筆 support 不合格整個 candidate 作廢（L296-303）。

### 3.5 官方資料不足時的 uncertainty（[讀碼]）
- DB 無資料：L338；無官方證據：L351；例外：L398 — 皆設 `unresolved`＋固定 uncertainty 文案，不選科。
- 驗證後無候選：L382-383 `unresolved`。
- 只有「AI 回 resolved＋恰 1 候選＋信心 ≥ `ACCEPT_THRESHOLD`」才 resolved（L384-391），否則 `ambiguous`（L392-393）。
- 缺口：ambiguous／unresolved 時 `department_uncertainty_reason` 只取 AI 文字（L319-320,375），AI 沒給就是 None。下游 `conversation_service.py:656` 有 `or "正式科別證據不足"` 的補位，但 `department_uncertainty_reason` 欄位本身仍為空。

### 3.6 Retrieval（子字串比對）誤取風險 — 只陳述事實
- [讀碼] `retrieve_official_evidence` 對每筆 present 證據，把 `normalized_value` **與患者原文 `source_text`** 都做 NFKC＋去空白＋casefold，再檢查「KB concept 是否為其子字串」（`department_reasoning_service.py:120-127`）。
- [讀碼] `source_text` 的寬度由 AI 決定；Backend 只驗證它是歷史訊息的子字串（L47），「最短連續原文、不得含無關子句」只是 prompt 規則（`rag_triage_adapter.py:587`），沒有 Backend 強制。若 AI 對 present 的「頭痛」回 `source_text="頭痛但沒有發燒"`，KB concept「發燒」會被取回。Backend 刻意不解析否定詞。
- [資料腳本] concept 長度：1 字 1 個（「癢」）、2 字 117 個；concept 之間互為子字串 49 對，例如「腫瘤」⊂13 個 concept、「水腫」⊂4 個、「疤痕」⊂4 個、「分泌物」⊂4 個、「囊腫」⊂「陰囊腫」、「腹痛」⊂「上腹痛／下腹痛」。
- [資料腳本] 部分 concept 是部位或一般詞而非症狀：「月經」「陰道」「子宮」「卵巢」「多喝」等；含這些字的原文（如「月經來的時候頭痛」「多喝水也沒用」）會命中。
- [資料腳本] 69 個 concept 對應多個科別（例如「頭暈」7 科、「咳嗽」7 科、「頭痛」5 科）；`lookup_concept`（`department_knowledge.py:173-188`）會把每科最佳證據都回傳，最終由收斂 AI 在其中選。
- [讀碼] 只要 lexical 有任何命中，就**不會**執行語意檢索與 entailment 審核（L347-349），lexical 誤取不會經 AI 審核過濾。
- [讀碼] 既有緩解：只用 `assertion=present` 且 `semantic_status∈{available,partial}` 的證據（L114-118）；同 concept 以最新一筆為準（L87-97）；候選最後仍須通過 §3.4 驗證。
- [測試名稱] `tests/test_department_evidence_negation.py:166,176,214,361` 涵蓋 absent／uncertain 原文不產生證據、混合句以 AI 分段處理；未見「present 證據的 source_text 含否定子句」的測試。

### 3.7 無 production caller 的 dead module／函式（grep 證據）

| 項目 | 位置 | grep 結果 | 可否刪 |
|---|---|---|---|
| `exact_db_department_ids` | `services/department_knowledge.py:245-256` | 只有 `tests/test_department_knowledge.py:9,298` | 可刪（需同步刪測試）；docstring 自稱 compatibility helper |
| `score_doctor_deterministically` | `services/specialty_scoring.py:128-135` | 只有 `tests/test_selective_wu_merge.py:16,76`、`tests/test_specialty_scoring.py:12,63` | 可刪（需同步改測試）；範圍邊緣（推薦排序，非 KB） |
| `clamp_score` | `services/specialty_scoring.py:138-141` | 全 backend 只有定義本身 | 可刪；範圍邊緣 |
| `_row_key` | `services/specialty_scoring.py:272-273` | 全 backend 只有定義本身 | 可刪；範圍邊緣 |
| `AI_Source/phase3_kb_migration_package/` | 目錄 | app 無引用；`tests/test_department_knowledge.py:41-98` 依賴 | **不可刪**：provenance 與 lineage 測試依據 |
| `accepted_semantic_evidence`（相容名稱） | `department_reasoning_service.py:100-102` | production：`routes/chat.py:735`、同檔 L113,151,354 | 不是 dead |

- voice／TTS 共用：`routes/voice.py:24` 匯入 `routes.chat.chat`，因此 `department_reasoning_service.py` 與 `department_knowledge.py` 整體在 voice 路徑上，**不可刪模組**；上表各函式皆未被 voice、`fixed_sentences.py`、`tts_cache.py` 引用。`question_specs.py`／`clarification_engine.py`（voice 依賴）不在 Phase 3 範圍，維持 Phase 2 報告結論。

### 3.8 待施工需求：保底知識條目（2026-10-27 新增，不判缺失）
- 現況 [資料腳本]：家庭醫學科（1233）13 筆，全為個別症狀（發燒、頭痛、頭暈、癢、咳嗽…），來源皆 `vghtpe_patient_guide_2026`；無「無法區分之疾病」條目；無 `fallback_routing_evidence`；sources 中無護理諮詢專線來源。與計畫書 §9 Phase 3 盤點一致。
- 施工時需注意的既有程式限制 [讀碼]：
  1. `department_knowledge.py:38,143-144`：新 evidence_type 會被判為 non-routing 並 raise，**整份 KB 載入失敗**。
  2. `department_knowledge.py:180`：`evidence_rank` 只有兩種 key，新型別進 `lookup_concept` 會 KeyError。
  3. `department_reasoning_service.py:111,158`：lexical 與語意 catalog 取「所有 records 的 concept」，不看 evidence_type；若不排除，保底條目會進一般 retrieval（違反「一般症狀查詢不會誤取」驗收）。
  4. meta `enabled_evidence_types` 目前不被讀取（§3.2），不能只靠修改 meta 達成排除。
  5. 護理諮詢專線屬非醫療路由資訊，`sources.json` meta 為 `medical_sources_only: true`（L5），且 `_SOURCE_POLICY` 無對應 source_type，需決定放置位置。
- 預估：2 個 production 程式檔（`department_knowledge.py`、`department_reasoning_service.py`）＋ 1～2 個資料檔，另須 Phase 2 保底流程取用端。屬新增功能，**不在 Cleanup Gate 範圍**，依計畫書 L1617 以獨立階段（Phase 2.5）施工；PDF V4 原文與頁碼核對須人工完成。

## 4. 需要修復的項目

| # | 項目 | 預估 production 檔案數 | 對照最小修改原則／Cleanup Gate |
|---|---|---|---|
| F1 | 刪除 `exact_db_department_ids`（§3.7） | 1：`services/department_knowledge.py`（＋測試 1 檔） | 屬 §14「無 production caller 的 dead path」，適用 Cleanup Gate；不改 schema。 |
| F2 | 刪除 `specialty_scoring.py` 的 `clamp_score`、`_row_key`、`score_doctor_deterministically` | 1：`services/specialty_scoring.py`（＋測試 2 檔） | 同上屬 dead path；但模組屬推薦排序，若 Cleanup Gate 以 phase 分批，應與 Phase 4／推薦相關清理一起處理。 |
| F3 | ambiguous／unresolved 時 `department_uncertainty_reason` 可能為 None（§3.5） | 1：`services/department_reasoning_service.py` | 未證實造成 regression（下游有補位），依最小修改原則先列待辦，不主動改。 |
| F4 | lexical 子字串誤取風險（§3.6） | —（不判定歸屬） | 依指示只陳述事實；是否屬 Phase 3 或 Phase 4、是否修改，由使用者決定。不屬已證實 regression，不在 Cleanup Gate 允許範圍。 |
| F5 | meta 旗標不被讀取（§3.2） | 0（目前不需修） | 實際阻擋已由驗證器 fail-closed 達成；若要讓 meta 成為真開關即屬新增行為，不建議在 Cleanup Gate 中處理。可與 §3.8 一併評估。 |

## 5. 未能確認的項目與限制
- 未執行 pytest；[測試名稱] 只代表測試存在。
- 未連網或對照 PDF：541＋1 筆 `evidence_text` 是否逐字出自官方頁面、`retrieved_at` 是否真實、supplemental 來源 URL 查詢參數（`q=踝部扭傷`）與內容（足部疼痛）是否一致，皆未驗證。
- 未連 310 live DB：34 個 canonical_dept_id 是否都在 live active Department 中唯一存在，只能由 runtime `resolve_department_names` 判定，本次未確認。
- AI 實際回傳的 `source_text` 寬度（影響 §3.6 風險大小）需真 Cerebras 實測。
- 依指示，本報告不審查 Phase 4。
