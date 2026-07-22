# eBRAM Service Assistant Test Release Evaluation

## Release

- Test Agent version: `v1.0.5`
- Knowledge base: `eBRAM Website Support KB (Case 6A Test) r2`
- Knowledge documents: 28, all `AVAILABLE`
- Evaluation date: 2026-07-23

## Results

- Vector retrieval: Q01–Q10 passed; Q01–Q09 retained a correct official source in Top 5 and Q10 returned no knowledge hit.
- English customer questions Q01–Q09: passed with official eBRAM source links.
- Off-topic Q10: passed with a natural “I can help…” scope response, without an internal Agent code or contact fallback.
- Related knowledge gap: passed; the assistant stated that no guaranteed technical-support response time was published and used the verified Contact Us fallback.
- Traditional Chinese ZH01–ZH03: passed.
- Identity/legal-advice boundary ID01: passed without exposing the internal Agent code.
- Multi-turn mediation follow-up: passed in the same conversation.
- Conversation isolation: passed with a distinct conversation id and an independent answer.

## Final retrieval settings

- Hybrid Search: 10% keyword / 90% semantic (`embeddingRate=0.9`)
- Similarity threshold: `0.76`
- Top K: `5`
- Rerank: enabled; public hit testing used `BGE-Rerank`
- Query enhancement: enabled
- Knowledge graph: disabled

The v1.0.5 regression covered 15 individual questions plus multi-turn context and conversation isolation. Detailed runtime reports are written outside Git because they contain generated conversation identifiers and full model responses.
