from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any


TTAS_KNOWLEDGE_DIR = Path(__file__).resolve().parents[2] / "knowledge" / "ttas"
_IMPLEMENTATION_STATUSES = {"enabled", "reference_only"}
_PREDICATE_OPERATORS = {"all", "any", "eq", "lt", "lte", "gt", "gte", "between", "range_pair"}


class TTASRuleLoadError(RuntimeError):
    pass


@dataclass(frozen=True)
class TTASRuleSet:
    ruleset_id: str
    sources: dict[str, dict[str, Any]]
    evidence_fields: dict[str, dict[str, Any]]
    rules: tuple[dict[str, Any], ...]

    @property
    def enabled_rules(self) -> tuple[dict[str, Any], ...]:
        return tuple(rule for rule in self.rules if rule["implementation_status"] == "enabled")


def _read_json(name: str) -> dict[str, Any]:
    try:
        data = json.loads((TTAS_KNOWLEDGE_DIR / name).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise TTASRuleLoadError(f"TTAS knowledge file is unavailable or malformed: {name}") from exc
    if not isinstance(data, dict):
        raise TTASRuleLoadError(f"TTAS knowledge file must contain an object: {name}")
    return data


def _validate_predicate(predicate: object, fields: set[str], *, enabled: bool) -> None:
    if not isinstance(predicate, dict) or len(predicate) != 1:
        raise TTASRuleLoadError("TTAS predicate must contain exactly one operator")
    operator, value = next(iter(predicate.items()))
    if operator not in _PREDICATE_OPERATORS:
        raise TTASRuleLoadError(f"Unsupported TTAS predicate operator: {operator}")
    if operator in {"all", "any"}:
        if not isinstance(value, list) or not value:
            raise TTASRuleLoadError(f"TTAS {operator} predicate must contain clauses")
        for item in value:
            _validate_predicate(item, fields, enabled=enabled)
        return
    if not isinstance(value, list) or not value or not isinstance(value[0], str):
        raise TTASRuleLoadError(f"TTAS {operator} predicate has an invalid field expression")
    if enabled and value[0] not in fields:
        raise TTASRuleLoadError(f"Enabled TTAS rule references unknown evidence field: {value[0]}")
    expected_size = 6 if operator == "range_pair" else 3 if operator == "between" else 2
    if len(value) != expected_size:
        raise TTASRuleLoadError(f"TTAS {operator} predicate has an invalid arity")


@lru_cache(maxsize=1)
def load_ttas_rules() -> TTASRuleSet:
    manifest = _read_json("source_manifest.json")
    schema = _read_json("ttas_evidence_schema.json")
    rule_data = _read_json("active_rules.json")

    source_items = manifest.get("sources")
    field_items = schema.get("fields")
    rule_items = rule_data.get("rules")
    if not isinstance(source_items, list) or not isinstance(field_items, dict) or not isinstance(rule_items, list):
        raise TTASRuleLoadError("TTAS package registry, schema, or rules are structurally invalid")

    sources: dict[str, dict[str, Any]] = {}
    for source in source_items:
        if not isinstance(source, dict) or not isinstance(source.get("source_id"), str):
            raise TTASRuleLoadError("TTAS source is missing source_id")
        source_id = source["source_id"].strip()
        if not source_id or source_id in sources:
            raise TTASRuleLoadError(f"Duplicate or empty TTAS source_id: {source_id}")
        if not isinstance(source.get("url"), str) or not source["url"].startswith("https://"):
            raise TTASRuleLoadError(f"TTAS source must use an official HTTPS URL: {source_id}")
        sources[source_id] = source

    rules: list[dict[str, Any]] = []
    seen_rule_ids: set[str] = set()
    allowed_fields = set(field_items)
    for rule in rule_items:
        if not isinstance(rule, dict):
            raise TTASRuleLoadError("TTAS rule must be an object")
        rule_id = rule.get("rule_id")
        source_id = rule.get("source_id")
        status = rule.get("implementation_status")
        level = rule.get("ttas_level")
        if not isinstance(rule_id, str) or not rule_id.strip() or rule_id in seen_rule_ids:
            raise TTASRuleLoadError(f"Duplicate or invalid TTAS rule_id: {rule_id}")
        if source_id not in sources:
            raise TTASRuleLoadError(f"TTAS rule references unknown source_id: {source_id}")
        if status not in _IMPLEMENTATION_STATUSES:
            raise TTASRuleLoadError(f"TTAS rule has invalid implementation_status: {rule_id}")
        if type(level) is not int or not 1 <= level <= 5:
            raise TTASRuleLoadError(f"TTAS rule has invalid level: {rule_id}")
        if not isinstance(rule.get("required_fields"), list):
            raise TTASRuleLoadError(f"TTAS rule has invalid required_fields: {rule_id}")
        if not isinstance(rule.get("official_locator"), str) or not rule["official_locator"].strip():
            raise TTASRuleLoadError(f"TTAS rule has no official locator: {rule_id}")
        _validate_predicate(rule.get("predicate"), allowed_fields, enabled=status == "enabled")
        seen_rule_ids.add(rule_id)
        rules.append(rule)

    ruleset_id = rule_data.get("ruleset_id")
    if not isinstance(ruleset_id, str) or not ruleset_id.strip():
        raise TTASRuleLoadError("TTAS ruleset_id is missing")
    return TTASRuleSet(ruleset_id, sources, field_items, tuple(rules))


def clear_ttas_rule_cache() -> None:
    load_ttas_rules.cache_clear()
