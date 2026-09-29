# Phase 3 - 官方科別知識庫驗證

日期：2026-09-29  
工作目錄：`New_Android_Backend`  
使用者指定基準 commit：`eb163235a7b14d92e9fd473bfc854fd41a3fe771`

## 範圍與檔案

本階段只新增可追溯、唯讀的知識資料與驗證工具；沒有把 KB 接入 `/chat`、`detect_department_result()` 或推薦流程。

| 檔案 | 目的 |
| --- | --- |
| `backend/knowledge/sources.json` | 登錄官方來源、院所、類型、優先級、URL 與擷取日期。 |
| `backend/knowledge/vghtpe_department_guidance.json` | 21 筆短證據／概念及逐筆 provenance；所有 SQL 科別映射暫標 `unresolved`。 |
| `backend/app/services/department_knowledge.py` | 唯讀 loader、來源與記錄完整性驗證、正規化 exact-concept lookup、SQL 正式科別名稱的 exact-match 檢查；未在 production routing 呼叫。 |
| `backend/tests/test_department_knowledge.py` | 驗證來源、fallback、優先序、去重、未知院所及不可猜測 SQL 科別。 |
| `docs/PHASE2_CONVERSATIONAL_TRIAGE_VALIDATION.md` | 已通過驗收的施工紀錄，依要求刪除；本工作副本無法獨立檢查 Git 歷史。 |
| `docs/PHASE3_OFFICIAL_DEPARTMENT_KB_VALIDATION.md` | 本施工與來源核對紀錄。 |

`README.md`、`CONTRIBUTING.md` 與工作區上一層的 `修改計畫.md` 保留。沒有修改 Android、DB schema、SQL data、TTAS、doctor scoring 或 AI prompt。
此工作副本的 Git metadata 不可用，故無法獨立核對指定基準 commit；沒有 commit 或 push。

## 官方來源與科別

