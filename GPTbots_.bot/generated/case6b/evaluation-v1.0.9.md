# Agent L v1.0.9 POC Evaluation

Status: Passed

- Evaluation date: 2026-07-24
- Target: GPTBots test-mode Agent only
- Conversation model: one private conversation, two blocking messages
- Template fields returned: 37/37
- Filled field results returned: 37/37
- Template parse LogTree: `SUCCESS`
- Field fill LogTree: `SUCCESS`
- Input contract: concise text guidance plus DOCX/Markdown attachments
- Output contract: one JSON Object per stage

The first text guide identifies the original DOCX as the agreement template and
`case6b_template_context.md` as the authoritative placeholder/locator contract. The second
guide identifies `case6b_field_fill_context.md` as the complete field and evidence input and
states the JSON-only output requirement.

The API client prefers the newly persisted Assistant message for the current turn over
blocking-response debug text. Optional services retain `service_action="optional"`, and an
agreement end date remains `NEEDS_CONFIRMATION` when only a relative 12-month duration is
supported.

Full model responses, message identifiers, conversation identifiers, and runtime payloads are
stored only under ignored local `output/`.
