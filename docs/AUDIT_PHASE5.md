# Phase 5 審查報告（Backend only）

> 審查日期：2026-10-27
> 依據：`docs/修改計畫.md` §9 Phase 5（L1028-1148），並參照 §2 架構原則（L112-190）、§3.2 急迫程度依據（L220-252）、§11 永久禁止事項（L1464-1485）、§0／§14 最小修改原則與 Architecture Cleanup Gate（L26-28、L1619-1640）、§13「急迫性」小節（L1568-1573）；「急迫優先」條文見 Phase 2 停止條件（L712）與保底流程規則（L759）。
> 範圍：`backend/` 中 TTAS evidence 抽取／驗證／保存（`services/rag_triage_adapter.py`、`services/ttas_evidence.py`、`schemas.py`）、rule data 與載入（`knowledge/ttas/`、`services/ttas_rule_loader.py`）、deterministic evaluator（`services/ttas_evaluator.py`）、`/chat`、voice、`/recommend` 的急迫分流，以及舊自創急迫邏輯殘留。Android 不在範圍內。
> 不重審：Phase 2～4 結論只引用（`docs/AUDIT_PHASE2.md`、`AUDIT_PHASE3.md`、`AUDIT_PHASE4.md`）。不審 Phase 6 推薦排序與尚未施工的 Phase 2.5 保底流程。
> 方式：只讀。未修改 production 檔案、測試檔或資料檔，未執行 pytest，未 commit，未連 310。rule 統計以唯讀 Python（`json.load`，不寫回）執行。

## 0. 證據等級與設定值
- **[讀碼]**：已讀函式本體，並 grep `backend/app` 確認在 production 控制流上。
- **[資料腳本]**：以唯讀 Python 讀 `backend/knowledge/ttas/*.json` 統計，未寫回。
- **[測試名稱]**：只確認測試檔有此案例，未執行，**不能當作通過證據**。
- **無法確認**：需要實測、官方原文或真 SQL 才能判定，不推測。

目前設定值（[讀碼]；`backend/.env` 只擷取非機密鍵）：

| 鍵 | 目前值 | 預設值／位置 | 與 TTAS 的關係 |
|---|---|---|---|
| `AI_TIMEOUT_SECONDS` | 60（`.env`） | 8.0（`app/config.py:34`） | Turn Interpreter（含 TTAS evidence 抽取）逾時 |
| `CEREBRAS_MODEL` | gpt-oss-120b | 同（`config.py:28`） | 抽取模型；`AI_PROVIDER` 未設 → 預設 cerebras（`config.py:26`） |
| `BATCH_TRIAGE_ENABLED` | false | false（`config.py:29`） | 關閉時 legacy batch 路徑不進入 |
| `USE_TTAS_TRIAGE` | true | 不在 `Settings` 內 | **只被記錄進效能 log**（`services/chat_perf.py:51,122,241`），不是功能開關；TTAS 永遠執行 |
| `DEPLOY_MODE` / `AI_DOCTOR_SCORING_ENABLED` | local / true | `config.py:48,37` | 與 TTAS 無關，僅環境紀錄 |
| `ACCEPT_THRESHOLD` | 0.55 | `services/confidence_scoring.py:4` | TTAS evidence confidence 門檻（`ttas_evidence.py:29,94`） |
| `SAFETY_ANSWER_CAP` / `HARD_TURN_CAP` / `ABSOLUTE_TURN_CAP` | 3 / 8 / 24 | `services/conversation_service.py:18-22` | 安全篩檢重試上限與輪數上限 |
| `TTAS_KNOWLEDGE_DIR` | `backend/knowledge/ttas` | `services/ttas_rule_loader.py:10` | rule 套件路徑；`load_ttas_rules` 以 `lru_cache(maxsize=1)` 快取（L137），改 rule 需重啟 |

- 其餘 `.env` 鍵（DB_*、VOICE_*、`GOOGLE_API_KEY`、`CEREBRAS_API_KEY`、`DISABLE_LOCAL_EMBEDDING`）與 TTAS 無關，未擷取值。
- voice：`routes/voice.py:165-171` 以 `ChatRequest(case_id, message, confirmed, visit_type)` 直接呼叫 `routes.chat.chat`，本報告所述 `/chat` 急迫控制流同樣適用於 voice。

## 1. 主控制流摘要（[讀碼]）
`POST /chat`（`routes/chat.py:73-611`），自然對話模式：
1. `_resolve_case`（L701-721）；client 快照經 `sanitize_untrusted_snapshot` 清空 `ttas_evidence`、`ttas_result`、`red_flags*`、`age_*`、`triage`、`conversation_state`（`case_store.py:54-80`）。
2. L87 記錄 `urgent_terminal_at_request_start`；L275-277 本輪沒有使用者輸入時 `preserve_urgent_terminal=True`。
3. `semantic_ai_allowed = ai_available and clarification_status != "unresolved"`（L185-188）→ `refine_case_with_ai`（L279-281）。
4. Turn Interpreter（`rag_triage_adapter.py:63-230`）：同一次呼叫輸出 `ttas_evidence`；`validate_ttas_evidence_items` 驗證（L95-99、L334）；整輪通過結構驗證後才 `apply_ttas_evidence`（L211-212）。失敗分支見 #12。
5. `apply_ttas_evaluation`（L299）→ `evaluate_ttas`（`ttas_evaluator.py:26-92`）只讀已驗證 evidence 與年齡事實，依 enabled rule 計算；level 1/2 → `warning_required=True, is_final=True`（L99、L109-117）。
6. `ttas_requires_immediate_action`（L333）成立 → urgent terminal（L334-347），**不執行**科別推理、追問規劃與 `advance_conversation`。
7. stage gate：`_is_urgent_terminal`（L614-619：`clarification_status=="urgent"` 且 `warning_required` 且 `is_final`）優先於所有其他 gate（L475-483）；回覆固定為 `warning_message` 或 `URGENT_WARNING_FALLBACK`，且不經 `generate_triage_reply`（L554-557、L569）。
8. `/recommend`（`routes/recommend.py:59-69`）：`clarification_status=="urgent"` 或 `ttas_result` 為 matched 且 level∈{1,2} → 400。

