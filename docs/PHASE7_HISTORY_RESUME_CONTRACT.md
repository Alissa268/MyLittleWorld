# Phase 7 History Completion and Resume

Local history completion is independent of Backend confirmation. Confirmation,
recommendation and successful script revalidation leave history UNCOMPLETED.
Only the final confirmation action, with a validated formal selection and a
successful external Activity launch, completes an existing local history record.
This records completion of this App's guidance, not hospital registration.
Quick Search does not create consultation history. COMPLETED records are immutable
and read-only until explicitly deleted, including records saved by older versions.

## Read-Only Resume

`GET /chat/{case_id}/resume` returns an independent minimal projection:

- case_id, visit_type, stage, confirmed, awaiting_confirmation
- department_status, clarification_status, red_flags_checked, red_flags_status
- warning_required, warning_message
- department_result (dept_id, parentDept, childDept only)
- next_question, last_question_key, question_batch, can_continue

Missing, expired or process-restart-lost cases return HTTP 404. Reads do not create
cases, refresh TTL, increment question attempts, run AI/TTAS/SQL, or change workflow
state. No patient input, conversation history, evidence or full case is returned.
Responses are marked `Cache-Control: no-store`.

Known pending checklist keys use the saved question, or the existing canonical
question when no text was saved. Unknown batch keys disable continuation; they are
not guessed. Free-text pending questions and safety replies remain free-text.
Android ignores stale responses after switching records and never queries resume
for a COMPLETED record. A network failure is retryable, not proof of expiry.

Android continuation POST /chat requests with case_id also send the additive
`require_existing_case=true` flag. The default is false for existing callers.
If the case disappears between resume and an answer, the flagged request returns
404 rather than silently creating a blank case with the old ID.

Persisted history keeps its existing fields and recommendation snapshots even
while UNCOMPLETED. A stored selection is display-only: reopening history clears
the in-memory selected recommendation and script. Continuing registration requires
fresh SQL revalidation through /generate_script. No new health-data persistence,
permanent Backend store or medical inference is introduced.
