# Phase 6 審查報告（Backend only）

> 審查日期：2026-10-27
> 依據：`docs/修改計畫.md` §8 醫師推薦最終模式（L403-495；8.1 L405、8.2 L411、8.3 L439、8.4 L447、8.5 L464、8.6 L472、8.7 L478、8.8 L484）、§9 Phase 6（L1150-1259；6-A L1158、6-B L1171、6-C L1189、6-D L1201、6-E L1209、6-F L1219、6-G L1229），並參照 §2 架構原則（L112-190）、§11 永久禁止事項（L1464-1485，其中 13～16 在 L1480-1483）、§0 最小修改原則（L26-32）、§14 Architecture Cleanup Gate（L1600-1644，Gate 本體 L1619-1642）。
> 範圍：`backend/` 中時間偏好抽取／驗證／日期換算／硬限制與順位（`services/time_preference_service.py`、`services/rag_triage_adapter.py` 的 time_preferences 部分、`services/rule_engine.py` 的舊 availability 寫入）、SQL 班表過濾（`services/schedule_filter.py`、`db.py` 的班表查詢）、doctor_id 去重與 AI 專長評分（`services/specialty_scoring.py`）、兩欄排序（`services/appointment_service.py`）、`routes/recommend.py`、`routes/generate_script.py`（含 `services/quick_search_service.py:revalidate_schedule`）、`services/case_store.py`、`schemas.py`，以及舊自創排序／加權／醫療關鍵字殘留。Android 不在範圍內。
> 不重審：Phase 2～5 結論只引用（`docs/AUDIT_PHASE2.md`～`AUDIT_PHASE5.md`）。不審尚未施工的 Phase 2.5 保底流程。`/followup`（return_visit）與 `/schedules/search`（quick_search）只在與 `/generate_script` 共用處引用。
> 方式：只讀。未修改 production 檔案、測試檔或資料檔，未執行 pytest，未 commit，未連 310。只新增本報告。

## 0. 證據等級與設定值
- **[讀碼]**：已讀函式本體，並 grep `backend/app` 確認在 production 控制流上。
- **[資料腳本]**：本次沒有使用（Phase 6 沒有需要統計的資料檔）。
- **[測試名稱]**：只確認測試檔有此案例，未執行，**不能當作通過證據**。
- **無法確認**：需要實測、真 SQL 或官方文件才能判定，不推測。

目前設定值（[讀碼]；`backend/.env` 共 23 個鍵，只擷取非機密鍵）：

| 鍵 | `.env` 值 | `config.py` 預設值 | 與 Phase 6 的關係 |
|---|---|---|---|
| `AI_DOCTOR_SCORING_ENABLED` | true | **false**（`config.py:37`） | 關閉時專長評分整批中性（`specialty_scoring.py:57-59`） |
| `DOCTOR_SCORING_BATCH_SIZE` | 未設定 | 10（`config.py:38`，ge=1 le=25） | 每批醫師數 |
| `DOCTOR_SCORING_MAX_CANDIDATES` | 未設定 | 40（`config.py:39`，le=100） | 容量上限；實際容量 = min(此值, 批量×批數)＝40（`specialty_scoring.py:73`） |
| `DOCTOR_SCORING_MAX_BATCHES` | 未設定 | 4（`config.py:40`，le=10） | 最多批數 |
| `DOCTOR_SCORING_TOTAL_TIMEOUT_SECONDS` | 未設定 | 12.0（`config.py:41-46`，le=60） | 專長評分總時間預算；程式再以 60 秒封頂（`specialty_scoring.py:170`） |
| `AI_TIMEOUT_SECONDS` | 60 | 8.0（`config.py:34`） | 單次 Cerebras 呼叫逾時（SDK `timeout`，`ai_service.py:207-211`；`asyncio.wait_for`，`ai_service.py:227-230`）。專長評分另以 `wait_for(timeout=剩餘預算)` 包住（`specialty_scoring.py:98-101`），所以每批實際上限是「剩餘預算」，不是 60 秒 |
| `CEREBRAS_MODEL` | gpt-oss-120b | 同（`config.py:28`） | 專長評分與 Turn Interpreter 共用 |
| `BATCH_TRIAGE_ENABLED` | false | false（`config.py:29`） | 與 Phase 6 無直接關係 |
| `DEPLOY_MODE` / `USE_TTAS_TRIAGE` | local / true | `config.py:48` / 不在 `Settings` | 環境紀錄（`USE_TTAS_TRIAGE` 見 AUDIT_PHASE5 §0） |

- 寫在程式內、不能由 `.env` 調整的值：`TAG_NEUTRAL_SCORE=0.5`、`DEFAULT_*`（與 config 預設相同，作為設定值不合法時的退路，`specialty_scoring.py:18-22,161-170`）；`ACCEPT_THRESHOLD=0.55`（`confidence_scoring.py`，時間偏好也使用，`time_preference_service.py:75`）；搜尋天數預設 21、上限 90（`time_preference_service.py:220-229`）；當日截止時間 10:00／15:00／19:00（`schedule_filter.py:33-37`）；每欄 5 筆（`appointment_service.py:162,166-167,375`）。
- 其餘 `.env` 鍵（DB_*、VOICE_*、`GOOGLE_API_KEY`、`CEREBRAS_API_KEY`、`DISABLE_LOCAL_EMBEDDING` 等）與 Phase 6 無關或屬機密，未擷取值。

## 1. 主控制流摘要（[讀碼]）

**A. 時間偏好寫入（`/chat`，與 Turn Interpreter 同一次 AI 呼叫）**
1. `refine_case_with_ai` 在呼叫 AI 前先以 `datetime.now(TAIPEI_ZONE).date()` 固定當輪基準日（`rag_triage_adapter.py:75`）。
2. AI 輸出的 `time_preferences` 由 `validate_and_resolve_time_preferences` 驗證（`rag_triage_adapter.py:103-107`；repair 時 L142-146）：key 白名單（`time_preference_service.py:38-51,65`，不含 `reference_date`／`resolved_dates`）、`source_text` 須為本輪原文子字串、confidence 有限且 ≥0.55（L67-78）、Pydantic `TimePreference`（`schemas.py:198-256`，`extra="forbid"`；exclude 不可有 priority、prefer／acceptable 必須有 priority；各 kind 必填值）。通過後由 Backend 寫入 `reference_date` 與 `resolved_dates`（L81-87、L243-256）。單筆不合格 → 靜默略過（L77-78、L88-89）。
3. 整輪結構驗證通過才 `apply_time_preferences`（`rag_triage_adapter.py:213-214`）；同一目標（kind＋值，不含 relation）以後到者覆蓋（`time_preference_service.py:93-98,305-315`）。
4. 舊欄位 `preferred_days`／`preferred_sessions` 仍可由 AI 的 `semantic_extractions` 寫入（欄位白名單 `rag_triage_adapter.py:428-440`；值域 `field_acceptance.py:22-29`；寫入 `rule_engine.py:399-412`）。