## 2. 逐條審查表

| # | 要求 | 判定 | 證據（檔案:行號） | 說明 |
|---|---|---|---|---|
| 1 | 移除自創 `RED_FLAG_PATTERNS` | 符合 | grep `backend/`（含 json/md）`RED_FLAG_PATTERNS`：0 筆 | [讀碼] 已不存在。殘留的同名概念只有：`PatientInput.red_flags`（`schemas.py:188`，production 沒有任何地方寫入非空值；`rule_engine.py:220` 拒絕 AI 寫入）與固定安全篩檢問句（見 §3.B-1），兩者都不是 pattern 比對。 |
| 2 | 移除 `_score_duration`、`_score_function_limit`、自創 urgency 累加、其他無官方來源醫療分數 | 符合（殘留 schema 欄位另列 T10） | grep `backend/` `_score_duration\|_score_function_limit`：0 筆；`ttas_evaluator.py:106-108` | [讀碼] production 沒有任何 urgency 計分。`UrgencyResult.urgency_score/urgency_level` 恆為 None（`ttas_evaluator.py:107-108`）。殘留：①`rule_engine.py:44-71` `evaluate_urgency` 只是 TTAS wrapper，無 production caller（§3.F）；②`UrgencyNormalization.urgency_level` 預設 `"low"`、`matched_red_flags`（`schemas.py:166-175`），production 只在 `case_store.py:74` 重設，沒有邏輯讀取，但會隨 `triage_case.patient_input` 出現在 API 回應中。`appointment_service.py:315-435` 的 `_score_*` 屬 Phase 6 醫師專長，非急迫分數。 |
| 3 | AI 抽取 TTAS evidence 每欄都要求 `source_text` 與 `confidence` | 符合 | Prompt：`rag_triage_adapter.py:597-605`；Backend：`ttas_evidence.py:12,30`；schema 原則：`knowledge/ttas/ttas_evidence_schema.json` principles | [讀碼] prompt 規定每筆只能有 field／value／semantic_status／confidence／source_text（L599），source_text 為本輪最短逐字片段（L600），confidence 意義限定為「原文支持度」（L601）。Backend 要求 key 集合**完全等於**這 5 個（`ttas_evidence.py:12,30`），多或少一個 key 都丟棄。[測試名稱] `test_phase5_ttas_evidence.py:42,50,61,81`。 |
| 4 | Backend 驗證 source_text 出現在原文、欄位白名單、confidence 門檻 | 部分符合 | `ttas_evidence.py:16-59,93-105,108-140` | [讀碼] 驗證：①欄位 ∈ `ttas_evidence_schema.json` fields（L38）；②semantic_status ∈ {available, unknown, ambiguous}（L39）；③source_text 非空且為**本輪** user_sources 子字串（L40-42）；④confidence 有限、0～1、≥0.55（L43-46）；⑤value 符合型別／enum／range，`available` 不可配 null 或 `"unknown"`（L47、L108-140）。每次評估前再驗一次（`_stored_evidence_valid`，L93-105，比對整段 user history）。**缺口**：(a) Backend 不檢查 value 與 source_text 是否一致——例如 source_text「叫不醒」配 `gcs=5`、「發燒」配 `temperature_c=41.5` 都能通過（數值只檢 range；`temperature_c`、`sbp_mmhg`、`heart_rate_bpm` 等無上限，`ttas_evidence_schema.json` fields）；只靠 prompt L603 禁止換算。(b) 白名單是 schema 全部 49 個欄位，不是 prompt 顯示的 enabled 24 個（[資料腳本]）；多出的欄位會被保存但不被 enabled rule 使用。(c) `effective_ttas_evidence` 對沒有有效 evidence 的年齡，會以 `patient_input.age_*` 補一筆 `source_text="trusted_profile"`、confidence 1.0（L73-89）；目前 `patient_input.age_*` 只由 AI 年齡 evidence 寫入（L62-70），標籤「trusted_profile」與實際來源不符（未證實造成錯誤）。[測試名稱] `test_phase5_ttas_evidence.py:50,61,71,101`。 |
| 5 | TTAS rule data 版本化放在 `backend/knowledge/ttas/`，不散在 if/else | 部分符合 | `knowledge/ttas/README.md`；`source_manifest.json:2-13`；`active_rules.json:2-11`；`ttas_rule_loader.py:137-207`；`ttas_evaluator.py:45-58,128-173` | [讀碼] 有 `source_manifest.json`（package_id、版本策略 105.1.1＋107.5.1＋108.6.11 覆寫）、`ttas_evidence_schema.json`、`active_rules.json`（`ruleset_id`）、`evidence_catalog.json`；evaluator 是通用 predicate 直譯器，無症狀 if/else。差異：①所有規則合併在單一 `active_rules.json`，未依計畫建議拆成 `adult_non_trauma.json`／`trauma.json`（計畫用語為「建議」）；②有兩項判斷寫在 Python、不在 rule data：成人／兒童分界 18 歲／216 個月（`ttas_evaluator.py:128-135`），以及「level 1/2 才算急迫」（`ttas_evaluator.py:99,124`；`recommend.py:63`），兩者都沒有 source_id（§3.B、§3.G）。[測試名稱] `test_phase5_ttas_rules.py:25,112-160`。 |
| 6 | 每條 rule 保留官方 code、criterion、level、required evidence、source_id | 部分符合 | [資料腳本] 統計見下；loader 必填檢查 `ttas_rule_loader.py:164-185` | 共 64 條（enabled 28、reference_only 36）。缺漏數：`rule_id` 0、`ttas_level` 0、`required_fields` 0、`source_id` 0（全部在 manifest 內）、`official_locator` 0、`predicate` 0；**`official_code` 為 null 25 條**（enabled 7：`TTAS-MOD-UNCONSCIOUS`、`-ALTERED-CONSCIOUSNESS`、`-TEMP-HIGH-EXTREME`、`-TEMP-LOW-EXTREME`、`-ADULT-FEVER-IMMUNOCOMPROMISED`、`TTAS-PED-FEVER-LT3M`、`-GT3Y-IMMUNE`，`active_rules.json:109,129,149,167,185,477,564`；reference_only 18）；**沒有獨立的 criterion 文字欄位（64/64）**，criterion 只以機器 `predicate` 加上 `official_locator`（主訴／表名，例如「A0405 抽搐」）表示，沒有官方條文原文。enabled 規則 `required_fields` 與 predicate 欄位一致（不一致 0 條）。loader 不要求 `official_code`（L164-185）。 |
| 7 | deterministic evaluator 完全不讓 AI 決定 level | 符合 | `ttas_evaluator.py:68-91`；`ttas_evidence.py:30`；`rag_triage_adapter.py:285-290,585,605` | [讀碼] level 只取自 rule 的 `ttas_level`（L74），結果取最小值（L79,89）。AI 輸出若含 level／urgency_score 等頂層欄位 → 整輪 `invalid_turn_interpretation_fields`（L289-290）；evidence item 多 key 即丟棄。evaluator 不讀 raw text（README；[測試名稱] `test_phase5_ttas_evaluator.py:208`）。附註：AI 仍可經由 evidence 的 value 間接影響結果（#4 缺口 a）。[測試名稱] `test_phase5_ttas_evidence.py:81,111`、`test_phase5_ttas_evaluator.py:130,162`。 |
| 8 | 資料不足時 level=null、status=insufficient_information；未知生命徵象不被當正常 | 部分符合 | `ttas_evaluator.py:35-58,80-92,152-154`；`ttas_evidence.py:108-140` | [讀碼] 欄位不存在 → predicate `unknown`（L153-154），不視為 false；需年齡的規則在年齡未知時跳過並記 `age_years\|age_months`（L47-49）；沒有任何規則完整命中 → `level_candidate=None, status="insufficient_information"`（L80-86）；rule 載入失敗也是 insufficient（L27-33）。`available` 狀態不可配 null／`"unknown"`（`ttas_evidence.py:114-140`）。**缺口**：只要有任一較低級規則命中（例如 A031017 少量血便 level 4、A030713 level 3），即回 `status="matched"`、`level_candidate=3/4`，即使 GCS、體溫等可能導向 level 1/2 的規則因資料未知而無法判斷；未知欄位只記在 `missing_evidence`（L54-55,91）。`missing_evidence` 在 `backend/app` 沒有任何下游讀取（grep 只有 evaluator 本身），也沒有追問流程（計畫 L1109「必要時追問」未實作）。結果上等於「較高級條件未知 → 以較低級結果繼續一般流程」。SpO2、BP、HR 欄位在 schema 內，但 enabled 規則只使用 `gcs` 與 `temperature_c`（[資料腳本]），所以前三者不會被任何 enabled 規則判讀。[測試名稱] `test_phase5_ttas_evaluator.py:156,183,193`、`test_phase5_ttas_evidence.py:71`。 |
| 9 | 每個 TTAS 判斷輸出 official rule id、使用者 evidence、資料來源、結果 | 部分符合 | `schemas.py:132-152`；`ttas_evaluator.py:59-77,100-103`；`routes/chat.py:594-603` | [讀碼] matched 時每條規則輸出 `rule_id`、`official_code`、`source_id`、`official_locator`、`level`、`evidence_references[field, value, source_text]`；隨 `TriageResult.triage_case.ttas_result` 回傳，`triage_reasons` 另附 `TTAS official rule matched: <rule_id> (level N).`。差異：①enabled 7 條的 `official_code` 為 null（#6）；②「資料來源」只有 `source_id`，URL 需再查 manifest；`ruleset_id`／package 版本沒有放進結果（`ruleset_id` 只在 loader 使用）；③insufficient 結果沒有 rule id（設計上如此），只有 `missing_evidence` 與英文 warning。 |
| 10 | TTAS 判定急迫時任何時點直接走急迫流程，不受科別收斂、輪數上限、確認流程影響；`/recommend` 擋急迫 | 部分符合 | `routes/chat.py:185-188,279,296-299,333-347,475-483`；`recommend.py:59-69` | [讀碼] 同一輪中 TTAS 評估（L299）先於科別推理（L376）與 `advance_conversation`（L396），urgent 成立時兩者都不執行，所以不受科別收斂與 `HARD_TURN_CAP`／`ABSOLUTE_TURN_CAP` 影響。安全篩檢回合同樣先評估（L299）。已確認後再輸入文字 → 自動修訂（L196-198、L774-792）→ 再評估。`req.confirmed=True` 時 urgent gate 先於確認 gate（L475 先於 L502）。`/recommend` 兩個條件都擋（L59-69），userQuery 建案路徑也先評估再擋（L38-50）。**反例**：`clarification_status=="unresolved"` 後 `semantic_ai_allowed=False`（L185-188），之後使用者再描述急迫症狀時**不會呼叫 Turn Interpreter，也不會產生新 TTAS evidence**；L299 只會以舊 evidence 重算，回覆為 `UNRESOLVED_REPLY`（「請改用手動選科…」，`conversation_service.py:29`）或 `SAFETY_UNRESOLVED_REPLY`（L23-26）。這與計畫 L712／L759「任何時點優先」不一致（T2）。[測試名稱] `test_phase2_conversation.py:235`、`test_phase5_ttas_evidence.py:305`、`test_case_resume.py:110`。 |
| 11 | 否定、更正、不確定語句如何影響 TTAS evidence；由誰判定 | 部分符合 | `ttas_evidence.py:73-77`；`rag_triage_adapter.py:600-603,609`；`evidence_catalog.json:67-68,361` | [讀碼] **判定者是 AI**：TTAS evidence 沒有 `assertion` 欄位，否定以 value 表示（例如 `fever=false`、`seizure_status="none"`），不確定以 `semantic_status` unknown／ambiguous 表示；Backend 沒有否定詞表（§3.A）。**Backend 合併規則**：同欄位以最後一筆有效 evidence 為準（L73-77，依 list 順序覆蓋，不比較 confidence）；最新一筆若為 unknown／ambiguous，會讓該欄位從 `values` 消失（`ttas_evaluator.py:36-40`）。因此「之前以為會…其實不會」若被 AI 抽成同欄位 false，會覆蓋舊的 true，可能讓急迫降級；在 urgent terminal 下降級後的狀態見 §3.D（T3）。prompt 對 TTAS 的否定／更正沒有獨立條文；L609 的更正規則是針對 semantic_extractions；catalog 只有部分欄位說明「明確否認」（`fever`、`ruptured_membranes`）。實際 AI 輸出無法確認。 |
| 12 | AI／逾時／格式錯誤時的急迫判斷行為；是否把「判斷失敗」當「不急」 | 部分符合 | 見下表 | [讀碼] 多數分支不會進入一般掛號（fail closed），但有 4 個分支在判斷失敗時，流程與「不急」相同或遺失急迫 evidence（下表 ★）。 |

