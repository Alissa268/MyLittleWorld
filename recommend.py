import uuid

from config import trace_note
from database import get_available_schedules
from models import (
    RecommendRequest, RecommendationResult, DepartmentResult,
    RecommendationItem, FallbackDepartment,
)
from dept import recommend_department
from schedule import (TODAY, SESSION_ORDER, _session_open, _slot_match,
                      _session_range, select_feasible)
from scoring import score_specialties_with_ai, _row_tag, _has_specialty


def _build_items(rows, department_result, tag_scores, matched_specialties, slots,
                 specialty_priority=True, sort_by_date=False, doctor_pref="", limit=5):
    has_doctor = bool(doctor_pref and doctor_pref != "不限")
    items = []
    for row in rows:
        f_tag = _row_tag(row, tag_scores)
        reasons = []

        if has_doctor:
            reasons.append("符合您指定醫師" if doctor_pref in row["doctor"]
                           else "您指定的醫師不在此科或近期無號，改推同科其他醫師的門診")
        elif f_tag >= 0.7:
            matched = matched_specialties.get(row["specialty_tags"]) or row["specialty_tags"]
            reasons.append(f"醫師專長相符（{matched}）")

        if not slots:
            reasons.append("未指定時段，為您推薦近期可掛的門診")
        elif _slot_match(row, slots):
            reasons.append("符合您方便時段")
        else:
            reasons.append("您方便的時段近期無號，為您推薦時間最接近的門診")

        items.append(RecommendationItem(
            recommendation_id="rec_" + uuid.uuid4().hex[:6],
            parentDept=department_result.parentDept,
            childDept=row["childDept"],
            doctor=row["doctor"],
            date=row["date"],
            session=row["session"],
            session_time=_session_range(row["session"]),
            room=row["room"],
            score=round(f_tag, 2),
            reasons=reasons,
        ))

    def _when(x):
        return (x.date, SESSION_ORDER.get(x.session, 9))

    if sort_by_date:
        items.sort(key=_when)
    elif specialty_priority:
        items.sort(key=lambda x: (-x.score, *_when(x)))
    else:
        items.sort(key=lambda x: (*_when(x), -x.score))

    return items if limit is None else items[:limit]


def _open_rows(child_dept: str, visit_type: str = "初診") -> list:
    rows = get_available_schedules(child_dept, TODAY, visit_type=visit_type)
    return [r for r in rows if _session_open(r)]


def recommend(request: RecommendRequest) -> RecommendationResult:
    triage_case = request.triage_case
    patient_input = triage_case.patient_input
    availability = triage_case.availability
    preferences = triage_case.preferences
    case_id = triage_case.case_id
    slots = availability.available_slots
    doctor_pref = (preferences.doctor_preference or "不限").strip()
    has_doctor = bool(doctor_pref and doctor_pref != "不限")

    visit_type = triage_case.visit_type

    department_result, ai_fallbacks = recommend_department(patient_input, visit_type)

    used_dept = department_result
    all_rows = _open_rows(department_result.childDept, visit_type)
    if not all_rows:
        for alt in ai_fallbacks:
            alt_rows = _open_rows(alt.childDept, visit_type)
            if alt_rows:
                all_rows = alt_rows
                used_dept = DepartmentResult(
                    parentDept=alt.parentDept,
                    childDept=alt.childDept,
                    confidence=department_result.confidence,
                    reason=[f"主推薦（{department_result.childDept}）近期無號，改推薦次順位科別（{alt.childDept}）",
                            alt.reason],
                )
                break

    triage_case.department_result = used_dept

    if not all_rows:
        tried = [FallbackDepartment(parentDept=department_result.parentDept,
                                    childDept=department_result.childDept,
                                    reason="兩次推薦都無號可掛")]
        tried += [FallbackDepartment(parentDept=a.parentDept, childDept=a.childDept,
                                     reason="兩次推薦都無號可掛") for a in ai_fallbacks]
        return RecommendationResult(case_id=case_id, department=department_result,
                                    recommendations=[], fallback_departments=tried)

    feasible, relaxed_by_date = select_feasible(all_rows, slots, doctor_pref)

    # 除錯
    trace_note("班表篩選", {
        "掛號類別": visit_type,
        "查號的科別": used_dept.childDept,
        "是否換過次順位科": used_dept.childDept != department_result.childDept,
        "該科可掛號筆數": len(all_rows),
        "通過時段/醫師硬篩後": len(feasible),
        "病患方便時段": [f"{s.day}{s.session}" for s in slots] or ["未指定"],
        "指定醫師": doctor_pref,
        "時段無號而放寬": relaxed_by_date,
        "排序方式": ("純日期（已放寬）" if relaxed_by_date
                 else "專長→日期" if preferences.specialty_priority else "日期→專長"),
    })
    #

    if has_doctor:
        tag_scores, matched_specialties = {}, {}
    else:
        distinct_specialties = sorted({
            r["specialty_tags"] for r in feasible
            if _has_specialty(r["specialty_tags"])
        })
        tag_scores, matched_specialties = score_specialties_with_ai(distinct_specialties, patient_input)

    recommendations = _build_items(feasible, used_dept, tag_scores, matched_specialties,
                                   slots, specialty_priority=preferences.specialty_priority,
                                   sort_by_date=relaxed_by_date, doctor_pref=doctor_pref)

    return RecommendationResult(
        case_id=case_id,
        department=used_dept,
        recommendations=recommendations,
        fallback_departments=[],
    )


def main():
    from models import TriageCase, PatientInput, Availability, Slot, Preferences, Triage
    demo_request = RecommendRequest(
        triage_case=TriageCase(
            case_id="A1B2C3D4",
            patient_input=PatientInput(
                symptom="頭痛",
                body_part="頭部",
                duration="3天",
                severity="劇烈",
                onset="突然",
                accompanying_symptoms=["發燒", "頸部僵硬"],
            ),
            availability=Availability(
                available_slots=[
                    Slot(day="週一", session="早上"),
                    Slot(day="週三", session="早上"),
                ],
            ),
            preferences=Preferences(specialty_priority=True, doctor_preference="不限"),
            triage=Triage(urgency_level="medium", urgency_score=50),
        ),
        preference="醫師專長優先",
    )

    result = recommend(demo_request)
    print(result.model_dump_json(indent=2))


if __name__ == "__main__":
    main()
