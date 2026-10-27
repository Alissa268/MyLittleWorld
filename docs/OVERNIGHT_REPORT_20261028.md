# 夜間施工報告（2026-10-28）

分支 `New_Android_Backend`。依序執行 A、B、C 三項，三項全部完成並已 push，沒有跳過或 stash 的項目。未執行 Commit 6、步驟 8～11、AUDIT_PHASE6 T4/T6/T9/T10/T14 或任何清理。未修改 `android/`、`backend/.env`、TTAS 規則、`knowledge/`、SQL schema、`department_clues.py`、線索題決定規則、推薦排序、`time_preference_service` 時間排序；沒有刪除、改名或改型別既有 API 欄位；沒有用關鍵字或 regex 解析病人文字。

所有測試都在本機離線執行（AI 與 SQL 以 mock 或合成資料代替），**尚未在 310 以真 AI、真 SQL 驗證**。

## 總覽

| 項目 | 結果 | commit | 全套測試（passed / failed / errors / subtests） |
|---|---|---|---|
| A　API 契約範例與契約測試 | 完成 | `4175ed0` | 873 / 0 / 0 / 93 |
| B　/generate_script 安全 gate | 完成 | `f8ead93` | 890 / 0 / 0 / 93 |
| C　科別決定後詢問看診時間 | 完成 | `722c1ce` | 898 / 0 / 0 / 93 |

每個 commit push 後都以 `git ls-remote` 確認 remote hash 與本機一致。

---

## A｜步驟 6：API 契約範例與契約測試（AUDIT_PHASE6 T1）

**修改的檔案**
- `docs/PHASE6_BACKEND_API_CONTRACT.md`：新增「回應範例」一節（中文）。
- `backend/tests/test_phase6_api_contract.py`（新增，8 個測試）。

**內容**
- 範例由契約測試的合成資料在本機實際呼叫 API 擷取：`/recommend` 成功（兩欄）、無班表 503、被擋（急迫、未完成、安全篩檢未完成、未確認皆為 400）、AI 專長評分失敗（整批 0.5，仍 200）、`/generate_script` 成功、`/chat` 一般回覆（進入安全篩檢）。
- 契約測試檢查：頂層欄位、單筆欄位集合（21 個欄位）與型別（`recommendation_id`／`doctor_id`／`schedule_id` 非空字串、`dept_id` 整數、`date` ISO、`session` 非空字串、`specialty_tags` 字串或 null）、`score` 0～100、`specialty_score`／`time_score` 0～1、每欄最多 5 筆、`recommendation_id` 不重複、`total_count`。
- `total_count` 依現有程式（`appointment_service.py`）定義為「兩欄實際回傳筆數相加」，同一班表出現在兩欄會算兩次；不是 SQL 班表筆數、也不是醫師數。已寫入文件與測試，未改程式。

**契約破壞：** 沒有發現，沒有 xfail。

**改了預期的既有測試：** 無。

**自己的選擇**
- AI 失敗測試除了檢查 0.5，也確認 AI 確實被呼叫過（否則無 AI 時本來就是 0.5，測不到失敗路徑）。
- `/chat` 範例用 mock 的 Turn Interpreter 與 planner 產生「症狀已足夠、進入安全篩檢」的一般回覆；無 AI 時只會得到技術退路文字，不適合當範例。

---

## B｜步驟 7：/generate_script 安全 gate（AUDIT_PHASE6 T2、T3、T13）

**修改的檔案（正式程式 4 個，未超出允許範圍）**
- `backend/app/routes/generate_script.py`：gate 與掛號別檢查。
- `backend/app/services/case_store.py`：新增 `find_recommendation_case_id`、`clear_recommendations`、`case_is_urgent`（既有函式未改）。
- `backend/app/routes/chat.py`：修訂、急迫、科別改變時清除推薦。
- `backend/app/services/quick_search_service.py`：`revalidate_schedule` 新增可選參數 `expected_visit_type`。
- 測試：`backend/tests/test_generate_script_gate.py`（新增，17 個測試）。
- 文件：`docs/修改計畫.md` 新增「6-G-1 /generate_script 安全 gate（2026-10-28）」。`AUDIT_PHASE6.md` 未改。

