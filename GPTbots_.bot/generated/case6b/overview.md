# Agent L — Service Agreement Drafting

Internal GPTBots FlowAgent for Case 6B. The original
`GPTbots_.bot/EBRAM-Case6B.bot` remains unchanged.

## Flow (mermaid)

```mermaid
flowchart LR
    I["User Input"] --> R{"sys_user_msg_count < 1"}
    R -->|"true · TEMPLATE_PARSE"| P["AI Model-1<br/>Parse DOCX fields"]
    R -->|"false · FIELD_FILL"| F["AI Model-2<br/>Fill from evidence"]
    P --> O["JSON Output"]
    F --> O
```

## Runtime contract

- First message: a short text guide, the original DOCX, and `case6b_template_context.md`;
  the Markdown contains `[CASE6B_PHASE:TEMPLATE_PARSE]`, every detected field and its
  stable locator.
- Second message in the same conversation: `case6b_field_fill_context.md`; the Markdown contains
  `[CASE6B_PHASE:FIELD_FILL]`, the complete `field_list` and evidence `full_summary`. A short
  text guide identifies the attachment and expected JSON-only response.
- Both LLM components return one JSON object.
- Long-term memory, user properties, tools, workflows and databases are disabled.
- Short-term memory is auxiliary only; critical state is always supplied explicitly.
- Agent L never edits the DOCX. The application validates JSON and performs deterministic
  document replacement and conflict gating.
- Production runs do not attach a customer-specific template profile or hidden default values.
  Missing evidence remains visible for review. Relative durations such as `12 months` are
  accepted without inventing an end date.

## POC fixtures

- `fixtures/template_field_manifest.json`: 37 stable template fields.
- `fixtures/full_summary.json`: curated facts from all 9 customer materials.
- `fixtures/expected_fill.json`: expected status and values for the sample.

Run `run_case6b_poc.py` only after the generated bot is imported and released to the Case 6B
test-mode Agent. The script reads `AGENT_L_API_KEY` from the process environment or ignored
local `.env`; it never writes the key or GPTBots conversation identifiers to reports.

## Test-mode result

- Current test-mode release: `v1.0.9`
- Runtime payload: generic Markdown attachment contract plus concise text guidance
- Supports application manifests containing underscore/bracket placeholders, optional service
  rows, execution blanks and evidence provenance.
- Two-stage POC: passed on 2026-07-24 with 37/37 template fields and 37/37 fill results;
  both LogTree stages reported `SUCCESS`
- API response selection prefers the current turn's persisted Assistant message over
  blocking-response node/debug text.
- Live route: first turn (`0 < 1`) → `AI Model-1`; second turn (`1 < 1` is false)
  → `AI Model-2`
- Evaluation history: `evaluation-v1.0.2.md`, `evaluation-v1.0.3.md`,
  `evaluation-v1.0.4.md`, `evaluation-v1.0.5.md`, `evaluation-v1.0.8.md`,
  `evaluation-v1.0.9.md`
