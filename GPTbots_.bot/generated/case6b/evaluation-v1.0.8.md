# Agent L v1.0.8 POC Evaluation

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

The POC uses structured facts matching the production Agent A contract. Optional services
retain their evidence names and use `service_action="optional"`. The template's agreement
end-date field remains `NEEDS_CONFIRMATION`: the evidence provides a 12-month relative term
but no effective date, so calculating a calendar end date would be unsupported.

Full model responses, message identifiers, conversation identifiers, and runtime payloads are
stored only under ignored local `output/`.