**規則**
1. 已儲存推薦的問診案件產生腳本前，依 `/recommend` 的順序、狀態碼（400）與原文訊息檢查：非急迫 → 問診完成 → 安全篩檢完成 → 已確認。
2. 修訂（`revision_requested` 與確認後自動修訂）、該輪結束時為急迫、該輪科別與輪初不同 → 清掉該 case 的推薦，舊 `recommendation_id` 回 404。
3. 帶 `case_id` 只在該 case 的推薦裡找（原本就是如此，已加測試）。不帶 `case_id` 的跨 case 查找保留相容，但會以擁有該推薦的案件套用 gate；是否移除留待 Phase 7。
4. 重驗班表時案件 `visit_type` 必須與班表掛號別一致，不符回 409；只有 `/generate_script` 傳入，`/schedules/search` 不變。

**測試結果：** 新測試在舊程式上 14 個失敗（其餘 3 個是原本就正確的行為：正常流程、別的 case_id 回 404、`/schedules/search` 相容），新程式全過。Quick Search 的既有 `/generate_script` 測試（`test_quick_search.py`）全部仍通過。

**改了預期的既有測試：** 無。

**自己的選擇與理由**
- **gate 沒有抽成與 `/recommend` 共用的函式。** `/recommend` 的判斷寫在 `routes/recommend.py` 路由內，而本項不允許修改該檔。改為在 `generate_script.py` 以相同條件、相同順序、相同訊息實作，急迫判斷抽到 `case_store.case_is_urgent` 共用；並加了參數化測試，逐一比對兩個端點在 5 種被擋狀態（急迫、TTAS level 2、未完成、安全篩檢未完成、未確認）× 有無 `case_id` 的狀態碼與訊息完全一致。若之後允許修改 `recommend.py`，建議讓 `/recommend` 也改呼叫同一個函式，消除重複。
- **gate 只套用在 `initial`／`followup` 案件。** 回診 `/followup/recommend`（`return_visit`）也會儲存推薦，但不走問診與確認流程，套用 gate 會讓回診永遠無法產生腳本；它的班表查詢也刻意不解讀掛號別，所以掛號別檢查同樣不套用。Quick Search 路徑完全不變。
- **找不到擁有推薦的案件時維持舊行為（不擋）。** 正式流程中推薦與案件一起建立、一起過期，只有測試會單獨存推薦；為了不改變既有行為而保守處理。
- **「科別改變」以該輪開始與結束時的 `department_result.dept_id` 比較。** 推薦只在確認後才存在，因此等同「確認後科別改變」。

---

## C｜步驟 7a：科別決定後詢問看診時間

### 開工前三點調查結論

1. **Turn Interpreter 的 `pending_answer` 能否用在非醫療的 opaque intent？可以。** `_pending_answer_from_ai`（`rag_triage_adapter.py`）只要求 `answered_intent` 原樣等於 pending intent、`answer_source_text` 在本輪原文中、狀態與信心合法，沒有限制 intent 必須是醫療面向；`capture_pending_answer`（`conversation_service.py`）同樣通用，只有安全篩檢與線索題有特殊處理。答案會記入 `clarification_evidence[intent]` 並清除 pending。另外，若病人答非所問且沒有醫療證據，既有的 `unresolved_pending_turn` 會觸發一次修復，仍不合格時該輪標為解讀未完成；這不影響時間問題（Backend 會照規則再問或轉 `unspecified`）。為了讓「都可以」可靠地判為 answered，在 Turn Interpreter prompt 補了一條 15c（只描述問句內容，不解析 key 名稱）。
2. **已確認後修改時間的現有路徑（修改計畫 §4）會不會受影響？不會。** 確認後再輸入文字會走自動修訂（`_begin_revision`），同一輪 Turn Interpreter 照常抽 `time_preferences`，科別重算後再進確認。本次 `_begin_revision` 不重設 `availability_status`，已回答過的案件修訂後不會再問時間；修訂當輪若帶了新的時間資料，也會直接視為已回答。唯一的新行為：在本功能上線前就已確認、且從未提供時間的舊案件，若修訂症狀，會在重新確認前被問一次時間。
3. **修改的檔案：** `app/schemas.py`、`app/services/conversation_service.py`、`app/routes/chat.py`、`app/services/rag_triage_adapter.py`，正好是允許的 4 個；不需要動線索題決定規則、安全篩檢或 TTAS。

