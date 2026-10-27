# Phase 6 Backend Recommendation Contract

## Scope

Phase 6 changes Backend interpretation and recommendation ranking only. Android, Department convergence, TTAS, SQL schema/data, doctor scoring inputs, and navigation-script contracts are not redesigned.

## Time preferences

`Availability` retains `preferred_dates`, `preferred_days`, `preferred_sessions`, and `can_take_leave`. It also accepts additive `time_preferences` facts with:

- `kind`: `date`, `date_range`, `relative_week`, `relative_weekday`, `weekday`, `weekday_group`, or `session`
- `relation`: `exclude`, `prefer`, or `acceptable`
- `priority`: required for `prefer`/`acceptable`; absent for `exclude`
- grounded `source_text` and finite `confidence`
- English machine identities for weekday/session values
- `reference_date` and frozen `resolved_dates`

The existing Turn Interpreter proposes these facts in the same call that returns semantic, pending-answer, and TTAS evidence. Backend validates grounding and structure. It resolves relative dates against `Asia/Taipei` once, when accepting the fact, so a later `/recommend` request cannot shift its meaning.

`exclude` is a hard filter and is never relaxed. `prefer` does not prohibit unmentioned dates or sessions. Thus `下午最好` prefers afternoon, while `只能下午` requires explicit morning/evening exclusions from the grounded AI interpretation.

Legacy and new Availability data are merged per dimension. Any new date-kind fact owns the date dimension and supersedes legacy `preferred_dates`/`preferred_days`, while legacy `preferred_sessions` remains effective if no new session fact exists. Any new session fact owns the session dimension, while legacy date fields remain effective if no new date fact exists. With no `time_preferences`, legacy behavior is unchanged. New hard exclusions are applied before any legacy matching or `can_take_leave` relaxation and can never be reintroduced.

## Schedule truth and ranking

SQL remains the schedule and identity authority. A formal recommendation requires exact `dept_id`, `doctor_id`, `schedule_id`, date, session, compatible visit type, active/non-placeholder availability, and a non-past/open session. Missing `doctor_id` never falls back to doctor name.

Complex preferences are evaluated after SQL retrieval. Date and session dimensions form deterministic preference vectors. Non-dominated rows form tier 0; later Pareto fronts form subsequent tiers. No date/session weights are invented. Within one tier, actual date/session time is earlier first.

The two stable orders are:

- `specialty_first`: specialty relevance descending, time tier, actual datetime, doctor ID, schedule ID.
- `time_first`: time tier, actual datetime, specialty relevance descending, doctor ID, schedule ID.

Legacy `score`, `specialty_score`, and `time_score` fields remain type-compatible, but `score` and `time_score` do not control ordering. The former 70/30 formulas are not ranking truth.

## Specialty relevance

Schedule rows are grouped by real `doctor_id` before AI scoring. Each unique doctor is presented once with SQL `specialty_tags` and validated current patient semantic evidence. The returned score is only symptom-to-specialty-tag semantic relevance; it is not diagnosis confidence, physician quality, or outcome probability. Natural-language reasons are not required.

Doctors are sent in configurable small batches. In addition to the provider's per-call timeout, the complete scoring operation has a configurable monotonic-clock deadline (`DOCTOR_SCORING_TOTAL_TIMEOUT_SECONDS`, default 12 seconds). Each batch receives only the remaining total budget; no later batch starts after the deadline.

AI scores are used only if every scorable doctor is returned exactly once with a finite score in `[0, 1]`. Total-budget timeout, per-call timeout, malformed JSON, missing/extra/duplicate IDs, invalid score, or capacity overflow makes the entire candidate set neutral (`0.5`), including batches that succeeded before a later failure. Doctors without specialty tags are neutral and are not sent to AI. No medical keyword scorer is used as fallback.

## Response compatibility

`/recommend` continues to return `specialty_first` and `time_first`, each with at most five concrete Schedule recommendations. Each item keeps its existing navigation identity and can be revalidated before `/generate_script`.

Phase 6 does not change the outward response to one doctor card with expandable alternative slots. A doctor may therefore occupy more than one concrete Schedule row in a column even though specialty scoring occurs once per `doctor_id`. The one-card presentation belongs to Phase 7.

