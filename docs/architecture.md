# Architecture

## Logical view

```text
Local HTML/JS GUI        CLI                  MCP stdio server
         \                    |                    /
          \                   |                   /
                 Application service layer
   Case / Ingestion / Retrieval / QA / Claims / Coverage /
           Candidates / Contradictions / Timeline
                              |
       -------------------------------------------------
       |             |             |                 |
   SQLite/FTS5   Local files   Model adapters   Cache/logging
```

The browser GUI is served by the existing FastAPI process. It calls the same
application services as the HTTP, CLI, and MCP adapters. `CaseWorkspaceService`
only coordinates existing validated services and scopes their read operations to
the selected Case. It does not decide factual support or contradiction verdicts.
The browser is local, but a configured cloud model receives the selected synthetic
or de-identified text during QA, Claim extraction, and semantic checks.

The model boundary is split: an OpenAI-compatible adapter handles cloud chat, while a
separate local adapter handles embeddings. Application services depend only on
`LLMProvider` and `EmbeddingProvider`; they do not import Qwen- or BGE-specific code.

## Dependency rule

- `domain` has no framework dependencies.
- `application` depends on domain types and declared protocols.
- `infrastructure` implements those protocols.
- `interfaces` call application services; they do not implement case-analysis logic.
- Frontend code calls the HTTP API and never reads local storage directly.

## MVP storage

- SQLite stores metadata, FTS content, analysis records, explicit local feedback, and small vectors.
- Original PDFs and rendered page images live under the configured data directory.
- Vector similarity may use NumPy for the MVP dataset size; no separate vector database is required.
- Derived records are versioned rather than overwritten when parser, chunking, prompt, model, or embedding versions change.

## MCP decision

- Official Python SDK: exactly `mcp==2.2.0`.
- Transport: local `stdio` only for MVP.
- MCP functions are adapters over application services.

The SDK version is deliberately exact because the official v2 SDK is a breaking redesign from v1 and continued unpinned updates would add avoidable schedule risk.