**B. `POST /recommend`（`routes/recommend.py:23-135`）**
1. 取 case：server 端已存 case 優先；沒有時才用 `sanitize_untrusted_snapshot(triage_case)`（L29-37）。
2. 閘門：urgent（L59-69）→ visit_type（L71-89）→ `is_complete`／`need_more_info`（L91-92）→ 安全篩檢（L93-94）→ 確認（L96-102）→ 已驗證科別（L104-116）。
3. `recommend_appointments`（`appointment_service.py:75-171`）：以 SQL 主資料再驗科別（L79-94）→ 以台灣時間取 `now`（L98）→ SQL 查班表（L109-118，`db.py:610-731`）→ 依 dept_id、visit_type、正式 ID 過濾（L119-121）→ `select_effective_feasible_rows`（L124-129）：開放狀態＋截止時間 → 新版硬排除 → 醫師偏好 → 舊欄位條件 → `pareto_time_tiers`（L138）→ `score_doctor_specialties`（L139）→ 兩欄各自排序取前 5（L141-171）。
4. 任一欄為空 → 503 `build_no_schedule_message`（`recommend.py:125-126`）；成功才寫回 case 與推薦表（L128-135）。

**C. `POST /generate_script`（`routes/generate_script.py:12-70`）**
取已存推薦（有 case_id 用 `get_recommendation`，否則 `find_recommendation` 搜尋全部 case，L14-18）→ ID 完整性（L27-34）→ `revalidate_schedule` 以 schedule_id 重查 SQL（L41-51；`quick_search_service.py:89-138`）→ 產生腳本，並把 case 設為 `SCRIPT_READY`（L54-60）。**沒有任何 workflow／urgent 檢查。**

## 2. 逐條審查表

