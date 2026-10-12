# Natural Chat / Safety Ownership Regression Repair

## Scope

Backend conversation plumbing only. Department retrieval, candidate validation,
TTAS rules/evaluator, recommendation ranking, SQL, Android, voice, Accessibility,
environment files and credentials are unchanged. No new medical NLP rules.
This workspace has no project Git metadata; its enclosing Git repository resolves
to the Windows user home directory, not the requested project branch. No reset,
checkout, commit or push was performed.

## State Ownership

- Entering a fresh safety screen resets attempts/validation, clears active
  department pending intent/question/information need and proposed next intent.
- A safety retry does not reset attempts. Three failed answers still terminate
  with unresolved safety, never an assumed negative.
- Only grounded, confident `answered + absent` for `safety_screen` completes the
  negative workflow contract. Missing TTAS matches do not complete it.
- Acceptance atomically exits safety and clears its context before the route
  recomputes department candidates and calls the normal planner in the same turn.
- Candidate/evidence history remains intact. Pre-safety intents are not restored;
  fresh reasoning may propose an unanswered intent again.
- A question and information need are bound to the accepted proposal's intent.
  Technical replies do not replace the clinical pending question for interpretation.

## Dialogue Interpretation

The existing Turn Interpreter additionally proposes `dialogue_act`:
`act`, `source_text`, `confidence`. Allowed acts: `answer`,
`request_explanation`, `claims_already_answered`, `correction`,
`new_information`, `unknown`, `off_topic`.

Only a current-turn literal source and finite confidence at the existing accept
threshold are accepted; malformed/extra keys, bad enums and low confidence become
unknown. The act is not a medical fact and grants no state authority. Medical,
TTAS and time evidence continue independently in the same response.

The planner receives the act and accepted clarification evidence. A stale pending
intent with an accepted answer is cleared before recomputation. Unproven claims
do not create answers. Answered intents cannot be asked again with new wording;
the existing explicit Backend revision path clears prior clarification ownership.
No additional dialogue classifier or semantic similarity call was added.

## Planner Validation and Repair

Question validation returns a machine rejection code. On invalid proposals there
is at most one additional planner repair call, with the original proposal,
rejection code, current accepted evidence, required intent, current information
need, pending question and dialogue act. Provider failure returns a technical
failure without a retry loop. Primary calls ignore planner answer fields.

Valid questions keep the original AI wording. Repair failures use a transparent
technical response, cannot establish completion/confirmation, and preserve an
existing pending need for a later retry. The former Backend-generated focused
retry function is removed. Legacy canned question constants remain only for
import compatibility and rejection checks, never as normal-chat output.

The former connector-only multi-dimension regex was narrowed to independent
additional question requests. A proposal can supply one `information_needs`
entry, owned by its intent; older proposal shapes remain compatible. Ordinary
Chinese connectors in a single focused question are accepted, while multiple
requests, workflow instructions, diagnosis assertions and department/doctor
suggestions remain rejected. This is output validation, not patient-text NLP.

## Regression Evidence

Before production edits, the new tests and corrected Phase 4.2.2 expectations
produced **19 failed, 11 passed** on the original local implementation.

`test_natural_chat_state_repair.py` covers the reported four-turn sequence,
atomic negative safety completion, partial safety, fresh revision attempts,
pre-safety ownership, both dialogue acts with and without accepted answers,
medical evidence coexisting with an explanation request, repair success/failure,
answered-intent dedupe, partial same-intent questions, ordinary connectors,
malformed proposals/answer enums, explicit revision, pending-question retention
and PHI-free structured logs. Existing bounded/urgent safety tests remain active.

Updated old test contracts (no protective tests deleted):

- `test_phase422_clarification_focus`: invalid or duplicate questions must not
  produce a neutral canned question.
- `test_phase41_clarification`: missing planner output cannot splice an internal
  information need into a fabricated question; accepted facts/pending remain.
- `test_phase2_conversation`: provider failures use a technical response;
  malformed JSON permits one repair rather than exactly one provider call.
- `test_semantic_refinement_gate`: malformed planner output adds one repair call,
  not another interpretation or reply-generation call.
- `test_phase4_department_convergence`: safety clears proposed department intent
  and completion releases stale intent before recomputation.

## Diagnostics and Live Acceptance

Per-turn ownership logs contain case ID, clarification/pending/department state
before/after, safety-turn/completed flags, safety validation/attempt count,
dialogue act, question acceptance/rejection code, repair flag and reply source.
Sources include `AI_PLANNER`, `AI_REPAIR`, `SAFETY_CONTROLLED_FALLBACK`,
`TECHNICAL_FALLBACK`, `CONFIRMATION` and `URGENT_BACKEND`. No patient source spans,
full questions, provider JSON or credentials are written by these logs.

Tests use mocked AI/SQL and project-local writable pytest temporary directories.
Final targeted run: **188 passed, 33 subtests passed, 0 failed/errors**.
Final full Backend run: **699 passed, 322 subtests passed, 0 failed/errors**.
Both runs report 7 existing FastAPI/Starlette deprecation warnings. The new file
contains 35 parametrized regression cases. Android was not modified or rebuilt.
310 acceptance still needs real Cerebras contextual denial/partial distinction,
focused explanation wording, stable intent handling, repair latency and live
Department reconvergence. No 310, real SQL or phone acceptance is claimed.
