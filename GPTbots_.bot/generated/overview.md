# Agent I - Legal PDF Page Translation

Agent I translates exactly one rendered PDF page image per request. The caller supplies the direction and page metadata; the Agent returns one page-faithful translated image.

The integration is included in the eBRAM `case4-case5-v1.0` release. The application, rather than the Agent, owns upload validation, page rendering, sequential execution, retries, image retrieval, and PDF assembly.

## Flow (mermaid)

```mermaid
flowchart LR
    Input["User Input<br/>direction + one page image"] --> Gemini["AI Model-1<br/>Gemini 3.1 Image"]
    Gemini --> Output["Output<br/>one translated page image"]
```

## Runtime policy

- Supported directions: English to Traditional Chinese; Traditional Chinese to English.
- All long-term memory, short-term memory, user attributes, and tools are disabled.
- Output type is Image only; reasoning is hidden.
- Page order, PDF assembly, retries, and failure handling are owned by the eBRAM Case 4 application.
- The application accepts one PDF up to 25 MB and 30 pages, renders at 150/120/96 DPI, and fails the whole document if any page fails.
- Case 4 exposes only the translated PDF download and deliberately has no follow-up chat.

## Published test versions

- `1.0.1`: initial legal page-translation prompt.
- `1.0.2`: strengthened the Traditional Chinese to English direction.
- `1.0.3`: added exact structural-cardinality checks and prohibited cross-page reconstruction. This is the current released test version.

## API POC findings

- Blocking calls can return the image reference in `output[].content.image`, but this field may be null even after a successful image generation.
- The reliable fallback is `GET /v2/messages`, selecting only the Assistant turn and its `branch_content[].image[]`. User turns contain the source upload and must never be used as translated output.
- Assistant history may expose a `/thumbnail/` URL. The application first requests the corresponding original asset and falls back to the thumbnail only when the original cannot be downloaded or validated.
- Generated image URLs must use an approved GPTBots HTTPS domain; URL/base64 outputs are size-limited and decoded before PDF assembly.
- The release sample used an `864×1222` original image instead of the `566×800` thumbnail, materially improving the assembled PDF resolution.
- Both directions produced a complete page image at the same aspect ratio in the v1.0.3 POC. Table dimensions, amounts, dates, and section counts were preserved.
- The English-to-Traditional-Chinese sample retained some English product names alongside their Traditional Chinese translation. This model-level bilingual rendering remains a manual-review item after the two planned prompt-adjustment rounds.