| # | 要求 | 判定 | 證據（檔案:行號） | 說明 |
|---|---|---|---|---|
| **1** | **6-A API 契約** | | | |
| 1a | `/recommend` 欄位名稱、型別、層級：`case_id`、`recommendations.specialty_first／time_first`、`total_count`、`fallback_departments` | 符合 | `schemas.py:450-452,470-474`；`appointment_service.py:163-171`；`recommend.py:23` `response_model=RecommendationResult` | [讀碼] 四個頂層欄位與兩欄巢狀結構都在；`fallback_departments` 固定 `[]`（L169）。 |
| 1b | 每欄最多 5 筆；`total_count` 為兩欄相加 | 符合 | `appointment_service.py:162,166-167,375-376`；`schemas.py:451-452`（`max_length=5`） | [讀碼] `total_count = len(specialty_first[:5]) + len(time_first[:5])`，不是去重醫師數。同一 schedule 可同時出現在兩欄（各自計入）。[測試名稱] `test_appointment_ranking.py:180`。 |
| 1c | 單筆欄位齊全、型別未變 | 符合（附註） | `schemas.py:426-447`；`appointment_service.py:349-373,411-418` | [讀碼] `recommendation_id`、`doctor_id`（str）、`schedule_id`（str）、`dept_id`（int）、`date`／`session`（str）、`specialty_tags`（**str**，L362）、`score`（0～100：`specialty*100` 或 `time_score*100`，L417-418）、`specialty_score`（0～1，L363）、`time_score`（0～1，L364）、`match_reason`、`reasons`（list[str]）。附註：①`match_reason` 在主推薦路徑**恆為 None**（L365；型別仍是 `Optional[str]`）；②`time_score` 現在只有 0 或 1（第 0 層＝1.0，其餘＝0.0，L317），仍在 0～1 範圍內，但意義與舊版的連續分數不同；③`reasons` 內含英文內部字串，例如 `Pareto preference tier=`、`specialty semantic relevance=`、`相容顯示 score=…不作排序權重`（L483,504,518-521），會出現在 API 回應中。舊版 `match_reason` 是否曾有值：**無法確認**（沒有基準樣本）。 |
| 1d | `docs/PHASE6_BACKEND_API_CONTRACT.md` 是否存在 | 不符合 | Glob `docs/PHASE6*`：0 筆；grep `API_CONTRACT` 全 repo（排除 .venv）只出現在 `修改計畫.md` | 6-A（L1169）與 §8.8（L493）要求的交付文件不存在；也沒有「成功／無班表／急迫／AI 失敗」的回應樣本。 |
| 1e | 對應的契約測試 | 部分符合 | [測試名稱] `test_appointment_ranking.py:232`（兩欄 key 存在）、`:180`（每欄 5 筆）、`:222`（doctor_id／schedule_id／dept_id／date／session）、`:243`（可經 `/generate_script` 重新驗證）、`:85`（缺 ID 不進推薦）、`:109`（非 70/30）；`test_backend_flow.py:237`（`total_count <= 10`） | 沒有找到以下契約斷言：`specialty_tags` 為字串、`score` 介於 0～100、子分數介於 0～1、`total_count` 等於兩欄相加、單筆完整欄位集合與型別、`fallback_departments` 型別。grep `isinstance(.*specialty_tags`、`total_count ==` 等：0 筆。 |
| **2** | **6-B 時間偏好** | | | |
| 2a | 只由 AI 抽取，且重要限制有 `source_text` 驗證 | 部分符合 | `time_preference_service.py:54-90`；`rag_triage_adapter.py:103-107,213-214`；prompt 第 26 條 `rag_triage_adapter.py:606` | [讀碼] 新版時間偏好只來自 AI 的 `time_preferences`；每筆都必須有本輪逐字 `source_text`＋confidence ≥0.55，否則丟棄。**缺口**：①單筆不合格（例如 AI 給 exclude 又附 priority、kind 缺必填值）時**靜默略過、不觸發 repair**（L77-78、L88-89；`_turn_interpretation_rejection_reason` 不檢查被丟棄的時間偏好，`rag_triage_adapter.py:272-318`）——若被丟的是 exclude，使用者的硬限制就消失；②Backend 只檢查 `source_text` 是原文子字串，不檢查它是否真的支持 relation（例如 source「下午最好」配 `relation=exclude` 也能通過）。舊欄位 `preferred_days／sessions` 同樣有 grounding（`rag_triage_adapter.py:438`），但沒有「不能／偏好」之分（見 2b）。[測試名稱] `test_time_preference_service.py:33,63`。 |
| 2b | 「最好下午」是否被誤判為「只能下午」 | 部分符合 | 新版：`time_preference_service.py:101-106,161-191`；prompt `rag_triage_adapter.py:606,698`。舊版：`time_preference_service.py:125-126,136-158`；`schedule_filter.py:195-217`；`rag_triage_adapter.py:431` | [讀碼] **新版**：prompt 明示「最好下午」是 prefer；prefer 只影響順位，不進硬排除，符合。[測試名稱] `test_time_preference_service.py:103`。**舊版路徑仍在 production**：AI 仍可把時段寫入 `semantic_extractions.preferred_sessions`（欄位白名單 L431），而舊欄位在推薦時是**硬過濾**——沒有 `time_preferences` 時走 `select_feasible_rows`，不符合者被移除，`can_take_leave=False` 時回 `[]`（`schedule_filter.py:207-217`）；有新版日期偏好但沒有新版時段偏好時，舊 `preferred_sessions` 同樣當作硬條件（`time_preference_service.py:136-158`）。所以只要 AI 把「最好下午」寫成舊欄位而非 `time_preferences`，結果就等於「只能下午」。[測試名稱] `test_appointment_ranking.py:210`（斷言舊 `preferred_sessions=["下午"]` 會排除上午）、`test_natural_chat_state_repair.py:594`（AI 路徑產生 `preferred_sessions=["下午"]`）。AI 實際走哪一條：**需 310**。 |
| 2c | 沒提到的時段是否被誤判為禁止或同意 | 部分符合 | 同 2b；`time_preference_service.py:259-267` | [讀碼] 新版：沒提到的時段不會被排除（只有 exclude 才排除），也不會被當成偏好（`_dimension_rank` 給 max+1 層）。prompt 明示「沒有提到的時段不得輸出 exclude」。舊版：只要有 `preferred_sessions`，其他時段一律被過濾（2b），等於「沒提到＝禁止」。 |
| 2d | 相對日期由 Backend 以台灣時區與當輪基準日換算 | 符合 | `rag_triage_adapter.py:75`；`time_preference_service.py:81-87,243-256`；prompt `rag_triage_adapter.py:606,700` | [讀碼] AI 只輸出 `week_offset`＋英文 weekday；Backend 用 `Asia/Taipei` 當輪日期算週一，再加 `week_offset` 週與星期偏移，結果凍結在 `resolved_dates`，之後比對只看 `resolved_dates`（L296-297）。AI 帶 `reference_date`／`resolved_dates` 會被整筆丟棄（L65）。附註：絕對日期（kind=`date`／`date_range`）的年份由 AI 給，prompt **沒有提供今天日期**，Backend 也不檢查日期是否在過去或年份是否合理（見 2e、T7）。 |
| 2e | 跨週、跨月、跨年、當天截止時間 | 部分符合 | `time_preference_service.py:243-256`；`schedule_filter.py:33-37,140-154`；`time_preference_service.py:220-229`；`db.py:643-644,650` | [讀碼] 跨週／月／年用 `timedelta` 計算，沒有字串拼接。[測試名稱] `test_time_preference_service.py:44`（跨月）、`:56`（跨年）、`test_schedule_filter.py:56,62,76,83`（截止時間、台北日界）。**缺口**：①當日截止時間 10:00／15:00／19:00 寫死在 Python（`schedule_filter.py:33-37`），沒有 SQL 或院方來源；②6-B 要求的適用範圍「某日之後」「只到某日」沒有對應的 kind（`date_range` 需起訖都給，而且需要 AI 自行換算絕對日期）；③搜尋天數預設 21、上限 90 寫死（L220-229），超過 90 天的偏好不會被查到；④`relative_week` 以週一為一週起點（L251），「這週」在週日說時涵蓋的是當週一到當天，是否符合使用者語意：**無法確認**。 |
| 2f | 舊 `preferred_dates／days／sessions` 型別保留 | 符合 | `schemas.py:259-279` | [讀碼] 三欄仍是 `List[str]`，`preferred_dates` 仍驗證 `YYYY-MM-DD`；新欄位 `time_preferences` 是另加的 `List[TimePreference]`（L264）。 |
| 2g | 舊快照是否可能覆蓋已保存的硬性時間限制 | 部分符合 | `routes/chat.py:701-721`（L713-714）；`routes/recommend.py:29-37`；`case_store.py:54-80` | [讀碼] **覆蓋**：server 端有 case 時直接使用已存 case，完全忽略 client 快照（chat L713-714；recommend L37），所以缺少新欄位的舊快照**不會**覆蓋已保存的限制。**注入**：server 端沒有 case（重啟、TTL 2 小時到期、換 worker）時，`sanitize_untrusted_snapshot` **不清除也不重新驗證** `availability.time_preferences`（含 client 自帶的 `resolved_dates`）、`preferred_*`、`can_take_leave`、`preferences.doctor_preference`，這些 client 斷言會被當成已驗證事實使用（與 AUDIT_PHASE4 R2 同類）。`/recommend` 用快照時 `conversation_state` 被重設 → `is_complete=False` → 400（recommend L91-92），所以要經過 `/chat` 重新走完流程才會用到。未找到涵蓋此情境的測試（`test_phase42_backend_hardening.py:199` 的快照清理測試沒有檢查 availability）。 |
| 2h | 是否為時間偏好額外多發 AI 呼叫 | 符合 | `rag_triage_adapter.py:76-78,103-107,123-146` | [讀碼] 與 Turn Interpreter 同一次呼叫（repair 也共用），沒有獨立的時間偏好 AI 呼叫。[測試名稱] `test_time_preference_service.py:126`。 |
| 2i | production 是否對患者自由文字做時間關鍵字判斷 | 符合 | 見 §3.A | [讀碼] `SESSION_ALIASES`（`schedule_filter.py:18-30`）、`_session_rank`（`appointment_service.py:557-564`）只套用在 SQL session 欄位或 AI 已正規化的 enum；`field_acceptance.py:22-29` 是 AI 輸出 enum 檢查；`conversation_service.py:33` 的「看診時段／什麼時段方便」是 AI 追問的輸出 guard。沒有對患者原文做時間詞比對。 |
| **3** | **6-C SQL 候選與去重** | | | |
| 3a | 先驗科別、doctor_id、schedule_id、初複診、停診、時段是否仍開放 | 符合（附註） | SQL：`db.py:642-646`（dept_id、日期範圍、`doc.is_active=1`、visit_type）、`db.py:686-691`（placeholder、status，`db.py:10-11,47-53`）；Python：`appointment_service.py:119-121,222-242`、`schedule_filter.py:140-154,165-177` | [讀碼] 順序為 SQL（dept_id／醫師在職／初複診／日期）→ 過濾 placeholder 與非開放 status（`請假休診`、`額滿`、`額滿關診`，以及不在開放清單的任何值）→ Python 再驗 dept_id、visit_type token、正式 ID（doctor_id、schedule_id、日期可解析、session 非空）→ 當日截止時間與 status。附註：`_formal_schedule_rows` 只要求 session 非空（L241），SQL session 若是未列在 `SESSION_ALIASES` 的值，`normalize_session` 會原樣回傳（`schedule_filter.py:40-49`），這種診次**不會被時段排除條件命中**。實際 SQL session 值：**需 310**。 |
| 3b | 同一 doctor_id 專長只評一次 | 符合 | `specialty_scoring.py:47-52,181-187`；`appointment_service.py:421-432` | [讀碼] 先以 doctor_id 去重，分數再以 doctor_id 套回每一筆診次。[測試名稱] `test_specialty_scoring.py:82`、`test_appointment_ranking.py:165`。計畫允許的「專長字串完全相同的不同醫師共用評分」沒有實作（計畫用語為「可」）。 |
| 3c | 去重鍵是否為 doctor_id；有無拿姓名或中文專長當 ID | 符合（附註） | `specialty_scoring.py:181-187,236-260`；`appointment_service.py:309-313,380-408` | [讀碼] 專長去重與 AI 回傳比對都用 doctor_id；推薦清單去重用 schedule_id。附註：①`doctor_preference` 以**醫師姓名子字串**過濾診次（`time_preference_service.py:129-134`；`schedule_filter.py:202-206`），沒有命中時靜默回到全部候選；production 沒有任何地方寫入此欄位（只有預設「不限」，`schemas.py:284`），只能經 client 快照帶入（2g）；②`db.map_schedule_record` 在缺 doctor_id 時以**醫師姓名**補位（`db.py:739`），但沒有 production caller（§3.C）。 |
| 3d | 缺 schedule_id 或 doctor_id 的診次被排除 | 符合 | `appointment_service.py:233-242`；`specialty_scoring.py:50`；`db.py:707-708` | [讀碼] SQL 為 NULL 時轉成空字串，再由 `_formal_schedule_rows` 排除。[測試名稱] `test_appointment_ranking.py:85`、`test_specialty_scoring.py:236`。 |
| **4** | **6-D AI 專長評分與退路** | | | |
| 4a | AI 輸出是否只有 doctor_id＋score | 符合（附註） | prompt `specialty_scoring.py:209-225`；驗證 `:236-260` | [讀碼] prompt 只要求 `{"scores":[{"doctor_id","score"}]}`，禁止自然語言理由；prompt 只送 doctor_id＋specialty_tags（L195-201）與已接受的結構化症狀證據（L202-208），不送姓名或班表細節。Backend 只讀這兩個欄位；附註：item 或頂層多出的 key **不會被拒絕**，只是被忽略。 |
| 4b | Backend 驗證：在候選內、同科、無重複、0～1 | 符合 | `specialty_scoring.py:236-260,47-51` | [讀碼] doctor_id 必須是字串且在本批 expected 內、不得重複、score 為 int／float（排除 bool）、有限、0～1，且整批 ID 集合必須完全相等。同科：候選在進入評分前已依 dept_id（`appointment_service.py:119-121`）與 child_dept（`specialty_scoring.py:50`）過濾，AI 只能回候選內 ID。[測試名稱] `test_specialty_scoring.py:118`（參數化的不合法 payload）。 |
| 4c | 是否只評 SQL 前 N 位 | 符合 | `specialty_scoring.py:73-83` | [讀碼] 有專長資料的醫師數 > 容量時**整批中性、不呼叫 AI**，沒有「前 N 位用 AI、其餘用中性」。[測試名稱] `test_specialty_scoring.py:210`。 |
| 4d | 是否串行多次呼叫（逐位或逐批） | 部分符合 | `specialty_scoring.py:93-105` | [讀碼] 不是逐位；但**逐批串行**：`for` 迴圈內 `await`，預設最多 4 批（每批 10 位）依序呼叫，總時間受 12 秒預算限制。計畫 L470／L1205 允許「少量批次」但「是否值得採多批次，依 310 實測延遲再決定」；目前沒有實測紀錄。[測試名稱] `test_specialty_scoring.py:95`。 |
| 4e | 時間預算與容量上限是否為設定值 | 符合 | `config.py:37-46`；`specialty_scoring.py:68-91,161-170` | [讀碼] 批量、容量、批數、總預算都可由 `.env` 調整；目前 `.env` 都沒設，全部使用預設（§0）。 |
| 4f | 逾時、格式錯、超容量時是否整批 0.5 | 符合（附註） | `specialty_scoring.py:52-59,76-83,92-112,114-125` | [讀碼] AI 關閉／不可用、超容量、預算耗盡、單批逾時、JSON 錯、任何一批不完整 → `return neutral`（全部 0.5），已成功批次的分數也一併捨棄。附註：沒有 `specialty_tags` 的醫師不送 AI、單獨給 0.5，而同次其他醫師可能有 AI 分數（L61-64,114-124）；這符合 §8.6（L474）「未取得有效專長評分者採中性值 0.5」，不是「前 N 位」問題。[測試名稱] `test_specialty_scoring.py:118,129,141,170,190,223`。 |
| 4g | `SPECIALTY_KEYWORDS`、`_explainable_specialty_matches` 等中文醫療關鍵字 | 符合 | grep `backend/`（排除 .venv，含 py／json／md）：0 筆 | [讀碼] 兩個名稱都已不存在，沒有 caller。`score_doctor_deterministically` 只回中性值（`specialty_scoring.py:128-135`），無 production caller（§3.C）。 |
| 4h | 目前 timeout 與逾時設定 | 符合 | 見 §0 | [讀碼] 專長評分：總預算 12 秒（預設）；每批以剩餘預算為上限；單次 SDK 逾時 60 秒（`.env`）實際上不會被用滿。Turn Interpreter（含時間偏好）：60 秒（`.env`）。SQL：**沒有任何連線或查詢逾時設定**（`db.py:21-34`，見 §3.E）。 |
| **5** | **6-E 排序定義** | | | |
| 5a | 專長優先：硬限制 → 專長 → 時間層級 → 真實時間 | 符合 | `appointment_service.py:124-139,392-400` | [讀碼] 硬限制先在 `select_effective_feasible_rows` 移除；排序 key 為 `(-specialty, time_tier, date, session, doctor_id, schedule_id)`。[測試名稱] `test_appointment_ranking.py:109,121,157`。 |
| 5b | 時間優先：硬限制 → 日期／時段順位 → 真實時間 → 專長 | 符合 | `appointment_service.py:401-408`；`time_preference_service.py:161-213` | [讀碼] key 為 `(time_tier, date, session, -specialty, doctor_id, schedule_id)`。沒有時間偏好時所有診次同為第 0 層，等於真實最早可掛優先。[測試名稱] `test_appointment_ranking.py:133,146`。 |
| 5c | 專長分數能否補償使用者明確不能的時間 | 符合 | `time_preference_service.py:128` | [讀碼] exclude 在評分與排序之前就從候選中移除，不在排序 key 內，專長分數無法讓它回來。[測試名稱] `test_appointment_ranking.py:188`、`test_time_preference_service.py:220`。 |
| 5d | 是否殘留 70/30、+15／+10 等人工權重 | 符合 | grep 見 §3.B | [讀碼] Phase 6 範圍檔案中沒有 70／30／0.7／0.3／15 權重字面值，也沒有 `weight`、`bonus`、`_score_date/_session/_time` 等舊常數或函式。唯一名為 `weighted_score` 的函式只回傳單一子分數×100（`appointment_service.py:411-418`）。 |
| 5e | 舊分數欄位是否獨立於排名依據 | 符合 | `appointment_service.py:303-306,315-318,380-408` | [讀碼] 先排序（`_ranking_key` 不讀 `score`／`time_score`），之後才計算顯示用的 `time_score` 與 `score`。[測試名稱] `test_appointment_ranking.py:109`。 |
| 5f | 日期與時段互有優劣時是否擅自加權 | 部分符合 | `time_preference_service.py:194-213,330-331`；`appointment_service.py:401-408`；`conversation_service.py:33` | [讀碼] 沒有加權：以 Pareto 非支配分層，互有優劣的兩筆放在同一層。[測試名稱] `test_time_preference_service.py:113`。**但**：①同層之後依日期、再依時段排序，實際效果是「較早日期優先」；計畫允許「以真實日期時間作穩定排序」（L1187），但要求「不宣稱這就是使用者的總偏好」，目前 `reasons` 只寫 `符合使用者時間偏好第 N 順位`（`time_preference_service.py:232-240`），沒有說明兩者互有優劣；②§8.4（L460）與 6-B（L1187）要求的「必要時以一句澄清問題確認日期或時段較重要」**沒有實作**；而且 AI 追問 guard `_UNSAFE_QUESTION` 包含「看診時段」「什麼時段方便」「哪天有空」「看診日期」，會擋下這類追問（`conversation_service.py:33`）。 |
| 5g | 是否有硬限制被放寬的路徑 | 部分符合 | `time_preference_service.py:128-158`；`schedule_filter.py:195-217`；`appointment_service.py:340-341` | [讀碼] 新版 exclude **不會被放寬**（`can_take_leave` 也不會）。仍存在的放寬：①舊欄位在 `can_take_leave=True` 且無符合時，整個舊條件被放寬（L156-157；`schedule_filter.py:211-215`），並附理由「您方便的時段近期無號，改推薦時間最接近的門診」；②`doctor_preference` 沒有命中時靜默改用全部醫師（L134）；③不合格的 exclude 在驗證階段就被丟掉（2a），等於被放寬，而且沒有任何紀錄或理由。 |
| **6** | **6-F／§11-13～16 禁止事項** | | | |
| 6a | 是否改成只回醫師名單 | 符合 | `appointment_service.py:303-377` | [讀碼] 以 schedule_id 去重，同一醫師不同診次可各佔一筆。 |
| 6b | 是否有 dept_id、doctor_id 硬編 | 符合 | grep §3.D | [讀碼] 範圍檔案中沒有 4 位以上數字字面值、沒有科別名稱。 |
| 6c | DB 沒班表時是否自動換科 | 符合 | `appointment_service.py:169`；`recommend.py:125-126`；`ai_reply_generator.py:95-115` | [讀碼] `fallback_departments=[]`；無班表 → 503，訊息只說找不到該科班表，不建議其他科。 |
| 6d | 是否假設某科有夜診或週末診而寫死 | 符合（附註） | `schedule_filter.py:9-37` | [讀碼] 沒有「某科有／沒有夜診或週末」的邏輯，是否有班完全由 SQL 決定。[測試名稱] `test_appointment_ranking.py:202`。附註：時段的時鐘範圍（上午 08:30-12:00 等）與當日截止時間寫死，屬營運習慣（§3.D）。 |
| 6e | AI 中性分數是否被寫成「專長高度相符」 | 符合（附註） | `appointment_service.py:339,498-505,508-521`；`specialty_scoring.py:146-149` | [讀碼] grep「高度相符／專長相符」：0 筆；`match_reason` 恆為 None。附註：`is_best_match` 對專長欄第一筆固定為 True（`appointment_service.py:369`），即使全部是中性 0.5 也一樣；這是既有欄位，如何顯示屬 Phase 7。 |
| 6f | 回應中是否如實標示「未取得可靠 AI 專長分數，依可掛號時間排序」 | 部分符合 | `specialty_scoring.py:146-149`；`appointment_service.py:498-505` | [讀碼] 只有每筆 `reasons` 中的一句「專長依據：…AI 專長語意評分未完整可用，本次所有候選統一使用中性值 0.50」。①沒有說明「依可掛號時間排序」；②沒有回應層級的狀態（不改 API 的前提下，目前只能從每筆理由文字看出）；③逾時／容量／AI 關閉三種原因的文字相同，原因只在 log（`specialty_scoring.py:58,77-83,107-111`）。 |
| **7** | **generate_script** | | | |
| 7a | 是否依 schedule_id／doctor_id／日期／時段重新驗 SQL | 部分符合 | `quick_search_service.py:89-138`；`generate_script.py:27-34,41-51` | [讀碼] 以 schedule_id 重查 SQL，比對 schedule_id、doctor_id、dept_id、date、session，並重驗 status 與當日截止時間；回傳內容改用 DB 值。**未重驗 visit_type**（初複診）；也不重驗使用者時間限制（計畫未要求）。[測試名稱] `test_appointment_ranking.py:243`（mock 掉 `revalidate_schedule`，只證明路由會呼叫它）。 |
| 7b | 是否有 urgent gate（Phase 5 T11） | **不符合** | `generate_script.py:12-70` | [讀碼] 確認 AUDIT_PHASE5 T11：整個路由沒有讀 `clarification_status`、`ttas_result`、`confirmed`、`is_complete` 或 stage。只要 `_RECOMMENDATIONS_BY_CASE` 裡還有舊推薦，案件變成 urgent 後仍能產生腳本，並把 stage 設為 `SCRIPT_READY`（L59）。 |
| 7c | `_RECOMMENDATIONS_BY_CASE` 在修訂、進入 urgent、換科時是否被清除 | **不符合** | `case_store.py:31-48,139-149`；`routes/chat.py:743-771` | [讀碼] 只有兩種情況會改變：TTL 到期（L38-47）或下一次成功的 `/recommend` 整批覆蓋（L144）。`_begin_revision` 清 `recommendation_generated`、`selected_recommendation_id`、`department_result`，但不清推薦表；urgent 分支（AUDIT_PHASE5 §1 #6）也不清；`case_store` 沒有任何清除單一 case 推薦的函式。`/recommend` 失敗（例如 503）時也不會清掉舊表（recommend L125-135）。 |
| 7d | 舊 `recommendation_id` 的重放風險 | 部分符合 | `generate_script.py:14-19,35-40`；`case_store.py:156-161`；`appointment_service.py:350` | [讀碼] SQL 重驗可擋下「班表已不存在／已關閉／已過截止」的舊 ID，這部分符合 §8.8 L491。仍有的風險：①修訂、換科、進入 urgent 後，舊 ID 仍能產生腳本（7b、7c）；②不帶 `case_id` 時 `find_recommendation` 會搜尋**所有 case**，找到就產生腳本（不更新任何 case）；③ID 格式可推得（`rec_s_{case_id}_001`，`case_id` 為 `case_` 加 8 位 hex，`case_store.py:50-51`）。實際是否被利用：**無法確認**。 |
| **8** | **/recommend 閘門** | | | |
| 8a | 急迫、未確認、未完成安全篩檢、無科別時是否都擋住 | 符合（附註） | `recommend.py:59-69,91-94,96-102,104-116` | [讀碼] 四項都會擋：urgent／level 1-2 → 400；未完成 → 400；安全篩檢未完成 → 400；未確認 → 400；無已驗證科別 → 422。附註：request body `confirmed=true` 會在 `/recommend` 內直接把案件設為已確認（L96-99），不必先經 `/chat` 的確認 gate；這是既有行為，前提是 L91-94 已通過。 |
| 8b | 與 Phase 5 T3、T5 的交互影響（只引用） | 部分符合 | AUDIT_PHASE5 §3.D、#12、§4 T3／T5 | 只引用：/recommend 的急迫判斷只看 `clarification_status=="urgent"` 與 `ttas_result` level∈{1,2}（L59-64），安全篩檢只看 `safety_screen_resolved`（L93）。因此 **T3**（urgent 被更正降級後 `red_flags_checked=True` 殘留 → 跳過安全篩檢）與 **T5**（rule 套件載入失敗 → `insufficient_information`）兩條路徑都能通過 /recommend 閘門。Phase 6 本身沒有新增或放寬閘門。 |

