# MCP MVP tool contracts

SDK: exact `mcp[cli]==2.2.0`. Transport: local `stdio`.

All tools delegate to application services. Page numbers are one-based.

## `list_cases`

- Input: `{}`
- Output: `{ "cases": CaseSummary[] }`

## `import_document`

- Input: `{ "case_id": string, "path": string }`
- Output: `DocumentSummary`
- The path is local and will be subject to allowed-root validation in the ingestion service.

## `search_case_evidence`

- Input: `{ "case_id": string, "query": string, "limit": integer = 10 }`
- Output: `{ "hits": SearchHit[] }`
- Each hit contains document/version, page, quote, evidence ID, chunk ID, final rank/score,
  per-channel ranks/scores, and retrieval sources. The shared service defaults to hybrid RRF.

## `get_document_page`

- Input: `{ "document_id": string, "page_number": integer }`
- Output: `PageView`

## `detect_conflicts`

- Input: `{ "case_id": string }`
- Output: `{ "conflicts": ConflictView[] }`
- Each side carries source-traceable citations.

## `ask_case`

- Input: `{ "case_id": string, "question": string }`
- Output: `GroundedAnswer`
- Only citations accepted by the application citation validator may be returned.
