# Phase 4.2 Backend Hardening Plan

**Project:** New_Android_Backend  
**Current audited checkpoint:** `1756d06c29071d18813929d661ed57ac0f3100a2`  
**Scope:** Backend only  
**Phase 5 status:** Not started  
**Goal:** Fix backend trust-boundary, DB-failure semantics, state consistency, return-visit validation, privacy logging, and a small set of deployment-hardening issues discovered during the full backend audit.

---

## 0. Why this hardening phase exists

Phase 4 and Phase 4.1 successfully established:

- AI-first free-text understanding
- grounded semantic evidence
- official department knowledge
- live DB department resolution
- candidate-department convergence
- multi-turn clarification
- safety precedence
- no legacy symptom-to-department fallback for ordinary free text

However, a broader backend audit found several cross-module issues that can bypass or undermine those guarantees even though the Phase 4 unit tests pass.

This phase is therefore a **backend hardening phase before Phase 5**.

The target architecture remains:

> AI understands language.  
> Backend owns state, validation, workflow, and safety gates.  
> SQL Server owns canonical department / doctor / schedule truth.  
> Client input is never allowed to redefine server-owned truth.

---

# 1. Priority summary

| Priority | Problem | Required outcome |
|---|---|---|
| P0 | Client-supplied `triage_case` can override server state | Server store must remain authoritative |
| P0 | Existing `department_result.dept_id` is not always revalidated against live DB | Every routable department must be revalidated before recommendation |
| P1 | DB outage is collapsed into “no schedules” | Distinguish DB failure from genuine zero results |
| P1 | Recommendation mutates stored case before successful completion | Recommendation state update must be commit-after-success |
| P1 | Return-visit department/doctor identity can be accepted without canonical DB validation | Return visit must validate department and doctor relationship |
| P2 | Return visit without `preferred_dates` silently produces no results | Contract must explicitly support a bounded default search or reject missing date |
| P2 | Normal recommendation/follow-up selections are not revalidated immediately before navigation script | Selected schedule must be revalidated |
| P2 | Some DB functions do not guarantee connection close on exceptions | DB connections must always close |
| P2 | INFO logs contain patient health content | Production logs must be metadata-only |
| P3 | `/reference/doctors` deduplicates by doctor name | Preserve distinct doctor IDs |
| P3 | In-memory case/recommendation store has no cleanup | Add bounded TTL cleanup appropriate for prototype |
| P3 | Clarification “one intent = one question” validation is still heuristic | Strengthen planner/backend contract without medical keyword rules |

---

# 2. P0 — Server-authoritative case state

## Problem

`/chat` currently resolves a case approximately as:

```python
supplied = req.triage_case
stored = get_case(...)
return supplied or stored or create_case(...)
```

`/recommend` has a similar preference for a client-supplied `triage_case`.

This means a client can send fields such as:

- `conversation_state.is_complete`
- `conversation_state.confirmed`
- `conversation_state.stage`
- `patient_input.red_flags_checked`
- `department_result`
- `candidate_departments`

and potentially replace server-owned state.

That violates the core architecture.

## Required change

For an existing `case_id`:

1. Load the server-stored case.
2. Server-stored workflow state is authoritative.
3. Do **not** replace it with the client-supplied `triage_case`.
4. A supplied `triage_case` may only be used for backward compatibility when no server case exists, and even then server-owned fields must not be trusted blindly.

Preferred design:

- `/chat` should primarily use:
  - `case_id`
  - `message`
  - `messages`
  - `answers`
  - `confirmed`
  - `revision_requested`
  - `visit_type`
- `/recommend` should primarily use:
  - `case_id`
  - `confirmed`
  - `visit_type`
- Existing `triage_case` request fields may remain in schema temporarily for Android/API compatibility, but cannot override an existing stored case.

## Desired end state

A request containing:

```json
{
  "case_id": "case_real",
  "triage_case": {
    "case_id": "case_real",
    "conversation_state": {
      "is_complete": true,
      "confirmed": true,
      "stage": "recommending"
    },
    "patient_input": {
      "red_flags_checked": true
    },
    "department_result": {
      "dept_id": 999999,
      "childDept": "不存在科"
    }
  }
}
```

must **not** override the real server case.

If the stored case is incomplete, `/recommend` must still reject it.

## Required tests