**統計：符合 4（#1,2,3,7）／部分符合 8（#4,5,6,8,9,10,11,12）／不符合 0／無法確認 0。**

#12 失敗分支逐一列出（[讀碼]）：

| 失敗情況 | 位置 | 本輪 TTAS 結果 | 最終狀態 | 是否等同「不急」 |
|---|---|---|---|---|
| runtime AI 不可用 | `chat.py:181-188`；`rag_triage_adapter.py:67-69` | 無新 evidence；以舊 evidence 重算 | 一般回合：`TECHNICAL_FALLBACK`、COLLECTING；安全回合：每輪 `safety_answer_attempts+1`（`conversation_service.py:477`），3 次後 `unresolved`＋`SAFETY_UNRESOLVED_REPLY` | 否，不會進入掛號；但回覆不含急迫警示。[測試名稱] `test_phase5_ttas_evidence.py:403,429,458` |
| provider 例外／逾時 | `rag_triage_adapter.py:77-91` 回 None | 同上 | `interpretation_complete` 視為 False（`chat.py:356-359`），不做科別推理；技術性 fallback | 否，但 ★ 本輪原文中的急迫事實**永久遺失**：之後的 evidence 必須以「當輪」原文為來源（`ttas_evidence.py:40-42`；prompt L600），除非使用者重述 |
| 頂層非 JSON | L80-84 → `data={}` → repair | repair 成功則正常；失敗 → `ttas_evidence=[]` | `interpretation_complete=False`（L147-155、L169-177） | 同上 ★ |
| 首輪結構不合格、repair 也不合格 | L121-177 | 首輪中即使有合格的 TTAS evidence 也一併丟棄（只在 L211 才套用） | 同上 | 同上 ★ |
| 單筆 TTAS item 不合格（未逐字、confidence<0.55、型別錯） | `ttas_evidence.py:29-48` 靜默略過 | 該筆不存在；**不觸發 repair**（`_turn_interpretation_rejection_reason` 不檢查 TTAS item 被丟棄，L272-318） | 其餘部分照常，可繼續科別推理與安全篩檢 | ★ 是：該急迫事實被當作沒說過，流程照一般路徑走 |
| rule 套件讀取／驗證失敗 | `ttas_rule_loader.py:32-207` raise → `ttas_evaluator.py:27-33` insufficient；`ttas_evidence.py:22-25` 回 [] ；prompt catalog 為空（`rag_triage_adapter.py:565-568`） | `insufficient_information`＋warning | 對話流程**照常**：安全篩檢陰性＋科別 resolved 後可進入確認與 `/recommend`（`recommend.py:59-69` 只擋 level 1/2） | ★ 是：「規則無法執行」在流程上等同「不急」，只在 `ttas_result.warnings` 留紀錄 |
| `clarification_status=="unresolved"` 之後 | `chat.py:185-188` | 不再抽取 | `UNRESOLVED_REPLY`／`SAFETY_UNRESOLVED_REPLY` | 不進掛號，但新急迫症狀不會被判讀（#10 反例，T2） |
| 已在 urgent terminal，後續 AI 失敗 | `chat.py:299,333` | 以已存 evidence 重算，仍 level 1/2 | 維持 urgent | 否 |
| `asyncio.CancelledError` | 沿用 AUDIT_PHASE4 §3.E：重新拋出 | — | 請求中止，`save_case`（L519）不執行 | 否 |

