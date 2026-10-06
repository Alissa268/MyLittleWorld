你現在只執行 New_Android_Backend 的 Phase 5：TTAS-based 急迫性重做。
不得開始 Phase 6。

基準：
- branch: New_Android_Backend
- Phase 4 已通過 310 live acceptance。
- 核心原則：AI 做推理，Rule 做裁判。
- 自由文字由 AI 理解；Backend 不新增醫療中文 keyword/phrase/regex 來猜患者意思。

我已提供 `phase5_ttas_source_package`，裡面有：
- source_manifest.json
- ttas_evidence_schema.json
- active_rules.json
- old_ailogic_crosswalk.json
- PHASE5_TTAS_SOURCE_AND_RULESET.md

這些是本次 Phase 5 的醫療資料依據。不要自行上網補規則，不要自行發明 TTAS criterion。
如果 package 與既有程式衝突，先依 package + 官方 code 保守 fail closed，不要猜。

==================================================
目標
==================================================

把目前 legacy：
- RED_FLAG_BUCKETS / RED_FLAG_PATTERNS 類自訂安全 keyword
- normalize_urgency() 自訂語意判級
- detect_red_flags()
- _score_duration
- _score_function_limit
- _score_onset
- _score_inflammation
- _score_treatment
- _score_risk
- 自訂 urgency_score 累加

從 primary runtime 急迫性判斷移除/停用。

改成：

User current turn
→ SAME Turn Interpreter
→ 一般 semantic_extractions + pending_answer + ttas_evidence
→ Backend validation
→ versioned TTAS rule data
→ deterministic TTAS evaluator
→ level_candidate / insufficient_information
→ Backend safety/workflow gate

不要新增第二次固定 TTAS AI call。

==================================================
一、先把資料包放入正式 knowledge
==================================================

建立：
backend/knowledge/ttas/

至少包含：
- source_manifest.json
- active_rules.json
- old_ailogic_crosswalk.json
- README.md

來源內容不要改寫成另一套醫療規則。
保留 source_id / official_code / official_locator / implementation_status。

`reference_only` 規則禁止 evaluator 執行。

==================================================
二、Schema
==================================================

新增 TTAS evidence model。

建議 generic shape：
- field
- value
- semantic_status
- confidence
- source_text

field/value allow-list 以 `ttas_evidence_schema.json` 為準。

新增 TTAS evaluation result，至少：
- status: matched | insufficient_information
- level_candidate: 1..5 | null
- matched_rules
- missing_evidence
- warnings

matched rule 要能回：
- local rule_id
- official_code（若官方表有）
- source_id
- evidence refs / source_text

AI 不得輸出：
- TTAS level
- urgency_score
- warning_required
- red_flags_checked
- workflow state

==================================================
三、Turn Interpreter
==================================================

沿用目前 Phase 4 的 SAME Turn Interpreter，不另開常態 TTAS AI call。

輸出 contract 增加：
`ttas_evidence: [...]`

Prompt 說明：
- 只抽本輪患者明確表達的 TTAS evidence。
- 每筆 source_text 必須是本輪最短連續逐字片段。
- 未提供的血壓、SpO2、GCS、心率、體溫、血糖等必須保持 unknown / 不輸出，不得假設正常。
- 不准自己判 TTAS 級數。
- 不准把「看起來正常」補成生命徵象正常。
- 年齡若由 current turn 明確提供可 grounded extraction；否則不要猜。

保持 Phase 4：
- semantic_extractions
- pending_answer
- repair 行為
- department evidence
不回歸。

==================================================
四、TTAS evidence validation
==================================================

Backend 只做：
- field allow-list
- type/range
- finite numeric
- confidence threshold
- source_text grounding
- semantic_status validation

不得新增：
if "胸痛" in text
if "耳鳴" in text
if "喘" in text
之類自由文字醫療理解。

數值 evidence 必須有患者原文 grounding。

==================================================
五、年齡
==================================================

正式 schema 增加 optional：
- age_years
- age_months

來源只能：
1. 已可信 profile/context（若目前系統沒有，不要虛構）
2. AI grounded evidence（患者本輪真的說年齡）

禁止 deterministic regex 從 raw patient text 猜年齡作 TTAS 判斷。

年齡未知：
- age-dependent pediatric/adult-specific numeric rule 不執行
- 不准預設 adult
- missing_evidence 可記錄 age

不要在這一 Phase 重做整個 Android profile。

==================================================
六、Rule loader + evaluator
==================================================

建立 TTAS rule loader：
- 啟動後/首次使用載入 JSON
- validation fail closed
- duplicate rule_id / invalid level / missing source_id → fail closed
- 只執行 implementation_status=enabled

evaluator：
- deterministic
- 不看 raw user text
- 只看 validated TTAS evidence + trusted case facts
- 多規則命中取最急迫，也就是最小 level 數字
- 每個 matched rule 保留 trace
- required evidence unknown 不得當 false
- 沒有完整命中的 supported rule：
  status=insufficient_information
  level_candidate=null
- 絕對不要「沒命中就 level 4/5」

