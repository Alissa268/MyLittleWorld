from __future__ import annotations

import json

import pytest

from app.services import ttas_rule_loader
from app.services.ttas_rule_loader import TTASRuleLoadError, load_ttas_rules


def test_official_ttas_package_integrity() -> None:
    ruleset = load_ttas_rules()
    assert len(ruleset.rules) == len({rule["rule_id"] for rule in ruleset.rules})
    assert all(rule["source_id"] in ruleset.sources for rule in ruleset.rules)
    assert all(1 <= rule["ttas_level"] <= 5 for rule in ruleset.rules)
    assert all(
        rule["implementation_status"] in {"enabled", "reference_only"}
        for rule in ruleset.rules
    )


def test_reference_only_hypertension_rules_are_not_executable() -> None:
    ruleset = load_ttas_rules()
    reference_ids = {
        rule["rule_id"]
        for rule in ruleset.rules
        if rule["implementation_status"] == "reference_only"
    }
    enabled_ids = {rule["rule_id"] for rule in ruleset.enabled_rules}
    assert {"TTAS-A020407", "TTAS-A020409", "TTAS-A020410", "TTAS-A020411"} <= reference_ids
    assert reference_ids.isdisjoint(enabled_ids)


def test_clinical_modifier_and_untraceable_context_rules_are_reference_only() -> None:
    ruleset = load_ttas_rules()
    reference_ids = {
        rule["rule_id"]
        for rule in ruleset.rules
        if rule["implementation_status"] == "reference_only"
    }
    assert {
        "TTAS-MOD-RESP-SEVERE",
        "TTAS-MOD-RESP-MODERATE",
        "TTAS-MOD-RESP-MILD",
        "TTAS-MOD-SHOCK",
        "TTAS-MOD-HEMODYNAMIC-INSUFFICIENT",
        "TTAS-MOD-ADULT-HIGH-RISK-MECHANISM",
        "TTAS-A020210",
        "TTAS-A041011",
        "TTAS-A041017",
        "TTAS-A130409",
        "TTAS-A130413",
        "TTAS-E010809",
        "TTAS-T010109",
        "TTAS-T010110",
        "TTAS-T120207",
        "TTAS-T120707",
    } <= reference_ids


def test_stroke_rules_use_official_six_hour_boundary() -> None:
    rules = {rule["official_code"]: rule for rule in load_ttas_rules().rules}
    assert rules["A041011"]["predicate"]["all"][1] == {"lt": ["stroke_onset_hours", 6]}
    assert rules["A041017"]["predicate"]["all"][1] == {"gte": ["stroke_onset_hours", 6]}
    assert rules["A041011"]["implementation_status"] == "reference_only"
    assert rules["A041017"]["implementation_status"] == "reference_only"


def test_malformed_rules_fail_closed(tmp_path, monkeypatch) -> None:
    source = ttas_rule_loader.TTAS_KNOWLEDGE_DIR
    for name in ("source_manifest.json", "ttas_evidence_schema.json", "active_rules.json"):
        (tmp_path / name).write_text((source / name).read_text(encoding="utf-8"), encoding="utf-8")
    data = json.loads((tmp_path / "active_rules.json").read_text(encoding="utf-8"))
    data["rules"][0]["source_id"] = "missing-source"
    (tmp_path / "active_rules.json").write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setattr(ttas_rule_loader, "TTAS_KNOWLEDGE_DIR", tmp_path)
    ttas_rule_loader.clear_ttas_rule_cache()
    with pytest.raises(TTASRuleLoadError):
        ttas_rule_loader.load_ttas_rules()
    ttas_rule_loader.clear_ttas_rule_cache()
