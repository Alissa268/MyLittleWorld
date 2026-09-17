# 固定問診音檔目錄（0912）

本目錄以 2026-09-12 目前工作樹為準，只盤點患者在 Android 問診畫面可能看見或透過訊息播放鍵朗讀的文案。未修改問診內容，也未呼叫任何 LLM、Voice Gateway 或音訊生成服務。

## 結論摘要

- 核心題庫的唯一來源是 `backend/app/services/question_specs.py::QUESTION_SPECS`。目前確實是 **7 類 × 5 個 variant = 35 句**。
- 35 句不是備用草稿：`select_question_variant()` 使用 `random.choice()`，因此每一個 variant 都可能在 runtime 被選到。
- `fixed_sentences.py` 只完整涵蓋核心 35 句與部分澄清句，**不是所有患者可見文案的完整 source of truth**。它還包含目前不可達的 `can_take_leave` 澄清、未被 runtime 引用的 guidance，以及一個與現行 `/chat` 確認回覆不同的舊 `confirm_department` 文案。
- 目前 Android 的主要文字問診路徑不是讀取 `reply_audio_id`：AI 訊息會由 `ChatViewModel.prepareSpeech()` / `speakMessage()` 經 `TtsSession` 呼叫 `/voice/tts`。因此未來要改成本地 `res/raw`，仍需另外建立「訊息文字或 sentence ID → raw resource」的 Android 對照；本次只產生清單，沒有做整合。
- 最小必要固定包為 **45 句**：核心 35 句、目前可達澄清 8 句、固定系統回覆 2 句。每種語言各 45 個檔案，國語＋台語共 90 個。
- 另有 20 句 OPTIONAL 固定文字（顯示但目前無播放入口、只在防禦性/API 分支、或目前 runtime 不可達）。全部也製作時，每種語言為 65 個，兩種語言共 130 個。
- 目前正常 Android 問診對話中有 **1 種必須維持動態 TTS 的句型**：包含正式科別名稱的確認句。

## Runtime 使用鏈

```text
QUESTION_SPECS
  -> rule_engine._question_text_for()
  -> select_question_variant() / clarification_prompt()
  -> batch_question_service.build_question_batch()（目前每批最多 1 題）
  -> routes/chat.py: TriageResult.question_batch / reply
  -> MedicalDtos.parseTriageResult()
  -> ChatViewModel.currentBatchQuestion / consumeTriageResponse()
  -> ChatMessage(sender = AI)
  -> ChatScreen.RealBubbleItem
  -> ChatViewModel.prepareSpeech() / speakMessage()
  -> TtsSession
  -> POST /voice/tts
```

補充：`backend/app/routes/voice.py::_FIXED_LOOKUP` 只供舊 `/voice/chat` 路徑以文字反查 `audio_id`。目前 Android 真機問診是逐題 `/chat` 加獨立 `/voice/tts`，而 `MedicalDtos.kt` 的現行 `VoiceChatResponseDto` 也沒有消費 `reply_audio_id`。

## 判定規則

- `REQUIRED`：目前 Android 正常問診可形成 AI `ChatMessage`，因而會進入目前的 TTS prefetch／播放路徑。
- `OPTIONAL`：固定文字存在，但目前只顯示、不具播放入口，或只在防禦性/API/舊語音分支使用，或標記為 `UNUSED_CURRENT_RUNTIME`。
- `DYNAMIC_NOT_PREGENERATED`：包含 runtime 資料，不能用單一固定音檔取代。
- `UNUSED_CURRENT_RUNTIME`：程式中有固定字串，但依目前 checklist、呼叫圖或全域引用搜尋，正常 runtime 不會走到。

## 完整目錄

