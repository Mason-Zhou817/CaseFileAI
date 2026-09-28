# MVP requirements

## Deadline

Final delivery: **2026-10-11**. Code freeze is planned for 2026-10-10.

## Required capabilities

- Case creation and management.
- Text PDF parsing and scanned PDF OCR.
- Original document, page, and quoted source viewing.
- Text chunking and indexing.
- Chinese keyword retrieval plus vector retrieval and hybrid fusion.
- Source-traceable case-file question answering.
- Citation-ID and citation-completeness validation.
- Structured fact/claim extraction.
- Date, amount, action-presence, and subject/person conflict detection.
- Side-by-side conflict source display.
- Basic timeline.
- Browser GUI.
- Local MCP server.
- Cache and incremental analysis.
- Basic structured logs, feedback records, tests, and evaluations.

## Required MCP tools

- `list_cases`
- `import_document`
- `search_case_evidence`
- `get_document_page`
- `detect_conflicts`
- `ask_case`

The initial transport is local `stdio`. Tool handlers may validate inputs and map outputs, but all business logic must call the application service layer.

## Citation acceptance criterion

Every displayed citation must resolve to:

1. an existing document;
2. an existing one-based page number;
3. a verbatim source quote found on that page.

Character offsets and visual highlighting are best-effort and must not block the MVP.

## Model and data policy

- Original files, indexes, metadata, cache, logs, and feedback are local-first.
- The primary MVP inference path uses a configured Qwen chat model through Alibaba
  Cloud Model Studio's OpenAI-compatible API and a local embedding model.
- The initial local embedding model is the pinned `BAAI/bge-small-zh-v1.5` revision.
  BGE-M3 is an evaluation candidate only if measured retrieval quality is insufficient.
- DeepSeek Flash remains an optional future adapter and is not part of the first phase.
- Only synthetic or de-identified case materials may be sent to a public model API.
- Model and embedding providers sit behind narrow protocols. Supporting many providers is not an MVP goal.
- API base URL, API key, and model ID are environment configuration and must never be
  hard-coded, committed, or logged.

## Deferred work

- Statute and similar-case retrieval.
- Adversarial litigation simulation and opponent prediction.
- Money-flow graphs and general Excel/bank-statement analysis.
- Word ingestion.
- Complete local large-model support.
- Broad multi-provider integrations.
- Enterprise identity and authorization.

## Day 1 frozen decisions

- Stable IDs and immutable versioned derived records.
- Answer-to-source citation lineage.
- Content- and version-aware cache keys.
- Shared application service contracts for GUI, CLI, and MCP.
- Ordinary columns may evolve through database migrations.