## 3. 特別檢查項目

### A. production 中對「患者自由文字」做 keyword／regex／phrase-list 判斷的路徑（[讀碼]）
- grep `re\.(compile|search|match|fullmatch|findall|sub|split)\(` 於 `backend/app`：共 14 處，與 AUDIT_PHASE4 §3.A 清單相同（`conversation_service.py:32-52,337`、`department_reasoning_service.py:21`、`department_knowledge.py:43,113,195`、`rule_engine.py:393`），全部是 AI 輸出品質 guard、ID／日期格式或 KB 正規化，不判斷患者語意。**TTAS 三個模組（`ttas_evaluator.py`、`ttas_evidence.py`、`ttas_rule_loader.py`）沒有任何 regex。**
- grep 中文症狀字面值（胸痛、喘、呼吸、昏、意識、抽搐、出血、吐血、黑便、頭痛、發燒、骨折、懷孕、過敏、中風、無力）搭配 `in`／`==`：0 筆。
- 所有 `x in text` 型比對（`ttas_evidence.py:41,102`、`rag_triage_adapter.py:251,482,492,517`、`conversation_service.py:390,607,773`、`department_preference_service.py:54`、`department_reasoning_service.py:47,292`、`time_preference_service.py:72`）都是「AI 給的 source_text 是否逐字出現在原文」的 grounding 檢查，不判斷語意。`schedule_filter.py:47`（SQL session 欄位）、`voice_client.py:315` 非患者文字。
- `rule_engine.py:29-41` `apply_user_message` 只記錄原文。
- **結論：** 未發現以 keyword／regex／phrase-list 判斷患者急迫或症狀語意的 production 路徑。