**統計：符合 29（1a,1b,1c,2d,2f,2h,2i,3a,3b,3c,3d,4a,4b,4c,4e,4f,4g,4h,5a,5b,5c,5d,5e,6a,6b,6c,6d,6e,8a）／部分符合 13（1e,2a,2b,2c,2e,2g,4d,5f,5g,6f,7a,7d,8b）／不符合 3（1d,7b,7c）／無法確認 0。**（共 45 項；「符合（附註）」計入符合。）

## 3. 特別檢查項目

### A. production 中對「患者自由文字」做 keyword／regex／phrase-list 的路徑（[讀碼]）
- grep `re\.(compile|search|match|fullmatch|findall|sub|split)\(` 於 `backend/app`：共 14 處，位置與 AUDIT_PHASE4 §3.A **完全相同**（`conversation_service.py:32-52,337`、`department_reasoning_service.py:21`、`department_knowledge.py:43,113,195`、`rule_engine.py:393`），**沒有新增**。Phase 6 的 4 個主要模組（`time_preference_service.py`、`specialty_scoring.py`、`schedule_filter.py`、`appointment_service.py`）沒有任何 regex。
- Phase 6 範圍內的字串 `in` 比對：
  | 位置 | 作用對象 | 是否判斷患者語意 |
  |---|---|---|
  | `time_preference_service.py:72` | AI 的 `source_text` 是否出現在本輪原文 | 否（grounding） |
  | `schedule_filter.py:46-48` `normalize_session` 子字串 | SQL session 欄位、AI 正規化後的舊欄位 enum | 否 |
  | `appointment_service.py:557-564` `_session_rank` | SQL session 欄位 | 否 |
  | `time_preference_service.py:132`、`schedule_filter.py:204` | `doctor_preference` 對醫師姓名的子字串比對 | 否（不是患者原文；production 不寫入此欄位，見 2-3c） |
  | `field_acceptance.py:22-29` | AI 輸出的 `preferred_days／sessions` enum | 否 |
  | `conversation_service.py:33` `_UNSAFE_QUESTION`（含時間詞） | AI 產生的追問 | 否（輸出 guard；但會擋時間澄清追問，見 5f） |
