# Agent L POC Evaluation — v1.0.4

Status: Passed

- Evaluation date: 2026-07-24
- One private conversation with two blocking user turns.
- Turn 1 LogTree: `User Input → Regular-1 → AI Model-1 → Output`, `SUCCESS`.
- Turn 2 LogTree: `User Input → Regular-1 → AI Model-2 → Output`, `SUCCESS`.
- Template parser returned all 37 stable field IDs once, in order, without values.
- Field filler returned all 37 field IDs once, in order, with valid statuses.
- Status totals: 13 `FILLED`, 6 `NEEDS_CONFIRMATION`, 12 `REMOVE`,
  6 `LEAVE_BLANK`.
- Every `FILLED` value includes at least one evidence source.
- The four selected services and monthly prices, both party names and addresses, and Net 30
  terms matched the expected fixture.
- Unknown substantive fields remained `NEEDS_CONFIRMATION`.
- Six signature fields remained `LEAVE_BLANK`.
- Six unused service rows (12 name/price fields) were marked `REMOVE`.

The live-verified phase condition is `sys_user_msg_count < 1`: the Regular node sees completed
prior user turns, so the first turn is 0 and the second is 1.

Full runtime responses remain only under ignored `output/`.
