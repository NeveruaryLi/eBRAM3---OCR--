# Agent L v1.0.5 — Case 6B V1.1 evaluation

Date: 2026-07-24
Mode: GPTBots test mode only

## Customer sample result

- One customer DOCX template and all nine customer materials completed the real
  OCR / local extraction / Agent A / Agent L pipeline.
- The live run produced 248 structured evidence facts and all 37 template fields.
- Agent L returned four included, two optional and four preserved blank service rows.
- A false conflict caused by `12` versus `12 months` was traced to a lost unit in one
  normalized fact. Conflict canonicalization now falls back to the source wording when
  a numeric duration has lost its unit.
- The exact live evidence and Agent output were replayed through review, finalize and
  report download after the fix: zero unresolved fields, zero unresolved conflicts,
  DOCX ready and PDF ready.

## Golden checks

Passed:

- both legal entity names and CR numbers;
- four included services with descriptions and monthly prices;
- two optional services with their prices and four blank service rows;
- HKD 0 at signing, HKD 0 at onboarding and HKD 68,000 per service month;
- Net 30, 12-month term, 30-day notice and 10-day material return;
- both company names in the signature section, with personal names, signatures and
  dates left blank;
- ten service rows, four-page PDF output and a non-splitting signature section.

No API key, conversation identifier, customer document body or raw platform response is
stored in this evaluation record.