### 修改的檔案
- `backend/app/schemas.py`：`ConversationState` 新增 `availability_status`（預設 `not_asked`）、`availability_question_attempts`（預設 0）。只新增欄位，舊快照可載入；不受信任的快照本來就整個重設 `ConversationState`。
- `backend/app/services/conversation_service.py`：固定問句、`appointment_availability` intent、`availability_settled`／`awaiting_availability_answer` 等函式，以及 `advance_conversation` 在原本進入 CONFIRMATION 前插入時間步驟。
- `backend/app/routes/chat.py`：等待時間回答（且科別、安全狀態不變）時不呼叫 planner。
- `backend/app/services/rag_triage_adapter.py`：prompt 新增 15c。
- 測試：`backend/tests/test_availability_question.py`（新增，8 個測試）。
- 文件：`docs/修改計畫.md` Phase 6 最後新增「6-H 科別收斂後詢問看診時間」，含時間四層對應。

新測試涵蓋：收斂後問時間；具體時間 → 確認且寫入 `time_preferences`；「都可以」→ 確認且沒有時間條件；已講過時間不問；兩次答非所問 → `unspecified` 並確認（重問那輪不呼叫 planner）；回答時間時說出急迫症狀 → 急迫流程且 `availability_status` 保留；修訂不重問；時間未完成時 `/recommend` 回「尚未完成」。線索題相關測試全部通過。

### 改了預期的既有測試（7 個 helper／setup，共影響 13 個測試）

原因相同：這些測試驗證的是「醫療完成 → 正式科別 → CONFIRMATION」的既有關卡，本身沒有提供看診時間；新規格要求在 CONFIRMATION 前先問時間，所以它們改停在時間問題。為了保留原測試的意圖，**只在建案 setup 加一行 `availability_status = "answered"`（等同病人已經回答過時間）並加註解**，沒有改任何斷言，也沒有刪除或跳過測試。時間步驟本身由新測試檔驗證。

| 檔案 | 修改位置 | 受影響的測試 |
|---|---|---|
| `test_clue_decision.py` | `new_case()`、`decision_case()` | `test_present_answer_to_unique_clue_asks_confirmation_of_that_department`、`test_five_absent_answers_decide_the_highest_score`、`test_unknown_answers_lead_to_tiebreak_then_family_medicine_confirmation`、`test_ai_resolved_candidate_goes_straight_to_confirmation`、`test_backend_decision_counts_as_sufficient_but_ai_cannot_confirm` |
| `test_conversation_progress_limits.py` | `clinical_case()` | `test_completed_case_at_absolute_cap_still_uses_existing_confirmation_gate` |
| `test_natural_chat_state_repair.py` | `clinical_case()` | `test_second_d3_truly_sufficient_does_not_need_information_needs`、`test_control_prior_reference_precedes_meta_pending_answer_with_new_evidence`、`test_control_invalid_prior_reference_falls_back_to_valid_direct_answer`（2 個參數） |
| `test_phase41_clarification.py` | `test_real_hematuria_two_turn_grounded_answer_clears_pending` 的初始案件 | 同左 |
| `test_phase4_department_convergence.py` | `test_resolved_case_safety_then_confirmation_keeps_validated_result` 的第一輪 mock（該測試第一輪用客戶端快照，狀態會被重設，所以在 mock 內設定） | 同左 |
| `test_phase4_pending_answer_classification.py` | `pending_pain_case()` | `test_chat_orders_semantic_classifier_department_and_planner_and_avoids_neutral_retry` |

### 自己的選擇與理由
- **時間問題用 Backend 固定問句，不經 planner。** 規格指定固定句；planner 的輸出過濾（`_UNSAFE_QUESTION`）本來就會擋下「看診時段」類問句。
- **「已回答」只看結構化結果：** 已接受的時間資料，或 `clarification_evidence` 有 `appointment_availability`（Turn Interpreter answered＋grounded＋達門檻）。Backend 不讀病人原文判斷。
- **回答時間那一輪的完成判斷：** 只要上一輪已問時間（`availability_status=asked`），本輪就視同醫療已足夠，但安全篩檢、grounded 症狀與細節、正式科別仍須通過；若新症狀讓科別不再是 resolved，就釋放時間 pending，回到一般問診，`availability_status` 保留為 `asked`。
- **急迫流程的測試** 用模擬的 TTAS level 1 結果（`warning_required`、`is_final`）驗證流程銜接；真正的 TTAS 規則判斷由 Phase 5 測試負責，也需在 310 實測。
- **已知限制（未處理，記錄於此）：**
  - `/recommend` 的舊 `userQuery` 路徑（沒有 `/chat` 案件時直接用一句話建案）會自行把 `is_complete` 設為 true，不經時間步驟；該路徑在 `routes/recommend.py`，不在本項允許範圍。
  - 若回答時間那輪新症狀讓科別改變、之後又被重算為 resolved，下一次會直接進時間或確認步驟，即使 planner 想再追問；屬少見情境。