| ID | 類別 | Variant | 程式實際文字 | Source file | Source symbol | 是否固定 | 建議國語檔名 | 建議台語檔名 | 是否必要 | Runtime status / 備註 |
|---|---|---:|---|---|---|---|---|---|---|---|
| triage_symptom_01 | A. 核心問診 / symptom | 01 | 請描述目前最主要的不舒服症狀。 | backend/app/services/question_specs.py | `QUESTION_SPECS[question_id="symptom"].variants` | YES | triage_symptom_01_zh.m4a | triage_symptom_01_taigi.m4a | REQUIRED | RUNTIME_REACHABLE |
| triage_symptom_02 | A. 核心問診 / symptom | 02 | 想先了解一下，你現在最主要是哪裡不舒服？ | backend/app/services/question_specs.py | `QUESTION_SPECS[question_id="symptom"].variants` | YES | triage_symptom_02_zh.m4a | triage_symptom_02_taigi.m4a | REQUIRED | RUNTIME_REACHABLE |
| triage_symptom_03 | A. 核心問診 / symptom | 03 | 請問目前最困擾你的症狀是什麼？ | backend/app/services/question_specs.py | `QUESTION_SPECS[question_id="symptom"].variants` | YES | triage_symptom_03_zh.m4a | triage_symptom_03_taigi.m4a | REQUIRED | RUNTIME_REACHABLE |
| triage_symptom_04 | A. 核心問診 / symptom | 04 | 方便說明一下，你這次最想處理的不舒服嗎？ | backend/app/services/question_specs.py | `QUESTION_SPECS[question_id="symptom"].variants` | YES | triage_symptom_04_zh.m4a | triage_symptom_04_taigi.m4a | REQUIRED | RUNTIME_REACHABLE |
| triage_symptom_05 | A. 核心問診 / symptom | 05 | 請告訴我，目前最主要的身體不適是什麼？ | backend/app/services/question_specs.py | `QUESTION_SPECS[question_id="symptom"].variants` | YES | triage_symptom_05_zh.m4a | triage_symptom_05_taigi.m4a | REQUIRED | RUNTIME_REACHABLE |
| triage_emergency_symptoms_01 | A. 核心問診 / emergency_symptoms (`red_flags`) | 01 | 請問是否有突發胸痛、嚴重呼吸困難、意識不清、大量出血、半邊無力或劇烈頭痛等急迫症狀？ | backend/app/services/question_specs.py | `QUESTION_SPECS[question_id="emergency_symptoms"].variants` | YES | triage_emergency_symptoms_01_zh.m4a | triage_emergency_symptoms_01_taigi.m4a | REQUIRED | RUNTIME_REACHABLE；safety critical |
| triage_emergency_symptoms_02 | A. 核心問診 / emergency_symptoms (`red_flags`) | 02 | 想確認安全狀況：目前是否有突發胸痛、嚴重呼吸困難、意識不清、大量出血、半邊無力或劇烈頭痛？ | backend/app/services/question_specs.py | `QUESTION_SPECS[question_id="emergency_symptoms"].variants` | YES | triage_emergency_symptoms_02_zh.m4a | triage_emergency_symptoms_02_taigi.m4a | REQUIRED | RUNTIME_REACHABLE；safety critical |
| triage_emergency_symptoms_03 | A. 核心問診 / emergency_symptoms (`red_flags`) | 03 | 請確認一下，你現在有沒有突發胸痛、嚴重呼吸困難、意識不清、大量出血、半邊無力或劇烈頭痛？ | backend/app/services/question_specs.py | `QUESTION_SPECS[question_id="emergency_symptoms"].variants` | YES | triage_emergency_symptoms_03_zh.m4a | triage_emergency_symptoms_03_taigi.m4a | REQUIRED | RUNTIME_REACHABLE；safety critical |
| triage_emergency_symptoms_04 | A. 核心問診 / emergency_symptoms (`red_flags`) | 04 | 為了確認是否需要立即處理，請問有無突發胸痛、嚴重呼吸困難、意識不清、大量出血、半邊無力或劇烈頭痛？ | backend/app/services/question_specs.py | `QUESTION_SPECS[question_id="emergency_symptoms"].variants` | YES | triage_emergency_symptoms_04_zh.m4a | triage_emergency_symptoms_04_taigi.m4a | REQUIRED | RUNTIME_REACHABLE；safety critical |
| triage_emergency_symptoms_05 | A. 核心問診 / emergency_symptoms (`red_flags`) | 05 | 接著確認急迫症狀：你是否出現突發胸痛、嚴重呼吸困難、意識不清、大量出血、半邊無力或劇烈頭痛？ | backend/app/services/question_specs.py | `QUESTION_SPECS[question_id="emergency_symptoms"].variants` | YES | triage_emergency_symptoms_05_zh.m4a | triage_emergency_symptoms_05_taigi.m4a | REQUIRED | RUNTIME_REACHABLE；safety critical |
| triage_body_part_01 | A. 核心問診 / body_part | 01 | 請問症狀主要發生在哪個部位？ | backend/app/services/question_specs.py | `QUESTION_SPECS[question_id="body_part"].variants` | YES | triage_body_part_01_zh.m4a | triage_body_part_01_taigi.m4a | REQUIRED | RUNTIME_REACHABLE |
| triage_body_part_02 | A. 核心問診 / body_part | 02 | 想了解一下，這個不舒服主要在身體哪裡？ | backend/app/services/question_specs.py | `QUESTION_SPECS[question_id="body_part"].variants` | YES | triage_body_part_02_zh.m4a | triage_body_part_02_taigi.m4a | REQUIRED | RUNTIME_REACHABLE |
| triage_body_part_03 | A. 核心問診 / body_part | 03 | 請問你主要是哪個部位感到不適？ | backend/app/services/question_specs.py | `QUESTION_SPECS[question_id="body_part"].variants` | YES | triage_body_part_03_zh.m4a | triage_body_part_03_taigi.m4a | REQUIRED | RUNTIME_REACHABLE |
| triage_body_part_04 | A. 核心問診 / body_part | 04 | 方便告訴我，症狀集中在哪個位置嗎？ | backend/app/services/question_specs.py | `QUESTION_SPECS[question_id="body_part"].variants` | YES | triage_body_part_04_zh.m4a | triage_body_part_04_taigi.m4a | REQUIRED | RUNTIME_REACHABLE |
| triage_body_part_05 | A. 核心問診 / body_part | 05 | 這個症狀主要影響身體的哪個部位呢？ | backend/app/services/question_specs.py | `QUESTION_SPECS[question_id="body_part"].variants` | YES | triage_body_part_05_zh.m4a | triage_body_part_05_taigi.m4a | REQUIRED | RUNTIME_REACHABLE |
| triage_duration_01 | A. 核心問診 / duration | 01 | 請問這個症狀大約持續多久了？ | backend/app/services/question_specs.py | `QUESTION_SPECS[question_id="duration"].variants` | YES | triage_duration_01_zh.m4a | triage_duration_01_taigi.m4a | REQUIRED | RUNTIME_REACHABLE |
| triage_duration_02 | A. 核心問診 / duration | 02 | 想了解一下，這個不舒服大概持續多久了呢？ | backend/app/services/question_specs.py | `QUESTION_SPECS[question_id="duration"].variants` | YES | triage_duration_02_zh.m4a | triage_duration_02_taigi.m4a | REQUIRED | RUNTIME_REACHABLE |
| triage_duration_03 | A. 核心問診 / duration | 03 | 這個症狀大約是從什麼時候開始的？ | backend/app/services/question_specs.py | `QUESTION_SPECS[question_id="duration"].variants` | YES | triage_duration_03_zh.m4a | triage_duration_03_taigi.m4a | REQUIRED | RUNTIME_REACHABLE |
| triage_duration_04 | A. 核心問診 / duration | 04 | 方便告訴我，這個情況已經持續多久了嗎？ | backend/app/services/question_specs.py | `QUESTION_SPECS[question_id="duration"].variants` | YES | triage_duration_04_zh.m4a | triage_duration_04_taigi.m4a | REQUIRED | RUNTIME_REACHABLE |
| triage_duration_05 | A. 核心問診 / duration | 05 | 請問你大概多久以前開始出現這個症狀？ | backend/app/services/question_specs.py | `QUESTION_SPECS[question_id="duration"].variants` | YES | triage_duration_05_zh.m4a | triage_duration_05_taigi.m4a | REQUIRED | RUNTIME_REACHABLE |
| triage_severity_01 | A. 核心問診 / severity | 01 | 請問症狀程度是輕微、中等、明顯，還是很痛或影響生活？ | backend/app/services/question_specs.py | `QUESTION_SPECS[question_id="severity"].variants` | YES | triage_severity_01_zh.m4a | triage_severity_01_taigi.m4a | REQUIRED | RUNTIME_REACHABLE |
| triage_severity_02 | A. 核心問診 / severity | 02 | 想了解一下，目前不舒服的程度大約是輕微、中等還是嚴重？ | backend/app/services/question_specs.py | `QUESTION_SPECS[question_id="severity"].variants` | YES | triage_severity_02_zh.m4a | triage_severity_02_taigi.m4a | REQUIRED | RUNTIME_REACHABLE |
| triage_severity_03 | A. 核心問診 / severity | 03 | 請問這個症狀現在的嚴重程度如何？ | backend/app/services/question_specs.py | `QUESTION_SPECS[question_id="severity"].variants` | YES | triage_severity_03_zh.m4a | triage_severity_03_taigi.m4a | REQUIRED | RUNTIME_REACHABLE |
| triage_severity_04 | A. 核心問診 / severity | 04 | 這個不舒服有沒有明顯到影響你的日常生活？ | backend/app/services/question_specs.py | `QUESTION_SPECS[question_id="severity"].variants` | YES | triage_severity_04_zh.m4a | triage_severity_04_taigi.m4a | REQUIRED | RUNTIME_REACHABLE |
| triage_severity_05 | A. 核心問診 / severity | 05 | 方便描述一下，症狀目前是輕微、普通，還是相當嚴重嗎？ | backend/app/services/question_specs.py | `QUESTION_SPECS[question_id="severity"].variants` | YES | triage_severity_05_zh.m4a | triage_severity_05_taigi.m4a | REQUIRED | RUNTIME_REACHABLE |
| triage_preferred_days_01 | A. 核心問診 / preferred_days | 01 | 請問你最近哪幾天有空就醫？ | backend/app/services/question_specs.py | `QUESTION_SPECS[question_id="preferred_days"].variants` | YES | triage_preferred_days_01_zh.m4a | triage_preferred_days_01_taigi.m4a | REQUIRED | RUNTIME_REACHABLE |
| triage_preferred_days_02 | A. 核心問診 / preferred_days | 02 | 想了解一下，你近期哪些日子方便看診？ | backend/app/services/question_specs.py | `QUESTION_SPECS[question_id="preferred_days"].variants` | YES | triage_preferred_days_02_zh.m4a | triage_preferred_days_02_taigi.m4a | REQUIRED | RUNTIME_REACHABLE |
| triage_preferred_days_03 | A. 核心問診 / preferred_days | 03 | 請問最近有哪一天比較方便就醫？ | backend/app/services/question_specs.py | `QUESTION_SPECS[question_id="preferred_days"].variants` | YES | triage_preferred_days_03_zh.m4a | triage_preferred_days_03_taigi.m4a | REQUIRED | RUNTIME_REACHABLE |
| triage_preferred_days_04 | A. 核心問診 / preferred_days | 04 | 方便告訴我，你接下來哪些日期可以看診嗎？ | backend/app/services/question_specs.py | `QUESTION_SPECS[question_id="preferred_days"].variants` | YES | triage_preferred_days_04_zh.m4a | triage_preferred_days_04_taigi.m4a | REQUIRED | RUNTIME_REACHABLE |
| triage_preferred_days_05 | A. 核心問診 / preferred_days | 05 | 你近期較方便安排就醫的日子是哪幾天呢？ | backend/app/services/question_specs.py | `QUESTION_SPECS[question_id="preferred_days"].variants` | YES | triage_preferred_days_05_zh.m4a | triage_preferred_days_05_taigi.m4a | REQUIRED | RUNTIME_REACHABLE |
| triage_preferred_sessions_01 | A. 核心問診 / preferred_sessions | 01 | 請問你偏好的看診時段是上午、下午還是夜間？ | backend/app/services/question_specs.py | `QUESTION_SPECS[question_id="preferred_sessions"].variants` | YES | triage_preferred_sessions_01_zh.m4a | triage_preferred_sessions_01_taigi.m4a | REQUIRED | RUNTIME_REACHABLE |
| triage_preferred_sessions_02 | A. 核心問診 / preferred_sessions | 02 | 想確認一下，你看診比較方便的時段是上午、下午或夜間？ | backend/app/services/question_specs.py | `QUESTION_SPECS[question_id="preferred_sessions"].variants` | YES | triage_preferred_sessions_02_zh.m4a | triage_preferred_sessions_02_taigi.m4a | REQUIRED | RUNTIME_REACHABLE |
| triage_preferred_sessions_03 | A. 核心問診 / preferred_sessions | 03 | 請問你偏好安排上午、下午，還是夜間門診？ | backend/app/services/question_specs.py | `QUESTION_SPECS[question_id="preferred_sessions"].variants` | YES | triage_preferred_sessions_03_zh.m4a | triage_preferred_sessions_03_taigi.m4a | REQUIRED | RUNTIME_REACHABLE |
| triage_preferred_sessions_04 | A. 核心問診 / preferred_sessions | 04 | 方便告訴我，你比較適合上午、下午或夜間看診嗎？ | backend/app/services/question_specs.py | `QUESTION_SPECS[question_id="preferred_sessions"].variants` | YES | triage_preferred_sessions_04_zh.m4a | triage_preferred_sessions_04_taigi.m4a | REQUIRED | RUNTIME_REACHABLE |
| triage_preferred_sessions_05 | A. 核心問診 / preferred_sessions | 05 | 你希望看診時間安排在上午、下午還是夜間呢？ | backend/app/services/question_specs.py | `QUESTION_SPECS[question_id="preferred_sessions"].variants` | YES | triage_preferred_sessions_05_zh.m4a | triage_preferred_sessions_05_taigi.m4a | REQUIRED | RUNTIME_REACHABLE |
| triage_clarification_symptom_01 | B. 澄清問句 / symptom | 01 | 想確認一下，您最主要想處理的不舒服症狀是什麼？ | backend/app/services/clarification_engine.py | `CLARIFICATION_PROMPTS["symptom"]` | YES | triage_clarification_symptom_01_zh.m4a | triage_clarification_symptom_01_taigi.m4a | REQUIRED | RUNTIME_REACHABLE；strict validation 失敗後至多一次 |
| triage_clarification_red_flags_01 | B. 澄清問句 / red_flags | 01 | 想再確認一下，你目前有沒有剛才提到的任何一項急迫症狀？如果有，請告訴我是胸痛、嚴重呼吸困難、意識不清、大量出血、半邊無力或劇烈頭痛；如果都沒有，請回答『都沒有』。 | backend/app/services/clarification_engine.py | `CLARIFICATION_PROMPTS["red_flags"]` | YES | triage_clarification_red_flags_01_zh.m4a | triage_clarification_red_flags_01_taigi.m4a | REQUIRED | RUNTIME_REACHABLE；safety-specific clarification |
| triage_clarification_body_part_01 | B. 澄清問句 / body_part | 01 | 想確認一下，症狀主要在身體哪個部位？ | backend/app/services/clarification_engine.py | `CLARIFICATION_PROMPTS["body_part"]` | YES | triage_clarification_body_part_01_zh.m4a | triage_clarification_body_part_01_taigi.m4a | REQUIRED | RUNTIME_REACHABLE |
| triage_clarification_duration_01 | B. 澄清問句 / duration | 01 | 想確認一下，這個症狀大約持續多久了？例如幾天、幾週或幾個月。 | backend/app/services/clarification_engine.py | `CLARIFICATION_PROMPTS["duration"]` | YES | triage_clarification_duration_01_zh.m4a | triage_clarification_duration_01_taigi.m4a | REQUIRED | RUNTIME_REACHABLE |
| triage_clarification_severity_01 | B. 澄清問句 / severity | 01 | 想確認一下，您的疼痛或不舒服是輕微、普通，還是嚴重到影響睡眠或日常活動？ | backend/app/services/clarification_engine.py | `CLARIFICATION_PROMPTS["severity"]` | YES | triage_clarification_severity_01_zh.m4a | triage_clarification_severity_01_taigi.m4a | REQUIRED | RUNTIME_REACHABLE |
| triage_clarification_preferred_days_01 | B. 澄清問句 / preferred_days | 01 | 想確認一下，您可就醫的日期是平日、週末、每天都可以，還是目前不確定？ | backend/app/services/clarification_engine.py | `CLARIFICATION_PROMPTS["preferred_days"]` | YES | triage_clarification_preferred_days_01_zh.m4a | triage_clarification_preferred_days_01_taigi.m4a | REQUIRED | RUNTIME_REACHABLE |
| triage_clarification_preferred_sessions_01 | B. 澄清問句 / preferred_sessions | 01 | 想確認一下，您比較方便的時段是上午、下午、夜間，還是全天都可以？ | backend/app/services/clarification_engine.py | `CLARIFICATION_PROMPTS["preferred_sessions"]` | YES | triage_clarification_preferred_sessions_01_zh.m4a | triage_clarification_preferred_sessions_01_taigi.m4a | REQUIRED | RUNTIME_REACHABLE |
| triage_clarification_department_01 | B. 澄清問句 / department | 01 | 目前提供的資訊還不足以確定最合適的科別，我再確認一點：除了目前描述的不舒服之外，還有其他明顯症狀嗎？ | backend/app/routes/chat.py | `DEPARTMENT_CLARIFICATION_TEXT` | YES | triage_clarification_department_01_zh.m4a | triage_clarification_department_01_taigi.m4a | REQUIRED | RUNTIME_REACHABLE；科別判定失敗時至多一次 |
| triage_clarification_preferred_days_unavailable_01 | B. 澄清問句 / preferred_days | unavailable | 了解您最近可能不方便。想確認是否完全沒有可就醫日期，還是只有部分日期可以？ | backend/app/services/clarification_engine.py | `clarification_prompt(semantic_status="unavailable")` | YES | triage_clarification_preferred_days_unavailable_01_zh.m4a | triage_clarification_preferred_days_unavailable_01_taigi.m4a | OPTIONAL | UNUSED_CURRENT_RUNTIME；`preferred_days` 的 unavailable 目前直接視為 satisfied |
| triage_clarification_can_take_leave_01 | B. 澄清問句 / can_take_leave | 01 | 想確認一下，如果需要較早看診，您是否方便請假？ | backend/app/services/clarification_engine.py | `CLARIFICATION_PROMPTS["can_take_leave"]` | YES | triage_clarification_can_take_leave_01_zh.m4a | triage_clarification_can_take_leave_01_taigi.m4a | OPTIONAL | UNUSED_CURRENT_RUNTIME；不在 `CHECKLIST_FIELD_ORDER` |
| triage_clarification_generic_01 | B. 澄清問句 / generic | 01 | 想再確認一下剛剛那個回答，可以請您用更明確的方式說明嗎？ | backend/app/services/clarification_engine.py | `clarification_prompt()` default | YES | triage_clarification_generic_01_zh.m4a | triage_clarification_generic_01_taigi.m4a | OPTIONAL | UNUSED_CURRENT_RUNTIME；7 個 checklist field 都有專用 key，未知 field 先被拒絕 |
| triage_confirmation_department_dynamic_01 | C. 確認問句 / department | dynamic | 目前建議科別為 {case.department_result.childDept}，請確認後取得推薦掛號方案。 | backend/app/routes/chat.py | `chat()` awaiting-confirmation branch | NO | — | — | DYNAMIC_NOT_PREGENERATED | RUNTIME_REACHABLE；科別名稱來自正式 department result |
| triage_confirmation_completed_01 | C. 確認問句 / confirmed | 01 | 已確認分診結果，可呼叫 /recommend 取得推薦掛號方案。 | backend/app/routes/chat.py | `chat()` confirmed branch | YES | triage_confirmation_completed_01_zh.m4a | triage_confirmation_completed_01_taigi.m4a | OPTIONAL | BACKEND_RUNTIME；正常 Android `chooseRecommendation()` 收到後直接導航，不建立訊息 |
| triage_confirmation_legacy_cache_01 | C. 確認問句 / confirmed | legacy | 已確認分診結果，接下來為您推薦掛號方案。 | backend/app/services/fixed_sentences.py | `BACKEND_FIXED_SENTENCES["confirm_department"]` | YES | triage_confirmation_legacy_cache_01_zh.m4a | triage_confirmation_legacy_cache_01_taigi.m4a | OPTIONAL | UNUSED_CURRENT_RUNTIME；與現行 `/chat` confirmed reply 不同，無其他引用 |
| triage_safety_urgent_01 | D. 固定安全警示 | 01 | 症狀較急迫，建議盡快就醫，必要時請假處理。 | backend/app/services/rule_engine.py | `evaluate_urgency()` warning message | YES | triage_safety_urgent_01_zh.m4a | triage_safety_urgent_01_taigi.m4a | OPTIONAL | RUNTIME_DISPLAY_ONLY；Android `UrgentWarningCard` 顯示，卡片目前無播放鍵 |
| triage_safety_uncertain_01 | D. 固定安全警示 | 02 | 目前無法完全確認是否有急迫症狀；若有突發胸痛、嚴重呼吸困難、意識不清、大量出血、半邊無力或劇烈頭痛，請優先尋求緊急醫療協助。 | backend/app/services/rule_engine.py | `RED_FLAG_UNCERTAINTY_WARNING` | YES | triage_safety_uncertain_01_zh.m4a | triage_safety_uncertain_01_taigi.m4a | OPTIONAL | RUNTIME_DISPLAY_ONLY；Android `UrgentWarningCard` 顯示，卡片目前無播放鍵 |
| triage_safety_footer_01 | D. 固定安全警示 | 03 | 本資訊僅供參考！若有緊急症狀請立即就醫 | android/app/src/main/java/com/example/medicalaiguidance/screen/ChatScreen.kt | `RealBubbleItem()` safety-label `Text` | YES | triage_safety_footer_01_zh.m4a | triage_safety_footer_01_taigi.m4a | OPTIONAL | RUNTIME_DISPLAY_ONLY；每個 AI 訊息泡泡的固定標示，本身不由播放鍵朗讀 |
| triage_system_department_unresolved_01 | E. 固定系統提示 | 01 | 目前仍無法安全地自動判定唯一科別，系統沒有替你套用預設科別。請改用手動選科，或洽醫院掛號服務協助。 | backend/app/routes/chat.py | `DEPARTMENT_UNRESOLVED_REPLY` | YES | triage_system_department_unresolved_01_zh.m4a | triage_system_department_unresolved_01_taigi.m4a | REQUIRED | RUNTIME_REACHABLE；Android 會建立可播放 AI 訊息 |
| triage_system_revision_01 | E. 固定系統提示 | 02 | 好的，我會延續這次問診紀錄重新整理。請補充想修改的症狀、嚴重程度、看診日期或時段。 | backend/app/routes/chat.py | `chat()` revision-requested branch | YES | triage_system_revision_01_zh.m4a | triage_system_revision_01_taigi.m4a | REQUIRED | RUNTIME_REACHABLE；使用「我想修改」流程時顯示 |
| triage_system_return_visit_01 | E. 固定系統提示 | 03 | 回診不進行完整症狀分診，請使用 /followup/recommend 提供原科別、原醫師與可看診時段。 | backend/app/routes/chat.py | `chat()` return-visit branch | YES | triage_system_return_visit_01_zh.m4a | triage_system_return_visit_01_taigi.m4a | OPTIONAL | BACKEND_RUNTIME；目前 Android `startNewConversation()` 不為 return_visit 啟動 ChatScreen 問診 |
| triage_system_batch_wrapper_01 | E. 固定系統提示 | 04 | 請一次回答以下問題。 | backend/app/routes/chat.py | `chat()` question-batch reply | YES | triage_system_batch_wrapper_01_zh.m4a | triage_system_batch_wrapper_01_taigi.m4a | OPTIONAL | BACKEND_ACTIVE_ANDROID_NOT_RENDERED；Android 優先顯示 `questionBatch.first().question`，且目前每批 1 題 |
| triage_system_empty_state_01 | E. 固定系統提示 | 05 | 不舒服嗎？請告訴我 | android/app/src/main/java/com/example/medicalaiguidance/screen/ChatScreen.kt | `ChatScreen()` empty-state `Text` | YES | triage_system_empty_state_01_zh.m4a | triage_system_empty_state_01_taigi.m4a | OPTIONAL | RUNTIME_DISPLAY_ONLY；非 AI `ChatMessage`，目前不進 TTS |
| triage_system_analyzing_01 | E. 固定系統提示 | 06 | 正在分析中請稍後 | android/app/src/main/java/com/example/medicalaiguidance/screen/ChatScreen.kt | `AiThinkingBubble()` commented-out `Text` | YES | triage_system_analyzing_01_zh.m4a | triage_system_analyzing_01_taigi.m4a | OPTIONAL | UNUSED_CURRENT_RUNTIME；文字目前被註解，畫面只顯示動畫圓點 |
| triage_system_continue_fallback_01 | E. 固定系統提示 | 07 | 請繼續描述症狀或可看診時間。 | android/app/src/main/java/com/example/medicalaiguidance/viewmodel/ChatViewModel.kt | `consumeTriageResponse()` fallback | YES | triage_system_continue_fallback_01_zh.m4a | triage_system_continue_fallback_01_taigi.m4a | OPTIONAL | DEFENSIVE_FALLBACK；合法 Backend response 正常會有 question/reply/warning |
| triage_system_continue_fallback_en_01 | E. 固定系統提示 | 08 | I received your information. Please continue describing your symptoms or preferred visit time. | android/app/src/main/java/com/example/medicalaiguidance/viewmodel/ChatViewModel.kt | `sendMessage()` fallback | YES | triage_system_continue_fallback_en_01_zh.m4a | triage_system_continue_fallback_en_01_taigi.m4a | OPTIONAL | DEFENSIVE_FALLBACK；合法 Backend response 正常不會落到此分支 |
| triage_system_revision_local_01 | E. 固定系統提示 | 09 | 好的，請直接補充想修改的症狀、嚴重程度或希望看診時間，我會延續這次問診重新整理。 | android/app/src/main/java/com/example/medicalaiguidance/viewmodel/ChatViewModel.kt | `continueEditing()` no-active-case branch | YES | triage_system_revision_local_01_zh.m4a | triage_system_revision_local_01_taigi.m4a | OPTIONAL | DEFENSIVE_FALLBACK；正常顯示修改按鈕時已有 active case |
| triage_system_revision_response_fallback_01 | E. 固定系統提示 | 10 | 好的，我會延續這次問診紀錄重新整理。請補充想修改的症狀、嚴重程度或看診日期/時段。 | android/app/src/main/java/com/example/medicalaiguidance/viewmodel/ChatViewModel.kt | `continueEditing()` empty-response fallback | YES | triage_system_revision_response_fallback_01_zh.m4a | triage_system_revision_response_fallback_01_taigi.m4a | OPTIONAL | DEFENSIVE_FALLBACK；Backend revision branch固定回覆非空 |
| triage_system_greeting_01 | E. 固定系統提示 | 11 | 您好，請問哪裡不舒服？我來幫您掛號。 | backend/app/services/fixed_sentences.py | `GUIDANCE_SENTENCES["greeting"]` | YES | triage_system_greeting_01_zh.m4a | triage_system_greeting_01_taigi.m4a | OPTIONAL | UNUSED_CURRENT_RUNTIME；全域無消費端引用 |
| triage_system_querying_01 | E. 固定系統提示 | 12 | 好的，正在為您查詢，請稍候。 | backend/app/services/fixed_sentences.py | `GUIDANCE_SENTENCES["querying"]` | YES | triage_system_querying_01_zh.m4a | triage_system_querying_01_taigi.m4a | OPTIONAL | UNUSED_CURRENT_RUNTIME；全域無消費端引用 |
| triage_system_listening_01 | E. 固定系統提示 | 13 | 請說，我在聽。 | backend/app/services/fixed_sentences.py | `GUIDANCE_SENTENCES["listening"]` | YES | triage_system_listening_01_zh.m4a | triage_system_listening_01_taigi.m4a | OPTIONAL | UNUSED_CURRENT_RUNTIME；全域無消費端引用 |
| triage_system_not_understood_01 | E. 固定系統提示 | 14 | 抱歉，沒聽清楚，可以再說一次嗎？ | backend/app/services/fixed_sentences.py | `GUIDANCE_SENTENCES["not_understood"]` | YES | triage_system_not_understood_01_zh.m4a | triage_system_not_understood_01_taigi.m4a | OPTIONAL | UNUSED_CURRENT_RUNTIME；現行 ASR 錯誤使用其他操作錯誤文字 |