- 科別偏好：仍由 AI `department_preference`＋Backend 驗證（AUDIT_PHASE4 §3.F），Phase 6 沒有新增路徑。專長匹配：只有 AI；grep `SPECIALTY_KEYWORDS`、`_explainable_specialty_matches`、`keyword`（`backend/app`）：只有 `department_knowledge.py:15`（官方 KB 來源設定，Phase 3 範圍）與 `specialty_scoring.py:133` 的 docstring。
- **結論：** 與 AUDIT_PHASE4 §3.A 相比沒有新增；未發現以 keyword／regex／phrase-list 判斷患者時間、科別或專長語意的 production 路徑。

### B. 手寫醫療或排序規則殘留（[讀碼]）
- **醫療規則**：Phase 6 範圍內沒有症狀→專長、症狀→科別的規則；grep 範圍檔案中的科別名稱、「膝」等症狀字：0 筆（appointment_service.py 的「膝→骨科」類 fallback 已不存在，與 AUDIT_PHASE3 L61 一致）。
- **排序權重**：沒有 70/30、+15／+10、60/40（grep `\b(70|30|0\.7|0\.3|15|10|60|40)\b` 於 Phase 6 檔案，只命中下表的非權重值）。
- **寫在 Python 的數字門檻**（都不是醫療規則，但沒有來源或沒有設定值）：
  | 值 | 位置 | 性質 |
  |---|---|---|
  | 當日截止 10:00／15:00／19:00 | `schedule_filter.py:33-37` | 營運習慣，沒有來源（T11） |
  | 時段時鐘範圍 08:30-12:00、13:30-17:00、18:00-21:00 | `schedule_filter.py:11-16` | 只用於 `session_time` 顯示（`appointment_service.py:359`；`quick_search_service.py:132`），不參與過濾 |
  | 搜尋天數 21（預設）、90（上限） | `time_preference_service.py:220-229` | 查詢範圍 |
  | 中性分數 0.5 | `specialty_scoring.py:18` | 計畫 §8.6 指定值 |
  | 批量 10、容量 40、批數 4、預算 12 秒、上限 60 秒 | `specialty_scoring.py:19-22,170` | 有 config 可覆寫（預設與 `config.py` 相同） |
  | 每欄 5 筆 | `appointment_service.py:162,166-167,375` | API 契約 |
  | `time_score` 二值 1.0／0.0 | `appointment_service.py:317` | 顯示用，不參與排序 |

