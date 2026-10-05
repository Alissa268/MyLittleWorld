# Phase 5 TTAS-based Urgency Validation

## Scope

Phase 5 replaces the primary AI-first free-text urgency decision with grounded TTAS evidence plus a deterministic evaluator. It does not change the official Department KB, Department convergence, SQL data, Android, schedule behavior, or doctor scoring.

This implementation is a preliminary screening subset defined by `phase5_ttas_source_package`. It is not a complete clinical TTAS implementation and no live-provider/live-SQL acceptance is claimed here.

## Source Package

The installed knowledge files are exact copies of the provided package:

- `backend/knowledge/ttas/source_manifest.json`
- `backend/knowledge/ttas/ttas_evidence_schema.json`
- `backend/knowledge/ttas/active_rules.json`
- `backend/knowledge/ttas/old_ailogic_crosswalk.json`

Only rules marked `implementation_status=enabled` execute. `reference_only` rules remain audit data and cannot affect a level candidate.

## Runtime Flow

The existing Turn Interpreter returns one JSON object containing:

1. `semantic_extractions`
2. `pending_answer`
3. `ttas_evidence`

No fixed second TTAS AI call was added. Backend validation enforces the field allow-list, field-specific value type/range, finite confidence, acceptance threshold, semantic status, and current-turn verbatim grounding. AI-provided level, score, warning, or workflow fields are ignored.

Validated evidence is stored as history and reduced to current field state by latest accepted evidence. The evaluator reads that structured state and trusted age facts only; it never reads raw user text.

## Deterministic Evaluation

- Rule data is loaded lazily and cached.
- Duplicate rule/source IDs, unknown sources, malformed predicates, invalid levels/statuses, and non-HTTPS sources fail closed.
- Missing required evidence remains unknown and is reported in `missing_evidence`.
- Multiple complete matches retain trace and choose the smallest, most urgent level number.
- No complete enabled match returns `insufficient_information` and `level_candidate=null`.
- No path defaults to level 4 or 5.
- Age-dependent rules do not execute unless trusted or grounded age evidence establishes the population.

Each match records `rule_id`, `official_code`, `source_id`, `official_locator`, level, and grounded evidence references.

## Legacy Differences

- The prototype 90/50/20 urgency score no longer drives the AI-first free-text route; `urgency_score` remains null.
- The old `<40` glucose threshold was not retained. A130409/A130413 use `<60` with symptom status and produce levels 2/3 respectively.
- Blanket high-blood-pressure behavior is not executed because those package rules are `reference_only`.
- A041011/A041017 use the official six-hour boundary.
- No-match is insufficient information, not low urgency.

Legacy deterministic safety parsing remains only for the existing controlled safety-question compatibility path. It is not invoked for ordinary AI-first free-text urgency interpretation and was not expanded in Phase 5.

## Tests

Focused tests cover package integrity, grounding and type validation, unknown vitals, age handling, all required modifier/official-code regressions, reference-only exclusion, multiple-match priority, evidence trace, no-match behavior, single Turn Interpreter call, and Phase 4 conversational regressions.