## 動態 TTS（不預生成）

| ID | Runtime 句型 | 為何不能固定 | 現行來源 |
|---|---|---|---|
| triage_confirmation_department_dynamic_01 | 目前建議科別為 `{case.department_result.childDept}`，請確認後取得推薦掛號方案。 | 含正式科別名稱；科別由每個 case 的 `department_result` 決定 | `backend/app/routes/chat.py::chat()` |

醫師推薦理由、醫師姓名、班表日期／時段、掛號導引腳本與 history 內容依需求排除，不計入本目錄的動態 TTS 句型數。`ai_reply_generator.generate_triage_reply()` 目前直接回傳 controlled fallback，沒有生成式問句，因此也沒有額外 AI 動態問句。

## 統計

### 核心固定問句

| question_id / state_field | 句數 |
|---|---:|
| symptom | 5 |
| emergency_symptoms / red_flags | 5 |
| body_part | 5 |
| duration | 5 |
| severity | 5 |
| preferred_days | 5 |
| preferred_sessions | 5 |
| **合計** | **35** |

- 是否為 7 類 × 5 種：**是**。
- B/C/D（澄清、確認、安全）另有 16 個固定字串；其中 12 個在目前 Backend 或 Android runtime 可達／可見，4 個標為 `UNUSED_CURRENT_RUNTIME`。另有 1 個動態科別確認句型。
- 全目錄固定字串：**65 句**（45 REQUIRED + 20 OPTIONAL）。
- 最小必要音檔包：**45 句**。
- 國語 REQUIRED：45 個；台語 REQUIRED：45 個；合計：**90 個音檔**。
- OPTIONAL 全部加入：每種語言各增 20 個；完整包為每種語言 65 個、國台語合計 **130 個音檔**。
- 仍需動態 TTS：**1 種句型**。

