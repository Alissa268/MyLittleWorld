
from database import get_available_schedules, get_departments_with_category
from schedule import _session_open, select_feasible, TODAY, SESSIONS_DESC
from recommend import _build_items
from models import FollowupRequest, FollowupChatRequest, RecommendationResult, DepartmentResult, FallbackDepartment


def recommend_followup(request: FollowupRequest) -> RecommendationResult:

    child = request.childDept
    slots = request.availability.available_slots

    parent = request.parentDept
    if not parent:
        all_depts = get_departments_with_category()
        parent = next((d["parentDept"] for d in all_depts if d["childDept"] == child), "")
    department_result = DepartmentResult(parentDept=parent, childDept=child)

    all_rows = get_available_schedules(child, TODAY, visit_type="複診")
    all_rows = [r for r in all_rows if _session_open(r)]

    doctor_pref = (request.preferences.doctor_preference or "不限").strip()
    has_doctor = bool(doctor_pref and doctor_pref != "不限")
    feasible, _ = select_feasible(all_rows, slots, doctor_pref)
    recommendations = _build_items(feasible, department_result, {}, {}, slots,
                                   specialty_priority=False, sort_by_date=True,
                                   doctor_pref=doctor_pref, limit=5 if has_doctor else None)

    fallback = [] if all_rows else [FallbackDepartment(parentDept=parent, childDept=child, reason="近期無可掛號")]

    return RecommendationResult(
        case_id=request.case_id,
        department=department_result,
        recommendations=recommendations,
        fallback_departments=fallback,
    )


FOLLOWUP_QUESTIONS = f"""請回答以下兩個問題，請「照編號」列點回答 1～2：

1. 【方便看診時段】您哪幾天的哪個時段方便看診？請把「星期」和「時段」一起回答。
   時段有 {SESSIONS_DESC} 三種。
   同一天有多個時段請一起寫（例如：週三下午和夜診）。

2. 【指定醫師】有沒有特別想看的醫師？（有就填醫師名字，沒有請填「不限」）

範例：1.週二早上、週三下午和夜診、週四早上  2.不限"""


def collect_followup(chat_request: FollowupChatRequest) -> FollowupRequest:

    from triage import parse_availability_answer
    from models import ChatRequest, TriageCase
    from database import get_departments_with_category

    child = (chat_request.dept or "").strip()
    all_depts = get_departments_with_category()
    if child not in {d["childDept"] for d in all_depts}:
        print(f"  查無「{child}」")

    tc = parse_availability_answer(
        ChatRequest(message=chat_request.preference_answer,
                    triage_case=TriageCase(case_id=chat_request.case_id or "FU"))
    )

    return FollowupRequest(
        case_id=tc.case_id,
        childDept=child,
        availability=tc.availability,     
        preferences=tc.preferences,       
    )


def main():
    dept = input("請問您要複診哪一科？（例如：手外科）：").strip()
    print("\n" + FOLLOWUP_QUESTIONS)
    pref = input("\n請一起回答上面三題：").strip()
    chat_request = FollowupChatRequest(dept=dept, preference_answer=pref)

    request = collect_followup(chat_request)   
    result = recommend_followup(request)        

    print("複診推薦結果（RecommendationResult）")
    print(result.model_dump_json(indent=2))


if __name__ == "__main__":
    main()