---

## 跳過或卡住的項目

無。沒有建立任何 stash。

## 明天要在 310 驗證的項目

以下以 PowerShell `Invoke-RestMethod` 直接打 Backend（真 Cerebras、真 SQL），不測 Android。`$B` 為 Backend URL。

### /chat：時間問題（C）
1. 新案件：`/chat` `{"message":"右膝蓋痛三天，走路會痛","visit_type":"initial"}` → 依序回答安全篩檢「都沒有」與線索題，直到科別決定。
   - 預期：科別決定的那一輪 `reply` 為固定時間問句，`conversation_state.reply_source=AVAILABILITY_QUESTION`、`availability_status=asked`、`stage=collecting`、`department_result` 已有值。
2. 回答「下週二下午比較方便，週四不行」→ 預期 `stage=waiting_confirmation`、`availability_status=answered`、`triage_case.availability.time_preferences` 有 prefer 與 exclude 兩筆，日期已換算成實際日期。
3. 另一個案件在時間問題回答「都可以」→ 預期直接 `waiting_confirmation`，`time_preferences` 為空。**這要確認真 AI 會把「都可以」判成 answered**（prompt 15c）；若沒有，會再問一次，第二次後轉 `unspecified`。
4. 另一個案件第一句就說時間（例如「膝蓋痛三天，只能下午看診」）→ 預期科別決定時不問時間，直接 `waiting_confirmation`。
5. 時間問題時回答「今天天氣很好」兩次 → 第一次重問同一句、`availability_question_attempts=2`；第二次 `availability_status=unspecified` 並進入 `waiting_confirmation`。
6. 時間問題時回答「下午可以，可是現在突然胸口很痛、喘不過氣」→ 預期走急迫流程（`clarification_status=urgent`、`stage=done`、回覆為急迫警示）。
7. `waiting_confirmation` 時改說「其實是左膝」→ 自動修訂，重新確認時不再問時間。
8. 觀察 `reply` 是否與固定問句一字不差（若 `generate_triage_reply` 在 310 開啟改寫，需確認不會改掉）。

### /recommend
9. 時間問題尚未回答時呼叫 `/recommend {"case_id":"…","confirmed":true}` → 預期 400「triage_case 尚未完成，請先完成 /chat 多輪問答。」
10. 完成確認後 `/recommend {"case_id":"…"}` → 預期 200，兩欄各最多 5 筆，`total_count`＝兩欄筆數相加；有說「週四不行」的案件，週四班表不得出現；欄位型別與 `docs/PHASE6_BACKEND_API_CONTRACT.md` 範例一致。
11. 模擬 AI 專長評分失敗（例如暫時把 `DOCTOR_SCORING_TOTAL_TIMEOUT_SECONDS` 調到極小值）→ 預期仍 200，所有 `specialty_score=0.5`。

### /generate_script（B）
12. 正常：取上一步任一 `recommendation_id`，`/generate_script {"case_id":"…","recommendation_id":"…"}` → 預期 200、10 個步驟，`recommendation` 以 SQL 重驗後的值為準。
13. 修訂後舊 ID：對同一 case 送 `/chat {"case_id":"…","revision_requested":true}`，再用舊 `recommendation_id` 呼叫 → 預期 404。
14. 別的 case：用 A 案件的 `recommendation_id` 搭配 B 案件的 `case_id` → 預期 404。
15. 不帶 `case_id`：只送 `recommendation_id` → 預期仍 200（舊相容路徑），但若擁有該推薦的案件已變成急迫或未確認，應回 400，訊息與 `/recommend` 相同。
16. 掛號別：以初診案件取得推薦後，若能在 310 找到同一 `schedule_id` 掛號別為複診的資料（或請院方協助），預期 409「指定班表的掛號別與問診案件不一致。」；若真實資料一個 `schedule_id` 同時支援初／複診，記錄實際 `visit_type` 欄位格式。
17. Quick Search：`/schedules/search` 取得 `qs_` 開頭結果後 `/generate_script {"case_id":"quick_…","recommendation_id":"qs_…"}` → 預期 200，行為與先前相同。
18. 回診：`/followup/recommend` 後 `/generate_script` → 預期 200（回診不套用 gate 與掛號別檢查）。