## 音檔交付清單（REQUIRED）

以下只列最小必要包；OPTIONAL 的合法檔名已完整列在上表。

### 國語需要

#### symptom

- `res/raw/triage_symptom_01_zh.m4a` — 請描述目前最主要的不舒服症狀。
- `res/raw/triage_symptom_02_zh.m4a` — 想先了解一下，你現在最主要是哪裡不舒服？
- `res/raw/triage_symptom_03_zh.m4a` — 請問目前最困擾你的症狀是什麼？
- `res/raw/triage_symptom_04_zh.m4a` — 方便說明一下，你這次最想處理的不舒服嗎？
- `res/raw/triage_symptom_05_zh.m4a` — 請告訴我，目前最主要的身體不適是什麼？

#### emergency_symptoms / red_flags

- `res/raw/triage_emergency_symptoms_01_zh.m4a` — 請問是否有突發胸痛、嚴重呼吸困難、意識不清、大量出血、半邊無力或劇烈頭痛等急迫症狀？
- `res/raw/triage_emergency_symptoms_02_zh.m4a` — 想確認安全狀況：目前是否有突發胸痛、嚴重呼吸困難、意識不清、大量出血、半邊無力或劇烈頭痛？
- `res/raw/triage_emergency_symptoms_03_zh.m4a` — 請確認一下，你現在有沒有突發胸痛、嚴重呼吸困難、意識不清、大量出血、半邊無力或劇烈頭痛？
- `res/raw/triage_emergency_symptoms_04_zh.m4a` — 為了確認是否需要立即處理，請問有無突發胸痛、嚴重呼吸困難、意識不清、大量出血、半邊無力或劇烈頭痛？
- `res/raw/triage_emergency_symptoms_05_zh.m4a` — 接著確認急迫症狀：你是否出現突發胸痛、嚴重呼吸困難、意識不清、大量出血、半邊無力或劇烈頭痛？

