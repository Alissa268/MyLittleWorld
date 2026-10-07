# Phase 6 Backend Recommendation Contract

## Scope

Phase 6 changes Backend interpretation and recommendation ranking only. Android, Department convergence, TTAS, SQL schema/data, doctor scoring inputs, and navigation-script contracts are not redesigned.

## Time preferences

`Availability` retains `preferred_dates`, `preferred_days`, `preferred_sessions`, and `can_take_leave`. It also accepts additive `time_preferences` facts with:

- `kind`: `date`, `date_range`, `relative_week`, `relative_weekday`, `weekday`, `weekday_group`, or `session`
- `relation`: `exclude`, `prefer`, or `acceptable`
- `priority`: required for `prefer`/`acceptable`; absent for `exclude`
- grounded `source_text` and finite `confidence`
- English machine identities for weekday/session values
- `reference_date` and frozen `resolved_dates`

The existing Turn Interpreter proposes these facts in the same call that returns semantic, pending-answer, and TTAS evidence. Backend validates grounding and structure. It resolves relative dates against `Asia/Taipei` once, when accepting the fact, so a later `/recommend` request cannot shift its meaning.

`exclude` is a hard filter and is never relaxed. `prefer` does not prohibit unmentioned dates or sessions. Thus `下午最好` prefers afternoon, while `只能下午` requires explicit morning/evening exclusions from the grounded AI interpretation.

Legacy and new Availability data are merged per dimension. Any new date-kind fact owns the date dimension and supersedes legacy `preferred_dates`/`preferred_days`, while legacy `preferred_sessions` remains effective if no new session fact exists. Any new session fact owns the session dimension, while legacy date fields remain effective if no new date fact exists. With no `time_preferences`, legacy behavior is unchanged. New hard exclusions are applied before any legacy matching or `can_take_leave` relaxation and can never be reintroduced.

## Schedule truth and ranking

SQL remains the schedule and identity authority. A formal recommendation requires exact `dept_id`, `doctor_id`, `schedule_id`, date, session, compatible visit type, active/non-placeholder availability, and a non-past/open session. Missing `doctor_id` never falls back to doctor name.

Complex preferences are evaluated after SQL retrieval. Date and session dimensions form deterministic preference vectors. Non-dominated rows form tier 0; later Pareto fronts form subsequent tiers. No date/session weights are invented. Within one tier, actual date/session time is earlier first.

The two stable orders are:

- `specialty_first`: specialty relevance descending, time tier, actual datetime, doctor ID, schedule ID.
- `time_first`: time tier, actual datetime, specialty relevance descending, doctor ID, schedule ID.

Legacy `score`, `specialty_score`, and `time_score` fields remain type-compatible, but `score` and `time_score` do not control ordering. The former 70/30 formulas are not ranking truth.

## Specialty relevance

Schedule rows are grouped by real `doctor_id` before AI scoring. Each unique doctor is presented once with SQL `specialty_tags` and validated current patient semantic evidence. The returned score is only symptom-to-specialty-tag semantic relevance; it is not diagnosis confidence, physician quality, or outcome probability. Natural-language reasons are not required.

Doctors are sent in configurable small batches. In addition to the provider's per-call timeout, the complete scoring operation has a configurable monotonic-clock deadline (`DOCTOR_SCORING_TOTAL_TIMEOUT_SECONDS`, default 12 seconds). Each batch receives only the remaining total budget; no later batch starts after the deadline.

AI scores are used only if every scorable doctor is returned exactly once with a finite score in `[0, 1]`. Total-budget timeout, per-call timeout, malformed JSON, missing/extra/duplicate IDs, invalid score, or capacity overflow makes the entire candidate set neutral (`0.5`), including batches that succeeded before a later failure. Doctors without specialty tags are neutral and are not sent to AI. No medical keyword scorer is used as fallback.

## Response compatibility

`/recommend` continues to return `specialty_first` and `time_first`, each with at most five concrete Schedule recommendations. Each item keeps its existing navigation identity and can be revalidated before `/generate_script`.

Phase 6 does not change the outward response to one doctor card with expandable alternative slots. A doctor may therefore occupy more than one concrete Schedule row in a column even though specialty scoring occurs once per `doctor_id`. The one-card presentation belongs to Phase 7.

## Remaining live validation

Code-level tests use synthetic Schedule rows and mocked AI responses. Final validation still requires the 310 environment to confirm real SQL column values, actual specialty tag coverage, current session cutoffs, timezone deployment, provider batch reliability, and end-to-end script generation against a live Schedule row.