### C. dead code（無 production caller）
方法：對 Phase 6 範圍檔案的每個 `def`／常數，以 grep（`-w` 等效）計 `backend/app` 中除定義行以外的引用；0 筆者再 grep `backend/tests`。

| 項目 | 位置 | `backend/app` | tests 引用 | 判定 |
|---|---|---|---|---|
| `clamp_score` | `specialty_scoring.py:138-141` | 0 | 0 | **dead**（AUDIT_PHASE3 F2 已列） |
| `_row_key` | `specialty_scoring.py:272-273` | 0 | 0 | **dead**（AUDIT_PHASE3 F2 已列） |
| `score_doctor_deterministically` | `specialty_scoring.py:128-135` | 0 | `test_specialty_scoring.py:12,63`、`test_selective_wu_merge.py:16,76` | 只有測試使用（AUDIT_PHASE3 F2 已列）；刪除需同步改 2 個測試 |
| `_weekday_label` | `appointment_service.py:579-580` | 0 | 0 | **dead**（AUDIT_PHASE4 R1 已列） |
| `slot.get("source") == "mock"` 分支 | `appointment_service.py:344-347` | 推薦路徑的資料只來自 `_fetch_available_slots_from_db`，`source` 固定為 `"db"`（`db.py:719`）；grep `"mock"` 於 `backend/app` 只有此行 | `test_db_adapter_mapping.py:308`、`test_quick_search.py:318` 只斷言回應**沒有** mock | **不可達分支**（新發現） |
| `map_schedule_record` | `db.py:734-755` | 0 | `test_recommendation_data_access.py:6,11,34,61,74` | 只有測試使用；內含以醫師姓名補 doctor_id 的邏輯（L739）（新發現） |
| `filter_rows_by_availability` | `schedule_filter.py:180-192` | 0 | `test_schedule_filter.py:10,94,100,113,115,124` | 只有測試使用（新發現） |
| `availability_from_project_smart_slots` | `schedule_filter.py:95-113` | 0 | `test_schedule_filter.py:9,31` | 只有測試使用（新發現） |
| `weighted_score` | `appointment_service.py:411-418` | L318 | `test_appointment_ranking.py:20,118` | 不是 dead；名稱有誤導性，但內容只是單一分數×100 |
| `availability_slots` | `schedule_filter.py:82-92` | L126、L186 | — | 不是 dead（L186 的 caller 本身只有測試使用） |
| 其他（`recommend_appointments`、`resolve_department_result`、`_filter_slots_by_*`、`_formal_schedule_rows`、`_ranking_key`、`_score_for_slot`、`_score_explanation_reasons` 及各 `_*_reason`、`_session_rank`、`score_doctor_specialties`、`_validate_complete_batch`、`_build_scoring_prompt`、`_unique_doctor_rows`、`validate_and_resolve_time_preferences`、`apply_time_preferences`、`filter_hard_exclusions`、`select_effective_feasible_rows`、`time_preference_vectors`、`pareto_time_tiers`、`search_horizon_days`、`describe_time_match`、`select_feasible_rows`、`open_rows`、`row_matches_availability`、`session_is_open`、`row_is_available`、`find_recommendation`、`sanitize_untrusted_snapshot`、`revalidate_schedule`） | — | ≥1 | — | 都有 production caller |