#### body_part

- `res/raw/triage_body_part_01_zh.m4a` — 請問症狀主要發生在哪個部位？
- `res/raw/triage_body_part_02_zh.m4a` — 想了解一下，這個不舒服主要在身體哪裡？
- `res/raw/triage_body_part_03_zh.m4a` — 請問你主要是哪個部位感到不適？
- `res/raw/triage_body_part_04_zh.m4a` — 方便告訴我，症狀集中在哪個位置嗎？
- `res/raw/triage_body_part_05_zh.m4a` — 這個症狀主要影響身體的哪個部位呢？

#### duration

- `res/raw/triage_duration_01_zh.m4a` — 請問這個症狀大約持續多久了？
- `res/raw/triage_duration_02_zh.m4a` — 想了解一下，這個不舒服大概持續多久了呢？
- `res/raw/triage_duration_03_zh.m4a` — 這個症狀大約是從什麼時候開始的？
- `res/raw/triage_duration_04_zh.m4a` — 方便告訴我，這個情況已經持續多久了嗎？
- `res/raw/triage_duration_05_zh.m4a` — 請問你大概多久以前開始出現這個症狀？

#### severity

- `res/raw/triage_severity_01_zh.m4a` — 請問症狀程度是輕微、中等、明顯，還是很痛或影響生活？
- `res/raw/triage_severity_02_zh.m4a` — 想了解一下，目前不舒服的程度大約是輕微、中等還是嚴重？
- `res/raw/triage_severity_03_zh.m4a` — 請問這個症狀現在的嚴重程度如何？
- `res/raw/triage_severity_04_zh.m4a` — 這個不舒服有沒有明顯到影響你的日常生活？
- `res/raw/triage_severity_05_zh.m4a` — 方便描述一下，症狀目前是輕微、普通，還是相當嚴重嗎？