### B. 手寫醫療規則殘留（[讀碼]）
1. **安全篩檢固定問句的症狀清單**：「突發胸痛、嚴重呼吸困難、意識不清、大量出血、半邊無力或劇烈頭痛」（`question_specs.py:40-49`、`clarification_engine.py:6`）沒有 source_id。它只作為問句，回答由 AI 解讀，Backend 只接受「整份明確否認」完成篩檢（`conversation_service.py:484-525`），不是判級規則。但與 enabled 規則對照（[資料腳本]）：胸痛（A020210/A020211）、呼吸困難（MOD-RESP-*）、半邊無力（A041011/A041017）全部是 `reference_only`；「意識不清」只能經數值 GCS 觸發；「大量出血」只涵蓋吐血／血便（A030910、A031010）；只有「劇烈頭痛」對應 enabled A040410。患者對前 4 項回答「有」時：`answer_assertion=present` 不會完成篩檢，也不會產生 TTAS 急迫，系統會重問（AI 問句或 `SAFETY_RETRY_REPLY`「目前尚無法可靠理解這次安全回答…」，`conversation_service.py:27,542-549`），最多 3 次後 `unresolved`（L534-541）。[測試名稱] `test_backend_flow.py:91-101`（「突發胸痛而且呼吸困難」斷言 `warning_required=False`、insufficient）。（T4）
2. **成人／兒童分界**：`years >= 18`、`months >= 216`（`ttas_evaluator.py:131-134`），寫在 Python，rule data 中沒有對應的 source_id。
3. **急迫門檻**：「level ∈ {1,2} 才警示並終止流程」（`ttas_evaluator.py:99,124`；`recommend.py:63`），寫在 Python。level 3～5 命中時沒有任何差別處理，照一般流程。
4. **其他**：`rag_triage_adapter.py:590` severity 正規化（mild／moderate／severe）屬語意正規化，未被 TTAS 使用；`ttas_evaluator.py:111` 與 `chat.py:58-61` 是固定警示文案。Python 中**沒有**生命徵象數值門檻；GCS 3-8／9-13、體溫 >41／<32 等數值都只存在 `active_rules.json`。

### C. 官方 rule 可追溯性（[資料腳本]；未連網）
- manifest 15 個來源，全部是 `https://`：`www.mohw.gov.tw` 9 個、`dep.mohw.gov.tw` 5 個（衛福部官方網域），`www.sem.org.tw` 1 個（`sem_ttas_policy_2019`，台灣急診醫學會，role=`professional_policy_background`，`source_manifest.json:128-133`）。
- 64 條 rule 引用的 source_id 只有 6 個，全部在 mohw 網域：`mohw_ttas_105_adult_nontrauma` 22、`mohw_ttas_105_general` 19、`mohw_ttas_105_trauma` 10、`mohw_ttas_107_general_patch` 6、`mohw_ttas_105_environment` 5、`mohw_ttas_108_adult_nontrauma_patch` 2。`sem_ttas_policy_2019` 沒有被任何 rule 引用。兒童表（`mohw_ttas_105_pediatric`、`mohw_ttas_107_pediatric_patch`）也沒有被任何 rule 引用；兒童發燒規則引用的是 `107_general_patch`。
- loader 只檢查 URL 以 `https://` 開頭（`ttas_rule_loader.py:157-158`），錯誤訊息寫「official HTTPS URL」，但**沒有網域白名單**。
- `old_ailogic_crosswalk.json` 只供審查，`backend/app` 沒有讀取（grep `crosswalk` 無結果）。
- URL 內容是否真的是對應表格、rule 數值是否與官方原文一致：**未能確認**（§5）。

