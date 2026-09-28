import json
from config import ask_llm, trace_note  
from models import PatientInput

TAG_NEUTRAL = 0.5


def _has_specialty(specialty: str) -> bool:
    return bool((specialty or "").strip())


def score_specialties_with_ai(distinct_specialties: list[str], patient_input: PatientInput):
    if not distinct_specialties:
        return {}, {}

    tag_lines = "\n".join(f"{i}. {t}" for i, t in enumerate(distinct_specialties, 1))

    prompt = f"""你是醫療分診助理。以下是某科別候選醫師的「專長描述」清單：
{tag_lines}

病患的症狀資料：
{patient_input.model_dump_json(indent=2)}

請判斷每位醫師的專長描述與這位病患症狀的相符程度，並做兩件事：

【任務1】給 0.0~1.0 的相符分數（score）
    評分方式：用你的醫學知識做「推理」，不是字面比對——
    病患症狀是白話（如「胸悶、心悸」），專長是專業術語（如「心律不整、心臟電生理」），
    兩者不會字面吻合。
    先判斷「這位病患最可能罹患的是哪些疾病？」
    再看「這位醫師的專長，能不能治療那些疾病？」

    - 0.8~1.0：專長中有「病患最可能罹患的疾病」。
    - 0.6~0.7：專長涵蓋該器官系統的相關疾病，但不是最對症的那一個。
    - 0.5：專長寫的是「做什麼檢查」而不是「治什麼病」，或只是籠統的科別名，看不出針對性。
    - 0.0~0.3：專長明顯屬於其他器官系統，與病患症狀無關。

【任務2】挑出 matched
    從該專長描述中「只挑出跟病患症狀相符的專長項目」，用頓號分隔；
    若整串都不相符則給空字串。

【注意事項】
    專長的「排列順序」不代表擅長程度，排第 1 跟排最後一樣，只要有出現就算會治。
    分數只看「涵蓋了幾個病患可能罹患的疾病」，不看專長總共寫了幾項。

【輸出格式】只輸出 JSON，不要其他文字：
{{"1": {{"score": 分數, "matched": "相符的專長項目"}}, "2": {{"score": 分數, "matched": "相符的專長項目"}}, ...}}
key 一律用清單的「編號」（字串），不要把專長描述抄回來。清單裡每個編號都要有，不可遺漏。"""

    response = ask_llm(prompt, "專長評分", max_tokens=8192)
    print(f"  AI 專長評分：{response}")
    scores, matched = {}, {}
    try:
        result = json.loads(response)
        for i, t in enumerate(distinct_specialties, 1):
            key = str(i)
            if key in result and isinstance(result[key], dict):
                scores[t] = float(result[key].get("score", TAG_NEUTRAL))
                matched[t] = str(result[key].get("matched", "") or "")
        # 除錯
        trace_note("專長評分", {
            "送去評分的專長段數": len(distinct_specialties),
            "成功解析段數": len(scores),
            "門檻 0.7 以上段數": sum(1 for s in scores.values() if s >= 0.7),
            "逐段結果": [
                {"專長": t, "score": scores[t], "matched": matched.get(t, "")}
                for t in sorted(scores, key=lambda k: -scores[k])
            ],
        })
        #
        return scores, matched
    except (json.JSONDecodeError, TypeError, ValueError):
        print("  專長評分解析失敗，全部視為中性")
        # 除錯
        trace_note("專長評分", {"解析失敗": True, "全部視為中性 0.5": True,
                              "送去評分的專長段數": len(distinct_specialties)})
        #
        return {}, {}


def _row_tag(row: dict, tag_scores: dict) -> float:
    if _has_specialty(row["specialty_tags"]):
        return tag_scores.get(row["specialty_tags"], TAG_NEUTRAL)
    return TAG_NEUTRAL