- 計畫點名的舊 70/30 計分函式、`SPECIALTY_KEYWORDS`、`_explainable_specialty_matches`、「膝→骨科」弱 fallback：grep 皆 0 筆，**已不存在**。
- voice／TTS 共用程式（`routes/voice.py`、`fixed_sentences.py`、`tts_cache.py`、`question_specs.py`、`clarification_engine.py`）**一律不列為可刪**；其中 `question_specs.py:86-98`、`clarification_engine.py:10-12`、`fixed_sentences.py:14-15` 仍有 `preferred_days／sessions` 舊問句，本報告只陳述，不建議刪除。

### D. 硬編碼（[讀碼]）
- **dept_id／doctor_id**：Phase 6 範圍檔案（`routes/recommend.py`、`routes/generate_script.py`、`appointment_service.py`、`schedule_filter.py`、`specialty_scoring.py`、`time_preference_service.py`、`case_store.py`、`quick_search_service.py`、`db.py`、`script_service.py`）grep `\b[0-9]{4,}\b`：只有 `script_service.py:16` `delay_ms=1200`（非 ID，與 AUDIT_PHASE4 §3.H 相同），**沒有 dept_id／doctor_id 字面值**。
- **科別名稱**：0 筆（同 AUDIT_PHASE4 §3.H）。
- **時段**：`SESSION_ALIASES` 重複定義兩份（`schedule_filter.py:18-30`、`db.py:337-349`）；AI 舊欄位值域為「上午／下午／夜間」（`field_acceptance.py:25`），推薦時正規化為「晚上」（`schedule_filter.py:28`）；`SESSION_VALUE` morning／afternoon／evening（`time_preference_service.py:30-34`）。
- **星期**：`WEEKDAY_LABELS`（`schedule_filter.py:9`）、`WEEKDAY_INDEX`（`time_preference_service.py:21-29`）、`field_acceptance.py:24`；一週從週一開始（`time_preference_service.py:251`）。
- **營運習慣**：當日截止時間、時段時鐘範圍（§3.B）；初診／複診對照（`appointment_service.py:257-265`）；開放／關閉 status 字串（`db.py:10-11`、`schedule_filter.py:167`）。
- **沒有**：「某科有夜診／週末」、「某醫師優先」、預設科別等寫死邏輯。

### E. 效能（只讀碼；實測數字一律「需 310」）
- **SQL**（每次 `/recommend`）：`fetch_active_departments` 1 次（`appointment_service.py:79`）；有指定科別時路由再多 1 次（`recommend.py:106` 呼叫 `resolve_requested_department` 未傳入清單，`department_preference_service.py:68-70`）；班表 1 次，無 `TOP`、`max_slots=None`，範圍 21～90 天（`appointment_service.py:109-118`；`db.py:623-648`）。**沒有連線或查詢逾時**（`db.py:21-34` 的 `pyodbc.connect` 沒有 timeout 參數），而且是在 `async def recommend` 中同步呼叫 pyodbc，查詢期間會佔住 event loop。
- **AI**：`/recommend` 0～4 次，逐批串行（`specialty_scoring.py:93-105`）；有專長資料的醫師 >40 位時 0 次（整批中性）；總預算 12 秒。`/chat` 時間偏好 0 次額外呼叫（2h）。
- **Python 計算**：`pareto_time_tiers` 每層 O(n²) 比較（`time_preference_service.py:204-212`），層數上限約為（日期層數+1）×（時段層數+1）；數百筆時的實際耗時：需 310。`/generate_script` 1 次 SQL（`fetch_quick_search_slot_by_id`）。
- **整體耗時上限**：程式中**只有專長評分有總預算**；SQL 與整個 `/recommend` 沒有上限。熱門科別 SQL／AI／總耗時：需 310。

### F. 目前設定值
見 §0 表格。補充：`get_settings()` 每次呼叫都重新建立 `Settings()`、重讀 `.env`（`config.py:62-63`），`specialty_scoring.py:56` 在每次 `/recommend` 讀一次。

### G. Architecture Cleanup Gate 對照（§14 L1619-1642）
| Gate 允許／禁止項目 | Phase 6 目前狀態 |
|---|---|
| 刪除 production 中對患者自由文字做語意判斷的 keyword／regex／phrase-list | Phase 6 範圍內沒有此類路徑（§3.A），無需刪除。時間偏好已改由 AI structured output 提出。 |
| 保留 AI 輸出品質 guard 與固定系統文案 | `_UNSAFE_QUESTION` 屬於此類；它同時擋下時間澄清追問（5f）。是否調整是設計決定，不屬 Cleanup。 |
| 科別收斂不得退回關鍵字猜科 | `appointment_service.py:88-93` 無已驗證科別直接 raise；不做猜科。 |
| 可刪除無 production caller 的 dead path（先搜 caller、同步清測試） | 候選見 §3.C：`clamp_score`、`_row_key`、`_weekday_label`（無測試）；`score_doctor_deterministically`、`map_schedule_record`、`filter_rows_by_availability`、`availability_from_project_smart_slots`（只有測試）；不可達的 mock 分支。尚未刪除。 |
| 不得刪除 Voice／TTS／正式 API 共用程式 | §3.C 候選都沒有被 voice／TTS 檔案引用（grep 只有定義與 tests）。 |
| 修復 `request_explanation` 控制流 | 不在 Phase 6 範圍（AUDIT_PHASE2）。 |
| 不得修改 Android、SQL schema／正式資料、TTAS rule、官方 KB、Voice Gateway、Accessibility、secrets | Phase 6 程式碼只讀 SQL（`SELECT`），未發現寫入；§4 各項都不需要動這些範圍。 |
| 非功能性整理：`.pytest_tmp*` | `.gitignore:12` 已有 `**/.pytest_tmp*/`；Glob `backend/.pytest_tmp*`：0 筆。 |
| 新增需求（Phase 2.5 保底流程）不得混入 | Phase 6 程式中沒有家醫科保底相關邏輯（grep「家醫／家庭醫學」：0 筆）。 |

## 4. 需要修復的項目

