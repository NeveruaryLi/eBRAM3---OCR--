# Agent L POC Evaluation — v1.0.2

Status: Failed

- Evaluation date: 2026-07-24
- Template fields returned: 0
- Filled field results returned: 37
- Both LogTree traces completed successfully but both turns executed `AI Model-2`.
- Root cause: the imported `op="eq"` Regular rule did not match the first turn in the live
  platform. The supported exported rule was changed to `sys_user_msg_count < 2`, where the
  first user turn is 1 and the second is 2.
- The first response was the structured `INVALID_FIELD_FILL_INPUT` error. The second response
  produced field results from incomplete context and is not accepted as a valid POC result.

Full runtime responses remain only under ignored `output/`.
