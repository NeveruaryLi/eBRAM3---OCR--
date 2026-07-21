# Agent I - Legal PDF Page Translation

Agent I translates exactly one rendered PDF page image per request. The caller supplies the direction and page metadata; the Agent returns one page-faithful translated image.

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