| # | 項目 | 預估 production 檔案數 | 對照最小修改原則與 Cleanup Gate |
|---|---|---|---|
| T1 | 缺 `docs/PHASE6_BACKEND_API_CONTRACT.md` 與回應樣本；契約測試沒有檢查 `specialty_tags` 型別、分數量尺、`total_count`=兩欄相加、單筆欄位集合（1d、1e） | 0（文件 1＋測試 1） | 6-A 明列的交付物，不動 production、不改 schema。但仍屬新增工作，**等使用者指示再做**。 |
| T2 | `/generate_script` 沒有 urgent／workflow gate（7b；即 AUDIT_PHASE5 T11） | 1：`routes/generate_script.py` | 新增 workflow gate＝state machine 相關；未實測。**需使用者同意，不主動修。** |
| T3 | `_RECOMMENDATIONS_BY_CASE` 在修訂、urgent、換科時不清除；不帶 case_id 時可跨 case 找推薦（7c、7d） | 2：`services/case_store.py`、`routes/chat.py`（若同時限制 `find_recommendation`，再加 `routes/generate_script.py` → 3） | 改 state／workflow；未實測。**需使用者同意，不主動修。** 建議與 T2 一起決定。 |
| T4 | 舊 `preferred_days／sessions` 在推薦時是硬過濾，AI 若把「最好下午」寫成舊欄位就會變成「只能下午」；沒提到的時段也會被排除（2b、2c） | 1～3：`time_preference_service.py`、`schedule_filter.py`，可能加上 `rag_triage_adapter.py`（prompt／欄位白名單） | 改變舊欄位語意，可能影響 Phase 1/2 測試與 Android 舊選項流程；AI 實際走哪條路徑未實測。**需使用者同意，不主動修。** |
| T5 | `sanitize_untrusted_snapshot` 不清除也不重新驗證 `time_preferences`（含 `resolved_dates`）、`preferred_*`、`can_take_leave`、`doctor_preference`（2g） | 1：`services/case_store.py` | 與 AUDIT_PHASE4 R2 同類；未實測。**列入待辦，不主動修**；若要做，建議與 R2 一起處理。 |
| T6 | 不合格的時間偏好（含 exclude）靜默丟棄、不觸發 repair，硬限制可能消失（2a、5g③） | 1～2：`rag_triage_adapter.py`（repair 條件）、`time_preference_service.py` | 改 Turn Interpreter 契約／repair 條件；未實測。**需使用者同意，不主動修。** |
| T7 | 絕對日期（`date`／`date_range`）年份由 AI 給，但 prompt 不提供今天日期，Backend 不檢查是否為過去或年份是否合理；「某日之後」無法表達（2d 附註、2e②） | 1～2：`rag_triage_adapter.py`（prompt）、`time_preference_service.py` | 改 prompt 與驗證，不是已證實 regression。**列入待辦，不主動修。** |
| T8 | 中性分數的標示不完整：沒有「依可掛號時間排序」、沒有回應層級狀態、三種原因同一句；中性時 `is_best_match` 仍為 True（6e、6f） | 文字修正 1：`specialty_scoring.py` 或 `appointment_service.py`；新增回應欄位則動 `schemas.py` | 只改理由文字：1 檔，但不是已證實 regression，**列入待辦**。新增回應層級欄位＝改 API schema，**需使用者同意，不主動修**（§8.7 L482 也說 Phase 6 不新增 Android 必讀欄位）。 |
| T9 | 日期與時段互有優劣時沒有澄清追問；`_UNSAFE_QUESTION` 會擋下時間類追問（5f） | ≥2：`conversation_service.py`、對話規劃相關檔案 | 新增對話行為＝改 state machine／AI planner。**需使用者同意，不主動修。** |
| T10 | dead code／不可達分支：`clamp_score`、`_row_key`、`score_doctor_deterministically`、`_weekday_label`、`appointment_service.py:344-347` mock 分支、`map_schedule_record`、`filter_rows_by_availability`、`availability_from_project_smart_slots`（§3.C） | 4：`specialty_scoring.py`、`appointment_service.py`、`db.py`、`schedule_filter.py`（＋測試 4 檔：`test_specialty_scoring.py`、`test_selective_wu_merge.py`、`test_recommendation_data_access.py`、`test_schedule_filter.py`） | 屬 §14「無 production caller 的 dead path」，適用 Cleanup Gate 的 3 檔例外。其中 4 項已在 AUDIT_PHASE3 F2、AUDIT_PHASE4 R1 列出，應合併成同一次清理，避免重複。刪除前需重跑 grep。**等使用者指示 Cleanup Gate 施工時再做。** |
| T11 | 當日截止時間、時段時鐘範圍、搜尋天數 21／90 寫死在 Python，沒有院方或 SQL 來源（2e①③、§3.B、§3.D） | 1：`schedule_filter.py`（＋`time_preference_service.py`） | 需要院方資料決定數值；不是 regression。**列入待辦，不主動修。** |
| T12 | SQL 沒有連線／查詢逾時，且在 async 路由中同步執行；整個 `/recommend` 沒有總耗時上限（§3.E） | 1～2：`db.py`、`routes/recommend.py` | 效能風險，需 310 實測證實。**列入待辦，不主動修。** |
| T13 | `/generate_script` 重驗不檢查 visit_type（7a） | 1：`services/quick_search_service.py` | 與 quick_search 共用；未實測。**列入待辦，不主動修。** |
| T14 | `/recommend` 的 `confirmed=true` 可直接完成確認，不必經 `/chat` 確認 gate（8a 附註） | 1：`routes/recommend.py` | 既有行為；改它＝改 state machine，也可能影響 Android。**需使用者同意，不主動修。** |
| T15 | API 意義變更待 Phase 7 釐清：`time_score` 變成 0／1、`match_reason` 恆為 null、`reasons` 含英文內部字串（1c） | 0（Phase 6 不改） | 改欄位內容會影響前端，屬 Phase 7 雙端協調。**記錄為 Phase 7 待辦。** |
| T16 | `apply_time_preferences` 的覆蓋鍵不含 `reference_date`／`resolved_dates`：跨過週一後說「這週可以」會覆蓋上週說的「這週不行」（雖然指的是不同週）（§1-A3） | 1：`time_preference_service.py` | 只由讀碼推得，未實測；TTL 2 小時內跨週才會發生。**列入待辦，不主動修。** |

## 5. 未能確認的項目與限制
- 未執行 pytest；所有 [測試名稱] 只代表測試存在，不代表通過。
- **需 310（真 Cerebras＋正式 SQL）：**
  - AI 對「最好下午」「只能下午」「這週不行」等語句，實際輸出到 `time_preferences` 還是舊 `preferred_*`；兩者同時輸出的頻率（T4）。
  - 不合格時間偏好被靜默丟棄的頻率，特別是 exclude（T6）。
  - AI 對絕對日期（例如「11 月 3 號」）輸出的年份是否正確（T7）。
  - 正式 SQL `Schedule.session` 與 `status` 的實際值是否都在 `SESSION_ALIASES` 與開放／關閉清單內（3a 附註）。
  - 熱門科別的班表筆數、去重後醫師數、有專長資料的醫師數是否超過容量 40；超過時整批中性的實際比例。
  - SQL 耗時、專長評分每批延遲與 12 秒預算是否足夠、`/recommend` 總耗時；逐批串行是否需要調整（4d、§3.E）。
  - `/generate_script` 在修訂／urgent 後使用舊 `recommendation_id` 的實際行為（T2、T3）。
- **需官方或院方資料：** 當日掛號截止時間、各時段時鐘範圍（T11）；「這週」的週起始日是否應以週一計算（2e④）。
- **需基準樣本：** 舊版 `match_reason`、`time_score` 的實際內容與量尺（1c）；目前 repo 沒有 Phase 6 前的 `/recommend` 回應樣本。
- **其他限制：** in-memory case／推薦表在重啟或多 worker 時遺失（`case_store.py:27-30`，AUDIT_PHASE4 §3.C 風險 3），這也是 2g 快照注入情境的前提；`/followup`（return_visit）與 quick_search 的排序未審查；Android 如何解讀 `score`、`is_best_match`、`reasons` 不在範圍內（Phase 7）。
