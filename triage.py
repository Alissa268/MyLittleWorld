import json
import re
import uuid
from config import ask_llm
from schedule import SESSIONS_DESC
from models import (
    ChatRequest, TriageResult, TriageCase, HistoryRecord,
    PatientInput, Triage, ConversationState,
    Availability, Preferences,
)

def collect_symptoms(chat_request: ChatRequest) -> TriageResult:
    
    message = chat_request.message 
    triage_case = chat_request.triage_case

    if triage_case is None:
        case_id = str(uuid.uuid4())[:8].upper()
        triage_case = TriageCase(case_id=case_id)
        print(f"  建立新病歷：{case_id}")
    else:
        print(f"  沿用病歷：{triage_case.case_id}")

    triage_case.history_records.append(HistoryRecord(role="user", content=message))

    history_str = ""
    for msg in triage_case.history_records:
        role_label = "使用者" if msg.role == "user" else "助理"
        history_str += f"{role_label}：{msg.content}\n"


    current_input = triage_case.patient_input
    pending_field = triage_case.triage.next_question or "無"

    prompt = f"""你是台北榮民總醫院的分診助理，正在透過對話收集病患症狀。

目前對話紀錄：
{history_str}

目前已收集到的症狀資料：
{current_input.model_dump_json(indent=2)}

目前待補答欄位：
{pending_field}

若上一輪指定要補答某個欄位，病患在補充問題頁面輸入的最新文字，就是該欄位的新答案。
請用此答案取代該欄位原本無效的內容，再重新檢查全部症狀資料。

你的任務：
1. 根據對話更新症狀資料
2. 每次都要依「台灣急診五級檢傷分類(TTAS) 民眾衛教版」判斷急迫程度（級數數字越小越嚴重），規則如下：

【重 → 立即急診】(urgency_level=high, urgency_score=90)　＝ TTAS 第一、二級
  第一級 復甦急救（立即處理）：
    - 心跳、呼吸停止，肢體及嘴唇發青、發紫
    - 體溫 >41°C 或 <32°C
    - 無意識、意識混亂（對疼痛刺激無反應、只能呻吟或說單一字句、只有疼痛刺激才會睜眼）
    - 持續抽搐且無意識
  第二級 危急（10分鐘內）：
    - 急性意識狀態改變（語言與動作遲滯，但尚可溝通）
    - 持續胸悶、胸痛且冒冷汗
    - 低血糖（<40mg/dl）
    - 大量血便、黑便、嘔血
    - 外傷造成之大量出血，頭頸軀幹骨盆部位血流不止
    - 槍傷，頭、頸、軀幹鈍傷、穿刺傷，開放性傷口疑似骨折
    - 高處墜落、車禍（乘客被拋出車外）、頭部撞擊後曾失去意識
    - 突發性視覺改變
    - 免疫功能不全且發燒
    - 會陰部穿刺傷與大量出血，生殖器腫脹變形
    - 外傷或接觸化學物質後出現的神經功能異常（動作與感覺改變）
    - 化學物質濺入眼睛
    - 疑似藥物過敏導致呼吸困難
    - 螫傷、咬傷導致呼吸困難或意識改變

【中 → 當日或隔天門診】(urgency_level=medium, urgency_score=50)　＝ TTAS 第三級
  第三級 緊急（30分鐘內）：
    - 走動時明顯有呼吸急促
    - 經期逾期且腹痛
    - 無法控制的腹瀉或嘔吐
    - 外傷後肢體腫脹變形疑似骨折／脫臼
    - 咖啡色嘔吐物或黑便
    - 高血壓（收縮壓>200mmHg 或 舒張壓>110mmHg）且沒有任何症狀
    - 抽搐後意識已恢復
    - 廣泛性紅疹／水泡
    - 毒氣或其他氣體吸入，無呼吸窘迫徵象
    - 急產（宮縮>2分鐘）
    
【輕 → 一般門診】(urgency_level=low, urgency_score=20)　＝ TTAS 第四、五級
  只要沒有明確命中上面「重」(第一、二級) 或「中」(第三級) 清單裡的項目，一律歸此類。

【TTAS 判級的原則】
1. 嚴格按上方 TTAS 清單逐條比對：只有症狀「明確符合」清單某一項才判該級。
2. red_flags 填入觸發「重」（第一、二級）的症狀描述，沒有就空陣列。

【資料收集原則】
1. 病患已先填過「症狀表單」，會在一則訊息裡「照編號」一次答完 1～6，編號對應欄位如下：
   1→symptom、2→body_part、3→duration、4→severity、5→onset、6→accompanying_symptoms。
   請照編號對應吸收，不要重複問表單已經答過的；只有真的缺欄位，或欄位內容不符合該欄位語意時才追問。
2. 足夠條件：必須要有 symptom + body_part + duration + severity（問病患「輕微／中等／劇烈」）+ onset（問「症狀是突然開始，還是慢慢變嚴重」）+
  accompanying_symptoms。
  (1) body_part：病患答「無」「不知道」「不清楚」→ 算已回答，body_part 填 null，不要再追問。
  (2) accompanying_symptoms：只有病患原始回答明確表示「無」時，才算已回答並填空陣列 []。
    若病患在第 6 題實際填了其他內容，但內容無法辨識為症狀，不得自行改寫為空陣列 []；必須追問該欄。
3. 不夠 → is_complete=false，只問「下一個」最重要的問題。
4. 足夠 → is_complete=true。
5. 問法：每次只問一題、用白話讓長輩聽得懂、已答過的不重複問（病患可能一次答多項）。

【欄位內容不合理的處理】
欄位雖然有填寫，但內容無法合理對應該欄位的語意時，視為該欄無效，必須追問該欄。
請依內容語意判斷，不得只依特定關鍵字、固定格式或語言判斷。

- symptom 必須能辨識為主要不適、疾病徵象或症狀描述；若無法辨識，填空字串 ""，不得填 null。
- body_part 必須能辨識為身體部位；「無」、「不知道」、「不清楚」視為有效。
- duration 必須能辨識為症狀持續的時間長度或時間範圍。
- accompanying_symptoms 必須能辨識為其他症狀；只有原始回答明確為「無」時，才可填空陣列 [] 並視為有效。
- 不得因內容使用英文、數字或中英混合就直接判定無效；應先判斷內容是否仍可合理理解。

若多個欄位不合理，每次只能追問一個最重要的欄位。
追問優先順序：symptom → duration → body_part → accompanying_symptoms。

需要追問時：
- need_more_info 必須為 true。
- is_complete 必須為 false。
- next_question 必須填 symptom、body_part、duration、accompanying_symptoms 其中一個欄位代號。
- 不得在 next_question 填問題文字。
- 必須輸出完整合法 JSON，不得輸出空白。

【回覆格式規定】
1. TTAS 判斷為「重」  (high)：reply=「您的狀況可能屬於緊急情況，請立即前往急診就醫。（症狀摘要一句）」
  例如：您的狀況可能屬於緊急情況，請立即前往急診就醫。（持續胸痛冒冷汗）
  不論資料是否齊全，立刻設 is_complete=true、warning_required=true，停止追問、不要再問任何問題。
2. 還要繼續問時，reply 填「資料需要補充。」即可。
  實際要顯示給病患的固定問題由前端依 next_question 處理。
3. 資料收集完成且TTAS為「中」(medium)：reply 格式必須是「[症狀摘要，一句話]，建議您盡早就醫」
  例如：「無法控制的嘔吐一天、中等程度，建議您盡早就醫」（此例對應第三級「無法控制的腹瀉或嘔吐」）
4. 資料收集完成且TTAS為「低」(low)：reply 格式必須是「[症狀摘要，一句話]」
  例如：「輕微流鼻水兩天、無發燒」
5. 上述情況都只摘要病患症狀，不要推薦或提到任何科別，格式也嚴格執行上述規定。


請輸出以下 JSON，不要輸出任何其他文字：
{{
  "patient_input": {{
    "symptom": "主要症狀描述",
    "body_part": "哪個部位或 null",
    "duration": "持續多久或 null",
    "severity": "嚴重程度或 null",
    "onset": "怎麼開始的或 null",
    "accompanying_symptoms": ["伴隨症狀列表"],
    "red_flags": ["危險警訊列表"]
  }},
  "triage": {{
    "urgency_score": 數字（low=20, medium=50, high=90）,
    "urgency_level": "low 或 medium 或 high",
    "warning_required": true 或 false,
    "warning_message": "警告訊息或 null",
    "need_more_info": true 或 false,
    "next_question": "need_more_info=true 時，填 symptom、body_part、duration、accompanying_symptoms 其中一個；false 時填 null"
  }},
  "conversation_state": {{
    "stage": "collecting",
    "is_complete": true 或 false
  }},
  "reply": "對使用者說的話（格式見上方規定）"
}}"""

    response = ask_llm(prompt, "症狀收集", max_tokens=1024)
    print(f"  AI 回應：{response}")

    reply = "抱歉，我沒聽清楚，請再說一次。"
    try:
        ai_result = json.loads(response)
        if "patient_input" in ai_result:
            patient_data = ai_result["patient_input"]
            if patient_data.get("symptom") is None:
                patient_data["symptom"] = ""
            new_pi = PatientInput(**patient_data)
            new_pi.age = triage_case.patient_input.age       
            new_pi.gender = triage_case.patient_input.gender
            triage_case.patient_input = new_pi
        if "conversation_state" in ai_result:
            triage_case.conversation_state = ConversationState(**ai_result["conversation_state"])
        if "triage" in ai_result:        
            triage_case.triage = Triage(**ai_result["triage"])
        reply = ai_result.get("reply", "")
    except (json.JSONDecodeError, TypeError, ValueError) as e:
        print(f"  AI 回應解析失敗，保留原值：{e}")

    triage_case.history_records.append(HistoryRecord(role="assistant", content=reply))

    is_complete = triage_case.conversation_state.is_complete
    
    triage_result = TriageResult(
        case_id=triage_case.case_id,
        triage_case=triage_case,
        conversation_state=triage_case.conversation_state,
        triage=triage_case.triage,
        department_result=None,             
        next_question=None,
        reply=reply,                        
        needMoreInfo=not is_complete        
    )
    return triage_result