- forged `is_complete=true` cannot bypass incomplete server case
- forged `confirmed=true` cannot bypass confirmation
- forged `red_flags_checked=true` cannot bypass safety gate
- forged `department_result` cannot replace stored department
- mismatched `case_id` inside supplied snapshot is rejected
- stored case always wins when both stored and supplied state exist

---

# 3. P0 — Canonical department revalidation before recommendation

## Problem

`recommend_appointments()` only re-resolves a department when:

```python
department.dept_id is None
```

Therefore a non-null but invalid/stale ID can proceed directly into schedule lookup.

Current tests even allow a fake ID such as `999999` to reach schedule lookup and only expect an empty result.

## Required change

Before **every normal recommendation query**:

1. Load live active departments.
2. Revalidate the entire department tuple:
   - `dept_id`
   - `parentDept`
   - `childDept`
3. Require exactly one canonical live match.
4. Replace the case result with the canonical live row.
5. If no exact canonical match exists, raise `DepartmentResolutionError`.
6. Never query schedules using an unvalidated `dept_id`.

Explicit user department preference must undergo the same canonical DB validation.

## Desired end state

```text
validated Phase 4 department
→ live Department revalidation
→ exact canonical dept_id/name tuple
→ Schedule query
```

Not:

```text
client/stale dept_id
→ Schedule query
```

## Required tests

- nonexistent `dept_id=999999` is rejected before schedule query
- valid ID with wrong `childDept` is rejected
- valid child name with wrong parent is rejected
- duplicate/non-unique department record remains unresolved
- valid canonical department proceeds normally

---

# 4. P1 — Distinguish DB outage from real “no rows”

## Problem

`fetch_available_slots()` currently catches DB exceptions and returns `[]`.

`recommend_followup()` also catches DB exceptions and converts them to `rows=[]`.

The API therefore cannot distinguish:

```text
DB successfully returned zero rows
```

from:

```text
DB connection/query failed
```

This produces misleading user-facing messages.

## Required change

Create a clear data-access error contract, for example:

```python
class DatabaseUnavailableError(RuntimeError):
    pass
```

or equivalent.

Rules:

- DB connection/query failure:
  - propagate a typed error
  - route returns `503`
- successful query with zero rows:
  - return `[]`
  - route may return normal empty recommendation result / no-schedule response
- never use mock schedule fallback

Apply consistently to:

- normal recommendation
- follow-up recommendation
- quick search
- reference endpoints
- schedule revalidation

## Desired end state

```text
DB works + zero schedules
→ real “no available schedule” behavior

DB unavailable
→ HTTP 503
→ “正式班表目前無法查詢，請稍後重試”
```

## Required tests

- normal recommendation DB failure → 503
- follow-up DB failure → 503
- successful zero rows remains different from DB failure
- no mock doctor/schedule is introduced

---

# 5. P1 — Recommendation state must commit only after success

## Problem

The case object in `case_store` is mutable and returned by reference.

`/recommend` currently changes fields such as:

- `confirmed`
- `conversation_state.stage = recommending`
- `conversation_state.confirmed`
- `recommendation_generated`

before recommendation success is fully known.

A failed schedule lookup can therefore leave the stored case in a partially-completed state.

## Required change

Use commit-after-success semantics.

Recommended approach:

1. Read stored case.
2. Create a deep working copy:

```python
working_case = case.model_copy(deep=True)
```

3. Validate and query using `working_case`.
4. Do not mutate the stored object during validation/query.
5. Only after a valid recommendation result is produced:
   - set recommendation state
   - save working case
   - save recommendation result
6. If query fails or returns the route's error condition:
   - original stored state remains unchanged

## Desired end state

Failed `/recommend`:

```text
before:
waiting_confirmation / confirmed state as appropriate

failure:
DB 503 or no schedule response

after:
server case is not falsely marked recommendation_generated
and is not left in an inconsistent stage
```

## Required tests

- DB failure does not mutate stored stage
- no-schedule failure does not set `recommendation_generated=True`
- `DepartmentResolutionError` leaves original stored state unchanged
- successful recommendation commits stage and recommendation state once

---

# 6. P1 — Canonical return-visit department and doctor validation

## Problem

`/followup/recommend` can construct:

```python
DepartmentResult(
    dept_id=request.dept_id,
    parentDept=request.parentDept,
    childDept=request.childDept,
    confidence=1.0,
    reason=["回診科別由使用者指定"],
)
```

