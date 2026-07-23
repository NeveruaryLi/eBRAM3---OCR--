# Agent L POC Evaluation — v1.0.3

Status: Failed

- Evaluation date: 2026-07-24
- Template fields returned: 37
- Filled field results returned in the required schema: 0
- The first turn correctly executed `AI Model-1` and parsed all 37 fields.
- The second turn also executed `AI Model-1`, returning a non-contract `filled_fields` shape.
- Root cause: the live Regular node sees completed prior user turns, so the first turn is `0`
  and the second turn is `1`. The `sys_user_msg_count < 2` condition therefore matched both.
- Resolution: restore the original exported condition `sys_user_msg_count < 1`; this matches
  only the first turn and sends the second turn to `AI Model-2`.

Full runtime responses remain only under ignored `output/`.