BASIC_INFO_QUESTIONS = """請先提供病患基本資料，請「照編號」列點回答 1～2：

1. 【年齡】請填數字（例如：45）
2. 【性別】請填 男 / 女

範例：1.21  2.男"""


SYMPTOM_QUESTIONS = """請描述您的症狀，請「照編號」列點回答 1～6：

1. 【主要症狀】您哪裡不舒服？（例如：肚子痛、頭暈、咳嗽）
2. 【部位】不舒服的部位在哪裡？（例如：右下腹、後腦杓、胸口；
   說不出來或沒有特定部位請填「無」）
3. 【持續多久】這個症狀持續多久了？（例如：2天、一週）
4. 【嚴重程度】輕微 / 中等 / 劇烈？
5. 【怎麼開始的】突然開始的，還是慢慢變嚴重的？
6. 【伴隨症狀】還有沒有其他不舒服？（例如：發燒、拉肚子、想吐；沒有請填「無」）

範例：1.肚子痛  2.右下腹  3.2天  4.中等  5.慢慢變嚴重  6.發燒、拉肚子"""


def parse_basic_info(chat_request: ChatRequest) -> TriageCase:
    answer = chat_request.message
    triage_case = chat_request.triage_case or TriageCase(case_id=str(uuid.uuid4())[:8].upper())

    cleaned = re.sub(r"(?:^|\s)[12]\s*[.、:：]\s*", " ", answer)

    m = re.search(r"\d+", cleaned)
    triage_case.patient_input.age = int(m.group()) if m else None
    if "男" in answer:
        triage_case.patient_input.gender = "男"
    elif "女" in answer:
        triage_case.patient_input.gender = "女"

    return triage_case