### D. 急迫結果的終止狀態（[讀碼]）
- **進入 urgent 時**（`chat.py:334-347`，再由 L475-483 確認）：`red_flags_checked=True`、`red_flags_status="positive_ttas"`、`clarification_status="urgent"`、`is_complete=True`、`stage=DONE`、`awaiting_confirmation=False`、`conversation_state.confirmed=False`、`case.confirmed=False`、`need_more_info=False`、`next_question=None`、`is_final=True`；存於 in-memory store（L519）。回覆為固定警示，不經 AI（L554-557,569）。`/chat/{id}/resume` 視為 terminal、`can_continue=False`（`case_resume_service.py:13-14`）。
- **之後只送 `confirmed=true`、不帶文字**：`preserve_urgent_terminal`（L275-277）→ 不重算（L298）→ 維持 DONE、`confirmed=False`。[測試名稱] `test_phase5_ttas_evidence.py:490`。
- **之後再送文字**：`clarification_status` 不是 unresolved → 仍呼叫 Turn Interpreter（L185-188）→ evidence 累加後重算（L299）。舊 evidence 沒被同欄位新 evidence 覆蓋時，仍是 level 1/2 → 維持 urgent。
- **可能回到一般掛號流程的路徑**（T3，未實測）：若新 evidence 覆蓋了觸發欄位（false／none／unknown，見 #11），level 不再是 1/2 → 走一般分支（L348-408），`advance_conversation` 把 `clarification_status` 改為 clarifying 等狀態。因為進入 urgent 時已把 `red_flags_checked=True`（L335），`safety_screen_resolved()` 回 True（`conversation_service.py:55-56`），`_enter_safety_check` 會被跳過（L620）；之後科別 resolved → WAITING_CONFIRMATION → 確認 → `/recommend`（L59-69 不再擋，L93 安全篩檢視為已完成）。也就是說，**使用者從未明確否認整份安全篩檢，也可能進入掛號推薦**。未找到涵蓋「urgent → 更正降級」的測試。
- **`revision_requested=True`**：`_begin_revision`（L743-771）把 `clarification_status` 改回 collecting、stage 改回 COLLECTING，但不清 `ttas_evidence`、`ttas_result`、`triage`、`red_flags_checked`，並在 L147-175 直接回應。此時 `/recommend` 仍被 `ttas_result` level 1/2（`recommend.py:62-63`）與 `is_complete=False`（L91）擋住；下一輪文字會重算並回到 urgent（evidence 未變時）。
- **範圍外觀察（`/generate_script`）**：`routes/generate_script.py:12-60` 沒有 urgent 檢查；`_RECOMMENDATIONS_BY_CASE`（`case_store.py:139-149`）在修訂或進入 urgent 時不會被清除。若案件先取得推薦、之後才變成 urgent，用舊 `recommendation_id` 呼叫 `/generate_script` 仍會產生劇本，並把 stage 設為 SCRIPT_READY（L59）。下一次 `/chat` 帶文字會因 SCRIPT_READY 觸發自動修訂再重算回 urgent。未實測（T11）。
- **legacy batch（`BATCH_TRIAGE_ENABLED=false`）**：非對話模式不會設定 `clarification_status="urgent"`（L300-309、L409-410），所以 `_is_urgent_terminal` 不成立。實際上只要有自由文字就會切到對話模式（`batch_extraction_service.py:47-48`；`chat.py:223-230`），只剩「全部是結構化選項」的 batch 回合會留在非對話模式，而這類回合不會新增 TTAS evidence。目前設定下不會進入。

### E. 文案（[讀碼]）
- 急迫回覆：「目前 TTAS 初步篩檢結果較急迫，請儘速由醫療人員評估；如症狀持續惡化請尋求緊急醫療協助。」（`ttas_evaluator.py:111`）；fallback：「目前初步急迫性篩檢結果較急迫…」（`chat.py:58-61`）。兩者都使用「初步篩檢」，沒有宣稱診斷，也沒有宣稱取代急診正式檢傷。level 1 與 level 2 使用同一句。
- 回覆文字沒有明示「不能取代急診正式檢傷」；這段定位只出現在 `knowledge/ttas/README.md`、`active_rules.json:3`、`source_manifest.json:4`，不是 API 文案。
- `triage_reasons` 以英文輸出 `TTAS official rule matched: <rule_id> (level N).`、`TTAS evidence is insufficient; no urgency level was inferred or defaulted.`（`ttas_evaluator.py:100-105`），會出現在 API 回應中。
- `SAFETY_UNRESOLVED_REPLY`（`conversation_service.py:23-26`）會建議由醫療人員評估；`UNRESOLVED_REPLY`（L29）只建議手動選科，不含安全提示（關聯 #10 反例）。
- grep `backend/app` 中「確診／診斷為／你患有／你得了」只出現在禁止 AI 追問使用的 guard（`conversation_service.py:33`）。

### F. dead code（無 production caller）
方法：對 Phase 5 範圍每個 `def`／常數，以 `grep -rnw <name> backend/app --include=*.py`（排除定義行）計數；計數為 0 的再 grep `backend/tests`。