## Remaining live validation

Code-level tests use synthetic Schedule rows and mocked AI responses. Final validation still requires the 310 environment to confirm real SQL column values, actual specialty tag coverage, current session cutoffs, timezone deployment, provider batch reliability, and end-to-end script generation against a live Schedule row.

## 回應範例（2026-10-28，AUDIT_PHASE6 T1）

以下範例由契約測試 `backend/tests/test_phase6_api_contract.py` 使用的合成資料在本機實際呼叫 API 擷取（離線、AI 以 mock 代替、班表為假資料），不含真實病人或醫師資料。日期會隨執行日變動；為節省篇幅，每欄只列第 1 筆。

### 契約重點（契約測試逐項檢查）

- 回應頂層欄位固定為 `case_id`、`recommendations`（`specialty_first`、`time_first` 兩欄）、`fallback_departments`、`total_count`。
- 每欄最多 5 筆；同一 `recommendation_id` 不會重複出現。
- 單筆欄位集合固定為：`recommendation_id`、`parentDept`、`childDept`、`doctor`、`date`、`session`、`slot`、`doctor_id`、`schedule_id`、`session_time`、`room`、`visit_type`、`specialty_tags`、`specialty_score`、`time_score`、`match_reason`、`score`、`reasons`、`rank`、`is_best_match`、`dept_id`。
- 型別：`recommendation_id`、`doctor_id`、`schedule_id` 為非空字串；`dept_id` 為整數；`date` 為 ISO `YYYY-MM-DD`；`session` 為非空字串；`specialty_tags` 為字串或 `null`（SQL 原文，不拆陣列）；`reasons` 為字串陣列；`is_best_match` 為布林。
- 分數量尺：`score` 在 0～100（相容顯示值，不作排序）；`specialty_score` 與 `time_score` 在 0～1。
- `total_count`＝兩欄實際回傳筆數相加（`len(specialty_first) + len(time_first)`，見 `appointment_service.py`）。同一班表同時出現在兩欄會算兩次；它不是 SQL 班表筆數，也不是去重後醫師數。
- AI 專長評分失敗（逾時、格式錯誤等）時，整批所有醫師的 `specialty_score` 都是中性 0.5，仍回 200。

### /recommend 成功（200，兩欄；8 筆班表時兩欄各 5 筆，`total_count`=10）

```json
{
  "case_id": "contract-case",
  "recommendations": {
    "specialty_first": [
      {
        "recommendation_id": "rec_s_contract-case_001",
        "parentDept": "外科系",
        "childDept": "一般骨科",
        "doctor": "測試醫師0",
        "date": "2026-10-29",
        "session": "上午",
        "slot": "3201診",
        "doctor_id": "D000",
        "schedule_id": "S0000",
        "session_time": "08:30-12:00",
        "room": "3201診",
        "visit_type": "初診",
        "specialty_tags": "膝關節、運動傷害",
        "specialty_score": 0.5,
        "time_score": 1.0,
        "match_reason": null,
        "score": 50.0,
        "reasons": [
          "推薦理由：符合目前推薦科別",
          "科別依據：使用者症狀「膝蓋痛」，目前建議科別為 一般骨科",
          "時間依據：使用者未指定日期/時段偏好，Schedule 為 2026-10-29 週四 上午",
          "狀態依據：Schedule.status=open，判斷為可掛號",
          "專長依據：SQL specialty_tags=膝關節、運動傷害；specialty semantic relevance=0.50；AI 專長語意評分未完整可用，本次所有候選統一使用中性值 0.50",
          "排序依據：specialty_score → time preference tier → datetime → doctor_id；specialty_score=0.50；time preference tier=0；相容顯示 score=50.00，不作排序權重",
          "掛號別：初診"
        ],
        "rank": 1,
        "is_best_match": true,
        "dept_id": 1298
      }
    ],
    "time_first": [
      {
        "recommendation_id": "rec_t_contract-case_001",
        "doctor_id": "D000",
        "schedule_id": "S0000",
        "dept_id": 1298,
        "date": "2026-10-29",
        "session": "上午",
        "specialty_score": 0.5,
        "time_score": 1.0,
        "score": 100.0,
        "rank": 1,
        "is_best_match": false,
        "…": "其餘欄位與 specialty_first 相同結構"
      }
    ]
  },
  "fallback_departments": [],
  "total_count": 10
}
```

