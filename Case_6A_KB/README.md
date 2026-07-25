# Case 6A — eBRAM Website Support Knowledge Base

This directory is the import source for Agent K's GPTBots knowledge base. It contains mechanically curated English content from official `ebram.org` pages and official eBRAM PDF attachments.

## Build result

- 28 Markdown documents: 25 canonical source documents plus three verbatim fee-schedule extracts for precise retrieval from long rules PDFs.
- All documents passed the GPTBots document validator.
- Each document records its canonical page, resolved source, retrieval date, Sitemap last-modified date, category and body SHA-256.
- Customer acceptance questions are not included as knowledge documents.

## Curation policy

Included material covers eBRAM services, arbitration, mediation, APEC ODR, application procedures, rules and fees, platform security, panel applications and training, support contacts, deal-making and LawTech services.

News, events, careers, videos, dynamic panel-member profiles and unrelated research are excluded. Navigation, footer, Cookie and repeated call-to-action content is removed. Official wording is not summarized or expanded.

## Rebuild

Run in the project's `ebram` Conda environment:

```powershell
python Case_6A_KB\build_case6a_kb.py
```

Raw responses are cached under `.cache/`. Use `--refresh` for a deliberate website refresh. A failed or textless source causes a non-zero exit unless `--allow-partial` is explicitly used; partial builds must not be uploaded.

The API synchronizer is located at `GPTbots_.bot/generated/case6a/sync_case6a_kb.py`. It reads `AGENT_K_API_KEY` from the process environment, creates or reuses an isolated test knowledge base, uploads these Markdown files and runs retrieval checks.
