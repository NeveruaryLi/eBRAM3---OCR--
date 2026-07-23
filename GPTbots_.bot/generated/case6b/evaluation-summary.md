# Agent L POC Evaluation

Status: Passed

- Agent version: `v1.0.4`
- Evaluation date: 2026-07-24
- Conversation model: one private conversation, two blocking messages
- Template fields returned: 37
- Filled field results returned: 37
- Template parse trace: SUCCESS
- Field fill trace: SUCCESS
- Template route: `Regular-1 → AI Model-1`
- Field-fill route: `Regular-1 → AI Model-2`
- Result statuses: 13 `FILLED`, 6 `NEEDS_CONFIRMATION`, 12 `REMOVE`,
  6 `LEAVE_BLANK`
- Every `FILLED` result includes evidence.

## Failures

- None

Full model responses and runtime identifiers are stored under ignored `output/`.
