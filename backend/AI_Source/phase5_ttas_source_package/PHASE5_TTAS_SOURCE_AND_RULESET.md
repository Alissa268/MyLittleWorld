# Phase 5 TTAS 官方來源與實作邊界

## 結論

本資料包採「105.1.1 基礎版 + 107.5.1 總表/兒童修正 + 108.6.11 成人非外傷 A041011/A041017 修正」的版本組合。2026-10-05 查詢衛福部醫事司目前的 TTAS 修正專區，未找到 108.6.11 之後的新修正項目。

本專題的定位不是建立完整臨床 TTAS 系統，而是 **TTAS-based 急迫性初步篩檢**。

## 正確架構

使用者自由文字
→ 同一次 Turn Interpreter 抽一般語意 evidence + pending answer + TTAS evidence
→ Backend 做 schema / allow-list / source_text / confidence 驗證
→ versioned TTAS rule data
→ deterministic evaluator
→ level_candidate 或 insufficient_information

AI 不得直接輸出 TTAS 級數。

## TTAS 是什麼

衛福部說明，TTAS 是急診看診前用來判斷「急迫程度與看診優先順序」的工具，不等同疾病嚴重度，也不是住院優先標準。

五級名稱與建議候診時間：
1. 復甦急救：立即
2. 危急：10 分鐘
3. 緊急：30 分鐘
4. 次緊急：60 分鐘
5. 非緊急：120 分鐘

主要首要調節變數包含呼吸、血行動力、意識、體溫、疼痛與高危險性受傷機轉。

## 與 Phase 3 KB 的差別

Phase 3：
患者 evidence → 官方科別 KB → AI 比較候選科別

Phase 5：
患者 evidence → 官方 TTAS 規則資料 → deterministic evaluator

TTAS 不用向量搜尋，也不需要額外一個 AI call。

## 版本處理

- 105.1.1：總表、環境、外傷、成人非外傷、兒童為 base。
- 107.5.1：總表與兒童版為 override；尤其兒童生命徵象/年齡門檻不可再沿用 105 舊值。
- 108.6.11：成人非外傷 A041011/A041017 的中風症狀發作時間由 4.5 小時改為 6 小時。

不要把 patch PDF 當成完整規則表；應視為對 105 base 的版本覆蓋。

## 舊 AILogic 不能直接搬

舊 `AILogic/triage.py` 的「民眾衛教版」是簡化 prompt，不是可追溯的 production ruleset，而且至少存在兩個明顯差異：

1. 舊版寫低血糖 <40mg/dl；官方成人非外傷現行資料是 <60mg/dl 且有症狀為第二級，<60mg/dl 無症狀為第三級。
2. 舊版把 SBP>200 / DBP>110 無症狀概括成第三級；官方 A0204 其實依 ≥220/130 與 200-220/110-130 分成第三、第四級。
3. 舊版 90/50/20 urgency_score 不是官方 TTAS。
4. 「沒命中前面清單就 low」不可保留。規則未涵蓋或必要資料未知時，回 `insufficient_information`，不能假設正常。

完整差異見 `old_ailogic_crosswalk.json`。

## 年齡問題

兒童規則明顯依月齡/年齡分層。現在 New_Android_Backend 的 `PatientInput` 沒有正式 age 欄位，而 legacy adapter 仍有從 raw text regex 猜年齡的做法。

Phase 5 不可沿用這種做法。

建議：
- 加入 optional `age_years` / `age_months` 事實欄位（來源可為 profile 或 AI grounded extraction）。
- Backend 不從自由文字 keyword/regex 自行推斷年齡。
- 年齡未知時，跳過所有 age-dependent 規則；不得套成人數值門檻代替。

## 規則資料分層

`active_rules.json` 有兩種 `implementation_status`：
- `enabled`：目前證據模型可以直接且可追溯執行。
- `reference_only`：官方規則已確認，但 evidence schema / predicate 還不夠精確，不准先用近似邏輯硬跑。

這可以防止「為了支援全部 TTAS」而偷偷在 Backend 寫新的醫療猜測。

## 結果模型建議

TTAS evaluator 回：

- `status`: matched / insufficient_information
- `level_candidate`: 1..5 或 null
- `matched_rules`: rule_id, official_code（若官方有編碼）, source_id, evidence refs
- `missing_evidence`
- `warnings`

若無任何完整命中的已支援規則：
- 不得預設 level 4/5
- `level_candidate = null`
- `status = insufficient_information`

## 效能

不新增獨立 TTAS AI call。Turn Interpreter 一次回一般 evidence + TTAS evidence。
規則 JSON 在服務啟動時載入記憶體，Backend 本機比對即可，正常不應是效能瓶頸。