| 項目 | 位置 | `backend/app` | tests 引用 | 判定 |
|---|---|---|---|---|
| `evaluate_urgency` | `services/rule_engine.py:44-71` | 0 | `test_backend_flow.py:17,94`（`test_high_urgency_red_flag`）、`test_phase42_backend_hardening.py:38,553`（`test_info_logs_do_not_contain_patient_health_text`）；`test_phase5_ttas_evidence.py:420` 只斷言 `chat_route` 沒有此屬性，不受刪除影響 | **確認為 dead**（與 AUDIT_PHASE2 §3.4／F2 一致）。可刪；連動需改 2 個測試：把 `evaluate_urgency(case)` 換成 `apply_ttas_evaluation(case)`（兩個測試的斷言只用到 TTAS 結果與 log 內容） |
| `clear_ttas_rule_cache` | `services/ttas_rule_loader.py:210-211` | 0 | `test_phase5_ttas_rules.py`（10 處） | 只有測試使用的 cache 重設介面；刪除會讓 rule 測試無法重置快取。**不列為可刪** |
| `UrgencyNormalization`、`SeverityNormalization` | `schemas.py:155-175,192-193` | 只有 `case_store.py:11-13,73-74` 重設 | `test_phase42_backend_hardening.py:28-30,184-213` | 沒有邏輯讀取，但屬 API schema（`triage_case.patient_input`）；刪除＝改 schema，**需使用者同意**（T10） |
| `range_pair` operator | `ttas_rule_loader.py:12,58` | evaluator 沒有對應分支，會回 unknown（`ttas_evaluator.py:169-170`） | — | 只被 2 條 reference_only 規則使用（[資料腳本]），enabled 規則不會用到。不是 dead code，屬未實作的 operator，不建議動 |
| 其他（`evaluate_ttas`、`apply_ttas_evaluation`、`ttas_requires_immediate_action`、`_population`、`_evaluate_predicate`、`_missing_required_fields`、`validate_ttas_evidence_items`、`apply_ttas_evidence`、`effective_ttas_evidence`、`_stored_evidence_valid`、`_value_matches_spec`、`_read_json`、`_validate_predicate`、`_predicate_fields`、`_build_prompt_evidence_catalog`、`_evidence_type`、`load_ttas_rules`、`TTASRuleSet`、`_is_urgent_terminal`、`URGENT_WARNING_FALLBACK`） | — | ≥1 | — | 都有 production caller，不是 dead |

- `USE_TTAS_TRIAGE`：只有效能 log 讀取（§0），不是程式碼，不列入刪除；列為文件澄清。
- voice／TTS 共用程式（`routes/voice.py`、`fixed_sentences.py`、`tts_cache.py`、`question_specs.py`、`clarification_engine.py`）**一律不列為可刪**；§3.B-1 的安全問句雖在 `question_specs.py`／`clarification_engine.py`，本報告只陳述事實，不建議刪除。

### G. 硬編碼（[讀碼]）
- **TTAS 等級**：`{1, 2}` 寫死 3 處（`ttas_evaluator.py:99,124`；`recommend.py:63`）；loader 的 `1 <= level <= 5`（`ttas_rule_loader.py:177`）屬格式檢查。
- **年齡分界**：18／216（`ttas_evaluator.py:132,134`）。
- **dept_id／科別名稱**：TTAS 路徑（`ttas_*.py`、`chat.py` urgent 分支、`recommend.py:59-69`）中沒有 dept_id 或科別名稱；grep 四位數與「…科」字面值的結果與 AUDIT_PHASE4 §3.H 相同（無）。
- **rule_id**：Python 中沒有寫死任何 rule_id 或 official_code（grep `TTAS-[A-Z]|[ATE][0-9]{6}` 於 `backend/app/**/*.py`：0 筆）。

### H. 目前設定值
見 §0。補充：沒有任何 TTAS 專用的 config 開關；`USE_TTAS_TRIAGE=true` 不影響行為；rule 套件路徑固定、不可經由 env 覆寫（`ttas_rule_loader.py:10`）。

## 4. 需要修復的項目