before proving that the department is canonical.

The service can also return a department reason implying official master-data validation even when the DB query returned no rows.

## Required change

Before return-visit schedule search:

1. Validate department through live department master.
2. Prefer `dept_id` as identity when provided.
3. Require child/parent names to agree with the live row when also supplied.
4. Validate original doctor using canonical doctor data / schedule association.
5. If `original_doctor_id` exists:
   - it must correspond to the supplied doctor name and department
6. If only name exists:
   - require a unique matching canonical doctor relationship
7. Do not claim “由正式主資料選擇” until validation actually succeeds.
8. No fake confidence `1.0` based solely on request text.

## Desired end state

```text
return-visit request
→ canonical Department validation
→ canonical doctor identity / department relationship
→ schedule lookup
→ exact original-doctor slots only
```

## Required tests

- fake department ID rejected
- department ID/name mismatch rejected
- fake doctor ID rejected
- doctor ID/name mismatch rejected
- doctor belonging only to another department rejected
- validated relationship proceeds
- zero schedule rows do not fabricate validation evidence

---

# 7. P2 — Define behavior when return visit has no preferred date

## Problem

Production `fetch_return_visit_slots()` immediately returns `[]` if `preferred_dates` is empty.

But the API schema does not require `preferred_dates`.

Tests using mocked DB functions can therefore pass while production always returns no result.

## Required change

Choose one explicit contract and document it.

Preferred behavior for this prototype:

- If `preferred_dates` is empty:
  - search a bounded future window, e.g. next 21 days, for the original doctor/department
- If explicit dates are supplied:
  - search only those dates

Do not silently query an unbounded schedule table.

If the team prefers strict input instead, return `422` requiring a date.  
Do not leave the current silent-empty mismatch.

## Desired end state

API behavior and production DB behavior must match tests.

## Required tests

- no preferred date follows the documented bounded policy
- explicit dates remain exact
- future-window search cannot return past rows

---

# 8. P2 — Revalidate a selected schedule before navigation script

## Problem

Quick Search selections are revalidated before script generation.

Normal `/recommend` and follow-up stored recommendations are not necessarily reloaded from SQL immediately before generating the navigation script.

A schedule may become:

- full
- closed
- deleted
- reassigned

between recommendation and user selection.

## Required change

For any stored recommendation with a real `schedule_id`:

1. Reload the schedule by `schedule_id`.
2. Verify:
   - same `schedule_id`
   - same `doctor_id`
   - same `dept_id`
   - same date/session
   - active doctor
   - non-placeholder doctor
   - currently available status
3. Use DB-authoritative fields in script.
4. If changed/unavailable:
   - return `409`
5. DB failure:
   - `503`

Reuse/generalize the existing Quick Search revalidation logic instead of creating three inconsistent implementations.

## Desired end state

```text
recommendation card
→ user selects
→ DB revalidation
→ navigation script
```

---

# 9. P2 — Guarantee DB connection cleanup

## Problem

Some DB paths call `conn.close()` only on the successful path.

If cursor execution/fetch raises, the connection can remain open until garbage collection.

## Required change

Every direct connection path should use:

```python
conn = create_db_connection()
try:
    ...
finally:
    conn.close()
```

or a proper context manager.

Audit all functions in `app/db.py`.

## Required tests

Use mocked connection objects and assert `.close()` is called on:

- success
- cursor execution failure
- fetch failure

---

# 10. P2 — Remove patient health content from INFO logs

## Problem

Current INFO logs include full `patient_input` snapshots and sometimes extracted answer text.

Examples include symptom/body-part/red-flag data.

These are health-related data and should not be copied into routine server logs.

## Required change

INFO logs should contain only operational metadata such as:

- `case_id`
- counts
- field names
- accepted/rejected status
- provider/model
- latency
- exception type
- stage
- boolean flags

Do not log:

- full user message
- symptom strings
- body part text
- red-flag content
- `before_patient`
- `after_patient`
- raw semantic source text
- raw AI medical payloads

If development debugging truly requires content, use an explicit disabled-by-default local-only debug switch and keep it out of production logs.

## Desired end state

An ordinary 310 server console should not print a patient's health description.

## Required tests

Where practical, use `caplog` / logging capture to ensure representative INFO logs do not contain supplied medical strings.

---

# 11. P3 — Preserve distinct same-name doctors

## Problem