#### preferred_days

- `res/raw/triage_preferred_days_01_zh.m4a` — 請問你最近哪幾天有空就醫？
- `res/raw/triage_preferred_days_02_zh.m4a` — 想了解一下，你近期哪些日子方便看診？
- `res/raw/triage_preferred_days_03_zh.m4a` — 請問最近有哪一天比較方便就醫？
- `res/raw/triage_preferred_days_04_zh.m4a` — 方便告訴我，你接下來哪些日期可以看診嗎？
- `res/raw/triage_preferred_days_05_zh.m4a` — 你近期較方便安排就醫的日子是哪幾天呢？

#### preferred_sessions

- `res/raw/triage_preferred_sessions_01_zh.m4a` — 請問你偏好的看診時段是上午、下午還是夜間？
- `res/raw/triage_preferred_sessions_02_zh.m4a` — 想確認一下，你看診比較方便的時段是上午、下午或夜間？
- `res/raw/triage_preferred_sessions_03_zh.m4a` — 請問你偏好安排上午、下午，還是夜間門診？
- `res/raw/triage_preferred_sessions_04_zh.m4a` — 方便告訴我，你比較適合上午、下午或夜間看診嗎？
- `res/raw/triage_preferred_sessions_05_zh.m4a` — 你希望看診時間安排在上午、下午還是夜間呢？