| # | 項目 | 預估 production 檔案數 | 對照最小修改原則與 Cleanup Gate |
|---|---|---|---|
| T1 | 刪除 dead `evaluate_urgency`（§3.F） | 1：`rule_engine.py`（＋測試 2 檔：`test_backend_flow.py`、`test_phase42_backend_hardening.py`） | **可刪 dead code**，屬 §14 明列「無 production caller 的 dead path」，適用 Cleanup Gate；建議與 AUDIT_PHASE2 F2 一起處理。刪除前需重跑 grep。 |
| T2 | `clarification_status=="unresolved"` 後不再抽取 TTAS evidence，新急迫症狀不會被判讀（#10 反例、#12） | 1：`routes/chat.py`（L185-188 的條件） | 涉及 state machine 與 AI 呼叫條件，且與計畫 L712／L759「任何時點優先」有落差；未實測證實。**需使用者同意，不主動修。** |
| T3 | urgent 被更正降級後，因 `red_flags_checked=True` 已設，會跳過安全篩檢而回到一般掛號（§3.D、#11） | 1～2：`routes/chat.py`（可能加上 `conversation_service.py`） | 改 state machine；只由讀碼推得，未實測、無測試。**需使用者同意，不主動修。** |
| T4 | 安全篩檢問句列出的 6 項中有 4 項沒有 enabled 規則；患者回答「有」時不會急迫警示，只會重問最多 3 次後 unresolved（§3.B-1） | —（可能牽涉 rule data、問句或 state machine） | 補規則＝改 TTAS 官方 rule 執行範圍（目前是刻意設為 reference_only）；改問句／流程＝改 state machine。**需使用者同意，不主動修。** 不得自行補醫療規則。 |
| T5 | rule 套件載入失敗時，流程照常可進入掛號（#12 ★） | 1～2：`routes/chat.py`、`routes/recommend.py` | 改 workflow gate（state machine）；未實測。**需使用者同意，不主動修。** |
| T6 | 急迫 evidence 遺失：單筆 TTAS item 不合格時靜默丟棄且不觸發 repair；Turn Interpreter 失敗的那一輪原文中的急迫事實之後無法再被引用（#12 ★） | 1：`rag_triage_adapter.py` | 牽涉 Turn Interpreter 契約／repair 條件；未實測。**需使用者同意，不主動修。** |
| T7 | Backend 不驗證 TTAS evidence 的 value 與 source_text 是否一致（#4a）；白名單是 schema 49 欄而非 enabled 24 欄（#4b）；`trusted_profile` 標籤與來源不符（#4c） | 1：`ttas_evidence.py` | 若要檢查數值是否出現在原文，等於 Backend 解析患者文字，與 Cleanup Gate「Backend 不解析患者語意」的方向衝突，需先決定設計。未實測。**列入待辦，不主動修。** |
| T8 | 較高級條件資料未知時，仍以較低級命中結果輸出 `matched`；`missing_evidence` 沒有下游使用，也沒有追問（#8） | ≥2（evaluator 輸出語意＋對話流程） | 改 TTAS 判定語意與 state machine。**需使用者同意，不主動修。** |
| T9 | rule 可追溯性：enabled 7 條 `official_code=null`；64 條沒有 criterion 原文欄位；結果不含 `ruleset_id`；兒童來源檔未被引用（#6、#9、§3.C） | 資料檔 1～2（`active_rules.json`）＋可能 `schemas.py`／`ttas_evaluator.py` | 改 TTAS rule data／API schema。**需使用者同意，不主動修；不得修改官方 rule 內容。** |
| T10 | schema 殘留：`UrgencyNormalization.urgency_level` 預設 `"low"`、`matched_red_flags`、`UrgencyResult.urgency_score/urgency_level` 恆為 None，但仍在 API 回應中（#2、§3.F） | 2：`schemas.py`、`case_store.py`（＋測試 1 檔） | 改 API schema。**需使用者同意，不主動修。** |
| T11 | `/generate_script` 沒有 urgent gate，且舊推薦不會被清除（§3.D，範圍外） | 1～2：`routes/generate_script.py`（或 `case_store.py`） | 不在本次審查範圍；未實測。**列入待辦，不主動修。** |
| T12 | 寫在 Python 的醫療相關判斷沒有 source_id：年齡 18／216、急迫門檻 level {1,2}（§3.B-2、3；§3.G） | 1～2：`ttas_evaluator.py`、`recommend.py`（若搬到 rule data，另加資料檔） | 搬移到 rule data＝改 TTAS rule 套件結構。**需使用者同意，不主動修。** |
| T13 | loader 的來源 URL 只檢查 `https://`，沒有網域白名單（§3.C） | 1：`ttas_rule_loader.py` | 新增驗證，不是 regression；未證實造成錯誤。**列入待辦，不主動修。** |

## 5. 未能確認的項目與限制
- 未執行 pytest；所有 [測試名稱] 只代表測試存在，不代表通過。
- **需 310 真 Cerebras：**
  - AI 是否確實為每個 enabled 欄位輸出逐字 source_text 與 ≥0.55 的 confidence；單筆被靜默丟棄（T6）的實際頻率。
  - 否定、更正、不確定語句（「沒有喘」「之前以為會痛，其實不會」）的實際抽取結果，以及 urgent 降級（T3）是否真的會發生。
  - AI 是否遵守 prompt L603，不把「叫不醒」「很燒」換算成 GCS／體溫（T7 的實際風險）。
  - 安全篩檢中回答「有胸痛」等情況的實際對話體驗（T4）。
  - Turn Interpreter 在 60 秒 timeout 下的逾時頻率。
- **需對照官方文件（未連網）：**
  - manifest 15 個 URL 的內容是否與標題、版本相符；`later_official_revision_found=false`（`source_manifest.json:11`）是否仍成立。
  - 64 條 rule 的 level、predicate 數值（GCS 3-8／9-13、體溫 >41／<32、兒童 <3 個月 >38／<36、懷孕 >20 週、宮縮 ≤2 分鐘等）與 `official_code` 是否和官方表格一致。
  - enabled 7 條 `official_code=null` 的調節變數規則，官方表格是否本來就沒有代碼。
  - 成人／兒童以 18 歲分界是否符合官方定義。
  - `reference_only` 的 hardening 理由（例如呼吸窘迫需臨床評估）是否與官方說明一致。
- **需實測證實的狀態風險：** §3.D 的降級路徑（T3）、`/generate_script` 舊推薦（T11）、unresolved 後的急迫輸入（T2），目前只有讀碼推論。
- **其他限制：** in-memory case store 重啟或多 worker 時會遺失急迫狀態（`case_store.py` 設計已知，AUDIT_PHASE4 §3.C 風險 3）；legacy batch 路徑在目前設定下不會進入，未深入審查；Android 端如何呈現 `warning_message`、`ttas_result` 不在範圍內。
- 本報告不審查 Phase 6 推薦排序與 Phase 2.5 保底流程；目前 `HARD_TURN_CAP=8`、`ABSOLUTE_TURN_CAP=24` 只作為環境紀錄。