`fetch_reference_doctors()` deduplicates using doctor `name`.

Two valid doctors with the same display name but different `doctor_id` values collapse into one row.

## Required change

Identity is `doctor_id`, not name.

Return each distinct canonical doctor ID.

Ordering can remain deterministic.

## Required tests

- same name + different doctor IDs → both returned
- duplicate schedule association for same doctor ID → one returned

---

# 12. P3 — Add TTL cleanup to in-memory case store

## Problem

`_CASES` and `_RECOMMENDATIONS_BY_CASE` grow for the lifetime of the process.

For the current single-worker prototype this is acceptable functionally, but long-running 310 sessions can accumulate memory.

## Required change

Add lightweight last-touched timestamps and bounded TTL cleanup.

Suggested prototype defaults:

- TTL: 2 hours
- cleanup opportunistically during create/get/save
- recommendations expire with their case
- no background thread required

Do not migrate to Redis/SQL in this phase.

## Required tests

- active case survives within TTL
- expired case removed
- expired recommendations removed with case
- touching a case extends its lifetime

---

# 13. P3 — Strengthen atomic clarification contract

## Problem

Phase 4.1 correctly rejects common duplicate/multi-question patterns, but detecting “one intent = one question” solely through punctuation is heuristic.

## Required change

Do not add medical keyword mappings.

Instead:

1. Strengthen planner prompt:
   - one intent
   - one information dimension
   - one interrogative request
2. Keep backend duplicate/history validation.
3. Reject obvious conjunction-style multi-dimension questions where possible.
4. Keep safe focused fallback.
5. Never automatically clear pending intent just because new medical content exists.

## Desired end state

Clarification remains:

```text
one pending intent
→ one focused question
→ grounded answer
→ deterministic state update
```

---

# 14. API compatibility requirements

This backend hardening phase must not unnecessarily break Android.

Keep successful response shapes compatible for:

- `/chat`
- `/recommend`
- `/followup/recommend`
- `/schedules/search`
- `/generate_script`

Do not remove existing response fields.

Allowed behavior changes:

- invalid/forged state may now return `409` / `422`
- DB outage may now return `503` instead of fake empty results
- stale selected schedules may return `409`

Do not modify Android in this phase.

---

# 15. Implementation order

Implement in this exact order:

1. Server-authoritative case resolution
2. Department live revalidation
3. DB failure typed error semantics
4. Recommendation commit-after-success
5. Return-visit canonical identity validation
6. Missing-date return-visit policy
7. Schedule revalidation before script
8. DB connection cleanup
9. Patient-content log scrubbing
10. Same-name doctor identity
11. Case-store TTL
12. Clarification atomicity hardening
13. Full regression tests
14. Update validation documentation

Do not mix Phase 5 work into this phase.

---

# 16. Required regression suite

Run at minimum:

```powershell
cd backend
python -m pytest -q
```

All previous Phase 0–4.1 tests must continue to pass after necessary expectation updates.

Add focused tests for every item above.

Important security/trust tests must explicitly prove:

```text
client state cannot override server state
invalid dept_id cannot reach Schedule query
DB outage != empty schedule
failed recommendation does not mutate stored state
return visit cannot invent department/doctor identity
```

---

# 17. Definition of done

Phase 4.2 is complete only when all are true:

- Backend store owns workflow state.
- Client cannot self-assert completion/confirmation/safety completion.
- Every routable `department_result` is revalidated against live Department truth.
- Schedule query errors propagate as service errors, not fake empty availability.
- Recommendation state is committed only after successful validation/query.
- Return-visit department and doctor identities are canonical.
- Return visit without date has an explicit, tested policy.
- Every selected schedule is revalidated before navigation.
- DB connections close on every path.
- Routine INFO logs contain no patient health content.
- Doctor identity uses doctor ID, not display name.
- In-memory cases expire.
- Clarification remains grounded and atomic.
- Full backend test suite passes.
- Android, DB schema/data, official KB, TTAS, urgency rules, and Phase 5 are untouched.

---

# 18. Out of scope

Do **not** implement in Phase 4.2:

- TTAS replacement
- new urgency scoring
- production KB expansion
- fuzzy department aliases
- Phase 6 AI doctor scoring redesign
- Android conversational UX changes
- SQL schema migration
- authentication system redesign
- Redis deployment
- multi-worker architecture
- Phase 5