#### clarification

- `res/raw/triage_clarification_symptom_01_zh.m4a` — 想確認一下，您最主要想處理的不舒服症狀是什麼？
- `res/raw/triage_clarification_red_flags_01_zh.m4a` — 想再確認一下，你目前有沒有剛才提到的任何一項急迫症狀？如果有，請告訴我是胸痛、嚴重呼吸困難、意識不清、大量出血、半邊無力或劇烈頭痛；如果都沒有，請回答『都沒有』。
- `res/raw/triage_clarification_body_part_01_zh.m4a` — 想確認一下，症狀主要在身體哪個部位？
- `res/raw/triage_clarification_duration_01_zh.m4a` — 想確認一下，這個症狀大約持續多久了？例如幾天、幾週或幾個月。
- `res/raw/triage_clarification_severity_01_zh.m4a` — 想確認一下，您的疼痛或不舒服是輕微、普通，還是嚴重到影響睡眠或日常活動？
- `res/raw/triage_clarification_preferred_days_01_zh.m4a` — 想確認一下，您可就醫的日期是平日、週末、每天都可以，還是目前不確定？
- `res/raw/triage_clarification_preferred_sessions_01_zh.m4a` — 想確認一下，您比較方便的時段是上午、下午、夜間，還是全天都可以？
- `res/raw/triage_clarification_department_01_zh.m4a` — 目前提供的資訊還不足以確定最合適的科別，我再確認一點：除了目前描述的不舒服之外，還有其他明顯症狀嗎？

#### system

- `res/raw/triage_system_department_unresolved_01_zh.m4a` — 目前仍無法安全地自動判定唯一科別，系統沒有替你套用預設科別。請改用手動選科，或洽醫院掛號服務協助。
- `res/raw/triage_system_revision_01_zh.m4a` — 好的，我會延續這次問診紀錄重新整理。請補充想修改的症狀、嚴重程度、看診日期或時段。

### 台語需要

#### symptom

- `res/raw/triage_symptom_01_taigi.m4a` — 請描述目前最主要的不舒服症狀。
- `res/raw/triage_symptom_02_taigi.m4a` — 想先了解一下，你現在最主要是哪裡不舒服？
- `res/raw/triage_symptom_03_taigi.m4a` — 請問目前最困擾你的症狀是什麼？
- `res/raw/triage_symptom_04_taigi.m4a` — 方便說明一下，你這次最想處理的不舒服嗎？
- `res/raw/triage_symptom_05_taigi.m4a` — 請告訴我，目前最主要的身體不適是什麼？

#### emergency_symptoms / red_flags