| Source ID／優先級 | 實際核對的官方頁面 | 本 KB 對應的官方科名 | 筆數 |
| --- | --- | --- | ---: |
| `vghtpe_im_department_reference_2022`／1 | [臺北榮總內科部「就診科別參考」](https://wd.vghtpe.gov.tw/im/Fpage.action?fid=243&muid=561)，頁面標示最後更新 2022-09-25；直接列出建議掛號科別與疾病、症狀。 | 一般內科、胃腸肝膽科、腎臟科、血液腫瘤科、感染科、心臟科、內分泌新陳代謝科、過敏免疫風濕科 | 19 |
| `vghtpe_doctor_specialty_search_2026`／2 | [臺北榮總「醫師及專長查詢」](https://www.vghtpe.gov.tw/docsearch.action)；僅取頁面明列的科部與專長關鍵字。 | 骨科部、耳鼻喉頭頸部 | 2 |
| `vghtc_symptom_query_2026`／3 | [臺中榮總「症狀查詢」](https://www.vghtc.gov.tw/SymptomQuery/605)；入口可確認，但動態查詢結果未能逐項核對。 | **無 production fallback 記錄** | 0 |

臺中榮總只可補北榮沒有的同項資料。現在沒有可核對的臺中「症狀 → 科別」結果，因此不把其他中榮衛教或醫師專長文章冒充症狀查詢資料，也不建立 70/30 等數學權重。`lookup_concept()` 對相同概念只保留最優先來源；測試以合成臺中 fixture 驗證 fallback 標記與北榮優先，合成資料不進 KB。證據文字每筆只保留短片段，沒有複製網頁全文。

## 科別映射狀態

唯讀 SQL `Department` master 查詢嘗試失敗（ODBC TCP 連線拒絕），故無法在此環境證實任何目前有效的 `dept_id`。現有 `android/app/src/main/assets/vgh_departments.json` 僅作本地名稱對照，不等於 live SQL：一般內科、胃腸肝膽科、腎臟科、血液腫瘤科、感染科為字面完全相同；心臟科／心臟內科、內分泌新陳代謝科／新陳代謝科、過敏免疫風濕科／過敏免疫風濕、骨科部／一般骨科及耳鼻喉頭頸部／耳科等只有近似或不同層級，**沒有推測別名對應**。即使本地資產同名，21 筆 KB 記錄仍一律標 `department_resolution=unresolved`，沒有填造 ID，也不得直接作為可掛號科別。`exact_db_department_ids()` 只有在外部提供正式 SQL row 且名稱唯一、ID 為正整數時才回報對應；不會改寫 KB 或啟動推薦。

## 舊詞庫逐科審核

舊 `feat-dept-doctor-recommendation/dept_keywords.py` 僅作待查 seed，AST 讀取得 35 科、763 個詞項，沒有 import 或複製其 mapping／匹配邏輯。下表「收錄」只表示該詞與**同名官方表列科別**逐字相符、已進 provenance KB；SQL 映射仍未解決，並非臨床驗證或可路由的科別規則。其餘詞項未進 KB，即使看起來合理也不補常識。

| 舊科別（詞項數） | 本次有官方直接表證據並收錄的詞項 | 其餘處理 |
| --- | --- | --- |
| 一般內科（23） | 發燒、頭暈、胸痛、腹痛 | 19 個未逐項納入 |
| 胃腸肝膽科（20） | 黃疸、便秘、腹瀉 | 17 個未逐項納入 |
| 腎臟科（10） | 蛋白尿、血尿、尿量減少 | 7 個未逐項納入 |
| 血液腫瘤科（29） | 貧血、紫斑症 | 27 個未逐項納入 |
| 感染科（12） | 寒顫、皮膚紅腫、小便疼痛 | 9 個未逐項納入 |

以下 30 個舊科別的詞項 **0 個遷入**；或未逐詞取得可核對的官方直接證據，或舊 key 與官方科名／本地科別目錄不一致。括號為舊詞項數，均排除於本次 production KB migration：

家庭醫學科(一般門診/戒菸)（38）、心臟內科（17）、新陳代謝科（36）、神經內科（23）、胸腔內科（9）、過敏免疫風濕（23）、腫瘤內科（16）、一般外科（9）、直腸外科（3）、心臟外科（29）、胸腔外科（11）、外傷兼疝氣及肝膽胃腸外科（1）、骨科（14）、神經外科（11）、泌尿外科（14）、整形外科（44）、眼科（10）、牙科（12）、口腔顎面外科（11）、耳科（12）、鼻科（16）、喉科（11）、婦產科（18）、兒童內科（4）、皮膚科（29）、精神科（181）、失智特別門診（1）、復健醫學（24）、疼痛控制科（9）、醫學美容中心（33）。

因此，本次 15 個舊詞項有逐字官方證據進 KB，其餘 748 個沒有作為舊詞庫 migration；其中一部分可能在官方其他頁面有證據，但本次**未驗證，不宣稱不存在**。KB 另有 6 筆官方來源概念，因科名不一致或不是舊詞庫同名項目，不計入舊詞 migration。舊檔本身不在 production 依賴中，也沒有被修改。

## 完整性與測試

loader 拒絕：未登錄或重複的 `source_id`、重複 URL、非官方 HTTPS 網域、未知院所／類型、錯誤優先級、來源與 record metadata 不一致、缺 URL／日期／短證據、正規化後重複的 source＋科別＋概念，以及未標明 `fallback_for_vghtpe` 的臺中記錄。任何 `department_resolution` 非 `unresolved` 的檔案記錄均被拒絕，直到有獨立 SQL 驗證流程；`lookup_concept()` 只回來源證據，不決定單一科別或 Top-K。

在 `New_Android_Backend/backend` 執行：

```powershell
$env:CEREBRAS_API_KEY=''
$env:PYTHONPATH=(Get-Location).Path
$env:Path=(Resolve-Path '.\.venv\Scripts').Path + ';' + $env:Path
pytest -q
```

最後一次完整 Backend suite 結果：`425 passed, 8 warnings, 285 subtests passed in 16.74s`。警告為既有 FastAPI／Starlette deprecation 與 pytest cache 權限。Android 未修改，不執行 Android build。

## 邊界

未建立 Phase 4 candidate convergence 或 AI 依 KB 正式選科；未進行 Phase 5 TTAS、Phase 6 doctor scoring 或 Phase 7/8 Android 工作。此 KB 是公開頁面結構化索引，**不宣稱已做臨床驗證**。正式 SQL 科別映射與動態中榮查詢的逐筆證據需要可用來源後再審核，不能由相似名稱、舊詞庫或 LLM 補猜。
