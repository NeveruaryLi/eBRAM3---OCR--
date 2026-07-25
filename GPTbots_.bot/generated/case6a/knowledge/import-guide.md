# eBRAM website knowledge import guide

## Staging

1. In GPTBots developer space, create `eBRAM Website Staging KB` with knowledge graph and access control disabled.
2. Add the official Sitemap URL `https://www.ebram.org/sitemap.xml`.
3. Wait until all selected pages finish parsing, then review the document preview and source URL for every row in `sitemap-manifest.csv`.
4. Inspect related same-domain links and attachments needed for Q03, Q06 and Q07. Do not add non-eBRAM sources.

## Curation

- Keep headings, numbered procedures, rules, tables, fee data, contact details and official links.
- Remove navigation, footer, Cookie text, language selectors, repeated calls to action and decorative visual assets.
- Prefer formal rules, policies and fee documents over guides, and guides over promotional pages.
- Disable exact/near duplicates. If two official sources disagree, retain both in staging and record the conflict for review.
- Use English canonical pages as the primary corpus. Agent K produces Traditional Chinese answers from the grounded English content.

## Test knowledge base

1. Create `eBRAM Website Support KB (Case 6A Test)` with graph and access control disabled.
2. Add only the reviewed Include rows and any approved official attachment discovered during staging.
3. Use Document storage with heading-aware Markdown/token chunks. Start around 500-800 tokens; keep numbered rules at clause level.
4. Preserve each document's official source URL. Add metadata where available: `category`, `language`, `canonical_url`, `last_modified`, `content_hash`, `refresh_tier`.
5. Bind the returned knowledge-base id to `KGs-1`, regenerate the `.bot`, validate it, and publish only to the Case 6A test-mode Agent.

The public GPTBots API does not expose Sitemap import. Do not invent or call an internal endpoint; perform the Sitemap step in the developer console.