AVAILABILITY_QUESTIONS = f"""請回答以下三個問題，請「照編號」列點回答 1～3：

1. 【方便看診時段】您哪幾天的哪個時段方便看診？請把「星期」和「時段」一起回答。
   時段有 {SESSIONS_DESC} 三種。
   同一天有多個時段請一起寫（例如：週三下午和夜診）。

2. 【看診考量】您希望「找專長最對症的醫師（醫師專長優先）」，
   還是「盡快有門診可以看（時間優先）」？
   （請回答：醫師專長優先 / 時間優先）

3. 【指定醫師】有沒有特別想看的醫師？
   （有就填醫師名字，沒有請填「不限」）

範例：1.週二早上、週三下午和夜診、週四早上  2.醫師專長優先  3.不限"""


def parse_availability_answer(chat_request: ChatRequest) -> TriageCase:
    user_answer = chat_request.message          
    triage_case = chat_request.triage_case       

    prompt = f"""病患回答了以下就診偏好問題（病患會「照編號」列點回答）：
「{user_answer}」

問題對應的欄位說明：
1.方便看診時段 → available_slots（「星期+時段」組合的陣列）。
    每一筆是一個 {{"day": "週X", "session": "早上/下午/夜診"}}。
    day 只能是 週一、週二、週三、週四、週五、週六、週日。
    session 只能是 早上、下午、夜診。
    同一天有多個時段就展開成多筆，並「沿用同一個星期」——
    不管病患用「和」「跟」「、」「，」分隔都一樣。例如：
      「週四早上和下午」→ {{"day":"週四","session":"早上"}}、{{"day":"週四","session":"下午"}}
      「週三下午，夜診」→ {{"day":"週三","session":"下午"}}、{{"day":"週三","session":"夜診"}}
    絕對不可以自己發明星期（例如「不限」）；真的判斷不出星期，就不要輸出那一筆。
    病患沒說明確時段就回空陣列 []。
2.看診考量 → specialty_priority（布林）
    病患回答「醫師專長優先」「專長」「醫師」，或表達想看最對症的醫師 → specialty_priority = true
    病患回答「時間」「時間優先」，或表達想盡快看到 → specialty_priority = false
    沒提到（複診問卷不會問這題）→ 預設 true
3.指定醫師 → doctor_preference（字串，有說名字就填名字，沒有填「不限」）

請解析病患回答並輸出以下 JSON，不要輸出任何其他文字：
{{
  "availability": {{
    "available_slots": [{{"day": "週X", "session": "早上/下午/夜診"}}]
  }},
  "preferences": {{
    "specialty_priority": true 或 false,
    "doctor_preference": "醫師名字或不限",
    "hospital_preference": "台北榮總"
  }}
}}"""

    response = ask_llm(prompt, "偏好解析", max_tokens=1024)
    print(f"  AI 回應：{response}")

    try:
        ai_result = json.loads(response)
        if "availability" in ai_result:
            triage_case.availability = Availability(**ai_result["availability"])
        if "preferences" in ai_result:
            triage_case.preferences = Preferences(**ai_result["preferences"])
    except (json.JSONDecodeError, TypeError, ValueError) as e:
        print(f"  解析失敗，保留預設值：{e}")

    return triage_case