`reference_only` 不得執行近似 predicate。

==================================================
七、現有 safety workflow
==================================================

Phase 4 的 safety-first ordering 必須保留。

但判斷來源逐步換成 TTAS evaluator。

注意：
- `red_flags_checked` 是 Backend workflow state，AI 不可直接設定。
- 不要因為一次沒有 TTAS match 就當 safety negative。
- 若需要使用者知道的關鍵資訊，讓現有 clarification planner 產生自然問題。
- 不要一次問完整生命徵象問卷。
- 只問目前能改變安全 disposition 且使用者可能知道的一個資訊。

現有 A1 → safety question → negative answer → department convergence 的 Phase 4 regression 必須保持 PASS。

==================================================
八、Urgency compatibility
==================================================

不要再使用舊 90/50/20 score 作醫療判斷。

若既有 API schema 暫時需要 `urgency_score`：
- 設為 null
- 不要用 TTAS level 再自行發明 score mapping

`urgency_level` 若既有 Android contract 暫時不能一次移除：
- 不可聲稱它等於完整 TTAS
- 優先新增 TTAS result 欄位，舊欄位只做相容輸出
- 在報告/註解清楚標示相容層

不要偷偷把：
TTAS 1/2=high, 3=medium, 4/5=low
當新的醫療 truth 後又沿用舊自訂 scoring。

==================================================
九、舊 AILogic 不得照抄
==================================================

`old_ailogic_crosswalk.json` 已指出舊 prompt 有差異，例如：

- 舊版低血糖 <40 錯誤；package 依官方 A130409/A130413 為 <60 條件。
- 舊版高血壓 >200/110 無症狀一律第三級錯誤；官方有 A020409 / A020411 分層。
- 90/50/20 非官方 TTAS。
- 未命中舊清單不可直接 low。
- 108 修正中風時間為 6 小時。

這些 regression 必測。

==================================================
十、不要碰
==================================================

本 Phase 不改：
- Phase 3 department KB
- Phase 4 department convergence
- candidate Top-K logic
- SQL department mapping
- doctor recommendation
- specialty scoring
- schedule filtering
- Android conversational UX
- Phase 6
- Phase 7

也不要刪 legacy 檔案只為整理。
先讓 primary runtime 不再依賴舊急迫性醫療猜測即可。

==================================================
十一、測試
==================================================

至少新增：

A. Rule data
- source_id 都存在
- rule_id unique
- level 1..5
- reference_only 不會執行
- 108 override A041011/A041017 使用 6 小時

B. Evidence validation
- grounded valid evidence accepted
- ungrounded rejected
- unknown vital 不轉 normal
- AI 嘗試輸出 level/score 被忽略/拒絕
- invalid numeric range reject

C. Evaluator
- severe respiratory distress → level 1
- moderate respiratory distress → level 2
- mild respiratory distress → level 3
- shock → level 1
- altered consciousness → level 2
- extreme temperature supported cases
- multiple matches → choose most urgent

D. Official-code regressions
- A130409: glucose <60 + symptoms → 2
- A130413: glucose <60 without symptoms → 3
- 舊 <40 不能成為 hardcoded threshold
- A020409/A020411 high BP asymptomatic thresholds correctly distinct
  （目前 ruleset 內為 reference_only 時，先驗證「不可執行」，不要擅自完成近似 predicate）
- A040504 / A040511 / A040516 seizure states
- A041011 <6h → 2
- A041017 >=6h → 3
- A030713 acute persistent vomiting → 3
- E010509 chemical eye injury → 2
- E010809 toxic gas exposure without respiratory distress → 3
- open fracture vs deformity suspected fracture level distinction

E. Insufficient information
- missing needed vital → level null
- no supported official rule match → insufficient_information
- never default 4/5

F. Phase 4 non-regression
重跑：
- phase4 pending answer
- phase4 department convergence
- phase41 clarification
- phase2 conversation
並保留 A1/A2/A3 route behavior。

==================================================
十二、執行前先檢查
==================================================

請先閱讀：
- `phase5_ttas_source_package/PHASE5_TTAS_SOURCE_AND_RULESET.md`
- `source_manifest.json`
- `ttas_evidence_schema.json`
- `active_rules.json`
- `old_ailogic_crosswalk.json`
- 現在的 rag_triage_adapter.py
- rule_engine.py
- semantic_normalizer.py
- conversation_service.py
- chat.py
- schemas.py

不要在沒有讀資料包的情況下自行設計 TTAS。

==================================================
十三、完成後
==================================================

先不要 commit / push。

回報：

CHANGED FILES

LEGACY URGENCY PATHS REMOVED FROM PRIMARY RUNTIME

TTAS DATA FILES INSTALLED

TTAS EVIDENCE CONTRACT

TTAS EVALUATOR

AGE HANDLING

OFFICIAL RULE TRACE EXAMPLES

OLD AILOGIC DIFFERENCES FIXED

PHASE 4 NON-REGRESSION

TARGETED TEST RESULTS

FULL BACKEND TEST RESULTS

GIT DIFF STAT

STOP

完成後停止，不進 Phase 6。
