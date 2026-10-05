from __future__ import annotations

import pytest

from app.schemas import Message, TTASEvidence, TriageCase
from app.services import ttas_evaluator
from app.services.ttas_evaluator import evaluate_ttas
from app.services.ttas_rule_loader import TTASRuleLoadError


def _case(*facts: tuple[str, object], age: float | None = None) -> TriageCase:
    case = TriageCase(case_id="ttas", history_records=[Message(role="user", content="reported")])
    case.patient_input.age_years = age
    case.ttas_evidence = [
        TTASEvidence(
            field=field,
            value=value,
            semantic_status="available",
            confidence=0.95,
            source_text="reported",
        )
        for field, value in facts
    ]
    return case


@pytest.mark.parametrize(
    ("facts", "expected_level"),
    [
        (("respiratory_distress", "severe"), 1),
        (("respiratory_distress", "moderate"), 2),
        (("respiratory_distress", "mild"), 3),
        (("hemodynamic_status", "shock"), 1),
        (("consciousness", "altered"), 2),
        (("temperature_c", 42.0), 1),
    ],
)
def test_modifiers_match_official_levels(facts, expected_level) -> None:
    result = evaluate_ttas(_case(facts))
    assert result.status == "matched"
    assert result.level_candidate == expected_level


def test_multiple_matches_choose_most_urgent_and_keep_trace() -> None:
    result = evaluate_ttas(
        _case(("respiratory_distress", "moderate"), ("hemodynamic_status", "shock"))
    )
    assert result.level_candidate == 1
    assert {item.rule_id for item in result.matched_rules} >= {
        "TTAS-MOD-RESP-MODERATE",
        "TTAS-MOD-SHOCK",
    }
    assert all(item.source_id and item.official_locator for item in result.matched_rules)


@pytest.mark.parametrize(
    ("official_code", "facts", "level"),
    [
        ("A130409", (("blood_glucose_mg_dl", 55), ("hypoglycemia_symptoms", True)), 2),
        ("A130413", (("blood_glucose_mg_dl", 55), ("hypoglycemia_symptoms", False)), 3),
        ("A040504", (("seizure_status", "ongoing"),), 1),
        ("A040511", (("seizure_status", "stopped_not_recovered"),), 2),
        ("A040516", (("seizure_status", "stopped_recovered"),), 3),
        ("A041011", (("stroke_symptoms", True), ("stroke_onset_hours", 5.9)), 2),
        ("A041017", (("stroke_symptoms", True), ("stroke_onset_hours", 6.0)), 3),
        ("A030713", (("vomiting_pattern", "acute_persistent"),), 3),
        ("E010509", (("chemical_eye_injury", True),), 2),
    ],
)
def test_official_code_regressions(official_code, facts, level) -> None:
    result = evaluate_ttas(_case(*facts, age=30))
    matches = [item for item in result.matched_rules if item.official_code == official_code]
    assert matches and matches[0].level == level


def test_toxic_gas_without_respiratory_distress_is_level_three() -> None:
    result = evaluate_ttas(
        _case(("toxic_gas_exposure", True), ("respiratory_distress", "none"))
    )
    assert any(item.official_code == "E010809" and item.level == 3 for item in result.matched_rules)


def test_open_fracture_and_suspected_deformity_have_distinct_levels() -> None:
    open_result = evaluate_ttas(_case(("open_fracture", True)))
    suspected_result = evaluate_ttas(
        _case(("suspected_fracture_or_dislocation_deformity", True))
    )
    assert open_result.level_candidate == 2
    assert suspected_result.level_candidate == 3


def test_missing_vital_and_no_match_never_default_to_four_or_five() -> None:
    missing = evaluate_ttas(_case(("toxic_gas_exposure", True)))
    no_match = evaluate_ttas(_case(("respiratory_distress", "none")))
    assert missing.status == "insufficient_information"
    assert missing.level_candidate is None
    assert "respiratory_distress" in missing.missing_evidence
    assert no_match.status == "insufficient_information"
    assert no_match.level_candidate is None


def test_age_dependent_rules_do_not_assume_adult() -> None:
    unknown_age = evaluate_ttas(_case(("blood_glucose_mg_dl", 55), ("hypoglycemia_symptoms", True)))
    adult = evaluate_ttas(
        _case(("blood_glucose_mg_dl", 55), ("hypoglycemia_symptoms", True), age=30)
    )
    assert unknown_age.level_candidate is None
    assert "age_years|age_months" in unknown_age.missing_evidence
    assert adult.level_candidate == 2


def test_reference_only_blood_pressure_rule_never_executes() -> None:
    result = evaluate_ttas(_case(("sbp_mmhg", 210), ("dbp_mmhg", 120), age=30))
    assert result.status == "insufficient_information"
    assert result.level_candidate is None
    assert not any((item.official_code or "").startswith("A0204") for item in result.matched_rules)


def test_raw_history_is_not_an_evaluator_input() -> None:
    case = TriageCase(
        case_id="raw-isolation",
        history_records=[Message(role="user", content="我喘得很嚴重而且正在抽搐")],
    )
    result = evaluate_ttas(case)
    assert result.status == "insufficient_information"
    assert result.level_candidate is None


def test_loader_failure_makes_evaluator_fail_closed(monkeypatch) -> None:
    def fail():
        raise TTASRuleLoadError("do not expose upstream detail")

    monkeypatch.setattr(ttas_evaluator, "load_ttas_rules", fail)
    result = evaluate_ttas(_case(("respiratory_distress", "severe")))
    assert result.status == "insufficient_information"
    assert result.level_candidate is None
    assert result.matched_rules == []