- `res/raw/triage_emergency_symptoms_01_taigi.m4a` — 請問是否有突發胸痛、嚴重呼吸困難、意識不清、大量出血、半邊無力或劇烈頭痛等急迫症狀？
- `res/raw/triage_emergency_symptoms_02_taigi.m4a` — 想確認安全狀況：目前是否有突發胸痛、嚴重呼吸困難、意識不清、大量出血、半邊無力或劇烈頭痛？
- `res/raw/triage_emergency_symptoms_03_taigi.m4a` — 請確認一下，你現在有沒有突發胸痛、嚴重呼吸困難、意識不清、大量出血、半邊無力或劇烈頭痛？
- `res/raw/triage_emergency_symptoms_04_taigi.m4a` — 為了確認是否需要立即處理，請問有無突發胸痛、嚴重呼吸困難、意識不清、大量出血、半邊無力或劇烈頭痛？
- `res/raw/triage_emergency_symptoms_05_taigi.m4a` — 接著確認急迫症狀：你是否出現突發胸痛、嚴重呼吸困難、意識不清、大量出血、半邊無力或劇烈頭痛？

#### body_part

- `res/raw/triage_body_part_01_taigi.m4a` — 請問症狀主要發生在哪個部位？
- `res/raw/triage_body_part_02_taigi.m4a` — 想了解一下，這個不舒服主要在身體哪裡？
- `res/raw/triage_body_part_03_taigi.m4a` — 請問你主要是哪個部位感到不適？
- `res/raw/triage_body_part_04_taigi.m4a` — 方便告訴我，症狀集中在哪個位置嗎？
- `res/raw/triage_body_part_05_taigi.m4a` — 這個症狀主要影響身體的哪個部位呢？

#### duration

- `res/raw/triage_duration_01_taigi.m4a` — 請問這個症狀大約持續多久了？
- `res/raw/triage_duration_02_taigi.m4a` — 想了解一下，這個不舒服大概持續多久了呢？
- `res/raw/triage_duration_03_taigi.m4a` — 這個症狀大約是從什麼時候開始的？
- `res/raw/triage_duration_04_taigi.m4a` — 方便告訴我，這個情況已經持續多久了嗎？
- `res/raw/triage_duration_05_taigi.m4a` — 請問你大概多久以前開始出現這個症狀？

#### severity

- `res/raw/triage_severity_01_taigi.m4a` — 請問症狀程度是輕微、中等、明顯，還是很痛或影響生活？
- `res/raw/triage_severity_02_taigi.m4a` — 想了解一下，目前不舒服的程度大約是輕微、中等還是嚴重？
- `res/raw/triage_severity_03_taigi.m4a` — 請問這個症狀現在的嚴重程度如何？
- `res/raw/triage_severity_04_taigi.m4a` — 這個不舒服有沒有明顯到影響你的日常生活？
- `res/raw/triage_severity_05_taigi.m4a` — 方便描述一下，症狀目前是輕微、普通，還是相當嚴重嗎？

#### preferred_days

- `res/raw/triage_preferred_days_01_taigi.m4a` — 請問你最近哪幾天有空就醫？
- `res/raw/triage_preferred_days_02_taigi.m4a` — 想了解一下，你近期哪些日子方便看診？
- `res/raw/triage_preferred_days_03_taigi.m4a` — 請問最近有哪一天比較方便就醫？
- `res/raw/triage_preferred_days_04_taigi.m4a` — 方便告訴我，你接下來哪些日期可以看診嗎？
- `res/raw/triage_preferred_days_05_taigi.m4a` — 你近期較方便安排就醫的日子是哪幾天呢？

#### preferred_sessions

- `res/raw/triage_preferred_sessions_01_taigi.m4a` — 請問你偏好的看診時段是上午、下午還是夜間？
- `res/raw/triage_preferred_sessions_02_taigi.m4a` — 想確認一下，你看診比較方便的時段是上午、下午或夜間？
- `res/raw/triage_preferred_sessions_03_taigi.m4a` — 請問你偏好安排上午、下午，還是夜間門診？
- `res/raw/triage_preferred_sessions_04_taigi.m4a` — 方便告訴我，你比較適合上午、下午或夜間看診嗎？
- `res/raw/triage_preferred_sessions_05_taigi.m4a` — 你希望看診時間安排在上午、下午還是夜間呢？

#### clarification

- `res/raw/triage_clarification_symptom_01_taigi.m4a` — 想確認一下，您最主要想處理的不舒服症狀是什麼？
- `res/raw/triage_clarification_red_flags_01_taigi.m4a` — 想再確認一下，你目前有沒有剛才提到的任何一項急迫症狀？如果有，請告訴我是胸痛、嚴重呼吸困難、意識不清、大量出血、半邊無力或劇烈頭痛；如果都沒有，請回答『都沒有』。
- `res/raw/triage_clarification_body_part_01_taigi.m4a` — 想確認一下，症狀主要在身體哪個部位？
- `res/raw/triage_clarification_duration_01_taigi.m4a` — 想確認一下，這個症狀大約持續多久了？例如幾天、幾週或幾個月。
- `res/raw/triage_clarification_severity_01_taigi.m4a` — 想確認一下，您的疼痛或不舒服是輕微、普通，還是嚴重到影響睡眠或日常活動？
- `res/raw/triage_clarification_preferred_days_01_taigi.m4a` — 想確認一下，您可就醫的日期是平日、週末、每天都可以，還是目前不確定？
- `res/raw/triage_clarification_preferred_sessions_01_taigi.m4a` — 想確認一下，您比較方便的時段是上午、下午、夜間，還是全天都可以？
- `res/raw/triage_clarification_department_01_taigi.m4a` — 目前提供的資訊還不足以確定最合適的科別，我再確認一點：除了目前描述的不舒服之外，還有其他明顯症狀嗎？

#### system

- `res/raw/triage_system_department_unresolved_01_taigi.m4a` — 目前仍無法安全地自動判定唯一科別，系統沒有替你套用預設科別。請改用手動選科，或洽醫院掛號服務協助。
- `res/raw/triage_system_revision_01_taigi.m4a` — 好的，我會延續這次問診紀錄重新整理。請補充想修改的症狀、嚴重程度、看診日期或時段。

## 明確排除

未列入音檔需求的內容：debug log、exception/API error、開發者或 LLM prompt、semantic extraction prompt、rule-engine `reasons`、推薦排序/score 文案、醫師與班表動態資料、按鈕/無障礙操作文字、history 畫面、GettingStarted 既有音檔內容，以及 Voice/ASR 操作狀態訊息。