### /recommend 無班表（503）

```json
{"detail": "目前找不到符合條件的一般骨科班表。"}
```

### /recommend 被擋（皆為 400）

| 狀態 | `detail` |
|---|---|
| 急迫（`clarification_status=urgent` 或 TTAS level 1/2） | `目前急迫性篩檢結果不適合繼續一般掛號推薦，請優先尋求醫療評估。` |
| 問診未完成（`is_complete` 不是 true 或 `need_more_info`） | `triage_case 尚未完成，請先完成 /chat 多輪問答。` |
| 安全篩檢未完成 | `急迫症狀篩檢尚未完成，請先返回 /chat 確認安全狀況。` |
| 尚未確認 | `triage_case 尚未確認，請先以 /chat 傳入 confirmed=true。` |

其他既有錯誤碼（本次未改）：`visit_type` 與案件不一致 409、缺 `visit_type` 422、`quick_search` 改走 `/schedules/search` 400、無已驗證科別 422、SQL 無法查詢 503。

### /recommend AI 專長評分失敗（200，整批中性 0.5）

AI 呼叫逾時時仍回 200，兩欄每一筆的 `specialty_score` 都是 `0.5`，`reasons` 的專長依據寫「AI 專長語意評分未完整可用，本次所有候選統一使用中性值 0.50」。單筆結構同上。

### /generate_script 成功（200）

```json
{
  "isSuccess": true,
  "script_id": "vgh_booking_001",
  "recommendation_id": "rec_s_contract-script_001",
  "recommendation": {"recommendation_id": "rec_s_contract-script_001", "…": "與 /recommend 單筆相同欄位，重驗後以 SQL 為準"},
  "steps": [
    {"action": "open_app", "target": "台北榮民總醫院app", "resource_id": null, "text": "台北榮民總醫院app",
     "class_name": null, "description": "開啟台北榮民總醫院掛號 App", "delay_ms": 1200, "retry": 1},
    {"action": "click", "target": "行動掛號", "resource_id": null, "text": "行動掛號",
     "class_name": "android.widget.TextView", "description": "點擊首頁的行動掛號入口", "delay_ms": 500, "retry": 2}
  ],
  "message": "已產生導引劇本：一般骨科 / 測試醫師1 / 2026-10-30",
  "step_count": 10
}
```

10 個步驟的 `target` 依序為：台北榮民總醫院app、行動掛號、繼續掛號、依門診科別、外科系、一般骨科、選擇看診時間/醫師、2026-10-30、3201診測試醫師1、填寫個人資料。

### /chat 一般回覆（200，症狀已足夠、進入安全篩檢）

頂層欄位：`case_id`、`triage_case`、`conversation_state`、`triage`、`department_result`、`next_question`、`reply`、`needMoreInfo`、`triage_reasons`、`question_batch`。`triage_case` 為完整案件快照，以下省略。

```json
{
  "case_id": "case_…",
  "reply": "請問是否有突發胸痛、嚴重呼吸困難、意識不清、大量出血、半邊無力或劇烈頭痛等急迫症狀？",
  "next_question": "請問是否有突發胸痛、嚴重呼吸困難、意識不清、大量出血、半邊無力或劇烈頭痛等急迫症狀？",
  "needMoreInfo": true,
  "department_result": null,
  "triage_reasons": [
    "TTAS evidence is insufficient; no urgency level was inferred or defaulted.",
    "症狀描述已足夠，仍須完成既有急迫症狀篩檢。"
  ],
  "question_batch": [],
  "conversation_state": {
    "stage": "collecting",
    "clarification_status": "safety_check",
    "reply_source": "SAFETY_CONTROLLED_FALLBACK",
    "is_complete": false,
    "awaiting_confirmation": false,
    "confirmed": false,
    "department_status": "unresolved",
    "pending_clarification_intent": null,
    "…": "其餘 conversation_state 欄位省略"
  },
  "triage": {"need_more_info": true, "is_final": false, "urgency_level": null, "…": "其餘省略"},
  "triage_case": "…"
}
```