def main():
    from web.flow import step

    print(BASIC_INFO_QUESTIONS)
    print("\n（輸入 q 結束）\n")

    triage_case = None
    while True:
        user_input = input("請回答：").strip()
        if user_input.lower() == "q":
            break
        if not user_input:
            continue

        response = step(ChatRequest(message=user_input, triage_case=triage_case))
        triage_case = response.triage_case

        print(triage_case.model_dump_json(indent=2))
        print("─" * 50)
        print(response.reply)
        print("─" * 50)

        if response.recommendation is not None:
            urgency = triage_case.triage.urgency_level or "low"
            urgency_label = {"high": " 高（請立即急診）", "medium": " 中（盡快就醫）",
                             "low": " 低（一般門診）"}.get(urgency, urgency)
            slots = triage_case.availability.available_slots
            slots_str = "、".join(f"{s.day}{s.session}" for s in slots) if slots else "（未指定，不限時段）"
            print(f"  急迫程度：{urgency_label}")
            print(f"  方便時段：{slots_str}")
            print(f"  看診考量：{'醫師專長優先' if triage_case.preferences.specialty_priority else '時間優先'}")
            print(f"  指定醫師：{triage_case.preferences.doctor_preference}")
            if urgency == "medium":
                print("  緊急程度：中（TTAS 第三級）→ 建議盡快於當日或隔天就診")
            print("=" * 50)
            print("分診與科別推薦結果（RecommendationResult）")
            print("=" * 50)
            print(response.recommendation.model_dump_json(indent=2))
            print("=" * 50)

        if not response.needMoreInfo:
            triage_case = None
            print()
            print(BASIC_INFO_QUESTIONS)
            print()


if __name__ == "__main__":
    main()



