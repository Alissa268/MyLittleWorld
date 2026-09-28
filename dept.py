import json
from config import ask_llm, trace_note   
from database import get_departments_with_category
from dept_keywords import count_symptom_coverage
from models import PatientInput, DepartmentResult, FallbackDepartment, VisitType

FIRST_VISIT_EXCLUDED_PARENTS = {"大我門診"}


def recommend_department(patient_input: PatientInput,
                         visit_type: VisitType = "初診",
                         ) -> tuple[DepartmentResult, list[FallbackDepartment]]:

    coverage_count, total_symptoms = count_symptom_coverage(patient_input)

    all_depts = get_departments_with_category()
  
    if visit_type == "初診":
        candidate_depts = [d for d in all_depts
                           if d["parentDept"] not in FIRST_VISIT_EXCLUDED_PARENTS]
    else:
        candidate_depts = list(all_depts)

    age = patient_input.age
    if age is not None and age >= 18:
        candidate_depts = [d for d in candidate_depts
                           if not (d["parentDept"] == "婦幼" and d["childDept"] != "婦產科")]

   
    candidate_lines = "\n".join(
        f"- 子科別「{d['childDept']}」（父科別「{d['parentDept']}」）" for d in candidate_depts
    )

    if coverage_count:
        hint_lines = "\n".join(
            f"- {dept}：命中 {c} 個症狀"
            for dept, c in sorted(coverage_count.items(), key=lambda x: -x[1])
        )
        keyword_hint = f"""【關鍵字初步分析】
        以下科別命中較多，可能相關：
        {hint_lines}
        請參考，但最終以你的醫學判斷為準；若與關鍵字不同，請在 reason 說明原因。"""
    else:
        keyword_hint = "（關鍵字未命中明顯科別，請依症狀自行判斷）"

    prompt = f"""你是台北榮民總醫院的分診助理。以下是病患症狀資料：
{patient_input.model_dump_json(indent=2)}

{keyword_hint}

可選擇的科別清單（必須從中挑一個，parentDept 與 childDept 要完全照抄）：
{candidate_lines}

【年齡規則】病患 age 未滿 18 歲時（含青少年），請「優先」選擇兒童相關科別
（父科別為「婦幼」，例如兒童內科、兒童腸胃科、兒童過敏感染…）。
只有當該症狀在清單裡「沒有對應的兒童科」時（例如皮膚、眼睛沒有兒童版），
才改選成人科別。age 為 18 以上則一律選成人科別。

【症狀不明確時的處理】
若病患症狀模糊、非特異、沒有明確器官指向，難以判斷該掛哪一科時，
導向「家庭醫學科(一般門診/戒菸)」做初步鑑別診斷，
並在 reason 說明「症狀模糊，建議先由家醫科初步評估」。

【confidence 給分規則】
"confidence"：對「這個科別判斷」的把握程度，反映醫學判斷即可
- 症狀典型、明確指向單一科 → 高分（0.8~1.0）
- 症狀符合但不只一科可能 → 中等（0.5~0.7）
- 症狀模糊、非特異、勉強猜的 → 低分（0.2~0.4）

【reason 撰寫規則】（固定回兩則）
1. 第一則：依病患症狀，用白話說明為什麼建議掛這一科（醫學判斷）。
2. 第二則：對照上面「關鍵字初步分析」，說明關鍵字命中情況；
   若你的判斷與關鍵字不同，請在這則說明原因；
   若關鍵字未命中任何科，請寫「無關鍵字佐證，依症狀判斷」。

【備選科別（alternatives）】
除了最適合的主科，請再依症狀判斷 1 個「次適合」的科別（同樣從清單挑、完全照抄，
不可與主科相同），作為主科近期掛不到號時的備選；並附一句 reason 說明為什麼這科也可能適合。

請判斷最適合的「父科別 + 子科別」，輸出以下 JSON，不要輸出任何其他文字：
{{
  "parentDept": "父科別（完全照抄清單）",
  "childDept": "子科別（完全照抄清單）",
  "confidence": 0.0 到 1.0 的數字,
  "reason": ["第一則：醫學判斷（為什麼掛這科）", "第二則：關鍵字比對結果"],
  "alternatives": [
    {{"parentDept": "備選父科（照抄清單）", "childDept": "備選子科（照抄清單）", "reason": "為什麼這科也可能適合"}}
  ]
}}"""

    response = ask_llm(prompt, "選科", max_tokens=1536)
    print(f"  AI 分診回應：{response}")

    try:
        ai_result = json.loads(response)
    except json.JSONDecodeError:
        print("  分診 JSON 解析失敗，退回第一個科別")
        fallback = candidate_depts[0]
        ai_result = {
            "parentDept": fallback["parentDept"],
            "childDept": fallback["childDept"],
            "confidence": 0.5,
            "reason": ["AI 回應解析失敗，使用候選清單第一筆"],
        }

    child = (ai_result.get("childDept", "") or "").strip()
    if child not in {d["childDept"] for d in candidate_depts} and "/" in child:
        child = child.split("/")[-1].strip()

    confidence_ai = float(ai_result.get("confidence", 0.5))
    coverage = coverage_count.get(child, 0) / total_symptoms
    confidence = round(0.5 * confidence_ai + 0.5 * coverage, 2)

    # 除錯
    trace_note("選科", {
        "候選科別數": len(candidate_depts),
        "關鍵字命中": dict(sorted(coverage_count.items(), key=lambda x: -x[1])),
        "病患症狀總數": total_symptoms,
        "AI 自報信心": confidence_ai,
        "關鍵字覆蓋度": f"{coverage_count.get(child, 0)}/{total_symptoms} = {round(coverage, 2)}",
        "最終信心": f"0.5×{confidence_ai} + 0.5×{round(coverage, 2)} = {confidence}",
        "AI 選的科": f"{ai_result.get('parentDept', '')} / {child}",
        "AI 給的備選": ai_result.get("alternatives", []),
    })
    #

    department_result = DepartmentResult(
        parentDept=ai_result.get("parentDept", ""),
        childDept=child,
        confidence=confidence,
        reason=ai_result.get("reason", []),
    )

    valid_children = {d["childDept"] for d in candidate_depts}
    alt_fallbacks = []
    seen = {child}
    for alt in ai_result.get("alternatives", []):
        if not isinstance(alt, dict):
            continue
        alt_child = (alt.get("childDept") or "").strip()
        if alt_child not in valid_children or alt_child in seen:
            continue
        seen.add(alt_child)
        alt_parent = next((d["parentDept"] for d in candidate_depts if d["childDept"] == alt_child), "")
        alt_fallbacks.append(FallbackDepartment(
            parentDept=alt_parent,
            childDept=alt_child,
            reason=alt.get("reason") or f"{child}近期無號時，可依症狀考慮改掛{alt_child}",
        ))

    return department_result, alt_fallbacks
